import json
import re

import pytest

from monmusu_ps.app import CONSOLE
from monmusu_ps.console import catalog, naming
from monmusu_ps.console.content import hints
from test_console import TRANSLATED, get, panel, post, runtime_of, translate


__all__ = ["panel"]

PRICE = "price"
NESTED = "dayData[0].rewards.m_rewards[0].m_count"
REWARD = "dayData[0].rewards.m_rewards[0].m_walletName"
GOLD_GOODS = 300101
RECIPE = "subskill_recipe_data"
MATERIAL = "materials[0].id"
POSITION = re.compile(r"\[\d+\]")


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
    shipped = runtime_of(panel).masters.base("shop_data")["table"]
    assert (shop["rows"], shop["key"], shop["changed"]) == (len(shipped), ["id"], 0)
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
        "title": "特価",
        "path": PRICE,
        "shipped": price,
        "value": price + 9,
        "hints": {"shipped": "", "value": ""},
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


async def test_values_carry_what_the_game_calls_them(panel, tmp_path):
    fields = (await get(panel, "content"))["fields"]
    assert (fields["m_walletName"], fields[PRICE]) == ("资源", "价格")
    page = await get(panel, "content/rows", table="shop_data", q=str(GOLD_GOODS))
    row = next(row for row in page["rows"] if row["key"] == {"id": GOLD_GOODS})
    assert row["values"]["price_name"] == "gold"
    assert row["hints"]["price_name"] == "ゴールドコイン"
    assert not any("." in path or "[" in path for path in row["hints"])
    cost = await get(panel, "content/rows", table="unit_upgrade_cost", limit=1)
    assert cost["rows"][0]["hints"] == {"cost_resource": "ゴールドコイン"}
    sheet = {"table": "login_bonus_cell", "key": json.dumps({"#": 0})}
    assert (await get(panel, "content/row", **sheet))["hints"][REWARD] == "幻獣石(無償)"
    await translate(panel, tmp_path, TRANSLATED)
    assert (await get(panel, "content/row", **sheet))["hints"][REWARD] == "幻兽石(无偿)"


async def test_a_row_named_by_a_resource_is_found_by_that_name(panel, tmp_path):
    page = await get(panel, "content/rows", table="expired_item")
    row = page["rows"][0]
    assert row["values"]["item_name"] == "wallet.gachamedal.gmedal_sdgacha01"
    assert row["title"] == row["hints"]["item_name"] == "スタートダッシュガチャメダル"
    found = await get(panel, "content/rows", table="expired_item", q="スタートダッシュ")
    assert [entry["key"] for entry in found["rows"]] == [row["key"]]
    await translate(panel, tmp_path, TRANSLATED)
    found = await get(panel, "content/rows", table="expired_item", q="起点冲刺")
    assert [entry["title"] for entry in found["rows"]] == ["起点冲刺扭蛋奖章"]
    wallets = await get(panel, "content/rows", table="wallet_template", q="金币")
    gold = next(row for row in wallets["rows"] if row["key"]["name"] == "gold")
    assert gold["title"] == gold["hints"]["wallet_name"] == "金币"


async def test_a_changed_reference_is_listed_under_both_names(panel):
    body = {
        "table": "shop_data",
        "key": {"id": GOLD_GOODS},
        "fields": {"price_name": "free_coin"},
    }
    await post(panel, "content/row", body)
    changes = (await get(panel, "content/changes"))["changes"]
    assert [(row["path"], row["hints"]) for row in changes] == [
        ("price_name", {"shipped": "ゴールドコイン", "value": "幻獣石(無償)"})
    ]
    assert changes[0]["title"].startswith("EXPミルク")


def sub_skill(panel, identity: int) -> str:
    masters = runtime_of(panel).masters
    return masters.lookup("attach_ability_data", "id", identity)["name"]


def unit(panel, identity: int) -> str:
    card = runtime_of(panel).masters.asset("unitcard", identity)
    return f"{card['nickname']} {card['charaName']}"


async def first_row(panel, table: str) -> dict:
    return (await get(panel, "content/rows", table=table, limit=1))["rows"][0]


