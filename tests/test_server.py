import base64
import json
from pathlib import Path
import secrets
import struct
from urllib.parse import quote, quote_plus

from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import pytest

from monmusu_ps.app import RUNTIME, create_app
from monmusu_ps.config import Settings
from monmusu_ps.core.transport import (
    DecodedRequest,
    Protocol,
    decode_request,
    encode_response,
)
from monmusu_ps.core.registry import FeatureRegistry


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
APPLICATION = "749446"


class NativeClient:
    def __init__(self, http):
        self.http = http
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        self.token = None
        self.password = None

    async def login(self, profile="local"):
        sdk = await self.http.post(
            "/api/sdk/request",
            json={
                "viewer_id": profile,
                "access_token": "local",
                "onetime_token": "local",
                "sdk_version": "test",
                "command": "checklogin",
            },
        )
        assert (await sdk.json())["result_code"] == 0
        value = await self.call(
            "auth/login/dmm_gp",
            {
                "id": profile,
                "ot": "local",
                "ap": "749446",
                "cvr": "1.0.62",
                "ab": "469259656c",
            },
            protocol=Protocol.RSA,
        )
        self.token = value["data"]["access_token"]
        self.password = value["data"]["kdfp"]
        return value

    async def call(self, endpoint, payload, protocol=Protocol.AES, method="POST"):
        if protocol == Protocol.RSA:
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            nonce = base64.b64encode(secrets.token_bytes(16)).decode()
            signature = self.key.sign(
                f"dka#oc-@i0eowyghjkl@{text}@{nonce}".encode(),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            numbers = self.key.public_key().public_numbers()
            modulus = numbers.n.to_bytes(128, "big")
            exponent = numbers.e.to_bytes(3, "big")
            nonce_pack = (
                struct.pack(">HH", len(nonce), len(signature))
                + nonce.encode()
                + signature
            )
            public_pack = (
                struct.pack(">HH", len(modulus), len(exponent)) + modulus + exponent
            )
            request_text = (
                quote_plus(text)
                + "\n"
                + base64.b64encode(nonce_pack).decode()
                + "\n"
                + base64.b64encode(public_pack).decode()
            )
            body = quote(request_text, safe="").encode()
        else:
            body = encode_response(
                payload, DecodedRequest({}, protocol, None), self.password
            )
        headers = {"x-ag-protocol": str(int(protocol))}
        if protocol == Protocol.AES:
            headers["x-ag-token"] = self.token
        response = await self.http.request(
            method, "/api/" + endpoint, data=body, headers=headers
        )
        raw = await response.read()
        assert response.status == 200, (endpoint, response.status, raw)
        assert response.headers["x-ag-protocol"] == str(int(protocol))
        if "x-ag-refreshtoken" in response.headers:
            self.token = response.headers["x-ag-refreshtoken"]
        if protocol == Protocol.RSA:
            plaintext = b"".join(
                self.key.decrypt(
                    base64.b64decode(line),
                    padding.OAEP(
                        mgf=padding.MGF1(hashes.SHA1()),
                        algorithm=hashes.SHA1(),
                        label=None,
                    ),
                )
                for line in raw.splitlines()
            )
            return json.loads(plaintext)
        return decode_request(raw, protocol, self.password).payload


@pytest.fixture
async def http(tmp_path):
    app = create_app(Settings.load(ROOT, tmp_path), clock=lambda: NOW)
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_native_login_initialize_and_home(http):
    client = NativeClient(http)
    config = await client.call(
        "appenv/ep-conf",
        {"provider": "dmm-gp", "cvr": "1.0.62", "cv": "cl.dmm.web.r"},
        Protocol.PLAIN,
    )
    assert config["data"]["gapi_ep"] == "http://127.0.0.1:18621"
    login = await client.login()
    assert login["sync"] is None
    initialized = await client.call("player/init", {"_z0": 0})
    assert initialized["status"]["code"] == 1
    assert "data" not in initialized
    result = await client.call(
        "player/get",
        {"player_module_ids": ["stamina", "composition", "experience", "farming"]},
    )
    assert result["timesync"] == NOW
    assert result["data"]["player"]["tutorial"] == 0
    assert result["data"]["player"]["follows"] == 0
    assert len(result["data"]["player"]["player_code"]) == 8
    assert result["data"]["modules"]["stamina"][0]["amount"] == 70
    assert isinstance(result["sync"]["module_prop"][0]["data"], dict)
    units = await client.call("unit/all", {"_z0": 0})
    assert [row["card_id"] for row in units["data"]["units"]] == [11, 22]
    home_prefs = await client.call(
        "prefs",
        {"owner_domain": "player", "owner_id": "@me", "prefs_name": "home_favorite"},
    )
    assert json.loads(home_prefs["data"]["prefs"]["payload"])["my_page_unit_cards"] == [
        11,
        22,
    ]
    wallets = await client.call("wallet/all", {"_z0": 0})
    held = {(row["category"], row["name"]): row["val"] for row in wallets["data"]["wallets"]}
    tickets = {key: val for key, val in held.items() if key[0] == "ticket"}
    assert {key[1]: val for key, val in held.items() if key not in tickets} == {
        "paid_coin": 0,
        "gold": 500,
        "free_coin": 300,
    }
    # Logging in stocks every open ticket pool's ticket.
    assert len(tickets) > 500 and set(tickets.values()) == {999}
    await client.call("player/home/visit", {"_z0": 0}, method="PUT")
    assert (await http.app[RUNTIME].store.read("dmm:local"))["home_visited_at"] == NOW


async def test_player_reads_and_home_visits_share_the_stamina_recovery(http):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]

    async def drain():
        async with runtime.store.transaction("dmm:local") as state:
            state["modules"]["stamina"][0].update(
                amount=10, max_capacity=500, latest_recovered_at=NOW - 650
            )

    await drain()
    visited = await client.call("player/home/visit", {"_z0": 0}, method="PUT")
    row = visited["sync"]["module_prop"][0]["data"]["stamina"][0]
    assert (row["amount"], row["max_capacity"]) == (12, 999)
    assert row["latest_recovered_at"] == NOW - 50
    settled = await client.call("player/home/visit", {"_z0": 0}, method="PUT")
    assert settled["sync"] is None
    await drain()
    loaded = await client.call("player/get", {"player_module_ids": ["stamina"]})
    row = loaded["data"]["modules"]["stamina"][0]
    assert (row["amount"], row["max_capacity"]) == (12, 999)
    assert row["latest_recovered_at"] == NOW - 50


