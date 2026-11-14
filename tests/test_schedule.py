from copy import deepcopy
import json
import logging
from pathlib import Path
import re

import pytest

from monmusu_ps.app import RUNTIME
from monmusu_ps.core.schedule import TABLES
from test_battle import NOW, runtime, state
from test_quest_gates import master, rejection, start
from test_server import http


__all__ = ["http", "runtime", "state"]

ROOT = Path(__file__).resolve().parents[1]
FOREVER = "2999/12/31 23:59:59"
STAGE, TICKET_SHEET, BINGO, RAID_EVENT, RAID_TABLE = 10019, 423, 10, 119, 100015
ENDLESS, HUNT_CATEGORY = 50901, 5
PASS_EVENT, MISSION_PASS, MONTHLY_PASS = 139, 17, 9105
MONTHLY_POINTS = "bonus_pass.point.9101"
DAY = 86400
SNAPSHOT = "2026/11/13 12:00:00"
TICKET = ("event", "pt_sugoroku260727")
CLIENT_TYPES = {int: "int", str: "string"}


@pytest.fixture(autouse=True)
def unscheduled():
    yield


class Source:
    error = None

    def __init__(self, document):
        self.document = document

    def read(self):
        return self.document


def scheduled(runtime, *activities, rows=()):
    document = {"activities": list(activities), "rows": list(rows)}
    runtime.schedule.config = Source(document)
    runtime.schedule.reload()
    return {name: runtime.schedule.rows(name) for name in TABLES}


def activity(kind, *ids, **window):
    return {"kind": kind, "ids": list(ids), **window}


def fields(changes, table, **key):
    return next(row["fields"] for row in changes[table] if row["key"] == key)


def test_the_shipped_schedule_reopens_every_closed_mode(runtime):
    payload = runtime.schedule.payload()
    assets = {table["asset"]: table["rows"] for table in payload["tables"]}
    assert set(assets) == {
        "announce_image_data.asset",
        "gacha_data.asset",
        "sugoroku_data.asset",
        "bingo_data.asset",
        "event_list_data.asset",
        "everyones_killevent_data.asset",
        "quest_data.asset",
        "quest_secret_category_table.asset",
        "login_bonus.asset",
        "mission_data.asset",
        "wallet_template.asset",
        "banner_data.asset",
        "bonus_pass_data.asset",
        "season_pass_data.asset",
        "shop_data.asset",
        "shop_tab.asset",
    }
    assert all(set(table) == {"asset", "nest", "rows"} for table in payload["tables"])
    assert len(payload["revision"]) == 16
    for rows in assets.values():
        for row in rows:
            assert set(row) == {"key", "strings", "caches"}
            assert set(row["strings"].values()) == {FOREVER}
    masters = runtime.masters
    for stage in (10017, 10018, STAGE):
        row = masters.lookup("sugoroku_data", "stage_id", stage)
        assert masters.in_term(row["start_date"], row["end_date"], NOW)
    for quest_id in (ENDLESS, 90040):
        quest = masters.lookup("quest_data", "id", quest_id)
        assert masters.in_term(quest["openTime"], quest["closeTime"], NOW)


DUMP = ROOT / "decompile/il2cpp_dump/dump.cs"


@pytest.mark.skipif(not DUMP.exists(), reason="needs the client's IL2CPP dump")
def test_client_field_names_exist_in_the_client(runtime):
    dump = DUMP.read_text(encoding="utf-8")

    def declared_by(class_name):
        head = re.search(rf"^public class {class_name} (.*)$", dump, re.MULTILINE)
        assert head, class_name
        body = dump[head.end() : dump.index("\n}\n", head.end())]
        fields = {
            field: kind.split()[-1]
            for kind, field in re.findall(r"^\t(.+) (\w+); // 0x", body, re.MULTILINE)
        }
        parent = re.match(r": (\w+) //", head.group(1))
        return {**declared_by(parent.group(1)), **fields} if parent else fields

    client_tables = {name: table for name, table in TABLES.items() if table.row_class}
    assert set(TABLES) - set(client_tables) == {"votes"}
    for name, table in client_tables.items():
        declared = declared_by(table.row_class)
        outer = runtime.masters.base(name)["table"]
        sample = table.rows(outer)[0]
        for key in table.keys:
            assert declared[key] == CLIENT_TYPES[type(sample[key])], (name, key)
        for field, cache in table.dates.items():
            assert declared[field] == "string", (name, field)
            assert cache is None or declared[cache] == "Nullable<DateTime>", (
                name,
                cache,
            )
        holders = (
            rf"^\t\w+ {table.row_class}\[\] {table.nest or 'table'}; // 0x",
            rf"^public class \w+ : cDataTableBase<{table.row_class}> //",
        )
        assert any(re.search(holder, dump, re.MULTILINE) for holder in holders), name


