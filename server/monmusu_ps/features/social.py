from functools import partial

from ..core import pagination
from ..core.context import Context
from ..core.sync import Result, scoped


FOLLOWING, FOLLOWERS = "social/follow/list", "social/follower/list"


def following(ctx: Context) -> Result:
    social = ctx.runtime.social
    rows = social.following(ctx.state, ctx.now)
    extra = {"friend_point": social.friend_point(ctx.state)}
    return Result(
        pagination.start(ctx.session, FOLLOWING, rows, ctx.payload["per_view"], extra)
    )


def followers(ctx: Context) -> Result:
    social = ctx.runtime.social
    rows = social.followers(ctx.state, ctx.now)
    reminders = social.remind(ctx.state, ctx.now)
    return Result(
        pagination.start(ctx.session, FOLLOWERS, rows, ctx.payload["per_view"]),
        scoped(ctx.state, rev_reminder=reminders),
    )


def more(ctx: Context, family: str) -> Result:
    return Result(pagination.resume(ctx.session, family, ctx.payload["sequence_token"]))


def follower_count(ctx: Context) -> Result:
    return Result({"followers": ctx.runtime.social.follower_count(ctx.state)})


def recommended(ctx: Context) -> Result:
    return Result({"players": ctx.runtime.social.recommended(ctx.state, ctx.now)})


def search(ctx: Context) -> Result:
    found = ctx.runtime.social.search(ctx.state, ctx.payload["player_code"], ctx.now)
    return Result({"found_data": found})


def follow(ctx: Context) -> Result:
    return ctx.runtime.social.follow(ctx.state, ctx.payload["player_code"], ctx.now)


def unfollow(ctx: Context) -> Result:
    return ctx.runtime.social.unfollow(ctx.state, ctx.payload["player_code"], ctx.now)


def greet(ctx: Context) -> Result:
    return ctx.runtime.social.greet(ctx.state, ctx.payload["player_codes"], ctx.now)


def lending(ctx: Context) -> Result:
    return Result({"rental_units": ctx.runtime.social.lending(ctx.state)})


def set_lending(ctx: Context) -> Result:
    return ctx.runtime.social.set_lending(ctx.state, ctx.payload, ctx.now)


def selects(ctx: Context) -> Result:
    return Result(ctx.runtime.social.selects(ctx.state, ctx.now))


ROUTES = {
    "social/follow/list": following,
    "social/follow/list/continue": partial(more, family=FOLLOWING),
    "social/follower/list": followers,
    "social/follower/list/continue": partial(more, family=FOLLOWERS),
    "social/follower/count": follower_count,
    "social/player/selects/recommended": recommended,
    "social/player/search": search,
    "social/follow/entry": follow,
    "social/follow/delete": unfollow,
    "social/follow/greeting": greet,
    "social/rental_units": lending,
    "social/rental_units/update": set_lending,
    "social/rental_units/selects": selects,
}
