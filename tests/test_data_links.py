from copy import deepcopy
import json
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
import pytest

from monmusu_ps.app import RUNTIME, create_app
from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiRejected
from test_server import NativeClient, posted


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
DAY = 86400
PASSWORD = "Abcd1234"
STONE_SKU = "com.dmm.dmmgames.monmusutd.stone_2.dmm"
MODULES = {"player_module_ids": ["stamina"]}


@pytest.fixture
async def world(tmp_path, monkeypatch):
    clock = {"now": NOW}
    app = create_app(Settings.load(ROOT, tmp_path), clock=lambda: clock["now"])
    runtime = app[RUNTIME]
    settings = deepcopy(runtime.config.read())
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    async with TestClient(TestServer(app)) as http:
        yield http, runtime, clock


async def enter(http, viewer):
    client = NativeClient(http)
    await client.login(viewer)
    player = (await client.call("player/get", MODULES))["data"]["player"]
    return client, player["player_code"]


def ledger(runtime):
    return json.loads(runtime.data_links.path.read_text(encoding="utf-8"))


def attempt(code, password=PASSWORD):
    return {"player_code": code, "password": password}


async def offer(http, viewer="alice", password=PASSWORD):
    client, code = await enter(http, viewer)
    issued = await client.call("data_link/password/issue", {"password": password})
    assert (issued["data"], issued["sync"]) == ({"_z0": 0}, None)
    return client, code


async def test_a_confirmed_password_moves_the_account_onto_the_target_save(world):
    http, runtime, _ = world
    alice, alice_code = await offer(http)
    bob, bob_code = await enter(http, "bob")
    confirmed = await bob.call("data_link/password/confirm", attempt(alice_code))
    assert confirmed["data"] == {
        "rejected_reason": None,
        "success": {
            "player_code": alice_code,
            "nickname": "ご主人",
            "rank": 1,
            "login_at": NOW,
        },
    }
    assert ledger(runtime)["bindings"] == {}
    executed = await bob.call("data_link/password/exec", attempt(alice_code))
    assert (executed["data"], executed["sync"]) == ({"_z0": 0}, None)
    assert ledger(runtime) == {
        "passwords": {},
        "bindings": {
            "dmm:bob": {"profile": "dmm:alice", "previous": "dmm:bob", "linked_at": NOW}
        },
    }
    login = await bob.login("bob")
    assert login["sync"] is None
    session = runtime.sessions.get(bob.token)
    assert (session.profile, session.login) == ("dmm:alice", "dmm:bob")
    linked = (await bob.call("player/get", MODULES))["data"]["player"]
    assert linked["player_code"] == alice_code != bob_code
    displaced = await posted(http, alice, "player/get", MODULES)
    assert displaced.status == 401
    assert runtime.store.exists("dmm:bob")


async def test_confirm_explains_refusals_inside_a_200(world):
    http, _, clock = world
    alice, alice_code = await offer(http)
    bob, bob_code = await enter(http, "bob")

    async def reason(client, payload):
        answer = await client.call("data_link/password/confirm", payload)
        assert answer["data"]["success"] is None
        return answer["data"]["rejected_reason"]

    for payload in (
        attempt("ZZZZZZZZ"),
        attempt(bob_code),
        attempt(alice_code, "Wrong1234"),
        attempt(alice_code, None),
        attempt(None),
    ):
        assert await reason(bob, payload) == "INVALID_INPUT_PARAMS"
    assert await reason(alice, attempt(alice_code)) == "TARGET_OWN"
    clock["now"] = NOW + DAY
    timely = await bob.call("data_link/password/confirm", attempt(alice_code))
    assert timely["data"]["success"]["player_code"] == alice_code
    clock["now"] = NOW + DAY + 1
    assert await reason(bob, attempt(alice_code)) == "PASSWORD_EXPIRED"
    assert await reason(bob, attempt(alice_code, "Wrong1234")) == (
        "INVALID_INPUT_PARAMS"
    )


async def test_exec_refuses_with_a_422_and_changes_nothing(world):
    http, runtime, clock = world
    _, alice_code = await offer(http)
    bob, _ = await enter(http, "bob")
    before = ledger(runtime)
    wrong = await posted(
        http, bob, "data_link/password/exec", attempt(alice_code, "Wrong1234")
    )
    assert wrong.status == 422
    assert (await wrong.json())["data"] == {"rejected_reason": "INVALID_INPUT_PARAMS"}
    clock["now"] = NOW + DAY + 1
    late = await posted(http, bob, "data_link/password/exec", attempt(alice_code))
    assert (await late.json())["data"] == {"rejected_reason": "PASSWORD_EXPIRED"}
    assert ledger(runtime) == before
    assert runtime.data_links.resolve("dmm:bob") == "dmm:bob"


