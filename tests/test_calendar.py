from datetime import date
from pathlib import Path

import pytest

from monmusu_ps.core.calendar import Calendar
from monmusu_ps.core.masterdata import MasterData


ROOT = Path(__file__).resolve().parents[1]
RESET = 1_794_340_800


@pytest.fixture(scope="module")
def calendar():
    return Calendar(MasterData(ROOT / "masterdata"))


def test_game_day_turns_over_at_the_reset_hour(calendar):
    assert calendar.masters.timestamp("2026/11/11 05:00:00") == RESET
    assert calendar.day(RESET - 1) == date(2026, 11, 10)
    assert calendar.day(RESET) == date(2026, 11, 11)
    assert calendar.day(RESET + 86399) == date(2026, 11, 11)
    assert calendar.day(RESET + 86400) == date(2026, 11, 12)


def test_day_start_is_the_latest_reset(calendar):
    assert calendar.day_start(RESET - 1) == RESET - 86400
    assert calendar.day_start(RESET) == RESET
    assert calendar.day_start(RESET + 86399) == RESET
    assert calendar.day_start(RESET + 86400) == RESET + 86400


def test_game_day_keeps_the_previous_month_before_the_first_reset(calendar):
    first = calendar.masters.timestamp("2026/12/01 00:00:00")
    assert calendar.day(first) == date(2026, 11, 30)
    assert calendar.day(first + 5 * 3600) == date(2026, 12, 1)


@pytest.mark.parametrize(
    "moment", ["2026/11/11 00:00:00", "2026/11/11 02:42:54", "2026/11/11 23:59:59"]
)
def test_date_end_is_the_last_second_of_the_calendar_date(calendar, moment):
    end = calendar.masters.timestamp("2026/11/11 23:59:59")
    assert calendar.date_end(calendar.masters.timestamp(moment)) == end


def test_calendar_follows_changed_zone_and_reset_properties(calendar, monkeypatch):
    original = calendar.masters.property
    values = {("Int", 2): 0, ("Int", 3): 0}
    monkeypatch.setattr(
        calendar.masters,
        "property",
        lambda kind, key: values.get((kind, key), original(kind, key)),
    )
    assert calendar.day(RESET) == date(2026, 11, 10)
    assert calendar.day_start(RESET) == RESET - 20 * 3600
    assert calendar.date_end(RESET) == RESET + 4 * 3600 - 1
