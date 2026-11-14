from copy import deepcopy
import uuid

from .calendar import FOREVER
from .errors import ApiNotFound, ApiRejected
from .events import raised_by_event
from .journal import SHOP_EXCHANGE
from .limits import DAILY, DATETIME, LIFETIME, MONTHLY, WEEKLY
from .periods import SALES_PREFIX
from .resources import UnknownResource
from .sync import Result, diff_state


COUNTER_PREFIX = "shop."
FAVOR_PREFIX = "shop_sp_favor."
MAIL_SOURCE = "shop.product"
BLESSING = "blessing"
FAVOR_SOURCE = "shop.sp_favor"
PLATFORM_PAYMENT = ("point", "payment")
# targetPlatform values the DMM R18 PC client keeps: All, DMM, DMM_R18.
PLATFORMS = (0, 1, 6)
AWAKENING, UNIT = "spap", "unit"
UNIT_PREFIX = "unit.card."
GOLD = "wallet.point.gold"
OUT_OF_SCHEDULE = "OUT_OF_SCHEDULE"
SOLD_OUT = "LIMIT_OVER_OF_EXCHANGE_COUNT"
SOURCES = (
    "shop_data",
    "wallet_template",
    "item_template",
    "equipment",
    "attach_ability_data",
    "job_data",
    "unit_rarity",
)


def counter(row: dict) -> str:
    return f"{COUNTER_PREFIX}{row['hotfix_shop_id'] or row['id']}"


