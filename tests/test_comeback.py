from copy import deepcopy
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.journal import LOGIN
from monmusu_ps.runtime import Runtime
from test_battle import connect
from test_gacha import fund, order, wallet
from test_gacha_modes import limit, medals
from test_shop import buy, redeem, windows


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
RESET = 1_794_340_800
HOUR = 3600
DAY = 24 * HOUR
SHEET = 10000
GACHA_WINDOW = "gacha.cb_gacha"
GACHA_SECONDS = 120 * HOUR
SINGLE = "gacha.normal.cbgacha01_PL"
PAID_TEN = "gacha.normal.cbgacha01_paid_only10"
MEDAL = "wallet.gachamedal.gmedal_cbgacha01"
MEDAL_GOODS, MEDAL_UNIT, MEDAL_PRICE = 500011, 177, 1500
PACK, PACK_SKU = 591037, "com.dmm.dmmgames.monmusutd.comebackA.dmm"
MEDAL_SHOPS = range(500011, 500030)
PACK_SHOPS = (511037, 511038, 521037, 521038, 591037, 591038)
RETURNED = NOW + 14 * DAY


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
    state = create_player(
        "comeback", "COMEBACK", runtime.masters, runtime.config.read(), NOW - DAY
    )
    state["player"]["tutorial"] = 9999
    return state


def stamp(runtime, state, now=NOW):
    result = runtime.login_bonus.stamp(state, now)
    return {
        row["sheet_id"]: (row["prev_progress"], row["progress"])
        for row in result.data["sheets"]
    }


def record(state):
    return next((row for row in state["login_bonus"] if row["sheet_id"] == SHEET), None)


def logins(state):
    return [row["data"] for row in state["events"] if row["type"] == LOGIN]


def test_the_first_stamp_starts_the_campaign(runtime, state):
    result = runtime.login_bonus.stamp(state, NOW)
    stamped = {row["sheet_id"]: row["progress"] for row in result.data["sheets"]}
    assert stamped[SHEET] == 1
    assert record(state) == {"sheet_id": SHEET, "progress": 1, "stamp_at": NOW}
    mailed = [
        row["reward"]
        for row in state["inbox"]
        if row["tags"][0] == f"loginbonus.stamp_reward:{SHEET}:1"
    ]
    assert mailed == [{"rid": "wallet.point.free_coin", "val": 1000}]
    opened = windows(state)
    assert opened[GACHA_WINDOW] == {"start_at": NOW, "end_at": NOW + GACHA_SECONDS}
    assert result.sync["period"][0]["data"] == state["periods"]


def test_the_campaign_opens_every_comeback_shop(runtime, state):
    stamp(runtime, state)
    opened = windows(state)
    for shop_id in MEDAL_SHOPS:
        assert opened[f"sales.{shop_id}"] == {
            "start_at": NOW,
            "end_at": NOW + 120 * HOUR,
        }
    for shop_id in PACK_SHOPS:
        assert opened[f"sales.{shop_id}"] == {
            "start_at": NOW,
            "end_at": NOW + 168 * HOUR,
        }
    assert len(opened) == 1 + len(MEDAL_SHOPS) + len(PACK_SHOPS)


def test_the_campaign_starts_only_once(runtime, state):
    stamp(runtime, state)
    opened = deepcopy(windows(state))
    assert stamp(runtime, state, RESET)[SHEET] == (1, 2)
    assert windows(state) == opened


def test_the_comeback_sheet_runs_for_seven_days(runtime, state):
    for day in range(7):
        assert stamp(runtime, state, RESET + day * DAY)[SHEET] == (day, day + 1)
    assert stamp(runtime, state, RESET + 7 * DAY)[SHEET] == (7, 7)
    assert windows(state)[GACHA_WINDOW]["start_at"] == RESET


def test_the_campaign_can_be_kept_for_returning_players(runtime, state):
    runtime.config.read()["comeback"]["with_login_bonus"] = False
    assert SHEET not in stamp(runtime, state)
    assert record(state) is None
    assert state["periods"] == []


def test_check_in_reports_the_days_away(runtime, state):
    for moment in (NOW, NOW + 60, RESET, RESET + 14 * DAY):
        runtime.attendance.check_in(state, moment)
    assert logins(state) == [
        {"first_today": True, "away_days": 0},
        {"first_today": False, "away_days": 0},
        {"first_today": True, "away_days": 1},
        {"first_today": True, "away_days": 14},
    ]
    assert state["last_login_day"] == RESET + 14 * DAY


