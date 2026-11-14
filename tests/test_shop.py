from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import unit
from monmusu_ps.core.payments import SHOP
from monmusu_ps.core.progression import player_rank
from monmusu_ps.features.shop import ROUTES
from test_battle import connect, post
from test_gacha import NOW, fund, official, runtime, state, synced, wallet
from test_gacha_modes import limit, sdk
from test_periods import announce


__all__ = ["runtime", "state"]

HOUR = 3600
DAY = 24 * HOUR
JST = timezone(timedelta(hours=9))
GOLD_PACK = 9001
EXP_BOOK = 9005
ANCIENT_KEY = 300108
WEEKLY_PACK = 150000
WEEKLY_LOOTBOX = "wallet.lootbox.lb_weeklypack231204_sr"
EQUIP_SET = 900000
EQUIP_TICKET = "wallet.selectbox.231130_c"
START_DASH_UNIT, LATE_START_DASH_UNIT = 500001, 500002
START_DASH_MEDAL = "wallet.gachamedal.gmedal_sdgacha01"
START_DASH_CARD = 177
BLESSING, COST_BLESSING = 10000, 10001
SUBJUGATION_EVENT, ENDED_EVENT = 63, 137
STONES, SMALL_STONES = 100005, 100006
STONE_SKU = "com.dmm.dmmgames.monmusutd.stone_2.dmm"
SMALL_STONE_SKU = "com.dmm.dmmgames.monmusutd.stone_1.dmm"
BEGINNER_PACK = 591001
BOSS_PACK_SKU = "com.dmm.dmmgames.monmusutd.boss_a.dmm"
BEGINNER_SKU = "com.dmm.dmmgames.monmusutd.beginner_a.dmm"
RANK_PACK = 590010
RANK_SKU = "com.dmm.dmmgames.monmusutd.rank_010.dmm"
FOREIGN_PACK = 511001
LOGIN_PACK_SKU = "com.dmm.dmmgames.monmusutd.2_5annivbeginner.dmm"
DEFENSE_PACK_SKU = "com.dmm.dmmgames.monmusutd.tdyarozeex3_500pt.dmm"
MEDICINE, EXPIRED_MEDICINE = 103, 600101
MEDICINE_ITEM = "item.spapp.warrior_sr"
EPIC_WARRIOR, COMMON_WARRIOR, EPIC_ROGUE = 10, 11, 23
PAID_COIN, FREE_COIN, GOLD = (
    "wallet.point.paid_coin",
    "wallet.point.free_coin",
    "wallet.point.gold",
)
FAVOR = {
    "no": 10,
    "start_at": 1792378800,
    "end_at": 253402300799,
    "is_acceptable_of_card": True,
}


def goods(runtime, shop_id):
    return runtime.masters.lookup("shop_data", "id", shop_id)


def spoil(runtime, shop_id):
    reward = goods(runtime, shop_id)["shopItems"]["m_rewards"][0]
    reward.update(m_walletCategory="blessing", m_walletName="blessing.gift.debuff")


def buy(runtime, state, shop_id, quantity=1, now=NOW):
    payload = {"shop_id": shop_id, "quantity": quantity}
    return runtime.shop.exchange(state, payload, now)


def redeem(runtime, state, sku, now=NOW):
    payment_id = runtime.payments.issue(state, sku, 1, now)
    voucher, shop_id = runtime.payments.claim(state, payment_id, SHOP)
    result = runtime.shop.redeem(state, shop_id, voucher["quantity"], now)
    runtime.payments.settle(state, voucher)
    return result


def awaken(runtime, state, instance_id, shop_id=MEDICINE, now=NOW):
    payload = {"shop_id": shop_id, "instance_id": instance_id}
    return runtime.shop.awaken(state, payload, now)


def sell(runtime, state, *orders, now=NOW):
    payload = {
        "sell_orders": [
            {"resource_id": rid, "amount": amount} for rid, amount in orders
        ]
    }
    return runtime.shop.sell(state, payload, now)


def untouched(state, error, action):
    before = deepcopy(state)
    with pytest.raises(error) as caught:
        action()
    assert state == before
    return caught.value


def member(state, card_id):
    owned = next((row for row in state["units"] if row["card_id"] == card_id), None)
    if owned is not None:
        return owned
    sequences = state["resource_sequences"]
    row = unit(sequences["unit"], card_id, NOW)
    sequences["unit"] += 1
    state["units"].append(row)
    return row


def letters(state):
    return [(row["reward"], row["tags"], row["message"]) for row in state["inbox"]]


def boxed_rewards(runtime, shop_id):
    orders = (
        runtime.resources.reward_order(reward)
        for reward in goods(runtime, shop_id)["shopItems"]["m_rewards"]
    )
    return [order for order in orders if order and order["rid"] != PAID_COIN]


