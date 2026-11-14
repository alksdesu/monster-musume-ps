from ..core import pagination
from ..core.context import Context
from ..core.sync import Result


FAMILY = "mileage_program/record/list"


def program(ctx: Context) -> Result:
    return Result(ctx.runtime.mileage.get(ctx.state, ctx.payload, ctx.now))


def commit(ctx: Context) -> Result:
    return ctx.runtime.mileage.commit(ctx.state, ctx.payload, ctx.now)


def records(ctx: Context) -> Result:
    rows = ctx.runtime.mileage.records(ctx.state, ctx.payload["range_names"])
    return Result(pagination.start(ctx.session, FAMILY, rows, ctx.payload["per_view"]))


def more(ctx: Context) -> Result:
    return Result(pagination.resume(ctx.session, FAMILY, ctx.payload["sequence_token"]))


ROUTES = {
    "mileage_program": program,
    "mileage_program/commit": commit,
    "mileage_program/record/list": records,
    "mileage_program/record/list/continue": more,
}
