from copy import deepcopy
import json

from ..core.context import Context
from ..core.errors import ApiConflict, ApiRejected
from ..core.sync import Result, diff_state, scoped
from ..core.tutorial import DECK


def initialize(ctx: Context) -> Result:
    return Result()


def get_player(ctx: Context) -> Result:
    before = deepcopy(ctx.state)
    ctx.runtime.progression.recover_stamina(ctx.state, ctx.now)
    ctx.runtime.attendance.check_in(ctx.state, ctx.now)
    modules = {
        name: ctx.state["modules"][name] for name in ctx.payload["player_module_ids"]
    }
    player = ctx.state["player"]
    sync = diff_state(before, ctx.state) or {}
    sync.update(scoped(ctx.state, player=[player], module_prop=modules))
    return Result(
        {
            "player": player,
            "player_text": ctx.state["player_text"],
            "modules": modules,
            "votes": ctx.runtime.votes.summaries(ctx.now),
            "resource_penalty": None,
        },
        sync,
    )


def home(ctx: Context) -> Result:
    before = deepcopy(ctx.state)
    ctx.state["home_visited_at"] = ctx.now
    ctx.runtime.progression.recover_stamina(ctx.state, ctx.now)
    ctx.runtime.battle_logs.remind(ctx.state, ctx.now)
    ctx.runtime.social.remind(ctx.state, ctx.now)
    return Result(sync=diff_state(before, ctx.state))


def nickname(ctx: Context) -> Result:
    return Result({"nickname": ctx.state["player"]["nickname"]})


def set_nickname(ctx: Context) -> Result:
    ctx.state["player"]["nickname"] = ctx.payload["nickname"]
    return Result(sync=scoped(ctx.state, player=[ctx.state["player"]]))


def set_player_text(ctx: Context) -> Result:
    field = {"player.name": "name", "player.bio": "bio"}[ctx.payload["key"]]
    text = ctx.payload["text"]
    if not isinstance(text, str):
        raise ValueError("Player text must be a string")
    ctx.state["player_text"][field] = text
    return Result(include_data=False)


def compositions(ctx: Context) -> Result:
    return Result(
        {
            "compositions": [
                row
                for row in ctx.state["compositions"]
                if row["section"] == ctx.payload["section"]
            ]
        }
    )


def composition(ctx: Context) -> Result:
    row = next(
        row
        for row in ctx.state["compositions"]
        if row["section"] == ctx.payload["section"] and row["no"] == ctx.payload["no"]
    )
    return Result({"composition": row})


def _composition_value(ctx: Context, value: dict) -> dict:
    template = next(
        (
            row
            for row in ctx.runtime.masters.table("composition_template")
            if row["section"] == value["section"]
        ),
        None,
    )
    if template is None:
        raise ApiRejected("INVALID_COMPOSITION_SECTION")
    module = next(
        row
        for row in ctx.state["modules"]["composition"]
        if row["section"] == value["section"]
    )
    if type(value["no"]) is not int or not 1 <= value["no"] <= module["capacity"]:
        raise ApiRejected("COMPOSITION_CAPACITY_OVER")
    if not isinstance(value["label"], str):
        raise ValueError("Composition label must be a string")
    if len(value["label"]) > template["label_max_len"]:
        raise ApiRejected("LABEL_TOO_LONG")
    slots = value["slots"]
    if not isinstance(slots, list):
        raise ValueError("Composition slots must be a list")
    if len(slots) > module["slot_capacity"]:
        raise ApiRejected("SLOT_CAPACITY_OVER")
    owned = {
        1: {row["instance_id"] for row in ctx.state["units"]},
        2: {row["instance_id"] for row in ctx.state["boms"]},
    }
    members, numbers = set(), set()
    for raw in slots:
        slot = json.loads(raw)
        kind, instance, number = slot["t"], slot["iid"], slot["sn"]
        if type(number) is not int or not 0 <= number < module["slot_capacity"]:
            raise ApiRejected("SLOT_CAPACITY_OVER")
        if instance not in owned.get(kind, ()):
            raise ValueError("Composition slot does not match owned units")
        if (kind, instance) in members or (kind, number) in numbers:
            raise ApiRejected("SLOT_NOT_UNIQUE")
        members.add((kind, instance))
        numbers.add((kind, number))
    json.loads(value["exdata"])
    return {
        "section": value["section"],
        "no": value["no"],
        "label": value["label"],
        "slots": list(slots),
        "exdata": value["exdata"],
    }


