import json

from aiohttp.test_utils import TestClient, TestServer
import pytest

from monmusu_ps.app import RUNTIME
from monmusu_ps.core.layout import GUARDED, INDEX, KNOWN, LOCKED, Layouts, infer
from monmusu_ps.core.overrides import EACH, Overrides, coerced, dated, parts, slot
from monmusu_ps.core.schedule import TABLES
from test_battle import runtime, state
from test_schedule import FOREVER, Source, activity, scheduled


__all__ = ["runtime", "state"]

GOODS = 103
BINGO = 10
POOL = "gacha.normal.tutorial_L"
START_CELL = 1


@pytest.fixture(autouse=True)
def unscheduled(request):
    # These tests set their own schedule; the shipped one would move the same rows.
    if "runtime" in request.fixturenames:
        scheduled(request.getfixturevalue("runtime"))


def overridden(runtime, **tables):
    runtime.overrides.config = Source({"tables": tables})
    runtime.overrides.reload()


def entry(key, **fields):
    return {"key": key, "fields": fields}


def test_paths_name_one_value_inside_a_row():
    assert parts("price") == ["price"]
    assert parts("rewards[2].m_count") == ["rewards", 2, "m_count"]
    assert parts("dayData[0].rewards.m_rewards[1].m_count") == [
        *("dayData", 0, "rewards", "m_rewards", 1, "m_count")
    ]
    for broken in ("", "[0]", "a..b", "a[x]", "a[-1]", "a b", "a.", 5, None):
        with pytest.raises(ValueError):
            parts(broken)


def test_values_keep_the_kind_the_game_expects():
    assert coerced(3, 9) == 9 and coerced("a", "") == ""
    assert coerced(0.5, 2) == 2.0 and type(coerced(0.5, 2)) is float
    assert coerced(2**40, 2**41) == 2**41
    for current, value in ((3, "9"), (3, 1.5), (3, True), (3, 2**31), ("a", 1)):
        with pytest.raises(ValueError):
            coerced(current, value)
    for current in ([1], {"a": 1}, None):
        with pytest.raises(ValueError):
            coerced(current, 1)
    assert coerced(1, 0, "bool") == 0 and coerced(5, 2**32 - 1, "uint") == 2**32 - 1
    assert coerced(5, 2**40, "long") == 2**40
    for current, value, kind in ((1, 2, "bool"), (5, -1, "uint"), (5, 2**31, "enum")):
        with pytest.raises(ValueError):
            coerced(current, value, kind)
    row = {"rewards": [{"val": 1}], "name": "x"}
    holder, last = slot(row, parts("rewards[0].val"))
    assert holder is row["rewards"][0] and last == "val"
    for missing in ("rewards[3].val", "rewards[0].amount", "name.first", "nothing"):
        with pytest.raises(ValueError, match="is not in this row"):
            slot(row, parts(missing))


def test_rows_are_told_apart_by_the_narrowest_key():
    assert infer([{"name": "a", "id": 1}, {"name": "b", "id": 2}]) == ("id",)
    assert infer([{"kind": 1, "item_id": 5}, {"kind": 2, "item_id": 6}]) == ("item_id",)
    rows = [{"a": 1, "b": 1}, {"a": 1, "b": 2}, {"a": 2, "b": 1}]
    assert infer(rows) == ("a", "b")
    assert infer([{"a": 1, "rows": []}, {"a": 1, "rows": []}]) == (INDEX,)
    assert infer([{"id": 1.5}, {"id": 2.5}]) == (INDEX,)


def test_the_schedule_and_the_overrides_name_rows_the_same_way(runtime):
    layouts = Layouts()
    for name, table in TABLES.items():
        if table.row_class is None:
            continue
        layout = layouts.of(name, runtime.masters.base(name))
        assert (layout.keys, layout.nest) == (tuple(table.keys), table.nest), name
    assert set(KNOWN) <= set(TABLES)


def test_an_override_changes_what_the_server_reads(runtime):
    masters = runtime.masters
    shipped = masters.base("shop_data")
    price = masters.lookup("shop_data", "id", GOODS)["price"]
    assert masters.document("shop_data") is shipped
    overridden(
        runtime, shop_data=[entry({"id": GOODS}, price=price + 40, name="値下げ")]
    )
    row = masters.lookup("shop_data", "id", GOODS)
    assert (row["price"], row["name"]) == (price + 40, "値下げ")
    assert masters.base("shop_data") is shipped
    assert next(row for row in shipped["table"] if row["id"] == GOODS)["price"] == price
    assert masters.document("shop_data") is masters.document("shop_data")
    assert runtime.overrides.touched() == ["shop_data"]
    assert runtime.overrides.report() is None
    overridden(runtime)
    assert masters.document("shop_data") is shipped
    assert runtime.overrides.touched() == []


