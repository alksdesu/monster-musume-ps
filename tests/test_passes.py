import pytest

from monmusu_ps.core.errors import ApiNotFound
from monmusu_ps.core.passes import FREE, PREMIUM
from monmusu_ps.core.transport import DecodedRequest, Protocol, encode_response
from monmusu_ps.features.bonus_pass import ROUTES
from test_battle import connect
from test_gacha import NOW, fund, official, runtime, state, synced, wallet
from test_gacha_modes import limit, sdk
from test_shop import buy, redeem


__all__ = ["runtime", "state"]

HOUR = 3600
DAY = 24 * HOUR
MONTHLY = 9105
LAST_REQUIREMENT, LAST_TIER = 2800, 60
POINTS = "bonus_pass.point.9101"
UNLOCK = "bonus_pass.unlock_pass.9101-2"
PASS_SHOP = 200447
PASS_SKU = "com.dmm.dmmgames.monmusutd.monthlypass.dmm"
POINT_PACK = 316201
MEDAL = "wallet.event.pt_gosyuzinmedal2603"
POTION = "wallet.potion.stamina_m"
AFTER_END = NOW + 18 * DAY + 8 * HOUR
AFTER_CLOSE = NOW + 30 * DAY
LOCKED = {"is_locked": True, "received_tier": 0, "remind_tier": 0}


def receive(runtime, state, pass_id=MONTHLY, now=NOW):
    return runtime.bonus_passes.receive(state, {"pass_id": pass_id}, now)


def checkoff(runtime, state, tier, pass_id=MONTHLY):
    payload = {"pass_id": pass_id, "tier": tier}
    return runtime.bonus_passes.checkoff(state, payload, NOW)


def claims(result):
    return [(row["track"], row["tier"]) for row in result.data["received_list"]]


def gains(result, index):
    return [
        (row["rid"], row["val"], row["inc"], row["inbox"], row["over"])
        for row in result.data["received_list"][index]["rewards"]
    ]


def progression(state, pass_id=MONTHLY):
    return next(row for row in state["bonus_passes"] if row["pass_id"] == pass_id)


def fields(runtime, model):
    return {field["name"] for field in runtime.models[model]["fields"]}


def grant(runtime, state, rid, amount, now=NOW):
    changes = runtime.resources.grant(state, [{"rid": rid, "val": amount}], now)
    return changes.deliveries[0]


async def put(http, client, endpoint, payload):
    body = encode_response(
        payload, DecodedRequest({}, Protocol.AES, None), client.password
    )
    return await http.put(
        "/api/" + endpoint,
        data=body,
        headers={"x-ag-protocol": "2", "x-ag-token": client.token},
    )


def test_running_passes_are_listed_like_the_official_sample(runtime, state):
    _, response = official("bonus_pass/progressions")
    listed = runtime.bonus_passes.progressions(state, NOW)
    sample = response["data"]["progressions"]
    assert [{**row, "tier_progressed_at": 0} for row in listed] == [
        {**row, "tier_progressed_at": 0} for row in sample
    ]
    assert listed[0]["tier_progressed_at"] == NOW
    assert state["bonus_passes"] == listed
    assert runtime.bonus_passes.progressions(state, NOW + 5) == listed


def test_closed_passes_leave_the_list(runtime, state):
    fund(runtime, state, POINTS, 10)
    assert runtime.bonus_passes.progressions(state, AFTER_END) == [progression(state)]
    assert runtime.bonus_passes.progressions(state, AFTER_CLOSE) == []
    assert progression(state)["progression"] == 10


