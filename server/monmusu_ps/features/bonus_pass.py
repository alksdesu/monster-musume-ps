from ..core.context import Context
from ..core.sync import Result


def progressions(ctx: Context) -> Result:
    rows = ctx.runtime.bonus_passes.progressions(ctx.state, ctx.now)
    return Result({"progressions": rows})


def receive_rewards(ctx: Context) -> Result:
    return ctx.runtime.bonus_passes.receive(ctx.state, ctx.payload, ctx.now)


def checkoff(ctx: Context) -> Result:
    return ctx.runtime.bonus_passes.checkoff(ctx.state, ctx.payload, ctx.now)


ROUTES = {
    "bonus_pass/progressions": progressions,
    "bonus_pass/tier/receive_reward": receive_rewards,
    "bonus_pass/progression/checkoff": checkoff,
}