def test_a_sugoroku_stage_brings_its_ticket_and_sign_in_sheet(runtime):
    changes = scheduled(runtime, activity("sugoroku", STAGE, end=FOREVER))
    assert fields(changes, "sugoroku_data", stage_id=STAGE) == {
        "end_date": FOREVER,
        "close_date": FOREVER,
    }
    category, name = TICKET
    assert fields(changes, "wallet_template", category=category, name=name) == {
        "expire_at": FOREVER
    }
    assert fields(changes, "login_bonus", id=TICKET_SHEET) == {"end_dt": FOREVER}
    assert [name for name, rows in changes.items() if rows] == [
        "sugoroku_data",
        "login_bonus",
        "wallet_template",
    ]


def test_a_window_moves_only_the_sides_it_names(runtime):
    opened = "2026/11/01 12:00:00"
    changes = scheduled(runtime, activity("sugoroku", STAGE, start=opened, end=""))
    assert fields(changes, "sugoroku_data", stage_id=STAGE) == {
        "start_date": opened,
        "end_date": "",
        "close_date": "",
    }
    category, name = TICKET
    assert fields(changes, "wallet_template", category=category, name=name) == {
        "expire_at": ""
    }
    assert fields(changes, "login_bonus", id=TICKET_SHEET) == {
        "start_dt": opened,
        "end_dt": "",
    }
    untouched = scheduled(runtime, activity("sugoroku", STAGE))
    assert not any(untouched.values())
    hidden = scheduled(runtime, activity("sugoroku", STAGE, end=FOREVER, enabled=False))
    assert not any(hidden.values())


def test_an_event_reopens_its_stages_and_its_raid(runtime):
    changes = scheduled(runtime, activity("event", RAID_EVENT, end=FOREVER))
    assert fields(changes, "event_list_data", id=RAID_EVENT) == {"close_time": FOREVER}
    assert fields(changes, "everyones_killevent_data", dataId=RAID_TABLE) == {
        "ed_date": FOREVER
    }
    stages = {row["key"]["id"] for row in changes["quest_data"]}
    assert stages == {90040, 90041, 90042, 90043}
    assert all(row["fields"] == {"closeTime": FOREVER} for row in changes["quest_data"])


def test_an_event_with_a_shop_and_missions_reopens_them(runtime):
    masters = runtime.masters
    event = next(
        row
        for row in masters.base("event_list_data")["table"]
        if row["shop_id"] and row["close_time"]
    )
    tab = next(
        row
        for row in masters.base("shop_tab")["table"]
        if row["id"] == event["shop_id"]
    )
    changes = scheduled(runtime, activity("event", event["id"], end=FOREVER))
    assert fields(changes, "shop_tab", id=tab["id"])["endDate"] == FOREVER
    currency = tab["main_draw_wallet_category"], tab["main_draw_wallet_name"]
    held = any(
        (row["category"], row["name"]) == currency
        for row in masters.base("wallet_template")["table"]
    )
    assert held == any(
        tuple(row["key"].values()) == currency for row in changes["wallet_template"]
    )
    group = next(
        row
        for row in masters.base("mission_category_collective_data")["table"]
        if row["event_id"]
    )
    missions = scheduled(runtime, activity("event", group["event_id"], end=FOREVER))
    expected = {
        row["id"]
        for row in masters.base("mission_data")["table"]
        if row["category_collective"] == group["id"] and row["endDate"]
    }
    assert {row["key"]["id"] for row in missions["mission_data"]} >= expected