def windows(state):
    return {row["id"]: row["period"] for row in state["periods"]}


def test_goods_cost_their_price_and_count_the_purchase(runtime, state):
    result = buy(runtime, state, GOLD_PACK, 3)
    assert result.data == {"_z0": 0}
    assert wallet(state, "gold")["val"] == 500 - 30 + 3000
    counter = {"id": "shop.9001", "times": 3, "counted_at": NOW}
    assert limit(state, "shop.9001") == counter
    assert synced(result) == {"wallet": [wallet(state, "gold")], "limit": [counter]}
    event = state["events"][-1]
    assert (event["type"], event["at"]) == ("shop_exchange", NOW)
    assert event["data"] == {
        "shop_id": GOLD_PACK,
        "quantity": 3,
        "costs": [{"rid": GOLD, "val": 30}],
        "rewards": [{"rid": GOLD, "val": 3000}],
    }


def test_unlimited_goods_keep_counting(runtime, state):
    buy(runtime, state, GOLD_PACK, 2)
    buy(runtime, state, GOLD_PACK, 5, NOW + 9)
    counter = {"id": "shop.9001", "times": 7, "counted_at": NOW + 9}
    assert limit(state, "shop.9001") == counter


def test_poor_players_are_turned_away(runtime, state):
    error = untouched(state, ApiRejected, lambda: buy(runtime, state, ANCIENT_KEY))
    assert error.reason == "NOT_ENOUGH_CURRENCY"


@pytest.mark.parametrize("quantity", [0, -1, "1", True, None, 1.0])
def test_quantities_must_be_positive_integers(runtime, state, quantity):
    untouched(state, ValueError, lambda: buy(runtime, state, GOLD_PACK, quantity))


@pytest.mark.parametrize("shop_id", [1, FOREIGN_PACK, "9001", None])
def test_unknown_and_foreign_goods_are_not_found(runtime, state, shop_id):
    untouched(state, ApiNotFound, lambda: buy(runtime, state, shop_id))


@pytest.mark.parametrize(
    "term",
    [
        {"endDate": "2026/11/11 04:46:40"},
        {"startDate": "2026/11/11 04:46:41"},
    ],
)
def test_goods_outside_their_term_are_not_found(runtime, state, term):
    goods(runtime, GOLD_PACK).update(term)
    untouched(state, ApiNotFound, lambda: buy(runtime, state, GOLD_PACK))


def test_goods_inside_their_term_are_sold(runtime, state):
    goods(runtime, GOLD_PACK).update(
        startDate="2026/11/11 04:46:40", endDate="2026/11/11 04:46:41"
    )
    buy(runtime, state, GOLD_PACK)
    assert wallet(state, "gold")["val"] == 1490


@pytest.mark.parametrize(
    ("cycle", "span"),
    [
        ({"LimitResetTrigger": "daily"}, DAY),
        ({"LimitResetTrigger": "weekly", "DayOfWeekForReset": 1}, 7 * DAY),
        ({"LimitResetTrigger": "monthly", "DayOfMonthForReset": 1}, 31 * DAY),
        (
            {
                "LimitResetTrigger": "datetime",
                "DateTimeForReset": datetime.fromtimestamp(NOW + DAY, JST).strftime(
                    "%Y/%m/%d %H:%M:%S"
                ),
            },
            DAY,
        ),
    ],
)
def test_purchase_limits_restart_with_their_cycle(runtime, state, cycle, span):
    goods(runtime, GOLD_PACK).update(buylimit=2, **cycle)
    buy(runtime, state, GOLD_PACK, 2)
    untouched(state, ApiNotFound, lambda: buy(runtime, state, GOLD_PACK))
    later = NOW + span
    buy(runtime, state, GOLD_PACK, 1, later)
    assert limit(state, "shop.9001") == {
        "id": "shop.9001",
        "times": 1,
        "counted_at": later,
    }


def test_lifetime_limits_never_restart(runtime, state):
    goods(runtime, GOLD_PACK).update(buylimit=2)
    buy(runtime, state, GOLD_PACK)
    untouched(state, ApiNotFound, lambda: buy(runtime, state, GOLD_PACK, 2))
    buy(runtime, state, GOLD_PACK, 1, NOW + 400 * DAY)
    later = NOW + 800 * DAY
    untouched(state, ApiNotFound, lambda: buy(runtime, state, GOLD_PACK, 1, later))