def test_nested_rows_and_values_are_reached(runtime):
    masters = runtime.masters
    overridden(
        runtime,
        gacha_data=[entry({"id": POOL}, priority=7, title="一連")],
        gacha_prize_data=[entry({"id": "Tutorial"}, **{"prizes[0].prize_num": 3})],
        login_bonus_cell=[
            entry({INDEX: 0}, **{"dayData[0].rewards.m_rewards[0].m_count": 500})
        ],
    )
    group = next(row for row in masters.table("gacha_data") if row["id"] == POOL)
    pool = next(row for row in group["enclosure"] if row["id"] == POOL)
    assert (pool["priority"], pool["title"]) == (7, "一連")
    assert group["priority"] != 7 or group is not pool
    prizes = masters.lookup("gacha_prize_data", "id", "Tutorial")["prizes"]
    assert prizes[0]["prize_num"] == 3
    sheet = masters.table("login_bonus_cell")[0]
    assert sheet["dayData"][0]["rewards"]["m_rewards"][0]["m_count"] == 500
    assert runtime.overrides.report() is None


def test_overrides_and_the_schedule_stack(runtime):
    masters = runtime.masters
    shipped = masters.lookup("bingo_data", "id", BINGO)
    field = next(
        name
        for name, value in shipped.items()
        if type(value) is int and name != "id" and value < 1000
    )
    overridden(
        runtime, bingo_data=[entry({"id": BINGO}, **{field: shipped[field] + 1})]
    )
    scheduled(runtime, activity("bingo", BINGO, end=FOREVER))
    row = masters.lookup("bingo_data", "id", BINGO)
    assert row[field] == shipped[field] + 1 and row["end_dt"] == FOREVER
    below = masters.document("bingo_data", before=runtime.schedule)
    reopened = next(row for row in below["table"] if row["id"] == BINGO)
    assert reopened[field] == shipped[field] + 1
    assert reopened["end_dt"] == shipped["end_dt"]
    overridden(runtime)
    runtime.schedule.reload()
    row = masters.lookup("bingo_data", "id", BINGO)
    assert row[field] == shipped[field] and row["end_dt"] == FOREVER


def test_the_schedule_is_expanded_from_the_overridden_content(runtime):
    snapshot = {"kind": "bingo", "running_at": "2026/11/13 12:00:00", "end": FOREVER}
    assert scheduled(runtime, snapshot)["bingo_data"] == []
    overridden(runtime, bingo_data=[entry({"id": BINGO}, end_dt="2027/01/01 00:00:00")])
    runtime.schedule.reload()
    moved = runtime.schedule.rows("bingo_data")
    assert [(row["key"], row["fields"]) for row in moved] == [
        ({"id": BINGO}, {"end_dt": FOREVER})
    ]
    assert runtime.masters.lookup("bingo_data", "id", BINGO)["end_dt"] == FOREVER
    overridden(runtime)
    runtime.schedule.reload()
    assert runtime.schedule.rows("bingo_data") == []


