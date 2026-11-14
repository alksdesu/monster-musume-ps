from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.context import Context
from monmusu_ps.core.errors import ApiRejected
from monmusu_ps.core.journal import TUTORIAL_PROGRESS, Journal
from monmusu_ps.core.sync import envelope
from monmusu_ps.core.tutorial import (
    COMPLETE,
    DECK,
    GACHA,
    LOGIN_BONUS,
    QUEST,
    ROUTE,
    START,
    Tutorial,
)
from monmusu_ps.features.tutorial import skip
from monmusu_ps.runtime import Runtime
from test_battle import closing, connect, enlist, opening


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
PROFILE = "tutorial-test"
CLIENT_PROGRESS = [0, 100, 200, 250, 300, 350, 500, 600, 700, 900, 9999]
STEPS = {
    DECK: {200: 250, 300: 350, 600: 700},
    GACHA: {500: 600},
    LOGIN_BONUS: {900: 9999},
}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: NOW, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    settings["tutorial_skip"]["random_rewards"] = False
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def tutorial(runtime):
    return Tutorial(runtime.masters, Journal(runtime.config))


async def perform_skip(runtime, payload=None):
    async with runtime.store.transaction(PROFILE) as state:
        result = skip(
            Context(
                runtime, {"_z0": 0} if payload is None else payload, state, None, NOW
            )
        )
    return result, await runtime.store.read(PROFILE)


def wallet(state, name):
    return next(
        row
        for row in state["wallets"]
        if row["category"] == "point" and row["name"] == name
    )


def at(progress):
    return {"player": {"tutorial": progress}, "events": []}


def test_route_stores_exactly_the_values_the_client_maps():
    assert [START, *(progress for progress, _ in ROUTE)] == CLIENT_PROGRESS
    assert COMPLETE == CLIENT_PROGRESS[-1]


def test_route_is_walked_in_the_recorded_order(tutorial):
    state = at(START)
    quests = iter(tutorial.quests())
    for progress, action in ROUTE:
        if action == QUEST:
            assert tutorial.complete_quest(state, next(quests), NOW)
        else:
            assert tutorial.complete_step(state, action, NOW)
        assert state["player"]["tutorial"] == progress
    assert next(quests, None) is None
    assert tutorial.finished(state)
    assert [
        (event["type"], event["at"], event["data"]) for event in state["events"]
    ] == [(TUTORIAL_PROGRESS, NOW, {"progress": progress}) for progress, _ in ROUTE]


@pytest.mark.parametrize("action", STEPS)
@pytest.mark.parametrize("progress", CLIENT_PROGRESS)
def test_step_completes_only_while_it_is_pending(tutorial, action, progress):
    state = at(progress)
    reached = STEPS[action].get(progress, progress)
    assert tutorial.complete_step(state, action, NOW) is (reached != progress)
    assert state["player"]["tutorial"] == reached
    assert len(state["events"]) == (reached != progress)


@pytest.mark.parametrize("action", [QUEST, "story"])
def test_only_named_steps_can_be_completed_directly(tutorial, action):
    with pytest.raises(ValueError, match="Unknown tutorial step"):
        tutorial.complete_step(at(START), action, NOW)


@pytest.mark.parametrize(
    "quest_id,progress", list(zip(range(10000, 10005), (100, 200, 300, 500, 900)))
)
def test_tutorial_quest_raises_the_progress_to_its_step(tutorial, quest_id, progress):
    state = at(START)
    assert tutorial.complete_quest(state, quest_id, NOW)
    assert state["player"]["tutorial"] == progress
    assert not tutorial.complete_quest(state, quest_id, NOW)
    assert state["player"]["tutorial"] == progress
    assert len(state["events"]) == 1


@pytest.mark.parametrize("progress", [700, COMPLETE])
def test_quest_completion_never_lowers_the_progress(tutorial, progress):
    state = at(progress)
    assert not tutorial.complete_quest(state, 10000, NOW)
    assert state["player"]["tutorial"] == progress
    assert state["events"] == []


def test_other_quests_leave_the_tutorial_untouched(tutorial):
    state = at(START)
    assert not tutorial.complete_quest(state, 10005, NOW)
    assert state["player"]["tutorial"] == START
    assert state["events"] == []


def test_finishing_announces_the_final_progress(tutorial):
    state = at(START)
    tutorial.finish(state, NOW)
    assert tutorial.finished(state)
    assert state["events"][-1]["data"] == {"progress": COMPLETE}


async def test_new_players_have_not_started_the_tutorial(runtime):
    state = await runtime.store.read(PROFILE)
    assert not runtime.tutorial.started(state)
    assert not runtime.tutorial.finished(state)


