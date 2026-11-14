from ..core.context import Context
from ..core.sync import Result


def use(ctx: Context) -> Result:
    return ctx.runtime.serial_codes.use(ctx.state, ctx.payload, ctx.now)


ROUTES = {"serial_code/use": use}
