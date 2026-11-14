from ..core.context import Context
from ..core.sync import Result


def issue(ctx: Context) -> Result:
    ctx.runtime.data_links.issue(ctx.state, ctx.payload, ctx.now)
    return Result({"_z0": 0})


def confirm(ctx: Context) -> Result:
    return Result(ctx.runtime.data_links.confirm(ctx.session, ctx.payload, ctx.now))


def execute(ctx: Context) -> Result:
    ctx.runtime.data_links.execute(ctx.session, ctx.payload, ctx.now)
    return Result({"_z0": 0})


ROUTES = {
    "data_link/password/issue": issue,
    "data_link/password/confirm": confirm,
    "data_link/password/exec": execute,
}
