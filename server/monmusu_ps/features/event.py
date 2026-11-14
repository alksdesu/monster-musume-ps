from ..core.context import Context
from ..core.sync import Result


def confirm_growth(ctx: Context) -> Result:
    return ctx.runtime.event_growth.confirm(ctx.state, ctx.payload, ctx.now)


def confirm_sealed(ctx: Context) -> Result:
    data = ctx.runtime.sealed_characters.confirm(ctx.state, ctx.payload, ctx.now)
    return Result(data)


def modify_sealed(ctx: Context) -> Result:
    data = ctx.runtime.sealed_characters.modify(ctx.state, ctx.payload, ctx.now)
    return Result(data)


def confirm_raid(ctx: Context) -> Result:
    return ctx.runtime.subdue_raids.confirm(ctx.state, ctx.payload, ctx.now)


ROUTES = {
    "event/grow_chara/confirm": confirm_growth,
    "event/sealed_chara/confirm": confirm_sealed,
    "event/sealed_chara/modify": modify_sealed,
    "event/subdue_raid/confirm": confirm_raid,
}
