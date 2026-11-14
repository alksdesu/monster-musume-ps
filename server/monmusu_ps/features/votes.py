from ..core.context import Context
from ..core.sync import Result


def campaign(ctx: Context) -> Result:
    return Result(ctx.runtime.votes.campaign(ctx.state, ctx.payload, ctx.now))


def post(ctx: Context) -> Result:
    return ctx.runtime.votes.post(ctx.state, ctx.payload, ctx.now)


def ranking(ctx: Context) -> Result:
    return Result(ctx.runtime.votes.ranking(ctx.state, ctx.payload, ctx.now))


ROUTES = {"votes/campaign": campaign, "votes/post": post, "votes/ranking": ranking}
