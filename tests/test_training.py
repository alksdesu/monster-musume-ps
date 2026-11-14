from copy import deepcopy
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.runtime import Runtime


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
LATER = NOW + 90
DAY = 86400
CODE = "TRAINER1"
WARRIOR, FAIRY, BOM = 1, 2, 1
GOLD = "wallet.point.gold"
MILK, CREAM, RIVER = (
    "wallet.unit_exp.r_all",
    "wallet.unit_exp.sr_all",
    "wallet.unit_exp.lr_all",
)
BROWN = "item.material.Brown1"
RANK_CAP_EXP = 15558
PATTERN = {"A": 1001, "B": 8101, "C": 9301, "D": 8301}
NEXT_PATTERN = {"A": 1002, "B": 8102, "C": 9302, "D": 8302}
WHETSTONE, FINE_WHETSTONE = (
    "wallet.event.pt_exweapon240603_c",
    "wallet.event.pt_exweapon240603_l",
)


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
    state = create_player("trainer", CODE, runtime.masters, runtime.config.read(), NOW)
    runtime.missions.sweep(state, NOW)
    for period in set(runtime.missions._panels.values()):
        runtime.periods.open(state, period, NOW, NOW + DAY, NOW)
    purse = [{"rid": GOLD, "val": runtime.resources.balance(state, GOLD)}]
    runtime.resources.spend(state, purse, NOW)
    return state


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


def synced(result, key):
    return result.sync[key][0]["data"]


def achieved(state):
    return {row["id"] for row in state["missions"]}


def feed(runtime, state, *materials, now=LATER):
    orders = [{"rid": rid, "val": amount} for rid, amount in materials]
    payload = {"instance_id": WARRIOR, "materials": orders}
    return runtime.training.enhance(state, payload, now)


def ripen(runtime, state, pattern=PATTERN, gold=1000):
    warrior = unit(state)
    cap = runtime.progression.level_cap(warrior)
    table = next(
        row["table"]
        for row in runtime.masters.document("unit_level_exp_table")["tables"]
        if row["rarityId"] == 82
    )
    warrior.update(t_exp=table[cap - 1], r_unit_lv=cap, gear_set=dict(pattern))
    give(runtime, state, GOLD, gold)


def test_milk_raises_experience_and_level(runtime, state):
    give(runtime, state, MILK, 4)
    give(runtime, state, CREAM, 1)
    result = feed(runtime, state, (MILK, 3), (CREAM, 1))
    warrior = unit(state)
    assert (warrior["t_exp"], warrior["r_unit_lv"], warrior["updated_at"]) == (
        650,
        7,
        LATER,
    )
    assert result.data == {"unit": warrior, "cost": {"rid": GOLD, "val": 0}}
    assert synced(result, "unit") == [warrior]
    assert (stock(runtime, state, MILK), stock(runtime, state, CREAM)) == (1, 0)
    assert state["events"][-1]["data"] == {"instance_id": WARRIOR, "action": "level"}


def test_overflowing_milk_stops_at_the_level_cap(runtime, state):
    give(runtime, state, RIVER, 2)
    feed(runtime, state, (RIVER, 1))
    assert (unit(state)["t_exp"], unit(state)["r_unit_lv"]) == (RANK_CAP_EXP, 30)
    refused = rejection(lambda: feed(runtime, state, (RIVER, 1)))
    assert refused == "HAS_CANT_CONSUMABLE_MATERIAL"
    assert stock(runtime, state, RIVER) == 1


@pytest.mark.parametrize(
    ("materials", "reason"),
    [
        ([(GOLD, 1)], "HAS_INVALID_MATERIAL"),
        ([(MILK, 0)], "HAS_INVALID_MATERIAL"),
        ([(MILK, -1)], "HAS_INVALID_MATERIAL"),
        ([(MILK, "1")], "HAS_INVALID_MATERIAL"),
        ([], "HAS_CANT_CONSUMABLE_MATERIAL"),
        ([(MILK, 2)], "NOT_ENOUGH_MATERIAL"),
        ([(MILK, 1), (MILK, 1)], "NOT_ENOUGH_MATERIAL"),
    ],
)
def test_unusable_feed_materials_are_refused(runtime, state, materials, reason):
    give(runtime, state, MILK, 1)
    assert rejection(lambda: feed(runtime, state, *materials)) == reason
    assert unit(state)["t_exp"] == 0


