from copy import deepcopy
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.runtime import Runtime
from test_battle import connect


ROOT = Path(__file__).resolve().parents[1]
CREATED = 1_794_326_425
NOW = 1_794_332_713
HOUR = 3600
TIERS = [(0, 1), (10207, 2), (10507, 3), (10707, 4), (11007, 5), (11307, 6)]


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
    return create_player(
        "ranch", "RANCH001", runtime.masters, runtime.config.read(), CREATED
    )


def wallet(state, category, name):
    return next(
        row
        for row in state["wallets"]
        if row["category"] == category and row["name"] == name
    )


def harvest(runtime, state, now=NOW):
    return runtime.ranch.harvest(
        state, {"section": "basic", "assumed_duration": 0}, now
    )


def delivered(rid, amount, **changes):
    return {"rid": rid, "val": amount, "inc": amount, "inbox": 0, "over": 0, **changes}


def clear(state, quest_id):
    state["quest_records"].append({"quest_id": quest_id, "wins": 1})


def test_harvest_matches_the_official_sample(runtime, state):
    wallet(state, "point", "gold")["val"] = 1076
    result = harvest(runtime, state)
    assert result.data == {
        "rewards": [
            delivered("wallet.point.gold", 1560),
            delivered("wallet.unit_exp.sr_all", 2),
        ]
    }
    assert set(result.sync) == {"module_prop", "wallet"}
    assert result.sync["module_prop"][0]["data"] == {
        "farming": [
            {
                "section": "basic",
                "tier": 1,
                "interval_min": 1,
                "term_min": 1800,
                "start_at": NOW,
            }
        ]
    }
    assert wallet(state, "point", "gold")["val"] == 2636
    milk = wallet(state, "unit_exp", "sr_all")
    assert (milk["val"], milk["created_at"]) == (2, NOW)
    assert {
        (row["category"], row["name"]) for row in result.sync["wallet"][0]["data"]
    } == {
        ("point", "gold"),
        ("unit_exp", "sr_all"),
    }


def test_growth_stops_at_the_term(runtime, state):
    result = harvest(runtime, state, CREATED + 30 * HOUR + 59)
    capped = harvest(
        runtime,
        create_player(
            "ranch", "RANCH002", runtime.masters, runtime.config.read(), CREATED
        ),
        CREATED + 300 * HOUR,
    )
    assert result.data == capped.data
    assert result.data["rewards"] == [
        delivered("wallet.point.gold", 27000),
        delivered("wallet.unit_exp.ssr_all", 3),
        delivered("wallet.unit_exp.sr_all", 6),
    ]


@pytest.mark.parametrize("elapsed", [-100, 0, 59])
def test_nothing_is_harvested_within_the_first_interval(runtime, state, elapsed):
    before = deepcopy(state)
    result = harvest(runtime, state, CREATED + elapsed)
    assert result.data == {"rewards": []}
    assert result.sync is None
    assert state == before


def test_seconds_past_the_last_interval_are_dropped(runtime, state):
    harvest(runtime, state, CREATED + 119)
    assert state["modules"]["farming"][0]["start_at"] == CREATED + 119
    assert wallet(state, "point", "gold")["val"] == 515
    assert harvest(runtime, state, CREATED + 178).data == {"rewards": []}
    assert harvest(runtime, state, CREATED + 179).data["rewards"] == [
        delivered("wallet.point.gold", 15)
    ]


@pytest.mark.parametrize(("quest_id", "tier"), TIERS)
def test_tier_follows_the_cleared_main_quests(runtime, state, quest_id, tier):
    if quest_id:
        clear(state, quest_id)
    row = runtime.masters.lookup("ranch_reward_table", "id", tier)
    result = harvest(runtime, state, CREATED + 60)
    assert result.data["rewards"][0] == delivered(
        "wallet.point.gold", row["reward_gold"]
    )
    assert state["modules"]["farming"][0]["tier"] == tier


def test_unfinished_attempts_do_not_raise_the_tier(runtime, state):
    state["quest_records"].append({"quest_id": 10207, "wins": 0})
    harvest(runtime, state, CREATED + 60)
    assert state["modules"]["farming"][0]["tier"] == 1


def test_experience_is_paid_in_the_largest_bottles(runtime, state):
    clear(state, 11307)
    result = harvest(runtime, state, CREATED + 30 * HOUR)
    assert result.data["rewards"] == [
        delivered("wallet.point.gold", 48600),
        delivered("wallet.unit_exp.lr_all", 1),
        delivered("wallet.unit_exp.ssr_all", 5),
        delivered("wallet.unit_exp.sr_all", 1),
        delivered("wallet.unit_exp.r_all", 2),
    ]


def test_experience_below_the_smallest_bottle_is_dropped(runtime, state):
    assert harvest(runtime, state, CREATED + 4 * 60).data["rewards"] == [
        delivered("wallet.point.gold", 60)
    ]
    assert harvest(runtime, state, CREATED + 4 * 60 + 5 * 60).data["rewards"] == [
        delivered("wallet.point.gold", 75),
        delivered("wallet.unit_exp.r_all", 1),
    ]


def test_amounts_over_the_holding_cap_are_discarded(runtime, state):
    wallet(state, "point", "gold")["val"] = 99_999_990
    result = harvest(runtime, state, CREATED + 60)
    assert result.data["rewards"] == [delivered("wallet.point.gold", 15, inc=9, over=6)]
    assert wallet(state, "point", "gold")["val"] == 99_999_999
    assert state["inbox"] == []
    assert state["modules"]["farming"][0]["start_at"] == CREATED + 60


def test_unknown_sections_are_rejected(runtime, state):
    before = deepcopy(state)
    with pytest.raises(ValueError, match="Unknown farming section"):
        runtime.ranch.harvest(state, {"section": "event", "assumed_duration": 60}, NOW)
    assert state == before


async def test_native_harvest_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        assert "farming/yield" in runtime.registry.handlers
        async with runtime.store.transaction(profile) as state:
            state["modules"]["farming"][0]["start_at"] -= 10 * 60
        payload = {"section": "basic", "assumed_duration": 600}
        harvested = await client.call("farming/yield", payload)
        assert harvested["data"]["rewards"] == [
            delivered("wallet.point.gold", 150),
            delivered("wallet.unit_exp.r_all", 2),
        ]
        assert set(harvested["sync"]) == {"module_prop", "wallet"}
        again = await client.call("farming/yield", payload)
        assert again["data"] == {"rewards": []}
        assert again["sync"] is None
        player = await client.call("player/get", {"player_module_ids": ["farming"]})
        saved = await runtime.store.read(profile)
        assert player["data"]["modules"]["farming"] == saved["modules"]["farming"]
    finally:
        await http.close()