async def test_preferences_persist_between_logins_and_isolate_profiles(http):
    client = NativeClient(http)
    await client.login("alice")
    payload = {
        "owner_domain": "player",
        "owner_id": "@me",
        "prefs_name": "game_option",
        "payload": '{"volume":0.25}',
    }
    saved = await client.call("prefs/set", payload)
    assert saved["data"]["prefs"]["owner_id"] != "@me"
    await client.login("bob")
    listed = await client.call(
        "prefs/list",
        {"owner_domain": "player", "owner_id": "@me", "prefs_names": ["game_option"]},
    )
    template = http.app[RUNTIME].config.read()["prefs"]["game_option"]
    assert [row["payload"] for row in listed["data"]["prefs_list"]] == [template]
    assert (
        listed["data"]["prefs_list"][0]["owner_id"]
        != saved["data"]["prefs"]["owner_id"]
    )
    await client.login("alice")
    loaded = await client.call(
        "prefs",
        {"owner_domain": "player", "owner_id": "@me", "prefs_name": "game_option"},
    )
    assert loaded["data"]["prefs"]["payload"] == payload["payload"]


@pytest.mark.parametrize(
    "key,field,text",
    [("player.name", "name", "テスト名前"), ("player.bio", "bio", "本地玩家简介")],
)
async def test_player_text_update_persists_and_isolates_profiles(
    http, key, field, text
):
    client = NativeClient(http)
    await client.login("alice")
    response = await client.call("social/user_text/update", {"key": key, "text": text})
    assert response["status"]["code"] == 1
    assert "data" not in response
    await client.login("bob")
    other = await client.call("player/get", {"player_module_ids": []})
    assert other["data"]["player_text"][field] == ""
    await client.login("alice")
    restored = await client.call("player/get", {"player_module_ids": []})
    assert restored["data"]["player_text"][field] == text
    assert restored["data"]["player"]["nickname"] == "ご主人"


