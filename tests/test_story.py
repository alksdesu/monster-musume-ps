from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.context import Context
from monmusu_ps.core.errors import ApiRejected
from monmusu_ps.core.initial import create_player, unit
from monmusu_ps.core.journal import QUEST_COMPLETE
from monmusu_ps.features.story import ROUTES
from monmusu_ps.runtime import Runtime
from test_battle import clear, closing, connect, opening, play_route, post


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
SLIME = {"story_category": 4, "story_id": 6, "id": 1000601}
SLIME_RECORD = "story.4.6.1000601"
COSTUME = {"story_category": 5, "story_id": 6, "id": 1000605}
GROUP = {"story_category": 11, "story_id": 3, "id": 3}
SIDE = {"story_category": 3, "story_id": 6, "id": 1}


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
        "story", "STORY001", runtime.masters, runtime.config.read(), NOW
    )


@pytest.fixture
def context(runtime, state):
    return Context(runtime, {}, state, runtime.sessions.create("story-test"), NOW)


def call(ctx, endpoint, **payload):
    ctx.payload = payload
    return ROUTES[endpoint](ctx)


def unread(identity):
    return {"id": identity, "is_already_read": False}


def finished(identity):
    return {"id": identity, "is_already_read": True}


def coins(state):
    return next(
        row
        for row in state["wallets"]
        if row["category"] == "point" and row["name"] == "free_coin"
    )


def recruit(state, card_id, level=1):
    row = unit(state["resource_sequences"]["unit"], card_id, NOW)
    row["r_unit_lv"] = level
    state["resource_sequences"]["unit"] += 1
    state["units"].append(row)


def rejection(runtime, state, payload):
    before = deepcopy(state)
    with pytest.raises(ApiRejected) as caught:
        runtime.stories.read(state, payload, NOW)
    assert state == before
    return caught.value.reason


def announce(runtime, state, quest_id, first_clear=True):
    quest = runtime.masters.lookup("quest_data", "id", quest_id)
    runtime.journal.record(
        state,
        "questexec-test",
        QUEST_COMPLETE,
        NOW,
        {
            "quest_id": quest_id,
            "category": quest["category"],
            "section": quest["chapter"],
            "rating": 3,
            "result": "COMPLETE",
            "first_clear": first_clear,
            "costs": [],
            "kills": 0,
            "unit_ids": [],
            "support_owner": None,
            "support_card": None,
        },
    )


def test_reading_the_tutorial_story_matches_the_official_sample(runtime, state):
    state["stories"].append(unread(SLIME_RECORD))
    coins(state)["val"] = 100
    result = runtime.stories.read(state, SLIME, NOW)
    assert result.data is None
    assert set(result.sync) == {"story_record", "wallet"}
    assert result.sync["story_record"][0]["data"] == [finished(SLIME_RECORD)]
    assert [
        (row["category"], row["name"], row["val"])
        for row in result.sync["wallet"][0]["data"]
    ] == [("point", "free_coin", 150)]
    assert state["stories"] == [finished(SLIME_RECORD)]


def test_a_story_is_rewarded_only_once(runtime, state):
    state["stories"].append(unread(SLIME_RECORD))
    runtime.stories.read(state, SLIME, NOW)
    assert rejection(runtime, state, SLIME) == "IS_ALREADY_READ"
    assert coins(state)["val"] == 350


@pytest.mark.parametrize("level", [None, 1, 14])
def test_unit_stories_stay_closed_below_the_unlock_level(runtime, state, level):
    if level is not None:
        recruit(state, 6, level)
    assert rejection(runtime, state, SLIME) == "HAS_INVALID_STORY_ID"


@pytest.mark.parametrize("level", [15, 60])
def test_unit_stories_open_at_the_unlock_level(runtime, state, level):
    recruit(state, 6, level)
    result = runtime.stories.read(state, SLIME, NOW)
    assert set(result.sync) == {"story_record", "wallet"}
    assert state["stories"] == [finished(SLIME_RECORD)]
    assert coins(state)["val"] == 350


def test_stories_without_rewards_only_mark_the_record(runtime, state):
    recruit(state, 6)
    result = runtime.stories.read(state, {**SLIME, "id": 1000602}, NOW)
    assert set(result.sync) == {"story_record"}
    assert state["stories"] == [finished("story.4.6.1000602")]


