from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.sync import diff_state
from monmusu_ps.features.unit import ROUTES
from monmusu_ps.runtime import Runtime
from test_battle import connect
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
LATER = NOW + 90
CODE = "ARMORY01"
WARRIOR, FAIRY = 1, 2
PATTERN = {"A": 1001, "B": 8101, "C": 9301, "D": 8301}
HP, ATTACK, ULTIMATE = 1001, 1002, 10000
WARRIOR_SYSTEM = 2
CHARM = {"id": 5, "tid": 1, "para": [{"s": 1, "aid": 1, "val": 10}], "created_at": NOW}
UNIT_ENDPOINTS = set(ROUTES)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: NOW, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def state(runtime):
    return create_player("armory", CODE, runtime.masters, runtime.config.read(), NOW)


def give(runtime, state, rid, amount=1):
    runtime.resources.grant(state, [{"rid": rid, "val": amount}], NOW)


def stock(runtime, state, rid):
    return runtime.resources.balance(state, rid)


def unit(state, instance_id=WARRIOR):
    return next(row for row in state["units"] if row["instance_id"] == instance_id)


def rejection(action):
    with pytest.raises(ApiRejected) as refused:
        action()
    return refused.value.reason


def gear(*slots):
    return [{"slot": slot, "id": PATTERN[slot]} for slot in slots]


def equip(runtime, state, orders, now=LATER):
    return runtime.armory.equip_gear(
        state, {"instance_id": WARRIOR, "equip": orders}, now
    )


def attach(runtime, state, slot, skill, now=LATER):
    payload = {
        "instance_id": WARRIOR,
        "slot_no": slot,
        "skill_id": f"skill.attachable.{skill}",
    }
    return runtime.armory.attach_skill(state, payload, now)


def synced(result, key):
    return result.sync[key][0]["data"]


def test_gear_is_consumed_into_its_pattern_slot(runtime, state):
    give(runtime, state, "item.equip.1001", 2)
    give(runtime, state, "item.equip.9301")
    result = equip(runtime, state, gear("A", "C"))
    assert unit(state)["gear_set"] == {"A": 1001, "B": 0, "C": 9301, "D": 0}
    assert unit(state)["updated_at"] == LATER
    assert stock(runtime, state, "item.equip.1001") == 1
    assert {"id": "item.equip.9301", "val": 0} in synced(result, "item")
    assert synced(result, "unit") == [unit(state)]


def test_a_repeated_gear_request_costs_nothing_more(runtime, state):
    give(runtime, state, "item.equip.1001", 2)
    equip(runtime, state, gear("A"))
    assert equip(runtime, state, gear("A")).sync is None
    assert stock(runtime, state, "item.equip.1001") == 1


@pytest.mark.parametrize(
    ("orders", "reason"),
    [
        ([{"slot": "E", "id": 1001}], "INVALID_GEAR_SLOT"),
        ([{"slot": "A", "id": 1001}, {"slot": "A", "id": 1001}], "INVALID_GEAR_SLOT"),
        ([{"slot": "D", "id": 8301}], "INVALID_GEAR_SLOT"),
        ([{"slot": "A", "id": 8101}], "DISPROP_GEAR"),
        ([{"slot": "B", "id": 8101}], "NOT_ENOUGH_MATERIAL"),
        ([{"slot": "A", "id": 1001}, {"slot": "B", "id": 8101}], "NOT_ENOUGH_MATERIAL"),
    ],
)
def test_gear_requests_are_refused_as_a_whole(runtime, state, orders, reason):
    give(runtime, state, "item.equip.1001")
    unit(state)["gear_set"]["D"] = 1
    assert rejection(lambda: equip(runtime, state, orders)) == reason
    assert unit(state)["gear_set"] == {"A": 0, "B": 0, "C": 0, "D": 1}
    assert stock(runtime, state, "item.equip.1001") == 1


@pytest.mark.parametrize("orders", [None, [{"slot": "A", "id": "1001"}], [{"id": 1}]])
def test_malformed_gear_requests_are_errors(runtime, state, orders):
    with pytest.raises((ValueError, KeyError)):
        equip(runtime, state, orders)


def test_gear_follows_the_class_rank(runtime, state):
    unit(state)["rank"] = 1
    give(runtime, state, "item.equip.1002")
    assert rejection(lambda: equip(runtime, state, gear("A"))) == "DISPROP_GEAR"
    equip(runtime, state, [{"slot": "A", "id": 1002}])
    assert unit(state)["gear_set"]["A"] == 1002


