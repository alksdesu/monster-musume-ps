from copy import deepcopy

from ..core.context import Context
from ..core.payments import GACHA
from ..core.sync import Result, diff_state


def listed(ctx: Context) -> Result:
    return Result(data={"gacha_ids": ctx.runtime.gacha.listed(ctx.now)})


def draw(ctx: Context) -> Result:
    return ctx.runtime.gacha.draw(ctx.state, ctx.payload, ctx.now)


def draw_paid(ctx: Context) -> Result:
    payments = ctx.runtime.payments
    voucher, gacha_id = payments.claim(ctx.state, ctx.payload["payment_id"], GACHA)
    result = ctx.runtime.gacha.draw_prepaid(
        ctx.state, gacha_id, voucher["quantity"], ctx.now
    )
    payments.settle(ctx.state, voucher)
    return result


def open_lootbox(ctx: Context) -> Result:
    payload = ctx.payload
    pool = ctx.runtime.gacha.lootbox(payload["source"], payload["resource_id"])
    if payload["source"] == "inbox":
        ctx.runtime.inbox.redeem(
            ctx.state, payload["message_no"], payload["resource_id"], ctx.now
        )
    order = {
        "gacha_id": pool["id"],
        "exec_num": payload["exec_num"],
        "interruption": False,
    }
    return ctx.runtime.gacha.draw(ctx.state, order, ctx.now)


def box_catalog(ctx: Context) -> Result:
    catalog = ctx.runtime.gacha.box_catalog(ctx.payload["gacha_id"])
    return Result(data={"catalog": catalog})


def box_records(ctx: Context) -> Result:
    records = ctx.runtime.gacha.box_records(ctx.state, ctx.payload["gacha_ids"])
    return Result(data={"boxgacha_records": records})


def box_reset(ctx: Context) -> Result:
    before = deepcopy(ctx.state)
    ctx.runtime.gacha.box_reset(ctx.state, ctx.payload["gacha_id"], ctx.now)
    return Result(sync=diff_state(before, ctx.state))


def probabilities(ctx: Context) -> Result:
    engine = ctx.runtime.gacha
    return Result(data=engine.probabilities(ctx.state, ctx.payload, ctx.now))


def history(ctx: Context) -> Result:
    return Result(data={"history": ctx.runtime.gacha.history(ctx.state)})


def select(ctx: Context) -> Result:
    ctx.runtime.gacha.select(ctx.state, ctx.payload, ctx.now)
    return Result()


def selections(ctx: Context) -> Result:
    chosen = ctx.runtime.gacha.selections(ctx.state, ctx.now)
    return Result(data={"select_gacha_datas": chosen})


def stamp_records(ctx: Context) -> Result:
    records = ctx.runtime.gacha.stamp_records(ctx.state, ctx.payload["stamp_ids"])
    return Result(data={"gacha_stamp_records": records})


ROUTES = {
    "gacha/list": listed,
    "gacha/draw": draw,
    "gacha/draw/payment": draw_paid,
    "gacha/lootbox/open": open_lootbox,
    "gacha/box/content_catalog": box_catalog,
    "gacha/box_record/list": box_records,
    "gacha/box/reset": box_reset,
    "gacha/probabilities": probabilities,
    "gacha/history": history,
    "gacha/select": select,
    "gacha/select/get": selections,
    "gacha/stamp_record/list": stamp_records,
}
