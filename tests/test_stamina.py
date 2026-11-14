from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
import pytest

from monmusu_ps.app import RUNTIME, create_app
from monmusu_ps.config import RuntimeConfig, Settings
from monmusu_ps.core.calendar import Calendar
from monmusu_ps.core.context import Context
from monmusu_ps.core.errors import ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.journal import Journal
from monmusu_ps.core.mail import Mailbox
from monmusu_ps.core.masterdata import MasterData
from monmusu_ps.core.progression import Progression
from monmusu_ps.core.resources import InsufficientResource, Resources
from monmusu_ps.core.store import PlayerStore
from monmusu_ps.core.sync import envelope
from monmusu_ps.core.transport import DecodedRequest, Protocol, encode_response
from monmusu_ps.features.stamina import restore_coin, restore_item
from test_server import NativeClient


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000


@pytest.fixture
def ctx(tmp_path):
    masters = MasterData(ROOT / "masterdata")
    path = tmp_path / "settings.json"
    path.write_bytes((ROOT / "server/data/settings.json").read_bytes())
    config = RuntimeConfig(path)
    progression = Progression(masters, config, Journal(config))
    runtime = SimpleNamespace(
        masters=masters,
        config=config,
        resources=Resources(
            masters, config, Mailbox(config, Calendar(masters)), progression
        ),
        progression=progression,
    )
    state = create_player("stamina", "TEST0001", masters, config.read(), NOW)
    return Context(runtime, {}, state, None, NOW)


def stamina(ctx):
    return ctx.state["modules"]["stamina"][0]


def fund(ctx, rid, amount):
    ctx.runtime.resources.grant(ctx.state, [{"rid": rid, "val": amount}], ctx.now)


def test_coin_uses_real_price_and_natural_capacity(ctx):
    assert ctx.runtime.masters.property("Int", 18) == 100
    ctx.payload = {"num": 2}
    result = restore_coin(ctx)
    assert stamina(ctx)["amount"] == 210
    assert ctx.runtime.resources.balance(ctx.state, "wallet.point.free_coin") == 100
    assert ctx.runtime.resources.balance(ctx.state, "wallet.point.gold") == 500
    assert envelope(result, NOW)["data"] == {"data": {"_z0": 0}}
    assert result.sync["module_prop"] == [
        {"scope": "player@TEST0001", "data": {"stamina": [stamina(ctx)]}}
    ]
    assert result.sync["wallet"][0]["data"][0]["name"] == "free_coin"
    assert result.sync["wallet"][0]["data"][0]["val"] == 100


def test_coin_spends_free_then_paid_currency(ctx):
    ctx.runtime.resources.spend(
        ctx.state, [{"rid": "wallet.point.free_coin", "val": 150}], NOW
    )
    fund(ctx, "wallet.point.paid_coin", 100)
    ctx.payload = {"num": 2}
    result = restore_coin(ctx)
    assert ctx.runtime.resources.balance(ctx.state, "wallet.point.free_coin") == 0
    assert ctx.runtime.resources.balance(ctx.state, "wallet.point.paid_coin") == 50
    assert stamina(ctx)["amount"] == 210
    assert {(row["name"], row["val"]) for row in result.sync["wallet"][0]["data"]} == {
        ("free_coin", 0),
        ("paid_coin", 50),
    }


def test_coin_uses_rank_changed_capacity(ctx):
    ctx.runtime.progression.add_player_exp(ctx.state, 600, NOW)
    stamina(ctx)["amount"] = 20
    ctx.payload = {"num": 1}
    restore_coin(ctx)
    assert stamina(ctx)["capacity"] == 74
    assert stamina(ctx)["amount"] == 94


@pytest.mark.parametrize(
    "name,count,recovery", [("stamina_m", 3, 150), ("stamina_xxl", 2, 500)]
)
def test_real_potions_restore_across_natural_capacity(ctx, name, count, recovery):
    fund(ctx, f"wallet.potion.{name}", count)
    stamina(ctx)["amount"] = 25
    ctx.payload = {"name": name, "num": count}
    result = restore_item(ctx)
    assert stamina(ctx)["amount"] == 25 + recovery
    assert ctx.runtime.resources.balance(ctx.state, f"wallet.potion.{name}") == 0
    assert ctx.runtime.resources.balance(ctx.state, "wallet.point.free_coin") == 300
    assert result.data == {"data": {"_z0": 0}}
    assert result.sync["wallet"][0]["data"][0]["category"] == "potion"


def test_restoration_can_reach_exactly_the_absolute_cap(ctx):
    fund(ctx, "wallet.potion.stamina_m", 1)
    stamina(ctx)["amount"] = 949
    ctx.payload = {"name": "stamina_m", "num": 1}
    restore_item(ctx)
    assert stamina(ctx)["amount"] == 999
    assert ctx.runtime.resources.balance(ctx.state, "wallet.potion.stamina_m") == 0


@pytest.mark.parametrize("route", ["coin", "item"])
def test_excessive_restoration_is_not_truncated_or_charged(ctx, route):
    fund(ctx, "wallet.potion.stamina_m", 2)
    stamina(ctx)["amount"] = 950
    ctx.payload = {"num": 1} if route == "coin" else {"name": "stamina_m", "num": 1}
    before = deepcopy(ctx.state)
    with pytest.raises(ApiRejected) as caught:
        (restore_coin if route == "coin" else restore_item)(ctx)
    assert caught.value.reason == "EXCESSIVE_STAMINA"
    assert ctx.state == before


@pytest.mark.parametrize("route", ["coin", "item"])
def test_insufficient_balance_does_not_grant_stamina(ctx, route):
    ctx.payload = {"num": 4} if route == "coin" else {"name": "stamina_m", "num": 1}
    before = deepcopy(ctx.state)
    with pytest.raises(InsufficientResource):
        (restore_coin if route == "coin" else restore_item)(ctx)
    assert ctx.state == before


