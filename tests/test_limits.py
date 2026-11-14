from pathlib import Path

import pytest

from monmusu_ps.core.calendar import Calendar
from monmusu_ps.core.limits import (
    DAILY,
    DATETIME,
    LIFETIME,
    MONTHLY,
    WEEKLY,
    Limits,
)
from monmusu_ps.core.masterdata import MasterData


ROOT = Path(__file__).resolve().parents[1]
RESET = 1_794_340_800
DAY = 86400


@pytest.fixture(scope="module")
def limits():
    return Limits(Calendar(MasterData(ROOT / "masterdata")))


@pytest.mark.parametrize("cycle", [LIFETIME, DAILY])
def test_first_count_creates_the_record(limits, cycle):
    state = {"limits": []}
    assert limits.used(state, "gacha.pool", cycle, RESET) == 0
    limits.count(state, "gacha.pool", cycle, 2, RESET)
    assert state["limits"] == [{"id": "gacha.pool", "times": 2, "counted_at": RESET}]
    assert limits.used(state, "gacha.pool", cycle, RESET + 1) == 2


def test_lifetime_counts_accumulate_across_days(limits):
    state = {"limits": [{"id": "gacha.pool", "times": 3, "counted_at": RESET - 1}]}
    assert limits.used(state, "gacha.pool", LIFETIME, RESET + 30 * DAY) == 3
    limits.count(state, "gacha.pool", LIFETIME, 1, RESET + 30 * DAY)
    assert state["limits"] == [
        {"id": "gacha.pool", "times": 4, "counted_at": RESET + 30 * DAY}
    ]


def test_daily_counts_restart_at_the_reset_hour(limits):
    state = {"limits": [{"id": "gacha.pool", "times": 3, "counted_at": RESET - 1}]}
    assert limits.used(state, "gacha.pool", DAILY, RESET - 1) == 3
    assert limits.used(state, "gacha.pool", DAILY, RESET) == 0
    assert state["limits"][0]["times"] == 3
    limits.count(state, "gacha.pool", DAILY, 1, RESET)
    assert state["limits"] == [{"id": "gacha.pool", "times": 1, "counted_at": RESET}]
    limits.count(state, "gacha.pool", DAILY, 2, RESET + DAY - 1)
    assert state["limits"][0]["times"] == 3


def test_records_are_counted_separately(limits):
    state = {"limits": [{"id": "gacha.pool", "times": 1, "counted_at": RESET}]}
    limits.count(state, "gacha.other", DAILY, 5, RESET)
    assert limits.used(state, "gacha.pool", DAILY, RESET) == 1
    assert limits.used(state, "gacha.other", DAILY, RESET) == 5
    assert [row["id"] for row in state["limits"]] == ["gacha.pool", "gacha.other"]


@pytest.mark.parametrize("rows", [[], [{"id": "x", "times": 1, "counted_at": RESET}]])
def test_unknown_cycles_are_reported_before_any_change(limits, rows):
    state = {"limits": rows}
    snapshot = [dict(row) for row in rows]
    with pytest.raises(NotImplementedError, match="yearly"):
        limits.used(state, "x", "yearly", RESET)
    with pytest.raises(NotImplementedError, match="yearly"):
        limits.count(state, "x", "yearly", 1, RESET)
    assert state["limits"] == snapshot


# RESET is Wednesday 2026-11-11 05:00 JST; the week anchored on Monday began on 11-09.
def test_weekly_counts_restart_on_the_anchor_weekday(limits):
    monday = RESET - 2 * DAY
    state = {"limits": [{"id": "shop.1", "times": 2, "counted_at": monday}]}
    assert limits.used(state, "shop.1", WEEKLY, RESET, 1) == 2
    assert limits.used(state, "shop.1", WEEKLY, monday + 7 * DAY - 1, 1) == 2
    assert limits.used(state, "shop.1", WEEKLY, monday + 7 * DAY, 1) == 0
    assert limits.used(state, "shop.1", WEEKLY, RESET, 0) == 2
    state["limits"][0]["counted_at"] = monday - DAY
    assert limits.used(state, "shop.1", WEEKLY, RESET, 0) == 2
    assert limits.used(state, "shop.1", WEEKLY, RESET, 1) == 0
    limits.count(state, "shop.1", WEEKLY, 1, RESET, 1)
    assert state["limits"][0]["times"] == 1


def test_monthly_counts_restart_on_the_anchor_day(limits):
    first = RESET - 10 * DAY
    state = {"limits": [{"id": "shop.2", "times": 4, "counted_at": first}]}
    assert limits.used(state, "shop.2", MONTHLY, RESET, 1) == 4
    assert limits.used(state, "shop.2", MONTHLY, first + 30 * DAY - 1, 1) == 4
    assert limits.used(state, "shop.2", MONTHLY, first + 30 * DAY, 1) == 0
    assert limits.used(state, "shop.2", MONTHLY, RESET, 11) == 0
    assert limits.used(state, "shop.2", MONTHLY, RESET - 1, 11) == 4


def test_dated_counts_restart_once_at_the_moment(limits):
    state = {"limits": [{"id": "shop.3", "times": 1, "counted_at": RESET - 1}]}
    moment = "2026/11/11 5:00:00"
    assert limits.used(state, "shop.3", DATETIME, RESET - 1, moment) == 1
    assert limits.used(state, "shop.3", DATETIME, RESET, moment) == 0
    limits.count(state, "shop.3", DATETIME, 2, RESET + 5, moment)
    assert state["limits"][0] == {"id": "shop.3", "times": 2, "counted_at": RESET + 5}
    assert limits.used(state, "shop.3", DATETIME, RESET + 40 * DAY, moment) == 2
