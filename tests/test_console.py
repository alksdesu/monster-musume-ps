import asyncio
import base64
import json
import logging

from aiohttp.test_utils import TestClient, TestServer
import pytest

from monmusu_ps.app import CONSOLE, RUNTIME
from monmusu_ps.config import RuntimeConfig
from monmusu_ps.console.configs import FILES, compare
from monmusu_ps.console.service import RESTART_CODE, Service
from monmusu_ps.core.sync import Outbox, merged
from test_server import NativeClient


NOW = 1_794_340_000
FOREVER = "2999/12/31 23:59:59"
PROFILE, OTHER = "dmm:tester", "dmm:other"
GOLD = "wallet.point.gold"
BINGO = {"name": "bingo", "kind": "bingo", "ids": [10], "end": FOREVER}


@pytest.fixture(autouse=True)
def unscheduled():
    yield


@pytest.fixture
async def panel(game_app, folders):
    app = game_app(clock=lambda: NOW)
    runtime = app[RUNTIME]
    for profile in (PROFILE, OTHER):
        async with runtime.store.transaction(profile):
            pass
    async with TestClient(TestServer(app)) as client:
        client.folders = folders
        yield client


def runtime_of(panel):
    return panel.app[RUNTIME]


async def get(panel, path, expect=200, **query):
    response = await panel.get(f"/console/api/{path}", params=query)
    assert response.status == expect, await response.text()
    return await response.json()


async def post(panel, path, body, expect=200):
    response = await panel.post(f"/console/api/{path}", json=body)
    assert response.status == expect, await response.text()
    return await response.json()


def gold(state):
    return next(row["val"] for row in state["wallets"] if row["name"] == "gold")


async def test_the_console_only_answers_its_own_address(panel):
    assert (await panel.get("/console/")).status == 404
    foreign = await panel.get("/console/api/status", headers={"Host": "evil.example"})
    assert foreign.status == 403
    cross = await panel.post(
        "/console/api/service",
        json={"action": "pause"},
        headers={"Origin": "http://evil.example"},
    )
    assert cross.status == 403
    form = await panel.post("/console/api/service", data={"action": "pause"})
    assert form.status == 415
    assert not panel.app[CONSOLE].service.paused


async def test_status_reports_the_running_server(panel):
    status = await get(panel, "status")
    assert status["endpoints"] == status["contracts"] == 163
    assert status["paused"] is False and status["online"] == []
    assert set(status["problems"].values()) == {None}
    assert set(status["problems"]) == {"features", "settings", "schedule", "overrides"}
    assert status["now"] == NOW


async def test_pausing_stops_the_game_but_not_the_console(panel):
    assert (await post(panel, "service", {"action": "pause"}))["paused"] is True
    assert (await panel.get("/api/player/get")).status == 503
    assert (await panel.post("/api/sdk/request", json={})).status == 503
    assert (await panel.get("/health")).status == 200
    assert (await post(panel, "service", {"action": "resume"}))["paused"] is False
    assert (await panel.get("/api/no/such/endpoint")).status == 404
    await post(panel, "service", {"action": "dance"}, expect=400)
    await post(panel, "service", {}, expect=400)


async def test_leaving_keeps_the_exit_code_for_the_launcher(panel, monkeypatch):
    left = []
    monkeypatch.setattr(Service, "_leave", staticmethod(lambda: left.append(True)))
    service = panel.app[CONSOLE].service
    await post(panel, "service", {"action": "restart"})
    assert service.exit_code == RESTART_CODE and not left
    await asyncio.sleep(0.5)
    assert left == [True]
    await post(panel, "service", {"action": "stop"})
    assert service.exit_code == 0


async def test_recent_log_lines_are_kept_in_memory(panel):
    logging.getLogger("monmusu.test").warning("console %s", "probe")
    logged = await get(panel, "logs", after=0)
    probe = next(row for row in logged["rows"] if row["message"] == "console probe")
    assert probe["level"] == "WARNING" and probe["logger"] == "monmusu.test"
    assert logged["latest"] >= probe["seq"]
    later = await get(panel, "logs", after=logged["latest"])
    assert later["rows"] == [] and later["latest"] >= logged["latest"]