async def test_skip_replays_the_complete_tutorial_route(runtime):
    result, state = await perform_skip(runtime)
    assert state["player"]["tutorial"] == 9999
    assert [
        (row["quest_id"], row["record_rating"], row["wins"], row["tries"])
        for row in state["quest_records"]
    ] == [(quest, 3, 1, 1) for quest in range(10000, 10005)]
    assert len(state["quest_transactions"]) == 5
    assert sorted(state["team_logs"]) == [
        f"quest.{quest}#{state['player']['player_code']}"
        for quest in range(10000, 10005)
    ]
    units = {row["card_id"]: row for row in state["units"]}
    assert {card: row["t_exp"] for card, row in units.items()} == {
        11: 200,
        22: 200,
        6: 120,
        57: 80,
        23: 40,
    }
    assert {card: row["r_unit_lv"] for card, row in units.items()} == {
        11: 4,
        22: 4,
        6: 3,
        57: 2,
        23: 2,
    }
    assert state["modules"]["experience"][0]["exp"] == 800
    assert state["modules"]["experience"][0]["level"] == 2
    assert state["modules"]["stamina"][0]["amount"] == 104
    assert wallet(state, "gold")["val"] == 1100
    assert wallet(state, "free_coin")["val"] == 100
    assert wallet(state, "paid_coin")["val"] == 0
    deck = next(
        row
        for row in state["compositions"]
        if row["section"] == "unit_deck" and row["no"] == 1
    )
    slots = [json.loads(value) for value in deck["slots"]]
    cards = {row["instance_id"]: row["card_id"] for row in state["units"]}
    assert [(slot["sn"], cards[slot["iid"]]) for slot in slots if slot["t"] == 1] == [
        (0, 11),
        (1, 22),
        (2, 6),
        (3, 57),
        (4, 23),
    ]
    assert [slot for slot in slots if slot["t"] == 2] == [{"t": 2, "sn": 0, "iid": 1}]
    assert all(row["slots"] == [] for row in state["compositions"] if row["no"] != 1)
    assert state["limits"] == [
        {"id": "gacha.normal.tutorial_L", "times": 1, "counted_at": NOW}
    ]
    events = [
        event
        for event in state["events"]
        if event["type"] in ("quest_complete", "gacha_draw")
    ]
    assert [
        event["data"].get("quest_id", event["data"].get("gacha_id")) for event in events
    ] == [10000, 10001, 10002, 10003, "gacha.normal.tutorial_L", 10004]
    gacha = next(event for event in events if event["type"] == "gacha_draw")
    assert gacha["data"]["costs"] == [{"rid": "wallet.point.free_coin", "val": 300}]
    assert gacha["data"]["rewards"] == [{"rid": "unit.card.23", "val": 1}]
    assert state["stories"] == [{"id": "story.4.6.1000601", "is_already_read": False}]
    assert [row["id"] for row in state["periods"]] == [
        *(f"sales.{shop_id}" for shop_id in (511001, 521001, 591001)),
        *(f"sdpanel_mission.{day}" for day in range(1, 8)),
        "gacha.sd_gacha",
        *(f"sales.{shop_id}" for shop_id in range(500001, 500011)),
        *(f"sales.{shop_id}" for shop_id in range(501001, 501010)),
    ]
    assert state["inbox"] == []
    assert state["items"] == []
    body = envelope(result, NOW)
    assert "data" not in body
    assert body["status"]["code"] == 1
    assert {
        "player",
        "module_prop",
        "unit",
        "composition",
        "wallet",
        "quest_record",
        "limit",
        "story_record",
        "period",
    } <= result.sync.keys()
    for scopes in result.sync.values():
        assert len(scopes) == 1
        assert scopes[0]["scope"] == f"player@{state['player']['player_code']}"
    assert len(result.sync["unit"][0]["data"]) == 5
    assert result.sync["module_prop"][0]["data"]["experience"][0]["exp"] == 800


async def test_completed_skip_is_idempotent(runtime):
    await perform_skip(runtime)
    before = runtime.store.path(PROFILE).read_bytes()
    result, _ = await perform_skip(runtime)
    assert runtime.store.path(PROFILE).read_bytes() == before
    assert result.sync is None
    assert not result.include_data


@pytest.mark.parametrize("progress", [1, 100, 900])
async def test_skip_rejects_a_started_tutorial_without_changes(runtime, progress):
    async with runtime.store.transaction(PROFILE) as state:
        state["player"]["tutorial"] = progress
    before = runtime.store.path(PROFILE).read_bytes()
    with pytest.raises(ValueError, match="before it starts"):
        await perform_skip(runtime)
    assert runtime.store.path(PROFILE).read_bytes() == before


async def test_stamina_policy_is_internal_configuration(runtime):
    _, state = await perform_skip(
        runtime, {"_z0": 0, "costs_enabled": False, "random_rewards": False}
    )
    assert state["modules"]["stamina"][0]["amount"] == 104


async def test_skip_can_disable_stamina_costs(runtime):
    runtime.config.read()["tutorial_skip"]["consume_stamina"] = False
    _, state = await perform_skip(runtime)
    assert state["modules"]["stamina"][0]["amount"] == 144
    assert wallet(state, "free_coin")["val"] == 100


async def test_rating_policy_controls_gold_and_first_three_star_reward(runtime):
    runtime.config.read()["tutorial_skip"]["rating"] = 1
    _, state = await perform_skip(runtime)
    assert wallet(state, "gold")["val"] == 900
    assert wallet(state, "free_coin")["val"] == 0
    assert all(row["record_rating"] == 1 for row in state["quest_records"])
    assert state["player"]["tutorial"] == 9999