def test_the_free_weekly_pack_is_claimed_once_a_week(runtime, state):
    result = buy(runtime, state, WEEKLY_PACK)
    assert runtime.resources.balance(state, WEEKLY_LOOTBOX) == 1
    assert state["events"][-1]["data"]["costs"] == []
    assert set(synced(result)) == {"wallet", "limit"}
    untouched(state, ApiNotFound, lambda: buy(runtime, state, WEEKLY_PACK))
    buy(runtime, state, WEEKLY_PACK, 1, NOW + 7 * DAY)
    assert runtime.resources.balance(state, WEEKLY_LOOTBOX) == 2


def test_replacement_goods_share_the_original_counter(runtime, state):
    goods(runtime, GOLD_PACK)["hotfix_shop_id"] = 7
    result = buy(runtime, state, GOLD_PACK)
    counter = {"id": "shop.7", "times": 1, "counted_at": NOW}
    assert synced(result)["limit"] == [counter]
    assert limit(state, "shop.9001") is None


def test_timed_goods_follow_their_sales_window(runtime, state):
    fund(runtime, state, START_DASH_MEDAL, 3000)
    untouched(state, ApiNotFound, lambda: buy(runtime, state, START_DASH_UNIT))
    runtime.tutorial.finish(state, NOW)
    assert windows(state)["sales.500001"] == {
        "start_at": NOW,
        "end_at": NOW + 120 * HOUR,
    }
    result = buy(runtime, state, START_DASH_UNIT)
    recruit = synced(result)["unit"][0]
    assert recruit["card_id"] == START_DASH_CARD
    assert recruit in state["units"]
    assert runtime.resources.balance(state, START_DASH_MEDAL) == 1500
    untouched(state, ApiNotFound, lambda: buy(runtime, state, START_DASH_UNIT))
    closed = NOW + 120 * HOUR
    untouched(
        state,
        ApiNotFound,
        lambda: buy(runtime, state, LATE_START_DASH_UNIT, 1, closed),
    )
    buy(runtime, state, LATE_START_DASH_UNIT, 1, closed - 1)


def test_fully_awakened_units_are_not_sold_again(runtime, state):
    fund(runtime, state, START_DASH_MEDAL, 3000)
    runtime.tutorial.finish(state, NOW)
    owned = member(state, START_DASH_CARD)
    owned["upa"]["all_pt"] = 4
    goods(runtime, START_DASH_UNIT)["buylimit"] = 0
    buy(runtime, state, START_DASH_UNIT)
    assert member(state, START_DASH_CARD)["upa"]["all_pt"] == 5
    error = untouched(state, ApiRejected, lambda: buy(runtime, state, START_DASH_UNIT))
    assert error.reason == "INVALID_UNIT_CONDITION"


def test_present_box_goods_arrive_by_mail(runtime, state):
    fund(runtime, state, EQUIP_TICKET, 1)
    result = buy(runtime, state, EQUIP_SET)
    assert runtime.resources.balance(state, EQUIP_TICKET) == 0
    assert letters(state) == [
        (
            {"rid": f"item.equip.{equip}", "val": 1},
            ["shop.product:900000", "item"],
            None,
        )
        for equip in (1101, 8101, 9001, 8301)
    ]
    assert all(
        runtime.resources.balance(state, f"item.equip.{equip}") == 0
        for equip in (1101, 8101, 9001, 8301)
    )
    assert set(synced(result)) == {"wallet", "limit"}


def test_goods_with_unsupported_rewards_are_not_sold(runtime, state):
    spoil(runtime, STONES)
    untouched(
        state,
        NotImplementedError,
        lambda: runtime.payments.issue(state, STONE_SKU, 1, NOW),
    )


def test_platform_goods_are_granted_through_a_voucher(runtime, state):
    error = untouched(state, ApiRejected, lambda: buy(runtime, state, STONES))
    assert error.reason == "NOT_ENOUGH_CURRENCY"
    payment_id = runtime.payments.issue(state, STONE_SKU, 1, NOW)
    voucher, shop_id = runtime.payments.claim(state, payment_id, SHOP)
    assert (voucher["product_type"], shop_id) == ("shop", "100005")
    result = runtime.shop.redeem(state, shop_id, voucher["quantity"], NOW)
    runtime.payments.settle(state, voucher)
    assert result.data == {"_z0": 0}
    assert wallet(state, "paid_coin")["val"] == 600
    assert limit(state, "shop.100005")["times"] == 1
    assert state["events"][-1]["data"]["costs"] == []
    assert state["vouchers"] == []
    with pytest.raises(ApiNotFound):
        runtime.payments.claim(state, payment_id, SHOP)


def test_vouchers_only_redeem_platform_goods(runtime, state):
    untouched(
        state, ApiNotFound, lambda: runtime.shop.redeem(state, str(GOLD_PACK), 1, NOW)
    )
    untouched(
        state, ApiNotFound, lambda: runtime.shop.redeem(state, str(MEDICINE), 1, NOW)
    )


