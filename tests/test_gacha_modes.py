from copy import deepcopy

import pytest

from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from test_battle import connect, post
from test_gacha import (
    LOCAL,
    NOW,
    RESET,
    Dice,
    card,
    forge,
    fund,
    official,
    order,
    rates,
    recorded,
    rejection,
    roll,
    runtime,
    state,
    synced,
    table,
    wallet,
)


STEP_POOL = "gacha.step.vivre_stp"
SELECT_GROUP = (
    "gacha.normal.them_rantiki21_select_PR",
    "gacha.normal.OH_them_rantiki21_select_OH",
    "gacha.normal.them_rantiki21_select_PL",
)
PICK = "gacha.normal.them_rantiki21_select_PL"
STAMP = 91
LOOTBOX = "wallet.lootbox.lb_7mission231030_c"
BOX_POOL = "pt_event.box.local"
EVENT_COIN = "wallet.event.pt_cosmiccoin240603"
PAID_POOL = "gacha.normal.anniversary_cp4"
PAID_SKU = "com.dmm.dmmgames.monmusutd.gachaanniversary_cp4.dmm"

__all__ = ["runtime", "state"]


def limit(state, identity):
    return next((row for row in state["limits"] if row["id"] == identity), None)


def medals(state, name):
    return wallet(state, name, "gachamedal")["val"]


def draws(result):
    return [row["prize"]["rid"] for row in result.data["main_prizes"]]


def rarity(runtime, rid):
    return runtime.gacha.lottery.bucket(
        {"prize_type": "unit", "prize_name": rid.removeprefix("unit.card.")}
    )


def test_step_stages_change_cost_extras_and_closing_table(runtime, state):
    fund(runtime, state, "wallet.point.paid_coin", 9000)
    first = runtime.gacha.draw(state, order(STEP_POOL), NOW)
    assert wallet(state, "paid_coin")["val"] == 6000
    assert first.data["extra_prizes"][0]["rid"] == "wallet.gachamedal.gmedal_a0273"
    assert first.data["extra_prizes"][0]["val"] == 130
    assert first.data["rnd_extra_prizes"] == [
        {
            "rid": "wallet.gachamedal.gmedal_common",
            "val": 800,
            "inc": 800,
            "inbox": 0,
            "over": 0,
        }
    ]
    assert limit(state, STEP_POOL) == {"id": STEP_POOL, "times": 1, "counted_at": NOW}
    second = runtime.gacha.draw(state, order(STEP_POOL), NOW + 1)
    assert wallet(state, "paid_coin")["val"] == 6000
    assert second.data["extra_prizes"][0]["val"] == 100
    assert second.data["rnd_extra_prizes"] is None
    third = runtime.gacha.draw(state, order(STEP_POOL), NOW + 2)
    assert wallet(state, "paid_coin")["val"] == 3000
    assert third.data["extra_prizes"][0]["val"] == 150
    fourth = runtime.gacha.draw(state, order(STEP_POOL), NOW + 3)
    assert fourth.data["extra_prizes"][0]["val"] == 100
    assert wallet(state, "paid_coin")["val"] == 3000
    fifth = runtime.gacha.draw(state, order(STEP_POOL), NOW + 4)
    assert wallet(state, "paid_coin")["val"] == 0
    assert fifth.data["extra_prizes"][0]["val"] == 200
    assert rarity(runtime, draws(fifth)[-1]) == "SSR"
    assert limit(state, STEP_POOL)["times"] == 5
    assert limit(state, STEP_POOL + ".s-loop") is None
    fund(runtime, state, "wallet.point.paid_coin", 3000)
    assert (
        rejection(runtime, state, order(STEP_POOL), NOW + 5)
        == "LIMIT_OVER_OF_GACHA_TRIES"
    )


def test_step_rates_follow_the_current_stage(runtime, state):
    state["limits"].append({"id": STEP_POOL, "times": 4, "counted_at": NOW})
    closing = rates(runtime, state, STEP_POOL, alt=True)
    assert closing["rarities"] == [{"rid": "SSR", "p": "100.000%"}]
    assert len(closing["prizes"]) == 129
    state["limits"][-1]["times"] = 1
    assert len(rates(runtime, state, STEP_POOL, alt=True)["prizes"]) == 156
    assert len(rates(runtime, state, STEP_POOL)["prizes"]) == 188


