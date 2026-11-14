from ..core.context import Context
from ..core.sync import Result


def enhance(ctx: Context) -> Result:
    return ctx.runtime.training.enhance_bom(ctx.state, ctx.payload, ctx.now)


def enhance_passive(ctx: Context) -> Result:
    return ctx.runtime.training.enhance_bom_passive(ctx.state, ctx.payload, ctx.now)


ROUTES = {"bom/enhancement": enhance, "bom/enhancement/passive": enhance_passive}
