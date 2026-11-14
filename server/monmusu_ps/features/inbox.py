from functools import partial

from ..core import pagination
from ..core.context import Context
from ..core.sync import Result


LETTERS, HISTORY = "inbox/list", "inbox_archive/list"


def letters(ctx: Context) -> Result:
    rows = ctx.runtime.inbox.letters(ctx.state, ctx.payload, ctx.now)
    return Result(
        data=pagination.start(ctx.session, LETTERS, rows, ctx.payload["per_view"])
    )


def history(ctx: Context) -> Result:
    rows = ctx.runtime.inbox.history(ctx.state, ctx.payload, ctx.now)
    return Result(
        data=pagination.start(ctx.session, HISTORY, rows, ctx.payload["per_view"])
    )


def more(ctx: Context, family: str) -> Result:
    return Result(
        data=pagination.resume(ctx.session, family, ctx.payload["sequence_token"])
    )


def open_letter(ctx: Context) -> Result:
    return ctx.runtime.inbox.open(ctx.state, ctx.payload, ctx.now)


def open_letters(ctx: Context) -> Result:
    return ctx.runtime.inbox.open_many(ctx.state, ctx.payload, ctx.now)


async def post_debug(ctx: Context) -> Result:
    runtime = ctx.runtime
    runtime.inbox.authorize_debug()
    profile = runtime.profile_of(ctx.payload["player_code"])
    async with runtime.store.transaction(profile) as state:
        runtime.inbox.post(state, ctx.payload, ctx.now)
    return Result()


ROUTES = {
    "inbox/list/seq_start": letters,
    "inbox/list/seq_continue": partial(more, family=LETTERS),
    "inbox/message/open": open_letter,
    "inbox/message/multi_open": open_letters,
    "inbox_archive/list/seq_start": history,
    "inbox_archive/list/seq_continue": partial(more, family=HISTORY),
    "debug/inbox/message/post": post_debug,
}