def test_the_first_stone_pack_brings_the_bonus_unit_once(runtime, state):
    result = redeem(runtime, state, STONE_SKU)
    assert letters(state) == [
        (
            {"rid": "unit.card.69", "val": 1},
            ["shop.sp_favor:10", "unit"],
            "初購入特典",
        )
    ]
    received = {"id": "shop_sp_favor.10", "times": 1, "counted_at": NOW}
    assert received in synced(result)["limit"]
    redeem(runtime, state, STONE_SKU, NOW + 1)
    assert len(state["inbox"]) == 1
    assert limit(state, "shop_sp_favor.10") == received


def test_small_packs_bring_no_bonus_unit(runtime, state):
    redeem(runtime, state, SMALL_STONE_SKU)
    assert wallet(state, "paid_coin")["val"] == 120
    assert state["inbox"] == []
    assert limit(state, "shop_sp_favor.10") is None


def test_the_first_purchase_bonus_can_be_switched_off(runtime, state):
    runtime.config.read()["shop"]["first_purchase"] = None
    redeem(runtime, state, STONE_SKU)
    assert state["inbox"] == []
    assert runtime.shop.favors() == []


def test_special_favors_match_the_official_sample(runtime):
    _, response = official("sales/sp_favor/list")
    assert response["data"] == {"record_list": [FAVOR]}
    assert runtime.shop.favors() == [FAVOR]


def test_present_box_packs_keep_their_stones_direct(runtime, state):
    announce(runtime, state, NOW, 10004)
    result = redeem(runtime, state, BEGINNER_SKU)
    assert wallet(state, "paid_coin")["val"] == 3000
    boxed = boxed_rewards(runtime, BEGINNER_PACK)
    assert boxed[:3] == [
        {"rid": "wallet.ticket.gacha_commonl", "val": 1},
        {"rid": "item.material.Bom2", "val": 30},
        {"rid": "item.material.Red1", "val": 20},
    ]
    assert letters(state) == [
        (reward, ["shop.product:591001", reward["rid"].split(".")[0]], None)
        for reward in boxed
    ]
    assert limit(state, "shop.591001")["times"] == 1
    assert "item" not in synced(result)
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, BEGINNER_SKU, 1, NOW)


def test_rank_packs_open_when_the_rank_is_reached(runtime, state):
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, RANK_SKU, 1, NOW)
    fund(runtime, state, FREE_COIN, 22600)
    result = buy(runtime, state, EXP_BOOK, 226)
    assert player_rank(state) == 10
    opened = {"start_at": NOW, "end_at": NOW + DAY}
    assert windows(state) == {
        f"sales.{shop_id}": opened for shop_id in (510010, 520010, RANK_PACK)
    }
    assert len(synced(result)["period"]) == 3
    promoted = [row for row in state["events"] if row["type"] == "player_rank_up"]
    assert [row["data"] for row in promoted] == [{"from": 1, "to": 10}]
    redeem(runtime, state, RANK_SKU)
    assert wallet(state, "paid_coin")["val"] == 120
    assert [row["reward"] for row in state["inbox"]] == boxed_rewards(
        runtime, RANK_PACK
    )


def test_prep_packs_are_sold_once_their_mode_unlocks(runtime, state):
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, BOSS_PACK_SKU, 1, NOW)
    announce(runtime, state, NOW, 10107)
    redeem(runtime, state, BOSS_PACK_SKU)
    assert wallet(state, "paid_coin")["val"] == 2000
    assert wallet(state, "free_coin")["val"] == 300 + 160
    assert limit(state, "shop.591003")["times"] == 1
    boxed = [row["reward"]["rid"] for row in state["inbox"]]
    assert "skill.attachable.1030" in boxed


def test_later_experience_opens_no_second_window(runtime, state):
    fund(runtime, state, FREE_COIN, 23000)
    buy(runtime, state, EXP_BOOK, 226)
    before = deepcopy(state["periods"])
    buy(runtime, state, EXP_BOOK, 4, NOW + HOUR)
    assert state["periods"] == before


def test_orders_for_closed_goods_are_refused(runtime, state):
    untouched(
        state, ApiNotFound, lambda: runtime.payments.issue(state, BEGINNER_SKU, 1, NOW)
    )


def test_vouchers_for_closed_goods_are_dropped(runtime, state):
    announce(runtime, state, NOW, 10004)
    kept = runtime.payments.issue(state, STONE_SKU, 1, NOW)
    runtime.payments.issue(state, BEGINNER_SKU, 1, NOW)
    assert len(runtime.payments.pending(state, NOW)) == 2
    later = windows(state)["sales.591001"]["end_at"]
    assert later == NOW + 72 * HOUR
    assert len(runtime.payments.pending(state, later - 1)) == 2
    assert [row["payment_id"] for row in runtime.payments.pending(state, later)] == [
        kept
    ]
    assert [row["payment_id"] for row in state["vouchers"]] == [kept]