def test_free_steps_skip_the_random_extra(runtime, state):
    fund(runtime, state, "wallet.point.paid_coin", 3000)
    state["limits"].append({"id": STEP_POOL, "times": 1, "counted_at": NOW})
    result = runtime.gacha.draw(state, order(STEP_POOL), NOW)
    assert result.data["rnd_extra_prizes"] is None
    assert wallet(state, "paid_coin")["val"] == 3000
    assert state["events"][-1]["data"]["costs"] == []


def test_looping_steps_restart_and_count_the_round(runtime, state):
    table(runtime, "local", card(1, 57))
    forge(runtime, gacha_type="step", execlimit_max_times=2, step_loops=0, max_batch=1)
    runtime.gacha.draw(state, order(LOCAL), NOW)
    result = runtime.gacha.draw(state, order(LOCAL), NOW + 1)
    assert synced(result)["limit"] == [
        {"id": LOCAL, "times": 0, "counted_at": NOW + 1},
        {"id": LOCAL + ".s-loop", "times": 1, "counted_at": NOW + 1},
    ]
    runtime.gacha.draw(state, order(LOCAL), NOW + 2)
    assert limit(state, LOCAL)["times"] == 1


def test_bounded_loops_stop_after_the_last_round(runtime, state):
    table(runtime, "local", card(1, 57))
    forge(runtime, gacha_type="step", execlimit_max_times=2, step_loops=2, max_batch=1)
    for moment in range(4):
        runtime.gacha.draw(state, order(LOCAL), NOW + moment)
    assert limit(state, LOCAL)["times"] == 2
    assert limit(state, LOCAL + ".s-loop")["times"] == 1
    assert (
        rejection(runtime, state, order(LOCAL), NOW + 4) == "LIMIT_OVER_OF_GACHA_TRIES"
    )


def test_select_pools_need_a_featured_choice(runtime, state):
    fund(runtime, state, "wallet.point.free_coin", 3000)
    assert rejection(runtime, state, order(PICK)) == "NOT_QUALIFIED"
    choice = {
        "select_gacha_datas": [
            {"gacha_id": identity, "unit_card": "unit.card.101"}
            for identity in SELECT_GROUP
        ]
    }
    runtime.gacha.select(state, choice, NOW)
    assert state["gacha_selection"] == [
        {"gacha_id": identity, "unit_card": "unit.card.101"}
        for identity in SELECT_GROUP
    ]
    coins = wallet(state, "free_coin")["val"]
    result = runtime.gacha.draw(state, order(PICK), NOW)
    assert len(result.data["main_prizes"]) == 1
    assert wallet(state, "free_coin")["val"] == coins - 300


def test_selection_lists_every_live_select_pool(runtime, state):
    _, sample = official("gacha/select/get")
    picks = sample["data"]["select_gacha_datas"]
    assert recorded(runtime.gacha.selections(state, NOW), picks) == picks
    runtime.gacha.select(
        state,
        {"select_gacha_datas": [{"gacha_id": PICK, "unit_card": "unit.card.136"}]},
        NOW,
    )
    listed = {
        row["gacha_id"]: row["unit_card"]
        for row in recorded(runtime.gacha.selections(state, NOW), picks)
    }
    assert listed == {
        SELECT_GROUP[0]: None,
        SELECT_GROUP[1]: None,
        PICK: "unit.card.136",
    }


@pytest.mark.parametrize(
    ("gacha_id", "unit_card"),
    [
        (PICK, "unit.card.195"),
        (PICK, "101"),
        (PICK, None),
        ("gacha.normal.tutorial_L", "unit.card.23"),
    ],
)
def test_only_candidates_can_be_featured(runtime, state, gacha_id, unit_card):
    choice = {"select_gacha_datas": [{"gacha_id": gacha_id, "unit_card": unit_card}]}
    with pytest.raises(ValueError, match="cannot be featured"):
        runtime.gacha.select(state, choice, NOW)
    assert state["gacha_selection"] == []


