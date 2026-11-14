from collections import defaultdict
from copy import deepcopy

from .cards import find_unit
from .errors import ApiRejected
from .resources import UnknownResource
from .sync import Result, diff_state


SLOTS = ("A", "B", "C", "D")
EQUIP_PREFIX = "item.equip."
SKILL_PREFIX = "skill.attachable."
SKIN_PREFIX = "item.unit_skin."
ATTACH_SLOTS = 39
EXSKILL_SLOTS = range(1, 4)
MODES = {"primary": "primary_exskill", "rental": "rental_exskill"}
INVALID_GEAR_SLOT, DISPROP_GEAR = "INVALID_GEAR_SLOT", "DISPROP_GEAR"
NOT_ENOUGH_MATERIAL = "NOT_ENOUGH_MATERIAL"
INVALID_SKILL_SLOT, DISPROP_SKILL = "INVALID_SKILL_SLOT", "DISPROP_SKILL"
INVALID_SKIN = "HAS_INVALID_SKIN_NO"


class Armory:
    def __init__(self, masters, resources):
        self.masters = masters
        self.resources = resources

    def _card(self, unit: dict) -> dict:
        return self.masters.asset("unitcard", unit["card_id"])

    def _job(self, unit: dict) -> dict:
        identity = self._card(unit)["classId"] + unit["rank"]
        return self.masters.lookup("job_data", "id", identity)

    def pattern(self, unit: dict) -> list[int]:
        row = self.masters.lookup(
            "equipment_pattern", "id", self._job(unit)["equipmentPattern"]
        )
        return [int(rid.removeprefix(EQUIP_PREFIX)) for rid in row["pattern"]]

    def wear(self, state: dict, unit: dict, orders, now: int) -> None:
        if not isinstance(orders, list):
            raise ValueError("equip must be a list of gear slots")
        try:
            pattern = dict(zip(SLOTS, self.pattern(unit)))
        except KeyError as error:
            raise ApiRejected(DISPROP_GEAR) from error
        chosen: dict[str, int] = {}
        for order in orders:
            slot, identity = order["slot"], order["id"]
            if type(identity) is not int or identity < 1:
                raise ValueError("Gear is named by its equipment id")
            if slot not in SLOTS or slot in chosen:
                raise ApiRejected(INVALID_GEAR_SLOT)
            worn = unit["gear_set"][slot]
            if worn and worn != identity:
                raise ApiRejected(INVALID_GEAR_SLOT)
            if identity != pattern[slot]:
                raise ApiRejected(DISPROP_GEAR)
            chosen[slot] = identity
        # A slot already holding the piece is a repeated request, not a second cost.
        fresh = {
            slot: identity
            for slot, identity in chosen.items()
            if not unit["gear_set"][slot]
        }
        costs = [
            {"rid": f"{EQUIP_PREFIX}{identity}", "val": 1}
            for identity in fresh.values()
        ]
        self.resources.charge(state, costs, now, NOT_ENOUGH_MATERIAL)
        if fresh:
            unit["gear_set"].update(fresh)
            unit["updated_at"] = now

    def equip_gear(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        before = deepcopy(state)
        self.wear(state, unit, payload["equip"], now)
        return Result(sync=diff_state(before, state))

    def _skill_slot(self, unit: dict, number) -> str:
        opened = min(
            self.masters.property("Int", ATTACH_SLOTS),
            max(self._card(unit)["subSkillSlot"], unit["rank"]),
        )
        if type(number) is not int or not 1 <= number <= opened:
            raise ApiRejected(INVALID_SKILL_SLOT)
        return f"s{number}"

    def _skill(self, unit: dict, rid) -> dict:
        if not isinstance(rid, str) or not rid.startswith(SKILL_PREFIX):
            raise ApiRejected(DISPROP_SKILL)
        try:
            identity = int(rid.removeprefix(SKILL_PREFIX))
            skill = self.masters.lookup("attach_ability_data", "id", identity)
        except (KeyError, ValueError) as error:
            raise ApiRejected(DISPROP_SKILL) from error
        card = self._card(unit)
        allowed = {
            "restrictionForClass": (self._job(unit)["system_id"],),
            "restrictionForTribe": (card["mainTribe"], card["subTribe"]),
            "restrictionForElement": (card["element"],),
            "restrictionForFamily": (card["familyId"] or unit["card_id"],),
        }
        # Ultimate subskills are worn through the deck only.
        if skill["ultimate"] or any(
            skill[field] and skill[field] not in values
            for field, values in allowed.items()
        ):
            raise ApiRejected(DISPROP_SKILL)
        return skill

    def shelve(self, state: dict, skill_id: int, now: int) -> None:
        if skill_id:
            self.resources.restore(state, f"{SKILL_PREFIX}{skill_id}", 1, now)

    def attach_skill(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        slot = self._skill_slot(unit, payload["slot_no"])
        skill = self._skill(unit, payload["skill_id"])
        before = deepcopy(state)
        worn = unit["aa"][slot]
        if worn != skill["id"]:
            costs = [{"rid": payload["skill_id"], "val": 1}]
            self.resources.charge(state, costs, now, DISPROP_SKILL)
            # The client replaces a worn subskill without detaching it first.
            self.shelve(state, worn, now)
            unit["aa"][slot] = skill["id"]
            unit["updated_at"] = now
        return Result(sync=diff_state(before, state))

    def detach_skill(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        slot = self._skill_slot(unit, payload["slot_no"])
        before = deepcopy(state)
        if unit["aa"][slot]:
            self.shelve(state, unit["aa"][slot], now)
            unit["aa"][slot] = 0
            unit["updated_at"] = now
        return Result(sync=diff_state(before, state))

    def attach_charm(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        slot, worn = payload["slot"], unit["charm_set"]
        if type(slot) is not int or not 1 <= slot <= worn["unlocked"]:
            raise ApiRejected(INVALID_SKILL_SLOT)
        charm = next(
            (row for row in state["charms"] if row["id"] == payload["id"]), None
        )
        if charm is None:
            raise ApiRejected(DISPROP_SKILL)
        before = deepcopy(state)
        state["charms"].remove(charm)
        # A slot keeps a copy of the charm; the one it held before is gone.
        placed = {"s": slot, "tid": charm["tid"], "para": deepcopy(charm["para"])}
        kept = [row for row in worn["slots"] if row["s"] != slot]
        worn["slots"] = sorted([*kept, placed], key=lambda row: row["s"])
        unit["updated_at"] = now
        return Result(sync=diff_state(before, state))

    @staticmethod
    def skins(state: dict) -> list[dict]:
        held = defaultdict(list)
        for row in state["items"]:
            if row["id"].startswith(SKIN_PREFIX) and row["val"] > 0:
                card, skin = row["id"].removeprefix(SKIN_PREFIX).split("_")
                held[int(card)].append(int(skin))
        return [
            {"cid": card, "skins": sorted(skins)}
            for card, skins in sorted(held.items())
        ]

    def _holds_skin(self, state: dict, unit: dict, number: int) -> bool:
        try:
            rid = f"{SKIN_PREFIX}{unit['card_id']}_{number}"
            return self.resources.balance(state, rid) > 0
        except UnknownResource:
            return False

    def equip_skin(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        number = payload["skin_no"]
        if type(number) is not int or number < 0:
            raise ValueError("skin_no must be a skin number or zero")
        if number and not self._holds_skin(state, unit, number):
            raise ApiRejected(INVALID_SKIN)
        before = deepcopy(state)
        if unit["skin"] != number:
            unit["skin"] = number
            unit["updated_at"] = now
        return Result({"_z0": 0}, diff_state(before, state))

    def exskill(self, unit: dict, slot) -> int | None:
        if type(slot) is not int or slot not in EXSKILL_SLOTS:
            return None
        unlocked = slot == 1 or unit["flags"] & (1 << (slot - 2))
        return self._card(unit)[f"skillId{slot}"] if unlocked else None

    def equip_exskill(self, state: dict, payload: dict, now: int) -> Result:
        unit = find_unit(state, payload["instance_id"])
        field, slot = MODES[payload["mode"]], payload["slot_no"]
        if not self.exskill(unit, slot):
            raise ApiRejected(INVALID_SKILL_SLOT)
        before = deepcopy(state)
        if unit[field] != slot:
            unit[field] = slot
            unit["updated_at"] = now
        return Result(sync=diff_state(before, state))
