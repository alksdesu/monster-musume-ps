from copy import deepcopy
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.journal import (
    PLAYER_RANK_UP,
    QUEST_COMPLETE,
    TUTORIAL_PROGRESS,
)
from monmusu_ps.core.tutorial import ROUTE
from monmusu_ps.runtime import Runtime


ROOT = Path(__file__).resolve().parents[1]
CLEARED = 1_794_332_548
FINISHED = 1_794_332_574
RESET = 1_794_340_800
HOUR = 3600
DAY = 24 * HOUR
PANEL_CLOSING = RESET + 7 * DAY
START_DASH_SHOPS = [*range(500001, 500011), *range(501001, 501010)]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: FINISHED, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def state(runtime):
    return create_player(
        "period", "PERIOD01", runtime.masters, runtime.config.read(), CLEARED
    )


def window(identity, start, end, counted):
    return {
        "id": identity,
        "period": {"start_at": start, "end_at": end},
        "counted_at": counted,
    }


def announce(runtime, state, now, quest_id, first_clear=True):
    quest = runtime.masters.lookup("quest_data", "id", quest_id)
    runtime.journal.record(
        state,
        "questexec-test",
        QUEST_COMPLETE,
        now,
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


def spans(state):
    return {row["id"]: row["period"] for row in state["periods"]}


def test_last_tutorial_quest_opens_the_official_beginner_packs(runtime, state):
    announce(runtime, state, CLEARED, 10004)
    assert state["periods"] == [
        window(f"sales.{shop_id}", CLEARED, 1_794_591_748, CLEARED)
        for shop_id in (511001, 521001, 591001)
    ]


@pytest.mark.parametrize(("quest_id", "pack"), [(10006, 2), (10107, 3), (10207, 4)])
def test_unlock_quests_open_their_prep_packs(runtime, state, quest_id, pack):
    announce(runtime, state, CLEARED, quest_id)
    assert state["periods"] == [
        window(f"sales.{platform + pack}", CLEARED, CLEARED + 72 * HOUR, CLEARED)
        for platform in (511000, 521000, 591000)
    ]


def test_prep_pack_quests_follow_the_settings(runtime, state):
    runtime.config.read()["quest_clear_sales"] = {"ボスチャレンジ準備パック": 10005}
    announce(runtime, state, CLEARED, 10107)
    assert state["periods"] == []
    announce(runtime, state, CLEARED, 10005)
    assert set(spans(state)) == {"sales.511003", "sales.521003", "sales.591003"}


def test_repeated_clears_open_no_prep_packs(runtime, state):
    announce(runtime, state, CLEARED, 10107, first_clear=False)
    assert state["periods"] == []


def test_finishing_the_tutorial_opens_the_official_start_dash_windows(runtime, state):
    runtime.tutorial.finish(state, FINISHED)
    assert state["periods"] == [
        window("sdpanel_mission.1", FINISHED, 1_794_945_600, FINISHED),
        *(
            window(
                f"sdpanel_mission.{day}",
                RESET + (day - 2) * DAY,
                1_794_945_600,
                FINISHED,
            )
            for day in range(2, 8)
        ),
        window("gacha.sd_gacha", FINISHED, 1_794_764_574, FINISHED),
        *(
            window(f"sales.{shop_id}", FINISHED, 1_794_764_574, FINISHED)
            for shop_id in START_DASH_SHOPS
        ),
    ]


@pytest.mark.parametrize("progress", [progress for progress, _ in ROUTE[:-1]])
def test_earlier_tutorial_steps_open_nothing(runtime, state, progress):
    runtime.journal.record(
        state, "tutorial-test", TUTORIAL_PROGRESS, FINISHED, {"progress": progress}
    )
    assert state["periods"] == []


def test_quest_packs_last_as_long_as_the_shop_says(runtime, state):
    announce(runtime, state, CLEARED, 59002)
    assert state["periods"] == [
        window(f"sales.{shop_id}", CLEARED, CLEARED + 48 * HOUR, CLEARED)
        for shop_id in (511230, 521230, 591230)
    ]


@pytest.mark.parametrize(("quest_id", "first_clear"), [(10004, False), (10000, True)])
def test_other_clears_open_nothing(runtime, state, quest_id, first_clear):
    announce(runtime, state, CLEARED, quest_id, first_clear)
    assert state["periods"] == []


def test_reopening_replaces_the_windows(runtime, state):
    runtime.tutorial.finish(state, FINISHED)
    runtime.tutorial.finish(state, FINISHED + DAY)
    identities = [row["id"] for row in state["periods"]]
    assert len(identities) == len(set(identities)) == 27
    assert state["periods"][0] == window(
        "sdpanel_mission.1", FINISHED + DAY, PANEL_CLOSING + DAY, FINISHED + DAY
    )


def test_start_dash_lengths_follow_the_settings(runtime, state):
    runtime.config.read()["start_dash"] = {"gacha_seconds": 60, "panel_days": 3}
    runtime.tutorial.finish(state, FINISHED)
    opened = spans(state)
    assert opened["gacha.sd_gacha"] == {"start_at": FINISHED, "end_at": FINISHED + 60}
    assert opened["sdpanel_mission.1"]["end_at"] == RESET + 2 * DAY
    assert opened["sales.500001"]["end_at"] == FINISHED + 120 * HOUR


@pytest.mark.parametrize(
    ("now", "first_day"), [(RESET - 1, RESET - DAY), (RESET, RESET)]
)
def test_panel_days_turn_over_at_the_reset_hour(runtime, state, now, first_day):
    runtime.tutorial.finish(state, now)
    opened = spans(state)
    assert opened["sdpanel_mission.1"] == {
        "start_at": now,
        "end_at": first_day + 8 * DAY,
    }
    assert opened["sdpanel_mission.2"]["start_at"] == first_day + DAY
    assert opened["sdpanel_mission.7"]["start_at"] == first_day + 6 * DAY


def test_expired_panel_frames_open_no_panel_windows(runtime, state):
    runtime.masters.table("mission_panel_frame")[0]["endDate"] = "2026/11/01 00:00:00"
    runtime.tutorial.finish(state, FINISHED)
    assert [row["id"] for row in state["periods"]] == [
        "gacha.sd_gacha",
        *(f"sales.{shop_id}" for shop_id in START_DASH_SHOPS),
    ]


def rank_packs(runtime, *ranks):
    return {
        f"sales.{row['id']}"
        for row in runtime.masters.table("shop_data")
        if row["sales_conditions"] == "プレイヤーランク"
        and row["sales_conditions_param"] in {str(rank) for rank in ranks}
    }


def test_reaching_a_rank_opens_its_packs_for_a_day(runtime, state):
    runtime.journal.record(
        state, "rankup-test", PLAYER_RANK_UP, CLEARED, {"from": 8, "to": 21}
    )
    expected = rank_packs(runtime, 10, 20)
    assert {"sales.590010", "sales.590020"} <= expected
    assert spans(state) == {
        identity: {"start_at": CLEARED, "end_at": CLEARED + DAY}
        for identity in expected
    }


def test_ranks_without_packs_open_nothing(runtime, state):
    runtime.journal.record(
        state, "rankup-test", PLAYER_RANK_UP, CLEARED, {"from": 1, "to": 9}
    )
    assert state["periods"] == []
