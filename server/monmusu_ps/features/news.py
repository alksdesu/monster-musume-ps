from ..core import pagination
from ..core.context import Context
from ..core.sync import Result


FAMILY = "news/list"


def listing(ctx: Context) -> Result:
    rows, version = ctx.runtime.news.listing(ctx.now)
    # A reader already holding this edition has nothing to fetch.
    fresh = [] if ctx.payload["news_version"] == version else rows
    return Result(
        pagination.start(
            ctx.session,
            FAMILY,
            fresh,
            ctx.payload["per_view"],
            {"news_version": version},
        )
    )


def more(ctx: Context) -> Result:
    return Result(pagination.resume(ctx.session, FAMILY, ctx.payload["sequence_token"]))


ROUTES = {"news/list": listing, "news/list/continue": more}