async def test_random_reward_policy_uses_the_battle_reward_path(runtime):
    runtime.config.read()["tutorial_skip"]["random_rewards"] = True
    result, state = await perform_skip(runtime)
    assert state["items"]
    assert result.sync["item"][0]["data"]
    assert state["player"]["tutorial"] == 9999


async def test_gacha_cost_comes_from_the_tutorial_pool(runtime):
    group = runtime.masters.lookup("gacha_data", "id", "gacha.normal.tutorial_L")
    group["enclosure"][0]["cost_num"] = 200
    _, state = await perform_skip(runtime)
    assert wallet(state, "free_coin")["val"] == 200
    gacha = next(event for event in state["events"] if event["type"] == "gacha_draw")
    assert gacha["data"]["costs"] == [{"rid": "wallet.point.free_coin", "val": 200}]


async def test_skip_rolls_back_when_the_gacha_unit_cannot_join(runtime):
    runtime.config.read()["resources"]["unit_capacity"] = 4
    await runtime.store.read(PROFILE)
    before = runtime.store.path(PROFILE).read_bytes()
    with pytest.raises(ValueError, match="has not joined the roster"):
        await perform_skip(runtime)
    assert runtime.store.path(PROFILE).read_bytes() == before
    state = await runtime.store.read(PROFILE)
    assert state["player"]["tutorial"] == 0
    assert state["quest_transactions"] == {}
    assert wallet(state, "free_coin")["val"] == 300


async def test_insufficient_gacha_currency_keeps_the_original_save(runtime):
    async with runtime.store.transaction(PROFILE) as state:
        wallet(state, "free_coin")["val"] = 0
    before = runtime.store.path(PROFILE).read_bytes()
    with pytest.raises(ApiRejected) as caught:
        await perform_skip(runtime)
    assert caught.value.reason == "NOT_ENOUGH_COST"
    assert runtime.store.path(PROFILE).read_bytes() == before


async def test_existing_tutorial_gacha_limit_prevents_another_draw(runtime):
    async with runtime.store.transaction(PROFILE) as state:
        state["limits"].append(
            {"id": "gacha.normal.tutorial_L", "times": 1, "counted_at": NOW - 1}
        )
    before = runtime.store.path(PROFILE).read_bytes()
    with pytest.raises(ApiRejected) as caught:
        await perform_skip(runtime)
    assert caught.value.reason == "LIMIT_OVER_OF_GACHA_TRIES"
    assert runtime.store.path(PROFILE).read_bytes() == before


async def test_native_tutorial_closes_in_the_recorded_order(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    runtime.config.read()["comeback"]["with_login_bonus"] = False

    async def clear(quest_id):
        started = await client.call("quest/start", opening(quest_id))
        return await client.call("quest/end", closing(started["data"]["txid"]))

    async def deploy(card_id):
        saved = await runtime.store.read(profile)
        enlist(saved, card_id)
        return await client.call(
            "composition/update", {"composition": saved["compositions"][0]}
        )

    def progress(response):
        return response["sync"]["player"][0]["data"][0]["tutorial"]

    try:
        for quest_id in range(10000, 10004):
            ended = await clear(quest_id)
            for reward in ended["data"]["report"].get("initial_rewards", []):
                await deploy(int(reward["rid"].removeprefix("unit.card.")))
        drawn = await client.call(
            "gacha/draw",
            {
                "gacha_id": runtime.tutorial.gacha(),
                "exec_num": 1,
                "interruption": False,
            },
        )
        assert progress(drawn) == 600
        assert progress(await deploy(23)) == 700
        ended = await clear(10004)
        assert progress(ended) == 900
        assert [row["id"] for row in ended["sync"]["period"][0]["data"]] == [
            "sales.511001",
            "sales.521001",
            "sales.591001",
        ]
        slime = {"id": "story.4.6.1000601", "is_already_read": False}
        listed = await client.call("story/list", {"story_category": 4, "per_view": 100})
        assert listed["data"]["items"] == [slime]
        read = await client.call(
            "story/read", {"story_category": 4, "story_id": 6, "id": 1000601}
        )
        assert read["sync"]["story_record"][0]["data"] == [
            {**slime, "is_already_read": True}
        ]
        stamped = await client.call("loginbonus/get", {"_z0": 0})
        assert progress(stamped) == COMPLETE
        opened = [row["id"] for row in stamped["sync"]["period"][0]["data"]]
        assert opened[:8] == [
            *(f"sdpanel_mission.{day}" for day in range(1, 8)),
            "gacha.sd_gacha",
        ]
        assert len(opened) == 27
        panels = await client.call(
            "period/list/seq_start",
            {"range_names": ["sdpanel_mission"], "per_view": 100},
        )
        assert [row["id"] for row in panels["data"]["items"]] == opened[:7]
        saved = await runtime.store.read(profile)
        assert saved["player"]["tutorial"] == COMPLETE
        assert len(saved["periods"]) == 30
    finally:
        await http.close()