def test_free_rewards_are_mailed_tier_by_tier(runtime, state):
    fund(runtime, state, POINTS, 30)
    result = receive(runtime, state)
    assert result.data["pass_id"] == MONTHLY
    assert claims(result) == [(FREE, 1), (FREE, 2), (FREE, 3)]
    assert gains(result, 0) == [(MEDAL, 20, 0, 20, 0), (POTION, 2, 0, 2, 0)]
    row = progression(state)
    assert row["free_track"] == {
        "is_locked": False,
        "received_tier": 3,
        "remind_tier": 0,
    }
    assert row["premium_track"] == LOCKED
    assert runtime.resources.balance(state, POTION) == 0
    assert [(row["reward"], row["tags"], row["message"]) for row in state["inbox"]][
        :2
    ] == [
        ({"rid": MEDAL, "val": 20}, ["bnspass.reward:9105:1:1", "wallet"], None),
        ({"rid": POTION, "val": 2}, ["bnspass.reward:9105:1:1", "wallet"], None),
    ]
    assert len(state["inbox"]) == 6
    assert synced(result)["bonuspass_progression"] == [row]
    again = receive(runtime, state)
    assert again.data == {"pass_id": MONTHLY, "received_list": []}
    assert not again.sync
    assert len(state["inbox"]) == 6


def test_later_tiers_only_bring_their_own_rewards(runtime, state):
    fund(runtime, state, POINTS, 10)
    assert claims(receive(runtime, state)) == [(FREE, 1)]
    fund(runtime, state, POINTS, 20)
    assert claims(receive(runtime, state)) == [(FREE, 2), (FREE, 3)]


def test_premium_rewards_wait_for_the_unlock(runtime, state):
    fund(runtime, state, POINTS, 20)
    assert claims(receive(runtime, state)) == [(FREE, 1), (FREE, 2)]
    fund(runtime, state, UNLOCK, 1)
    assert progression(state)["premium_track"]["is_locked"] is False
    result = receive(runtime, state)
    assert claims(result) == [(PREMIUM, 1), (PREMIUM, 2)]
    assert gains(result, 0)[0] == (MEDAL, 110, 0, 110, 0)
    assert state["inbox"][-1]["tags"] == ["bnspass.reward:9105:2:2", "wallet"]
    fund(runtime, state, POINTS, 10)
    assert claims(receive(runtime, state)) == [(FREE, 3), (PREMIUM, 3)]
    assert progression(state)["premium_track"]["received_tier"] == 3


def test_the_premium_track_is_unlocked_once(runtime, state):
    first = runtime.resources.grant(state, [{"rid": UNLOCK, "val": 1}], NOW)
    assert first.deliveries == [
        {"rid": UNLOCK, "val": 1, "inc": 1, "inbox": 0, "over": 0}
    ]
    assert first.updates["bonuspass_progression"] == [progression(state)]
    assert progression(state)["free_track"]["is_locked"] is False
    assert grant(runtime, state, UNLOCK, 1) == {
        "rid": UNLOCK,
        "val": 1,
        "inc": 0,
        "inbox": 0,
        "over": 1,
    }
    assert len(state["bonus_passes"]) == 1


@pytest.mark.parametrize("now", [AFTER_END, AFTER_CLOSE])
def test_unlocks_outside_the_season_are_lost(runtime, state, now):
    assert grant(runtime, state, UNLOCK, 1, now)["over"] == 1
    assert state["bonus_passes"] == []
    assert not runtime.resources.redeemable(state, UNLOCK, now)
    assert not runtime.resources.redeemable(state, POINTS, now)


def test_pass_resources_are_entitlements_without_a_balance(runtime, state):
    assert runtime.resources.entitlement(UNLOCK)
    assert runtime.resources.entitlement(POINTS)
    assert not runtime.resources.entitlement(POTION)
    assert runtime.resources.redeemable(state, UNLOCK, NOW)
    assert runtime.resources.redeemable(state, POINTS, NOW)
    assert runtime.resources.redeemable(state, POTION, AFTER_CLOSE)
    with pytest.raises(ValueError):
        runtime.resources.balance(state, UNLOCK)


