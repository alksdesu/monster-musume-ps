from ..core.context import Context
from ..core.sync import Result


def vip(ctx: Context) -> Result:
    return Result(ctx.runtime.membership.vip(ctx.now))


def dmm_premium(ctx: Context) -> Result:
    return Result(ctx.runtime.membership.dmm_premium(ctx.state, ctx.now))


ROUTES = {"vip/info": vip, "vip/dmm_premium/benefits": dmm_premium}