def test_featured_choice_becomes_the_pickup(runtime, state):
    plain = {row["no"]: row for row in rates(runtime, state, PICK)["prizes"]}
    runtime.gacha.select(
        state,
        {"select_gacha_datas": [{"gacha_id": PICK, "unit_card": "unit.card.101"}]},
        NOW,
    )
    featured = {row["no"]: row for row in rates(runtime, state, PICK)["prizes"]}
    weight = runtime.config.read()["gacha"]["pickup_weight"]
    chosen = float(featured[1]["p"].rstrip("%"))
    peer = next(
        float(featured[number]["p"].rstrip("%"))
        for number, row in featured.items()
        if number != 1
        and rarity(runtime, row["rid"]) == rarity(runtime, featured[1]["rid"])
    )
    assert chosen == pytest.approx(peer * weight, abs=0.0005 * (weight + 1))
    assert plain[1]["p"] == plain[2]["p"]


def test_stamp_cards_fill_cell_by_cell(runtime, state):
    table(runtime, "local", card(1, 57))
    forge(runtime, stamp_id=STAMP, max_batch=40)
    result = runtime.gacha.draw(state, order(LOCAL), NOW)
    assert result.data["stamp_prizes"] == [
        {
            "rid": "wallet.point.gold",
            "val": 100000,
            "inc": 0,
            "inbox": 100000,
            "over": 0,
        }
    ]
    assert state["gacha_stamp_records"] == [
        {"id": "stamp.91", "step": 1, "cell_index": 1, "in_progress": True}
    ]
    assert synced(result)["gacha_stamp_record"] == state["gacha_stamp_records"]
    letter = state["inbox"][-1]
    assert letter["tags"] == ["gacha.stamp_reward:91:1:1", "wallet"]
    assert letter["reward"] == {"rid": "wallet.point.gold", "val": 100000}
    runtime.gacha.draw(state, order(LOCAL, 11), NOW)
    assert state["gacha_stamp_records"][0] == {
        "id": "stamp.91",
        "step": 1,
        "cell_index": 12,
        "in_progress": True,
    }
    runtime.gacha.draw(state, order(LOCAL), NOW)
    assert state["gacha_stamp_records"][0] == {
        "id": "stamp.91",
        "step": 2,
        "cell_index": 1,
        "in_progress": True,
    }
    finished = runtime.gacha.draw(state, order(LOCAL, 23), NOW)
    assert len(finished.data["stamp_prizes"]) == 23
    assert state["gacha_stamp_records"][0] == {
        "id": "stamp.91",
        "step": 3,
        "cell_index": 12,
        "in_progress": False,
    }
    assert len(state["inbox"]) == 36
    assert runtime.gacha.draw(state, order(LOCAL), NOW).data["stamp_prizes"] is None
    assert runtime.gacha.stamp_records(state, [91, 7]) == state["gacha_stamp_records"]
    assert runtime.gacha.stamp_records(state, [7]) == []


def test_unknown_stamp_cards_are_reported(runtime, state):
    table(runtime, "local", card(1, 57))
    forge(runtime, stamp_id=424242)
    with pytest.raises(ValueError, match="Unknown gacha stamp card 424242"):
        runtime.gacha.draw(state, order(LOCAL), NOW)


def lootbox(source="direct", exec_num=1, message_no=0, resource_id=LOOTBOX):
    return {
        "source": source,
        "resource_id": resource_id,
        "message_no": message_no,
        "exec_num": exec_num,
    }


def open_box(runtime, state, payload, now=NOW):
    from monmusu_ps.core.context import Context
    from monmusu_ps.features.gacha import ROUTES

    return ROUTES["gacha/lootbox/open"](Context(runtime, payload, state, None, now))


