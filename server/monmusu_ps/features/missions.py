from ..core.context import Context
from ..core.sync import Result


def records(ctx: Context) -> Result:
    missions = ctx.runtime.missions
    return Result(data=missions.records(ctx.state, ctx.payload["category_id"], ctx.now))


def receive(ctx: Context) -> Result:
    return ctx.runtime.missions.receive(ctx.state, ctx.payload, ctx.now)


ROUTES = {"mission/list/category": records, "mission/receive": receive}
