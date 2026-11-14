import json

import pytest

from test_console import get, panel, post, runtime_of


__all__ = ["panel"]

PRICE = "price"
NESTED = "dayData[0].rewards.m_rewards[0].m_count"


@pytest.fixture(autouse=True)
def unscheduled():
    yield


async def test_content_tables_are_offered_without_battle_data(panel):
    listed = await get(panel, "content")
    assert [group["key"] for group in listed["groups"]][:2] == ["shop", "gacha"]
    tables = {row["name"]: row for row in listed["tables"]}
    assert (tables["shop_data"]["group"], tables["shop_data"]["label"]) == (
        "shop",
        "商店商品",
    )
    assert all(row["group"] and row["label"] for row in listed["tables"])
    hidden = {"skill_data", "ability_data", "effect_data", "property_sheet"}
    hidden |= {"sort_config", "equipment_cost_exp_table"}
    assert {"recipe_skill", "event_character_growth"} <= set(tables)
    assert not hidden & set(tables)
    shop = tables["shop_data"]
    assert (shop["rows"], shop["key"], shop["changed"]) == (12634, ["id"], 0)
    assert shop["fields"] > 20
    assert tables["login_bonus_cell"]["key"] == ["#"]
    assert tables["wallet_template"]["key"] == ["category", "name"]
    assert tables["gacha_data"]["rows"] >= 1351
    assert {"mission_data", "quest_data", "login_bonus", "banner_data"} <= set(tables)
    await get(panel, "content/rows", table="skill_data", expect=404)
    await get(panel, "content/rows", table="no_such_table", expect=404)
    await get(panel, "content/rows", table="property_sheet", expect=404)
    await get(panel, "content/rows", expect=400)


async def test_content_rows_are_searched_edited_and_put_back(panel):
    runtime = runtime_of(panel)
    page = await get(
        panel, "content/rows", table="shop_data", q="覚醒ポイント", limit=5
    )
    assert page["total"] > 5 and len(page["rows"]) == 5 and page["key"] == ["id"]
    assert {"name": PRICE, "label": "价格", "kind": "number"} in page["columns"]
    assert {"name": "name", "label": "名称", "kind": "text"} in page["columns"]
    assert page["rows"][0]["title"] == page["rows"][0]["values"]["name"]
    key, price = page["rows"][0]["key"], page["rows"][0]["values"][PRICE]
    body = {
        "table": "shop_data",
        "key": key,
        "fields": {PRICE: price + 9, "name": "特価"},
    }
    edited = await post(panel, "content/row", body)
    assert edited["current"][PRICE] == price + 9
    assert edited["shipped"][PRICE] == price
    assert edited["changed"] == ["name", PRICE]
    assert runtime.masters.lookup("shop_data", "id", key["id"])[PRICE] == price + 9
    changed = await get(panel, "content/rows", table="shop_data", changed="1")
    assert changed["total"] == 1
    assert changed["rows"][0]["changed"] == ["name", PRICE]
    listed = (await get(panel, "content/changes"))["changes"]
    assert {
        "table": "shop_data",
        "key": key,
        "path": PRICE,
        "shipped": price,
        "value": price + 9,
    } in listed
    tables = {row["name"]: row for row in (await get(panel, "content"))["tables"]}
    assert tables["shop_data"]["changed"] == 1
    back = await post(panel, "content/row", {**body, "fields": {PRICE: price}})
    assert back["changed"] == ["name"]
    again = await post(panel, "content/row", {**body, "fields": {"name": None}})
    assert again["changed"] == [] and again["current"] == again["shipped"]
    saved = json.loads((panel.folders.data / "overrides.json").read_text("utf-8"))
    assert saved == {"tables": {}}


async def test_edits_that_do_not_fit_change_nothing(panel):
    runtime = runtime_of(panel)
    page = await get(panel, "content/rows", table="shop_data", limit=1)
    key, price = page["rows"][0]["key"], page["rows"][0]["values"][PRICE]
    body = {"table": "shop_data", "key": key, "fields": {PRICE: price + 1}}
    refused = await post(
        panel, "content/row", {**body, "fields": {PRICE: "free"}}, expect=400
    )
    assert "has to stay a whole number" in refused["error"]
    await post(panel, "content/row", {**body, "fields": {"cost": 1}}, expect=400)
    await post(panel, "content/row", {**body, "fields": {"id": 5}}, expect=400)
    await post(panel, "content/row", {**body, "fields": {}}, expect=400)
    await post(panel, "content/row", {**body, "key": {"id": -1}}, expect=404)
    await post(panel, "content/row", {**body, "key": {"name": "x"}}, expect=404)
    await post(panel, "content/row", {**body, "table": "skill_data"}, expect=404)
    assert runtime.masters.lookup("shop_data", "id", key["id"])[PRICE] == price
    assert (await get(panel, "content/changes"))["changes"] == []
    await post(panel, "content/row", body)
    reset = await post(panel, "content/reset", {"table": "shop_data", "key": key})
    assert reset == {"reset": 1}
    assert (await post(panel, "content/reset", {"table": "shop_data"})) == {"reset": 0}
    row = await get(panel, "content/row", table="shop_data", key=json.dumps(key))
    assert row["changed"] == [] and row["current"][PRICE] == price
    await get(panel, "content/row", table="shop_data", key="{broken", expect=400)
    await get(panel, "content/row", table="shop_data", expect=400)