def test_awakening_medicine_raises_the_potential(runtime, state):
    target = member(state, EPIC_WARRIOR)
    fund(runtime, state, MEDICINE_ITEM, 4)
    result = awaken(runtime, state, target["instance_id"], now=NOW + 5)
    assert result.data == {"_z0": 0}
    assert target["upa"] == {"all_pt": 1, "used_pt": 0, "skills": []}
    assert target["updated_at"] == NOW + 5
    assert runtime.resources.balance(state, MEDICINE_ITEM) == 1
    changed = synced(result)
    assert changed["unit"] == [target]
    assert set(changed) == {"unit", "item", "limit"}
    assert limit(state, "shop.103")["times"] == 1
    assert state["events"][-1]["data"] == {
        "shop_id": MEDICINE,
        "quantity": 1,
        "costs": [{"rid": MEDICINE_ITEM, "val": 3}],
        "rewards": [{"rid": "wallet.spap.warrior_sr", "val": 1}],
    }


@pytest.mark.parametrize(
    ("card_id", "points", "medicine", "reason"),
    [
        (COMMON_WARRIOR, 0, 3, "INVALID_TARGET"),
        (EPIC_ROGUE, 0, 3, "INVALID_TARGET"),
        (EPIC_WARRIOR, 5, 3, "TARGET_IS_REACHED_LIMIT"),
        (EPIC_WARRIOR, 0, 2, "NOT_ENOUGH_CURRENCY"),
    ],
)
def test_awakening_checks_the_target_and_the_price(
    runtime, state, card_id, points, medicine, reason
):
    target = member(state, card_id)
    target["upa"]["all_pt"] = points
    fund(runtime, state, MEDICINE_ITEM, medicine)
    error = untouched(
        state, ApiRejected, lambda: awaken(runtime, state, target["instance_id"])
    )
    assert error.reason == reason


def test_awakening_needs_an_owned_unit(runtime, state):
    fund(runtime, state, MEDICINE_ITEM, 3)
    error = untouched(state, ApiRejected, lambda: awaken(runtime, state, 999))
    assert error.reason == "INVALID_TARGET"


def test_awakening_follows_schedule_and_platform(runtime, state):
    target = member(state, EPIC_WARRIOR)
    fund(runtime, state, MEDICINE_ITEM, 3)
    expired = untouched(
        state,
        ApiRejected,
        lambda: awaken(runtime, state, target["instance_id"], EXPIRED_MEDICINE),
    )
    assert expired.reason == "OUT_OF_SCHEDULE"
    goods(runtime, MEDICINE)["targetPlatform"] = 3
    foreign = untouched(
        state, ApiRejected, lambda: awaken(runtime, state, target["instance_id"])
    )
    assert foreign.reason == "MISSMATCH_TO_PLATFORM"


def test_awakening_respects_purchase_limits(runtime, state):
    target = member(state, EPIC_WARRIOR)
    fund(runtime, state, MEDICINE_ITEM, 6)
    goods(runtime, MEDICINE)["buylimit"] = 1
    awaken(runtime, state, target["instance_id"])
    error = untouched(
        state, ApiRejected, lambda: awaken(runtime, state, target["instance_id"])
    )
    assert error.reason == "LIMIT_OVER_OF_EXCHANGE_COUNT"


def test_medicine_and_plain_goods_use_their_own_endpoints(runtime, state):
    target = member(state, EPIC_WARRIOR)
    fund(runtime, state, MEDICINE_ITEM, 3)
    untouched(state, ValueError, lambda: buy(runtime, state, MEDICINE))
    untouched(
        state,
        ValueError,
        lambda: awaken(runtime, state, target["instance_id"], GOLD_PACK),
    )
    untouched(
        state, ApiNotFound, lambda: awaken(runtime, state, target["instance_id"], 1)
    )


def test_selling_pays_gold(runtime, state):
    fund(runtime, state, "item.material.Red1", 5)
    fund(runtime, state, "wallet.unit_exp.r_all", 4)
    fund(runtime, state, "item.equip.1001", 2)
    fund(runtime, state, "skill.attachable.1001", 1)
    result = sell(
        runtime,
        state,
        ("item.material.Red1", 3),
        ("wallet.unit_exp.r_all", 4),
        ("item.equip.1001", 1),
        ("skill.attachable.1001", 1),
    )
    assert result.data is None
    assert wallet(state, "gold")["val"] == 500 + 3 * 10 + 4 * 10 + 60 + 20
    assert runtime.resources.balance(state, "item.material.Red1") == 2
    assert runtime.resources.balance(state, "wallet.unit_exp.r_all") == 0
    assert runtime.resources.balance(state, "item.equip.1001") == 1
    assert runtime.resources.balance(state, "skill.attachable.1001") == 0
    assert set(synced(result)) == {"wallet", "item"}


