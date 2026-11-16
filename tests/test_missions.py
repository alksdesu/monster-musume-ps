from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.missions import ACHIEVED, RECEIVED
from monmusu_ps.runtime import Runtime
from test_battle import closing, connect, opening


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_332_574
TODAY = 1_794_254_400
RESET = 1_794_340_800
DAY = 86400
FOREVER = 253_402_300_799
PANEL_CLOSING = TODAY + 8 * DAY
INVALID = "INVALID_MISSION_STATUS"
OFFICIAL_DAILY = {
    "record_list": [
        {"id": 25001, "status": 1, "updated_at": 1794327467, "achieved_at": 1794327467},
        {"id": 25002, "status": 1, "updated_at": 1794330686, "achieved_at": 1794330686},
        {"id": 25003, "status": 1, "updated_at": 1794332547, "achieved_at": 1794332547},
        {"id": 25004, "status": 1, "updated_at": 1794327184, "achieved_at": 1794327184},
        {"id": 25011, "status": 0, "updated_at": 1794332572, "achieved_at": 0},
        {"id": 25021, "status": 0, "updated_at": 1794332572, "achieved_at": 0},
    ],
    "counter_list": [
        {"name": "2.25001", "val": 5, "expired_at": 1794340800},
        {"name": "2.DailyLogin", "val": 1, "expired_at": 1794340800},
        {"name": "2.25011", "val": 0, "expired_at": 1794340800},
        {"name": "2.25021", "val": 0, "expired_at": 1794340800},
    ],
}
OFFICIAL_RECEIPT = {
    "received_list": [
        {
            "id": 25004,
            "rewards": [
                {
                    "rid": "wallet.quest_key.ancient",
                    "val": 1,
                    "inc": 1,
                    "inbox": 0,
                    "over": 0,
                },
                {
                    "rid": "wallet.ticket.quest_skip",
                    "val": 5,
                    "inc": 5,
                    "inbox": 0,
                    "over": 0,
                },
                {
                    "rid": "bonus_pass.point.9101",
                    "val": 10,
                    "inc": 10,
                    "inbox": 0,
                    "over": 0,
                },
            ],
        }
    ],
    "rejected_reasons": [],
}


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
        "missions", "MISSION1", runtime.masters, runtime.config.read(), NOW
    )


def clear(runtime, state, quest_id, now=NOW):
    started = runtime.battles.start(state, opening(quest_id), now)
    return runtime.battles.finish(state, closing(started.data["txid"]), now)


def login(runtime, state, now=NOW):
    runtime.attendance.check_in(state, now)


def achieved(state):
    return {row["id"] for row in state["missions"] if row["status"] == ACHIEVED}


def counters(state):
    return {
        row["name"]: (row["val"], row["expired_at"])
        for row in state["mission_counters"]
    }


def mark(state, mission_id, status=ACHIEVED, at=NOW):
    state["missions"].append(
        {"id": mission_id, "status": status, "updated_at": at, "achieved_at": at}
    )


def listing(runtime, state, category, now=NOW):
    return runtime.missions.records(state, category, now)


def receive(runtime, state, *ids, now=NOW):
    return runtime.missions.receive(state, {"id_list": list(ids)}, now)


def level(state):
    return next(
        row["level"]
        for row in state["modules"]["experience"]
        if row["section"] == "player_rank"
    )


def test_daily_listing_matches_the_official_sample(runtime, state):
    state["missions"] = [dict(row) for row in OFFICIAL_DAILY["record_list"][:4]]
    state["mission_counters"] = [
        dict(row) for row in OFFICIAL_DAILY["counter_list"][:2]
    ]
    assert listing(runtime, state, 2, 1_794_332_572) == OFFICIAL_DAILY


def test_listing_orders_missions_by_their_id_text(runtime, state):
    data = listing(runtime, state, 1)
    ids = [row["id"] for row in data["record_list"]]
    assert len(ids) == 559
    assert ids[:11] == [*range(100000, 100010), 10001]
    assert data["counter_list"] == []
    assert all(
        row == {"id": row["id"], "status": 0, "updated_at": NOW, "achieved_at": 0}
        for row in data["record_list"]
    )


def test_listing_names_each_counter_once_in_first_appearance_order(runtime, state):
    data = listing(runtime, state, 7)
    assert len(data["record_list"]) == 50
    names = [row["name"] for row in data["counter_list"]]
    assert len(names) == 27
    assert names[:12] == [
        "7.70000",
        "7.70001",
        "7.70002",
        "7.70003",
        "7.70004",
        "7.70005",
        "7.70007",
        "7.70009",
        "7.70011",
        "7.70014",
        "7.70017",
        "7.70018",
    ]
    assert {(row["val"], row["expired_at"]) for row in data["counter_list"]} == {
        (0, FOREVER)
    }


