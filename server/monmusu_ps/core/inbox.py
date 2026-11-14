from copy import deepcopy

from .errors import ApiRejected
from .resources import ResourceCapacityError
from .sync import Result, diff_state


OPENED, EXPIRED = 1, 2
ASCENDING, DESCENDING = 1, 2
# The client keeps a refused letter only for this reason; any other removes its row.
OVERFLOW = "ASSET_CAPACITY_OVERFLOW"
MISSING = "NOT_FOUND_MESSAGE"
UNEXPECTED = "NOT_EXPECTED_MESSAGE_REWARD"
FAULTY = "SOMETHING_MESSAGE_ERROR"
DEBUG_SOURCE = "debug.post"


class Inbox:
    def __init__(self, config, mailbox, resources, spoken=lambda text: text):
        self.config = config
        self.mailbox = mailbox
        self.resources = resources
        self.spoken = spoken

    def _settings(self) -> dict:
        return self.config.read()["mail"]

    def _file(self, state: dict, kind: int, letter: dict, moment: int) -> None:
        lifetime = self._settings()["archive"]["lifetime_seconds"]
        state["inbox_archive"].append(
            {
                "log_type": kind,
                "item": letter,
                "log_at": moment,
                "expire_at": self.mailbox.deadline(moment, lifetime),
            }
        )

    def sweep(self, state: dict, now: int) -> None:
        kept = []
        for letter in state["inbox"]:
            if self.mailbox.expired(letter, now):
                self._file(state, EXPIRED, letter, letter["expire_at"])
            else:
                kept.append(letter)
        state["inbox"] = kept
        logs = [
            row for row in state["inbox_archive"] if not self.mailbox.expired(row, now)
        ]
        surplus = len(logs) - self._settings()["archive"]["capacity"]
        state["inbox_archive"] = logs[max(0, surplus) :]

    # The save keeps a letter's title as the game data spells it; the game is handed it in its language.
    def _told(self, row: dict) -> dict:
        if "item" in row:
            return {**row, "item": self._told(row["item"])}
        return {**row, "message": self.spoken(row["message"])}

    def _ordered(self, rows: list[dict], order) -> list[dict]:
        rows = [self._told(row) for row in rows]
        if order not in (ASCENDING, DESCENDING):
            raise ValueError("Inbox order must be 1 or 2")
        return rows[::-1] if order == DESCENDING else rows

    def letters(self, state: dict, payload: dict, now: int) -> list[dict]:
        self.sweep(state, now)
        rows = sorted(
            (row for row in state["inbox"] if row["inbox"] == payload["inbox_name"]),
            key=lambda row: row["no"],
        )
        return self._ordered(rows, payload["order"])

    def history(self, state: dict, payload: dict, now: int) -> list[dict]:
        self.sweep(state, now)
        kind = payload["log_filter"]
        rows = []
        if payload["inbox_name"] == self._settings()["archive"]["inbox"]:
            rows = [
                row
                for row in state["inbox_archive"]
                if not kind or row["log_type"] == kind
            ]
        return self._ordered(rows, payload["order"])

    @staticmethod
    def _letter(state: dict, box, number) -> dict | None:
        if type(number) is not int:
            return None
        return next(
            (
                row
                for row in state["inbox"]
                if row["inbox"] == box and row["no"] == number
            ),
            None,
        )

    def _claim(self, state: dict, box, number, now: int) -> dict:
        letter = self._letter(state, box, number)
        if letter is None:
            raise ApiRejected(MISSING)
        try:
            changes = self.resources.grant(state, [letter["reward"]], now)
        except ResourceCapacityError as error:
            raise ApiRejected(OVERFLOW) from error
        state["inbox"].remove(letter)
        self._file(state, OPENED, letter, now)
        return {"no": number, "reward": changes.deliveries[0]}

    @staticmethod
    def _refusal(box, number, error: ApiRejected) -> dict:
        return {"inbox": box, "no": number, "reason": error.reason}

    def open(self, state: dict, payload: dict, now: int) -> Result:
        before = deepcopy(state)
        self.sweep(state, now)
        box, number = payload["inbox_name"], payload["message_no"]
        try:
            data = {"success": self._claim(state, box, number, now)}
        except ApiRejected as error:
            data = {"rejected_reason": self._refusal(box, number, error)}
        return Result(data, diff_state(before, state))

    def open_many(self, state: dict, payload: dict, now: int) -> Result:
        before = deepcopy(state)
        self.sweep(state, now)
        box, numbers = payload["inbox_name"], payload["message_no_list"]
        if not isinstance(numbers, list):
            raise ValueError("message_no_list must be a list")
        successes, refusals = [], []
        for number in numbers:
            try:
                successes.append(self._claim(state, box, number, now))
            except ApiRejected as error:
                refusals.append(self._refusal(box, number, error))
        return Result(
            {"successes": successes, "rejected_reasons": refusals},
            diff_state(before, state),
        )

    def redeem(self, state: dict, number, rid: str, now: int) -> int:
        self.sweep(state, now)
        letter = self._letter(state, self._settings()["inbox"], number)
        if letter is None:
            raise ApiRejected(MISSING)
        if letter["reward"]["rid"] != rid:
            raise ApiRejected(UNEXPECTED)
        try:
            delivered = self._claim(state, letter["inbox"], number, now)
        except ApiRejected as error:
            raise ApiRejected(FAULTY) from error
        return delivered["reward"]["inc"]

    def authorize_debug(self) -> None:
        if not self._settings()["debug_post"]:
            raise PermissionError("Debug mail posting is disabled")

    def post(self, state: dict, payload: dict, now: int) -> dict:
        box, message = payload["inbox_name"], payload["message"]
        if box != self._settings()["inbox"]:
            raise ValueError(f"Unknown inbox: {box}")
        if not isinstance(message, str | None):
            raise ValueError("Mail message must be text")
        if not isinstance(payload["reward"], dict):
            raise ValueError("Mail reward must be a resource order")
        reward = {"rid": payload["reward"]["rid"], "val": payload["reward"]["val"]}
        self.resources.check([reward])
        if not reward["val"]:
            raise ValueError("Mail rewards must be positive")
        return self.mailbox.post(state, reward, DEBUG_SOURCE, now, message or None)
