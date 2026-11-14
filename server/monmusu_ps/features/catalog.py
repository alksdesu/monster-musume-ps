from functools import partial

from ..core import pagination
from ..core.context import Context
from ..core.sync import Result


def _collections(ctx: Context, fields: tuple[tuple[str, str], ...]) -> Result:
    return Result(data={name: ctx.state[key] for name, key in fields})


def _start(ctx: Context, family: str, rows: list, extra: dict | None = None) -> Result:
    return Result(
        data=pagination.start(ctx.session, family, rows, ctx.payload["per_view"], extra)
    )


def _continue(ctx: Context, family: str) -> Result:
    return Result(
        data=pagination.resume(ctx.session, family, ctx.payload["sequence_token"])
    )


def unit(ctx: Context) -> Result:
    instance_id = ctx.payload["instance_id"]
    for row in ctx.state["units"]:
        if row["instance_id"] == instance_id:
            return Result(data={"unit": row})
    raise KeyError(f"Unknown unit instance: {instance_id}")


def items(ctx: Context) -> Result:
    return _start(ctx, "item/list", ctx.state["items"])


def _ranges(ctx: Context, collection: str, family: str) -> Result:
    by_id = {row["id"]: row for row in ctx.runtime.config.read()[collection]}
    by_id.update((row["id"], row) for row in ctx.state[collection])
    ranges = ctx.payload["range_names"]
    rows = [
        row
        for identifier, row in by_id.items()
        if not ranges
        or any(
            identifier == name or identifier.startswith(name + ".") for name in ranges
        )
    ]
    return _start(ctx, family, rows)


def _quest_scope(range_names) -> tuple[int | None, int | None]:
    if not isinstance(range_names, list) or len(range_names) > 2:
        raise ValueError("range_names holds at most a quest category and a chapter")
    values = []
    for name in range_names:
        if not isinstance(name, str) or not (name.isascii() and name.isdigit()):
            raise ValueError(
                "range_names must be decimal quest categories and chapters"
            )
        values.append(int(name))
    category, section = (*values, None, None)[:2]
    return category, section


def quest_records(ctx: Context) -> Result:
    category, section = _quest_scope(ctx.payload["range_names"])
    rows = [
        row
        for row in ctx.state["quest_records"]
        if (category is None or row["category"] == category)
        and (section is None or row["section"] == section)
    ]
    return _start(ctx, "quest/record/list", rows)


def secret_records(ctx: Context) -> Result:
    return Result(ctx.runtime.secret_quests.listing(ctx.state))


def paid_coin_batches(ctx: Context) -> Result:
    return _start(ctx, "purchase/vcr/record/list", [])


ROUTES = {
    "unit/all": partial(
        _collections, fields=(("units", "units"), ("charms", "charms"))
    ),
    "unit/": unit,
    "bom/all": partial(_collections, fields=(("boms", "boms"),)),
    "wallet/all": partial(_collections, fields=(("wallets", "wallets"),)),
    "item/list": items,
    "item/list/continue": partial(_continue, family="item/list"),
    "period/list/seq_start": partial(
        _ranges, collection="periods", family="period/list"
    ),
    "period/list/seq_continue": partial(_continue, family="period/list"),
    "limit/list/seq_start": partial(_ranges, collection="limits", family="limit/list"),
    "limit/list/seq_continue": partial(_continue, family="limit/list"),
    "quest/record/list": quest_records,
    "quest/record/list/continue": partial(_continue, family="quest/record/list"),
    "quest/record/secret": secret_records,
    "purchase/vcr/record/list": paid_coin_batches,
    "purchase/vcr/record/list/continue": partial(
        _continue, family="purchase/vcr/record/list"
    ),
}
