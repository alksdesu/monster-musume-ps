from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
import re

from .errors import ApiRejected
from .initial import bom, unit
from .loginbonus import UNLOCK, UNLOCK_PREFIX, holds, unlocked
from .passes import PassCatalog, record
from .progression import player_rank


UNCOUNTED = ("bonus_pass", "pass_unlock", "sheet_unlock")
ENTITLEMENTS = (*UNCOUNTED, "experience", "stamina")


class UnknownResource(ValueError):
    pass


class InsufficientResource(ValueError):
    def __init__(self, rid: str, required: int, available: int):
        self.rid = rid
        self.required = required
        self.available = available
        super().__init__(f"Insufficient {rid}: need {required}, have {available}")


class ResourceCapacityError(ValueError):
    def __init__(self, rid: str, requested: int, remaining: int):
        self.rid = rid
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"Capacity exceeded for {rid}: need {requested}, room {remaining}"
        )


@dataclass
class ResourceChanges:
    deliveries: list[dict] = field(default_factory=list)
    updates: dict[str, list | dict] = field(default_factory=dict)


@dataclass(frozen=True)
class _Definition:
    kind: str
    key: str | int | tuple
    capacity: int | None = None
    revision: int | None = None


class _Batch:
    def __init__(self, state: dict, source: str | None = None):
        self.state = state
        self.source = source
        self.values: dict = {}
        self.updates: dict[str, dict] = defaultdict(dict)
        self.module_updates: dict[str, dict] = defaultdict(dict)

    def read(self, key: str):
        return self.values[key] if key in self.values else self.state[key]

    def edit(self, key: str):
        if key not in self.values:
            self.values[key] = deepcopy(self.state[key])
        return self.values[key]

    def allocate(self, kind: str) -> int:
        sequences = self.edit("resource_sequences")
        value = sequences[kind]
        sequences[kind] += 1
        return value

    def commit(self, deliveries: list[dict]) -> ResourceChanges:
        updates = {key: list(rows.values()) for key, rows in self.updates.items()}
        if self.module_updates:
            updates["module_prop"] = {
                key: list(rows.values()) for key, rows in self.module_updates.items()
            }
        self.state.update(self.values)
        return ResourceChanges(deliveries, deepcopy(updates))


FREE_COSTS = "free_costs"