async def test_targets_bound_to_a_dmm_account_can_be_closed_to_linking(world):
    http, runtime, _ = world
    _, alice_code = await offer(http)
    bob, _ = await enter(http, "bob")
    runtime.config.read()["data_link"]["allow_linked_target"] = False
    for endpoint in ("data_link/password/confirm", "data_link/password/exec"):
        refused = await posted(http, bob, endpoint, attempt(alice_code))
        assert refused.status == 422
        assert (await refused.json())["data"] == {
            "rejected_reason": "ALREADY_LINKED_DMM"
        }
    async with runtime.store.transaction("guest") as guest:
        code = guest["player"]["player_code"]
        runtime.data_links.issue(guest, {"password": PASSWORD}, NOW)
    opened = await bob.call("data_link/password/confirm", attempt(code))
    assert opened["data"]["success"]["player_code"] == code


@pytest.mark.parametrize(
    "password",
    ["Abc1234", "Abcd123456789", "Abcd 1234", "Abcd1234\n", "パスワード12", 12345678],
)
async def test_passwords_must_be_8_to_12_letters_and_digits(world, password):
    _, runtime, _ = world
    state = await runtime.store.read("dmm:alice")
    with pytest.raises(ValueError):
        runtime.data_links.issue(state, {"password": password}, NOW)
    assert not runtime.data_links.path.exists()


async def test_a_new_password_replaces_the_old_one(world):
    http, runtime, clock = world
    alice, alice_code = await offer(http)
    clock["now"] = NOW + DAY
    await alice.call("data_link/password/issue", {"password": "Next56789"})
    bob, _ = await enter(http, "bob")
    stale = await bob.call("data_link/password/confirm", attempt(alice_code))
    assert stale["data"]["rejected_reason"] == "INVALID_INPUT_PARAMS"
    clock["now"] = NOW + 2 * DAY
    fresh = await bob.call(
        "data_link/password/confirm", attempt(alice_code, "Next56789")
    )
    assert fresh["data"]["success"]["player_code"] == alice_code
    assert list(ledger(runtime)["passwords"]) == [alice_code]


async def test_linking_back_to_the_first_save_drops_the_binding(world):
    http, runtime, _ = world
    _, alice_code = await offer(http)
    bob, bob_code = await offer(http, "bob", "Back56789")
    await bob.call("data_link/password/exec", attempt(alice_code))
    await bob.login("bob")
    own = await bob.call("data_link/password/confirm", attempt(alice_code))
    assert own["data"]["rejected_reason"] == "INVALID_INPUT_PARAMS"
    await bob.call("data_link/password/exec", attempt(bob_code, "Back56789"))
    assert ledger(runtime) == {"passwords": {}, "bindings": {}}
    await bob.login("bob")
    restored = (await bob.call("player/get", MODULES))["data"]["player"]
    assert restored["player_code"] == bob_code


async def test_passwords_can_outlive_a_link_by_configuration(world):
    http, runtime, _ = world
    runtime.config.read()["data_link"]["consume_password_on_exec"] = False
    _, alice_code = await offer(http)
    bob, _ = await enter(http, "bob")
    await bob.call("data_link/password/exec", attempt(alice_code))
    carol, _ = await enter(http, "carol")
    again = await carol.call("data_link/password/confirm", attempt(alice_code))
    assert again["data"]["success"]["player_code"] == alice_code


async def test_platform_orders_reach_the_linked_save(world):
    http, runtime, _ = world
    _, alice_code = await offer(http)
    bob, _ = await enter(http, "bob")
    await bob.call("data_link/password/exec", attempt(alice_code))
    payment_id = await runtime.order("bob", STONE_SKU, 1)
    target = await runtime.store.read("dmm:alice")
    assert [row["payment_id"] for row in target["vouchers"]] == [payment_id]
    assert (await runtime.store.read("dmm:bob"))["vouchers"] == []


async def test_sessions_without_a_platform_login_are_their_own_identity(world):
    _, runtime, _ = world
    session = runtime.sessions.create("dmm:solo")
    assert session.login == "dmm:solo"
    with pytest.raises(ApiRejected):
        runtime.data_links.execute(session, attempt("ZZZZZZZZ"), NOW)
