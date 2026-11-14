from ..core.context import Context
from ..core.sync import Result


def get(ctx: Context) -> Result:
    return ctx.runtime.login_bonus.stamp(ctx.state, ctx.now)


def sheets(ctx: Context) -> Result:
    return Result({"sheets": ctx.runtime.login_bonus.sheets(ctx.state, ctx.now)})


ROUTES = {"loginbonus/get": get, "loginbonus/sheets": sheets}