def test_quests_bingo_and_hunt_categories_reopen(runtime, state):
    changes = scheduled(
        runtime,
        activity("quest", ENDLESS, end=FOREVER),
        activity("bingo", BINGO, end=FOREVER),
        activity("secret", HUNT_CATEGORY, end=FOREVER),
    )
    assert fields(changes, "quest_data", id=ENDLESS) == {"closeTime": FOREVER}
    assert fields(changes, "bingo_data", id=BINGO) == {"end_dt": FOREVER}
    assert fields(changes, "quest_secret_category_table", id=HUNT_CATEGORY) == {
        "end_at": FOREVER
    }
    targets = {
        row["id"]
        for row in runtime.masters.base("mission_data")["table"]
        if str(ENDLESS) in row["target"].split(",")
        and row["category"] == 4
        and row["condition"] in (1, 2, 29, 50, 51)
        and row["endDate"]
    }
    assert targets and {row["key"]["id"] for row in changes["mission_data"]} == targets
    master(runtime, ENDLESS).update(needQuestIndex=0, needRank=0)
    start(runtime, state, ENDLESS)
    scheduled(runtime)
    master(runtime, ENDLESS).update(needQuestIndex=0, needRank=0)
    assert rejection(lambda: start(runtime, state, ENDLESS)) == "OUT_OF_SCHEDULE"


def ended_group_with_banners(runtime):
    masters = runtime.masters
    advertised = {row["option"] for row in masters.base("banner_data")["table"]}
    return next(
        group
        for group in masters.base("gacha_data")["table"]
        if group["enclosure_id"] in advertised
        and all(
            pool["end_at"] and masters.timestamp(pool["end_at"]) < NOW
            for pool in group["enclosure"]
        )
    )


def test_a_pool_group_reopens_with_its_banners(runtime):
    group = ended_group_with_banners(runtime)
    catalog = runtime.gacha.catalog
    pools = [pool["id"] for pool in group["enclosure"]]
    assert all(catalog.ended(catalog.pool(pool), NOW) for pool in pools)
    changes = scheduled(runtime, activity("gacha", group["id"], end=FOREVER))
    assert {row["key"]["id"] for row in changes["gacha_data"]} == set(pools)
    assert all(row["fields"] == {"end_at": FOREVER} for row in changes["gacha_data"])
    banners = {
        row["id"]
        for row in runtime.masters.base("banner_data")["table"]
        if row["option"] == group["enclosure_id"] and row["endAt"]
    }
    assert banners and {row["key"]["id"] for row in changes["banner_data"]} == banners
    assert not any(catalog.ended(catalog.pool(pool), NOW) for pool in pools)
    served = {table["asset"]: table for table in runtime.schedule.payload()["tables"]}
    assert served["gacha_data.asset"]["nest"] == "enclosure"
    assert served["gacha_data.asset"]["rows"][0]["caches"] == ["endDate"]
    assert served["banner_data.asset"]["nest"] is None
    scheduled(runtime)
    assert all(catalog.ended(catalog.pool(pool), NOW) for pool in pools)


def test_a_pool_keeps_the_currencies_that_outlast_it(runtime):
    group = runtime.masters.base("gacha_data")["table"]
    stone = next(
        row
        for row in group
        if row["enclosure"][0]["cost_name"] == "stone" and row["enclosure"][0]["end_at"]
    )
    changes = scheduled(runtime, activity("gacha", stone["id"], end=FOREVER))
    kept = {tuple(row["key"].values()) for row in changes["wallet_template"]}
    assert changes["gacha_data"] and ("point", "stone") not in kept


def test_banners_and_sign_in_sheets_can_be_scheduled_alone(runtime):
    opened = "2026/11/01 12:00:00"
    changes = scheduled(
        runtime,
        activity("banner", 1748, start=opened, end=FOREVER),
        activity("loginbonus", TICKET_SHEET, end=FOREVER),
    )
    assert fields(changes, "banner_data", id=1748) == {
        "startAt": opened,
        "endAt": FOREVER,
    }
    assert fields(changes, "login_bonus", id=TICKET_SHEET) == {"end_dt": FOREVER}


def test_a_mission_pass_reopens_with_its_goods_and_missions(runtime):
    masters = runtime.masters
    catalog = runtime.bonus_passes.catalog
    scheduled(runtime)
    row = masters.lookup("bonus_pass_data", "id", MISSION_PASS)
    assert catalog.earning_by(row["ref_prog_point_rid"], NOW) is None
    changes = scheduled(runtime, activity("pass", MISSION_PASS, end=FOREVER))
    assert fields(changes, "bonus_pass_data", id=MISSION_PASS) == {"end_dt": FOREVER}
    assert fields(changes, "shop_data", id=row["ref_shop_id_dmm"]) == {
        "endDate": FOREVER
    }
    points = ("bonus_pass", "point.17")
    feeding = {
        mission["id"]
        for mission in masters.base("mission_data")["table"]
        if mission["endDate"]
        and any(
            (reward["m_walletCategory"], reward["m_walletName"]) == points
            for reward in mission["rewardtable"]["m_rewards"]
        )
    }
    assert feeding and {row["key"]["id"] for row in changes["mission_data"]} == feeding
    assert catalog.earning_by(row["ref_prog_point_rid"], NOW)["id"] == MISSION_PASS


