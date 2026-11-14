from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import uuid

from .errors import ApiNotFound, ApiRejected
from .gacha_box import BOX
from .gacha_catalog import STEP
from .journal import GACHA_DRAW
from .limits import DATETIME, LIFETIME, MONTHLY, WEEKLY
from .lottery import percent
from .sync import Result, diff_state


UNIT_PREFIX = "unit.card."
LOOP_SUFFIX = ".s-loop"
PERIOD_PREFIX = "gacha."
STAMP_PREFIX = "stamp."
STAMP_SOURCE = "gacha.stamp_reward"
PLATFORM_PAYMENT = ("point", "payment")
LOOTBOX = "lootbox"
LOOTBOX_SOURCES = ("direct", "inbox")


@dataclass(frozen=True)
class Plan:
    pool: dict
    stage: dict
    used: int
    batches: int
    rewards: list[dict]
    extras: list[dict]
    randoms: list[dict]
    drawn: list[int] | None = None


class GachaEngine:
    def __init__(
        self,
        catalog,
        lottery,
        boxes,
        config,
        resources,
        limits,
        periods,
        mailbox,
        tutorial,
        journal,
        rng,
    ):
        self.catalog = catalog
        self.lottery = lottery
        self.boxes = boxes
        self.config = config
        self.resources = resources
        self.limits = limits
        self.periods = periods
        self.mailbox = mailbox
        self.tutorial = tutorial
        self.journal = journal
        self.rng = rng

    def _settings(self) -> dict:
        return self.config.read()["gacha"]

    def _listed(self, now: int) -> list[dict]:
        hidden = set(self._settings()["hidden_ids"])
        catalog = self.catalog
        return [
            pool
            for pool in catalog.pools()
            if pool["id"] not in hidden
            and not catalog.ended(pool, now)
            and catalog.stocked(pool)
            and catalog.shown(pool)
        ]

    def listed(self, now: int) -> list[str]:
        return [pool["id"] for pool in self._listed(now)]

    def require_open(self, state: dict, pool: dict, now: int) -> None:
        if (
            pool["id"] in self._settings()["hidden_ids"]
            or not self.catalog.started(pool, now)
            or self.catalog.ended(pool, now)
            or not self.catalog.stocked(pool)
        ):
            raise ApiRejected("OUT_OF_SCHEDULE")
        if pool["period_name"] and not self.periods.active(
            state, PERIOD_PREFIX + pool["period_name"], now
        ):
            raise ApiRejected("OUT_OF_SCHEDULE")

    @staticmethod
    def batches(pool: dict, requested) -> int:
        if type(requested) is not int or requested < 1:
            raise ValueError("exec_num must be a positive integer")
        if requested > pool["max_batch"]:
            raise ApiRejected("TOO_LARGE_NUM_OF_TRIALS")
        return requested

    @staticmethod
    def _cycle(pool: dict) -> tuple[str, int | str | None]:
        cycle = pool["execlimit_cycle_mode"]
        anchors = {
            WEEKLY: pool["execlimit_on_dow"],
            MONTHLY: pool["execlimit_on_dom"],
            DATETIME: pool["execlimit_on_dt"],
        }
        return cycle, anchors.get(cycle)

    def _stage(self, state: dict, pool: dict, now: int) -> tuple[dict, int]:
        cycle, anchor = self._cycle(pool)
        used = self.limits.used(
            state, pool["execlimit_counter_name"], cycle, now, anchor
        )
        return self.catalog.stage(pool, used + 1), used

    def reward(self, prize: dict) -> dict:
        order = self.resources.reward_order(
            {
                "m_walletCategory": prize["prize_type"],
                "m_walletName": prize["prize_name"],
                "m_count": prize["prize_num"],
            }
        )
        if order is None:
            raise ValueError(f"Empty gacha prize #{prize['no']}")
        return order

    @staticmethod
    def _aggregate(orders: list[dict]) -> list[dict]:
        totals: dict[str, int] = defaultdict(int)
        for order in orders:
            totals[order["rid"]] += order["val"]
        return [{"rid": rid, "val": value} for rid, value in totals.items()]

    def _chosen(self, state: dict, pool: dict) -> str | None:
        if not pool["select_type"]:
            return None
        for row in state["gacha_selection"]:
            if row["gacha_id"] == pool["id"]:
                return row["unit_card"]
        return None

    def _shares(self, table_id: str, chosen: str | None) -> list[tuple[dict, float]]:
        prizes = [
            {**prize, "pickup": int(UNIT_PREFIX + prize["prize_name"] == chosen)}
            if prize["is_select"]
            else prize
            for prize in self.lottery.table(table_id)
        ]
        return self.lottery.shares(prizes)

    def _lottery(self, stage: dict, batches: int, chosen: str | None) -> list[dict]:
        main = self._shares(stage["prize_table_id"], chosen)
        guaranteed, closing = 0, main
        if stage["alt_prize_table_id"]:
            guaranteed = min(stage["alt_draw"], stage["draw"])
            closing = self._shares(stage["alt_prize_table_id"], chosen)
        prizes = []
        for _ in range(batches):
            for index in range(stage["draw"]):
                late = index >= stage["draw"] - guaranteed
                prizes.append(self.lottery.pick(closing if late else main))
        return prizes

    def _extra_orders(self, stage: dict, batches: int) -> list[dict]:
        if not stage["ex_prize_group_id"]:
            return []
        prizes = self.catalog.extras(stage["ex_prize_group_id"])
        return self._aggregate([self.reward(prize) for prize in prizes] * batches)

    def _random_orders(self, stage: dict, batches: int) -> list[dict]:
        if not stage["rnd_ex_prize_group_id"] or stage["cost_num"] <= 0:
            return []
        candidates = self.catalog.randoms(stage["rnd_ex_prize_group_id"])
        return self._aggregate(
            [self.reward(self.rng.choice(candidates)) for _ in range(batches)]
        )

    def costs(self, stage: dict, batches: int) -> list[dict]:
        cost = self.resources.reward_order(
            {
                "m_walletCategory": stage["cost_type"],
                "m_walletName": stage["cost_name"],
                "m_count": stage["cost_num"] * batches,
            }
        )
        return [] if cost is None else [cost]

    def _admit(
        self, state: dict, pool: dict, requested, now: int
    ) -> tuple[int, dict, int]:
        self.require_open(state, pool, now)
        batches = self.batches(pool, requested)
        stage, used = self._stage(state, pool, now)
        limit = pool["execlimit_max_times"]
        if limit > 0 and used + batches > limit:
            raise ApiRejected("LIMIT_OVER_OF_GACHA_TRIES")
        if pool["select_type"] and self._chosen(state, pool) is None:
            raise ApiRejected("NOT_QUALIFIED")
        return batches, stage, used

    def admit_prepaid(self, state: dict, gacha_id: str, batches, now: int) -> None:
        _, stage, _ = self._admit(state, self.catalog.pool(gacha_id), batches, now)
        if not self._prepaid(stage):
            raise ApiRejected("NOT_QUALIFIED")

    def plan(
        self, state: dict, pool: dict, requested, now: int, interruption=False
    ) -> Plan:
        batches, stage, used = self._admit(state, pool, requested, now)
        if pool["gacha_type"] == BOX:
            rewards, drawn = self.boxes.pick(state, pool, batches, bool(interruption))
            return Plan(pool, stage, used, len(drawn), rewards, [], [], drawn)
        chosen = self._chosen(state, pool)
        return Plan(
            pool,
            stage,
            used,
            batches,
            [self.reward(prize) for prize in self._lottery(stage, batches, chosen)],
            self._extra_orders(stage, batches),
            self._random_orders(stage, batches),
        )

    def _advance(self, state: dict, plan: Plan, now: int) -> None:
        pool, counter = plan.pool, plan.pool["execlimit_counter_name"]
        cycle, anchor = self._cycle(pool)
        self.limits.count(state, counter, cycle, plan.batches, now, anchor)
        finished = plan.used + plan.batches >= pool["execlimit_max_times"]
        if pool["gacha_type"] != STEP or not finished:
            return
        rounds, loops = pool["step_loops"], counter + LOOP_SUFFIX
        if rounds == 0 or self.limits.used(state, loops, LIFETIME, now) + 1 < rounds:
            self.limits.restart(state, counter, now)
            self.limits.count(state, loops, LIFETIME, 1, now)

    def _press(
        self, state: dict, stamp_id: int, sheets: dict, record: dict, now: int
    ) -> list[dict]:
        if record["cell_index"] == max(sheets[record["step"]]["cells"]):
            record.update(step=record["step"] % max(sheets) + 1, cell_index=0)
        record["cell_index"] += 1
        sheet = sheets[record["step"]]
        filled = record["cell_index"] == max(sheet["cells"])
        if filled and record["step"] == max(sheets) and not sheet["reset"]:
            record["in_progress"] = False
        source = f"{STAMP_SOURCE}:{stamp_id}:{record['step']}:{record['cell_index']}"
        prizes = []
        for row in sheet["cells"][record["cell_index"]]["rewards"]["m_rewards"]:
            order = self.resources.reward_order(row)
            if order is not None:
                self.mailbox.post(state, order, source, now)
                prizes.append({**order, "inc": 0, "inbox": order["val"], "over": 0})
        return prizes

    def _stamp(self, state: dict, plan: Plan, now: int) -> list[dict]:
        stamp_id = plan.pool["stamp_id"]
        if not stamp_id:
            return []
        sheets, identity = self.catalog.sheets(stamp_id), f"{STAMP_PREFIX}{stamp_id}"
        record = next(
            (row for row in state["gacha_stamp_records"] if row["id"] == identity), None
        )
        if record is None:
            record = {
                "id": identity,
                "step": min(sheets),
                "cell_index": 0,
                "in_progress": True,
            }
            state["gacha_stamp_records"].append(record)
        prizes = []
        for _ in range(plan.batches):
            if not record["in_progress"]:
                break
            prizes += self._press(state, stamp_id, sheets, record, now)
        return prizes

    @staticmethod
    def _prize(state: dict, number: int, delivery: dict) -> dict:
        duplicate = (
            delivery["rid"].startswith(UNIT_PREFIX)
            and not delivery["inc"]
            and not delivery["inbox"]
        )
        if not duplicate:
            return {"no": number, "prize": delivery}
        card_id = int(delivery["rid"].removeprefix(UNIT_PREFIX))
        owned = next(row for row in state["units"] if row["card_id"] == card_id)
        return {
            "no": number,
            "prize": {
                key: value for key, value in delivery.items() if key != "derived"
            },
            "prize_unit_ant": {
                "lv": owned["r_unit_lv"],
                "is_dup": True,
                "dup_items": delivery.get("derived", []),
            },
        }

    def _record(self, state: dict, pool: dict, result: dict, now: int) -> None:
        history = state["gacha_history"]
        history.append(
            {"log_at": now, "gacha_id": pool["id"], "result": deepcopy(result)}
        )
        del history[: max(0, len(history) - self._settings()["history"])]

    def deliver(
        self, state: dict, before: dict, plan: Plan, costs: list[dict], now: int
    ) -> Result:
        grant = self.resources.grant
        main = grant(state, plan.rewards, now, policy="mail").deliveries
        extra = grant(state, plan.extras, now, policy="mail").deliveries
        bonus = grant(state, plan.randoms, now, policy="mail").deliveries
        stamps = self._stamp(state, plan, now)
        if plan.drawn is not None:
            self.boxes.mark(state, plan.pool, plan.drawn)
        self._advance(state, plan, now)
        self.tutorial.complete_gacha(state, plan.pool["id"], now)
        self.journal.record(
            state,
            f"gachaexec-{uuid.uuid4()}",
            GACHA_DRAW,
            now,
            {
                "gacha_id": plan.pool["id"],
                "draws": len(plan.rewards),
                "costs": costs,
                "rewards": plan.rewards,
            },
        )
        result = {
            "main_prizes": [
                self._prize(state, number, delivery)
                for number, delivery in enumerate(main, 1)
            ],
            "extra_prizes": extra or None,
            "rnd_extra_prizes": bonus or None,
            "actual_exec_num": plan.batches,
            "stamp_prizes": stamps or None,
        }
        self._record(state, plan.pool, result, now)
        return Result(result, diff_state(before, state))

    @staticmethod
    def _prepaid(stage: dict) -> bool:
        return (stage["cost_type"], stage["cost_name"]) == PLATFORM_PAYMENT

    def draw(self, state: dict, payload: dict, now: int) -> Result:
        pool = self.catalog.pool(payload["gacha_id"])
        plan = self.plan(state, pool, payload["exec_num"], now, payload["interruption"])
        if self._prepaid(plan.stage):
            raise ApiRejected("NOT_ENOUGH_COST")
        costs = self.costs(plan.stage, plan.batches)
        before = deepcopy(state)
        self.resources.charge(state, costs, now, "NOT_ENOUGH_COST")
        return self.deliver(state, before, plan, costs, now)

    def draw_prepaid(self, state: dict, gacha_id: str, batches, now: int) -> Result:
        plan = self.plan(state, self.catalog.pool(gacha_id), batches, now)
        if not self._prepaid(plan.stage):
            raise ApiRejected("NOT_QUALIFIED")
        return self.deliver(state, deepcopy(state), plan, [], now)

    def lootbox(self, source, resource_id) -> dict:
        if source not in LOOTBOX_SOURCES:
            raise ValueError(f"Unknown lootbox source {source!r}")
        for pool in self.catalog.pools():
            cost = f"wallet.{LOOTBOX}.{pool['cost_name']}"
            if pool["cost_type"] == LOOTBOX and cost == resource_id:
                return pool
        raise ApiNotFound(str(resource_id))

    def _box_pool(self, gacha_id) -> dict:
        pool = self.catalog.pool(gacha_id)
        if pool["gacha_type"] != BOX:
            raise ApiNotFound(pool["id"])
        return pool

    def box_catalog(self, gacha_id) -> list[dict]:
        return self.boxes.catalog(self._box_pool(gacha_id))

    def box_records(self, state: dict, gacha_ids) -> list[dict]:
        if not isinstance(gacha_ids, list):
            raise ValueError("gacha_ids must be a list")
        pools = [self._box_pool(identity) for identity in gacha_ids]
        return self.boxes.records(state, pools)

    def box_reset(self, state: dict, gacha_id, now: int) -> None:
        pool = self._box_pool(gacha_id)
        self.require_open(state, pool, now)
        self.boxes.reset(state, pool, now)

    def probabilities(self, state: dict, payload: dict, now: int) -> dict:
        pool = self.catalog.pool(payload["gacha_id"])
        stage, _ = self._stage(state, pool, now)
        table_id = stage["prize_table_id"]
        if payload["needs_alt_table"] and stage["alt_prize_table_id"]:
            table_id = stage["alt_prize_table_id"]
        shares = self._shares(table_id, self._chosen(state, pool))
        rarities = (
            self.lottery.by_rarity(shares) if payload["needs_rarity_table"] else {}
        )
        return {
            "prizes": [
                {
                    "no": prize["no"],
                    "rid": self.reward(prize)["rid"],
                    "val": prize["prize_num"],
                    "p": percent(share),
                }
                for prize, share in shares
            ],
            "rarities": [
                {"rid": name, "p": percent(total)} for name, total in rarities.items()
            ],
        }

    @staticmethod
    def history(state: dict) -> list[dict]:
        return list(reversed(state["gacha_history"]))

    def select(self, state: dict, payload: dict, now: int) -> None:
        choices = {}
        for entry in payload["select_gacha_datas"]:
            pool = self.catalog.pool(entry["gacha_id"])
            self.require_open(state, pool, now)
            candidates = {
                UNIT_PREFIX + prize["prize_name"]
                for prize in self.lottery.table(pool["prize_table_id"])
                if prize["is_select"]
            }
            if not pool["select_type"] or entry["unit_card"] not in candidates:
                raise ValueError(
                    f"{entry['unit_card']!r} cannot be featured in {pool['id']}"
                )
            choices[pool["id"]] = entry["unit_card"]
        kept = [
            row for row in state["gacha_selection"] if row["gacha_id"] not in choices
        ]
        state["gacha_selection"] = kept + [
            {"gacha_id": identity, "unit_card": card}
            for identity, card in choices.items()
        ]

    def selections(self, state: dict, now: int) -> list[dict]:
        chosen = {row["gacha_id"]: row["unit_card"] for row in state["gacha_selection"]}
        return [
            {"gacha_id": pool["id"], "unit_card": chosen.get(pool["id"])}
            for pool in self._listed(now)
            if pool["select_type"]
        ]

    @staticmethod
    def stamp_records(state: dict, stamp_ids: list[int]) -> list[dict]:
        wanted = {f"{STAMP_PREFIX}{identity}" for identity in stamp_ids}
        return [row for row in state["gacha_stamp_records"] if row["id"] in wanted]