def test_costume_stories_need_the_costume(runtime, state):
    recruit(state, 6)
    assert rejection(runtime, state, COSTUME) == "HAS_INVALID_STORY_ID"
    state["items"].append({"id": "item.unit_skin.6_1", "val": 1})
    runtime.stories.read(state, COSTUME, NOW)
    assert state["stories"] == [finished("story.5.6.1000605")]
    assert coins(state)["val"] == 350


def test_group_stories_need_every_trained_member_and_the_quest(runtime, state):
    assert rejection(runtime, state, GROUP) == "HAS_INVALID_STORY_ID"
    for row in state["units"]:
        row["r_unit_lv"] = 15
    assert rejection(runtime, state, GROUP) == "HAS_INVALID_STORY_ID"
    state["quest_records"].append({"quest_id": 10100, "wins": 0})
    assert rejection(runtime, state, GROUP) == "HAS_INVALID_STORY_ID"
    state["quest_records"][0]["wins"] = 1
    state["units"][0]["r_unit_lv"] = 14
    assert rejection(runtime, state, GROUP) == "HAS_INVALID_STORY_ID"
    state["units"][0]["r_unit_lv"] = 15
    runtime.stories.read(state, GROUP, NOW)
    assert state["stories"] == [finished("story.11.3.3")]
    assert coins(state)["val"] == 350


@pytest.mark.parametrize("category", [0, 7, 12, -1, "4", True, None])
def test_unknown_categories_are_rejected(runtime, state, category):
    payload = {**SLIME, "story_category": category}
    assert rejection(runtime, state, payload) == "HAS_INVALID_CATEGORY_ID"
    with pytest.raises(ApiRejected) as caught:
        runtime.stories.records(state, category)
    assert caught.value.reason == "HAS_INVALID_CATEGORY_ID"


@pytest.mark.parametrize(
    "payload",
    [
        {"story_category": 4, "story_id": 6, "id": 999},
        {"story_category": 4, "story_id": 7, "id": 1000601},
        {"story_category": 5, "story_id": 6, "id": 1000601},
        {"story_category": 4, "story_id": "6", "id": 1000601},
        {"story_category": 4, "story_id": 6, "id": "1000601"},
        {"story_category": 1, "story_id": 10000, "id": 1},
        {"story_category": 2, "story_id": 1, "id": 1},
        SIDE,
    ],
)
def test_mismatched_or_locked_stories_are_rejected(runtime, state, payload):
    recruit(state, 6, 60)
    assert rejection(runtime, state, payload) == "HAS_INVALID_STORY_ID"


def test_unlocked_side_stories_are_read_without_rewards(runtime, state):
    state["stories"].append(unread("story.3.6.1"))
    result = runtime.stories.read(state, SIDE, NOW)
    assert set(result.sync) == {"story_record"}
    assert state["stories"] == [finished("story.3.6.1")]


def episode(identity):
    return {"story_category": 6, "story_id": identity, "id": identity}


def test_event_episodes_open_with_their_raid(runtime, state):
    runtime.stories.read(state, episode(5), NOW)
    assert finished("story.6.5.5") in state["stories"]
    assert rejection(runtime, state, episode(11)) == "HAS_INVALID_STORY_ID"
    runtime.stories.read(state, episode(10), NOW)
    assert rejection(runtime, state, episode(11)) == "HAS_INVALID_STORY_ID"
    runtime.config.read()["subdue_raid"]["everyone"]["base"] = 600_000_000
    runtime.stories.read(state, episode(11), NOW)
    assert finished("story.6.11.11") in state["stories"]
    assert rejection(runtime, state, episode(11)) == "IS_ALREADY_READ"


def test_event_episodes_wait_for_their_release(runtime, state):
    runtime.subdue_raids.episode(5)["releaseData"] = "2999/01/01 00:00:00"
    assert rejection(runtime, state, episode(5)) == "HAS_INVALID_STORY_ID"
    groups = runtime.masters.table("everyones_killevent_episode")
    groups[0]["episodeData"] = [
        row for row in groups[0]["episodeData"] if row["story_id"] != 6
    ]
    assert rejection(runtime, state, episode(6)) == "HAS_INVALID_STORY_ID"


