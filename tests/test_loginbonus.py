from copy import deepcopy
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.runtime import Runtime
from test_battle import connect


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_332_574
CREATED = 1_794_326_424
RESET = 1_794_340_800
DAY = 86400
EXPIRY = 1_797_001_199
OFFICIAL_SHEETS = [1, 153, 13, 465, 468, 469, 470, 471]
OFFICIAL_MAILS = [
    ("wallet.point.free_coin", 7000, [":153:1", "vcr", "wallet"]),
    ("wallet.point.free_coin", 1500, [":465:1", "vcr", "wallet"]),
    ("wallet.point.free_coin", 1500, [":468:1", "vcr", "wallet"]),
    ("wallet.quest_key.premium", 3, [":1:1", "wallet"]),
    ("wallet.ticket.quest_skip", 5, [":13:1", "wallet"]),
    ("wallet.unit_exp.sr_all", 5, [":13:1", "wallet"]),
    ("wallet.quest_key.power", 2, [":465:1", "wallet"]),
    ("wallet.ticket.quest_skip", 10, [":465:1", "wallet"]),
    ("wallet.potion.stamina_m", 5, [":468:1", "wallet"]),
    ("wallet.ticket.quest_skip", 10, [":468:1", "wallet"]),
    ("wallet.quest_key.power", 1, [":469:1", "wallet"]),
    ("wallet.potion.stamina_m", 1, [":470:1", "wallet"]),
    ("wallet.ticket.quest_skip", 5, [":470:1", "wallet"]),
    ("wallet.potion.stamina_m", 1, [":471:1", "wallet"]),
    ("wallet.ticket.quest_skip", 10, [":471:1", "wallet"]),
]
NO_FRIEND_POINTS = {
    "friend_point": 0,
    "rental": {"point": 0, "count": 0, "is_max": False},
    "follower": {"point": 0, "count": 0, "is_max": False},
}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: NOW, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    settings["comeback"]["with_login_bonus"] = False
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def state(runtime):
    state = create_player(
        "bonus", "BONUS001", runtime.masters, runtime.config.read(), CREATED
    )
    state["player"]["tutorial"] = 900
    return state


def stamped(sheet_id, progress, at, previous=None):
    return {
        "sheet_id": sheet_id,
        "progress": progress,
        "prev_progress": progress - 1 if previous is None else previous,
        "stamp_at": at,
    }


def progress(result):
    return {
        row["sheet_id"]: (row["prev_progress"], row["progress"])
        for row in result.data["sheets"]
    }


def test_first_stamp_matches_the_official_response(runtime, state):
    result = runtime.login_bonus.stamp(state, NOW)
    assert result.data == {
        "sheets": [stamped(sheet_id, 1, NOW) for sheet_id in OFFICIAL_SHEETS],
        "get_friend_points": NO_FRIEND_POINTS,
    }
    assert set(result.sync) == {"player", "period", "mission_record"}
    assert result.sync["player"][0]["data"] == [state["player"]]
    assert result.sync["period"][0]["data"] == state["periods"]
    assert state["player"]["tutorial"] == 9999
    assert state["login_bonus"] == [
        {"sheet_id": sheet_id, "progress": 1, "stamp_at": NOW}
        for sheet_id in OFFICIAL_SHEETS
    ]


def test_first_stamp_mails_the_official_rewards(runtime, state):
    runtime.login_bonus.stamp(state, NOW)
    assert state["inbox"] == [
        {
            "inbox": "main",
            "no": number,
            "message": None,
            "reward": {"rid": rid, "val": amount},
            "tags": ["loginbonus.stamp_reward" + tags[0], *tags[1:]],
            "receive_at": NOW,
            "expire_at": EXPIRY,
        }
        for number, (rid, amount, tags) in enumerate(OFFICIAL_MAILS, 1)
    ]
    assert state["resource_sequences"]["inbox"] == 16
    assert state["wallets"] == deepcopy(
        create_player(
            "bonus", "BONUS001", runtime.masters, runtime.config.read(), CREATED
        )["wallets"]
    )


