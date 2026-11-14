from ..core.context import Context
from ..core.sync import Result


def synthesize(ctx: Context) -> Result:
    return ctx.runtime.synthesis.synthesize(ctx.state, ctx.payload, ctx.now)


ROUTES = {"item/subskill/synthesize": synthesize}