def test_repeated_sell_orders_add_up(runtime, state):
    fund(runtime, state, "item.material.Red1", 5)
    sell(runtime, state, ("item.material.Red1", 2), ("item.material.Red1", 3))
    assert wallet(state, "gold")["val"] == 550
    fund(runtime, state, "item.material.Red1", 5)
    error = untouched(
        state,
        ApiRejected,
        lambda: sell(
            runtime, state, ("item.material.Red1", 3), ("item.material.Red1", 3)
        ),
    )
    assert error.reason == "HAS_INVALID_RESOURCE_ID"


@pytest.mark.parametrize(
    "order",
    [
        (MEDICINE_ITEM, 1),
        ("item.material.Red1", 6),
        ("item.material.None", 1),
        (GOLD, 1),
        ("unit.card.11", 1),
    ],
)
def test_unsalable_and_missing_goods_are_refused(runtime, state, order):
    fund(runtime, state, MEDICINE_ITEM, 3)
    fund(runtime, state, "item.material.Red1", 5)
    error = untouched(state, ApiRejected, lambda: sell(runtime, state, order))
    assert error.reason == "HAS_INVALID_RESOURCE_ID"


@pytest.mark.parametrize(
    "payload",
    [
        {"sell_orders": []},
        {"sell_orders": None},
        {"sell_orders": [{"resource_id": "item.material.Red1", "amount": 0}]},
        {"sell_orders": [{"resource_id": "item.material.Red1", "amount": "1"}]},
        {"sell_orders": [{"resource_id": "item.material.Red1"}]},
        {},
    ],
)
def test_malformed_sell_requests_are_invalid(runtime, state, payload):
    fund(runtime, state, "item.material.Red1", 5)
    untouched(
        state, (ValueError, KeyError), lambda: runtime.shop.sell(state, payload, NOW)
    )


def test_sale_income_stops_at_the_gold_cap(runtime, state):
    cap = next(
        row["val_cap"]
        for row in runtime.masters.table("wallet_template")
        if (row["category"], row["name"]) == ("point", "gold")
    )
    wallet(state, "gold")["val"] = cap - 5
    fund(runtime, state, "item.material.Red1", 1)
    sell(runtime, state, ("item.material.Red1", 1))
    assert wallet(state, "gold")["val"] == cap


def test_worthless_goods_can_be_cleared_out(runtime, state):
    fund(runtime, state, "item.equip.1000", 2)
    result = sell(runtime, state, ("item.equip.1000", 2))
    assert wallet(state, "gold")["val"] == 500
    assert set(synced(result)) == {"item"}


def test_login_bonus_packs_unlock_their_sheet(runtime, state):
    result = redeem(runtime, state, LOGIN_PACK_SKU)
    assert wallet(state, "paid_coin")["val"] == 500
    assert state["login_bonus"] == [{"sheet_id": 240, "progress": 0, "stamp_at": 0}]
    assert state["inbox"] == []
    assert limit(state, "shop.200234")["times"] == 1
    assert {"wallet", "limit"} <= set(synced(result))
    with pytest.raises(ApiNotFound):
        runtime.payments.issue(state, LOGIN_PACK_SKU, 1, NOW)
    stamped = runtime.login_bonus.stamp(state, NOW)
    progress = {row["sheet_id"]: row["progress"] for row in stamped.data["sheets"]}
    assert progress[240] == 1


def test_an_unlocked_sheet_is_not_sold_again(runtime, state):
    fund(runtime, state, "loginbonus.unlock.240", 1)
    untouched(
        state,
        ApiNotFound,
        lambda: runtime.payments.issue(state, LOGIN_PACK_SKU, 1, NOW),
    )


def test_packs_deliver_their_other_goods_with_the_unlock(runtime, state):
    redeem(runtime, state, DEFENSE_PACK_SKU)
    assert wallet(state, "paid_coin")["val"] == 500
    assert runtime.resources.balance(state, "skill.attachable.1305") == 1
    assert state["login_bonus"] == [{"sheet_id": 449, "progress": 0, "stamp_at": 0}]
    assert state["inbox"] == []


def test_shop_routes_cover_the_sales_endpoints():
    assert set(ROUTES) == {
        "sales/exchange/item",
        "sales/exchange/item/payment",
        "sales/exchange/unit_awaking_pt",
        "sales/sell/item",
        "sales/sp_favor/list",
    }