def listed(runtime, state, category, now=NOW):
    return [row["id"] for row in listing(runtime, state, category, now)["record_list"]]


def running(runtime, mission_id, now):
    row = runtime.masters.lookup("mission_data", "id", mission_id)
    return runtime.masters.in_term(row["startDate"], row["endDate"], now)


def test_listing_hides_missions_outside_their_window(runtime, state):
    opened = listed(runtime, state, 4)
    assert opened and all(running(runtime, mission, NOW) for mission in opened)
    closing_time = runtime.masters.timestamp("2026/11/16 10:29:59")
    closed = {
        mission for mission in opened if not running(runtime, mission, closing_time)
    }
    assert closed and not closed & set(listed(runtime, state, 4, closing_time))
    assert listing(runtime, state, 3) == {"record_list": [], "counter_list": []}
    assert listed(runtime, state, 6)[:3] == [2001, 2002, 2003]


@pytest.mark.parametrize("category", [0, 8, "2", None, 2.0, True])
def test_unknown_categories_are_rejected(runtime, state, category):
    with pytest.raises(ValueError, match="Unknown mission category"):
        listing(runtime, state, category)


def test_logins_count_once_per_game_day(runtime, state):
    login(runtime, state)
    assert achieved(state) == {25004, 26100}
    assert counters(state) == {"2.DailyLogin": (1, RESET), "6.TotalLogin": (1, FOREVER)}
    assert state["last_login_day"] == TODAY
    before = deepcopy(state)
    login(runtime, state, NOW + 60)
    before["events"] = state["events"]
    before["last_login_at"] = NOW + 60
    assert state == before
    login(runtime, state, RESET)
    assert counters(state) == {
        "2.DailyLogin": (1, RESET + DAY),
        "6.TotalLogin": (2, FOREVER),
    }
    assert [row for row in state["missions"] if row["id"] == 25004] == [
        {"id": 25004, "status": 1, "updated_at": RESET, "achieved_at": RESET}
    ]


def test_logins_catch_up_a_finished_tutorial(runtime, state):
    state["player"]["tutorial"] = 9999
    login(runtime, state)
    assert achieved(state) == {25004, 26100, 26300}


def test_finishing_the_tutorial_achieves_the_account_mission(runtime, state):
    runtime.tutorial.finish(state, NOW)
    assert state["missions"] == [
        {"id": 26300, "status": 1, "updated_at": NOW, "achieved_at": NOW}
    ]
    assert state["mission_counters"] == []


def test_quest_clears_advance_shared_counters_once_per_clear(runtime, state):
    clear(runtime, state, 10000)
    assert counters(state)["2.25001"] == (1, RESET)
    assert counters(state)["5.beginner_clearcount"] == (1, FOREVER)
    assert {25001, 52805} <= achieved(state)
    assert not {5001, 201} & achieved(state)
    for quest_id in (10001, 10002, 10003):
        clear(runtime, state, quest_id)
    assert counters(state)["2.25001"] == (4, RESET)
    assert level(state) == 2
    assert {201, 25002, 52806} <= achieved(state)
    assert 202 not in achieved(state)
    clear(runtime, state, 10004)
    assert counters(state)["2.25001"] == (5, RESET)
    assert counters(state)["5.26200"] == (1, FOREVER)
    assert {25003, 26201, 52807, 5001} <= achieved(state)
    assert 26200 not in achieved(state)
    assert all(
        row["updated_at"] == row["achieved_at"] == NOW for row in state["missions"]
    )


def test_rank_missions_chain_through_their_predecessors(runtime, state):
    runtime.progression.add_player_exp(state, 3100, NOW)
    clear(runtime, state, 10000)
    expected = {
        row["id"]
        for row in runtime.masters.table("mission_data")
        if row["category"] == 1
        and row["condition"] == 14
        and row["count"] <= level(state)
    }
    assert expected == {201, 202}
    assert {
        row["id"] for row in state["missions"] if 201 <= row["id"] <= 600
    } == expected


def test_panel_missions_wait_for_their_period(runtime, state):
    clear(runtime, state, 10000)
    assert "7.70001" not in counters(state)
    runtime.tutorial.finish(state, NOW)
    clear(runtime, state, 10001, NOW + 1)
    assert counters(state)["7.70001"] == (1, FOREVER)
    assert counters(state)["7.70000"] == (1, FOREVER)
    assert 70001 in achieved(state)
    clear(runtime, state, 10002, PANEL_CLOSING)
    assert counters(state)["7.70001"] == (1, FOREVER)