def test_unknown_units_are_not_found(runtime, state):
    with pytest.raises(ApiNotFound):
        runtime.armory.equip_gear(state, {"instance_id": 99, "equip": []}, NOW)


@pytest.mark.parametrize(("rank", "opened"), [(0, 0), (1, 1), (2, 2), (4, 2)])
def test_subskill_slots_open_with_the_class_rank(runtime, state, rank, opened):
    unit(state)["rank"] = rank
    give(runtime, state, f"skill.attachable.{HP}", 3)
    for slot in (1, 2, 3):
        if slot <= opened:
            attach(runtime, state, slot, HP)
        else:
            reason = rejection(lambda slot=slot: attach(runtime, state, slot, HP))
            assert reason == "INVALID_SKILL_SLOT"
    assert stock(runtime, state, f"skill.attachable.{HP}") == 3 - opened


def test_replacing_a_subskill_returns_the_old_one(runtime, state):
    unit(state)["rank"] = 1
    give(runtime, state, f"skill.attachable.{HP}")
    give(runtime, state, f"skill.attachable.{ATTACK}")
    attach(runtime, state, 1, HP)
    result = attach(runtime, state, 1, ATTACK)
    assert unit(state)["aa"] == {"s1": ATTACK, "s2": 0, "s3": 0, "s4": 0}
    assert {(row["id"], row["val"]) for row in synced(result, "item")} == {
        (f"skill.attachable.{HP}", 1),
        (f"skill.attachable.{ATTACK}", 0),
    }
    assert synced(result, "unit") == [unit(state)]
    assert attach(runtime, state, 1, ATTACK).sync is None


def test_detaching_returns_the_subskill_whatever_the_capacity(runtime, state):
    unit(state)["rank"] = 1
    give(runtime, state, f"skill.attachable.{HP}")
    attach(runtime, state, 1, HP)
    give(runtime, state, f"skill.attachable.{HP}")
    runtime.config.read()["resources"]["attachable_capacity"] = 1
    payload = {"instance_id": WARRIOR, "slot_no": 1}
    result = runtime.armory.detach_skill(state, payload, LATER)
    assert unit(state)["aa"]["s1"] == 0
    assert stock(runtime, state, f"skill.attachable.{HP}") == 2
    assert synced(result, "item") == [{"id": f"skill.attachable.{HP}", "val": 2}]
    assert runtime.armory.detach_skill(state, payload, LATER).sync is None


@pytest.mark.parametrize(
    "skill_id",
    [
        "skill.attachable.999999",
        "skill.attachable.x",
        "item.equip.1001",
        f"skill.attachable.{ULTIMATE}",
        f"skill.attachable.{ATTACK}",
        5,
    ],
)
def test_unfit_subskills_are_refused(runtime, state, skill_id):
    unit(state)["rank"] = 1
    give(runtime, state, f"skill.attachable.{ULTIMATE}")
    payload = {"instance_id": WARRIOR, "slot_no": 1, "skill_id": skill_id}
    reason = rejection(lambda: runtime.armory.attach_skill(state, payload, NOW))
    assert reason == "DISPROP_SKILL"


def test_restricted_subskills_need_a_matching_unit(runtime, state):
    unit(state)["rank"] = 1
    give(runtime, state, f"skill.attachable.{HP}")
    skill = runtime.masters.lookup("attach_ability_data", "id", HP)
    skill["restrictionForClass"] = 99
    try:
        assert rejection(lambda: attach(runtime, state, 1, HP)) == "DISPROP_SKILL"
        skill["restrictionForClass"] = WARRIOR_SYSTEM
        attach(runtime, state, 1, HP)
    finally:
        skill["restrictionForClass"] = 0
    assert unit(state)["aa"]["s1"] == HP


def test_a_charm_moves_from_the_bag_into_its_slot(runtime, state):
    spare = {**CHARM, "id": 6, "tid": 2}
    state["charms"] = [deepcopy(CHARM), spare]
    payload = {"instance_id": WARRIOR, "slot": 1, "id": 5}
    result = runtime.armory.attach_charm(state, payload, LATER)
    assert state["charms"] == [spare]
    assert unit(state)["charm_set"] == {
        "unlocked": 1,
        "slots": [{"s": 1, "tid": 1, "para": CHARM["para"]}],
    }
    assert set(result.sync) == {"unit"}
    runtime.armory.attach_charm(state, {**payload, "id": 6}, LATER)
    assert state["charms"] == []
    assert [row["tid"] for row in unit(state)["charm_set"]["slots"]] == [2]


