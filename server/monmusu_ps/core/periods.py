from .journal import PLAYER_RANK_UP, QUEST_COMPLETE, TUTORIAL_PROGRESS
from .tutorial import COMPLETE


DAY = 86400
QUEST_CLEAR, START_DASH = "クエストクリア", "スタートダッシュガチャ"
PLAYER_RANK = "プレイヤーランク"
SALES_PREFIX = "sales."
BLESSING_PREFIX = "blessing."
MINUTE = 60
START_DASH_GACHA = "gacha.sd_gacha"
START_DASH_PANEL = "sdpanel_mission"


def _seconds(span: str) -> int:
    hours, minutes, seconds = (int(part) for part in span.split(":"))
    return (hours * 60 + minutes) * 60 + seconds


class Periods:
    def __init__(self, masters, config, calendar, journal):
        self.masters = masters
        self.config = config
        self.calendar = calendar
        self._document = None
        self._sales: dict[tuple[str, str], list[dict]] = {}
        journal.subscribe(QUEST_COMPLETE, self._on_quest_complete)
        journal.subscribe(PLAYER_RANK_UP, self._on_rank_up)
        journal.subscribe(TUTORIAL_PROGRESS, self._on_tutorial_progress)

    def active(self, state: dict, identity: str, now: int) -> bool:
        rows = {row["id"]: row for row in self.config.read()["periods"]}
        rows.update((row["id"], row) for row in state["periods"])
        if identity not in rows:
            return False
        window = rows[identity]["period"]
        return window["start_at"] <= now < window["end_at"]

    def conditional_sales(self, condition: str, parameter: str = "") -> list[dict]:
        document = self.masters.document("shop_data")
        if document is not self._document:
            sales: dict[tuple[str, str], list[dict]] = {}
            conditional = (row for row in document["table"] if row["sales_conditions"])
            for row in sorted(conditional, key=lambda row: row["id"]):
                key = row["sales_conditions"], row["sales_conditions_param"]
                sales.setdefault(key, []).append(row)
            self._sales, self._document = sales, document
        return self._sales.get((condition, parameter), [])

    @staticmethod
    def open(state: dict, identity: str, start: int, end: int, now: int) -> None:
        row = next((row for row in state["periods"] if row["id"] == identity), None)
        if row is None:
            row = {"id": identity}
            state["periods"].append(row)
        row.update(period={"start_at": start, "end_at": end}, counted_at=now)

    def blessing(self, shop_id: int) -> dict | None:
        rows = self.masters.table("beast_gods_blessing_table")
        return next((row for row in rows if row["shop_id"] == shop_id), None)

    def bless(self, state: dict, blessing: dict, uses: int, now: int) -> None:
        identity = f"{BLESSING_PREFIX}{blessing['id']}"
        row = next((row for row in state["periods"] if row["id"] == identity), None)
        window = row["period"] if row else None
        running = window is not None and window["start_at"] <= now < window["end_at"]
        start, end = (window["start_at"], window["end_at"]) if running else (now, now)
        # A blessing bought while it still runs has its duration added on top.
        span = blessing["effect_duration"] * MINUTE * uses
        self.open(state, identity, start, end + span, now)

    def open_sales(self, state: dict, goods: list[dict], now: int) -> None:
        for row in goods:
            end = now + _seconds(row["sales_period"])
            self.open(state, f"{SALES_PREFIX}{row['id']}", now, end, now)

    def _on_rank_up(self, state: dict, event: dict) -> None:
        reached = range(event["data"]["from"] + 1, event["data"]["to"] + 1)
        goods = [
            row
            for rank in reached
            for row in self.conditional_sales(PLAYER_RANK, str(rank))
        ]
        self.open_sales(state, goods, event["at"])

    def _on_quest_complete(self, state: dict, event: dict) -> None:
        if not event["data"]["first_clear"]:
            return
        quest_id = event["data"]["quest_id"]
        unlocking = self.config.read()["quest_clear_sales"]
        goods = self.conditional_sales(QUEST_CLEAR, str(quest_id)) + [
            row
            for row in self.conditional_sales(QUEST_CLEAR)
            if unlocking.get(row["name"]) == quest_id
        ]
        self.open_sales(state, sorted(goods, key=lambda row: row["id"]), event["at"])

    def _on_tutorial_progress(self, state: dict, event: dict) -> None:
        if event["data"]["progress"] != COMPLETE:
            return
        now = event["at"]
        settings = self.config.read()["start_dash"]
        opening = self.calendar.day_start(now)
        closing = opening + settings["panel_days"] * DAY
        frames = {
            row["id"]
            for row in self.masters.table("mission_panel_frame")
            if row["get_period_key"] == START_DASH_PANEL
            and self.masters.in_term(row["startDate"], row["endDate"], now)
        }
        panels = sorted(
            (
                row
                for row in self.masters.table("mission_panel_content")
                if row["panel_frame_id"] in frames
            ),
            key=lambda row: row["id"],
        )
        for elapsed, panel in enumerate(panels):
            start = max(now, opening + elapsed * DAY)
            self.open(state, panel["period_id"], start, closing, now)
        self.open(state, START_DASH_GACHA, now, now + settings["gacha_seconds"], now)
        self.open_sales(state, self.conditional_sales(START_DASH), now)