def test_training_advances_level_missions(runtime, state):
    give(runtime, state, RIVER, 1)
    feed(runtime, state, (RIVER, 1))
    counters = {row["name"]: row["val"] for row in state["mission_counters"]}
    assert (counters["5.lv10_unit2"], counters["7.70005"]) == (1, 1)
    assert {26204, 70004, 70017} <= achieved(state)
    assert 26205 not in achieved(state)


def test_class_change_spends_the_gear_and_the_gold(runtime, state):
    ripen(runtime, state)
    result = runtime.training.upgrade(state, {"instance_id": WARRIOR}, LATER)
    warrior = unit(state)
    assert (warrior["rank"], warrior["r_unit_lv"], warrior["t_exp"]) == (
        1,
        30,
        RANK_CAP_EXP,
    )
    assert warrior["gear_set"] == {"A": 0, "B": 0, "C": 0, "D": 0}
    assert warrior["gear_history"] == [PATTERN]
    assert result.data == {"unit": warrior}
    assert {"unit", "wallet"} <= set(result.sync)
    assert stock(runtime, state, GOLD) == 0
    assert runtime.progression.level_cap(warrior) == 60
    assert 70003 in achieved(state)


def test_class_change_conditions(runtime, state):
    change = runtime.training.upgrade
    payload = {"instance_id": WARRIOR}
    ripen(runtime, state, gold=999)
    assert rejection(lambda: change(state, payload, NOW)) == "NOT_ENOUGH_COST"
    unit(state)["gear_set"]["D"] = 0
    assert rejection(lambda: change(state, payload, NOW)) == "CONDITIONS_VIOLATION"
    unit(state)["gear_set"]["D"] = PATTERN["D"]
    unit(state).update(r_unit_lv=29)
    assert rejection(lambda: change(state, payload, NOW)) == "CONDITIONS_VIOLATION"
    unit(state).update(rank=4, r_unit_lv=150)
    assert rejection(lambda: change(state, payload, NOW)) == "THERE_NO_UPGRADE"
    with pytest.raises(ApiNotFound):
        change(state, {"instance_id": 99}, NOW)


def test_easy_upgrade_feeds_equips_and_changes_class_in_order(runtime, state):
    give(runtime, state, RIVER, 2)
    give(runtime, state, GOLD, 1000)
    for identity in (*PATTERN.values(), NEXT_PATTERN["A"]):
        give(runtime, state, f"item.equip.{identity}")
    payload = {
        "enhancement_1": {
            "instance_id": WARRIOR,
            "materials": [{"rid": RIVER, "val": 1}],
        },
        "equip_gear_1": {
            "instance_id": WARRIOR,
            "equip": [
                {"slot": slot, "id": identity} for slot, identity in PATTERN.items()
            ],
        },
        "upgrade_1": {"instance_id": WARRIOR},
        "enhancement_2": {
            "instance_id": WARRIOR,
            "materials": [{"rid": RIVER, "val": 1}],
        },
        "equip_gear_2": {
            "instance_id": WARRIOR,
            "equip": [{"slot": "A", "id": NEXT_PATTERN["A"]}],
        },
        "upgrade_2": None,
        "enhancement_3": None,
    }
    result = runtime.training.enhance_all(state, payload, LATER)
    warrior = unit(state)
    assert (warrior["rank"], warrior["t_exp"]) == (1, RANK_CAP_EXP + 50000)
    assert 30 < warrior["r_unit_lv"] < 60
    assert warrior["gear_set"] == {"A": NEXT_PATTERN["A"], "B": 0, "C": 0, "D": 0}
    assert result.data is None
    assert synced(result, "unit")[0] == warrior
    assert {"wallet", "item"} <= set(result.sync)
    assert stock(runtime, state, RIVER) == 0