async def test_battle_values_of_a_quest_are_left_out(panel):
    runtime = runtime_of(panel)
    page = await get(panel, "content/rows", table="quest_data", limit=1)
    names = {column["name"] for column in page["columns"]}
    assert {"name", "gold", "openTime", "cleartable"} <= names
    assert not {"coreLife", "enemyRevision", "mana", "battleMemberId"} & names
    key, values = page["rows"][0]["key"], page["rows"][0]["values"]
    assert "coreLife" not in values and "gold" in values
    tables = {row["name"]: row for row in (await get(panel, "content"))["tables"]}
    assert tables["quest_data"]["fields"] == len(names) == 39
    row = await get(panel, "content/row", table="quest_data", key=json.dumps(key))
    assert "coreLife" not in row["shipped"] and "coreLife" not in row["current"]
    body = {"table": "quest_data", "key": key, "fields": {"coreLife": 1}}
    refused = await post(panel, "content/row", body, expect=400)
    assert "is battle data and stays as shipped" in refused["error"]
    life = runtime.masters.lookup("quest_data", "id", key["id"])["coreLife"]
    found = await get(panel, "content/rows", table="training_menu", limit=1)
    assert not {"quest_id", "type", "training_id"} & set(found["rows"][0]["values"])
    edited = await post(
        panel, "content/row", {**body, "fields": {"gold": values["gold"] + 5}}
    )
    assert edited["current"]["gold"] == values["gold"] + 5
    assert runtime.masters.lookup("quest_data", "id", key["id"])["coreLife"] == life


async def test_nested_content_is_edited_by_path(panel):
    body = {"table": "login_bonus_cell", "key": {"#": 0}, "fields": {NESTED: 500}}
    edited = await post(panel, "content/row", body)
    assert edited["changed"] == [NESTED]
    reward = edited["current"]["dayData"][0]["rewards"]["m_rewards"][0]
    shipped = edited["shipped"]["dayData"][0]["rewards"]["m_rewards"][0]
    assert (reward["m_count"], shipped["m_count"]) == (500, 50)
    listed = (await get(panel, "content/changes"))["changes"]
    assert [(row["path"], row["shipped"], row["value"]) for row in listed] == [
        (NESTED, 50, 500)
    ]
    whole = await post(panel, "content/reset", {"table": "login_bonus_cell"})
    assert whole == {"reset": 1}
    assert (await get(panel, "content/changes"))["changes"] == []


async def test_pools_are_edited_inside_their_groups(panel):
    runtime = runtime_of(panel)
    key = {"id": "gacha.normal.tutorial_L"}
    body = {"table": "gacha_data", "key": key, "fields": {"title": "一連"}}
    edited = await post(panel, "content/row", body)
    assert edited["current"]["title"] == "一連" and edited["changed"] == ["title"]
    group = next(
        row for row in runtime.masters.table("gacha_data") if row["id"] == key["id"]
    )
    assert group["enclosure"][0]["title"] == "一連"
    found = await get(panel, "content/rows", table="gacha_data", q="一連")
    assert [row["key"] for row in found["rows"]] == [key]


async def test_a_field_is_written_into_every_matching_row(panel):
    runtime = runtime_of(panel)
    found = await get(
        panel, "content/rows", table="shop_data", q="覚醒ポイント", limit=1
    )
    body = {"table": "shop_data", "q": "覚醒ポイント", "path": PRICE, "value": 0}
    assert (await post(panel, "content/bulk", body))["rows"] == found["total"]
    page = await get(panel, "content/rows", table="shop_data", changed=1, limit=1)
    assert page["total"] == found["total"] and page["rows"][0]["values"][PRICE] == 0
    current = runtime.masters.document("shop_data", before=runtime.schedule)["table"]
    assert all(
        row[PRICE] == 0
        for row in current
        if "覚醒ポイント" in json.dumps(row, ensure_ascii=False)
    )
    # Writing the shipped value back takes the override away again.
    rows = [
        row
        for row in runtime.masters.base("shop_data")["table"]
        if "覚醒ポイント" in json.dumps(row, ensure_ascii=False)
    ]
    prices = {row[PRICE] for row in rows}
    if len(prices) == 1:
        assert (await post(panel, "content/bulk", {**body, "value": prices.pop()}))[
            "rows"
        ] == found["total"]
        assert (await get(panel, "content/rows", table="shop_data", changed=1))[
            "total"
        ] == 0
    await post(panel, "content/bulk", {**body, "path": "id"}, expect=400)
    await post(panel, "content/bulk", {**body, "path": "nowhere"}, expect=400)
    await post(panel, "content/bulk", {**body, "value": "free"}, expect=400)
