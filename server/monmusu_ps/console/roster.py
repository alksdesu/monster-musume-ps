from dataclasses import dataclass
from itertools import count

from ..core.initial import unit as new_unit
from .editing import Editing


SOURCE = "console.roster"
PLAYABLE = 1
EXSKILL_LEVELS = ("exskill_lv", "exskill_lv_2", "exskill_lv_3")
SKILL_IDS = ("skillId1", "skillId2", "skillId3")
AWAKENING_SLOTS = (1, 2, 3, 4, 5)
SKIN_PREFIX = "item.unit_skin."
ASPECTS = ("level", "rank", "exskill", "weapon", "potential")


def find_unit(state: dict, instance_id) -> dict:
    unit = next(
        (row for row in state["units"] if row["instance_id"] == instance_id), None
    )
    if unit is None:
        raise KeyError(f"No unit with instance id {instance_id}")
    return unit


# Everything a listing reads from the master data, read once for all the units in it.
@dataclass(frozen=True)
class _Books:
    rarities: dict
    ranks: dict
    jobs: frozenset
    skills: dict
    recipes: dict
    abilities: dict
    weapons: dict
    skins: dict
    say: object


class Roster:
    def __init__(self, runtime):
        self.runtime = runtime
        self.editing = Editing(runtime)
        self._indexes: dict[str, tuple[dict, dict]] = {}

    def _spoken(self, text: str) -> str:
        return self.runtime.localization.text(text) or text

    def _card(self, card_id: int) -> dict:
        return self.runtime.masters.asset("unitcard", card_id)

    # Class changes climb one rank at a time, so the top is the last rank the job table runs to without a gap.
    def rank_cap(self, card: dict) -> int:
        masters, top = self.runtime.masters, 0
        for rank in count(1):
            try:
                masters.lookup("job_data", "id", card["classId"] + rank)
            except KeyError:
                return top
            top = rank

    def exskill_cap(self, card: dict, slot: int) -> int:
        masters, skill_id = self.runtime.masters, card[SKILL_IDS[slot - 1]]
        if not skill_id:
            return 0
        try:
            recipe = masters.lookup("skill_data", "id", skill_id)["recipeId"]
            tiers = masters.lookup("recipe_skill", "id", recipe)["recipes"]
        except KeyError:
            return 1
        return max((tier["level"] for tier in tiers), default=0) + 1

    # Both tables are scanned once per edition of the master data, not once per unit.
    def _indexed(self, name: str, build):
        document = self.runtime.masters.document(name)
        kept = self._indexes.get(name)
        if kept is None or kept[0] is not document:
            self._indexes[name] = kept = document, build(document["table"])
        return kept[1]

    def _weapon_caps(self) -> dict[int, int]:
        def build(rows):
            caps: dict[int, int] = {}
            for row in rows:
                caps[row["card_id"]] = max(caps.get(row["card_id"], 0), row["level"])
            return caps

        return self._indexed("unique_weapon_data", build)

    def _abilities(self) -> dict[int, str]:
        return self._indexed(
            "ability_data", lambda rows: {row["id"]: row["name"] for row in rows}
        )

    def weapon_cap(self, card: dict) -> int:
        return self._weapon_caps().get(card["id"], 0)

    def _skins_by_card(self) -> dict[int, list[dict]]:
        def build(rows):
            held: dict[int, list[dict]] = {}
            for row in rows:
                held.setdefault(row["unit_card_id"], []).append(row)
            return held

        return self._indexed("unit_skin_data", build)

    def _books(self) -> _Books:
        masters = self.runtime.masters
        return _Books(
            rarities={row["id"]: row for row in masters.table("unit_rarity")},
            ranks={row["rank"]: row for row in masters.table("unit_rank_info_table")},
            jobs=frozenset(row["id"] for row in masters.table("job_data")),
            skills={row["id"]: row for row in masters.table("skill_data")},
            recipes={row["id"]: row for row in masters.table("recipe_skill")},
            abilities=self._abilities(),
            weapons=self._weapon_caps(),
            skins=self._skins_by_card(),
            say=self.runtime.localization.speaker(),
        )

    def potential_cap(self) -> int:
        return self.runtime.config.read()["resources"]["duplicate_unit"][
            "potential_cap"
        ]

    def skins(self, card: dict) -> list[dict]:
        return [
            {
                "id": row["skin_id"],
                "name": self._spoken(row["name"]),
                "adult": bool(row["is_x"]),
            }
            for row in self.runtime.masters.table("unit_skin_data")
            if row["unit_card_id"] == card["id"]
        ]

    def _slot_open(self, unit: dict, slot: int) -> bool:
        return slot == 1 or bool(unit["flags"] & (1 << (slot - 2)))

    def describe(self, unit: dict, books: _Books | None = None) -> dict:
        books = books or self._books()
        card = self._card(unit["card_id"])
        rarity = books.rarities[card["rarityId"]]
        rank_cap = 0
        for rank in count(1):
            if card["classId"] + rank not in books.jobs:
                break
            rank_cap = rank

        def skill_cap(skill_id: int) -> int:
            skill = books.skills.get(skill_id)
            recipe = books.recipes.get(skill["recipeId"]) if skill else None
            if recipe is None:
                return 1
            return max((tier["level"] for tier in recipe["recipes"]), default=0) + 1

        return {
            "instance_id": unit["instance_id"],
            "card_id": card["id"],
            "name": books.say(card["charaName"]),
            "nickname": books.say(card["nickname"]),
            "rarity": rarity["idName"],
            "level": unit["r_unit_lv"],
            "level_cap": rarity["maxLevel"] + books.ranks[unit["rank"]]["addMaxLevel"],
            "rank": unit["rank"],
            "rank_cap": rank_cap,
            "exskills": [
                {
                    "slot": slot,
                    "name": books.say(books.skills[card[SKILL_IDS[slot - 1]]]["name"])
                    if card[SKILL_IDS[slot - 1]] in books.skills
                    else "",
                    "level": unit[EXSKILL_LEVELS[slot - 1]],
                    "cap": skill_cap(card[SKILL_IDS[slot - 1]]),
                    "open": self._slot_open(unit, slot),
                }
                for slot in (1, 2, 3)
                if card[SKILL_IDS[slot - 1]]
            ],
            "weapon": {
                "level": unit["uqw_lv"],
                "cap": books.weapons.get(card["id"], 0),
            },
            "potential": {
                "points": unit["upa"]["all_pt"],
                "cap": self.potential_cap(),
                "used": unit["upa"]["used_pt"],
                "skills": unit["upa"]["skills"],
                "choices": [
                    {
                        "slot": slot,
                        "name": books.say(
                            books.abilities.get(card[f"awakingAbilityId{slot}"], "")
                        ),
                        "cost": card[f"awakingCost{slot}"],
                    }
                    for slot in AWAKENING_SLOTS
                    if card[f"awakingAbilityId{slot}"]
                ],
            },
            "skin": unit["skin"],
            "skins": [
                {
                    "id": row["skin_id"],
                    "name": books.say(row["name"]),
                    "adult": bool(row["is_x"]),
                }
                for row in books.skins.get(card["id"], [])
            ],
            "protected": unit["is_protected"],
        }

    @staticmethod
    def topped(described: dict) -> bool:
        return (
            described["level"] == described["level_cap"]
            and described["rank"] == described["rank_cap"]
            and all(
                skill["open"] and skill["level"] == skill["cap"]
                for skill in described["exskills"]
            )
            and described["weapon"]["level"] == described["weapon"]["cap"]
            and described["potential"]["points"] == described["potential"]["cap"]
        )

    def books(self) -> _Books:
        return self._books()

    async def units(self, profile: str) -> list[dict]:
        state = await self.runtime.store.read(self.editing.known(profile))
        books = self._books()
        return [self.describe(unit, books) for unit in state["units"]]

    async def cards(self, profile: str) -> list[dict]:
        state = await self.runtime.store.read(self.editing.known(profile))
        owned = {unit["card_id"]: unit["instance_id"] for unit in state["units"]}
        books = self._books()
        return [
            {
                "card_id": card["id"],
                "name": books.say(card["charaName"]),
                "nickname": books.say(card["nickname"]),
                "rarity": books.rarities[card["rarityId"]]["idName"],
                "instance_id": owned.get(card["id"]),
            }
            for card in self.runtime.masters.assets("unitcard")
            if card["type"] == PLAYABLE
        ]

    def _playable(self) -> list[dict]:
        return [
            card
            for card in self.runtime.masters.assets("unitcard")
            if card["type"] == PLAYABLE
        ]

    def give(self, state: dict, card_ids, now: int) -> int:
        owned = {unit["card_id"] for unit in state["units"]}
        wanted = [
            card_id for card_id in dict.fromkeys(card_ids) if card_id not in owned
        ]
        for card_id in wanted:
            self._card(card_id)
        sequences = state["resource_sequences"]
        for card_id in wanted:
            state["units"].append(new_unit(sequences["unit"], card_id, now))
            sequences["unit"] += 1
        return len(wanted)

    async def add(self, profile: str, card_ids: list) -> dict:
        if not isinstance(card_ids, list) or not all(type(n) is int for n in card_ids):
            raise ValueError("card_ids has to be a list of card numbers")
        async with self.editing.edit(profile) as (state, outcome):
            outcome["added"] = self.give(state, card_ids, int(self.runtime.clock()))
        return outcome

    def _set_level(self, unit: dict, level) -> None:
        cap = self.runtime.progression.level_cap(unit)
        if type(level) is not int or not 1 <= level <= cap:
            raise ValueError(f"Level has to be a whole number from 1 to {cap}")
        unit["t_exp"] = self.runtime.progression.level_exp(unit, level)
        unit["r_unit_lv"] = level

    def _set_rank(self, unit: dict, card: dict, rank) -> None:
        cap = self.rank_cap(card)
        if type(rank) is not int or not 0 <= rank <= cap:
            raise ValueError(f"Rank has to be a whole number from 0 to {cap}")
        unit["rank"] = rank
        # A lower rank may no longer hold the level the unit had.
        self._set_level(
            unit, min(unit["r_unit_lv"], self.runtime.progression.level_cap(unit))
        )

    def _set_exskill(self, unit: dict, card: dict, slot, level) -> None:
        if slot not in (1, 2, 3) or not card[SKILL_IDS[slot - 1]]:
            raise ValueError("The unit has no EX skill in that slot")
        cap = self.exskill_cap(card, slot)
        if type(level) is not int or not 1 <= level <= cap:
            raise ValueError(f"EX skill {slot} goes from 1 to {cap}")
        unit[EXSKILL_LEVELS[slot - 1]] = level
        if slot > 1:
            unit["flags"] |= 1 << (slot - 2)

    def _set_weapon(self, unit: dict, card: dict, level) -> None:
        cap = self.weapon_cap(card)
        if type(level) is not int or not 0 <= level <= cap:
            raise ValueError(f"The unique weapon goes from 0 to {cap}")
        unit["uqw_lv"] = level

    def _set_potential(self, unit: dict, card: dict, points, skills) -> None:
        cap = self.potential_cap()
        awakening = unit["upa"]
        if points is not None:
            if type(points) is not int or not 0 <= points <= cap:
                raise ValueError(f"Potential points go from 0 to {cap}")
            awakening["all_pt"] = points
        if skills is not None:
            if not isinstance(skills, list) or len(set(skills)) != len(skills):
                raise ValueError("Potential skills have to be a list of distinct slots")
            if any(
                slot not in AWAKENING_SLOTS or not card[f"awakingAbilityId{slot}"]
                for slot in skills
            ):
                raise ValueError("The unit has no potential skill in that slot")
            spent = sum(card[f"awakingCost{slot}"] for slot in skills)
            if spent > awakening["all_pt"]:
                raise ValueError(
                    "Those potential skills cost more points than the unit has"
                )
            awakening.update(skills=sorted(skills), used_pt=spent)
        elif awakening["used_pt"] > awakening["all_pt"]:
            awakening.update(skills=[], used_pt=0)

    def _set_skin(self, state: dict, unit: dict, card: dict, skin, now: int) -> None:
        if type(skin) is not int or skin not in {row["id"] for row in self.skins(card)}:
            raise ValueError("The unit has no such skin")
        if skin:
            rid = f"{SKIN_PREFIX}{card['id']}_{skin}"
            resources = self.runtime.resources
            if resources.balance(state, rid) <= 0:
                resources.grant(state, [{"rid": rid, "val": 1}], now, policy="discard")
        unit["skin"] = skin

    def apply(self, state: dict, unit: dict, fields: dict, now: int) -> None:
        card = self._card(unit["card_id"])
        unknown = set(fields) - {
            "level",
            "rank",
            "exskills",
            "weapon",
            "potential_points",
            "potential_skills",
            "skin",
            "protected",
        }
        if unknown:
            raise ValueError(f"Unknown unit fields: {', '.join(sorted(unknown))}")
        if "rank" in fields:
            self._set_rank(unit, card, fields["rank"])
        if "level" in fields:
            self._set_level(unit, fields["level"])
        for slot, level in (fields.get("exskills") or {}).items():
            self._set_exskill(unit, card, int(slot), level)
        if "weapon" in fields:
            self._set_weapon(unit, card, fields["weapon"])
        if "potential_points" in fields or "potential_skills" in fields:
            self._set_potential(
                unit,
                card,
                fields.get("potential_points"),
                fields.get("potential_skills"),
            )
        if "skin" in fields:
            self._set_skin(state, unit, card, fields["skin"], now)
        if "protected" in fields:
            if type(fields["protected"]) is not bool:
                raise ValueError("protected is yes or no")
            unit["is_protected"] = fields["protected"]
        unit["updated_at"] = now

    async def edit(self, profile: str, instance_id, fields: dict) -> dict:
        if not isinstance(fields, dict):
            raise ValueError("fields has to be an object")
        async with self.editing.edit(profile) as (state, outcome):
            unit = find_unit(state, instance_id)
            self.apply(state, unit, fields, int(self.runtime.clock()))
            outcome["unit"] = self.describe(unit)
        return outcome

    def fill(self, state: dict, unit: dict, aspects, now: int) -> list[str]:
        card, filled = self._card(unit["card_id"]), []
        if "rank" in aspects and unit["rank"] < self.rank_cap(card):
            self._set_rank(unit, card, self.rank_cap(card))
            filled.append("rank")
        cap = self.runtime.progression.level_cap(unit)
        if "level" in aspects and unit["r_unit_lv"] < cap:
            self._set_level(unit, cap)
            filled.append("level")
        if "exskill" in aspects:
            for slot in (1, 2, 3):
                top = self.exskill_cap(card, slot)
                if top and (
                    unit[EXSKILL_LEVELS[slot - 1]] < top
                    or not self._slot_open(unit, slot)
                ):
                    self._set_exskill(unit, card, slot, top)
                    filled.append(f"exskill{slot}")
        if "weapon" in aspects and unit["uqw_lv"] < self.weapon_cap(card):
            self._set_weapon(unit, card, self.weapon_cap(card))
            filled.append("weapon")
        if "potential" in aspects and unit["upa"]["all_pt"] < self.potential_cap():
            self._set_potential(unit, card, self.potential_cap(), None)
            filled.append("potential")
        if filled:
            unit["updated_at"] = now
        return filled

    async def fill_all(self, profile: str, aspects: list, instance_ids=None) -> dict:
        if not isinstance(aspects, list) or not set(aspects) <= set(ASPECTS):
            raise ValueError(f"aspects has to name some of {', '.join(ASPECTS)}")
        async with self.editing.edit(profile) as (state, outcome):
            now = int(self.runtime.clock())
            chosen = (
                state["units"]
                if instance_ids is None
                else [find_unit(state, i) for i in instance_ids]
            )
            touched = {}
            for unit in chosen:
                filled = self.fill(state, unit, aspects, now)
                if filled:
                    touched[unit["instance_id"]] = filled
            outcome["filled"] = touched
        return outcome