async def test_token_rotation_and_real_put_contract(http):
    client = NativeClient(http)
    await client.login()
    token = client.token
    await client.call("unit/all", {"_z0": 0})
    assert client.token != token
    body = encode_response(
        {"_z0": 0}, DecodedRequest({}, Protocol.AES, None), client.password
    )
    stale = await http.post(
        "/api/unit/all", data=body, headers={"x-ag-token": token, "x-ag-protocol": "2"}
    )
    assert stale.status == 401
    assert stale.headers["x-ag-protocol"] == "1"
    assert (await stale.json())["status"]["code"] == 90200002
    wrong_method = await http.post(
        "/api/player/home/visit",
        data=body,
        headers={"x-ag-token": client.token, "x-ag-protocol": "2"},
    )
    assert wrong_method.status == 405


async def posted(http, client, endpoint, payload):
    body = encode_response(
        payload, DecodedRequest({}, Protocol.AES, None), client.password
    )
    return await http.post(
        "/api/" + endpoint,
        data=body,
        headers={"x-ag-token": client.token, "x-ag-protocol": "2"},
    )


def slot(iid, sn, kind=1):
    return json.dumps(
        {
            "t": kind,
            "sn": sn,
            "iid": iid,
            "sauto": True,
            "skillAuto": 1,
            "classActionAuto": 1,
            "sskills": {"s1": 0, "s2": 0},
            "exskill": 1,
        },
        separators=(",", ":"),
    )


async def test_preferences_fall_back_to_templates_and_report_unknown_names(http):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]
    templates = runtime.config.read()["prefs"]
    owner = {"owner_domain": "player", "owner_id": "@me"}
    listed = await client.call(
        "prefs/list", {**owner, "prefs_names": sorted(templates)}
    )
    rows = {row["prefs_name"]: row for row in listed["data"]["prefs_list"]}
    assert set(rows) == set(templates)
    assert rows["game_option"]["payload"] == templates["game_option"]
    assert json.loads(rows["home_favorite"]["payload"])["my_page_unit_cards"] == [
        11,
        22,
    ]
    assert {row["owner_id"] for row in rows.values()} == {
        rows["home_favorite"]["owner_id"]
    }
    for endpoint, payload, reason in (
        ("prefs", {**owner, "prefs_name": "unknown_option"}, "UNDEFINED_PREFS"),
        (
            "prefs/list",
            {**owner, "prefs_names": ["game_option", "unknown_option"]},
            "UNDEFINED_PREFS",
        ),
        (
            "prefs/set",
            {**owner, "prefs_name": "unknown_option", "payload": "{}"},
            "UNDEFINED_PREFS",
        ),
        (
            "prefs/set",
            {**owner, "prefs_name": "game_option", "payload": "{broken"},
            "CONTENTS_JSON_PARSE_ERROR",
        ),
        ("prefs/create", {**owner, "prefs_name": "unknown_option"}, "UNDEFINED_PREFS"),
    ):
        response = await posted(http, client, endpoint, payload)
        assert response.status == 422
        assert (await response.json())["data"] == {"rejected_reason": reason}
    created = await client.call("prefs/create", {**owner, "prefs_name": "bom_cutin"})
    assert created["data"]["prefs"]["payload"] == templates["bom_cutin"]
    duplicate = await posted(
        http, client, "prefs/create", {**owner, "prefs_name": "bom_cutin"}
    )
    assert duplicate.status == 409
    assert await duplicate.json() == {
        "status": {"code": 90000005, "message": "Existing resources: prefs.bom_cutin"},
        "data": {"resources": ["prefs.bom_cutin"]},
    }
    saved = await runtime.store.read("dmm:local")
    assert set(saved["prefs"]) == {"home_favorite", "bom_cutin"}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"section": "bom_deck"}, "INVALID_COMPOSITION_SECTION"),
        ({"no": 21}, "COMPOSITION_CAPACITY_OVER"),
        ({"no": 0}, "COMPOSITION_CAPACITY_OVER"),
        ({"label": "あ" * 16}, "LABEL_TOO_LONG"),
        ({"slots": [slot(1, number) for number in range(15)]}, "SLOT_CAPACITY_OVER"),
        ({"slots": [slot(1, 14)]}, "SLOT_CAPACITY_OVER"),
        ({"slots": [slot(1, 0), slot(1, 1)]}, "SLOT_NOT_UNIQUE"),
        ({"slots": [slot(1, 0), slot(2, 0)]}, "SLOT_NOT_UNIQUE"),
    ],
)
async def test_composition_rules_answer_with_their_reasons(http, changes, reason):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]
    before = await runtime.store.read("dmm:local")
    value = {
        "section": "unit_deck",
        "no": 1,
        "label": "ok",
        "slots": [slot(1, 0)],
        "exdata": "{}",
        **changes,
    }
    response = await posted(http, client, "composition/update", {"composition": value})
    assert response.status == 422
    assert (await response.json())["data"] == {"rejected_reason": reason}
    assert await runtime.store.read("dmm:local") == before


