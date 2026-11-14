from copy import deepcopy
import json
import uuid

from .armory import SLOTS
from .cards import find_bom, find_unit
from .errors import ApiRejected
from .journal import BOM_ENHANCED, UNIT_ENHANCED
from .sync import Result, diff_state


GOLD = "wallet.point.gold"
MILK = "unit_exp"
# The client words and gates the awakening reset at this price.
RESET_GOLD = 500
AWAKENING_SLOTS = range(1, 6)
EXSKILL_LEVELS = ("exskill_lv", "exskill_lv_2", "exskill_lv_3")
LEVEL, EXSKILL, CLASS_CHANGE = "level", "exskill", "class_change"
STEPS = tuple(
    (f"{action}_{number}", action)
    for number in range(1, 6)
    for action in ("enhancement", "equip_gear", "upgrade")
)[:13]
INVALID_MATERIAL = "HAS_INVALID_MATERIAL"
UNUSABLE_MATERIAL = "HAS_CANT_CONSUMABLE_MATERIAL"
NOT_ENOUGH_MATERIAL = "NOT_ENOUGH_MATERIAL"
FEED_REASONS = (INVALID_MATERIAL, UNUSABLE_MATERIAL, NOT_ENOUGH_MATERIAL)
MAX_LEVEL = "ALREADY_MAX_LEVEL"
CONDITIONS_VIOLATION, NO_UPGRADE = "CONDITIONS_VIOLATION", "THERE_NO_UPGRADE"
NOT_ENOUGH_COST = "NOT_ENOUGH_COST"
NO_RESET_GOLD, EMPTY_SLOT = "NOT_ENOUGH_WALLET_FOR_RESET", "ALREADY_EMPTY_SLOT"
INVALID_SKILL, INVALID_SLOT = "HAS_INVALID_SKILL_ID", "HAS_INVALID_SLOT"
TAKEN_SLOT, NO_POINT = "NOT_ENOUGH_SKILL_SLOT", "NOT_ENOUGH_SKILL_POINT"


