from .calendar import Calendar


LIFETIME, DAILY, WEEKLY, MONTHLY, DATETIME = (
    "",
    "daily",
    "weekly",
    "monthly",
    "datetime",
)
CYCLES = (LIFETIME, DAILY, WEEKLY, MONTHLY, DATETIME)


class Limits:
    def __init__(self, calendar: Calendar):
        self.calendar = calendar

    def _expired(self, counted_at: int, cycle: str, anchor, now: int) -> bool:
        calendar = self.calendar
        if cycle == DAILY:
            return calendar.day(counted_at) != calendar.day(now)
        if cycle == WEEKLY:
            return calendar.week_start(counted_at, anchor) != calendar.week_start(
                now, anchor
            )
        if cycle == MONTHLY:
            return calendar.month_start(counted_at, anchor) != calendar.month_start(
                now, anchor
            )
        if cycle == DATETIME:
            moment = calendar.masters.timestamp(anchor)
            return counted_at < moment <= now
        return False

    def _times(
        self, state: dict, identifier: str, cycle: str, anchor, now: int
    ) -> tuple[dict | None, int]:
        if cycle not in CYCLES:
            raise NotImplementedError(f"Limit cycle {cycle!r} is not implemented")
        row = next((row for row in state["limits"] if row["id"] == identifier), None)
        if row is None:
            return None, 0
        if self._expired(row["counted_at"], cycle, anchor, now):
            return row, 0
        return row, row["times"]

    def used(
        self, state: dict, identifier: str, cycle: str, now: int, anchor=None
    ) -> int:
        return self._times(state, identifier, cycle, anchor, now)[1]

    def count(
        self,
        state: dict,
        identifier: str,
        cycle: str,
        amount: int,
        now: int,
        anchor=None,
    ) -> None:
        row, times = self._times(state, identifier, cycle, anchor, now)
        if row is None:
            row = {"id": identifier}
            state["limits"].append(row)
        row.update(times=times + amount, counted_at=now)

    def restart(self, state: dict, identifier: str, now: int) -> None:
        row = next((row for row in state["limits"] if row["id"] == identifier), None)
        if row is not None:
            row.update(times=0, counted_at=now)