def test_points_stop_at_the_last_tier(runtime, state):
    first = grant(runtime, state, POINTS, LAST_REQUIREMENT - 10)
    assert (first["inc"], first["over"]) == (LAST_REQUIREMENT - 10, 0)
    second = grant(runtime, state, POINTS, 30)
    assert (second["inc"], second["over"]) == (10, 20)
    third = grant(runtime, state, POINTS, 10)
    assert (third["inc"], third["over"]) == (0, 10)
    row = progression(state)
    assert (row["progression"], row["tier"]) == (LAST_REQUIREMENT, LAST_TIER)
    assert len(claims(receive(runtime, state))) == LAST_TIER


def test_ended_passes_stop_earning_but_keep_their_rewards(runtime, state):
    fund(runtime, state, POINTS, 20)
    assert grant(runtime, state, POINTS, 10, AFTER_END)["over"] == 10
    assert progression(state)["tier"] == 2
    assert claims(receive(runtime, state, now=AFTER_END)) == [(FREE, 1), (FREE, 2)]


def test_closed_passes_give_nothing(runtime, state):
    fund(runtime, state, POINTS, 20)
    result = receive(runtime, state, now=AFTER_CLOSE)
    assert result.data == {"pass_id": MONTHLY, "received_list": []}
    assert progression(state)["free_track"]["received_tier"] == 0
    assert state["inbox"] == []


@pytest.mark.parametrize("pass_id", [0, 99999, "9105", None])
def test_unknown_passes_are_not_found(runtime, state, pass_id):
    with pytest.raises(ApiNotFound):
        receive(runtime, state, pass_id)
    with pytest.raises(ApiNotFound):
        checkoff(runtime, state, 0, pass_id)


def test_passes_without_progress_have_nothing_to_receive(runtime, state):
    assert receive(runtime, state).data == {"pass_id": MONTHLY, "received_list": []}
    assert state["bonus_passes"] == []
    with pytest.raises(ApiNotFound):
        checkoff(runtime, state, 0)


def test_checkoff_remembers_the_reminded_tier(runtime, state):
    fund(runtime, state, POINTS, 30)
    result = checkoff(runtime, state, 2)
    assert result.data is None
    row = progression(state)
    assert row["free_track"]["remind_tier"] == 2
    assert row["premium_track"]["remind_tier"] == 2
    assert synced(result)["bonuspass_progression"] == [row]
    assert not checkoff(runtime, state, 1).sync
    assert progression(state)["free_track"]["remind_tier"] == 2
    checkoff(runtime, state, 3)
    assert progression(state)["premium_track"]["remind_tier"] == 3


@pytest.mark.parametrize("tier", [-1, 4, "1", None, True, 1.0])
def test_checkoff_rejects_tiers_the_pass_has_not_reached(runtime, state, tier):
    fund(runtime, state, POINTS, 30)
    with pytest.raises(ValueError):
        checkoff(runtime, state, tier)
    assert progression(state)["free_track"]["remind_tier"] == 0


def test_buying_the_monthly_pass_unlocks_the_premium_track(runtime, state):
    fund(runtime, state, POINTS, 10)
    result = redeem(runtime, state, PASS_SKU)
    assert wallet(state, "paid_coin")["val"] == 1800
    assert state["inbox"] == []
    row = progression(state)
    assert row["premium_track"]["is_locked"] is False
    assert synced(result)["bonuspass_progression"] == [row]
    assert limit(state, f"shop.{PASS_SHOP}")["times"] == 1
    assert claims(receive(runtime, state)) == [(FREE, 1), (PREMIUM, 1)]
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, PASS_SKU, 1, NOW)


@pytest.mark.parametrize("now", [AFTER_END, AFTER_CLOSE])
def test_the_monthly_pass_is_not_sold_outside_its_season(runtime, state, now):
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, PASS_SKU, 1, now)
    assert state["vouchers"] == []


def test_an_unlocked_pass_is_not_sold_again(runtime, state):
    fund(runtime, state, UNLOCK, 1)
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, PASS_SKU, 1, NOW)
    assert state["vouchers"] == []


