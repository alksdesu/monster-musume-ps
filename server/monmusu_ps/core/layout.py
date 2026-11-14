from dataclasses import dataclass
from itertools import combinations


ROWS = "table"
INDEX = "#"
KEYABLE = (int, str)


@dataclass(frozen=True)
class Layout:
    keys: tuple[str, ...]
    nest: str | None = None
    holder: str = ROWS

    @property
    def container(self) -> tuple[str, ...]:
        return (self.holder,) if self.nest is None else (self.holder, self.nest)

    def rows(self, document: dict) -> list[dict]:
        outer = document[self.holder]
        if self.nest is None:
            return outer
        return [row for group in outer for row in group[self.nest]]

    def key(self, row: dict, position: int) -> tuple:
        if self.keys == (INDEX,):
            return (position,)
        return tuple(row[field] for field in self.keys)


KNOWN = {
    "gacha_data": Layout(("id",), "enclosure"),
    "everyones_killevent_data": Layout(("dataId",)),
}
# Documents that keep their rows somewhere other than a table.
DOCUMENTS = {
    "faq_data": Layout(("id",), holder="documents"),
    "help_data": Layout(("id",), holder="documents"),
    "attr_list": Layout(("m_id",), holder="m_table"),
    "property_sheet": Layout(("m_key",), holder="m_propertyString"),
}


def _rank(name: str) -> int:
    lowered = name.lower()
    return 0 if lowered == "id" else 1 if lowered.endswith("id") else 2


def infer(rows: list[dict]) -> tuple[str, ...]:
    fields = [
        name for name in rows[0] if all(type(row.get(name)) in KEYABLE for row in rows)
    ]
    fields.sort(key=_rank)
    for size in (1, 2):
        for chosen in combinations(fields, size):
            seen = {tuple(row[name] for name in chosen) for row in rows}
            if len(seen) == len(rows):
                return chosen
    # Rows that nothing tells apart are named by where they stand.
    return (INDEX,)


class Layouts:
    def __init__(self):
        self._known: dict[str, tuple[dict, Layout]] = {}

    def of(self, name: str, document: dict) -> Layout:
        cached = self._known.get(name)
        if cached is None or cached[0] is not document:
            self._known[name] = document, self._read(name, document)
        return self._known[name][1]

    @staticmethod
    def _read(name: str, document: dict) -> Layout:
        if name in KNOWN:
            return KNOWN[name]
        if name in DOCUMENTS:
            return DOCUMENTS[name]
        rows = document.get(ROWS)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{name} is not a table of rows")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"{name} is not a table of rows")
        return Layout(infer(rows))


BATTLE = (
    *("ability_data", "attach_ability_data", "battle_event", "charm_ability_data"),
    *("curse_data", "enemy_node_ability_data", "ex_ability_data", "pray_ability_data"),
    *("special_ability", "summon_point_data", "unique_weapon_ability_data"),
    *("possession_ability_data", "skill_data", "class_action_data"),
    *("general_buff_debuff_data", "special_action", "special_action_group"),
    *("special_attack", "buff_field", "damage_field", "missile_data"),
    *("special_missile", "barrier_data", "barrier_attribute_effect_data"),
    *("weather_list", "type_bonus", "attr_list", "battle_member_data"),
    *("battle_npc_data", "enemy_summon_data", "token_data", "bom_data"),
    *("bom_lv_buff_data", "bom_num_buff_data", "job_data", "job_weapon_data"),
    *("tribe_list", "trait_tag", "trait_group_table", "npc_status_view_data"),
    *("unit_lv_buff", "beast_gods_blessing_effect", "beast_gods_blessing_table"),
    *("endless_challenge_buff", "wild_hunt_fever_mode"),
    *("wild_hunt_last_entry_loop_buff", "wild_hunt_next_wave_trigger_condition"),
    *("event_sealed_chara_mods", "equipment", "unique_weapon_data"),
    *("quest_training_data", "sort_config"),
)
GROWTH = (
    *("unit_rarity", "gear_rarity", "charm_rarity", "unit_rank_info_table"),
    *("equipment_pattern", "equipment_cost_exp_table"),
)
PLACEMENT = (
    *("effect_data", "motion_data", "enemy_ui_offset_data", "unit_ui_offset_data"),
    *("unit_offset_data", "unit_emote_data", "sound_bgm_data", "sound_voice_data"),
    *("resource_change_path_r18", "home_unit_touch_area"),
)
UNREAD = ("unit_rarity_info", "resource_change_path_str", "game_start_loading_image")
SETTINGS = ("property_sheet",)
LOCKED = {
    **dict.fromkeys(BATTLE, "battle data"),
    **dict.fromkeys(GROWTH, "it sets the strength units and gear already have"),
    **dict.fromkeys(PLACEMENT, "it tells the game where its assets are"),
    **dict.fromkeys(UNREAD, "the game never reads it once it is running"),
    **dict.fromkeys(SETTINGS, "it holds the settings the game and the server run by"),
}
# Tables that are operations content except for the values a battle is run with.
GUARDED = {
    "quest_data": frozenset(
        (
            *("category", "isScenarioOnly", "isEndless", "isWildHunt", "buffId"),
            *("mapType", "mapDataIndex", "enemyRevision", "coreLife", "mana"),
            *("sortieCount", "battleMemberId", "battleNPCId", "limitMemberId"),
            *("loseTimeLimitFrame", "bgmIndex", "bgmPriority", "trainingIds"),
            *("countup_targets", "target_num_check_list"),
        )
    ),
    "training_menu": frozenset(("quest_id", "type", "training_id")),
    "composition_template": frozenset(("init_slot_capacity", "max_slot_capacity")),
}
