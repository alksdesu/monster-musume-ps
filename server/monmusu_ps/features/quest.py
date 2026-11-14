from ..core.context import Context
from ..core.sync import Result


def start(ctx: Context) -> Result:
    return ctx.runtime.battles.start(ctx.state, ctx.payload, ctx.now)


def finish(ctx: Context) -> Result:
    return ctx.runtime.battles.finish(ctx.state, ctx.payload, ctx.now)


def cycle(ctx: Context) -> Result:
    return ctx.runtime.battles.cycle(ctx.state, ctx.payload, ctx.now)


def clear_teams(ctx: Context) -> Result:
    return Result(ctx.runtime.clear_teams.teams(ctx.state, ctx.payload))


def usage(ctx: Context) -> Result:
    return Result(ctx.runtime.clear_teams.usage(ctx.state, ctx.payload))


def subskill_probabilities(ctx: Context) -> Result:
    return Result(ctx.runtime.subskill_prizes.listing(ctx.state, ctx.payload))


ROUTES = {
    "quest/start": start,
    "quest/end": finish,
    "quest/cycle_exec": cycle,
    "quest/masters_team": clear_teams,
    "quest/character/ranking": usage,
    "quest/subskill_probabilities": subskill_probabilities,
}