def test_same_game_day_is_stamped_only_once(runtime, state):
    runtime.login_bonus.stamp(state, NOW)
    before = deepcopy(state)
    again = runtime.login_bonus.stamp(state, RESET - 1)
    assert again.data["sheets"] == [
        stamped(sheet_id, 1, NOW, previous=1) for sheet_id in OFFICIAL_SHEETS
    ]
    assert again.sync is None
    assert state == before


def test_next_game_day_stamps_the_next_cell(runtime, state):
    runtime.login_bonus.stamp(state, NOW)
    result = runtime.login_bonus.stamp(state, RESET)
    assert result.data["sheets"] == [
        stamped(sheet_id, 2, RESET) for sheet_id in OFFICIAL_SHEETS
    ]
    assert result.sync is None
    second = state["inbox"][15:]
    assert [row["no"] for row in second] == list(range(16, 16 + len(second)))
    assert {row["tags"][0].rsplit(":", 1)[1] for row in second} == {"2"}
    assert [row["reward"] for row in second[:3]] == [
        {"rid": "wallet.point.free_coin", "val": 750},
        {"rid": "wallet.point.free_coin", "val": 500},
        {"rid": "wallet.point.free_coin", "val": 500},
    ]
    assert all(row["tags"][1:] == ["vcr", "wallet"] for row in second[:3])
    assert all(row["tags"][1:] == ["wallet"] for row in second[3:])


def test_sheet_stops_after_its_last_cell(runtime, state):
    for offset in range(3):
        runtime.login_bonus.stamp(state, NOW + offset * DAY)
    assert progress(runtime.login_bonus.stamp(state, NOW + 3 * DAY))[469] == (3, 3)
    mailed = [row for row in state["inbox"] if ":469:" in row["tags"][0]]
    assert [row["tags"][0].rsplit(":", 1)[1] for row in mailed] == ["1", "2", "3"]
    record = next(row for row in state["login_bonus"] if row["sheet_id"] == 469)
    assert record == {"sheet_id": 469, "progress": 3, "stamp_at": NOW + 2 * DAY}


def test_monthly_sheet_follows_the_game_month(runtime, state):
    masters = runtime.masters
    runtime.login_bonus.stamp(state, NOW)
    last = masters.timestamp("2026/12/01 04:59:59")
    assert progress(runtime.login_bonus.stamp(state, last))[1] == (1, 2)
    december = runtime.login_bonus.stamp(state, last + 1)
    assert progress(december)[2] == (0, 1)
    assert 1 not in progress(december)
    january = runtime.login_bonus.stamp(state, masters.timestamp("2027/01/01 05:00"))
    assert progress(january)[1] == (0, 1)
    assert 2 not in progress(january)
    record = next(row for row in state["login_bonus"] if row["sheet_id"] == 1)
    assert record["progress"] == 1


# Master data fetched later opens sheets of its own around the same days.
def recorded(result):
    return [sheet for sheet in progress(result) if sheet in OFFICIAL_SHEETS]


def test_event_sheets_follow_their_master_term(runtime, state):
    closing = runtime.masters.timestamp("2026/11/16 10:29:59")
    stamped_before = recorded(runtime.login_bonus.stamp(state, closing - 1))
    assert set(stamped_before) == set(OFFICIAL_SHEETS)
    later = create_player(
        "bonus", "BONUS002", runtime.masters, runtime.config.read(), CREATED
    )
    assert recorded(runtime.login_bonus.stamp(later, closing)) == [1, 153, 13, 471]


@pytest.mark.parametrize(("age", "opened"), [(179, True), (180, False)])
def test_beginner_sheet_lasts_for_the_beginner_days(runtime, state, age, opened):
    assert runtime.masters.property("Int", 40) == 180
    state["created_at"] = RESET - 1
    result = runtime.login_bonus.stamp(state, RESET - 1 + age * DAY)
    assert (153 in progress(result)) is opened


