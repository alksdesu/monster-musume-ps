from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
import json
import secrets
import uuid

from .errors import ApiRejected
from .events import chapter_event
from .journal import QUEST_COMPLETE, QUEST_CYCLE
from .quest_gates import quest_record
from .sync import COLLECTIONS, Result, diff_state, scoped
from .teams import snapshot, team_log_key


LOG_KEY_LENGTH = 15
# The client reads bit 1 as a full rating earned without a support unit.
FULL_RATING, SELF_CLEAR = 1, 2
EVERYONE_KILL = 4
TRIPLE_SORTIE = 3
SWEEPABLE = frozenset({1, 3, 4, 6, 7, 9, 10})
DUNGEONS = frozenset({6, 7})
DUNGEON_SWEEPS = 10


@dataclass
class Settlement:
    report: dict
    costs: list
    kills: int = 0


class BattleEngine:
    def __init__(
        self,
        masters,
        config,
        resources,
        progression,
        tutorial,
        social,
        journal,
        rng,
        *,
        gates,
        prizes,
        secrets,
        training,
        tower,
    ):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.progression = progression
        self.tutorial = tutorial
        self.social = social
        self.journal = journal
        self.rng = rng
        self.gates = gates
        self.prizes = prizes
        self.secrets = secrets
        self.training = training
        self.tower = tower

    def _quest(self, identity: int) -> dict:
        try:
            return self.masters.lookup("quest_data", "id", identity)
        except KeyError as error:
            raise ApiRejected("INVALID_QUEST") from error

    _record = staticmethod(quest_record)

    def _raid(self, quest: dict) -> bool:
        event = chapter_event(self.masters, quest["chapter"], (EVERYONE_KILL,))
        return event is not None

    def _mode(self, quest: dict, mode) -> int:
        # Only everyone's subjugation offers the triple sortie the client marks as 1.
        if (
            type(mode) is not int
            or mode not in (0, 1)
            or (mode and not self._raid(quest))
        ):
            raise ApiRejected("INVALID_STATEMENT")
        return mode

    @staticmethod
    def _party(state: dict, use_deck: dict) -> list[int]:
        deck = next(
            (
                row
                for row in state["compositions"]
                if row["section"] == use_deck["section"] and row["no"] == use_deck["no"]
            ),
            None,
        )
        if deck is None:
            raise ApiRejected("INVALID_STATEMENT")
        party = [
            slot["iid"] for raw in deck["slots"] if (slot := json.loads(raw))["t"] == 1
        ]
        owned = {row["instance_id"] for row in state["units"]}
        if not party or len(party) != len(set(party)) or not set(party) <= owned:
            raise ApiRejected("INVALID_STATEMENT")
        return party

    def _check_costs(self, state: dict, costs: list) -> None:
        combined = self._aggregate(costs)
        for order in combined:
            if self.resources.balance(state, order["rid"]) < order["val"]:
                raise ApiRejected("NOT_ENOUGH_COST")

    def _fixed_rewards(self, table: dict) -> list:
        return [
            order
            for row in table["m_rewards"]
            if (order := self.resources.reward_order(row)) is not None
        ]

    def _roll_rewards(self, table: dict, first: bool, enabled: bool) -> list:
        if not enabled:
            return []
        rewards = []
        for row in table["m_rewards"]:
            if row["m_count"] == 0:
                continue
            chance = (
                row["m_firstProbability"]
                if first and row["m_firstProbability"] > 0
                else row["m_probability"]
            )
            if self.rng.random() * 100 < chance:
                order = self.resources.reward_order(row)
                if order is not None:
                    rewards.append(order)
        return rewards

    @staticmethod
    def _aggregate(orders: list) -> list:
        values = defaultdict(int)
        for order in orders:
            values[order["rid"]] += order["val"]
        return [{"rid": rid, "val": value} for rid, value in values.items() if value]

    @staticmethod
    def new_record(quest: dict) -> dict:
        return {
            "category": quest["category"],
            "section": quest["chapter"],
            "quest_id": quest["id"],
            "record_rating": 0,
            "wins": 0,
            "tries": 0,
            "flags": 0,
            "ec_best_score": 0,
            "ec_total_score": 0,
            "t_best_rank": 0,
            "wh_best_score": 0,
            "wh_total_score": 0,
        }

    def _open(
        self,
        state: dict,
        quest: dict,
        statement: dict,
        now: int,
        *,
        costs_enabled: bool,
        random_rewards: bool,
        capture_log: bool,
        swept: bool = False,
    ) -> dict:
        self.gates.check(state, quest, now)
        party = self._party(state, statement["use_deck"])
        mode = self._mode(quest, statement["r_mode"])
        training = (
            self.training.swept(state, quest)
            if swept
            else self.training.selection(state, quest, statement["t_menus"])
        )
        support = self.social.supporter(state, quest, statement, now)
        if support is not None:
            cards = {
                row["card_id"] for row in state["units"] if row["instance_id"] in party
            }
            if support["unit"]["card_id"] in cards:
                raise ApiRejected("INVALID_STATEMENT")
        self.progression.recover_stamina(state, now)
        costs = self.progression.stamina_cost(state, quest) if costs_enabled else []
        if mode:
            costs = [{**row, "val": row["val"] * TRIPLE_SORTIE} for row in costs]
        self._check_costs(state, costs)
        record = self._record(state, quest["id"])
        if record is None:
            record = self.new_record(quest)
            state["quest_records"].append(record)
        first = record["wins"] == 0
        drops = []
        for slot, row in enumerate(quest["droptable"]["m_rewards"]):
            selected = self._roll_rewards({"m_rewards": [row]}, first, random_rewards)
            if selected:
                drops.append({"slot": slot, "item": selected[0]})
        prizes = self.prizes.draw(record, quest) if random_rewards else []
        record["tries"] += 1
        record["log_key"] = list(secrets.token_bytes(LOG_KEY_LENGTH))
        return {
            "status": "active",
            "quest": deepcopy(quest),
            "started_at": now,
            "party": party,
            "support": support,
            "statement": deepcopy(statement),
            "costs": costs,
            "first": first,
            "previous_rating": record["record_rating"],
            "drop_list": drops,
            "random_rewards": random_rewards,
            "capture_log": capture_log,
            "rules": deepcopy(self.config.read()["battle"]),
            "rating_boost": list(self.masters.property("IntArray", 4)),
            "sub_skills": prizes,
            "training": training,
            "mode": mode,
            "raid": self._raid(quest),
            "log_key": list(record["log_key"]),
        }

    def _check_completion(
        self, transaction: dict, result: str, statement: dict
    ) -> None:
        rating = statement["rating"]
        if (
            result not in ("COMPLETE", "FAILURE")
            or type(rating) is not int
            or not 0 <= rating <= self.masters.property("Int", 41)
            or (result == "COMPLETE" and rating == 0)
        ):
            raise ApiRejected("INVALID_STATEMENT")
        slots = statement["loot_list"]
        offered = {row["slot"] for row in transaction["drop_list"]}
        if len(slots) != len(set(slots)) or not set(slots) <= offered:
            raise ApiRejected("INVALID_STATEMENT")
        quest, score = transaction["quest"], statement["ec_score"]
        if (
            type(score) is not int
            or score < 0
            or (self.tower.applies(quest) and not self.tower.valid(statement["tower"]))
            or (
                quest["isWildHunt"]
                and not self.secrets.valid_log(statement["log_data"])
            )
        ):
            raise ApiRejected("INVALID_STATEMENT")

    def _log_team(
        self, state: dict, transaction: dict, payload: dict, team: dict, now: int
    ) -> str | None:
        quest, statement = transaction["quest"], payload["statement"]
        if (
            payload["result"] != "COMPLETE"
            or not quest["is_team_loging"]
            or not transaction["capture_log"]
        ):
            return None
        key = team_log_key(quest["id"], state["player"]["player_code"])
        kept = state["team_logs"].get(key)
        # Hunts keep the highest score; other quests keep the best rating.
        earned = self.secrets.score(quest, statement), statement["rating"]
        if kept is None or earned >= (kept["wild_hunt_score"], kept["rating"]):
            state["team_logs"][key] = {
                "quest_id": quest["id"],
                "created_at": now,
                "rating": statement["rating"],
                "wild_hunt_score": earned[0],
                "txid": payload["txid"],
                "team": deepcopy(team),
                "log_data": list(statement["log_data"]),
            }
        return key

    def _count_kills(self, record: dict, transaction: dict, statement: dict) -> int:
        kills, multiplier = statement["ec_score"], 1
        if transaction["quest"]["isEndless"]:
            # Several targets falling together may carry the count past the cap.
            kills = min(kills, self.config.read()["battle"]["endless_kill_cap"])
        elif transaction["raid"]:
            multiplier = TRIPLE_SORTIE if transaction["mode"] else 1
        else:
            return 0
        record["ec_best_score"] = max(record["ec_best_score"], kills)
        record["ec_total_score"] += kills * multiplier
        return kills * multiplier

    def _settle(
        self, state: dict, transaction: dict, result: str, statement: dict, now: int
    ) -> Settlement:
        self.progression.recover_stamina(state, now)
        quest, rules = transaction["quest"], transaction["rules"]
        costs = deepcopy(transaction["costs"])
        if result != "COMPLETE":
            costs = [
                {
                    "rid": row["rid"],
                    "val": row["val"] * rules["failure_cost_percent"] // 100,
                }
                for row in costs
            ]
        self.resources.charge(state, costs, now, "NOT_ENOUGH_COST")
        regular = {"gold": 0, "rank_exp": 0, "unit_exp": 0, "unit_pt": 0}
        report = {"regular_rewards": regular}
        if result != "COMPLETE":
            return Settlement(report, costs)
        rating = statement["rating"]
        maximum = self.masters.property("Int", 41)
        regular.update(
            gold=int(
                quest["gold"]
                * transaction["rating_boost"][rating - 1]
                / 100
                * rules["gold_multiplier"]
            ),
            rank_exp=int(quest["playerExp"] * rules["rank_exp_multiplier"]),
            unit_exp=int(quest["unitExp"] * rules["unit_exp_multiplier"]),
            unit_pt=rules["unit_points"],
        )
        self.resources.grant(
            state,
            [{"rid": "wallet.point.gold", "val": regular["gold"]}],
            now,
            policy="mail",
        )
        self.progression.award_player_exp(state, regular["rank_exp"], now)
        self.progression.add_unit_exp(
            state, transaction["party"], regular["unit_exp"], now
        )
        drops = {row["slot"]: row["item"] for row in transaction["drop_list"]}
        groups = {
            "initial_rewards": self._fixed_rewards(quest["beginningRewardtable"])
            if transaction["first"]
            else [],
            "master_rewards": self._fixed_rewards(quest["highLevelRewardtable"])
            if rating == maximum and transaction["previous_rating"] < maximum
            else [],
            "clear_rewards": self.training.boost(
                transaction["training"],
                self._roll_rewards(
                    quest["cleartable"],
                    transaction["first"],
                    transaction["random_rewards"],
                ),
            ),
            "loot_rewards": [drops[slot] for slot in statement["loot_list"]],
        }
        for key, orders in groups.items():
            if orders:
                self.resources.grant(state, orders, now, policy="mail")
                report[key] = orders
        record = self._record(state, quest["id"])
        record["wins"] += 1
        record["record_rating"] = max(record["record_rating"], rating)
        if rating == maximum:
            record["flags"] |= FULL_RATING
            if transaction["support"] is None:
                record["flags"] |= SELF_CLEAR
        self.tutorial.complete_quest(state, quest["id"], now)
        kills = self._count_kills(record, transaction, statement)
        prizes = transaction["sub_skills"]
        if prizes:
            self.resources.grant(state, prizes, now, policy="mail")
            self.prizes.settle(record, quest, prizes)
            report["sub_skill_rewards"] = prizes
        self.training.record(record, transaction["training"])
        if quest["isWildHunt"]:
            report.update(
                self.secrets.settle(state, record, transaction, statement, now)
            )
        self.secrets.refresh(state, quest)
        if self.tower.applies(quest):
            scored = self.tower.settle(state, record, transaction, statement, now)
            report["event_result"] = {"tower": scored}
        return Settlement(report, costs, kills)

    def _journal(
        self,
        state: dict,
        identity: str,
        transaction: dict,
        now: int,
        *,
        result: str,
        rating: int,
        costs: list,
        kills: int = 0,
    ) -> None:
        quest, support = transaction["quest"], transaction["support"]
        self.journal.record(
            state,
            identity,
            QUEST_COMPLETE,
            now,
            {
                "quest_id": quest["id"],
                "category": quest["category"],
                "section": quest["chapter"],
                "rating": rating,
                "result": result,
                "first_clear": result == "COMPLETE" and transaction["first"],
                "costs": costs,
                "kills": kills,
                "unit_ids": list(transaction["party"]),
                "support_owner": support["owner"] if support else None,
                "support_card": support["unit"]["card_id"] if support else None,
            },
        )

    # A story quest never reports its end, so opening it is clearing it.
    def _watch(self, state: dict, identity: str, transaction: dict, now: int) -> None:
        costs = transaction["costs"]
        self.resources.charge(state, costs, now, "NOT_ENOUGH_COST")
        self._record(state, transaction["quest"]["id"])["wins"] += 1
        self._journal(
            state, identity, transaction, now, result="COMPLETE", rating=0, costs=costs
        )

    def start(
        self,
        state: dict,
        payload: dict,
        now: int,
        *,
        costs_enabled=True,
        random_rewards=True,
        capture_log=True,
    ) -> Result:
        before = deepcopy(state)
        quest = self._quest(payload["quest_id"])
        transaction = self._open(
            state,
            quest,
            payload["statement"],
            now,
            costs_enabled=costs_enabled,
            random_rewards=random_rewards,
            capture_log=capture_log,
        )
        transactions = state["quest_transactions"]
        for superseded in [
            key for key, row in transactions.items() if row["status"] == "active"
        ]:
            del transactions[superseded]
        transaction_id = f"questexec-{uuid.uuid4()}"
        if quest["isScenarioOnly"]:
            self._watch(state, transaction_id, transaction, now)
        else:
            transactions[transaction_id] = transaction
        return Result(
            {
                "txid": transaction_id,
                "prepaid_costs": [],
                "postpaid_costs": transaction["costs"],
                "drop_list": transaction["drop_list"],
                "sub_skill_list": transaction["sub_skills"],
            },
            diff_state(before, state),
        )

    @staticmethod
    def _replay(state: dict, transaction: dict) -> Result:
        updates = {}
        for key in transaction["sync_keys"]:
            if key == "player":
                updates[key] = [state["player"]]
            elif key == "module_prop":
                updates[key] = state["modules"]
            else:
                updates[key] = state[COLLECTIONS[key][0]]
        return Result(
            deepcopy(transaction["response_data"]),
            scoped(state, **deepcopy(updates)) if updates else None,
        )

    def _prune(self, transactions: dict) -> None:
        keep = self.config.read()["retention"]["completed_quest_transactions"]
        completed = [
            key for key, row in transactions.items() if row["status"] == "completed"
        ]
        for expired in completed[: max(0, len(completed) - keep)]:
            del transactions[expired]

    def finish(self, state: dict, payload: dict, now: int) -> Result:
        transactions = state["quest_transactions"]
        transaction = transactions.get(payload["txid"])
        if transaction is None:
            raise ApiRejected("INVALID_QUEST")
        if transaction["status"] == "completed":
            if transaction["completion_request"] != payload:
                raise ApiRejected("INVALID_STATEMENT")
            return self._replay(state, transaction)
        result, statement = payload["result"], payload["statement"]
        self._check_completion(transaction, result, statement)
        before = deepcopy(state)
        team = snapshot(
            state,
            transaction["party"],
            transaction["statement"]["use_deck"],
            transaction["support"],
        )
        settlement = self._settle(state, transaction, result, statement, now)
        quest = transaction["quest"]
        self._journal(
            state,
            payload["txid"],
            transaction,
            now,
            result=result,
            rating=statement["rating"],
            costs=settlement.costs,
            kills=settlement.kills,
        )
        data = {
            "quest_id": quest["id"],
            "result": result,
            "postpaid_costs": [],
            "report": settlement.report,
            "team_log_key": self._log_team(state, transaction, payload, team, now),
        }
        sync = diff_state(before, state)
        transactions[payload["txid"]] = {
            "status": "completed",
            "completed_at": now,
            "quest_id": quest["id"],
            "team": team,
            "completion_request": deepcopy(payload),
            "response_data": deepcopy(data),
            "sync_keys": list(sync or {}),
        }
        self._prune(transactions)
        return Result(data, sync)

    def cycle(self, state: dict, payload: dict, now: int) -> Result:
        quest = self._quest(payload["quest_id"])
        if (
            quest["category"] not in SWEEPABLE
            or quest["isScenarioOnly"]
            or self._raid(quest)
        ):
            raise ApiRejected("INVALID_QUEST")
        count = payload["num_of_exec"]
        limit = (
            DUNGEON_SWEEPS
            if quest["category"] in DUNGEONS
            else self.masters.property("Int", 45)
        )
        if type(count) is not int or not 1 <= count <= limit:
            raise ApiRejected("TOO_LARGE_NUM_OF_CYCLE_EXEC")
        self.gates.check(state, quest, now, count)
        record = self._record(state, quest["id"])
        rating = self.masters.property("Int", 41)
        # Training quests open their sweep on any clear, the rest on a full rating.
        mastered = record is not None and (
            record["wins"] > 0
            if self.training.event(quest)
            else record["record_rating"] >= rating
        )
        if not mastered:
            raise ApiRejected("NOT_QUALIFIED")
        before = deepcopy(state)
        self.progression.recover_stamina(state, now)
        costs = [
            {"rid": row["rid"], "val": row["val"] * count}
            for row in self.progression.stamina_cost(state, quest)
        ]
        tickets = [{"rid": "wallet.ticket.quest_skip", "val": count}]
        self._check_costs(state, costs + tickets)
        self.resources.spend(state, tickets, now)
        statement = {
            "use_deck": payload["statement"]["use_deck"],
            "rental_unit": None,
            "rental_unit_iid": 0,
            "t_menus": [],
            "r_mode": 0,
        }
        regular = {"gold": 0, "rank_exp": 0, "unit_exp": 0, "unit_pt": 0}
        rewards = {
            "clear_rewards": [],
            "loot_rewards": [],
            "skip_rewards": [],
            "sub_skill_rewards": [],
        }
        for _ in range(count):
            transaction = self._open(
                state,
                quest,
                statement,
                now,
                costs_enabled=True,
                random_rewards=True,
                capture_log=False,
                swept=True,
            )
            settlement = self._settle(
                state,
                transaction,
                "COMPLETE",
                {
                    "rating": rating,
                    "ec_score": 0,
                    "tower": None,
                    "loot_list": [row["slot"] for row in transaction["drop_list"]],
                    "log_data": [],
                },
                now,
            )
            for name, amount in settlement.report["regular_rewards"].items():
                regular[name] += amount
            for name in ("clear_rewards", "loot_rewards", "sub_skill_rewards"):
                rewards[name].extend(settlement.report.get(name, []))
            skipped = self._roll_rewards(quest["delegatetable"], False, True)
            self.resources.grant(state, skipped, now, policy="mail")
            rewards["skip_rewards"].extend(skipped)
        self.journal.record(
            state,
            f"questcycle-{uuid.uuid4()}",
            QUEST_CYCLE,
            now,
            {
                "quest_id": quest["id"],
                "category": quest["category"],
                "section": quest["chapter"],
                "num_of_exec": count,
                "costs": costs + tickets,
                "unit_ids": list(transaction["party"]),
            },
        )
        return Result(
            {
                "quest_id": quest["id"],
                "num_of_exec": count,
                "costs": costs + tickets,
                "report": {
                    "regular_rewards": regular,
                    **{name: self._aggregate(rows) for name, rows in rewards.items()},
                },
            },
            diff_state(before, state),
        )