async def test_an_id_carries_the_name_of_what_it_points_at(panel, tmp_path):
    masters = runtime_of(panel).masters
    recipe = await first_row(panel, RECIPE)
    product = sub_skill(panel, recipe["values"]["combinedsubskill_id"])
    assert recipe["hints"] == {"combinedsubskill_id": product}
    sheet = {"table": RECIPE, "key": json.dumps(recipe["key"])}
    opened = await get(panel, "content/row", **sheet)
    material = sub_skill(panel, opened["shipped"]["materials"][0]["id"])
    assert opened["hints"][MATERIAL] == material
    told = await first_row(panel, "story_data_unit")
    assert type(told["values"]["story_id"]) is str
    assert told["hints"]["story_id"] == unit(panel, int(told["values"]["story_id"]))
    secret = await first_row(panel, "quest_secret_group_table")
    quest = masters.lookup("quest_data", "id", secret["values"]["quest_id"])
    category = masters.lookup(
        "quest_secret_category_table", "id", secret["values"]["category_id"]
    )
    assert secret["hints"] == {
        "quest_id": quest["name"],
        "category_id": category["full_title"],
    }
    group = {"table": "story_data_group", "key": json.dumps({"id": 1})}
    unlocked = await get(panel, "content/row", **group)
    assert unlocked["shipped"]["need_quest_id"] == 0
    assert "need_quest_id" not in unlocked["hints"]
    found = await get(panel, "content/rows", table=RECIPE, q=product)
    assert recipe["key"] in [row["key"] for row in found["rows"]]
    await translate(panel, tmp_path, {product: "生命强化"})
    opened = await get(panel, "content/row", **sheet)
    assert opened["hints"]["combinedsubskill_id"] == opened["title"] == "生命强化"


async def test_a_row_without_a_name_goes_by_what_it_is_about(panel):
    recipe = await first_row(panel, RECIPE)
    product = recipe["values"]["combinedsubskill_id"]
    assert not catalog.row_title(recipe["values"], lambda row, name: "")
    assert recipe["title"] == sub_skill(panel, product)
    grown = await first_row(panel, "event_character_growth")
    assert grown["title"] == unit(panel, grown["values"]["card_id"])
    limited = await first_row(panel, "item_limit_table")
    assert limited["title"] == limited["hints"]["item_rid"] != ""
    kept = recipe["values"]["materials"][0]["id"]
    body = {"table": RECIPE, "key": recipe["key"], "fields": {MATERIAL: product}}
    await post(panel, "content/row", body)
    changes = (await get(panel, "content/changes"))["changes"]
    names = {"shipped": sub_skill(panel, kept), "value": sub_skill(panel, product)}
    assert [(row["path"], row["hints"], row["title"]) for row in changes] == [
        (MATERIAL, names, names["value"])
    ]


async def test_every_registered_reference_names_something(panel):
    console = panel.app[CONSOLE]
    content, named = console.content, console.names.reading()
    offered = {row["name"] for row in content.tables()}
    assert set(naming.REFERENCES) | set(naming.SUBJECTS) <= offered
    kinds = {kind for ways in naming.REFERENCES.values() for kind in ways.values()}
    assert kinds == set(naming.PREFIXES) | set(naming.LISTED)
    for table, ways in naming.REFERENCES.items():
        _, shipped, _ = content._open(table)
        unseen = set(ways)
        for row in shipped:
            told = hints(table, content._shown(table, row), named)
            unseen -= {POSITION.sub("", path) for path, _ in told}
            if not unseen:
                break
        assert not unseen, table
    for table in naming.SUBJECTS:
        _, shipped, _ = content._open(table)
        nameless = (row for row in shipped if not catalog.row_title(row, named.shown))
        assert any(named.title(table, row) for row in nameless), table
    for value in (0, "", True, 1.5, None, 999999999):
        assert named.referred("quest_data", "needQuestIndex", value) == ""
    assert named.referred("quest_data", "id", 10100) == ""
    assert named.referred("payment_data", "productId", "unnumbered") == ""


def unlabeled(node, found: set) -> None:
    if isinstance(node, dict):
        for name, value in node.items():
            if catalog.field_label(name) == name:
                found.add(name)
            unlabeled(value, found)
    elif isinstance(node, list):
        for value in node:
            unlabeled(value, found)


async def test_every_table_and_field_the_console_shows_has_a_label(panel):
    content = panel.app[CONSOLE].content
    tables = (await get(panel, "content"))["tables"]
    assert not [row["name"] for row in tables if row["label"] == row["name"]]
    found = set()
    for table in tables:
        _, shipped, _ = content._open(table["name"])
        for row in shipped:
            unlabeled(content._shown(table["name"], row), found)
    assert not found