async def test_saves_are_listed_backed_up_and_restored(panel):
    runtime = runtime_of(panel)
    listed = await get(panel, "saves")
    assert {row["profile"] for row in listed["saves"]} == {PROFILE, OTHER}
    assert listed["backups"] == []
    row = next(row for row in listed["saves"] if row["profile"] == PROFILE)
    assert row["folder"] == runtime.store.path(PROFILE).parent.name
    assert row["online"] is False and row["logins"] == []
    name = (await post(panel, "saves/backup", {"label": "first"}))["name"]
    assert name.startswith("console-") and name.endswith("-first")
    await post(panel, "saves/backup", {"label": "no spaces"}, expect=400)
    before = gold(await runtime.store.read(PROFILE))
    await post(panel, "player/balance", {"profile": PROFILE, "rid": GOLD, "value": 77})
    session = runtime.sessions.create(PROFILE)
    assert (await get(panel, "saves"))["saves"][0]["online"] in (True, False)
    kept = await post(panel, "saves/restore", {"backup": name, "folder": row["folder"]})
    assert kept["kept"].endswith("-before-restore")
    assert gold(await runtime.store.read(PROFILE)) == before
    with pytest.raises(KeyError):
        runtime.sessions.get(session.token)
    assert runtime.outbox.collect(PROFILE, None) is None
    backups = (await get(panel, "saves"))["backups"]
    assert {backup["name"] for backup in backups} == {name, kept["kept"]}
    assert {save["profile"] for save in backups[0]["saves"]} == {PROFILE, OTHER}
    await post(
        panel, "saves/restore", {"backup": "missing", "folder": row["folder"]}, 404
    )
    await post(panel, "saves/restore", {"backup": "..", "folder": "x"}, expect=400)


async def test_a_backup_from_a_newer_server_is_refused(panel):
    runtime = runtime_of(panel)
    name = (await post(panel, "saves/backup", {"label": ""}))["name"]
    folder = runtime.store.path(PROFILE).parent.name
    path = panel.folders.backups / name / folder / "state.json"
    document = json.loads(path.read_text("utf-8"))
    document["schema_version"] = runtime.store.schema_version + 1
    path.write_text(json.dumps(document), "utf-8")
    await post(panel, "saves/restore", {"backup": name, "folder": folder}, expect=400)
    other = runtime.store.path(OTHER).parent.name
    document["schema_version"] -= 1
    (panel.folders.backups / name / other / "state.json").write_text(
        json.dumps(document), "utf-8"
    )
    await post(panel, "saves/restore", {"backup": name, "folder": other}, expect=400)


async def test_a_login_can_be_pointed_at_another_save(panel):
    runtime = runtime_of(panel)
    session = runtime.sessions.create(PROFILE, PROFILE)
    await post(panel, "saves/bind", {"login": PROFILE, "profile": OTHER})
    assert runtime.data_links.resolve(PROFILE) == OTHER
    with pytest.raises(KeyError):
        runtime.sessions.get(session.token)
    rows = {row["profile"]: row for row in (await get(panel, "saves"))["saves"]}
    assert rows[OTHER]["logins"] == [PROFILE] and rows[PROFILE]["rebound_to"] == OTHER
    await post(panel, "saves/bind", {"login": PROFILE, "profile": PROFILE})
    assert runtime.data_links.resolve(PROFILE) == PROFILE
    await post(panel, "saves/bind", {"login": PROFILE, "profile": "dmm:nobody"}, 404)


async def test_saves_are_created_emptied_of_clients_and_deleted(panel):
    runtime = runtime_of(panel)
    await post(panel, "saves/create", {"profile": "dmm:fresh"})
    fresh = await runtime.store.read("dmm:fresh")
    assert fresh["player"]["tutorial"] == 0 and len(fresh["player"]["player_code"]) == 8
    await post(panel, "saves/create", {"profile": "dmm:fresh"}, expect=400)
    await post(panel, "saves/create", {"profile": "no colon"}, expect=400)
    await post(panel, "saves/create", {"profile": 5}, expect=400)
    session = runtime.sessions.create("dmm:fresh")
    await post(panel, "saves/kick", {"profile": "dmm:fresh"})
    with pytest.raises(KeyError):
        runtime.sessions.get(session.token)
    await post(panel, "saves/kick", {"profile": "dmm:nobody"}, expect=404)
    runtime.sessions.create("dmm:fresh")
    runtime.outbox.post("dmm:fresh", {"wallet": []})
    kept = (await post(panel, "saves/delete", {"profile": "dmm:fresh"}))["kept"]
    assert kept.endswith("-before-delete")
    assert not runtime.store.exists("dmm:fresh")
    assert not runtime.store.path("dmm:fresh").parent.exists()
    assert runtime.sessions.active() == []
    assert runtime.outbox.collect("dmm:fresh", None) is None
    saved = panel.folders.backups / kept / runtime.store.path("dmm:fresh").parent.name
    assert (saved / "state.json").exists()
    profiles = {row["profile"] for row in (await get(panel, "saves"))["saves"]}
    assert profiles == {PROFILE, OTHER}
    await post(panel, "saves/delete", {"profile": "dmm:fresh"}, expect=404)