def test_lootboxes_open_from_the_item_page(runtime, state):
    fund(runtime, state, LOOTBOX, 3)
    result = open_box(runtime, state, lootbox(exec_num=2))
    assert draws(result) == ["item.equip.1101", "item.equip.1101"]
    assert [row["prize"]["val"] for row in result.data["main_prizes"]] == [4, 4]
    assert [(row["rid"], row["val"]) for row in result.data["extra_prizes"]] == [
        (f"item.equip.{name}", 8) for name in (3401, 8101, 8201, 8301, 8401, 9001, 9301)
    ]
    assert result.data["actual_exec_num"] == 2
    assert wallet(state, "lb_7mission231030_c", "lootbox")["val"] == 1
    with pytest.raises(ApiRejected) as caught:
        open_box(runtime, state, lootbox(exec_num=2))
    assert caught.value.reason == "NOT_ENOUGH_COST"


def test_lootboxes_in_the_inbox_are_opened_from_the_letter(runtime, state):
    runtime.mailbox.post(state, {"rid": LOOTBOX, "val": 2}, "mission", NOW)
    runtime.mailbox.post(state, {"rid": "wallet.point.gold", "val": 5}, "mission", NOW)
    number = state["inbox"][0]["no"]
    with pytest.raises(ApiRejected) as missing:
        open_box(runtime, state, lootbox("inbox", 2, number + 5))
    assert missing.value.reason == "NOT_FOUND_MESSAGE"
    with pytest.raises(ApiRejected) as other:
        open_box(runtime, state, lootbox("inbox", 1, number + 1))
    assert other.value.reason == "NOT_EXPECTED_MESSAGE_REWARD"
    result = open_box(runtime, state, lootbox("inbox", 2, number))
    assert len(result.data["main_prizes"]) == 2
    assert [row["no"] for row in state["inbox"]] == [number + 1]
    assert state["inbox_archive"][-1]["item"]["no"] == number
    assert wallet(state, "lb_7mission231030_c", "lootbox")["val"] == 0


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (lootbox("mail"), ValueError),
        (lootbox(resource_id="wallet.lootbox.none"), ApiNotFound),
        (lootbox(resource_id="wallet.point.gold"), ApiNotFound),
        (lootbox(exec_num=101), ApiRejected),
    ],
)
def test_malformed_lootbox_requests_are_refused(runtime, state, payload, error):
    fund(runtime, state, LOOTBOX, 200)
    before = deepcopy(state)
    with pytest.raises(error):
        open_box(runtime, state, payload)
    assert state == before


def boxes():
    return {
        BOX_POOL: [
            {
                "no": 1,
                "publish_at": "",
                "disp_payload": "",
                "list": [
                    {
                        "no": 1,
                        "rid": "wallet.point.gold",
                        "val": 1000,
                        "load_num": 2,
                        "pickup": False,
                    },
                    {
                        "no": 2,
                        "rid": "unit.card.23",
                        "val": 1,
                        "load_num": 1,
                        "pickup": True,
                    },
                ],
            },
            {
                "no": 2,
                "publish_at": "2026/11/12 05:00",
                "disp_payload": "",
                "list": [
                    {
                        "no": 3,
                        "rid": "wallet.point.gold",
                        "val": 500,
                        "load_num": 1,
                        "pickup": False,
                    }
                ],
            },
        ]
    }


@pytest.fixture
def box(runtime, state, monkeypatch):
    monkeypatch.setattr(runtime.boxes, "read", boxes)
    pool = forge(
        runtime,
        BOX_POOL,
        gacha_type="box",
        cost_type="event",
        cost_name="pt_cosmiccoin240603",
        cost_num=1,
        max_batch=10,
        execlimit_max_times=0,
        prize_table_id="box_local",
    )
    fund(runtime, state, EVENT_COIN, 5)
    return pool


def box_order(count=1, interruption=False):
    return {"gacha_id": BOX_POOL, "exec_num": count, "interruption": interruption}


def test_box_records_start_with_the_first_box(runtime, state, box):
    assert runtime.gacha.box_records(state, [BOX_POOL]) == [
        {
            "id": BOX_POOL,
            "box_capaciity": 3,
            "reseted_box": 0,
            "drawn_list": [],
            "resettable": False,
        }
    ]
    assert runtime.gacha.box_catalog(BOX_POOL) == boxes()[BOX_POOL]
    with pytest.raises(ApiNotFound):
        runtime.gacha.box_records(state, ["gacha.normal.tutorial_L"])
    with pytest.raises(ApiNotFound):
        runtime.gacha.box_catalog("gacha.normal.tutorial_L")


