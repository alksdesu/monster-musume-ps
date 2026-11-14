from .journal import LOGIN


class Attendance:
    def __init__(self, calendar, journal):
        self.calendar = calendar
        self.journal = journal

    def check_in(self, state: dict, now: int) -> None:
        today = self.calendar.day_start(now)
        previous = state["last_login_day"]
        away = 0
        if previous:
            away = (self.calendar.day(today) - self.calendar.day(previous)).days
        state["last_login_day"] = today
        state["last_login_at"] = now
        data = {"first_today": previous != today, "away_days": away}
        self.journal.record(state, "login", LOGIN, now, data)