def test_a_monthly_pass_moves_its_season_and_stays_undated(runtime):
    catalog = runtime.bonus_passes.catalog
    opened, late = "2026/11/01 05:00:00", NOW + 400 * DAY
    scheduled(runtime)
    assert catalog.earning_by(MONTHLY_POINTS, late) is None
    changes = scheduled(
        runtime, activity("pass", MONTHLY_PASS, start=opened, end=FOREVER)
    )
    assert not changes["bonus_pass_data"] and not changes["shop_data"]
    assert fields(changes, "season_pass_data", id=10) == {
        "start_dt": opened,
        "end_dt": FOREVER,
        "close_dt": FOREVER,
    }
    assert catalog.earning_by(MONTHLY_POINTS, late)["id"] == MONTHLY_PASS


def test_an_event_brings_its_missions_pass_and_banners(runtime):
    masters = runtime.masters
    changes = scheduled(runtime, activity("event", PASS_EVENT, end=FOREVER))
    grouped = {
        mission["id"]
        for mission in masters.base("mission_data")["table"]
        if mission["category_collective"] == 239
    }
    assert len(grouped) == 79
    assert grouped <= {row["key"]["id"] for row in changes["mission_data"]}
    assert fields(changes, "bonus_pass_data", id=MISSION_PASS) == {"end_dt": FOREVER}
    assert not changes["season_pass_data"]
    event = masters.lookup("event_list_data", "id", PASS_EVENT)
    banners = {
        row["id"]
        for row in masters.base("banner_data")["table"]
        if row["option"] == event["tag"] and row["endAt"]
    }
    assert banners and {row["key"]["id"] for row in changes["banner_data"]} == banners
    assert fields(changes, "wallet_template", category="event", name="pt_ev000110")


def test_rows_behind_an_entrance_are_never_cut_short(runtime):
    early = "2026/11/10 00:00:00"
    changes = scheduled(runtime, activity("event", PASS_EVENT, end=early))
    assert fields(changes, "event_list_data", id=PASS_EVENT) == {"close_time": early}
    assert fields(changes, "quest_data", id=71740) == {"closeTime": early}
    assert not changes["shop_tab"] and not changes["wallet_template"]


def test_a_vote_follows_the_schedule_on_the_server_only(runtime):
    campaign = runtime.config.read()["votes"]["campaigns"][0]
    assert runtime.votes.summaries(NOW)
    later = "2030/01/01 00:00:00"
    changes = scheduled(runtime, activity("vote", 1, start=later, end=FOREVER))
    assert fields(changes, "votes", campaign_id=1, round_no=1) == {
        "start_at": later,
        "end_at": FOREVER,
    }
    assert runtime.votes.summaries(NOW) is None
    assert campaign["start_at"] != later
    assets = [table["asset"] for table in runtime.schedule.payload()["tables"]]
    assert assets == []
    row = {
        "table": "votes",
        "key": {"campaign_id": 1, "round_no": 1},
        "fields": {"end_at": "2026/01/01 00:00:00"},
    }
    scheduled(runtime, rows=[row])
    assert runtime.votes.summaries(NOW) is None
    scheduled(runtime)
    assert runtime.votes.summaries(NOW)


def test_the_schedule_follows_its_other_sources(runtime, monkeypatch, caplog):
    scheduled(runtime, activity("vote", 1, end=FOREVER))
    assert runtime.schedule.rows("votes")
    settings = deepcopy(runtime.config.read())
    settings["votes"]["campaigns"][0]["campaign_id"] = 2
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    with caplog.at_level(logging.ERROR, logger="monmusu.schedule"):
        runtime.schedule.reload()
    assert "missing vote" in caplog.text
    scheduled(runtime, activity("bingo", BINGO, end=FOREVER))
    replaced = deepcopy(runtime.masters.base("bingo_data"))
    replaced["table"] = replaced["table"][:-1]
    original = runtime.masters.base
    monkeypatch.setattr(
        runtime.masters,
        "base",
        lambda name: replaced if name == "bingo_data" else original(name),
    )
    with caplog.at_level(logging.ERROR, logger="monmusu.schedule"):
        runtime.schedule.reload()
    assert "missing bingo_data" in caplog.text
    assert runtime.masters.document("bingo_data")["table"] == replaced["table"]