@pytest.mark.parametrize(
    ("slot", "identity", "reason"),
    [
        (2, 5, "INVALID_SKILL_SLOT"),
        (0, 5, "INVALID_SKILL_SLOT"),
        (1, 9, "DISPROP_SKILL"),
    ],
)
def test_charms_need_an_open_slot_and_a_held_charm(
    runtime, state, slot, identity, reason
):
    state["charms"] = [deepcopy(CHARM)]
    payload = {"instance_id": WARRIOR, "slot": slot, "id": identity}
    assert rejection(lambda: runtime.armory.attach_charm(state, payload, NOW)) == reason


def test_new_charms_sync_the_whole_bag(state):
    state["charms"] = [deepcopy(CHARM)]
    before = deepcopy(state)
    state["charms"].append({**CHARM, "id": 6})
    assert diff_state(before, state)["charm"][0]["data"] == state["charms"]
    removed = deepcopy(state)
    state["charms"].pop()
    assert diff_state(removed, state) is None


def test_skins_are_listed_from_the_owned_skin_items(runtime, state):
    assert runtime.armory.skins(state) == []
    for rid in ("item.unit_skin.22_3", "item.unit_skin.22_1", "item.unit_skin.11_1"):
        give(runtime, state, rid)
    assert runtime.armory.skins(state) == [
        {"cid": 11, "skins": [1]},
        {"cid": 22, "skins": [1, 3]},
    ]


def test_wearing_a_skin_needs_its_item(runtime, state):
    payload = {"instance_id": FAIRY, "skin_no": 1}
    wear = runtime.armory.equip_skin
    assert rejection(lambda: wear(state, payload, NOW)) == "HAS_INVALID_SKIN_NO"
    give(runtime, state, "item.unit_skin.22_1")
    result = wear(state, payload, LATER)
    assert result.data == {"_z0": 0}
    assert synced(result, "unit") == [unit(state, FAIRY)]
    assert unit(state, FAIRY)["skin"] == 1
    assert wear(state, payload, LATER).sync is None
    assert rejection(lambda: wear(state, {**payload, "skin_no": 9}, NOW)) == (
        "HAS_INVALID_SKIN_NO"
    )
    wear(state, {**payload, "skin_no": 0}, LATER)
    assert unit(state, FAIRY)["skin"] == 0
    with pytest.raises(ValueError):
        wear(state, {**payload, "skin_no": "1"}, NOW)


def test_exskill_choices_follow_the_unlock_flags(runtime, state):
    choose = runtime.armory.equip_exskill
    payload = {"instance_id": WARRIOR, "slot_no": 2, "mode": "primary"}
    assert rejection(lambda: choose(state, payload, NOW)) == "INVALID_SKILL_SLOT"
    unit(state)["flags"] = 3
    result = choose(state, payload, LATER)
    assert (unit(state)["primary_exskill"], unit(state)["rental_exskill"]) == (2, 1)
    assert synced(result, "unit") == [unit(state)]
    choose(state, {**payload, "mode": "rental"}, LATER)
    assert unit(state)["rental_exskill"] == 2
    assert choose(state, payload, LATER).sync is None
    for slot in (0, 3, 4, "1"):
        refused = rejection(
            lambda slot=slot: choose(state, {**payload, "slot_no": slot}, NOW)
        )
        assert refused == "INVALID_SKILL_SLOT"
    with pytest.raises(KeyError):
        choose(state, {**payload, "mode": "deck"}, NOW)


def test_skin_listing_matches_the_client_dto(runtime, state):
    give(runtime, state, "item.unit_skin.22_1")
    context = Context(runtime, {}, state, runtime.sessions.create(CODE), NOW)
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))
    data = ROUTES["skin/all"](context).data
    expected = {
        field["name"]
        for field in schema["models"]["GetSkinAllResponse200Data"]["fields"]
    }
    assert set(data) == expected
    assert set(data["skins"][0]) == {
        field["name"] for field in schema["models"]["SkinItem"]["fields"]
    }


async def test_unit_endpoints_are_registered(tmp_path):
    http, _, runtime, _ = await connect(tmp_path)
    try:
        assert UNIT_ENDPOINTS <= set(runtime.registry.handlers)
    finally:
        await http.close()
