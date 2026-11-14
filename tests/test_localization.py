import json

from aiohttp.test_utils import TestClient, TestServer

from monmusu_l10n import scenes
from monmusu_l10n.markup import JAPANESE
from monmusu_l10n.memory import Memory
from monmusu_l10n.scenes import Line, Scene
from monmusu_ps.app import RUNTIME
from monmusu_ps.core.localization import sheet
from test_overrides import GOODS, runtime, state, unscheduled
from test_schedule import Source


__all__ = ["runtime", "state", "unscheduled"]

CHINESE = {"localization": {"language": "zh-Hans"}}
TOLD = [
    Scene(
        "scenario:character/1/A0_Nml", "", (Line("リン", "おはよう"), Line("", "朝だ"))
    ),
    Scene("scenario_r18:character/1/A1_R18#続き", "", (Line("リン", "こんばんは"),)),
]
SPOKEN = [("おはよう", "早上好"), ("こんばんは", "晚上好"), ("リン", "琳")]


def translated(runtime, pairs, told=(), settings=CHINESE):
    workspace = runtime.localization.workspace
    with Memory(workspace / "zh-Hans.jsonl") as memory:
        memory.record(pairs)
    scenes.save(told, workspace / "scenes.jsonl")
    runtime.localization.config = Source(settings)


def written(runtime) -> dict:
    return {
        (table.get("asset", table.get("folder")), *change["at"], *value["path"]): value[
            "value"
        ]
        for table in runtime.patch.payload()["tables"]
        for change in table["changes"]
        for value in change["set"]
    }


def test_the_game_is_handed_translations_the_server_does_not_read(runtime):
    name = runtime.masters.lookup("shop_data", "id", GOODS)["name"]
    assert runtime.patch.payload()["tables"] == []
    translated(runtime, [(name, "测试商品")])
    assert written(runtime)["shop_data.asset", GOODS, "name"] == "测试商品"
    assert runtime.masters.lookup("shop_data", "id", GOODS)["name"] == name


def test_readings_and_lookup_keys_stay_as_shipped(runtime):
    skill = next(
        row for row in runtime.masters.table("attach_ability_data") if row["kana"]
    )
    term = runtime.masters.table("detail_text_data")[0]
    translated(
        runtime, [(skill["kana"], "读音"), (term["id"], "键"), (term["text"], "说明")]
    )
    values = written(runtime)
    assert values["detail_text_data.asset", term["id"], "text"] == "说明"
    assert not [place for place in values if place[-1] in ("kana", "id")]


def test_what_the_game_compares_stays_as_shipped(runtime):
    monthly = next(
        row
        for row in runtime.masters.table("bonus_pass_data")
        if row["unlock_item_name"]
    )
    rarity = runtime.masters.table("gear_rarity")[0]
    translated(
        runtime,
        [(monthly["unlock_item_name"], "月度通行证"), (rarity["name"], "普通")],
    )
    kept = ("unlock_item_name", "name")
    assert not [
        place
        for place in written(runtime)
        if place[0] in ("bonus_pass_data.asset", "gear_rarity.asset")
        and place[-1] in kept
    ]


def test_without_a_language_the_game_stays_in_japanese(runtime):
    name = runtime.masters.lookup("shop_data", "id", GOODS)["name"]
    translated(runtime, [(name, "测试商品")], TOLD, settings={})
    assert runtime.patch.payload()["tables"] == []
    assert (runtime.localization.names(), runtime.localization.scene("A0_Nml")) == (
        {},
        {},
    )


def test_a_scene_is_handed_over_by_the_name_of_its_sheet(runtime):
    translated(runtime, SPOKEN, TOLD)
    assert runtime.localization.scene("A0_Nml") == {"おはよう": "早上好"}
    assert runtime.localization.scene("続き") == {"こんばんは": "晚上好"}
    assert runtime.localization.scene("A1_R18") == {}
    assert runtime.localization.names() == {"リン": "琳"}
    assert sheet("scenario:main/1100/1100011Nml") == "1100011Nml"