def test_entries_can_keep_whatever_was_running(runtime):
    masters = runtime.masters
    moment = masters.timestamp(SNAPSHOT)
    entry = {"kind": "gacha", "running_at": SNAPSHOT, "end": FOREVER}
    changes = scheduled(runtime, entry)
    kept = {
        pool["id"]
        for group in masters.base("gacha_data")["table"]
        for pool in group["enclosure"]
        if pool["end_at"] and masters.in_term(pool["start_at"], pool["end_at"], moment)
    }
    assert kept and {row["key"]["id"] for row in changes["gacha_data"]} == kept
    catalog = runtime.gacha.catalog
    assert not any(catalog.ended(catalog.pool(pool), NOW + 400 * DAY) for pool in kept)
    seasons = scheduled(
        runtime, {"kind": "pass", "running_at": SNAPSHOT, "end": FOREVER}
    )
    assert [row["key"] for row in seasons["season_pass_data"]] == [{"id": 10}]
    assert not seasons["bonus_pass_data"]


def test_plain_rows_and_shop_tabs_can_be_scheduled(runtime):
    changes = scheduled(
        runtime,
        activity("shop_tab", 2101, end=FOREVER),
        activity("goods", 200522, end=FOREVER),
        activity("mission", 52579, end=FOREVER),
        activity("announce", 636, end=""),
    )
    assert fields(changes, "shop_tab", id=2101) == {"endDate": FOREVER}
    assert fields(changes, "wallet_template", category="event", name="pt_ev000110")
    assert fields(changes, "shop_data", id=200522) == {"endDate": FOREVER}
    assert fields(changes, "mission_data", id=52579) == {"endDate": FOREVER}
    assert fields(changes, "announce_image_data", id=636) == {"end_date": ""}


def test_every_kind_lists_its_entrances(runtime):
    schedule = runtime.schedule
    assert set(schedule.kinds) == {
        "sugoroku",
        "bingo",
        "event",
        "quest",
        "secret",
        "gacha",
        "pass",
        "shop_tab",
        "vote",
        "banner",
        "loginbonus",
        "goods",
        "mission",
        "announce",
    }
    for kind in schedule.kinds:
        listed = schedule.entrances(kind)
        assert listed and all(
            set(row) == {"id", "name", "start", "end"} for row in listed
        )
        assert len({row["id"] for row in listed}) == len(listed), kind
    monthly = next(row for row in schedule.entrances("pass") if row["id"] == 9105)
    assert monthly["end"] == "2026/11/28 23:59:59"
    assert schedule.entrances("vote") == [
        {
            "id": 1,
            "name": "",
            "start": "2022/03/14 13:00:00",
            "end": "2099/12/31 04:59:59",
        }
    ]


def test_the_last_rejection_is_kept_for_display(runtime):
    scheduled(runtime, activity("bingo", BINGO, end=FOREVER))
    assert runtime.schedule.error is None
    scheduled(runtime, activity("bingo", 99, end=FOREVER))
    assert "missing bingo_data" in runtime.schedule.error
    scheduled(runtime)
    assert runtime.schedule.error is None


def test_rows_can_be_named_directly(runtime):
    row = {
        "table": "shop_tab",
        "key": {"id": 2101},
        "fields": {"endDate": FOREVER, "view_endDate": ""},
    }
    changes = scheduled(runtime, rows=[row])
    assert fields(changes, "shop_tab", id=2101) == row["fields"]
    assert runtime.masters.lookup("shop_tab", "id", 2101)["endDate"] == FOREVER