def test_box_draws_remove_prizes_from_the_box(runtime, state, box):
    runtime.boxes.read = lambda: boxes()
    roll(runtime, 0.0, 0.99)
    runtime.gacha.rng = runtime.gacha.lottery.rng
    runtime.gacha.boxes.rng = runtime.gacha.lottery.rng
    result = runtime.gacha.draw(state, box_order(2), NOW)
    assert draws(result) == ["wallet.point.gold", "unit.card.23"]
    assert result.data["actual_exec_num"] == 2
    assert wallet(state, "pt_cosmiccoin240603", "event")["val"] == 3
    record = runtime.gacha.box_records(state, [BOX_POOL])[0]
    assert record == {
        "id": BOX_POOL,
        "box_capaciity": 3,
        "reseted_box": 0,
        "drawn_list": [1, 2],
        "resettable": True,
    }
    assert synced(result)["boxgacha_record"] == [record]
    assert rejection(runtime, state, box_order(2)) == "TOO_LARGE_NUM_OF_TRIALS"
    runtime.gacha.boxes.rng = Dice(0.5)
    runtime.gacha.draw(state, box_order(1), NOW)
    assert rejection(runtime, state, box_order(1)) == "LIMIT_OVER_OF_GACHA_TRIES"


def test_box_draws_stop_at_the_featured_prize_when_asked(runtime, state, box):
    runtime.gacha.boxes.rng = Dice(0.99, 0.5, 0.5)
    result = runtime.gacha.draw(state, box_order(3, interruption=True), NOW)
    assert draws(result) == ["unit.card.23"]
    assert result.data["actual_exec_num"] == 1
    assert wallet(state, "pt_cosmiccoin240603", "event")["val"] == 4
    assert runtime.gacha.box_records(state, [BOX_POOL])[0]["drawn_list"] == [2]


def test_box_reset_needs_a_published_next_box(runtime, state, box):
    with pytest.raises(ApiRejected) as early:
        runtime.gacha.box_reset(state, BOX_POOL, NOW)
    assert early.value.reason == "UNFULFILLED_BOX_RESET_CONDITION"
    runtime.gacha.boxes.rng = Dice(0.99)
    runtime.gacha.draw(state, box_order(1), NOW)
    with pytest.raises(ApiRejected) as unpublished:
        runtime.gacha.box_reset(state, BOX_POOL, NOW)
    assert unpublished.value.reason == "UNFULFILLED_BOX_RESET_CONDITION"
    runtime.gacha.box_reset(state, BOX_POOL, RESET + 86400)
    assert runtime.gacha.box_records(state, [BOX_POOL]) == [
        {
            "id": BOX_POOL,
            "box_capaciity": 1,
            "reseted_box": 1,
            "drawn_list": [],
            "resettable": False,
        }
    ]
    with pytest.raises(ApiRejected) as last:
        runtime.gacha.box_reset(state, BOX_POOL, RESET + 86400)
    assert last.value.reason == "UNFULFILLED_BOX_RESET_CONDITION"


def test_empty_boxes_may_be_reset_without_a_featured_prize(runtime, state, box):
    runtime.boxes.read = lambda: {
        BOX_POOL: [
            {
                "no": 1,
                "publish_at": "",
                "disp_payload": "",
                "list": boxes()[BOX_POOL][0]["list"][:1],
            },
            boxes()[BOX_POOL][1],
        ]
    }
    runtime.gacha.boxes.rng = Dice(0.5, 0.5)
    runtime.gacha.draw(state, box_order(2), NOW)
    assert runtime.gacha.box_records(state, [BOX_POOL])[0]["resettable"] is False
    runtime.gacha.box_reset(state, BOX_POOL, RESET + 86400)
    assert runtime.gacha.box_records(state, [BOX_POOL])[0]["reseted_box"] == 1


def test_unconfigured_boxes_are_not_found(runtime, state, box):
    runtime.boxes.read = dict
    with pytest.raises(ApiNotFound):
        runtime.gacha.box_catalog(BOX_POOL)
    with pytest.raises(ApiNotFound):
        runtime.gacha.draw(state, box_order(1), NOW)
    with pytest.raises(ApiNotFound):
        runtime.gacha.box_records(state, [BOX_POOL])


