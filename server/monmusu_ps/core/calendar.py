from datetime import date, datetime, time, timedelta

from .masterdata import MasterData


# The client treats this timestamp, like zero, as "never".
FOREVER = 253402300799


class Calendar:
    def __init__(self, masters: MasterData):
        self.masters = masters

    def _moment(self, now: int) -> datetime:
        return datetime.fromtimestamp(now, self.masters.zone())

    def _reset(self) -> timedelta:
        return timedelta(hours=self.masters.property("Int", 2))

    def day(self, now: int) -> date:
        return (self._moment(now) - self._reset()).date()

    def day_start(self, now: int) -> int:
        midnight = datetime.combine(self.day(now), time(), self.masters.zone())
        return int((midnight + self._reset()).timestamp())

    # Weekdays follow the client's DayOfWeek numbering: Sunday is 0.
    def week_start(self, now: int, weekday: int) -> date:
        today = self.day(now)
        return today - timedelta(days=(today.weekday() + 1 - weekday) % 7)

    def month_start(self, now: int, day_of_month: int) -> date:
        today = self.day(now)
        anchor = max(1, day_of_month)
        if today.day >= anchor:
            return today.replace(day=anchor)
        previous = today.replace(day=1) - timedelta(days=1)
        return previous.replace(day=min(anchor, previous.day))

    def date_end(self, now: int) -> int:
        last = datetime.combine(
            self._moment(now).date(), time(23, 59, 59), self.masters.zone()
        )
        return int(last.timestamp())