def test_easy_upgrade_reports_step_failures_with_its_own_reasons(runtime, state):
    give(runtime, state, RIVER, 1)
    steps = {
        "enhancement_1": {
            "instance_id": WARRIOR,
            "materials": [{"rid": RIVER, "val": 1}],
        },
        "upgrade_1": {"instance_id": WARRIOR},
    }
    train = runtime.training.enhance_all
    unripe = rejection(lambda: train(deepcopy(state), steps, NOW))
    assert unripe == "HAS_CANT_CONSUMABLE_MATERIAL"
    short = {
        "enhancement_1": {
            "instance_id": WARRIOR,
            "materials": [{"rid": RIVER, "val": 2}],
        }
    }
    assert rejection(lambda: train(deepcopy(state), short, NOW)) == (
        "NOT_ENOUGH_MATERIAL"
    )
    mixed = {**steps, "upgrade_1": {"instance_id": FAIRY}}
    with pytest.raises(ValueError):
        train(state, mixed, NOW)
    with pytest.raises(ValueError):
        train(state, {}, NOW)


def test_awakening_opens_slots_within_the_points(runtime, state):
    awaken = runtime.training.awaken
    unit(state)["upa"]["all_pt"] = 3

    def request(*slots):
        return awaken(state, {"instance_id": WARRIOR, "skills": list(slots)}, LATER)

    result = request(2)
    assert result.data == {"_z0": 0}
    assert unit(state)["upa"] == {"all_pt": 3, "used_pt": 1, "skills": [2]}
    assert synced(result, "unit") == [unit(state)]
    request(3, 1)
    assert unit(state)["upa"] == {"all_pt": 3, "used_pt": 3, "skills": [1, 2, 3]}
    assert rejection(lambda: request(4)) == "NOT_ENOUGH_SKILL_POINT"
    assert rejection(lambda: request(2)) == "NOT_ENOUGH_SKILL_SLOT"
    assert rejection(lambda: request(6)) == "HAS_INVALID_SLOT"
    assert rejection(lambda: request(4, 4)) == "HAS_INVALID_SLOT"
    assert rejection(lambda: request()) == "NOT_ENOUGH_WALLET_FOR_RESET"
    give(runtime, state, GOLD, 500)
    reset = request()
    assert unit(state)["upa"] == {"all_pt": 3, "used_pt": 0, "skills": []}
    assert {"unit", "wallet"} <= set(reset.sync)
    assert stock(runtime, state, GOLD) == 0
    assert rejection(lambda: request()) == "ALREADY_EMPTY_SLOT"


def test_cards_without_awakening_abilities_refuse_slots(runtime, state):
    card = runtime.masters.asset("unitcard", 11)
    ability, card["awakingAbilityId4"] = card["awakingAbilityId4"], 0
    unit(state)["upa"]["all_pt"] = 5
    payload = {"instance_id": WARRIOR, "skills": [4]}
    try:
        reason = rejection(lambda: runtime.training.awaken(state, payload, NOW))
    finally:
        card["awakingAbilityId4"] = ability
    assert reason == "HAS_INVALID_SKILL_ID"


def test_exskills_rise_one_level_per_recipe_tier(runtime, state):
    enhance = runtime.training.enhance_exskill
    payload = {"instance_id": WARRIOR, "slot_no": 1}
    assert rejection(lambda: enhance(state, payload, NOW)) == "NOT_ENOUGH_MATERIAL"
    give(runtime, state, BROWN, 3 + 5 + 8 + 10)
    result = enhance(state, payload, LATER)
    assert result.data == {"unit": unit(state)}
    assert unit(state)["exskill_lv"] == 2
    assert stock(runtime, state, BROWN) == 23
    assert {"unit", "item"} <= set(result.sync)
    assert 70009 in achieved(state)
    for _ in range(3):
        enhance(state, payload, LATER)
    assert (unit(state)["exskill_lv"], stock(runtime, state, BROWN)) == (5, 0)
    assert rejection(lambda: enhance(state, payload, NOW)) == "ALREADY_MAX_LEVEL"