async def test_composition_create_adds_only_new_numbers(http):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]
    value = {
        "section": "unit_deck",
        "no": 20,
        "label": "新規",
        "slots": [slot(2, 0), slot(1, 0, kind=2)],
        "exdata": '{"bom_buf":true}',
    }
    duplicate = await posted(http, client, "composition/create", {"composition": value})
    assert duplicate.status == 409
    assert (await duplicate.json())["data"] == {
        "resources": ["composition.unit_deck.20"]
    }
    async with runtime.store.transaction("dmm:local") as state:
        state["compositions"] = [
            row for row in state["compositions"] if row["no"] != 20
        ]
    created = await client.call("composition/create", {"composition": value})
    assert created["data"] is None
    assert created["sync"] == {
        "composition": [
            {"scope": created["sync"]["composition"][0]["scope"], "data": [value]}
        ]
    }
    listed = await client.call("composition/list", {"section": "unit_deck"})
    assert listed["data"]["compositions"][-1] == value
    assert len(listed["data"]["compositions"]) == 20


async def test_invalid_composition_does_not_partially_write(http):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]
    before = await runtime.store.read("dmm:local")
    value = {
        "section": "unit_deck",
        "no": 1,
        "label": "bad",
        "slots": ['{"t":1,"sn":0,"iid":9999}'],
        "exdata": "{}",
    }
    body = encode_response(
        {"composition": value}, DecodedRequest({}, Protocol.AES, None), client.password
    )
    response = await http.post(
        "/api/composition/update",
        data=body,
        headers={"x-ag-token": client.token, "x-ag-protocol": "2"},
    )
    assert response.status == 400
    assert await runtime.store.read("dmm:local") == before


async def save_deck(http, progress):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]
    async with runtime.store.transaction("dmm:local") as state:
        state["player"]["tutorial"] = progress
    listed = await client.call("composition/list", {"section": "unit_deck"})
    deck = {**listed["data"]["compositions"][0], "label": "renamed"}
    updated = await client.call("composition/update", {"composition": deck})
    assert updated["data"] is None
    assert updated["sync"]["composition"][0]["data"] == [deck]
    saved = await runtime.store.read("dmm:local")
    assert saved["compositions"][0] == deck
    return updated["sync"], saved["player"]["tutorial"]


@pytest.mark.parametrize("progress,reached", [(200, 250), (300, 350), (600, 700)])
async def test_deck_update_completes_a_pending_deck_step(http, progress, reached):
    sync, saved = await save_deck(http, progress)
    assert set(sync) == {"composition", "player"}
    assert sync["player"][0]["data"][0]["tutorial"] == saved == reached


@pytest.mark.parametrize("progress", [0, 250, 500, 9999])
async def test_deck_update_outside_a_deck_step_syncs_only_the_deck(http, progress):
    sync, saved = await save_deck(http, progress)
    assert set(sync) == {"composition"}
    assert saved == progress