def test_collective_missions_complete_with_their_members(runtime, state):
    runtime.tutorial.finish(state, NOW)
    for mission_id in range(70001, 70007):
        mark(state, mission_id)
    for mission_id in (70007, 70014, 70021, 70028, 70035):
        mark(state, mission_id, RECEIVED)
    for mission_id in range(70043, 70049):
        mark(state, mission_id)
    clear(runtime, state, 10000, NOW + 1)
    assert {70000, 70042, 70049} <= achieved(state)
    assert counters(state)["7.70000"] == (6, FOREVER)
    assert counters(state)["7.70042"] == (6, FOREVER)
    assert counters(state)["7.70049"] == (7, FOREVER)


def test_receiving_matches_the_official_sample(runtime, state):
    mark(state, 25004, at=1_794_327_184)
    result = receive(runtime, state, 25004, now=1_794_332_736)
    assert result.data == OFFICIAL_RECEIPT
    assert set(result.sync) == {"wallet", "bonuspass_progression", "mission_record"}
    assert result.sync["mission_record"][0]["data"] == [
        {
            "id": 25004,
            "status": 2,
            "updated_at": 1_794_332_736,
            "achieved_at": 1_794_327_184,
        }
    ]
    assert result.sync["bonuspass_progression"][0]["data"] == [
        {
            "pass_id": 9105,
            "tier": 1,
            "progression": 10,
            "free_track": {"is_locked": False, "received_tier": 0, "remind_tier": 0},
            "premium_track": {"is_locked": True, "received_tier": 0, "remind_tier": 0},
            "tier_progressed_at": 1_794_332_736,
        }
    ]
    assert runtime.resources.balance(state, "wallet.quest_key.ancient") == 1
    gold = runtime.resources.balance(state, "wallet.point.gold")
    mark(state, 25001, at=1_794_327_467)
    again = receive(runtime, state, 25001, now=1_794_332_738)
    assert again.data["received_list"][0]["rewards"][0] == {
        "rid": "wallet.point.gold",
        "val": 1000,
        "inc": 1000,
        "inbox": 0,
        "over": 0,
    }
    assert runtime.resources.balance(state, "wallet.point.gold") == gold + 1000
    progression = state["bonus_passes"][0]
    assert (progression["tier"], progression["progression"]) == (2, 20)
    assert progression["tier_progressed_at"] == 1_794_332_738


def test_receiving_refuses_missions_that_are_not_waiting(runtime, state):
    mark(state, 25004)
    result = receive(runtime, state, 25002, 99_999_999, 25004, 25004)
    assert [row["id"] for row in result.data["received_list"]] == [25004]
    assert result.data["rejected_reasons"] == [
        {"id": 25002, "reason": INVALID},
        {"id": 99_999_999, "reason": INVALID},
        {"id": 25004, "reason": INVALID},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"id_list": None},
        {"id_list": "25004"},
        {"id_list": [25004, "25001"]},
        {"id_list": [True]},
    ],
)
def test_malformed_receipts_are_errors(runtime, state, payload):
    with pytest.raises(ValueError, match="id_list"):
        runtime.missions.receive(state, payload, NOW)


def test_overflowing_rewards_keep_the_mission_waiting(runtime, state):
    mark(state, 25001)
    cap = next(
        row["val_cap"]
        for row in runtime.masters.table("wallet_template")
        if (row["category"], row["name"]) == ("point", "gold")
    )
    next(row for row in state["wallets"] if row["name"] == "gold")["val"] = cap
    before = deepcopy(state)
    result = receive(runtime, state, 25001)
    assert result.data == {
        "received_list": [],
        "rejected_reasons": [{"id": 25001, "reason": "ASSET_CAPACITY_OVERFLOW"}],
    }
    assert result.sync is None
    assert state == before


def test_broken_reward_rows_are_reported_per_mission(runtime, state):
    row = runtime.masters.lookup("mission_data", "id", 25001)
    row["rewardtable"]["m_rewards"][0]["m_walletName"] = "未知"
    mark(state, 25001)
    mark(state, 25004)
    result = receive(runtime, state, 25001, 25004)
    assert [row["id"] for row in result.data["received_list"]] == [25004]
    assert result.data["rejected_reasons"] == [
        {"id": 25001, "reason": "REWARD_DATA_ERROR"}
    ]
    assert (
        next(row["status"] for row in state["missions"] if row["id"] == 25001)
        == ACHIEVED
    )