def test_unlock_and_comeback_sheets_are_left_to_their_own_rules(runtime, state):
    categories = {
        row["id"]: row["category"] for row in runtime.masters.table("login_bonus")
    }
    for offset in range(40):
        result = runtime.login_bonus.stamp(state, NOW + offset * DAY)
        assert {categories[sheet_id] for sheet_id in progress(result)} <= {
            "monthly_vip",
            "beginner",
            "event",
        }


@pytest.mark.parametrize(
    ("before", "after"), [(900, 9999), (0, 0), (500, 500), (700, 700), (9999, 9999)]
)
def test_only_the_pending_tutorial_step_is_completed(runtime, state, before, after):
    state["player"]["tutorial"] = before
    result = runtime.login_bonus.stamp(state, NOW)
    assert state["player"]["tutorial"] == after
    assert (result.sync is not None) is (before != after)


def test_listing_reports_progress_without_stamping(runtime, state):
    assert runtime.login_bonus.sheets(state, NOW) == []
    runtime.login_bonus.stamp(state, NOW)
    before = deepcopy(state)
    assert runtime.login_bonus.sheets(state, RESET) == [
        stamped(sheet_id, 1, NOW, previous=1) for sheet_id in OFFICIAL_SHEETS
    ]
    december = runtime.masters.timestamp("2026/12/01 05:00")
    assert [row["sheet_id"] for row in runtime.login_bonus.sheets(state, december)] == [
        153,
        13,
    ]
    january = runtime.masters.timestamp("2027/01/01 05:00")
    assert runtime.login_bonus.sheets(state, january)[0] == stamped(1, 0, NOW, 0)
    assert state == before


def test_every_login_bonus_reward_can_be_mailed(runtime):
    orders = [
        runtime.resources.reward_order(row)
        for table in runtime.masters.table("login_bonus_cell")
        for cell in table["dayData"]
        for row in cell["rewards"]["m_rewards"]
    ]
    assert sum(order is not None for order in orders) > 4000
    assert {order["rid"].split(".", 1)[0] for order in orders if order} == {
        "wallet",
        "item",
        "unit",
        "skill",
    }


async def test_native_login_bonus_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        assert {"loginbonus/get", "loginbonus/sheets"} <= set(runtime.registry.handlers)
        runtime.config.read()["comeback"]["with_login_bonus"] = False
        async with runtime.store.transaction(profile) as state:
            state["player"]["tutorial"] = 900
        first = await client.call("loginbonus/get", {"_z0": 0})
        assert [row["sheet_id"] for row in first["data"]["sheets"]] == OFFICIAL_SHEETS
        assert first["data"]["get_friend_points"] == NO_FRIEND_POINTS
        assert first["sync"]["player"][0]["data"][0]["tutorial"] == 9999
        again = await client.call("loginbonus/get", {"_z0": 0})
        assert again["sync"] is None
        assert {row["prev_progress"] for row in again["data"]["sheets"]} == {1}
        listed = await client.call("loginbonus/sheets", {"_z0": 0})
        assert listed["data"]["sheets"] == again["data"]["sheets"]
        mails = await client.call(
            "inbox/list/seq_start", {"inbox_name": "main", "per_view": 100, "order": 2}
        )
        assert [row["no"] for row in mails["data"]["items"]] == list(range(15, 0, -1))
        assert mails["data"]["items"][-1]["reward"] == {
            "rid": "wallet.point.free_coin",
            "val": 7000,
        }
    finally:
        await http.close()


UNLOCK_SHEET, SHORT_SHEET, LIMITED_SHEET = 240, 449, 459
UNLOCK = "loginbonus.unlock.240"
SHORT_UNLOCK = "loginbonus.unlock.449"
LIMITED_UNLOCK = "loginbonus.unlock.459"
AFTER_LIMITED_TERM = NOW + 20 * DAY


def unlock(runtime, state, rid=UNLOCK, now=NOW):
    changes = runtime.resources.grant(state, [{"rid": rid, "val": 1}], now)
    return changes.deliveries[0]


def listed(runtime, state, now=NOW):
    return [row["sheet_id"] for row in runtime.login_bonus.sheets(state, now)]