@pytest.mark.parametrize(
    ("endpoint", "model"),
    [
        ("sales/exchange/item", "ExchangeShopItemResponse200Data"),
        ("sales/exchange/item/payment", "ExchangeShopItemByPaymentResponse200Data"),
        ("sales/exchange/unit_awaking_pt", "ExchangeUnitAwakingPtResponse200Data"),
        ("sales/sp_favor/list", "GetSpecialFavorListResponse200Data"),
    ],
)
def test_response_fields_match_the_client_models(runtime, state, endpoint, model):
    schema = json.loads(
        (Path(__file__).parents[1] / "protocol/api_schema.json").read_text(
            encoding="utf-8"
        )
    )
    target = member(state, EPIC_WARRIOR)
    fund(runtime, state, MEDICINE_ITEM, 3)
    results = {
        "sales/exchange/item": lambda: buy(runtime, state, GOLD_PACK).data,
        "sales/exchange/item/payment": lambda: redeem(runtime, state, STONE_SKU).data,
        "sales/exchange/unit_awaking_pt": lambda: (
            awaken(runtime, state, target["instance_id"]).data
        ),
        "sales/sp_favor/list": lambda: {"record_list": runtime.shop.favors()},
    }
    expected = {field["name"] for field in schema["models"][model]["fields"]}
    assert set(results[endpoint]()) == expected
    assert runtime.has_response_data(endpoint)


def test_selling_has_no_response_data(runtime):
    assert not runtime.has_response_data("sales/sell/item")
    record = json.loads(
        (Path(__file__).parents[1] / "protocol/api_schema.json").read_text(
            encoding="utf-8"
        )
    )["models"]["SpecialFavorData"]
    assert {field["name"] for field in record["fields"]} == set(FAVOR)