def test_the_patch_is_written_out_once_until_a_translation_changes(runtime):
    name = runtime.masters.lookup("shop_data", "id", GOODS)["name"]
    translated(runtime, [(name, "测试商品")])
    first = runtime.patch.encoded()
    assert runtime.patch.encoded() is first
    assert json.loads(first) == runtime.patch.payload()
    translated(runtime, [(name, "另一个名字")])
    assert written(runtime)["shop_data.asset", GOODS, "name"] == "另一个名字"
    assert runtime.patch.encoded() is not first


async def test_translations_are_served_to_the_game(game_app):
    app = game_app()
    translated(app[RUNTIME], SPOKEN, TOLD)
    async with TestClient(TestServer(app)) as client:
        names = await (await client.get("/localization/names")).json()
        scene = await (await client.get("/localization/scenes/続き")).json()
        absent = await (await client.get("/localization/scenes/absent")).json()
    assert names["names"] == {"リン": "琳"}
    assert names["cards"]["10001"] == [[1, "エテジア"]]
    assert (scene, absent) == ({"lines": {"こんばんは": "晚上好"}}, {"lines": {}})


def test_icon_names_the_game_filters_by_stay_as_shipped(runtime):
    wallet = next(
        row
        for row in runtime.masters.table("wallet_template")
        if row["wallet_name"] == "フレンドポイント"
    )
    tab = next(
        row
        for row in runtime.masters.table("shop_tab")
        if row["tab_name"] == "フレンドポイント"
    )
    translated(runtime, [("フレンドポイント", "好友点数")])
    values = written(runtime)
    assert ("wallet_template.asset", wallet["id"], "wallet_name") not in values
    assert values["shop_tab.asset", tab["id"], "tab_name"] == "好友点数"


def test_units_enemies_and_tokens_are_named_in_the_patch(runtime):
    unit = runtime.masters.asset("unitcard", 1)
    enemy = next(
        card
        for card in runtime.masters.assets("enemydata")
        if JAPANESE.search(card["nickname"])
    )
    translated(
        runtime,
        [(unit["charaName"], "艾特吉亚"), (enemy["nickname"], "敌人称号")],
    )
    folders = {
        table["folder"]: table
        for table in runtime.patch.payload()["tables"]
        if "folder" in table
    }
    assert set(folders) == {"UnitCard", "EnemyData"}
    assert folders["UnitCard"]["count"] == len(runtime.masters.assets("unitcard"))
    assert folders["UnitCard"]["forget"] == []
    unit_change = next(
        change for change in folders["UnitCard"]["changes"] if change["at"] == [1]
    )
    assert unit_change["set"] == [
        {"path": ["charaName"], "value": "艾特吉亚", "forget": ["trait_id"]}
    ]
    assert runtime.masters.asset("unitcard", 1)["charaName"] == unit["charaName"]
    assert runtime.localization.cards()[str(unit["resource"])] == [
        [1, unit["charaName"]]
    ]


def test_documents_without_a_row_table_are_handed_over(runtime):
    faq = runtime.masters.base("faq_data")["documents"][0]
    attribute = runtime.masters.base("attr_list")["m_table"][1]
    translated(
        runtime, [(faq["title"], "常见问题"), (attribute["m_attr_name"], "属性")]
    )
    values = written(runtime)
    assert values["faq_data.asset", faq["id"], "title"] == "常见问题"
    assert values["attr_list.asset", attribute["m_id"], "m_attr_name"] == "属性"


def test_mail_titles_are_handed_over_in_the_language(runtime, state):
    title = "ミッション達成報酬"
    translated(runtime, [(title, "任务达成奖励")])
    letter = runtime.mailbox.post(
        state, {"rid": "wallet.point.gold", "val": 1}, "test", 0, title
    )
    payload = {"inbox_name": letter["inbox"], "order": 1}
    told = runtime.inbox.letters(state, payload, 0)
    assert [row["message"] for row in told] == ["任务达成奖励"]
    assert state["inbox"][0]["message"] == title
    assert state["inbox"][0] is not told[0]
