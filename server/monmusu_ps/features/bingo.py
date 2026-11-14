from ..core.context import Context
from ..core.sync import Result


def records(ctx: Context) -> Result:
    return Result(ctx.runtime.bingo.records(ctx.state))


def lottery(ctx: Context) -> Result:
    return ctx.runtime.bingo.lottery(ctx.state, ctx.payload, ctx.now)


def open_cell(ctx: Context) -> Result:
    return ctx.runtime.bingo.open(ctx.state, ctx.payload, ctx.now)


def receive_line(ctx: Context) -> Result:
    return ctx.runtime.bingo.receive_line(ctx.state, ctx.payload, ctx.now)


def receive_complete(ctx: Context) -> Result:
    return ctx.runtime.bingo.receive_complete(ctx.state, ctx.payload, ctx.now)


ROUTES = {
    "bingo/record/all": records,
    "bingo/lottery": lottery,
    "bingo/open": open_cell,
    "bingo/line/receive": receive_line,
    "bingo/complete/receive": receive_complete,
}