async def test_shared_save_files_are_edited_with_the_same_care(panel):
    runtime = runtime_of(panel)
    listed = (await get(panel, "shared-files"))["files"]
    assert listed == [
        "platform.json",
        "social.json",
        "data_links.json",
        "credentials.json",
    ]
    empty = await get(panel, "shared-file", name="social.json")
    assert empty["text"] == "{}" and empty["versions"] == [] and empty["error"] is None
    assert runtime.social.graph.edges() == []
    edge = {"source": "AAAAAAAA", "target": "BBBBBBBB", "followed_at": NOW}
    document = {"follows": [edge], "tallies": {}}
    body = {"name": "social.json", "text": json.dumps(document)}
    written = await post(panel, "shared-file", body)
    assert json.loads(written["text"]) == document and written["versions"] == []
    assert runtime.social.graph.edges() == [edge]
    wrong = {**body, "text": json.dumps({"follows": {}, "tallies": {}})}
    reply = await post(panel, "shared-file", wrong, expect=400)
    assert "$.follows has to stay a list" in reply["error"]
    lacking = {**body, "text": json.dumps({"follows": []})}
    assert (await post(panel, "shared-file", lacking, expect=409))["missing"] == [
        "$.tallies"
    ]
    emptied = await post(panel, "shared-file", {**lacking, "allow_missing": True})
    version = emptied["versions"][0]["version"]
    kept = await get(panel, "shared-file/version", name="social.json", version=version)
    assert json.loads(kept["text"]) == document
    restored = await post(
        panel, "shared-file/restore", {"name": "social.json", "version": version}
    )
    assert json.loads(restored["text"]) == document
    assert runtime.social.graph.edges() == [edge]
    await get(panel, "shared-file", name="state.json", expect=404)
    await get(panel, "config", name="platform.json", expect=404)


async def test_player_sections_can_be_read_and_replaced(panel):
    runtime = runtime_of(panel)
    summary = await get(panel, "player", profile=PROFILE)
    assert summary["player"]["player_code"]
    kinds = {row["name"]: row for row in summary["sections"]}
    assert kinds["wallets"]["synced"] and not kinds["inbox"]["synced"]
    wallets = (await get(panel, "player/section", profile=PROFILE, name="wallets"))[
        "value"
    ]
    assert wallets == summary["wallets"]
    for row in wallets:
        if row["name"] == "gold":
            row["val"] = 4242
    body = {"profile": PROFILE, "name": "wallets", "value": wallets}
    outcome = await post(panel, "player/section", body)
    assert outcome == {"synced": True, "dropped": False, "unsynced": []}
    assert gold(await runtime.store.read(PROFILE)) == 4242
    pending = runtime.outbox.collect(PROFILE, None)
    assert [row["val"] for row in pending["wallet"][0]["data"]] == [4242]
    assert runtime.outbox.collect(PROFILE, None) is None
    await post(panel, "player/section", {**body, "value": {}}, expect=400)
    await post(panel, "player/section", {**body, "name": "nothing"}, expect=404)
    await post(panel, "player/section", {**body, "value": [{"val": 1}]}, expect=404)
    assert gold(await runtime.store.read(PROFILE)) == 4242
    await get(panel, "player", profile="dmm:nobody", expect=404)
    await get(panel, "player", expect=400)


async def test_console_edits_reach_the_client_with_its_next_answer(panel):
    client = NativeClient(panel)
    await client.login()
    await client.call("player/init", {"_z0": 0})
    profile = runtime_of(panel).sessions.active()[0].profile
    assert (await get(panel, "status"))["online"] == [profile]
    body = {"profile": profile, "rid": GOLD, "value": 31337}
    assert (await post(panel, "player/balance", body))["synced"] is True
    modules = {"player_module_ids": ["stamina"]}
    answer = await client.call("player/get", modules)
    wallets = [row for batch in answer["sync"]["wallet"] for row in batch["data"]]
    assert [row["val"] for row in wallets if row["name"] == "gold"] == [31337]
    assert "wallet" not in ((await client.call("player/get", modules))["sync"] or {})


