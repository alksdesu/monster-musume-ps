from ..core import pagination
from ..core.context import Context
from ..core.sync import Result


FAMILY = "story/list"


def records(ctx: Context) -> Result:
    rows = ctx.runtime.stories.records(ctx.state, ctx.payload["story_category"])
    return Result(
        data=pagination.start(ctx.session, FAMILY, rows, ctx.payload["per_view"])
    )


def more_records(ctx: Context) -> Result:
    return Result(
        data=pagination.resume(ctx.session, FAMILY, ctx.payload["sequence_token"])
    )


def read(ctx: Context) -> Result:
    return ctx.runtime.stories.read(ctx.state, ctx.payload, ctx.now)


ROUTES = {
    "story/list": records,
    "story/list/continue": more_records,
    "story/read": read,
}