@pytest.mark.parametrize("count", [0, -1, 1.5, "2", True])
@pytest.mark.parametrize("handler", [restore_coin, restore_item])
def test_invalid_counts_are_rejected_without_changes(ctx, count, handler):
    ctx.payload = {"name": "stamina_m", "num": count}
    before = deepcopy(ctx.state)
    with pytest.raises(ValueError, match="positive Int32"):
        handler(ctx)
    assert ctx.state == before


def test_item_name_is_a_potion_name_not_a_resource_id(ctx):
    fund(ctx, "wallet.potion.stamina_m", 1)
    ctx.payload = {"name": "wallet.potion.stamina_m", "num": 1}
    before = deepcopy(ctx.state)
    with pytest.raises(StopIteration):
        restore_item(ctx)
    assert ctx.state == before


def test_elapsed_recovery_and_paid_recovery_have_one_final_sync(ctx):
    fund(ctx, "wallet.potion.stamina_m", 1)
    stamina(ctx)["amount"] = 20
    ctx.now += 650
    ctx.payload = {"name": "stamina_m", "num": 1}
    result = restore_item(ctx)
    assert stamina(ctx)["amount"] == 72
    assert stamina(ctx)["latest_recovered_at"] == ctx.now
    assert result.sync["module_prop"][0]["data"]["stamina"] == [stamina(ctx)]


@pytest.mark.parametrize("percent,expected", [(100, 70), (150, 70), (200, 140)])
def test_percentage_effect_matches_native_integer_conversion(
    ctx, monkeypatch, percent, expected
):
    fund(ctx, "wallet.potion.stamina_m", 1)
    rows = deepcopy(ctx.runtime.masters.table("wallet_template"))
    potion = next(
        row
        for row in rows
        if row["category"] == "potion" and row["name"] == "stamina_m"
    )
    potion.update(potion_eff_type="P", potion_eff_val=percent)
    original = ctx.runtime.masters.table
    monkeypatch.setattr(
        ctx.runtime.masters,
        "table",
        lambda name: rows if name == "wallet_template" else original(name),
    )
    stamina(ctx)["amount"] = 0
    ctx.payload = {"name": "stamina_m", "num": 1}
    restore_item(ctx)
    assert stamina(ctx)["amount"] == expected


async def test_failed_transaction_keeps_the_saved_balance_and_timer(ctx, tmp_path):
    stamina(ctx)["amount"] = 10
    initial = deepcopy(ctx.state)
    store = PlayerStore(tmp_path / "saves", lambda _: deepcopy(initial))
    before = await store.read("stamina")
    with pytest.raises(InsufficientResource):
        async with store.transaction("stamina") as state:
            ctx.state = state
            ctx.now += 650
            ctx.payload = {"num": 4}
            restore_coin(ctx)
    assert await store.read("stamina") == before


async def test_successful_repeated_restoration_persists_every_cost(ctx, tmp_path):
    fund(ctx, "wallet.potion.stamina_m", 3)
    initial = deepcopy(ctx.state)
    store = PlayerStore(tmp_path / "saves", lambda _: deepcopy(initial))
    for _ in range(3):
        async with store.transaction("stamina") as state:
            ctx.state = state
            ctx.payload = {"name": "stamina_m", "num": 1}
            restore_item(ctx)
    saved = await store.read("stamina")
    assert saved["modules"]["stamina"][0]["amount"] == 220
    assert ctx.runtime.resources.balance(saved, "wallet.potion.stamina_m") == 0


@pytest.mark.parametrize(
    "endpoint,payload,recovery",
    [
        ("player/restore/stamina/coin", {"num": 1}, 70),
        ("player/restore/stamina/item", {"name": "stamina_m", "num": 1}, 50),
    ],
)
async def test_http_capacity_rejection_and_success_dtos(
    tmp_path, endpoint, payload, recovery
):
    app = create_app(Settings.load(ROOT, tmp_path / "saves"), clock=lambda: NOW)
    runtime = app[RUNTIME]
    async with TestClient(TestServer(app)) as http:
        client = NativeClient(http)
        await client.login()
        profile = runtime.sessions.get(client.token).profile
        async with runtime.store.transaction(profile) as state:
            runtime.resources.grant(
                state, [{"rid": "wallet.potion.stamina_m", "val": 1}], NOW
            )
            state["modules"]["stamina"][0]["amount"] = 950
        before = await runtime.store.read(profile)
        request_body = encode_response(
            payload, DecodedRequest({}, Protocol.AES, None), client.password
        )
        response = await http.post(
            "/api/" + endpoint,
            data=request_body,
            headers={"x-ag-protocol": "2", "x-ag-token": client.token},
        )
        assert response.status == 422
        assert response.headers["x-ag-protocol"] == "1"
        rejected = await response.json()
        assert set(rejected) == {"status", "data"}
        assert rejected["status"]["code"] == 90000006
        assert rejected["data"] == {"rejected_reason": "EXCESSIVE_STAMINA"}
        assert await runtime.store.read(profile) == before

        async with runtime.store.transaction(profile) as state:
            state["modules"]["stamina"][0]["amount"] = 999 - recovery
        accepted = await client.call(endpoint, payload)
        assert accepted["data"] == {"data": {"_z0": 0}}
        assert accepted["sync"]["module_prop"][0]["data"]["stamina"][0]["amount"] == 999
        after = await runtime.store.read(profile)
        balance_id = (
            "wallet.point.stone"
            if endpoint.endswith("coin")
            else "wallet.potion.stamina_m"
        )
        assert runtime.resources.balance(after, balance_id) == (
            200 if endpoint.endswith("coin") else 0
        )