class Resources:
    _table_names = (
        "wallet_template",
        "item_template",
        "equipment",
        "attach_ability_data",
        "item_limit_table",
        "bonus_pass_data",
        "unit_rarity",
        "login_bonus",
    )
    _unit_id = re.compile(r"unit\.card\.([1-9][0-9]*)\Z")
    _bom_id = re.compile(r"bom\.card\.([1-9][0-9]*)\Z")

    def __init__(self, masters, config, mailbox, progression):
        self.masters = masters
        self.config = config
        self.mailbox = mailbox
        self.progression = progression
        self.passes = PassCatalog(masters)
        self._documents = ()
        self._definitions: dict[str, _Definition] = {}
        self._limits: dict[str, list] = {}
        self._rarities: dict[int, str] = {}
        self._sheets: dict[int, dict] = {}

    def _refresh(self) -> None:
        documents = tuple(self.masters.document(name) for name in self._table_names)
        if len(documents) == len(self._documents) and all(
            current is previous for current, previous in zip(documents, self._documents)
        ):
            return
        wallets, items, equipment, skills, limits, _, rarities, sheets = (
            document["table"] for document in documents
        )
        definitions = {
            f"wallet.{row['category']}.{row['name']}": _Definition(
                "wallet",
                (row["category"], row["name"]),
                row["val_cap"],
                row["revision"],
            )
            for row in wallets
        }
        definitions.update(
            (row["item_id"], _Definition("item", row["item_id"], row["val_cap"]))
            for row in [*items, *equipment]
        )
        definitions.update(
            (
                f"skill.attachable.{row['id']}",
                _Definition("item", f"skill.attachable.{row['id']}"),
            )
            for row in skills
        )
        definitions["wallet.point.stone"] = _Definition("stone", "stone")
        definitions["stamina.quest"] = _Definition("stamina", "quest")
        definitions["wallet.instant.stamina"] = _Definition("stamina", "quest")
        definitions["wallet.instant.pexp"] = _Definition("experience", "player_rank")
        definitions.update(
            (rid, _Definition("bonus_pass", rid)) for rid in self.passes.point_rids()
        )
        definitions.update(
            (rid, _Definition("pass_unlock", rid)) for rid in self.passes.unlock_rids()
        )
        unlockable = {row["id"]: row for row in sheets if row["category"] == UNLOCK}
        definitions.update(
            (f"{UNLOCK_PREFIX}{sheet_id}", _Definition("sheet_unlock", sheet_id))
            for sheet_id in unlockable
        )
        grouped_limits = defaultdict(list)
        for row in limits:
            grouped_limits[row["item_rid"]].append(row)
        self._definitions = definitions
        self._limits = grouped_limits
        self._rarities = {row["id"]: row["idName"] for row in rarities}
        self._sheets = unlockable
        self._documents = documents

    def _resolve(self, rid: str) -> _Definition:
        if rid in self._definitions:
            return self._definitions[rid]
        match = self._unit_id.fullmatch(rid)
        if match:
            card_id = int(match[1])
            try:
                self.masters.asset("unitcard", card_id)
            except FileNotFoundError as error:
                raise UnknownResource(f"Unknown unit resource: {rid}") from error
            return _Definition("unit", card_id)
        match = self._bom_id.fullmatch(rid)
        if match:
            card_id = int(match[1])
            try:
                self.masters.asset("bomcard", card_id)
            except FileNotFoundError as error:
                raise UnknownResource(f"Unknown bom resource: {rid}") from error
            return _Definition("bom", card_id)
        raise UnknownResource(f"Unknown resource: {rid}")

    @staticmethod
    def _quantity(value: int) -> int:
        if type(value) is not int or not 0 <= value <= 2147483647:
            raise ValueError("Resource quantity must be a nonnegative Int32")
        return value

    def _orders(self, orders) -> list[tuple[str, int, _Definition]]:
        result = []
        for order in orders:
            rid = order["rid"]
            if not isinstance(rid, str):
                raise UnknownResource("Resource ID must be a string")
            definition = self._resolve(rid)
            amount = self._quantity(order["val"])
            if amount:
                result.append((rid, amount, definition))
        return result

    @staticmethod
    def _balance(batch: _Batch, definition: _Definition) -> int:
        if definition.kind == "stone":
            return sum(
                row["val"]
                for row in batch.read("wallets")
                if row["category"] == "point"
                and row["name"] in ("free_coin", "paid_coin")
            )
        if definition.kind == "wallet":
            return next(
                (
                    row["val"]
                    for row in batch.read("wallets")
                    if (row["category"], row["name"]) == definition.key
                ),
                0,
            )
        if definition.kind == "item":
            return next(
                (
                    row["val"]
                    for row in batch.read("items")
                    if row["id"] == definition.key
                ),
                0,
            )
        if definition.kind == "unit":
            return sum(row["card_id"] == definition.key for row in batch.read("units"))
        if definition.kind == "experience":
            return next(
                row["exp"]
                for row in batch.read("modules")["experience"]
                if row["section"] == definition.key
            )
        if definition.kind in UNCOUNTED:
            raise ValueError(f"An entitlement is not a balance: {definition.kind}")
        return next(
            row["amount"]
            for row in batch.read("modules")["stamina"]
            if row["section"] == definition.key
        )

    def balance(self, state: dict, rid: str) -> int:
        self._refresh()
        return self._balance(_Batch(state), self._resolve(rid))

    def entitlement(self, rid: str) -> bool:
        self._refresh()
        return self._resolve(rid).kind in ENTITLEMENTS

    def redeemable(self, state: dict, rid: str, now: int) -> bool:
        self._refresh()
        definition = self._resolve(rid)
        if definition.kind == "bonus_pass":
            return self.passes.earning_by(rid, now) is not None
        if definition.kind == "pass_unlock":
            return self.passes.unlockable(state["bonus_passes"], rid, now)
        if definition.kind == "sheet_unlock":
            sheet = self._sheets[definition.key]
            running = self.masters.in_term(sheet["start_dt"], sheet["end_dt"], now)
            return running and not holds(state["login_bonus"], sheet["id"])
        return True

    def check(self, orders) -> None:
        self._refresh()
        self._orders(orders)

    def rids(self) -> list[str]:
        self._refresh()
        return sorted(self._definitions)

    def reward_order(self, row: dict) -> dict | None:
        self._refresh()
        category, name = row["m_walletCategory"], row["m_walletName"]
        if not category and not name:
            return None
        amount = self._quantity(row["m_count"])
        if not amount:
            return None
        if category == "item":
            rid = name
        elif category == "unit":
            rid = f"unit.card.{name}"
        elif category == "equip":
            rid = f"item.equip.{name}"
        elif category == "attachable":
            rid = f"skill.attachable.{name}"
        elif category in ("bonus_pass", "loginbonus"):
            rid = f"{category}.{name}"
        elif category == "bom":
            rid = f"bom.card.{name}"
        else:
            rid = f"wallet.{category}.{name}"
        self._resolve(rid)
        return {"rid": rid, "val": amount}

    def spend(self, state: dict, orders, now: int) -> ResourceChanges:
        self._refresh()
        totals: dict[str, int] = defaultdict(int)
        definitions = {}
        for rid, amount, definition in self._orders(orders):
            if definition.kind in ("unit", "bom"):
                raise NotImplementedError("Card resource spending is not implemented")
            totals[rid] += amount
            definitions[rid] = definition
        batch = _Batch(state)
        balances = {}
        stones = totals.pop("wallet.point.stone", 0)
        for rid, amount in totals.items():
            balance = self._balance(batch, definitions[rid])
            if balance < amount:
                raise InsufficientResource(rid, amount, balance)
            balances[rid] = balance
        if stones:
            coin_ids = ("wallet.point.free_coin", "wallet.point.paid_coin")
            remaining = {}
            for rid in coin_ids:
                definitions[rid] = self._resolve(rid)
                balances[rid] = self._balance(batch, definitions[rid])
                remaining[rid] = balances[rid] - totals.get(rid, 0)
            if sum(remaining.values()) < stones:
                raise InsufficientResource(
                    "wallet.point.stone", stones, sum(remaining.values())
                )
            for rid in coin_ids:
                taken = min(stones, remaining[rid])
                if taken:
                    totals[rid] += taken
                    stones -= taken
        for rid, amount in totals.items():
            self._set_balance(batch, definitions[rid], balances[rid] - amount, now)
        return batch.commit([])

    def charge(self, state: dict, orders, now: int, reason: str) -> ResourceChanges:
        # Every price the game asks comes through here, so one switch waives them all.
        if self.config.read()["resources"].get(FREE_COSTS):
            self._refresh()
            list(self._orders(orders))
            return _Batch(state).commit([])
        try:
            return self.spend(state, orders, now)
        except InsufficientResource as error:
            raise ApiRejected(reason) from error

    def restore(self, state: dict, rid: str, amount: int, now: int) -> None:
        self._refresh()
        batch, definition = _Batch(state), self._resolve(rid)
        # What the player already owned comes back whatever the capacity.
        balance = self._balance(batch, definition) + self._quantity(amount)
        self._set_balance(batch, definition, balance, now)
        batch.commit([])

    @staticmethod
    def _capacity(definition: _Definition, settings: dict, limit: dict | None) -> int:
        capacity = (
            definition.capacity
            if definition.capacity is not None
            else settings["attachable_capacity"]
        )
        return capacity if limit is None else min(capacity, limit["max_num"])

    def room(self, state: dict, rid: str, now: int) -> int:
        self._refresh()
        definition = self._resolve(rid)
        capacity = self._capacity(
            definition, self.config.read()["resources"], self._active_limit(rid, now)
        )
        return max(0, capacity - self._balance(_Batch(state), definition))

    def _set_balance(
        self, batch: _Batch, definition: _Definition, amount: int, now: int
    ) -> None:
        if definition.kind == "wallet":
            rows = batch.edit("wallets")
            row = next(
                (
                    row
                    for row in rows
                    if (row["category"], row["name"]) == definition.key
                ),
                None,
            )
            if row is None:
                category, name = definition.key
                row = {
                    "category": category,
                    "name": name,
                    "val": 0,
                    "revision": definition.revision,
                    "created_at": now,
                }
                rows.append(row)
            row["val"] = amount
            row["revision"] = definition.revision
            batch.updates["wallet"][definition.key] = row
        elif definition.kind == "item":
            rows = batch.edit("items")
            row = next((row for row in rows if row["id"] == definition.key), None)
            if row is None:
                row = {"id": definition.key, "val": 0}
                rows.append(row)
            row["val"] = amount
            batch.updates["item"][definition.key] = row
        else:
            row = next(
                row
                for row in batch.edit("modules")["stamina"]
                if row["section"] == definition.key
            )
            if row["amount"] >= row["capacity"] or amount >= row["capacity"]:
                row["latest_recovered_at"] = now
            row["amount"] = amount
            batch.module_updates["stamina"][definition.key] = row

    def _mail(self, batch: _Batch, reward: dict, now: int) -> None:
        source = batch.source
        tags = [] if source is None else self.mailbox.tags(source, reward["rid"])
        letter = self.mailbox.letter(batch.allocate("inbox"), reward, tags, now)
        batch.edit("inbox").append(letter)

    def _progress_pass(self, batch: _Batch, rid: str, amount: int, now: int) -> dict:
        delivery = {"rid": rid, "val": amount, "inc": 0, "inbox": 0, "over": amount}
        active = self.passes.earning_by(rid, now)
        if active is None:
            return delivery
        row = record(batch.edit("bonus_passes"), active["id"], now)
        gained = self.passes.advance(row, amount, now)
        batch.updates["bonuspass_progression"][row["pass_id"]] = row
        delivery.update(inc=gained, over=amount - gained)
        return delivery

    def _unlock_pass(self, batch: _Batch, rid: str, amount: int, now: int) -> dict:
        delivery = {"rid": rid, "val": amount, "inc": 0, "inbox": 0, "over": amount}
        active = self.passes.unlocked_by(rid, now)
        if active is None:
            return delivery
        row = record(batch.edit("bonus_passes"), active["id"], now)
        track = row["premium_track"]
        if not track["is_locked"]:
            return delivery
        track["is_locked"] = False
        batch.updates["bonuspass_progression"][row["pass_id"]] = row
        delivery.update(inc=1, over=amount - 1)
        return delivery

    def _duplicate_compensation(self, card_id: int, copies: int, rules: dict) -> dict:
        rarity = self._rarities[self.masters.asset("unitcard", card_id)["rarityId"]]
        if rarity not in rules["compensation"]:
            raise ValueError(f"No duplicate unit compensation for rarity {rarity}")
        amount = self._quantity(rules["compensation"][rarity] * copies)
        return {"rid": rules["compensation_rid"], "val": amount}

    def _unlock_sheet(self, batch: _Batch, rid: str, amount: int, definition):
        delivery = {"rid": rid, "val": amount, "inc": 0, "inbox": 0, "over": amount}
        if holds(batch.read("login_bonus"), definition.key):
            return delivery
        batch.edit("login_bonus").append(unlocked(definition.key))
        delivery.update(inc=1, over=amount - 1)
        return delivery

    def _grant_bom(self, batch, rid, amount, definition, now, policy) -> dict:
        delivery = {"rid": rid, "val": amount, "inc": 0, "inbox": 0, "over": 0}
        owned = any(row["card_id"] == definition.key for row in batch.read("boms"))
        if not owned:
            row = bom(batch.allocate("bom"), definition.key, now)
            batch.edit("boms").append(row)
            batch.updates["bom"][row["instance_id"]] = row
            delivery["inc"] = 1
        surplus = amount - delivery["inc"]
        if surplus:
            if policy == "reject":
                raise ResourceCapacityError(rid, amount, delivery["inc"])
            if policy == "mail":
                self._mail(batch, {"rid": rid, "val": surplus}, now)
                delivery["inbox"] = surplus
            else:
                delivery["over"] = surplus
        return delivery

    def _grant_unit(
        self, batch, rid, amount, definition, now, policy, settings, trail
    ) -> dict:
        delivery = {"rid": rid, "val": amount, "inc": 0, "inbox": 0, "over": 0}
        roster = batch.read("units")
        if all(row["card_id"] != definition.key for row in roster):
            if len(roster) >= settings["unit_capacity"]:
                if policy == "reject":
                    raise ResourceCapacityError(rid, amount, 0)
                if policy == "mail":
                    self._mail(batch, {"rid": rid, "val": amount}, now)
                    delivery["inbox"] = amount
                else:
                    delivery["over"] = amount
                return delivery
            batch.edit("units").append(
                unit(batch.allocate("unit"), definition.key, now)
            )
            delivery["inc"] = 1
        owned = next(
            row for row in batch.edit("units") if row["card_id"] == definition.key
        )
        rules = settings["duplicate_unit"]
        copies = amount - delivery["inc"]
        points = min(copies, max(0, rules["potential_cap"] - owned["upa"]["all_pt"]))
        if points:
            owned["upa"]["all_pt"] += points
            owned["updated_at"] = now
        if delivery["inc"] or points:
            batch.updates["unit"][owned["instance_id"]] = owned
        surplus = copies - points
        if surplus:
            order = self._duplicate_compensation(definition.key, surplus, rules)
            delivery["over"] = surplus
            if order["val"]:
                delivery["derived"] = [
                    self._grant(
                        batch,
                        order["rid"],
                        order["val"],
                        self._resolve(order["rid"]),
                        now,
                        policy,
                        settings,
                        (*trail, rid),
                    )
                ]
        return delivery

    def _grant_experience(
        self, batch: _Batch, rid: str, amount: int, definition, now: int, policy: str
    ) -> dict:
        modules = batch.edit("modules")
        experience = next(
            row for row in modules["experience"] if row["section"] == definition.key
        )
        previous = experience["exp"]
        updates = self.progression.add_player_exp({"modules": modules}, amount, now)
        for name, rows in updates.get("module_prop", {}).items():
            for row in rows:
                batch.module_updates[name][row["section"]] = row
        increment = experience["exp"] - previous
        excess = amount - increment
        if excess and policy == "reject":
            raise ResourceCapacityError(rid, amount, increment)
        return {"rid": rid, "val": amount, "inc": increment, "inbox": 0, "over": excess}

    def _active_limit(self, rid: str, now: int) -> dict | None:
        active = [
            row
            for row in self._limits.get(rid, ())
            if self.masters.in_term(row["start_at"], row["end_at"], now)
        ]
        if len(active) > 1:
            raise ValueError(f"Overlapping resource conversion rules: {rid}")
        return active[0] if active else None

    def grant(
        self,
        state: dict,
        orders,
        now: int,
        policy: str = "reject",
        source: str | None = None,
    ) -> ResourceChanges:
        if policy not in ("reject", "mail", "discard"):
            raise ValueError(f"Unknown resource delivery policy: {policy}")
        self._refresh()
        orders = self._orders(orders)
        settings = self.config.read()["resources"]
        rank = player_rank(state)
        batch = _Batch(state, source)
        deliveries = [
            self._grant(batch, rid, amount, definition, now, policy, settings, ())
            for rid, amount, definition in orders
        ]
        changes = batch.commit(deliveries)
        self.progression.promote(state, rank, now)
        return changes

    def _grant(
        self, batch, rid, amount, definition, now, policy, settings, trail
    ) -> dict:
        if rid in trail:
            raise ValueError(f"Resource conversion cycle: {rid}")
        if definition.kind == "bonus_pass":
            return self._progress_pass(batch, rid, amount, now)
        if definition.kind == "pass_unlock":
            return self._unlock_pass(batch, rid, amount, now)
        if definition.kind == "sheet_unlock":
            return self._unlock_sheet(batch, rid, amount, definition)
        if definition.kind == "experience":
            return self._grant_experience(batch, rid, amount, definition, now, policy)
        if definition.kind == "unit":
            return self._grant_unit(
                batch, rid, amount, definition, now, policy, settings, trail
            )
        if definition.kind == "bom":
            return self._grant_bom(batch, rid, amount, definition, now, policy)
        if definition.kind == "stone":
            raise NotImplementedError(
                "Stone rewards must specify free_coin or paid_coin"
            )
        balance = self._balance(batch, definition)
        limit = self._active_limit(rid, now)
        if definition.kind == "stamina":
            stamina = next(
                row
                for row in batch.read("modules")["stamina"]
                if row["section"] == definition.key
            )
            room = max(0, stamina["max_capacity"] - balance)
        else:
            room = max(0, self._capacity(definition, settings, limit) - balance)
        increment = min(amount, room)
        excess = amount - increment
        if excess and limit is None and policy == "reject":
            raise ResourceCapacityError(rid, amount, room)
        if increment:
            self._set_balance(batch, definition, balance + increment, now)
        delivery = {"rid": rid, "val": amount, "inc": increment, "inbox": 0, "over": 0}
        if excess:
            if limit is not None:
                converted_rid = limit["converted_item_rid"]
                converted_amount = self._quantity(excess * limit["converted_num"])
                converted = self._resolve(converted_rid)
                delivery["over"] = excess
                delivery["derived"] = [
                    self._grant(
                        batch,
                        converted_rid,
                        converted_amount,
                        converted,
                        now,
                        policy,
                        settings,
                        (*trail, rid),
                    )
                ]
            elif policy == "mail":
                self._mail(batch, {"rid": rid, "val": excess}, now)
                delivery["inbox"] = excess
            else:
                delivery["over"] = excess
        return delivery