async def test_credential_issue_and_login(http):
    client = NativeClient(http)
    challenge = await client.call("auth/handshake", {"tms": NOW * 1000}, Protocol.RSA)
    token = challenge["data"]["token"]
    credential = await client.call(
        "auth/credential/issue", {"provider": "dmm", "token": token}, Protocol.RSA
    )
    response = await client.call(
        "auth/login/credential",
        {
            "provider": "dmm",
            **credential["data"],
            "token": token,
            "cv": "cl.dmm.web.r",
            "cvr": "1.0.62",
            "ab": "469259656c",
        },
        Protocol.RSA,
    )
    assert response["data"]["access_token"].startswith("dat@")
    assert response["data"]["kdfp"]


async def test_platform_rotation_updates_native_credentials(http):
    client = NativeClient(http)
    await client.login()
    refreshed = await http.post(
        "/api/sdk/updateToken",
        json={
            "viewer_id": "local",
            "access_token": "local",
            "onetime_token": "local",
            "sdk_version": "test",
            "command": "updatetoken",
        },
    )
    tokens = await refreshed.json()
    assert tokens["result_code"] == 0
    assert tokens["access_token"] != "local"
    assert tokens["onetime_token"] != "local"
    profile = await http.post(
        "/api/sdk/request",
        json={
            "viewer_id": "local",
            "access_token": tokens["access_token"],
            "onetime_token": tokens["onetime_token"],
            "sdk_version": "test",
            "command": "getprofile",
        },
    )
    assert (await profile.json())["install_status"] is True
    stale = await http.post(
        "/api/sdk/request",
        json={
            "viewer_id": "local",
            "access_token": "local",
            "onetime_token": "local",
            "sdk_version": "test",
            "command": "getprofile",
        },
    )
    assert (await stale.json())["result_code"] == 110
    relogin = await client.call(
        "auth/login/dmm_gp",
        {
            "id": "local",
            "ot": tokens["onetime_token"],
            "ap": "749446",
            "cvr": "1.0.62",
            "ab": "469259656c",
        },
        Protocol.RSA,
    )
    assert relogin["data"]["access_token"].startswith("dat@")


async def test_return_to_title_reuses_current_platform_ticket(http):
    client = NativeClient(http)
    await client.login()
    first = await http.app[RUNTIME].store.read("dmm:local")
    tokens = {client.token}
    for _ in range(2):
        result = await client.call(
            "auth/login/dmm_gp",
            {
                "id": "local",
                "ot": "local",
                "ap": APPLICATION,
                "cvr": "1.0.62",
                "ab": "469259656c",
            },
            Protocol.RSA,
        )
        assert result["status"]["code"] == 1
        tokens.add(result["data"]["access_token"])
    assert len(tokens) == 3
    assert await http.app[RUNTIME].store.read("dmm:local") == first


async def test_feature_reload_preserves_native_session_and_save(http, tmp_path):
    client = NativeClient(http)
    await client.login()
    runtime = http.app[RUNTIME]
    session = runtime.sessions.get(client.token)
    before = await runtime.store.read(session.profile)
    directory = tmp_path / "features"
    directory.mkdir()
    module = directory / "profile.py"
    module.write_text(
        "from ..core.sync import Result\n"
        "def read(ctx):\n    return Result({'nickname': 'first'})\n"
        "ROUTES = {'player/nickname/': read}\n",
        encoding="utf-8",
    )
    runtime.registry.close()
    runtime.registry = FeatureRegistry(directory, runtime.contracts)
    assert (await client.call("player/nickname/", {"_z0": 0}))["data"][
        "nickname"
    ] == "first"
    module.write_text(
        "from ..core.sync import Result\n"
        "def read(ctx):\n    return Result({'nickname': ctx.state['player']['nickname'] + '-reloaded'})\n"
        "ROUTES = {'player/nickname/': read}\n",
        encoding="utf-8",
    )
    reloaded = await client.call("player/nickname/", {"_z0": 0})
    assert reloaded["data"]["nickname"] == "ご主人-reloaded"
    module.write_text("def broken(:\n", encoding="utf-8")
    unchanged = await client.call("player/nickname/", {"_z0": 0})
    assert unchanged["data"] == reloaded["data"]
    assert runtime.sessions.get(client.token) is session
    assert client.password == session.password
    assert await runtime.store.read(session.profile) == before
