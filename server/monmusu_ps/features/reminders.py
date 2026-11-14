from copy import deepcopy

from ..core.context import Context
from ..core.errors import ApiNotFound
from ..core.sync import Result, diff_state, scoped


def _key(ctx: Context) -> tuple[int, int]:
    group, identity = ctx.payload["group"], ctx.payload["id"]
    if type(group) is not int or type(identity) is not int:
        raise ValueError("Reminder group and id must be integers")
    return group, identity


def _row(rows: list[dict], key: tuple[int, int]) -> dict | None:
    return next((row for row in rows if (row["group"], row["id"]) == key), None)


def flags(ctx: Context) -> Result:
    return Result({"flag_reminders": ctx.state["flag_reminders"]})


def checkoff_revision(ctx: Context) -> Result:
    key = _key(ctx)
    revision = ctx.payload["rev"]
    if type(revision) is not int:
        raise ValueError("Reminder revision must be an integer")
    row = _row(ctx.state["revisioned_reminders"], key)
    if row is None:
        raise ApiNotFound(f"reminder.rev.{key[0]}.{key[1]}")
    seen, name = ctx.state["reminder_seen"], f"{key[0]}.{key[1]}"
    seen[name] = max(seen.get(name, 0), revision)
    before = deepcopy(ctx.state)
    # A revision newer than the one the client saw keeps the reminder alive.
    if row["rev"] <= revision:
        row["rev"] = 0
    return Result(sync=diff_state(before, ctx.state), include_data=False)


def checkoff_flag(ctx: Context) -> Result:
    key = _key(ctx)
    rows = ctx.state["flag_reminders"]
    row = _row(rows, key)
    if row is None:
        raise ApiNotFound(f"reminder.flag.{key[0]}.{key[1]}")
    rows.remove(row)
    return Result(sync=scoped(ctx.state, flag_reminder=rows), include_data=False)


ROUTES = {
    "reminder/flag/list": flags,
    "reminder/rev/checkoff": checkoff_revision,
    "reminder/flag/checkoff": checkoff_flag,
}
