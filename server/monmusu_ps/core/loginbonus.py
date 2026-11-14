from copy import deepcopy
from datetime import date

from .sync import Result, diff_state
from .tutorial import LOGIN_BONUS


MONTHLY, BEGINNER, EVENT, UNLOCK = "monthly_vip", "beginner", "event", "unlock"
COMEBACK = "comeback_cp"
ORDER = (MONTHLY, BEGINNER, EVENT, UNLOCK, COMEBACK)
RECORDED = (UNLOCK, COMEBACK)
UNLOCK_PREFIX = "loginbonus.unlock."


def unlocked(sheet_id: int) -> dict:
    return {"sheet_id": sheet_id, "progress": 0, "stamp_at": 0}


def holds(records: list[dict], sheet_id: int) -> bool:
    return any(row["sheet_id"] == sheet_id for row in records)


def restart(records: list[dict], sheet_id: int) -> None:
    row = next((row for row in records if row["sheet_id"] == sheet_id), None)
    if row is None:
        records.append(unlocked(sheet_id))
    else:
        row.update(unlocked(sheet_id))


class LoginBonus:
    def __init__(
        self, masters, calendar, mailbox, resources, tutorial, comeback, social
    ):
        self.masters = masters
        self.calendar = calendar
        self.mailbox = mailbox
        self.resources = resources
        self.tutorial = tutorial
        self.comeback = comeback
        self.social = social
        self._document = None
        self._cells: dict[int, dict[int, dict]] = {}

    def _days(self, sheet: dict) -> dict[int, dict]:
        document = self.masters.document("login_bonus_cell")
        if document is not self._document:
            self._cells = {
                row["dayData"][0]["id"]: {cell["day"]: cell for cell in row["dayData"]}
                for row in document["table"]
            }
            self._document = document
        return self._cells[sheet["table_id"]]

    def _open(self, state: dict, sheet: dict, today: date, now: int) -> bool:
        if not self.masters.in_term(sheet["start_dt"], sheet["end_dt"], now):
            return False
        if sheet["category"] == MONTHLY:
            return today.month in sheet["months"]
        if sheet["category"] == BEGINNER:
            age = today - self.calendar.day(state["created_at"])
            return age.days < self.masters.property("Int", 40)
        if sheet["category"] in RECORDED:
            return holds(state["login_bonus"], sheet["id"])
        return sheet["category"] == EVENT

    def _sheets(self, state: dict, today: date, now: int) -> list[dict]:
        opened = [
            sheet
            for sheet in self.masters.table("login_bonus")
            if self._open(state, sheet, today, now)
        ]
        return sorted(
            opened, key=lambda sheet: (ORDER.index(sheet["category"]), sheet["id"])
        )

    def _progress(self, record: dict | None, sheet: dict, today: date) -> int:
        if record is None:
            return 0
        if sheet["category"] == MONTHLY:
            stamped = self.calendar.day(record["stamp_at"])
            if (stamped.year, stamped.month) != (today.year, today.month):
                return 0
        return record["progress"]

    @staticmethod
    def _view(record: dict, progress: int, previous: int) -> dict:
        return {
            "sheet_id": record["sheet_id"],
            "progress": progress,
            "prev_progress": previous,
            "stamp_at": record["stamp_at"],
        }

    def sheets(self, state: dict, now: int) -> list[dict]:
        today = self.calendar.day(now)
        records = {row["sheet_id"]: row for row in state["login_bonus"]}
        views = []
        for sheet in self._sheets(state, today, now):
            record = records.get(sheet["id"])
            if record is not None:
                progress = self._progress(record, sheet, today)
                views.append(self._view(record, progress, progress))
        return views

    def stamp(self, state: dict, now: int) -> Result:
        before = deepcopy(state)
        self.comeback.welcome(state, now)
        today = self.calendar.day(now)
        records = {row["sheet_id"]: row for row in state["login_bonus"]}
        views, letters = [], []
        for sheet in self._sheets(state, today, now):
            record = records.get(sheet["id"])
            progress = self._progress(record, sheet, today)
            days = self._days(sheet)
            stamped = (
                record is not None and self.calendar.day(record["stamp_at"]) == today
            )
            if stamped or progress >= len(days):
                views.append(self._view(record, progress, progress))
                continue
            if record is None:
                record = {"sheet_id": sheet["id"]}
                state["login_bonus"].append(record)
            record.update(progress=progress + 1, stamp_at=now)
            views.append(self._view(record, progress + 1, progress))
            source = f"loginbonus.stamp_reward:{sheet['id']}:{progress + 1}"
            letters += [
                (source, order)
                for row in days[progress + 1]["rewards"]["m_rewards"]
                if (order := self.resources.reward_order(row)) is not None
            ]
        for currency in (True, False):
            for source, order in letters:
                if self.mailbox.currency(order["rid"]) == currency:
                    self.mailbox.post(state, order, source, now)
        self.tutorial.complete_step(state, LOGIN_BONUS, now)
        friend_points = self.social.collect(state, now)
        return Result(
            {"sheets": views, "get_friend_points": friend_points},
            diff_state(before, state),
        )
