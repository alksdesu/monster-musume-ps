from monmusu_ps.core.layout import INDEX
from monmusu_ps.core.patch import differences
from test_overrides import GOODS, POOL, entry, overridden, runtime, state, unscheduled
from test_schedule import FOREVER, activity, scheduled


__all__ = ["runtime", "state", "unscheduled"]

NESTED = "dayData[0].rewards.m_rewards[0].m_count"
BINGO = 10


def served(runtime, dated=True) -> dict:
    return {table["asset"]: table for table in runtime.patch.payload(dated)["tables"]}


def test_only_changed_values_are_found():
    shipped = {"id": 1, "rewards": [{"val": 1}, {"val": 2}], "name": "a", "rate": 0.5}
    current = {"id": 1, "rewards": [{"val": 1}, {"val": 9}], "name": "b", "rate": 0.5}
    assert list(differences(shipped, current)) == [
        (["rewards", 1, "val"], 9),
        (["name"], "b"),
    ]
    assert list(differences(shipped, shipped)) == []


def test_changes_are_sent_with_where_the_game_keeps_them(runtime):
    masters = runtime.masters
    assert runtime.patch.payload()["tables"] == []
    price = masters.lookup("shop_data", "id", GOODS)["price"]
    step = masters.table("gacha_step")[0]
    count = next(
        name for name, value in step.items() if type(value) is int and name != "step_no"
    )
    overridden(
        runtime,
        shop_data=[entry({"id": GOODS}, price=price + 1)],
        gacha_data=[entry({"id": POOL}, title="一連")],
        login_bonus_cell=[entry({INDEX: 0}, **{NESTED: 500})],
        gacha_step=[
            entry(
                {"step_id": step["step_id"], "step_no": step["step_no"]},
                **{count: step[count] + 1},
            )
        ],
    )
    tables = served(runtime)
    assert sorted(tables) == [
        "gacha_data.asset",
        "gacha_step.asset",
        "login_bonus_cell.asset",
        "shop_data.asset",
    ]
    shop = tables["shop_data.asset"]
    assert shop["forget"] == []
    assert (shop["rows"], shop["key"], shop["count"], shop["late"]) == (
        ["table"],
        ["id"],
        12634,
        False,
    )
    assert [change["at"] for change in shop["changes"]] == [[GOODS]]
    value = shop["changes"][0]["set"][0]
    assert (value["path"], value["value"]) == (["price"], price + 1)
    assert {"startAt", "endAt"} <= set(value["forget"])
    pools = tables["gacha_data.asset"]
    assert (pools["rows"], pools["key"]) == (["table", "enclosure"], ["id"])
    assert pools["changes"][0]["at"] == [POOL]
    sheets = tables["login_bonus_cell.asset"]
    assert (sheets["key"], sheets["changes"][0]["at"]) == ([], [0])
    assert sheets["changes"][0]["set"][0]["path"] == [
        *("dayData", 0, "rewards", "m_rewards", 0, "m_count")
    ]
    steps = tables["gacha_step.asset"]
    assert steps["late"] is True and steps["key"] == ["step_id", "step_no"]
    assert steps["changes"][0]["at"] == [step["step_id"], step["step_no"]]


def test_lookups_the_game_builds_on_a_table_are_named(runtime):
    quest = runtime.masters.table("quest_data")[0]
    category = runtime.masters.table("quest_secret_category_table")[0]
    number = next(
        name for name, value in category.items() if type(value) is int and name != "id"
    )
    overridden(
        runtime,
        quest_data=[entry({"id": quest["id"]}, name="x")],
        quest_secret_category_table=[
            entry({"id": category["id"]}, **{number: category[number] + 1})
        ],
    )
    tables = served(runtime)
    assert tables["quest_data.asset"]["forget"] == ["m_dataIndex"]
    kept = tables["quest_secret_category_table.asset"]["changes"][0]["set"][0]
    assert "m_traitGroupData" in kept["forget"]


def test_the_schedule_can_be_left_out(runtime):
    price = runtime.masters.lookup("shop_data", "id", GOODS)["price"]
    overridden(runtime, shop_data=[entry({"id": GOODS}, price=price + 1)])
    scheduled(runtime, activity("bingo", BINGO, end=FOREVER))
    dated, shipped = served(runtime), served(runtime, dated=False)
    assert {"bingo_data.asset", "shop_data.asset"} <= set(dated)
    assert sorted(shipped) == ["shop_data.asset"]
    assert dated["shop_data.asset"] == shipped["shop_data.asset"]
    moved = dated["bingo_data.asset"]["changes"][0]
    assert moved["at"] == [BINGO] and moved["set"][0]["value"] == FOREVER
    assert (
        runtime.patch.payload()["revision"] != runtime.patch.payload(False)["revision"]
    )


def test_a_patch_is_worked_out_once_per_change(runtime):
    price = runtime.masters.lookup("shop_data", "id", GOODS)["price"]
    overridden(runtime, shop_data=[entry({"id": GOODS}, price=price + 1)])
    first = runtime.patch.payload()
    assert runtime.patch.payload()["tables"][0] is first["tables"][0]
    overridden(runtime, shop_data=[entry({"id": GOODS}, price=price + 2)])
    second = runtime.patch.payload()
    assert second["revision"] != first["revision"]
    assert second["tables"][0]["changes"][0]["set"][0]["value"] == price + 2
    overridden(runtime)
    assert runtime.patch.payload()["tables"] == []