def test_paid_pools_are_drawn_through_a_voucher(runtime, state):
    assert rejection(runtime, state, order(PAID_POOL)) == "NOT_ENOUGH_COST"
    payment_id = runtime.payments.issue(state, PAID_SKU, 1, NOW)
    assert state["vouchers"] == [
        {
            "payment_id": payment_id,
            "product_sku": PAID_SKU,
            "product_type": "gacha",
            "quantity": 1,
            "created_at": NOW,
        }
    ]
    voucher, gacha_id = runtime.payments.claim(state, payment_id, "gacha")
    assert gacha_id == PAID_POOL
    before = {row["name"]: row["val"] for row in state["wallets"]}
    result = runtime.gacha.draw_prepaid(state, gacha_id, voucher["quantity"], NOW)
    runtime.payments.settle(state, voucher)
    assert len(result.data["main_prizes"]) == 10
    assert rarity(runtime, draws(result)[-1]) == "SSR"
    assert result.data["extra_prizes"][0]["rid"] == "wallet.gachamedal.gmedal_common"
    after = {row["name"]: row["val"] for row in state["wallets"]}
    assert all(after[name] >= value for name, value in before.items())
    assert state["vouchers"] == []
    assert state["events"][-1]["data"]["costs"] == []
    with pytest.raises(ApiNotFound):
        runtime.payments.claim(state, payment_id, "gacha")


def test_vouchers_are_bound_to_their_product_kind(runtime, state):
    payment_id = runtime.payments.issue(state, PAID_SKU, 1, NOW)
    with pytest.raises(ApiNotFound):
        runtime.payments.claim(state, payment_id, "shop")
    with pytest.raises(ApiNotFound):
        runtime.payments.claim(state, None, "gacha")
    with pytest.raises(KeyError):
        runtime.payments.issue(state, "com.dmm.unknown.sku", 1, NOW)
    with pytest.raises(ValueError):
        runtime.payments.issue(state, PAID_SKU, 0, NOW)


def prepaid(runtime, state, payment_id):
    voucher, gacha_id = runtime.payments.claim(state, payment_id, "gacha")
    result = runtime.gacha.draw_prepaid(state, gacha_id, voucher["quantity"], NOW)
    runtime.payments.settle(state, voucher)
    return result


def test_orders_for_exhausted_pools_are_refused(runtime, state):
    prepaid(runtime, state, runtime.payments.issue(state, PAID_SKU, 1, NOW))
    before = deepcopy(state)
    with pytest.raises(ApiRejected) as caught:
        runtime.payments.issue(state, PAID_SKU, 1, NOW)
    assert caught.value.reason == "LIMIT_OVER_OF_GACHA_TRIES"
    assert state == before


def test_vouchers_for_exhausted_pools_are_dropped(runtime, state):
    first = runtime.payments.issue(state, PAID_SKU, 1, NOW)
    runtime.payments.issue(state, PAID_SKU, 1, NOW)
    assert len(runtime.payments.pending(state, NOW)) == 2
    prepaid(runtime, state, first)
    assert runtime.payments.pending(state, NOW) == []
    assert state["vouchers"] == []


def test_prepaid_draws_only_serve_paid_pools(runtime, state):
    with pytest.raises(ApiRejected) as caught:
        runtime.gacha.draw_prepaid(state, "gacha.normal.tutorial_L", 1, NOW)
    assert caught.value.reason == "NOT_QUALIFIED"
    assert state["units"][-1]["card_id"] == 22


async def sdk(http, **fields):
    response = await http.post(
        "/api/sdk/request",
        json={
            "viewer_id": "local",
            "access_token": "local",
            "onetime_token": "local",
            "sdk_version": "1",
            **fields,
        },
    )
    return await response.json()


