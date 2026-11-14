import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
import pytest

from monmusu_ps.app import RUNTIME, create_app
from monmusu_ps.config import Settings
from monmusu_ps.core.context import Context
from monmusu_ps.core.platform import PlatformError
from monmusu_ps.core.transport import Protocol
from monmusu_ps.features.platform_tokens import set_token, update_token
from monmusu_ps.runtime import Runtime

from test_server import NativeClient
from test_transport import rsa_request


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
APPLICATION = "749446"


@pytest.fixture
async def http(tmp_path):
    async with TestClient(
        TestServer(create_app(Settings.load(ROOT, tmp_path), clock=lambda: NOW))
    ) as client:
        yield client


async def test_sdk_refresh_register_and_game_ticket_update_share_state(http):
    client = NativeClient(http)
    await client.login("alice")
    runtime = http.app[RUNTIME]
    game_token, password = client.token, client.password
    response = await http.post(
        "/api/sdk/updateToken",
        json={
            "viewer_id": "alice",
            "access_token": "local",
            "onetime_token": "local",
            "command": "updatetoken",
        },
    )
    sdk = await response.json()
    assert sdk["result_code"] == 0
    payload = {"id": "alice", "ot": sdk["onetime_token"], "ap": APPLICATION}
    registered = await client.call("auth/set_api_token/dmm_gp", payload, Protocol.RSA)
    assert "data" not in registered
    updated = await client.call("auth/update_api_token/dmm_gp", payload, Protocol.RSA)
    ticket = updated["data"]["ot"]
    assert ticket != sdk["onetime_token"]
    assert (
        runtime.platform.profile("alice", sdk["access_token"])["onetime_token"]
        == ticket
    )
    assert runtime.sessions.get(game_token).password == password
    assert client.token == game_token
    assert client.password == password

    login = await client.call(
        "auth/login/dmm_gp",
        {
            "id": "alice",
            "ot": ticket,
            "ap": APPLICATION,
            "cvr": "1.0.62",
            "ab": "469259656c",
        },
        Protocol.RSA,
    )
    assert runtime.sessions.get(login["data"]["access_token"]).profile == "dmm:alice"
    assert runtime.platform.authenticate("alice", ticket, APPLICATION) == "dmm:alice"


async def test_update_login_ticket_persists_across_runtime_restart(http):
    client = NativeClient(http)
    await client.login("alice")
    runtime = http.app[RUNTIME]
    updated = await client.call(
        "auth/update_api_token/dmm_gp",
        {
            "id": "alice",
            "ot": "local",
            "ap": APPLICATION,
        },
        Protocol.RSA,
    )
    ticket = updated["data"]["ot"]
    restored = Runtime(runtime.settings, clock=lambda: NOW)
    assert restored.platform.profile("alice", "local")["onetime_token"] == ticket
    assert restored.platform.authenticate("alice", ticket, APPLICATION) == "dmm:alice"
    with pytest.raises(PlatformError, match="one-time token"):
        restored.platform.update_ticket("alice", "local", APPLICATION)


async def test_invalid_update_ticket_returns_native_rejection_without_mutation(http):
    client = NativeClient(http)
    await client.login("alice")
    runtime = http.app[RUNTIME]
    before = runtime.platform.path.read_bytes()
    payload = {"id": "alice", "ot": "invalid-ticket", "ap": APPLICATION}
    response = await http.post(
        "/api/auth/update_api_token/dmm_gp",
        data=rsa_request(client.key, json.dumps(payload).encode()),
        headers={"x-ag-protocol": "3"},
    )
    assert response.status == 422
    assert response.headers["x-ag-protocol"] == "1"
    body = await response.json()
    assert body["status"]["code"] == 90000006
    assert body["data"] == {"rejected_reason": "INVALID_GP_ONETIME_TOKEN"}
    assert runtime.platform.path.read_bytes() == before
    assert runtime.sessions.get(client.token).password == client.password


@pytest.mark.parametrize("handler", [set_token, update_token])
@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"id": "unknown"}, 102),
        ({"ap": "different-app"}, 203),
        ({"ot": "wrong-ticket"}, 204),
    ],
)
def test_rejected_ticket_request_keeps_platform_state(
    tmp_path, handler, override, code
):
    runtime = Runtime(Settings.load(ROOT, tmp_path), clock=lambda: NOW)
    runtime.platform.check_login("alice", "local", "local")
    sdk = runtime.platform.refresh("alice", "local")
    payload = {"id": "alice", "ot": sdk["onetime_token"], "ap": APPLICATION, **override}
    before = runtime.platform.path.read_bytes()
    context = Context(runtime, payload, None, None, NOW)
    with pytest.raises(PlatformError) as caught:
        handler(context)
    assert caught.value.code == code
    assert runtime.platform.path.read_bytes() == before
    assert context.session is None


def test_ticket_registration_is_repeatable_and_does_not_consume_login(tmp_path):
    runtime = Runtime(Settings.load(ROOT, tmp_path), clock=lambda: NOW)
    runtime.platform.check_login("alice", "local", "local")
    sdk = runtime.platform.refresh("alice", "local")
    context = Context(
        runtime,
        {"id": "alice", "ot": sdk["onetime_token"], "ap": APPLICATION},
        None,
        None,
        NOW,
    )
    before = runtime.platform.path.read_bytes()
    set_token(context)
    set_token(context)
    assert runtime.platform.path.read_bytes() == before
    assert (
        runtime.platform.authenticate("alice", sdk["onetime_token"], APPLICATION)
        == "dmm:alice"
    )
