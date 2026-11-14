from collections.abc import Callable
import re

from monmusu_l10n.markup import JAPANESE

from ..core.localization import unchanged
from ..core.loginbonus import UNLOCK, UNLOCK_PREFIX
from ..core.resources import Resources
from .catalog import RESOURCE, row_title


SUB_SKILL_PREFIX = "skill.attachable."
CARD = re.compile(r"(unit|bom)\.card\.([1-9][0-9]*)")
CARD_FOLDERS = {"unit": "unitcard", "bom": "bomcard"}
TABLES = (
    "wallet_template",
    "item_template",
    "equipment",
    "attach_ability_data",
    "bonus_pass_data",
    "login_bonus",
)
# The stamina gauge and a price paid in platform points have no row that names them.
UNLISTED = {"stamina.quest": ("スタミナ",), "wallet.point.payment": ("DMMポイント",)}
# A field that says which resource, and the field beside it that says what kind of thing it is.
KINDS = {
    "m_walletName": "m_walletCategory",
    "prize_name": "prize_type",
    "price_name": "price_category",
    "cost_name": "cost_type",
    "consumption_name": "consumption_category",
    "dupli_name": "dupli_category",
    "item_name": "item_category",
    "iconWalletName": "iconWalletCategory",
    "main_draw_wallet_name": "main_draw_wallet_category",
    "sub_draw_wallet_name": "sub_draw_wallet_category",
    "requireItemName": "requireItemCategory",
    "box_co_prize_name": "box_co_prize_type",
}
UNIT, SUB_SKILL, QUEST, GOODS, SHOP_TAB = "unit", "sub_skill", "quest", "goods", "tab"
EVENT, PASS, MISSION, BINGO = "event", "pass", "mission", "bingo"
QUEST_CHAPTER, STORY_CHAPTER, SECRET_CATEGORY = "quest_chapter", "chapter", "secret"
# What a bare id can point at: a resource once its prefix is put back, or a row another table names.
PREFIXES = {UNIT: "unit.card.", SUB_SKILL: SUB_SKILL_PREFIX}
LISTED = {
    QUEST: ("quest_data", "name"),
    GOODS: ("shop_data", "name"),
    SHOP_TAB: ("shop_tab", "tab_name"),
    EVENT: ("event_list_data", "name"),
    PASS: ("bonus_pass_data", "display_name"),
    MISSION: ("mission_data", "text"),
    BINGO: ("bingo_data", "name"),
    QUEST_CHAPTER: ("quest_chapter_data", "title"),
    STORY_CHAPTER: ("chapter_data", "title"),
    SECRET_CATEGORY: ("quest_secret_category_table", "full_title"),
}
# Fields that hold such an id, by table and by the way down to them with list positions left out.
# One name means different things from table to table, so none is taken on its name alone.
REFERENCES = {
    "unit_skin_data": {"unit_card_id": UNIT},
    "story_data_main": {"story_id": QUEST},
    "story_data_other": {"unit_card_id": UNIT},
    "story_data_unit": {"story_id": UNIT},
    "story_data_group": {
        "units.id": UNIT,
        "need_quest_id": QUEST,
        "chapter_id": STORY_CHAPTER,
    },
    "event_character_growth": {"card_id": UNIT, "event_id": EVENT},
    "event_sealed_chara_props": {"reward_card_id": UNIT, "event_id": EVENT},
    "login_bonus_cell": {"dayData.chara_id": UNIT},
    "bonus_pass_data": {
        "chara_id": UNIT,
        "ref_shop_id_dmm": GOODS,
        "ref_shop_id_play_store": GOODS,
        "ref_shop_id_app_store": GOODS,
    },
    "season_pass_data": {"chara_id": UNIT, "pass_id": PASS},
    "bonus_pass_tier_data": {"pass_id": PASS},
    "bonus_pass_track_reward_data": {"pass_id": PASS},
    "subskill_recipe_data": {
        "combinedsubskill_id": SUB_SKILL,
        "materials.id": SUB_SKILL,
    },
    "quest_data": {"needQuestIndex": QUEST, "logShareMissionId": MISSION},
    "quest_secret_group_table": {"quest_id": QUEST, "category_id": SECRET_CATEGORY},
    "everyones_killevent_quest": {"questId": QUEST},
    "quest_synopsis_message_data": {
        "chapter_id": QUEST_CHAPTER,
        "target_quest_id": QUEST,
    },
    "sugoroku_quest_data": {"id": QUEST},
    "ranch_reward_table": {"progress_quest_id": QUEST},
    "sub_story_tab_data": {"unlock_quest_id": QUEST},
    "sub_story_data": {"event_id": EVENT, "shop_id": SHOP_TAB},
    "event_list_data": {"shop_id": SHOP_TAB},
    "gacha_detail_data": {"shop_tab_id": SHOP_TAB},
    "shop_data": {"hotfix_shop_id": GOODS},
    "shop_tab": {"shop_item_id": GOODS},
    "shop_collabo_system": {"shop_id": GOODS},
    "unit_awake_item": {"shop_id": GOODS},
    "payment_data": {"productId": GOODS},
    "mission_category_collective_data": {"event_id": EVENT},
    "mission_data": {"disclosureMission": MISSION},
    "mission_panel_frame": {"comp_mission_id": MISSION},
    "temple_tower_control": {"event_id": EVENT},
    "bingo_sheet": {"bingo_id": BINGO},
}
# Tables whose rows have no name of their own, and the field that says what each row is about.
SUBJECTS = {
    "bingo_sheet": "bingo_id",
    "bonus_pass_tier_data": "pass_id",
    "bonus_pass_track_reward_data": "pass_id",
    "event_character_growth": "card_id",
    "event_sealed_chara_props": "reward_card_id",
    "everyones_killevent_quest": "questId",
    "item_limit_table": "item_rid",
    "mission_panel_frame": "comp_mission_id",
    "payment_data": "productId",
    "quest_secret_group_table": "quest_id",
    "quest_synopsis_message_data": "chapter_id",
    "ranch_reward_table": "progress_quest_id",
    "shop_collabo_system": "shop_id",
    "sub_story_data": "event_id",
    "subskill_recipe_data": "combinedsubskill_id",
    "sugoroku_quest_data": "id",
    "temple_tower_control": "event_id",
    "unit_awake_item": "shop_id",
}
JOINT = " "