@pytest.mark.parametrize(
    "broken",
    [
        {"activities": [activity("sugoroku", STAGE, end="2999-12-31")], "rows": []},
        {
            "activities": [activity("sugoroku", STAGE, end="2999/1/1 0:00:00")],
            "rows": [],
        },
        {"activities": [activity("sugoroku", STAGE, end=5)], "rows": []},
        {"activities": [activity("sugoroku", 1, end=FOREVER)], "rows": []},
        {"activities": [activity("parade", STAGE, end=FOREVER)], "rows": []},
        {"activities": [activity("gacha", "gacha.missing", end=FOREVER)], "rows": []},
        {"activities": [activity("vote", 99, end=FOREVER)], "rows": []},
        {"activities": [activity("pass", 0, end=FOREVER)], "rows": []},
        {"activities": [{"kind": "sugoroku", "end": FOREVER}], "rows": []},
        {
            "activities": [{"kind": "gacha", "running_at": "", "end": FOREVER}],
            "rows": [],
        },
        {
            "activities": [
                {"kind": "bingo", "ids": [10], "running_at": SNAPSHOT, "end": FOREVER}
            ],
            "rows": [],
        },
        {"activities": []},
        {
            "activities": [],
            "rows": [
                {"table": "shop_tab", "key": {"id": 2101}, "fields": {"name": "x"}}
            ],
        },
        {
            "activities": [],
            "rows": [{"table": "shop_tab", "key": {"no": 2101}, "fields": {}}],
        },
        {
            "activities": [],
            "rows": [{"table": "unit_rarity", "key": {"id": 82}, "fields": {}}],
        },
    ],
)
def test_a_broken_schedule_keeps_the_last_good_one(runtime, caplog, broken):
    good = scheduled(runtime, activity("bingo", BINGO, end=FOREVER))
    runtime.schedule.config = Source(broken)
    with caplog.at_level(logging.ERROR, logger="monmusu.schedule"):
        runtime.schedule.reload()
    assert "Schedule rejected" in caplog.text
    assert runtime.schedule.rows("bingo_data") == good["bingo_data"]
    assert runtime.masters.lookup("bingo_data", "id", BINGO)["end_dt"] == FOREVER


def test_master_tables_follow_the_schedule(runtime):
    masters = runtime.masters
    scheduled(runtime)
    plain = masters.document("bingo_data")
    assert plain is masters.base("bingo_data")
    original = masters.lookup("bingo_data", "id", BINGO)["end_dt"]
    scheduled(runtime, activity("bingo", BINGO, end=FOREVER))
    moved = masters.document("bingo_data")
    assert moved is not plain and moved is masters.document("bingo_data")
    assert masters.lookup("bingo_data", "id", BINGO)["end_dt"] == FOREVER
    assert masters.base("bingo_data")["table"][-1]["end_dt"] == original
    assert masters.document("unit_rarity") is masters.base("unit_rarity")
    scheduled(runtime)
    assert masters.lookup("bingo_data", "id", BINGO)["end_dt"] == original


def test_the_schedule_is_reread_when_its_file_changes(runtime):
    first = runtime.schedule.payload()["revision"]
    document = json.loads(json.dumps(runtime.schedule.config.read()))
    document["activities"] = document["activities"][:1]
    runtime.schedule.config = Source(document)
    assert runtime.schedule.rows("bingo_data")
    assert runtime.schedule.payload()["revision"] != first
    assert not runtime.schedule.rows("bingo_data")


async def test_the_plugin_reads_the_schedule_over_http(http):
    response = await http.get("/masters/patch")
    assert response.status == 200
    served = await response.json()
    assert served == http.app[RUNTIME].patch.payload()
    assets = [table["asset"] for table in served["tables"]]
    assert assets == sorted(assets)
    stages = served["tables"][assets.index("sugoroku_data.asset")]
    assert (stages["rows"], stages["key"], stages["late"]) == (
        ["table"],
        ["stage_id"],
        False,
    )
    caches = ["startDateTime", "endDateTime", "closeDateTime"]
    assert stages["changes"][0] == {
        "at": [10017],
        "set": [
            {"path": ["end_date"], "value": FOREVER, "forget": caches},
            {"path": ["close_date"], "value": FOREVER, "forget": caches},
        ],
    }
    wallets = served["tables"][assets.index("wallet_template.asset")]
    assert wallets["key"] == ["category", "name"]
    assert ["event", "pt_sugoroku260727"] in [row["at"] for row in wallets["changes"]]
    shipped = await (
        await http.get("/masters/patch", params={"dates": "shipped"})
    ).json()
    assert shipped["tables"] == [] and shipped["revision"] != served["revision"]


def test_reopened_stages_pass_the_servers_own_date_checks(runtime, state):
    runtime.config.read()["sugoroku"].update(
        enforce_master_dates=True, charge_tickets=False
    )
    row = runtime.masters.base("sugoroku_data")["table"][-1]
    version = runtime.sugoroku.stages.stage(row).version
    payload = {
        "stage_id": STAGE,
        "stage_file_ver": version,
        "game_lv": 1,
        "cost_batch": 1,
        "piece_chara_id": 11,
    }
    scheduled(runtime)
    with pytest.raises(ValueError):
        runtime.sugoroku.start(state, payload, NOW)
    scheduled(runtime, activity("sugoroku", STAGE, end=FOREVER))
    assert runtime.sugoroku.start(state, payload, NOW).data["txid"]