def test_returning_players_get_a_fresh_campaign(runtime, state):
    runtime.attendance.check_in(state, NOW)
    stamp(runtime, state)
    runtime.limits.count(state, PAID_TEN, "", 3, NOW)
    runtime.limits.count(state, f"shop.{PACK}", "", 1, NOW)
    runtime.limits.count(state, "shop.9001", "", 2, NOW)
    runtime.attendance.check_in(state, RETURNED)
    assert record(state) == {"sheet_id": SHEET, "progress": 0, "stamp_at": 0}
    opened = windows(state)
    assert opened[GACHA_WINDOW] == {
        "start_at": RETURNED,
        "end_at": RETURNED + GACHA_SECONDS,
    }
    assert opened[f"sales.{PACK}"]["start_at"] == RETURNED
    assert limit(state, PAID_TEN)["times"] == 0
    assert limit(state, f"shop.{PACK}")["times"] == 0
    assert limit(state, "shop.9001")["times"] == 2
    assert stamp(runtime, state, RETURNED)[SHEET] == (0, 1)


def test_short_absences_keep_the_running_campaign(runtime, state):
    runtime.attendance.check_in(state, NOW)
    stamp(runtime, state)
    before = deepcopy(state)
    runtime.attendance.check_in(state, RETURNED - DAY)
    assert record(state) == record(before)
    assert state["periods"] == before["periods"]


def test_returning_can_be_switched_off(runtime, state):
    runtime.config.read()["comeback"]["away_days"] = 0
    runtime.attendance.check_in(state, NOW)
    stamp(runtime, state)
    opened = deepcopy(state["periods"])
    runtime.attendance.check_in(state, NOW + 60 * DAY)
    assert record(state)["progress"] == 1
    assert state["periods"] == opened


def test_returning_waits_for_the_tutorial(runtime, state):
    state["player"]["tutorial"] = 500
    runtime.attendance.check_in(state, NOW)
    runtime.attendance.check_in(state, RETURNED)
    assert record(state) is None
    assert state["periods"] == []


def test_the_comeback_pool_follows_the_campaign_window(runtime, state):
    fund(runtime, state, "wallet.point.free_coin", 900)
    with pytest.raises(ApiRejected) as early:
        runtime.gacha.draw(state, order(SINGLE), NOW)
    assert early.value.reason == "OUT_OF_SCHEDULE"
    stamp(runtime, state)
    result = runtime.gacha.draw(state, order(SINGLE), NOW)
    assert len(result.data["main_prizes"]) == 1
    assert medals(state, "gmedal_cbgacha01") == 10
    with pytest.raises(ApiRejected) as late:
        runtime.gacha.draw(state, order(SINGLE), NOW + GACHA_SECONDS)
    assert late.value.reason == "OUT_OF_SCHEDULE"


def test_comeback_medals_buy_the_featured_units(runtime, state):
    fund(runtime, state, MEDAL, MEDAL_PRICE)
    with pytest.raises(ApiNotFound):
        buy(runtime, state, MEDAL_GOODS)
    stamp(runtime, state)
    buy(runtime, state, MEDAL_GOODS)
    assert wallet(state, "gmedal_cbgacha01", "gachamedal")["val"] == 0
    assert any(row["card_id"] == MEDAL_UNIT for row in state["units"])
    assert limit(state, f"shop.{MEDAL_GOODS}")["times"] == 1
    with pytest.raises(ApiNotFound):
        buy(runtime, state, MEDAL_GOODS)


def test_comeback_packs_are_sold_during_the_campaign(runtime, state):
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, PACK_SKU, 1, NOW)
    stamp(runtime, state)
    redeem(runtime, state, PACK_SKU)
    assert wallet(state, "paid_coin")["val"] == 3000
    assert limit(state, f"shop.{PACK}")["times"] == 1
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, PACK_SKU, 1, NOW + 168 * HOUR)


async def test_native_login_starts_the_campaign(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        async with runtime.store.transaction(profile) as state:
            state["player"]["tutorial"] = 9999
        await client.call("player/get", {"player_module_ids": []})
        stamped = await client.call("loginbonus/get", {"_z0": 0})
        sheets = {row["sheet_id"]: row["progress"] for row in stamped["data"]["sheets"]}
        assert sheets[SHEET] == 1
        opened = {row["id"] for row in stamped["sync"]["period"][0]["data"]}
        assert GACHA_WINDOW in opened and f"sales.{PACK}" in opened
        listed = await client.call("loginbonus/sheets", {"_z0": 0})
        assert SHEET in {row["sheet_id"] for row in listed["data"]["sheets"]}
        async with runtime.store.transaction(profile) as state:
            assert logins(state)[-1] == {"first_today": True, "away_days": 0}
            assert record(state)["progress"] == 1
    finally:
        await http.close()