def test_mailed_rewards_reach_the_inbox(runtime, state):
    mark(state, 26202)
    result = receive(runtime, state, 26202)
    reward = {"rid": "wallet.ticket.gacha_common220801", "val": 1}
    assert result.data["received_list"] == [
        {"id": 26202, "rewards": [{**reward, "inc": 0, "inbox": 1, "over": 0}]}
    ]
    assert set(result.sync) == {"mission_record"}
    letter = state["inbox"][0]
    assert (letter["message"], letter["reward"], letter["tags"]) == (
        "初心者パネルミッション報酬",
        reward,
        ["mission.reward:26202", "wallet"],
    )
    assert runtime.resources.balance(state, reward["rid"]) == 0


def test_story_missions_unlock_their_story(runtime, state):
    mark(state, 2005)
    receive(runtime, state, 2005)
    assert state["stories"] == [{"id": "story.3.30005.30001", "is_already_read": False}]
    runtime.stories.read(
        state, {"story_category": 3, "story_id": 30005, "id": 30001}, NOW
    )
    assert state["stories"][0]["is_already_read"] is True


def test_daily_missions_reset_with_the_game_day(runtime, state):
    clear(runtime, state, 10000)
    assert 25001 in achieved(state)
    data = listing(runtime, state, 2, RESET)
    assert next(row for row in data["record_list"] if row["id"] == 25001) == {
        "id": 25001,
        "status": 0,
        "updated_at": RESET,
        "achieved_at": 0,
    }
    assert next(row for row in data["counter_list"] if row["name"] == "2.25001") == {
        "name": "2.25001",
        "val": 0,
        "expired_at": RESET + DAY,
    }
    assert 25001 not in achieved(state)
    assert counters(state)["5.beginner_clearcount"] == (1, FOREVER)
    assert receive(runtime, state, 25001, now=RESET).data["rejected_reasons"] == [
        {"id": 25001, "reason": INVALID}
    ]


def test_experience_rewards_level_the_player_within_the_receipt(runtime, state):
    mark(state, 25021)
    result = receive(runtime, state, 25021)
    rewards = result.data["received_list"][0]["rewards"]
    assert rewards[0] == {
        "rid": "wallet.instant.pexp",
        "val": 2000,
        "inc": 2000,
        "inbox": 0,
        "over": 0,
    }
    assert (rewards[1]["rid"], rewards[1]["inc"]) == ("wallet.instant.stamina", 50)
    assert rewards[2]["rid"] == "bonus_pass.point.9101"
    assert level(state) == 3
    assert achieved(state) == {201, 26207}
    assert {"experience", "stamina"} <= set(result.sync["module_prop"][0]["data"])


def test_response_field_names_match_the_client_models(runtime, state):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text(encoding="utf-8"))

    def fields(model):
        return {field["name"] for field in schema["models"][model]["fields"]}

    mark(state, 25004)
    data = listing(runtime, state, 2)
    assert set(data) == fields("GetMissionRecordListInCategoryResponse200Data")
    assert all(set(row) == fields("MissionRecord") for row in data["record_list"])
    assert all(set(row) == fields("MissionCounter") for row in data["counter_list"])
    receipt = receive(runtime, state, 25004, 25002).data
    assert set(receipt) == fields("ReceiveMissionRewardResponse200Data")
    assert set(receipt["received_list"][0]) == fields("MissionRewardResult")
    assert set(receipt["rejected_reasons"][0]) == fields("MissionRewardRejectedReason")
    assert set(state["bonus_passes"][0]) == fields("BonusPassProgression")


async def test_native_mission_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        assert {"mission/list/category", "mission/receive"} <= set(
            runtime.registry.handlers
        )
        player = await client.call(
            "player/get", {"player_module_ids": ["stamina", "experience"]}
        )
        assert {"player", "module_prop"} <= set(player["sync"])
        saved = await runtime.store.read(profile)
        assert {25004, 26100} <= {row["id"] for row in saved["missions"]}
        for category in range(1, 8):
            data = (
                await client.call("mission/list/category", {"category_id": category})
            )["data"]
            assert set(data) == {"record_list", "counter_list"}
        daily = (await client.call("mission/list/category", {"category_id": 2}))["data"]
        assert (
            next(row["status"] for row in daily["record_list"] if row["id"] == 25004)
            == 1
        )
        received = await client.call("mission/receive", {"id_list": [25004]})
        assert [row["id"] for row in received["data"]["received_list"]] == [25004]
        assert received["sync"]["mission_record"][0]["data"][0]["status"] == 2
        again = await client.call("mission/receive", {"id_list": [25004]})
        assert again["data"] == {
            "received_list": [],
            "rejected_reasons": [{"id": 25004, "reason": INVALID}],
        }
        assert again["sync"] is None
        saved = await runtime.store.read(profile)
        assert (
            next(row for row in saved["missions"] if row["id"] == 25004)["status"] == 2
        )
    finally:
        await http.close()
