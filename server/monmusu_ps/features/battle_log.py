from ..core.context import Context
from ..core.sync import Result


def put(ctx: Context) -> Result:
    return ctx.runtime.battle_logs.put(ctx.state, ctx.payload, ctx.now)


def locate(ctx: Context) -> Result:
    return ctx.runtime.battle_logs.locate(ctx.state, ctx.payload, ctx.now)


def mine(ctx: Context) -> Result:
    return ctx.runtime.battle_logs.mine(ctx.state, ctx.now)


def feed(ctx: Context) -> Result:
    return ctx.runtime.battle_logs.feed(ctx.state, ctx.now)


def like(ctx: Context) -> Result:
    return ctx.runtime.battle_logs.like(ctx.state, ctx.payload, ctx.now)


def share(ctx: Context) -> Result:
    return ctx.runtime.battle_logs.share(ctx.state, ctx.payload, ctx.now)


def unshare(ctx: Context) -> Result:
    return ctx.runtime.battle_logs.unshare(ctx.state, ctx.payload, ctx.now)


ROUTES = {
    "battle_log/put": put,
    "battle_log/": locate,
    "battle_log/all": mine,
    "battle_log/feed/list": feed,
    "battle_log/like": like,
    "battle_log/share": share,
    "battle_log/unshare": unshare,
}