async def test_removed_rows_and_unsynced_sections_are_pointed_out(panel):
    units = (await get(panel, "player/section", profile=PROFILE, name="units"))["value"]
    body = {"profile": PROFILE, "name": "units", "value": units[:-1]}
    assert (await post(panel, "player/section", body))["dropped"] is True
    body = {"profile": PROFILE, "name": "home_visited_at", "value": NOW}
    outcome = await post(panel, "player/section", body)
    assert outcome == {
        "synced": False,
        "dropped": False,
        "unsynced": ["home_visited_at"],
    }


async def test_balances_can_be_set_either_way(panel):
    runtime = runtime_of(panel)
    body = {"profile": PROFILE, "rid": GOLD, "value": 900}
    assert (await post(panel, "player/balance", body))["synced"] is True
    assert gold(await runtime.store.read(PROFILE)) == 900
    await post(panel, "player/balance", {**body, "value": 5})
    assert gold(await runtime.store.read(PROFILE)) == 5
    await post(panel, "player/balance", {**body, "value": -1}, expect=400)
    await post(panel, "player/balance", {**body, "value": 1.5}, expect=400)
    await post(panel, "player/balance", {**body, "rid": "wallet.no.such"}, expect=400)


async def test_gifts_arrive_directly_or_in_the_present_box(panel):
    runtime = runtime_of(panel)
    before = gold(await runtime.store.read(OTHER))
    gift = {
        "profiles": [PROFILE, OTHER],
        "orders": [{"rid": GOLD, "val": 30}],
        "delivery": "direct",
        "message": "",
    }
    outcomes = await post(panel, "player/grant", gift)
    assert set(outcomes) == {PROFILE, OTHER}
    assert outcomes[OTHER]["deliveries"][0]["rid"] == GOLD
    assert gold(await runtime.store.read(OTHER)) == before + 30
    boxed = {**gift, "profiles": [OTHER], "delivery": "inbox", "message": "おまけ"}
    outcome = (await post(panel, "player/grant", boxed))[OTHER]
    assert outcome["unsynced"] == ["inbox"]
    letter = (await runtime.store.read(OTHER))["inbox"][-1]
    assert letter["reward"] == {"rid": GOLD, "val": 30}
    assert letter["message"] == "おまけ" and letter["tags"][0] == "console.gift"
    assert gold(await runtime.store.read(OTHER)) == before + 30
    await post(panel, "player/grant", {**gift, "delivery": "pigeon"}, expect=400)
    await post(panel, "player/grant", {**gift, "orders": []}, expect=400)
    zero = [{"rid": GOLD, "val": 0}]
    await post(panel, "player/grant", {**gift, "orders": zero}, expect=400)
    unknown = [{"rid": "wallet.no.such", "val": 1}]
    await post(panel, "player/grant", {**gift, "orders": unknown}, expect=400)
    held = gold(await runtime.store.read(PROFILE))
    mixed = {**gift, "profiles": [PROFILE, "dmm:nobody"]}
    await post(panel, "player/grant", mixed, expect=404)
    assert gold(await runtime.store.read(PROFILE)) == held
    rids = {row["rid"]: row["name"] for row in (await get(panel, "resources"))["rows"]}
    assert rids[GOLD] and "unit.card.1" in rids


async def test_the_schedule_is_previewed_saved_and_kept_in_history(panel):
    runtime = runtime_of(panel)
    opened = await get(panel, "schedule")
    assert opened["document"] == {"activities": [], "rows": []}
    assert "gacha" in opened["kinds"] and opened["error"] is None
    preview = await post(panel, "schedule/preview", {"entry": BINGO})
    assert preview["tables"]["bingo_data"] == 1
    assert preview["sample"][0]["table"] == "bingo_data"
    await post(panel, "schedule/preview", {"entry": {**BINGO, "ids": [99]}}, 400)
    document = {"activities": [BINGO], "rows": []}
    saved = await post(panel, "schedule", {"document": document})
    assert saved["document"] == document and saved["revision"] != opened["revision"]
    assert saved["tables"]["bingo_data.asset"] == 1
    assert runtime.masters.lookup("bingo_data", "id", 10)["end_dt"] == FOREVER
    history = list((panel.folders.backups / "config/schedule.json").glob("*.json"))
    assert len(history) == 1
    assert json.loads(history[0].read_text("utf-8")) == opened["document"]
    broken = {"activities": [{**BINGO, "end": "tomorrow"}], "rows": []}
    await post(panel, "schedule", {"document": broken}, expect=400)
    assert (await get(panel, "schedule"))["document"] == document