def test_locked_exskill_slots_cannot_be_enhanced(runtime, state):
    enhance = runtime.training.enhance_exskill
    give(runtime, state, BROWN, 3)
    payload = {"instance_id": WARRIOR, "slot_no": 2}
    assert rejection(lambda: enhance(state, payload, NOW)) == "HAS_INVALID_MATERIAL"
    unit(state)["flags"] = 1
    enhance(state, payload, LATER)
    assert (unit(state)["exskill_lv"], unit(state)["exskill_lv_2"]) == (1, 2)
    refused = rejection(lambda: enhance(state, {**payload, "slot_no": 3}, NOW))
    assert refused == "HAS_INVALID_MATERIAL"


def test_a_unique_weapon_is_forged_then_reinforced_by_chance(runtime, state):
    forge = runtime.training.forge
    first = runtime.masters.lookup("unique_weapon_data", "uw_id", 11001)
    payload = {"instance_id": WARRIOR, "items": None}
    assert rejection(lambda: forge(state, payload, NOW)) == "NOT_ENOUGH_MATERIAL"
    for cell in first["recipe"]:
        give(runtime, state, cell["recipe"], cell["count"])
    give(runtime, state, GOLD, first["cost_gold"])
    created = forge(state, payload, LATER)
    assert created.data == {"unit": unit(state), "enhance_status": True}
    assert unit(state)["uqw_lv"] == 1
    assert stock(runtime, state, GOLD) == 0
    assert {"unit", "item", "wallet"} <= set(created.sync)
    give(runtime, state, WHETSTONE, 3)
    give(runtime, state, FINE_WHETSTONE, 2)
    sure = forge(state, {**payload, "items": [{"id": 2, "quantity": 1}]}, LATER)
    assert sure.data["enhance_status"] is True and unit(state)["uqw_lv"] == 2
    runtime.training.rng = SimpleNamespace(random=lambda: 0.5)
    missed = forge(state, {**payload, "items": [{"id": 1, "quantity": 2}]}, LATER)
    assert missed.data["enhance_status"] is False and unit(state)["uqw_lv"] == 2
    assert stock(runtime, state, WHETSTONE) == 1
    runtime.training.rng = SimpleNamespace(random=lambda: 0.1)
    lucky = forge(state, {**payload, "items": [{"id": 1, "quantity": 1}]}, LATER)
    assert lucky.data["enhance_status"] is True and unit(state)["uqw_lv"] == 3


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"id": 9, "quantity": 1}],
        [{"id": 1, "quantity": 0}],
        [{"id": 2, "quantity": 2}],
        [{"id": 1, "quantity": 1}, {"id": 2, "quantity": 1}],
        [{"id": 1, "quantity": 5}],
    ],
)
def test_reinforcement_materials_must_make_a_valid_chance(runtime, state, items):
    unit(state)["uqw_lv"] = 1
    give(runtime, state, WHETSTONE, 4)
    give(runtime, state, FINE_WHETSTONE, 4)
    payload = {"instance_id": WARRIOR, "items": items}
    reason = rejection(lambda: runtime.training.forge(state, payload, NOW))
    assert reason == "NOT_ENOUGH_MATERIAL"
    assert unit(state)["uqw_lv"] == 1


def test_unique_weapons_stop_at_their_last_level(runtime, state):
    unit(state)["uqw_lv"] = 11
    give(runtime, state, FINE_WHETSTONE, 1)
    payload = {"instance_id": WARRIOR, "items": [{"id": 2, "quantity": 1}]}
    reason = rejection(lambda: runtime.training.forge(state, payload, NOW))
    assert reason == "ALREADY_MAX_LEVEL"


