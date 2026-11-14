from dataclasses import dataclass
from copy import deepcopy
from typing import Any


@dataclass
class Result:
    data: Any = None
    sync: dict | None = None
    include_data: bool = True


def scoped(state: dict, **collections) -> dict:
    scope = f"player@{state['player']['player_code']}"
    return {
        key: [{"scope": scope, "data": value}] for key, value in collections.items()
    }


def merged(first: dict | None, second: dict | None) -> dict | None:
    if not first or not second:
        return first or second
    return {
        key: [*first.get(key, []), *second.get(key, [])] for key in {**first, **second}
    }


class Outbox:
    def __init__(self):
        self._pending: dict[str, dict] = {}

    def post(self, profile: str, sync: dict | None) -> None:
        pending = merged(self._pending.get(profile), sync)
        if pending:
            self._pending[profile] = pending

    def collect(self, profile: str, sync: dict | None) -> dict | None:
        return merged(self._pending.pop(profile, None), sync)

    def discard(self, profile: str) -> None:
        self._pending.pop(profile, None)


def envelope(result: Result, now: int) -> dict:
    value = {
        "status": {"code": 1, "message": "succeeded"},
        "sync": result.sync,
        "log": [],
        "timesync": now,
        "to": 0,
    }
    if result.include_data:
        value["data"] = result.data
    return value


COLLECTIONS = {
    "unit": ("units", ("instance_id",)),
    "bom": ("boms", ("instance_id",)),
    "composition": ("compositions", ("section", "no")),
    "wallet": ("wallets", ("category", "name")),
    "item": ("items", ("id",)),
    "charm": ("charms", ("id",)),
    "quest_record": ("quest_records", ("quest_id",)),
    "quest_secret": ("quest_secrets", ("category_id",)),
    "bonuspass_progression": ("bonus_passes", ("pass_id",)),
    "boxgacha_record": ("boxgacha_records", ("id",)),
    "gacha_stamp_record": ("gacha_stamp_records", ("id",)),
    "mission_record": ("missions", ("id",)),
    "mission_counter": ("mission_counters", ("name",)),
    "period": ("periods", ("id",)),
    "limit": ("limits", ("id",)),
    "story_record": ("stories", ("id",)),
    "rev_reminder": ("revisioned_reminders", ("group", "id")),
    "flag_reminder": ("flag_reminders", ("group", "id")),
}


# The client rebuilds these collections from whatever a sync carries.
WHOLE = ("charm",)


def changed_rows(before: list, after: list, keys: tuple[str, ...]) -> list:
    previous = {tuple(row[key] for key in keys): row for row in before}
    return [
        row for row in after if previous.get(tuple(row[key] for key in keys)) != row
    ]


def diff_state(before: dict, after: dict) -> dict | None:
    updates = {}
    if before["player"] != after["player"]:
        updates["player"] = [after["player"]]
    modules = {}
    for name, rows in after["modules"].items():
        changed = changed_rows(before["modules"][name], rows, ("section",))
        if changed:
            modules[name] = changed
    if modules:
        updates["module_prop"] = modules
    for sync_key, (state_key, keys) in COLLECTIONS.items():
        rows = changed_rows(before[state_key], after[state_key], keys)
        if rows:
            updates[sync_key] = after[state_key] if sync_key in WHOLE else rows
    return scoped(after, **deepcopy(updates)) if updates else None
