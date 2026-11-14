from ..core.context import Context
from ..core.payments import SHOP
from ..core.sync import Result


def exchange(ctx: Context) -> Result:
    return ctx.runtime.shop.exchange(ctx.state, ctx.payload, ctx.now)


def exchange_paid(ctx: Context) -> Result:
    payments = ctx.runtime.payments
    voucher, shop_id = payments.claim(ctx.state, ctx.payload["payment_id"], SHOP)
    result = ctx.runtime.shop.redeem(ctx.state, shop_id, voucher["quantity"], ctx.now)
    payments.settle(ctx.state, voucher)
    return result


def awaken(ctx: Context) -> Result:
    return ctx.runtime.shop.awaken(ctx.state, ctx.payload, ctx.now)


def sell(ctx: Context) -> Result:
    return ctx.runtime.shop.sell(ctx.state, ctx.payload, ctx.now)


def special_favors(ctx: Context) -> Result:
    return Result(data={"record_list": ctx.runtime.shop.favors()})


ROUTES = {
    "sales/exchange/item": exchange,
    "sales/exchange/item/payment": exchange_paid,
    "sales/exchange/unit_awaking_pt": awaken,
    "sales/sell/item": sell,
    "sales/sp_favor/list": special_favors,
}