def _composition_index(state: dict, value: dict) -> int | None:
    return next(
        (
            index
            for index, row in enumerate(state["compositions"])
            if row["section"] == value["section"] and row["no"] == value["no"]
        ),
        None,
    )


def set_composition(ctx: Context) -> Result:
    value = _composition_value(ctx, ctx.payload["composition"])
    before = deepcopy(ctx.state)
    index = _composition_index(ctx.state, value)
    if index is None:
        ctx.state["compositions"].append(value)
    else:
        ctx.state["compositions"][index] = value
    ctx.runtime.tutorial.complete_step(ctx.state, DECK, ctx.now)
    sync = diff_state(before, ctx.state) or {}
    sync.update(scoped(ctx.state, composition=[value]))
    return Result(sync=sync)


def create_composition(ctx: Context) -> Result:
    value = _composition_value(ctx, ctx.payload["composition"])
    if _composition_index(ctx.state, value) is not None:
        raise ApiConflict(f"composition.{value['section']}.{value['no']}")
    ctx.state["compositions"].append(value)
    return Result(sync=scoped(ctx.state, composition=[value]))


def _prefs_owner(ctx: Context) -> str:
    owner = ctx.payload["owner_id"]
    if ctx.payload["owner_domain"] != "player" or owner not in (
        "@me",
        ctx.state["player"]["player_code"],
    ):
        raise ValueError("Preference owner is outside this player")
    return ctx.state["player"]["player_code"]


def _prefs_template(ctx: Context, name) -> str:
    templates = ctx.runtime.config.read()["prefs"]
    if not isinstance(name, str) or name not in templates:
        raise ApiRejected("UNDEFINED_PREFS")
    return templates[name]


def _prefs_row(ctx: Context, owner: str, name) -> dict:
    template = _prefs_template(ctx, name)
    return ctx.state["prefs"].get(name) or {
        "prefs_name": name,
        "owner_domain": "player",
        "owner_id": owner,
        "payload": template,
    }


def _prefs_payload(value) -> str:
    if not isinstance(value, str):
        raise ValueError("Preference payload must be a string")
    try:
        json.loads(value)
    except ValueError as error:
        raise ApiRejected("CONTENTS_JSON_PARSE_ERROR") from error
    return value


def _store_prefs(ctx: Context, owner: str, name: str, payload: str) -> dict:
    value = {
        "prefs_name": name,
        "owner_domain": "player",
        "owner_id": owner,
        "payload": payload,
    }
    ctx.state["prefs"][name] = value
    return value


def prefs_list(ctx: Context) -> Result:
    owner = _prefs_owner(ctx)
    names = ctx.payload["prefs_names"]
    if not isinstance(names, list):
        raise ValueError("prefs_names must be a list")
    return Result({"prefs_list": [_prefs_row(ctx, owner, name) for name in names]})


def prefs_get(ctx: Context) -> Result:
    owner = _prefs_owner(ctx)
    return Result({"prefs": _prefs_row(ctx, owner, ctx.payload["prefs_name"])})


def prefs_set(ctx: Context) -> Result:
    owner = _prefs_owner(ctx)
    name = ctx.payload["prefs_name"]
    _prefs_template(ctx, name)
    payload = _prefs_payload(ctx.payload["payload"])
    return Result({"prefs": _store_prefs(ctx, owner, name, payload)})


def prefs_create(ctx: Context) -> Result:
    owner = _prefs_owner(ctx)
    name = ctx.payload["prefs_name"]
    template = _prefs_template(ctx, name)
    if name in ctx.state["prefs"]:
        raise ApiConflict(f"prefs.{name}")
    return Result({"prefs": _store_prefs(ctx, owner, name, template)})


ROUTES = {
    "player/init": initialize,
    "player/get": get_player,
    "player/home/visit": home,
    "player/nickname/": nickname,
    "player/nickname/update/": set_nickname,
    "social/user_text/update": set_player_text,
    "composition/list": compositions,
    "composition": composition,
    "composition/update": set_composition,
    "composition/create": create_composition,
    "prefs/list": prefs_list,
    "prefs": prefs_get,
    "prefs/set": prefs_set,
    "prefs/create": prefs_create,
}