async def test_the_catalog_shows_shipped_and_current_windows(panel):
    ended = await get(panel, "schedule/catalog", kind="bingo", status="ended")
    assert ended["total"] == 10 and not any(row["moved"] for row in ended["rows"])
    await post(panel, "schedule", {"document": {"activities": [BINGO], "rows": []}})
    running = await get(panel, "schedule/catalog", kind="bingo", status="running")
    assert running["total"] == 1
    row = running["rows"][0]
    assert row["id"] == 10 and row["moved"] and row["current"]["end"] == FOREVER
    assert row["shipped"]["end"] == "2025/06/23 10:29:59"
    found = await get(panel, "schedule/catalog", kind="event", q="らんちき", limit=3)
    assert found["total"] > 3 and len(found["rows"]) == 3
    assert all("らんちき" in row["name"] for row in found["rows"])
    paged = await get(panel, "schedule/catalog", kind="event", q="らんちき", offset=3)
    assert paged["rows"][0]["id"] != found["rows"][0]["id"]
    await get(panel, "schedule/catalog", kind="parade", expect=404)
    await get(panel, "schedule/catalog", expect=400)


async def test_configuration_files_are_checked_before_they_are_written(panel):
    runtime = runtime_of(panel)
    assert (await get(panel, "configs"))["files"] == list(FILES)
    opened = await get(panel, "config", name="settings.json")
    assert opened["error"] is None and opened["versions"] == []
    document = json.loads(opened["text"])
    document["sugoroku"]["chain_limit"] = 9
    body = {"name": "settings.json", "text": json.dumps(document, ensure_ascii=False)}
    written = await post(panel, "config", body)
    assert len(written["versions"]) == 1
    assert runtime.config.read()["sugoroku"]["chain_limit"] == 9
    version = written["versions"][0]["version"]
    kept = await get(panel, "config/version", name="settings.json", version=version)
    assert kept["text"] == opened["text"]
    wrong = json.loads(body["text"])
    wrong["sugoroku"]["chain_limit"] = "nine"
    reply = await post(panel, "config", {**body, "text": json.dumps(wrong)}, 400)
    assert "$.sugoroku.chain_limit has to stay a number" in reply["error"]
    del document["sugoroku"]["chain_limit"]
    lacking = {**body, "text": json.dumps(document)}
    reply = await post(panel, "config", lacking, expect=409)
    assert reply["missing"] == ["$.sugoroku.chain_limit"]
    assert runtime.config.read()["sugoroku"]["chain_limit"] == 9
    await post(panel, "config", {**lacking, "allow_missing": True})
    assert "chain_limit" not in runtime.config.read()["sugoroku"]
    await post(panel, "config", {**body, "text": "{broken"}, expect=400)
    await post(panel, "config", {**body, "name": "passwords.json"}, expect=404)
    restored = await post(
        panel, "config/restore", {"name": "settings.json", "version": version}
    )
    assert restored["text"] == opened["text"] and len(restored["versions"]) == 3
    await get(panel, "config/version", name="settings.json", version="../x", expect=400)


def test_shapes_are_compared_loosely_where_values_are_optional():
    current = {"a": 1, "b": None, "rows": [{"id": 1, "note": ""}], "free": {}}
    proposed = {"a": 2.5, "b": [1], "rows": [{"id": "x"}], "free": {"k": 1}, "new": 1}
    assert list(compare(current, proposed)) == [
        ("type", "$.rows[0].id has to stay a number"),
        ("missing", "$.rows[0].note"),
    ]
    assert list(compare({"a": True}, {"a": 1})) == [("type", "$.a has to stay a bool")]


async def test_a_file_broken_outside_the_console_keeps_the_last_good_one(panel):
    runtime = runtime_of(panel)
    limit = runtime.config.read()["sugoroku"]["chain_limit"]
    runtime.config.path.write_text("{half", "utf-8")
    assert runtime.config.read()["sugoroku"]["chain_limit"] == limit
    status = await get(panel, "status")
    assert "Expecting property name" in status["problems"]["settings"]
    opened = await get(panel, "config", name="settings.json")
    assert opened["text"] == "{half" and opened["error"]
    with pytest.raises(ValueError):
        RuntimeConfig(runtime.config.path).read()