class Training:
    def __init__(self, masters, resources, progression, armory, journal, rng):
        self.masters = masters
        self.resources = resources
        self.progression = progression
        self.armory = armory
        self.journal = journal
        self.rng = rng

    def _card(self, unit: dict) -> dict:
        return self.masters.asset("unitcard", unit["card_id"])

    def _announce(self, state: dict, unit: dict, action: str, now: int) -> None:
        data = {"instance_id": unit["instance_id"], "action": action}
        self.journal.record(state, f"training-{uuid.uuid4()}", UNIT_ENHANCED, now, data)

    def _milks(self) -> dict[str, int]:
        return {
            f"wallet.{row['category']}.{row['name']}": row["exp_point"]
            for row in self.masters.table("wallet_template")
            if row["category"] == MILK
        }

    def _feed(self, state: dict, unit: dict, materials, now: int) -> None:
        if not isinstance(materials, list):
            raise ValueError("materials must be a list of resource orders")
        milks, experience, costs = self._milks(), 0, []
        for order in materials:
            rid, amount = order["rid"], order["val"]
            if rid not in milks or type(amount) is not int or amount < 1:
                raise ApiRejected(INVALID_MATERIAL)
            experience += milks[rid] * amount
            costs.append({"rid": rid, "val": amount})
        if not costs or unit["r_unit_lv"] >= self.progression.level_cap(unit):
            raise ApiRejected(UNUSABLE_MATERIAL)
        self.resources.charge(state, costs, now, NOT_ENOUGH_MATERIAL)
        self.progression.add_unit_exp(state, [unit["instance_id"]], experience, now)
        unit["updated_at"] = now
        self._announce(state, unit, LEVEL, now)

    def _promote(self, state: dict, unit: dict, now: int) -> None:
        card, times = self._card(unit), unit["rank"] + 1
        try:
            self.masters.lookup("job_data", "id", card["classId"] + times)
        except KeyError as error:
            raise ApiRejected(NO_UPGRADE) from error
        worn = [unit["gear_set"][slot] for slot in SLOTS]
        if unit["r_unit_lv"] < self.progression.level_cap(unit) or (
            worn != self.armory.pattern(unit)
        ):
            raise ApiRejected(CONDITIONS_VIOLATION)
        price = next(
            (
                row
                for row in self.masters.table("unit_upgrade_cost")
                if (row["rarity"], row["times"]) == (card["rarityId"], times)
            ),
            None,
        )
        if price is None:
            raise ApiRejected(NO_UPGRADE)
        costs = [{"rid": price["cost_resource"], "val": price["cost_num"]}]
        self.resources.charge(state, costs, now, NOT_ENOUGH_COST)
        # The worn set is the material of the class change.
        unit["gear_history"].append(dict(unit["gear_set"]))
        unit.update(gear_set=dict.fromkeys(SLOTS, 0), rank=times, updated_at=now)
        self._announce(state, unit, CLASS_CHANGE, now)

    def enhance(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        before = deepcopy(state)
        self._feed(state, unit, payload["materials"], now)
        return Result(
            {"unit": unit, "cost": {"rid": GOLD, "val": 0}}, diff_state(before, state)
        )

    def upgrade(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        before = deepcopy(state)
        self._promote(state, unit, now)
        return Result({"unit": unit}, diff_state(before, state))

    def enhance_all(self, state: dict, payload: dict, now: int) -> Result:
        steps = [(action, payload[name]) for name, action in STEPS if payload.get(name)]
        targets = {step["instance_id"] for _, step in steps}
        if len(targets) != 1:
            raise ValueError("Easy upgrades train exactly one unit")
        unit = find_unit(state, targets.pop())
        before = deepcopy(state)
        try:
            for action, step in steps:
                if action == "enhancement":
                    self._feed(state, unit, step["materials"], now)
                elif action == "equip_gear":
                    self.armory.wear(state, unit, step["equip"], now)
                else:
                    self._promote(state, unit, now)
        except ApiRejected as error:
            reason = error.reason if error.reason in FEED_REASONS else UNUSABLE_MATERIAL
            raise ApiRejected(reason, str(error)) from error
        return Result(sync=diff_state(before, state))

    def awaken(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        slots, awakening = payload["skills"], unit["upa"]
        if not isinstance(slots, list):
            raise ValueError("skills must be a list of awakening slots")
        before = deepcopy(state)
        if not slots:
            if not awakening["skills"]:
                raise ApiRejected(EMPTY_SLOT)
            costs = [{"rid": GOLD, "val": RESET_GOLD}]
            self.resources.charge(state, costs, now, NO_RESET_GOLD)
            awakening.update(skills=[], used_pt=0)
        else:
            if len(set(slots)) != len(slots) or any(
                type(slot) is not int or slot not in AWAKENING_SLOTS for slot in slots
            ):
                raise ApiRejected(INVALID_SLOT)
            card = self._card(unit)
            if not all(card[f"awakingAbilityId{slot}"] for slot in slots):
                raise ApiRejected(INVALID_SKILL)
            if set(slots) & set(awakening["skills"]):
                raise ApiRejected(TAKEN_SLOT)
            spent = awakening["used_pt"] + sum(
                card[f"awakingCost{slot}"] for slot in slots
            )
            if spent > awakening["all_pt"]:
                raise ApiRejected(NO_POINT)
            awakening.update(
                skills=sorted([*awakening["skills"], *slots]), used_pt=spent
            )
        unit["updated_at"] = now
        return Result({"_z0": 0}, diff_state(before, state))

    def _tiers(self, table: str, recipe_id: int) -> dict[int, list[dict]]:
        try:
            recipe = self.masters.lookup(table, "id", recipe_id)
        except KeyError:
            return {}
        return {
            tier["level"]: [
                {"rid": cell["recipe"], "val": cell["count"]} for cell in tier["recipe"]
            ]
            for tier in recipe["recipes"]
        }

    def _refine(self, state, row, field, table, recipe_id, now) -> None:
        costs = self._tiers(table, recipe_id).get(row[field])
        if costs is None:
            raise ApiRejected(MAX_LEVEL)
        self.resources.charge(state, costs, now, NOT_ENOUGH_MATERIAL)
        row[field] += 1
        row["updated_at"] = now

    def enhance_exskill(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        slot = payload["slot_no"]
        skill_id = self.armory.exskill(unit, slot)
        if not skill_id:
            raise ApiRejected(INVALID_MATERIAL)
        recipe = self.masters.lookup("skill_data", "id", skill_id)["recipeId"]
        before = deepcopy(state)
        self._refine(state, unit, EXSKILL_LEVELS[slot - 1], "recipe_skill", recipe, now)
        self._announce(state, unit, EXSKILL, now)
        return Result({"unit": unit}, diff_state(before, state))

    def _weapon(self, unit: dict, level: int) -> dict | None:
        try:
            identity = unit["card_id"] * 1000 + level
            return self.masters.lookup("unique_weapon_data", "uw_id", identity)
        except KeyError:
            return None

    def _whetstones(self, items) -> tuple[list[dict], int]:
        if not isinstance(items, list):
            raise ValueError("items must be a list of reinforcement materials")
        stones = {
            row["id"]: row
            for row in self.masters.table("unique_weapon_reinforce_item_data")
        }
        costs, rate = [], 0
        for item in items:
            stone, quantity = stones.get(item["id"]), item["quantity"]
            if stone is None or type(quantity) is not int or quantity < 1:
                raise ApiRejected(NOT_ENOUGH_MATERIAL)
            costs.append({"rid": stone["resource_id"], "val": quantity})
            rate += stone["rate"] * quantity
        if not 0 < rate <= 100:
            raise ApiRejected(NOT_ENOUGH_MATERIAL)
        return costs, rate

    def forge(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        target = self._weapon(unit, unit["uqw_lv"] + 1)
        if target is None:
            raise ApiRejected(MAX_LEVEL)
        before = deepcopy(state)
        if unit["uqw_lv"]:
            costs, rate = self._whetstones(payload["items"])
        else:
            costs = [
                {"rid": cell["recipe"], "val": cell["count"]}
                for cell in target["recipe"]
            ]
            costs.append({"rid": GOLD, "val": target["cost_gold"]})
            rate = 100
        self.resources.charge(state, costs, now, NOT_ENOUGH_MATERIAL)
        forged = self.rng.random() * 100 < rate
        if forged:
            unit.update(uqw_lv=target["level"], updated_at=now)
        return Result(
            {"unit": unit, "enhance_status": forged}, diff_state(before, state)
        )

    @staticmethod
    def protect(state: dict, payload: dict, now: int) -> Result:
        changes = (
            (payload["protect_instance_ids"], True),
            (payload["unprotect_instance_ids"], False),
        )
        if not all(isinstance(identities, list) for identities, _ in changes):
            raise ValueError("Protection is requested by lists of unit instances")
        units = [
            (find_unit(state, identity), locked)
            for identities, locked in changes
            for identity in identities
        ]
        before = deepcopy(state)
        for unit, locked in units:
            if unit["is_protected"] != locked:
                unit.update(is_protected=locked, updated_at=now)
        return Result(sync=diff_state(before, state))

    def discard(self, state: dict, payload: dict, now: int) -> Result:
        identities = payload["instance_ids"]
        if (
            not isinstance(identities, list)
            or not identities
            or len(set(identities)) != len(identities)
        ):
            raise ValueError("instance_ids must name distinct units")
        units = [find_unit(state, identity) for identity in identities]
        held = set(state["lending"].values()) | {
            slot["iid"]
            for row in state["compositions"]
            for raw in row["slots"]
            if (slot := json.loads(raw))["t"] == 1
        }
        if any(unit["is_protected"] or unit["instance_id"] in held for unit in units):
            raise ValueError("Protected, fielded and lent units stay with the player")
        before = deepcopy(state)
        gold = 0
        for unit in units:
            rarity = self._card(unit)["rarityId"]
            gold += self.masters.lookup("unit_rarity", "id", rarity)["sale"]
            for skill_id in unit["aa"].values():
                self.armory.shelve(state, skill_id, now)
        state["units"] = [row for row in state["units"] if row not in units]
        deliveries = self.resources.grant(
            state, [{"rid": GOLD, "val": gold}], now, policy="mail"
        ).deliveries
        returns = {
            name: [
                {"rid": row["rid"], "val": row[key]} for row in deliveries if row[key]
            ]
            for name, key in (("received", "inc"), ("to_inbox", "inbox"))
        }
        return Result({"returns": returns}, diff_state(before, state))

    def _enhance_bom(self, state, payload, field, table, recipe_of, now) -> dict:
        bom = find_bom(state, payload["instance_id"])
        card = self.masters.asset("bomcard", bom["card_id"])
        self._refine(state, bom, field, table, recipe_of(card), now)
        data = {"instance_id": bom["instance_id"], "card_id": bom["card_id"]}
        self.journal.record(state, f"training-{uuid.uuid4()}", BOM_ENHANCED, now, data)
        return bom

    def enhance_bom(self, state: dict, payload: dict, now: int) -> Result:
        before = deepcopy(state)
        bom = self._enhance_bom(
            state,
            payload,
            "exskill_lv",
            "recipe_skill",
            lambda card: self.masters.lookup("skill_data", "id", card["skillId1"])[
                "recipeId"
            ],
            now,
        )
        return Result(
            {"boms": bom, "cost": {"rid": GOLD, "val": 0}}, diff_state(before, state)
        )

    def enhance_bom_passive(self, state: dict, payload: dict, now: int) -> Result:
        before = deepcopy(state)
        bom = self._enhance_bom(
            state,
            payload,
            "passive_lv",
            "recipe_ability",
            lambda card: self.masters.lookup("ability_data", "id", card["ability"][0])[
                "recipeId"
            ],
            now,
        )
        return Result({"bom": bom}, diff_state(before, state))