def first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


# The names as they read in the language chosen when it was opened.
class Reading:
    def __init__(self, names: "Names", say: Callable[[str], str]):
        self.say = say
        self._names = names
        self._listed = names.listed()
        self._resources: dict[str, str] = {}
        self._texts: dict[str, str] = {}
        self._referred: dict[tuple, str] = {}

    def resource(self, rid: str) -> str:
        name = self._resources.get(rid)
        if name is None:
            parts = self._listed.get(rid) or self._names.card(rid)
            name = JOINT.join(self.say(part) for part in parts if part)
            self._resources[rid] = name
        return name

    def pair(self, kind, name) -> str:
        if type(kind) is not str or type(name) is not str or not (kind and name):
            return ""
        return self.resource(Resources.rid_of(kind, name))

    # A table repeats the same few texts on thousands of rows, so each is worked out once.
    def _text(self, text: str) -> str:
        shown = self._texts.get(text)
        if shown is None:
            if RESOURCE.match(text):
                shown = self.resource(text)
            elif JAPANESE.search(text):
                said = self.say(text)
                shown = "" if said == text else said
            else:
                shown = ""
            self._texts[text] = shown
        return shown

    # What the game shows for the text kept at a step of a row or of a list inside it.
    def shown(self, holder, step) -> str:
        value = holder[step]
        if type(value) is not str:
            return ""
        if isinstance(holder, dict):
            kind = KINDS.get(step)
            if kind in holder:
                return self.pair(holder[kind], value) or self._text(value)
        return self._text(value)

    # What the id kept in a field of a table points at; way is the path to it without list positions.
    def referred(self, table: str, way: str, value) -> str:
        kind = REFERENCES.get(table, {}).get(way)
        if kind is None or type(value) not in (int, str) or not value:
            return ""
        name = self._referred.get((kind, value))
        if name is None:
            if kind in PREFIXES:
                name = self.resource(f"{PREFIXES[kind]}{value}")
            else:
                name = first_line(self.say(self._names.title(kind, value)))
            self._referred[kind, value] = name
        return name

    def said(self, table: str, way: str, holder, step) -> str:
        return self.shown(holder, step) or self.referred(table, way, holder[step])

    # A row goes by its own title; one that has none goes by the thing it is about.
    def title(self, table: str, row: dict) -> str:
        own = row_title(row, self.shown)
        subject = SUBJECTS.get(table)
        if own or subject not in row:
            return own
        return self.said(table, subject, row, subject)


# What the game ships as the name of each thing, before any translation.
class Names:
    def __init__(self, runtime):
        self.runtime = runtime
        self._documents: tuple = ()
        self._listed: dict[str, tuple[str, ...]] = {}

    # The texts each listed resource's name is put together from.
    def listed(self) -> dict[str, tuple[str, ...]]:
        masters = self.runtime.masters
        documents = tuple(masters.document(name) for name in TABLES)
        if unchanged(self._documents, documents):
            return self._listed
        wallets, items, equipment, skills, passes, sheets = (
            document["table"] for document in documents
        )
        listed = dict(UNLISTED)
        listed.update(
            (f"wallet.{row['category']}.{row['name']}", (row["wallet_name"],))
            for row in wallets
        )
        listed.update((row["item_id"], (row["display_name"],)) for row in items)
        listed.update((row["item_id"], (row["name"],)) for row in equipment)
        listed.update(
            (f"{SUB_SKILL_PREFIX}{row['id']}", (row["name"],)) for row in skills
        )
        # Seasons of one pass share its point and its unlock; the first of them names both.
        for row in passes:
            title = row["display_name"]
            listed.setdefault(
                row["ref_prog_point_rid"], (title, row["prog_point_name"])
            )
            listed.setdefault(
                row["ref_premium_track_unlock_rid"], (title, row["unlock_item_name"])
            )
        listed.update(
            (f"{UNLOCK_PREFIX}{row['id']}", (row["name"],))
            for row in sheets
            if row["category"] == UNLOCK
        )
        self._documents, self._listed = documents, listed
        return listed

    # Cards are files of their own and are read only when one is asked for.
    def card(self, rid: str) -> tuple[str, ...]:
        match = CARD.fullmatch(rid)
        if match is None:
            return ()
        try:
            card = self.runtime.masters.asset(CARD_FOLDERS[match[1]], int(match[2]))
        except FileNotFoundError:
            return ()
        return card["nickname"], card["charaName"]

    def title(self, kind: str, identity) -> str:
        table, field = LISTED[kind]
        try:
            return self.runtime.masters.lookup(table, "id", int(identity))[field]
        except (KeyError, ValueError):
            return ""

    def reading(self) -> Reading:
        return Reading(self, self.runtime.localization.speaker())

    # Everything a reward can be made of, for the lists an operator picks from.
    def catalog(self) -> list[dict]:
        masters, named = self.runtime.masters, self.reading()
        rids = self.runtime.resources.rids()
        rids += [
            f"{kind}.card.{card['id']}"
            for kind, folder in CARD_FOLDERS.items()
            for card in masters.assets(folder)
        ]
        return [{"rid": rid, "name": named.resource(rid)} for rid in rids]