def test_protection_toggles_the_unit_lock(runtime, state):
    protect = runtime.training.protect
    payload = {"protect_instance_ids": [WARRIOR], "unprotect_instance_ids": []}
    result = protect(state, payload, LATER)
    assert unit(state)["is_protected"] is True
    assert synced(result, "unit") == [unit(state)]
    assert protect(state, payload, LATER).sync is None
    protect(
        state,
        {"protect_instance_ids": [FAIRY], "unprotect_instance_ids": [WARRIOR]},
        NOW,
    )
    assert (unit(state)["is_protected"], unit(state, FAIRY)["is_protected"]) == (
        False,
        True,
    )
    with pytest.raises(ApiNotFound):
        protect(
            state, {"protect_instance_ids": [99], "unprotect_instance_ids": []}, NOW
        )
    with pytest.raises(ValueError):
        protect(state, {"protect_instance_ids": 1, "unprotect_instance_ids": []}, NOW)


def test_discarding_returns_gold_and_worn_subskills(runtime, state):
    give(runtime, state, "unit.card.57")
    spare = state["units"][-1]
    spare["aa"]["s1"] = 1001
    result = runtime.training.discard(
        state, {"instance_ids": [spare["instance_id"]]}, LATER
    )
    assert spare not in state["units"]
    assert result.data == {
        "returns": {"received": [{"rid": GOLD, "val": 700}], "to_inbox": []}
    }
    assert stock(runtime, state, GOLD) == 700
    assert stock(runtime, state, "skill.attachable.1001") == 1


def test_units_in_use_cannot_be_discarded(runtime, state):
    discard = runtime.training.discard
    with pytest.raises(ValueError):
        discard(state, {"instance_ids": [WARRIOR]}, NOW)
    give(runtime, state, "unit.card.57")
    spare = state["units"][-1]["instance_id"]
    state["lending"]["s1"] = spare
    with pytest.raises(ValueError):
        discard(state, {"instance_ids": [spare]}, NOW)
    state["lending"]["s1"] = 0
    state["units"][-1]["is_protected"] = True
    with pytest.raises(ValueError):
        discard(state, {"instance_ids": [spare]}, NOW)
    with pytest.raises(ValueError):
        discard(state, {"instance_ids": []}, NOW)
    with pytest.raises(ApiNotFound):
        discard(state, {"instance_ids": [99]}, NOW)


def test_the_bom_summon_skill_rises_with_its_recipe(runtime, state):
    enhance = runtime.training.enhance_bom
    payload = {"instance_id": BOM}
    assert rejection(lambda: enhance(state, payload, NOW)) == "NOT_ENOUGH_MATERIAL"
    for rid, amount in (
        ("item.material.Bom2", 30),
        ("item.material.Red1", 10),
        ("item.material.Light1", 5),
    ):
        give(runtime, state, rid, amount)
    result = enhance(state, payload, LATER)
    bom = state["boms"][0]
    assert (bom["exskill_lv"], bom["passive_lv"], bom["updated_at"]) == (2, 1, LATER)
    assert result.data == {"boms": bom, "cost": {"rid": GOLD, "val": 0}}
    assert synced(result, "bom") == [bom]
    assert stock(runtime, state, "item.material.Bom2") == 0
    assert 70016 in achieved(state)
    bom["exskill_lv"] = 15
    assert rejection(lambda: enhance(state, payload, NOW)) == "ALREADY_MAX_LEVEL"
    with pytest.raises(ApiNotFound):
        enhance(state, {"instance_id": 9}, NOW)


def test_the_bom_passive_rises_with_its_recipe(runtime, state):
    enhance = runtime.training.enhance_bom_passive
    give(runtime, state, "item.material.Bom2", 50)
    give(runtime, state, "item.material.Bom3", 10)
    result = enhance(state, {"instance_id": BOM}, LATER)
    bom = state["boms"][0]
    assert (bom["exskill_lv"], bom["passive_lv"]) == (1, 2)
    assert result.data == {"bom": bom}
    assert {"bom", "item"} <= set(result.sync)
    bom["passive_lv"] = 5
    refused = rejection(lambda: enhance(state, {"instance_id": BOM}, NOW))
    assert refused == "ALREADY_MAX_LEVEL"