def test_edits_that_would_break_the_game_are_refused(runtime):
    check = runtime.overrides.check
    masks = runtime.masters.table("tutorial_message_data")
    mask_key = {"id": masks[0]["id"]}
    goods = runtime.masters.lookup("shop_data", "id", GOODS)
    switch = next(
        name
        for name, value in goods.items()
        if type(value) is int
        and runtime.client_layout.leaf("shop_data", ("table",), [name])["type"]
        == "bool"
    )
    assert check({"tables": {}}) == {}
    assert check({"tables": {"shop_data": [entry({"id": GOODS}, **{switch: 1})]}})
    good = {"tables": {"shop_data": [entry({"id": GOODS}, price=1)]}}
    assert check(good) == {"shop_data": 1}
    refused = {
        "no row": {"shop_data": [entry({"id": -5}, price=1)]},
        "has no row": {"shop_data": [entry({"name": "x"}, price=1)]},
        "has to stay a whole number": {"shop_data": [entry({"id": GOODS}, price="1")]},
        "has to stay text": {"shop_data": [entry({"id": GOODS}, name=3)]},
        "is not in this row": {"shop_data": [entry({"id": GOODS}, cost=3)]},
        "names the row": {"shop_data": [entry({"id": GOODS}, id=4)]},
        "is not a single value": {
            "gacha_prize_data": [entry({"id": "Tutorial"}, prizes=[])]
        },
        "is set twice": {
            "shop_data": [entry({"id": GOODS}, price=1), entry({"id": GOODS}, price=2)]
        },
        "not a field path": {"shop_data": [entry({"id": GOODS}, **{"a..b": 1})]},
        "is not a master table": {"no_such_table": [entry({"id": 1}, a=1)]},
        "cannot be rewritten": {
            "tutorial_message_data": [entry(mask_key, **{"CoverMask1.x": 1.5})]
        },
        "the game can hold": {"shop_data": [entry({"id": GOODS}, **{switch: 2})]},
        "is read-only": {"property_sheet": [entry({"m_key": 3}, m_value=1)]},
        "is not a table of rows": {"unit_level_exp_table": [entry({"id": 1}, exp=1)]},
    }
    for reason, tables in refused.items():
        with pytest.raises(ValueError, match=reason):
            check({"tables": tables})
    for shape in ([], {"tables": []}, {"tables": {"shop_data": {}}}):
        with pytest.raises(ValueError):
            check(shape)
    with pytest.raises(ValueError, match="needs a list of key and fields"):
        check({"tables": {"shop_data": [{"key": {"id": GOODS}}]}})
    many = [entry({"id": -number}, price=1) for number in range(1, 9)]
    with pytest.raises(ValueError, match="and 3 more"):
        check({"tables": {"shop_data": many}})


def test_fields_that_only_hold_dates_are_found():
    rows = [
        {"start": "2026/1/5 9:00", "end": "", "text": "2026/01/05", "n": 1},
        {"start": "2026/01/05", "end": "2026/01/06 05:00:00", "text": "1/5 から"},
        {"days": [{"at": "2026/02/01 00:00:00"}, {"at": "2026/02/02 00:00:00"}]},
    ]
    assert dated(rows) == {
        ("start",): False,
        ("end",): True,
        ("days", EACH, "at"): False,
    }


def test_dates_stay_dates_the_game_can_read(runtime):
    check = runtime.overrides.check
    notice = runtime.masters.table("announce_image_data")[0]["id"]

    def refused(table, key, **fields):
        with pytest.raises(ValueError, match="has to be a date written like"):
            check({"tables": {table: [entry(key, **fields)]}})

    for moment in ("2026/01/31 05:00:00", ""):
        assert check(
            {"tables": {"shop_data": [entry({"id": GOODS}, startDate=moment)]}}
        )
    for unreadable in ("soon", "2026/1/31 5:00", "2026/01/31", "2026/02/30 00:00:00"):
        refused("shop_data", {"id": GOODS}, startDate=unreadable)
    refused("announce_image_data", {"id": notice}, start_date="")
    refused("gacha_data", {"id": POOL}, start_at="tomorrow")
    assert check(
        {"tables": {"shop_data": [entry({"id": GOODS}, text="2026/01/31 から")]}}
    )
    overridden(runtime, shop_data=[entry({"id": GOODS}, startDate="soon", name="x")])
    goods = runtime.masters.lookup("shop_data", "id", GOODS)
    shipped = runtime.masters.base("shop_data")["table"]
    original = next(row for row in shipped if row["id"] == GOODS)
    assert goods["name"] == "x" and goods["startDate"] == original["startDate"]
    assert "startDate has to be a date" in runtime.overrides.report()


def test_locked_tables_stay_as_shipped(runtime):
    locked = Overrides(
        runtime.masters,
        Source({"tables": {"shop_data": [entry({"id": GOODS}, price=1)]}}),
        {"shop_data": "battle data"},
    )
    shipped = runtime.masters.base("shop_data")
    assert locked.apply("shop_data", shipped) is shipped
    assert locked.report() == "shop_data is read-only: battle data"
    with pytest.raises(ValueError, match="read-only"):
        locked.check(locked.config.read())


def test_battle_data_and_asset_tables_are_locked(runtime):
    for name in LOCKED:
        assert "m_Name" in runtime.masters.base(name), name
    assert len(LOCKED) == 72
    open_tables = (
        "shop_data",
        "gacha_data",
        "mission_data",
        "login_bonus",
        "quest_data",
    )
    upkeep = ("recipe_skill", "recipe_ability", "event_character_growth", "charm_table")
    assert not set(open_tables + upkeep) & set(LOCKED)
    assert {"sort_config", "equipment_cost_exp_table", "unit_rarity"} <= set(LOCKED)
    skill = runtime.masters.table("skill_data")[0]
    battle = {"tables": {"skill_data": [entry({"id": skill["id"]}, name="x")]}}
    with pytest.raises(ValueError, match="skill_data is read-only: battle data"):
        runtime.overrides.check(battle)
    overridden(runtime, **battle["tables"])
    assert runtime.masters.document("skill_data") is runtime.masters.base("skill_data")