def test_points_can_be_bought_with_stones(runtime, state):
    result = buy(runtime, state, POINT_PACK, 2)
    row = progression(state)
    assert (row["progression"], row["tier"]) == (20, 2)
    assert wallet(state, "free_coin")["val"] == 300 - 60
    assert state["inbox"] == []
    assert limit(state, f"shop.{POINT_PACK}")["times"] == 2
    changed = synced(result)
    assert changed["bonuspass_progression"] == [row]
    assert set(changed) == {"wallet", "limit", "bonuspass_progression"}
    with pytest.raises(ApiNotFound):
        buy(runtime, state, POINT_PACK, 1, AFTER_END)


def test_pass_routes_and_models(runtime, state):
    assert set(ROUTES) == {
        "bonus_pass/progressions",
        "bonus_pass/tier/receive_reward",
        "bonus_pass/progression/checkoff",
    }
    assert not runtime.has_response_data("bonus_pass/progression/checkoff")
    listed = runtime.bonus_passes.progressions(state, NOW)
    assert set(listed[0]) == fields(runtime, "BonusPassProgression")
    assert set(listed[0]["free_track"]) == fields(runtime, "BonusPassTrackStatus")
    fund(runtime, state, POINTS, 10)
    data = receive(runtime, state).data
    assert set(data) == fields(runtime, "ReceiveBonusPassTierRewardResponse200Data")
    assert set(data["received_list"][0]) == fields(runtime, "ReceivedTierRewardResult")
    assert set(data["received_list"][0]["rewards"][0]) <= fields(
        runtime, "ResourceDeliverResult"
    )


async def test_native_pass_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        listed = await client.call("bonus_pass/progressions", {"_z0": 0})
        assert [row["pass_id"] for row in listed["data"]["progressions"]] == [MONTHLY]
        bought = await client.call(
            "sales/exchange/item", {"shop_id": POINT_PACK, "quantity": 2}
        )
        assert bought["sync"]["bonuspass_progression"][0]["data"][0]["tier"] == 2
        issued = await sdk(
            http,
            command="payment",
            itemId=PASS_SKU,
            itemName="マンスリーパス",
            unitPrice=1800,
            quantity=1,
        )
        assert issued["result_code"] == 0
        unlocked = await client.call(
            "sales/exchange/item/payment", {"payment_id": issued["payment_id"]}
        )
        row = unlocked["sync"]["bonuspass_progression"][0]["data"][0]
        assert row["premium_track"]["is_locked"] is False
        received = await client.call(
            "bonus_pass/tier/receive_reward", {"pass_id": MONTHLY}
        )
        assert received["data"]["pass_id"] == MONTHLY
        assert [
            (row["track"], row["tier"]) for row in received["data"]["received_list"]
        ] == [(FREE, 1), (PREMIUM, 1), (FREE, 2), (PREMIUM, 2)]
        checked = await client.call(
            "bonus_pass/progression/checkoff",
            {"pass_id": MONTHLY, "tier": 2},
            method="PUT",
        )
        assert checked["status"]["code"] == 1
        assert checked.get("data") is None
        unreached = await put(
            http,
            client,
            "bonus_pass/progression/checkoff",
            {"pass_id": MONTHLY, "tier": 9},
        )
        assert unreached.status == 400
        unknown = await put(
            http,
            client,
            "bonus_pass/progression/checkoff",
            {"pass_id": 99999, "tier": 0},
        )
        assert unknown.status == 404
        relisted = await client.call("bonus_pass/progressions", {"_z0": 0})
        row = relisted["data"]["progressions"][0]
        assert (row["tier"], row["progression"]) == (2, 20)
        assert row["free_track"] == {
            "is_locked": False,
            "received_tier": 2,
            "remind_tier": 2,
        }
        assert row["premium_track"] == {
            "is_locked": False,
            "received_tier": 2,
            "remind_tier": 2,
        }
        saved = await runtime.store.read(profile)
        assert progression(saved) == row
        assert len(saved["inbox"]) == 10
    finally:
        await http.close()