def test_unlock_sheets_stay_closed_until_bought(runtime, state):
    result = runtime.login_bonus.stamp(state, NOW)
    assert UNLOCK_SHEET not in progress(result)
    assert UNLOCK_SHEET not in listed(runtime, state)


def test_an_unlocked_sheet_is_listed_before_its_first_stamp(runtime, state):
    assert unlock(runtime, state) == {
        "rid": UNLOCK,
        "val": 1,
        "inc": 1,
        "inbox": 0,
        "over": 0,
    }
    assert state["login_bonus"] == [
        {"sheet_id": UNLOCK_SHEET, "progress": 0, "stamp_at": 0}
    ]
    assert runtime.login_bonus.sheets(state, NOW) == [
        {"sheet_id": UNLOCK_SHEET, "progress": 0, "prev_progress": 0, "stamp_at": 0}
    ]
    assert state["inbox"] == []


def test_an_unlocked_sheet_is_stamped_once_a_game_day(runtime, state):
    unlock(runtime, state)
    first = runtime.login_bonus.stamp(state, NOW)
    assert progress(first)[UNLOCK_SHEET] == (0, 1)
    mailed = [
        (row["reward"]["rid"], row["reward"]["val"])
        for row in state["inbox"]
        if row["tags"][0] == "loginbonus.stamp_reward:240:1"
    ]
    assert mailed == [("wallet.point.gold", 30000), ("wallet.unit_exp.ssr_all", 6)]
    again = runtime.login_bonus.stamp(state, RESET - 1)
    assert progress(again)[UNLOCK_SHEET] == (1, 1)
    second = runtime.login_bonus.stamp(state, RESET)
    assert progress(second)[UNLOCK_SHEET] == (1, 2)
    assert state["inbox"][-1]["tags"][0] != "loginbonus.stamp_reward:240:1"


def test_an_unlock_sheet_ends_with_its_last_cell(runtime, state):
    unlock(runtime, state, SHORT_UNLOCK)
    for day in range(5):
        stamped_day = runtime.login_bonus.stamp(state, RESET + day * DAY)
        assert progress(stamped_day)[SHORT_SHEET] == (day, day + 1)
    done = runtime.login_bonus.stamp(state, RESET + 5 * DAY)
    assert progress(done)[SHORT_SHEET] == (5, 5)
    assert SHORT_SHEET in listed(runtime, state, RESET + 5 * DAY)


def test_buying_an_unlocked_sheet_again_keeps_its_progress(runtime, state):
    unlock(runtime, state)
    runtime.login_bonus.stamp(state, NOW)
    assert unlock(runtime, state)["over"] == 1
    record = next(
        row for row in state["login_bonus"] if row["sheet_id"] == UNLOCK_SHEET
    )
    assert (record["progress"], record["stamp_at"]) == (1, NOW)


def test_limited_unlock_sheets_close_with_their_term(runtime, state):
    unlock(runtime, state, LIMITED_UNLOCK)
    assert LIMITED_SHEET in progress(runtime.login_bonus.stamp(state, NOW))
    late = runtime.login_bonus.stamp(state, AFTER_LIMITED_TERM)
    assert LIMITED_SHEET not in progress(late)
    assert LIMITED_SHEET not in listed(runtime, state, AFTER_LIMITED_TERM)


def test_sheet_unlocks_are_entitlements(runtime, state):
    assert runtime.resources.entitlement(UNLOCK)
    assert runtime.resources.redeemable(state, UNLOCK, NOW)
    assert runtime.resources.redeemable(state, LIMITED_UNLOCK, NOW)
    assert not runtime.resources.redeemable(state, LIMITED_UNLOCK, AFTER_LIMITED_TERM)
    unlock(runtime, state)
    assert not runtime.resources.redeemable(state, UNLOCK, NOW)
    with pytest.raises(ValueError, match="not a balance"):
        runtime.resources.balance(state, UNLOCK)
    order = runtime.resources.reward_order(
        {"m_walletCategory": "loginbonus", "m_walletName": "unlock.240", "m_count": 1}
    )
    assert order == {"rid": UNLOCK, "val": 1}