def test_battle_values_inside_operations_tables_are_guarded(runtime):
    for name, fields in GUARDED.items():
        assert name not in LOCKED and fields <= set(runtime.masters.table(name)[0])
    quest = runtime.masters.table("quest_data")[0]
    key = {"id": quest["id"]}
    for field in ("coreLife", "enemyRevision", "mana", "isEndless"):
        guarded = {"tables": {"quest_data": [entry(key, **{field: quest[field] + 1})]}}
        with pytest.raises(ValueError, match="is battle data and stays as shipped"):
            runtime.overrides.check(guarded)
    assert runtime.overrides.check(
        {"tables": {"quest_data": [entry(key, gold=quest["gold"] + 1, name="x")]}}
    )
    overridden(runtime, quest_data=[entry(key, coreLife=1, gold=quest["gold"] + 1)])
    current = runtime.masters.lookup("quest_data", "id", quest["id"])
    assert current["coreLife"] == quest["coreLife"]
    assert current["gold"] == quest["gold"] + 1
    assert "coreLife is battle data" in runtime.overrides.report()


def test_a_broken_file_keeps_the_last_good_overrides(runtime, tmp_path, caplog):
    masters = runtime.masters
    overridden(runtime, shop_data=[entry({"id": GOODS}, price=77)])
    runtime.overrides.config = Source({"tables": {"shop_data": "everything"}})
    runtime.overrides.reload()
    assert masters.lookup("shop_data", "id", GOODS)["price"] == 77
    assert "needs a list" in runtime.overrides.report()
    assert "keeping the previous ones" in caplog.text
    overridden(runtime, shop_data=[entry({"id": -1}, price=1)])
    assert masters.document("shop_data") is not masters.base("shop_data")
    assert runtime.overrides.report() == "shop_data has no row {'id': -1}"
    assert "Content override skipped" in caplog.text
    absent = Overrides(masters, runtime.config.__class__(tmp_path / "overrides.json"))
    assert absent.apply("shop_data", masters.base("shop_data")) is masters.base(
        "shop_data"
    )
    assert absent.touched() == [] and absent.report() is None


def test_boards_follow_the_cell_abilities_they_were_built_from(runtime):
    stage = runtime.masters.table("sugoroku_data")[-1]
    stages = runtime.sugoroku.stages
    board = stages.stage(stage)
    assert stages.stage(stage) is board
    cells = runtime.masters.table("sugoroku_cell_info_data")
    ability = next(row for row in cells if row["cell_category"] == START_CELL)
    overridden(
        runtime,
        sugoroku_cell_info_data=[
            entry({"cell_category": START_CELL}, ability_id=ability["ability_id"] + 5)
        ],
    )
    rebuilt = stages.stage(stage)
    assert rebuilt is not board and stages.stage(stage) is rebuilt
    overridden(runtime)
    assert stages.stage(stage) is not rebuilt


async def test_the_console_saves_overrides_only_when_they_fit(game_app, folders):
    app = game_app()
    async with TestClient(TestServer(app)) as client:
        masters = app[RUNTIME].masters
        price = masters.lookup("shop_data", "id", GOODS)["price"]
        good = {"tables": {"shop_data": [entry({"id": GOODS}, price=price + 1)]}}
        body = {"name": "overrides.json", "text": json.dumps(good)}
        written = await client.post("/console/api/config", json=body)
        assert written.status == 200 and (await written.json())["error"] is None
        status = await (await client.get("/console/api/status")).json()
        assert status["problems"]["overrides"] is None
        assert masters.lookup("shop_data", "id", GOODS)["price"] == price + 1
        bad = {"tables": {"shop_data": [entry({"id": GOODS}, price="free")]}}
        refused = await client.post(
            "/console/api/config", json={**body, "text": json.dumps(bad)}
        )
        assert refused.status == 400
        assert "has to stay a whole number" in (await refused.json())["error"]
        assert masters.lookup("shop_data", "id", GOODS)["price"] == price + 1
        kept = list((folders.backups / "config/overrides.json").glob("*.json"))
        assert len(kept) == 1
