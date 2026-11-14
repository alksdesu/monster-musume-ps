from ..core.context import Context
from ..core.sync import Result


def vouchers(ctx: Context) -> Result:
    pending = ctx.runtime.payments.pending(ctx.state, ctx.now)
    return Result(data={"voucher_list": pending})


ROUTES = {"payment/voucher/remainings": vouchers}