async def test_native_purchases_reach_the_save(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        bought = await client.call(
            "sales/exchange/item", {"shop_id": GOLD_PACK, "quantity": 2}
        )
        assert bought["data"] == {"_z0": 0}
        counter = {"id": "shop.9001", "times": 2, "counted_at": NOW}
        assert bought["sync"]["limit"][0]["data"] == [counter]
        assert bought["sync"]["wallet"][0]["data"][0]["val"] == 500 - 20 + 2000
        counted = await client.call(
            "limit/list/seq_start", {"range_names": ["shop"], "per_view": 100}
        )
        assert counted["data"] == {"items": [counter], "sequence_token": None}
        saved = await runtime.store.read(profile)
        assert limit(saved, "shop.9001") == counter
    finally:
        await http.close()


async def test_native_rejections_keep_the_save(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        before = await runtime.store.read(profile)
        poor = await post(
            http, client, "sales/exchange/item", {"shop_id": ANCIENT_KEY, "quantity": 1}
        )
        assert poor.status == 422
        assert await poor.json() == {
            "status": {"code": 90000006, "message": "NOT_ENOUGH_CURRENCY"},
            "data": {"rejected_reason": "NOT_ENOUGH_CURRENCY"},
        }
        closed = await post(
            http,
            client,
            "sales/exchange/item",
            {"shop_id": START_DASH_UNIT, "quantity": 1},
        )
        assert closed.status == 404
        malformed = await post(
            http, client, "sales/exchange/item", {"shop_id": GOLD_PACK, "quantity": 0}
        )
        assert malformed.status == 400
        unsold = await post(
            http,
            client,
            "sales/sell/item",
            {"sell_orders": [{"resource_id": "item.material.Red1", "amount": 1}]},
        )
        assert unsold.status == 422
        stranger = await post(
            http,
            client,
            "sales/exchange/unit_awaking_pt",
            {"shop_id": MEDICINE, "instance_id": 999},
        )
        assert stranger.status == 422
        assert (await stranger.json())["data"] == {"rejected_reason": "INVALID_TARGET"}
        assert await runtime.store.read(profile) == before
    finally:
        await http.close()


async def test_native_platform_purchases_are_granted_on_click(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        favors = await client.call("sales/sp_favor/list", {"_z0": 0})
        assert favors["data"] == {"record_list": [FAVOR]}
        issued = await sdk(
            http,
            command="payment",
            itemId=STONE_SKU,
            itemName="幻獣石 600",
            unitPrice=600,
            quantity=1,
            callbackurl="https://example.invalid/procedure",
            finishurl="https://example.invalid/confirmation",
        )
        assert issued["result_code"] == 0
        payment_id = issued["payment_id"]
        granted = await client.call(
            "sales/exchange/item/payment", {"payment_id": payment_id}
        )
        assert granted["data"] == {"_z0": 0}
        assert granted["sync"]["wallet"][0]["data"][0]["val"] == 600
        received = {"id": "shop_sp_favor.10", "times": 1, "counted_at": NOW}
        assert received in granted["sync"]["limit"][0]["data"]
        claimed = await client.call(
            "limit/list/seq_start",
            {"range_names": ["shop_sp_favor"], "per_view": 100},
        )
        assert claimed["data"] == {"items": [received], "sequence_token": None}
        pending = await client.call("payment/voucher/remainings", {"_z0": 0})
        assert pending["data"] == {"voucher_list": []}
        again = await post(
            http, client, "sales/exchange/item/payment", {"payment_id": payment_id}
        )
        assert again.status == 404
        saved = await runtime.store.read(profile)
        assert saved["vouchers"] == []
        assert [row["reward"] for row in saved["inbox"]] == [
            {"rid": "unit.card.69", "val": 1}
        ]
    finally:
        await http.close()


async def test_native_orders_for_unavailable_goods_fail(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        spoil(runtime, SMALL_STONES)
        for sku in (SMALL_STONE_SKU, BEGINNER_SKU, RANK_SKU):
            refused = await sdk(http, command="payment", itemId=sku, quantity=1)
            assert refused == {"result_code": 200}
        saved = await runtime.store.read(profile)
        assert saved["vouchers"] == []
        await client.call("unit/all", {"_z0": 0})
    finally:
        await http.close()


async def test_native_awakening_and_selling_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        async with runtime.store.transaction(profile) as saved:
            target = member(saved, EPIC_WARRIOR)
            fund(runtime, saved, MEDICINE_ITEM, 3)
            fund(runtime, saved, "item.material.Red1", 4)
        awakened = await client.call(
            "sales/exchange/unit_awaking_pt",
            {"shop_id": MEDICINE, "instance_id": target["instance_id"]},
        )
        assert awakened["data"] == {"_z0": 0}
        assert awakened["sync"]["unit"][0]["data"][0]["upa"]["all_pt"] == 1
        sold = await post(
            http,
            client,
            "sales/sell/item",
            {"sell_orders": [{"resource_id": "item.material.Red1", "amount": 4}]},
        )
        assert sold.status == 200
        saved = await runtime.store.read(profile)
        assert wallet(saved, "gold")["val"] == 540
        assert runtime.resources.balance(saved, "item.material.Red1") == 0
    finally:
        await http.close()


def test_a_blessing_opens_its_window_and_adds_time_when_bought_again(runtime, state):
    fund(runtime, state, FREE_COIN, 2000)
    held = wallet(state, "free_coin")["val"]
    result = buy(runtime, state, BLESSING)
    assert result.data == {"_z0": 0}
    assert windows(state)["blessing.1"] == {
        "start_at": NOW,
        "end_at": NOW + 120 * HOUR,
    }
    assert {"wallet", "period"} <= set(synced(result))
    assert wallet(state, "free_coin")["val"] == held - 300
    buy(runtime, state, BLESSING, now=NOW + HOUR)
    assert windows(state)["blessing.1"] == {
        "start_at": NOW,
        "end_at": NOW + 240 * HOUR,
    }
    buy(runtime, state, COST_BLESSING, 2)
    assert windows(state)["blessing.2"] == {
        "start_at": NOW,
        "end_at": NOW + 240 * HOUR,
    }
    later = NOW + 400 * HOUR
    buy(runtime, state, BLESSING, now=later)
    assert windows(state)["blessing.1"] == {
        "start_at": later,
        "end_at": later + 120 * HOUR,
    }
    assert state["inbox"] == []


def raise_in(runtime, card_id, event_id):
    growth = runtime.masters.table("event_character_growth")
    next(row for row in growth if row["event_id"] == event_id)["card_id"] = card_id
    return runtime.masters.lookup("event_list_data", "id", event_id)


def test_units_an_event_is_raising_take_no_awakening_medicine(runtime, state):
    target = member(state, EPIC_WARRIOR)
    fund(runtime, state, MEDICINE_ITEM, 12)
    raise_in(runtime, EPIC_WARRIOR, SUBJUGATION_EVENT)
    error = untouched(
        state, ApiRejected, lambda: awaken(runtime, state, target["instance_id"])
    )
    assert error.reason == "INVALID_TARGET"


def test_the_medicine_ban_ends_with_the_event(runtime, state):
    target = member(state, EPIC_WARRIOR)
    fund(runtime, state, MEDICINE_ITEM, 12)
    event = raise_in(runtime, EPIC_WARRIOR, ENDED_EVENT)
    awaken(runtime, state, target["instance_id"])
    event.update(open_time="", close_time="")
    error = untouched(
        state, ApiRejected, lambda: awaken(runtime, state, target["instance_id"])
    )
    assert error.reason == "INVALID_TARGET"
    runtime.masters.table("sub_story_data").append({"event_id": ENDED_EVENT})
    awaken(runtime, state, target["instance_id"])
    assert target["upa"]["all_pt"] == 2