def test_rewards_over_the_holding_cap_reject_the_read(runtime, state):
    state["stories"].append(unread(SLIME_RECORD))
    coins(state)["val"] = 999_999
    assert rejection(runtime, state, SLIME) == "ASSET_CAPACITY_OVERFLOW"


def test_unreadable_rewards_reject_the_read(runtime, state):
    state["stories"].append(unread(SLIME_RECORD))
    row = next(
        row for row in runtime.masters.table("story_data_unit") if row["id"] == 1000601
    )
    row["rewardtable"]["m_rewards"][0]["m_walletName"] = "missing"
    assert rejection(runtime, state, SLIME) == "REWARD_DATA_ERROR"


def test_third_tutorial_quest_unlocks_its_rewarded_story(runtime, state):
    results = play_route(runtime, state, (3, 3, 3))
    assert state["stories"] == [unread(SLIME_RECORD)]
    assert "story_record" not in results[1].sync
    assert results[2].sync["story_record"][0]["data"] == [unread(SLIME_RECORD)]


def test_replaying_the_quest_keeps_the_record(runtime, state):
    play_route(runtime, state, (3, 3, 3))
    runtime.stories.read(state, SLIME, NOW)
    replayed = clear(runtime, state, 10002)
    assert "story_record" not in replayed.sync
    assert state["stories"] == [finished(SLIME_RECORD)]


def test_failed_attempts_unlock_nothing(runtime, state):
    play_route(runtime, state, (3, 3))
    started = runtime.battles.start(state, opening(10002), NOW)
    runtime.battles.finish(
        state, closing(started.data["txid"], result="FAILURE", rating=0), NOW
    )
    assert state["stories"] == []


@pytest.mark.parametrize(
    ("quest_id", "record"), [(10006, "story.3.6.1"), (56000, "story.3.25.20001")]
)
def test_first_clears_unlock_the_side_stories_bound_to_the_quest(
    runtime, state, quest_id, record
):
    announce(runtime, state, quest_id)
    assert state["stories"] == [unread(record)]
    announce(runtime, state, quest_id)
    assert state["stories"] == [unread(record)]


def test_repeat_clears_unlock_nothing(runtime, state):
    announce(runtime, state, 10006, first_clear=False)
    assert state["stories"] == []


def test_story_list_filters_by_category_and_continues(context):
    selected = [unread(SLIME_RECORD), finished("story.4.6.1000603")]
    context.state["stories"] = [
        selected[0],
        finished("story.40.6.1"),
        unread("story.5.6.1000605"),
        selected[1],
    ]
    first = call(context, "story/list", story_category=4, per_view=1).data
    assert first["items"] == selected[:1]
    second = call(
        context, "story/list/continue", sequence_token=first["sequence_token"]
    ).data
    assert second == {"items": selected[1:], "sequence_token": None}


def test_story_list_fields_match_the_client_model(context):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text(encoding="utf-8"))
    fields = schema["models"]["GetStoryListResponse200Data"]["fields"]
    listed = call(context, "story/list", story_category=4, per_view=10).data
    assert set(listed) == {field["name"] for field in fields}


async def test_native_story_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        assert set(ROUTES) <= set(runtime.registry.handlers)
        async with runtime.store.transaction(profile) as state:
            state["stories"].append(unread(SLIME_RECORD))
        listed = await client.call("story/list", {"story_category": 4, "per_view": 100})
        assert listed["data"] == {
            "items": [unread(SLIME_RECORD)],
            "sequence_token": None,
        }
        read = await client.call("story/read", SLIME)
        assert read["data"] is None
        assert set(read["sync"]) == {"story_record", "wallet"}
        before = await runtime.store.read(profile)
        assert before["stories"] == [finished(SLIME_RECORD)]
        for endpoint, payload, reason in (
            ("story/read", SLIME, "IS_ALREADY_READ"),
            ("story/read", COSTUME, "HAS_INVALID_STORY_ID"),
            (
                "story/list",
                {"story_category": 7, "per_view": 100},
                "HAS_INVALID_CATEGORY_ID",
            ),
        ):
            response = await post(http, client, endpoint, payload)
            assert response.status == 422
            assert await response.json() == {
                "status": {"code": 90000006, "message": reason},
                "data": {"rejected_reason": reason},
            }
        assert await runtime.store.read(profile) == before
    finally:
        await http.close()
