from ..core.context import Context
from ..core.sync import Result


def harvest(ctx: Context) -> Result:
    return ctx.runtime.ranch.harvest(ctx.state, ctx.payload, ctx.now)


ROUTES = {"farming/yield": harvest}
