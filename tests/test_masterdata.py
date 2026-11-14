from pathlib import Path

import pytest

from monmusu_ps.core.masterdata import MasterData


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def masters():
    return MasterData(ROOT / "masterdata")


def test_timestamp_reads_master_time_in_the_property_zone(masters):
    assert masters.property("Int", 3) == 9
    assert masters.timestamp("2026/11/11 05:00:00") == 1_794_340_800
    assert masters.timestamp("2026/11/11 5:00:00") == 1_794_340_800
    assert masters.timestamp("2026/11/10 23:59:59") == 1_794_322_799


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026/11/11 5:00", 1_794_340_800),
        ("2026/11/11  05:00:00", 1_794_340_800),
        ("2026/11/11", 1_794_340_800 - 5 * 3600),
        ("2026/1/3 4:59:59", 1_767_383_999),
    ],
)
def test_timestamp_reads_every_shape_the_master_tables_use(masters, text, expected):
    assert masters.timestamp(text) == expected


def test_timestamp_follows_a_changed_zone_property(masters, monkeypatch):
    original = masters.property
    monkeypatch.setattr(
        masters,
        "property",
        lambda kind, key: 0 if (kind, key) == ("Int", 3) else original(kind, key),
    )
    assert masters.timestamp("2026/11/11 05:00:00") == 1_794_340_800 + 9 * 3600


@pytest.mark.parametrize(
    "text",
    ["2026-11-11 05:00:00", "2026/11/11 05", "11/11/2026 05:00:00", "2026/11", ""],
)
def test_timestamp_rejects_other_formats(masters, text):
    with pytest.raises(ValueError):
        masters.timestamp(text)


def test_every_master_schedule_is_readable(masters):
    values = [
        row[field]
        for row in masters.table("quest_data")
        for field in ("openTime", "closeTime")
        if row[field]
    ]
    values += [
        row[field]
        for row in masters.table("item_limit_table")
        for field in ("start_at", "end_at")
        if row[field]
    ]
    values += [
        pool[field]
        for group in masters.table("gacha_data")
        for pool in group["enclosure"]
        for field in ("start_at", "end_at")
        if pool[field]
    ]
    values += [
        row[field]
        for row in masters.table("login_bonus")
        for field in ("start_dt", "end_dt")
        if row[field]
    ]
    assert len(values) > 4000
    for value in values:
        assert type(masters.timestamp(value)) is int


def test_schedule_years_before_the_epoch_stay_ordered(masters):
    assert masters.timestamp("1023/05/08 12:00:00") < 0