async def test_native_payment_is_granted_on_click(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        balance = await sdk(http, command="getpoint")
        assert balance["can_use_point"] >= 5000
        issued = await sdk(
            http,
            command="payment",
            itemId=PAID_SKU,
            itemName="アニバーサリー記念ガチャ2026",
            unitPrice=3000,
            quantity=1,
            callbackurl="https://example.invalid/procedure",
            finishurl="https://example.invalid/confirmation",
        )
        assert issued["result_code"] == 0
        payment_id = issued["payment_id"]
        pending = await client.call("payment/voucher/remainings", {"_z0": 0})
        assert [row["payment_id"] for row in pending["data"]["voucher_list"]] == [
            payment_id
        ]
        drawn = await client.call("gacha/draw/payment", {"payment_id": payment_id})
        assert len(drawn["data"]["main_prizes"]) == 10
        assert set(drawn["data"]) == {
            "main_prizes",
            "extra_prizes",
            "rnd_extra_prizes",
            "actual_exec_num",
            "stamp_prizes",
        }
        settled = await client.call("payment/voucher/remainings", {"_z0": 0})
        assert settled["data"] == {"voucher_list": []}
        again = await post(
            http, client, "gacha/draw/payment", {"payment_id": payment_id}
        )
        assert again.status == 404
        empty = await post(http, client, "gacha/draw/payment", {"payment_id": None})
        assert empty.status == 404
        saved = await runtime.store.read(profile)
        assert saved["vouchers"] == []
        assert limit(saved, PAID_POOL)["times"] == 1
    finally:
        await http.close()


async def test_native_payment_needs_a_known_account_and_product(tmp_path):
    http, _, _, _ = await connect(tmp_path)
    try:
        unknown = await sdk(http, command="payment", itemId="com.dmm.none", quantity=1)
        assert unknown["result_code"] == 200
        stranger = await http.post(
            "/api/sdk/request",
            json={
                "viewer_id": "nobody",
                "access_token": "local",
                "onetime_token": "local",
                "sdk_version": "1",
                "command": "payment",
                "itemId": PAID_SKU,
                "quantity": 1,
            },
        )
        assert (await stranger.json())["result_code"] == 102
    finally:
        await http.close()


async def test_native_box_endpoints_follow_the_client_models(tmp_path, monkeypatch):
    http, client, runtime, _ = await connect(tmp_path)
    try:
        monkeypatch.setattr(runtime.boxes, "read", boxes)
        forge(
            runtime,
            BOX_POOL,
            gacha_type="box",
            cost_type="event",
            cost_name="pt_cosmiccoin240603",
            cost_num=1,
            max_batch=10,
            execlimit_max_times=0,
            prize_table_id="box_local",
        )
        records = await client.call("gacha/box_record/list", {"gacha_ids": [BOX_POOL]})
        assert records["data"]["boxgacha_records"][0]["box_capaciity"] == 3
        catalog = await client.call("gacha/box/content_catalog", {"gacha_id": BOX_POOL})
        assert catalog["data"]["catalog"] == boxes()[BOX_POOL]
        refused = await post(http, client, "gacha/box/reset", {"gacha_id": BOX_POOL})
        assert refused.status == 422
        assert (await refused.json())["data"] == {
            "rejected_reason": "UNFULFILLED_BOX_RESET_CONDITION"
        }
        missing = await post(
            http, client, "gacha/box/content_catalog", {"gacha_id": "pt_event.box.none"}
        )
        assert missing.status == 404
    finally:
        await http.close()


def test_remaining_vouchers_list_every_open_voucher(runtime, state):
    from monmusu_ps.core.context import Context
    from monmusu_ps.features.payment import ROUTES

    context = Context(runtime, {"_z0": 0}, state, None, NOW)
    assert ROUTES["payment/voucher/remainings"](context).data == {"voucher_list": []}
    runtime.payments.issue(state, PAID_SKU, 1, NOW)
    listed = ROUTES["payment/voucher/remainings"](context).data["voucher_list"]
    assert listed == state["vouchers"]
    fields = {
        field["name"]
        for field in runtime.models["GetRemainingVoucherListResponse200Data"]["fields"]
    }
    assert fields == {"voucher_list"}
    voucher = {field["name"] for field in runtime.models["Voucher"]["fields"]}
    assert set(listed[0]) == voucher
