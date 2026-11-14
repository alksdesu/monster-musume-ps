from ..core.context import Context
from ..core.sync import Result


def start(ctx: Context) -> Result:
    return ctx.runtime.sugoroku.start(ctx.state, ctx.payload, ctx.now)


def finish(ctx: Context) -> Result:
    return ctx.runtime.sugoroku.finish(ctx.state, ctx.payload, ctx.now)


def query(ctx: Context) -> Result:
    return Result(ctx.runtime.sugoroku.query(ctx.state))


def advance(ctx: Context) -> Result:
    return Result(ctx.runtime.sugoroku.advance(ctx.state, ctx.payload))


def end_turn(ctx: Context) -> Result:
    ctx.runtime.sugoroku.end_turn(ctx.state, ctx.payload)
    return Result(include_data=False)


def finish_battle(ctx: Context) -> Result:
    return Result(ctx.runtime.sugoroku.finish_battle(ctx.state, ctx.payload))


def save_deck(ctx: Context) -> Result:
    ctx.runtime.sugoroku.save_deck(ctx.state, ctx.payload)
    return Result(include_data=False)


def select_item(ctx: Context) -> Result:
    ctx.runtime.sugoroku.select_item(ctx.state, ctx.payload)
    return Result(include_data=False)


ROUTES = {
    "sugoroku/start": start,
    "sugoroku/end": finish,
    "sugoroku/session/query": query,
    "sugoroku/ingame/turn/exec": advance,
    "sugoroku/ingame/turn/end": end_turn,
    "sugoroku/ingame/battle/end": finish_battle,
    "sugoroku/ingame/deck/save": save_deck,
    "sugoroku/ingame/item/select": select_item,
}
