from copy import deepcopy
import json
from pathlib import Path

import pytest

from monmusu_ps.config import RuntimeConfig
from monmusu_ps.core.initial import create_player, unit
from monmusu_ps.core.journal import Journal
from monmusu_ps.core.masterdata import MasterData
from monmusu_ps.core.progression import Progression


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000


@pytest.fixture
def progression(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes((ROOT / "server/data/settings.json").read_bytes())
    config = RuntimeConfig(path)
    return Progression(MasterData(ROOT / "masterdata"), config, Journal(config))


@pytest.fixture
def state(progression):
    return create_player(
        "progression", "TEST0001", progression.masters, progression.config.read(), NOW
    )


def test_tutorial_experience_follows_the_order_units_join(progression, state):
    for index in range(5):
        quest = progression.masters.lookup("quest_data", "id", 10000 + index)
        stamina = state["modules"]["stamina"][0]
        stamina["amount"] -= progression.stamina_cost(state, quest)[0]["val"]
        progression.add_unit_exp(
            state, [row["instance_id"] for row in state["units"]], 40, NOW
        )
        progression.add_player_exp(state, 160, NOW)
        if index in (1, 2, 3):
            card_id = {1: 6, 2: 57, 3: 23}[index]
            state["units"].append(unit(len(state["units"]) + 1, card_id, NOW + index))

    assert state["modules"]["experience"] == [
        {"section": "player_rank", "exp": 800, "level": 2, "level_cap": 800}
    ]
    assert state["modules"]["stamina"][0]["capacity"] == 74
    assert state["modules"]["stamina"][0]["amount"] == 104
    assert [
        (row["card_id"], row["t_exp"], row["r_unit_lv"]) for row in state["units"]
    ] == [(11, 200, 4), (22, 200, 4), (6, 120, 3), (57, 80, 2), (23, 40, 2)]
    assert all(row["updated_at"] == row["created_at"] for row in state["units"])


def test_captured_rank_up_recovers_elapsed_stamina_before_the_refill(
    progression, state
):
    experience = state["modules"]["experience"][0]
    experience["exp"] = 480
    stamina = state["modules"]["stamina"][0]
    stamina["amount"] = 57
    progression.recover_stamina(state, NOW + 1008)
    assert stamina["amount"] == 60
    stamina["amount"] -= 8

    updates = progression.add_player_exp(state, 160, NOW + 1008)

    assert experience["exp"] == 640
    assert experience["level"] == 2
    assert stamina["amount"] == 126
    assert stamina["capacity"] == 74
    assert stamina["latest_recovered_at"] == NOW + 900
    assert updates == {
        "module_prop": {"experience": [experience], "stamina": [stamina]}
    }


def test_multiple_rank_ups_match_separate_rewards(progression, state):
    sequential = deepcopy(state)
    progression.add_player_exp(state, 3100, NOW)
    for amount in (600, 1000, 1500):
        progression.add_player_exp(sequential, amount, NOW)
    assert state == sequential
    assert state["modules"]["experience"][0]["level"] == 4
    assert state["modules"]["stamina"][0]["amount"] == 70 + 74 + 78 + 82


def test_player_experience_and_refills_stop_at_their_caps(progression, state):
    updates = progression.add_player_exp(state, 1_000_000_000, NOW)
    assert updates["module_prop"]["experience"][0]["exp"] == 26_043_600
    assert state["modules"]["experience"][0]["level"] == 800
    assert state["modules"]["stamina"][0]["capacity"] == 486
    assert state["modules"]["stamina"][0]["amount"] == 999
    assert progression.add_player_exp(state, 160, NOW) == {}


def test_player_uses_the_saved_level_cap(progression, state):
    state["modules"]["experience"][0]["level_cap"] = 3
    progression.add_player_exp(state, 3100, NOW)
    assert state["modules"]["experience"][0]["exp"] == 1600
    assert state["modules"]["experience"][0]["level"] == 3


@pytest.mark.parametrize(
    "card_id,cap_exp", [(11, 15558), (22, 17114), (23, 18670), (26, 20225)]
)
def test_unit_rarity_tables_and_class_caps(progression, state, card_id, cap_exp):
    state["units"] = [unit(1, card_id, NOW)]
    changed = progression.add_unit_exp(state, [1], 10_000_000, NOW + 100)
    assert changed["unit"][0]["t_exp"] == cap_exp
    assert changed["unit"][0]["r_unit_lv"] == 30
    assert changed["unit"][0]["updated_at"] == NOW
    assert progression.add_unit_exp(state, [1], 40, NOW + 200) == {}


@pytest.mark.parametrize("rank", [4, 5, 15])
def test_rank_table_owns_the_maximum_level(progression, state, rank):
    state["units"] = [unit(1, 11, NOW)]
    state["units"][0]["rank"] = rank
    progression.add_unit_exp(state, [1], 10_000_000, NOW)
    assert state["units"][0]["r_unit_lv"] == 150
    assert state["units"][0]["t_exp"] == 1_696_939


def test_experience_threshold_is_inclusive_and_instances_are_unique(progression, state):
    progression.add_unit_exp(state, [1, 1], 29, NOW)
    assert state["units"][0]["r_unit_lv"] == 1
    assert state["units"][0]["t_exp"] == 29
    assert state["units"][1]["t_exp"] == 0
    progression.add_unit_exp(state, [1], 1, NOW)
    assert state["units"][0]["r_unit_lv"] == 2


def test_invalid_experience_inputs_do_not_change_state(progression, state):
    before = deepcopy(state)
    with pytest.raises(ValueError, match="negative"):
        progression.add_player_exp(state, -1, NOW + 300)
    with pytest.raises(ValueError, match="negative"):
        progression.add_unit_exp(state, [1], -1, NOW)
    with pytest.raises(KeyError):
        progression.add_unit_exp(state, [1, 999], 40, NOW)
    assert state == before


def test_stamina_preserves_partial_intervals_and_caps_recovery(progression, state):
    stamina = state["modules"]["stamina"][0]
    stamina["amount"] = 67
    assert progression.recover_stamina(state, NOW + 299) == {}
    assert progression.recover_stamina(state, NOW - 1) == {}
    progression.recover_stamina(state, NOW + 650)
    assert (stamina["amount"], stamina["latest_recovered_at"]) == (69, NOW + 600)
    progression.recover_stamina(state, NOW + 950)
    assert (stamina["amount"], stamina["latest_recovered_at"]) == (70, NOW + 900)
    progression.recover_stamina(state, NOW + 1000)
    assert stamina["latest_recovered_at"] == NOW + 1000
    stamina["amount"] -= 1
    assert progression.recover_stamina(state, NOW + 1299) == {}
    progression.recover_stamina(state, NOW + 1300)
    assert stamina["amount"] == 70


def test_stamina_above_capacity_keeps_amount_and_resets_the_clock(progression, state):
    stamina = state["modules"]["stamina"][0]
    stamina["amount"] = 126
    updates = progression.recover_stamina(state, NOW + 1000)
    assert stamina["amount"] == 126
    assert stamina["latest_recovered_at"] == NOW + 1000
    assert updates == {"module_prop": {"stamina": [stamina]}}
    assert progression.recover_stamina(state, NOW + 1000) == {}


def test_live_stamina_limit_is_used_for_rank_refills(progression, state):
    config = progression.config.read().copy()
    config["stamina_max_capacity"] = 100
    progression.config.path.write_text(json.dumps(config), encoding="utf-8")
    progression.add_player_exp(state, 600, NOW)
    stamina = state["modules"]["stamina"][0]
    assert stamina["max_capacity"] == 100
    assert stamina["amount"] == 100


def test_quest_stamina_uses_mana_and_first_clear_discount(
    progression, state, monkeypatch
):
    quest = progression.masters.lookup("quest_data", "id", 10003)
    assert quest["sortieCost"] == 10
    assert progression.stamina_cost(state, quest) == [
        {"rid": "stamina.quest", "val": 8}
    ]
    original = progression.masters.property
    monkeypatch.setattr(
        progression.masters,
        "property",
        lambda kind, key: 50 if (kind, key) == ("Int", 17) else original(kind, key),
    )
    assert progression.stamina_cost(state, quest) == [
        {"rid": "stamina.quest", "val": 4}
    ]
    state["quest_records"] = [{"quest_id": 10003, "wins": 0, "tries": 2}]
    assert progression.stamina_cost(state, quest)[0]["val"] == 4
    state["quest_records"][0]["wins"] = 1
    assert progression.stamina_cost(state, quest)[0]["val"] == 8
    assert progression.stamina_cost(state, dict(quest, mana=0)) == []


@pytest.mark.parametrize(
    "quest_id,name",
    [(55000, "normal"), (56000, "premium"), (30901, "power"), (57000, "ancient")],
)
def test_quest_keys_replace_stamina(progression, state, quest_id, name):
    quest = progression.masters.lookup("quest_data", "id", quest_id)
    assert progression.stamina_cost(state, quest) == [
        {"rid": f"wallet.quest_key.{name}", "val": 1}
    ]
