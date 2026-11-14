from collections import Counter
from copy import deepcopy

from .armory import SKILL_PREFIX
from .cards import find_unit, rarity_id
from .errors import ApiNotFound, ApiRejected
from .sync import Result, diff_state


TABLET_PREFIX = "item.sskill_rcp."
SLOTS = ("s1", "s2", "s3", "s4")
OVERFLOW = "ASSET_CAPACITY_OVERFLOW"
NOT_ENOUGH_MATERIAL = "NOT_ENOUGH_MATERIAL"
INVALID_UNIT = "INVALID_UNIT_CONDITION"


class Synthesis:
    def __init__(self, masters, resources):
        self.masters = masters
        self.resources = resources

    def _wildcard(self, recipe: dict, material: dict, skill_id) -> int:
        try:
            skill = self.masters.lookup("attach_ability_data", "id", skill_id)
        except KeyError as error:
            raise ApiRejected(NOT_ENOUGH_MATERIAL) from error
        if skill["rarity"] != rarity_id(material["rarity"]) or (
            skill["purchase"] and not recipe["include_purchase"]
        ):
            raise ApiRejected(NOT_ENOUGH_MATERIAL)
        return skill["id"]

    @staticmethod
    def _strip(state: dict, taken: set, instance_id, skill_id: int) -> tuple:
        try:
            unit = find_unit(state, instance_id)
        except ApiNotFound as error:
            raise ApiRejected(INVALID_UNIT) from error
        slot = next(
            (
                slot
                for slot, worn in unit["aa"].items()
                if worn == skill_id and (instance_id, slot) not in taken
            ),
            None,
        )
        if slot is None:
            raise ApiRejected(INVALID_UNIT)
        taken.add((instance_id, slot))
        return unit, slot

    def synthesize(self, state: dict, payload: dict, now: int) -> Result:
        recipe_id = payload["recipe_id"]
        try:
            recipe = self.masters.lookup("subskill_recipe_data", "recipe_id", recipe_id)
        except KeyError as error:
            raise ApiNotFound(f"subskill_recipe.{recipe_id}") from error
        # The tablet is the standing licence for its recipe and is never spent.
        if self.resources.balance(state, f"{TABLET_PREFIX}{recipe_id}") < 1:
            raise ApiRejected(NOT_ENOUGH_MATERIAL)
        product = f"{SKILL_PREFIX}{recipe['combinedsubskill_id']}"
        if self.resources.room(state, product, now) < 1:
            raise ApiRejected(OVERFLOW)
        donors, chosen, borrowed = (
            payload["alt"],
            payload["wc_skill"],
            payload["wc_alt"],
        )
        stock: Counter = Counter()
        taken: set = set()
        stripped = []
        for slot, material in zip(SLOTS, recipe["materials"]):
            if material["id"]:
                if donors[slot]:
                    stripped.append(
                        self._strip(state, taken, donors[slot], material["id"])
                    )
                else:
                    stock[material["id"]] += 1
            elif borrowed[slot] is not None:
                donor, skill_id = (
                    borrowed[slot]["instance_id"],
                    borrowed[slot]["skill_id"],
                )
                skill_id = self._wildcard(recipe, material, skill_id)
                stripped.append(self._strip(state, taken, donor, skill_id))
            else:
                stock[self._wildcard(recipe, material, chosen[slot])] += 1
        before = deepcopy(state)
        costs = [
            {"rid": f"{SKILL_PREFIX}{skill_id}", "val": amount}
            for skill_id, amount in stock.items()
        ]
        self.resources.charge(state, costs, now, NOT_ENOUGH_MATERIAL)
        for unit, slot in stripped:
            unit["aa"][slot] = 0
            unit["updated_at"] = now
        generated = {"rid": product, "val": 1}
        self.resources.grant(state, [generated], now)
        return Result(
            {"recipe_id": recipe_id, "generated_resource": generated},
            diff_state(before, state),
        )
