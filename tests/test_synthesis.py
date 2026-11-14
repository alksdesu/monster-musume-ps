from copy import deepcopy
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.synthesis import rarity_id
from monmusu_ps.runtime import Runtime


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
LATER = NOW + 90
CODE = "SYNTH001"
WARRIOR, FAIRY = 1, 2
FIXED, FIXED_PRODUCT = 1, 1105
MIXED, MIXED_PRODUCT = 20, 1150
EPIC, RARE = 1003, 1002
LEGEND, BOUGHT_LEGEND = 1049, 1052
BAG = {"s1": 0, "s2": 0, "s3": 0, "s4": 0}
NOBODY = {"s1": None, "s2": None, "s3": None, "s4": None}


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
    return create_player("synth", CODE, runtime.masters, runtime.config.read(), NOW)


def skill(identity):
    return f"skill.attachable.{identity}"


def give(runtime, state, rid, amount=1):
    runtime.resources.grant(state, [{"rid": rid, "val": amount}], NOW)


def stock(runtime, state, identity):
    return runtime.resources.balance(state, skill(identity))


def unit(state, instance_id=WARRIOR):
    return next(row for row in state["units"] if row["instance_id"] == instance_id)


def request(recipe, alt=None, wc_skill=None, wc_alt=None):
    return {
        "recipe_id": recipe,
        "alt": {**BAG, **(alt or {})},
        "wc_skill": {**BAG, **(wc_skill or {})},
        "wc_alt": {**NOBODY, **(wc_alt or {})},
    }


def rejection(runtime, state, payload):
    with pytest.raises(ApiRejected) as refused:
        runtime.synthesis.synthesize(state, payload, NOW)
    return refused.value.reason


def test_rarity_names_encode_to_the_master_ids():
    assert [rarity_id(name) for name in ("C1", "R1", "SR1", "SSR1")] == [
        17201,
        21041,
        5460529,
        1397969457,
    ]


def test_fixed_materials_come_out_of_the_bag(runtime, state):
    give(runtime, state, f"item.sskill_rcp.{FIXED}")
    give(runtime, state, skill(EPIC), 2)
    give(runtime, state, skill(RARE), 3)
    result = runtime.synthesis.synthesize(state, request(FIXED), LATER)
    assert result.data == {
        "recipe_id": FIXED,
        "generated_resource": {"rid": skill(FIXED_PRODUCT), "val": 1},
    }
    assert (stock(runtime, state, EPIC), stock(runtime, state, RARE)) == (0, 1)
    assert stock(runtime, state, FIXED_PRODUCT) == 1
    assert runtime.resources.balance(state, f"item.sskill_rcp.{FIXED}") == 1
    rows = {row["id"]: row["val"] for row in result.sync["item"][0]["data"]}
    assert rows == {skill(EPIC): 0, skill(RARE): 1, skill(FIXED_PRODUCT): 1}
    assert "unit" not in result.sync


def test_materials_can_be_taken_off_units(runtime, state):
    give(runtime, state, f"item.sskill_rcp.{FIXED}")
    give(runtime, state, skill(RARE), 2)
    unit(state)["aa"].update(s1=EPIC, s2=EPIC)
    payload = request(FIXED, alt={"s1": WARRIOR, "s2": WARRIOR})
    result = runtime.synthesis.synthesize(state, payload, LATER)
    assert unit(state)["aa"] == {"s1": 0, "s2": 0, "s3": 0, "s4": 0}
    assert unit(state)["updated_at"] == LATER
    assert result.sync["unit"][0]["data"] == [unit(state)]
    assert (stock(runtime, state, EPIC), stock(runtime, state, RARE)) == (0, 0)
    assert stock(runtime, state, FIXED_PRODUCT) == 1


def test_one_worn_subskill_fills_one_material_slot(runtime, state):
    give(runtime, state, f"item.sskill_rcp.{FIXED}")
    give(runtime, state, skill(RARE), 2)
    unit(state)["aa"]["s1"] = EPIC
    payload = request(FIXED, alt={"s1": WARRIOR, "s2": WARRIOR})
    assert rejection(runtime, state, payload) == "INVALID_UNIT_CONDITION"
    missing = request(FIXED, alt={"s1": WARRIOR, "s2": 99})
    assert rejection(runtime, state, missing) == "INVALID_UNIT_CONDITION"


def test_wildcard_slots_take_a_chosen_subskill_of_their_rarity(runtime, state):
    give(runtime, state, f"item.sskill_rcp.{MIXED}")
    for identity in (1006, 1009, 1012, LEGEND, BOUGHT_LEGEND, EPIC):
        give(runtime, state, skill(identity))
    for choice in (BOUGHT_LEGEND, EPIC, 0, 999999):
        payload = request(MIXED, wc_skill={"s4": choice})
        assert rejection(runtime, state, payload) == "NOT_ENOUGH_MATERIAL"
    runtime.synthesis.synthesize(state, request(MIXED, wc_skill={"s4": LEGEND}), LATER)
    assert stock(runtime, state, LEGEND) == 0
    assert stock(runtime, state, MIXED_PRODUCT) == 1


def test_wildcard_slots_can_take_a_worn_subskill(runtime, state):
    give(runtime, state, f"item.sskill_rcp.{MIXED}")
    for identity in (1006, 1009, 1012):
        give(runtime, state, skill(identity))
    unit(state, FAIRY)["aa"]["s2"] = LEGEND
    worn = {"s4": {"instance_id": FAIRY, "skill_id": LEGEND}}
    runtime.synthesis.synthesize(state, request(MIXED, wc_alt=worn), LATER)
    assert unit(state, FAIRY)["aa"]["s2"] == 0
    assert stock(runtime, state, MIXED_PRODUCT) == 1
    for identity in (1006, 1009, 1012):
        give(runtime, state, skill(identity))
    assert rejection(runtime, state, request(MIXED, wc_alt=worn)) == (
        "INVALID_UNIT_CONDITION"
    )


def test_synthesis_needs_the_tablet_the_stock_and_room(runtime, state):
    give(runtime, state, skill(EPIC), 2)
    give(runtime, state, skill(RARE), 1)
    assert rejection(runtime, state, request(FIXED)) == "NOT_ENOUGH_MATERIAL"
    give(runtime, state, f"item.sskill_rcp.{FIXED}")
    assert rejection(runtime, state, request(FIXED)) == "NOT_ENOUGH_MATERIAL"
    give(runtime, state, skill(RARE), 1)
    give(runtime, state, skill(FIXED_PRODUCT), 1)
    runtime.config.read()["resources"]["attachable_capacity"] = 1
    assert rejection(runtime, state, request(FIXED)) == "ASSET_CAPACITY_OVERFLOW"
    with pytest.raises(ApiNotFound):
        runtime.synthesis.synthesize(state, request(999), NOW)