class Shop:
    def __init__(self, masters, config, resources, limits, periods, mailbox, journal):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.limits = limits
        self.periods = periods
        self.mailbox = mailbox
        self.journal = journal
        self._documents = ()
        self._rows: dict[int, dict] = {}
        self._prices: dict[str, int] = {}
        self._medicines: dict[str, dict] = {}
        self._systems: dict[int, int] = {}
        self._rarities: dict[int, str] = {}

    def _refresh(self) -> None:
        documents = tuple(self.masters.document(name) for name in SOURCES)
        if self._documents and all(
            current is previous for current, previous in zip(documents, self._documents)
        ):
            return
        goods, wallets, items, equipment, skills, jobs, rarities = (
            document["table"] for document in documents
        )
        prices = {
            f"wallet.{row['category']}.{row['name']}": row["sale_price"]
            for row in wallets
            if row["salable"]
        }
        prices.update(
            (row["item_id"], row["sale_price"])
            for row in [*items, *equipment]
            if row["salable"]
        )
        prices.update(
            (f"skill.attachable.{row['id']}", row["salePrice"])
            for row in skills
            if row["salable"]
        )
        self._rows = {row["id"]: row for row in goods}
        self._prices = prices
        self._medicines = {
            row["name"]: row for row in wallets if row["category"] == AWAKENING
        }
        self._systems = {row["id"]: row["system_id"] for row in jobs}
        self._rarities = {row["id"]: row["idName"] for row in rarities}
        self._documents = documents

    def _row(self, shop_id) -> dict:
        self._refresh()
        if type(shop_id) is not int or shop_id not in self._rows:
            raise ApiNotFound(str(shop_id))
        return self._rows[shop_id]

    @staticmethod
    def _local(row: dict) -> bool:
        return row["targetPlatform"] in PLATFORMS

    def goods(self, shop_id) -> dict:
        row = self._row(shop_id)
        if not self._local(row):
            raise ApiNotFound(str(shop_id))
        return row

    @staticmethod
    def _quantity(value) -> int:
        if type(value) is not int or value < 1:
            raise ValueError("quantity must be a positive integer")
        return value

    @staticmethod
    def _cycle(row: dict) -> tuple[str, int | str | None]:
        anchors = {
            DAILY: None,
            WEEKLY: row["DayOfWeekForReset"],
            MONTHLY: row["DayOfMonthForReset"],
            DATETIME: row["DateTimeForReset"],
        }
        trigger = row["LimitResetTrigger"]
        return (trigger, anchors[trigger]) if trigger in anchors else (LIFETIME, None)

    @staticmethod
    def _prepaid(row: dict) -> bool:
        return (row["price_category"], row["price_name"]) == PLATFORM_PAYMENT

    @staticmethod
    def _first_reward(row: dict) -> dict:
        return row["shopItems"]["m_rewards"][0]

    def _targeted(self, row: dict) -> bool:
        return self._first_reward(row)["m_walletCategory"] == AWAKENING

    def _refusal(self, state: dict, row: dict, quantity: int, now: int) -> str | None:
        if row["sales_period"]:
            opened = self.periods.active(state, f"{SALES_PREFIX}{row['id']}", now)
        else:
            opened = self.masters.in_term(row["startDate"], row["endDate"], now)
        if not opened:
            return OUT_OF_SCHEDULE
        if row["buylimit"]:
            cycle, anchor = self._cycle(row)
            used = self.limits.used(state, counter(row), cycle, now, anchor)
            if used + quantity > row["buylimit"]:
                return SOLD_OUT
        return None

    def _require_available(
        self, state: dict, row: dict, quantity: int, now: int
    ) -> None:
        refusal = self._refusal(state, row, quantity, now)
        if refusal is not None:
            raise ApiNotFound(f"{row['id']} ({refusal})")

    def _require_redeemable(
        self, state: dict, row: dict, rewards: list[dict], now: int
    ) -> None:
        if not all(
            self.resources.redeemable(state, order["rid"], now) for order in rewards
        ):
            raise ApiNotFound(f"{row['id']} ({OUT_OF_SCHEDULE})")

    def _costs(self, row: dict, quantity: int) -> list[dict]:
        total = row["price"] * quantity
        if not total:
            return []
        price = {
            "m_walletCategory": row["price_category"],
            "m_walletName": row["price_name"],
            "m_count": total,
        }
        return [self.resources.reward_order(price)]

    @staticmethod
    def _blessing_uses(row: dict) -> int:
        return sum(
            reward["m_count"]
            for reward in row["shopItems"]["m_rewards"]
            if reward["m_walletCategory"] == BLESSING
        )

    def _rewards(self, row: dict, quantity: int) -> list[dict]:
        if self._blessing_uses(row) and self.periods.blessing(row["id"]) is None:
            raise NotImplementedError(
                f"Shop item {row['id']} holds an unknown blessing"
            )
        try:
            orders = [
                self.resources.reward_order(
                    {**reward, "m_count": reward["m_count"] * quantity}
                )
                for reward in row["shopItems"]["m_rewards"]
                if reward["m_walletCategory"] != BLESSING
            ]
        except UnknownResource as error:
            raise NotImplementedError(
                f"Shop item {row['id']} holds an unsupported reward"
            ) from error
        return [order for order in orders if order is not None]

    def _pay(self, state: dict, costs: list[dict], now: int) -> None:
        self.resources.charge(state, costs, now, "NOT_ENOUGH_CURRENCY")

    def _immediate(self, rid: str) -> bool:
        return self.mailbox.currency(rid) or self.resources.entitlement(rid)

    def _deliver(
        self, state: dict, row: dict, quantity: int, rewards: list[dict], now: int
    ) -> None:
        uses = self._blessing_uses(row) * quantity
        if uses:
            self.periods.bless(state, self.periods.blessing(row["id"]), uses, now)
        direct, boxed = rewards, []
        if row["present_box"]:
            direct = [order for order in rewards if self._immediate(order["rid"])]
            boxed = [order for order in rewards if not self._immediate(order["rid"])]
        source = f"{MAIL_SOURCE}:{row['id']}"
        for order in boxed:
            self.mailbox.post(state, order, source, now)
        self.resources.grant(state, direct, now, "mail", source)

    def _count(self, state: dict, row: dict, quantity: int, now: int) -> None:
        cycle, anchor = self._cycle(row)
        self.limits.count(state, counter(row), cycle, quantity, now, anchor)

    def _complete(
        self,
        state: dict,
        before: dict,
        row: dict,
        quantity: int,
        costs: list[dict],
        rewards: list[dict],
        now: int,
    ) -> Result:
        self._count(state, row, quantity, now)
        self.journal.record(
            state,
            f"shopexchange-{uuid.uuid4()}",
            SHOP_EXCHANGE,
            now,
            {
                "shop_id": row["id"],
                "quantity": quantity,
                "costs": costs,
                "rewards": rewards,
            },
        )
        return Result({"_z0": 0}, diff_state(before, state))

    def _potential_cap(self) -> int:
        return self.config.read()["resources"]["duplicate_unit"]["potential_cap"]

    def _awakened(self, state: dict, row: dict) -> bool:
        reward = self._first_reward(row)
        if reward["m_walletCategory"] != UNIT:
            return False
        cap = self._potential_cap()
        return any(
            unit["card_id"] == int(reward["m_walletName"])
            and unit["upa"]["all_pt"] >= cap
            for unit in state["units"]
        )

    def exchange(self, state: dict, payload: dict, now: int) -> Result:
        row = self.goods(payload["shop_id"])
        quantity = self._quantity(payload["quantity"])
        if self._targeted(row):
            raise ValueError(f"Shop item {row['id']} needs a target unit")
        if self._prepaid(row):
            raise ApiRejected("NOT_ENOUGH_CURRENCY")
        self._require_available(state, row, quantity, now)
        if self._awakened(state, row):
            raise ApiRejected("INVALID_UNIT_CONDITION")
        costs, rewards = self._costs(row, quantity), self._rewards(row, quantity)
        self._require_redeemable(state, row, rewards, now)
        before = deepcopy(state)
        self._pay(state, costs, now)
        self._deliver(state, row, quantity, rewards, now)
        return self._complete(state, before, row, quantity, costs, rewards, now)

    def _prepaid_goods(
        self, state: dict, product_id: str, quantity: int, now: int
    ) -> tuple[dict, list[dict]]:
        row = self.goods(int(product_id))
        if not self._prepaid(row) or self._targeted(row):
            raise ApiNotFound(product_id)
        self._require_available(state, row, quantity, now)
        rewards = self._rewards(row, quantity)
        self._require_redeemable(state, row, rewards, now)
        return row, rewards

    def admit_prepaid(
        self, state: dict, product_id: str, quantity: int, now: int
    ) -> None:
        self._prepaid_goods(state, product_id, quantity, now)

    def _favor(self, state: dict, row: dict, now: int) -> None:
        favor = self.config.read()["shop"]["first_purchase"]
        if not favor or now < favor["start_at"]:
            return
        if row["tab_category"] != favor["tab"] or row["price"] < favor["min_price"]:
            return
        counter = f"{FAVOR_PREFIX}{favor['no']}"
        if self.limits.used(state, counter, LIFETIME, now):
            return
        reward = {"rid": f"{UNIT_PREFIX}{favor['unit_card']}", "val": 1}
        self.resources.check([reward])
        source = f"{FAVOR_SOURCE}:{favor['no']}"
        self.mailbox.post(state, reward, source, now, favor["message"])
        self.limits.count(state, counter, LIFETIME, 1, now)

    def redeem(self, state: dict, product_id: str, quantity: int, now: int) -> Result:
        row, rewards = self._prepaid_goods(state, product_id, quantity, now)
        before = deepcopy(state)
        self._deliver(state, row, quantity, rewards, now)
        self._favor(state, row, now)
        return self._complete(state, before, row, quantity, [], rewards, now)

    def favors(self) -> list[dict]:
        favor = self.config.read()["shop"]["first_purchase"]
        if not favor:
            return []
        return [
            {
                "no": favor["no"],
                "start_at": favor["start_at"],
                "end_at": FOREVER,
                "is_acceptable_of_card": True,
            }
        ]

    def _target(self, state: dict, row: dict, instance_id, now: int) -> dict:
        medicine = self._medicines[self._first_reward(row)["m_walletName"]]
        unit = next(
            (unit for unit in state["units"] if unit["instance_id"] == instance_id),
            None,
        )
        if unit is None:
            raise ApiRejected("INVALID_TARGET")
        card = self.masters.asset("unitcard", unit["card_id"])
        if (
            self._rarities[card["rarityId"]] != medicine["unit_rarity"]
            or self._systems[card["classId"]] != medicine["class_system_id"]
            # An event that is raising the unit decides its awakening by itself.
            or raised_by_event(self.masters, unit["card_id"], now)
        ):
            raise ApiRejected("INVALID_TARGET")
        return unit

    def awaken(self, state: dict, payload: dict, now: int) -> Result:
        row = self._row(payload["shop_id"])
        if not self._targeted(row):
            raise ValueError(f"Shop item {row['id']} grants no awakening points")
        if not self._local(row):
            raise ApiRejected("MISSMATCH_TO_PLATFORM")
        refusal = self._refusal(state, row, 1, now)
        if refusal is not None:
            raise ApiRejected(refusal)
        unit = self._target(state, row, payload["instance_id"], now)
        cap = self._potential_cap()
        if unit["upa"]["all_pt"] >= cap:
            raise ApiRejected("TARGET_IS_REACHED_LIMIT")
        costs, rewards = self._costs(row, 1), self._rewards(row, 1)
        before = deepcopy(state)
        self._pay(state, costs, now)
        points = self._first_reward(row)["m_count"]
        unit["upa"]["all_pt"] = min(cap, unit["upa"]["all_pt"] + points)
        unit["updated_at"] = now
        return self._complete(state, before, row, 1, costs, rewards, now)

    def sell(self, state: dict, payload: dict, now: int) -> Result:
        self._refresh()
        orders = payload["sell_orders"]
        if not isinstance(orders, list) or not orders:
            raise ValueError("sell_orders must be a non-empty list")
        goods, income = [], 0
        for order in orders:
            rid, amount = order["resource_id"], self._quantity(order["amount"])
            if rid not in self._prices:
                raise ApiRejected("HAS_INVALID_RESOURCE_ID")
            goods.append({"rid": rid, "val": amount})
            income += self._prices[rid] * amount
        before = deepcopy(state)
        self.resources.charge(state, goods, now, "HAS_INVALID_RESOURCE_ID")
        self.resources.grant(state, [{"rid": GOLD, "val": income}], now, "discard")
        return Result(sync=diff_state(before, state))