async def test_captures_are_browsed_and_compared(panel):
    def line(kind, data):
        return json.dumps({"t": "01:02:03.004", "kind": kind, "data": data})

    def answer(value):
        text = json.dumps(value).encode()
        return line("resp-b64", {"client": "x", "b64": base64.b64encode(text).decode()})

    url = "https://gapi.example/api/player/get"
    official = [
        line("header", {"url": url, "name": "a", "value": "b"}),
        line("req", {"proto": 2, "url": url, "payload": '{"a": 1}'}),
        line("secret", {"value": "hidden"}),
        answer({"data": {"player": {"code": "A"}, "votes": None, "rows": [{"id": 1}]}}),
        line(
            "req", {"proto": 2, "url": url.replace("player/get", "x/y"), "payload": ""}
        ),
    ]
    local = [
        line("req", {"proto": 2, "url": url, "payload": "{}"}),
        answer({"data": {"player": {"code": 5}, "rows": [{"id": 1, "extra": 2}]}}),
    ]
    folder = panel.folders.captures
    (folder / "session_1.jsonl").write_text("\n".join(official) + "\n", "utf-8")
    (folder / "session_2.jsonl").write_text("\n".join(local) + "\n", "utf-8")
    (folder / "session_3.jsonl").write_text("", "utf-8")
    (folder / "session_4.jsonl").write_text(local[0] + "\n" + local[1][:40], "utf-8")
    sessions = (await get(panel, "captures"))["sessions"]
    assert [row["name"] for row in sessions] == [
        "session_4.jsonl",
        "session_2.jsonl",
        "session_1.jsonl",
    ]
    recording = (await get(panel, "capture", name="session_4.jsonl"))["entries"]
    assert [row["answered"] for row in recording] == [False]
    entries = (await get(panel, "capture", name="session_1.jsonl"))["entries"]
    assert [(row["endpoint"], row["answered"]) for row in entries] == [
        ("player/get", True),
        ("x/y", False),
    ]
    entry = await get(panel, "capture/entry", name="session_1.jsonl", index=0)
    assert entry["request"] == {"a": 1} and entry["response"]["data"]["votes"] is None
    assert "hidden" not in json.dumps(entry)
    unanswered = await get(panel, "capture/entry", name="session_1.jsonl", index=1)
    assert unanswered == {
        "time": "01:02:03.004",
        "endpoint": "x/y",
        "request": "",
        "response": None,
    }
    body = {
        "left": {"name": "session_1.jsonl", "index": 0},
        "right": {"name": "session_2.jsonl", "index": 0},
    }
    assert (await post(panel, "capture/compare", body))["differences"] == [
        {"path": "$.data.player.code", "left": "str", "right": "number"},
        {"path": "$.data.rows[].extra", "left": "missing", "right": "number"},
        {"path": "$.data.votes", "left": "NoneType", "right": "missing"},
    ]
    await get(panel, "capture/entry", name="session_1.jsonl", index=9, expect=404)
    await get(panel, "capture", name="session_9.jsonl", expect=404)


async def test_master_data_is_summarised(panel):
    reply = await get(panel, "masters")
    assert reply["summary"]["folders"]["data"] > 100
    assert reply["summary"]["files"] == sum(reply["summary"]["folders"].values())
    assert reply["progress"] == {"running": False, "code": None, "lines": []}


def test_pending_syncs_are_merged_in_order():
    first = {"wallet": [{"scope": "s", "data": [1]}]}
    second = {"wallet": [{"scope": "s", "data": [2]}], "item": [{"scope": "s"}]}
    assert merged(None, None) is None and merged(first, None) is first
    assert merged(first, second) == {
        "wallet": [{"scope": "s", "data": [1]}, {"scope": "s", "data": [2]}],
        "item": [{"scope": "s"}],
    }
    outbox = Outbox()
    outbox.post("a", None)
    assert outbox.collect("a", second) is second
    outbox.post("a", first)
    outbox.post("a", second)
    assert outbox.collect("a", None) == merged(first, second)
    outbox.post("a", first)
    outbox.discard("a")
    assert outbox.collect("a", None) is None
