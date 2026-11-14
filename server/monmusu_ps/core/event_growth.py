from copy import deepcopy

from .errors import ApiRejected
from .sync import Result, diff_state


UNWISH_EVENT = 8
JOIN, LEVEL, AWAKENING, CLASS_CHANGE = "1", "2", "3", "4"
GOLD_PRIZE, RESOURCE = "5", "6"
GOLD = "wallet.point.gold"
UNIT_PREFIX = "unit.card."
SOURCE = "event.growth"
INVALID_EVENT, OUT_OF_SCHEDULE = "INVALID_EVENT_ID", "OUT_OF_SCHEDULE"


class EventGrowth:
    def __init__(self, masters, config, resources, boosts):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.boosts = boosts

    def _settings(self) -> dict:
        return self.config.read()["event_growth"]

    def _event(self, event_id, now: int) -> tuple[dict, list[dict]]:
        if type(event_id) is not int:
            raise ValueError("event_id must be an integer")
        try:
            event = self.masters.lookup("event_list_data", "id", event_id)
        except KeyError as error:
            raise ApiRejected(INVALID_EVENT) from error
        rows = [
            row
            for row in self.masters.table("event_character_growth")
            if row["event_id"] == event_id
        ]
        if not rows and event["event_type"] != UNWISH_EVENT:
            raise ApiRejected(INVALID_EVENT)
        term = event["open_time"], event["close_time"]
        scheduled = self._settings()["follow_master_schedule"]
        if scheduled and not self.masters.in_term(*term, now):
            raise ApiRejected(OUT_OF_SCHEDULE)
        return event, sorted(rows, key=lambda row: (row["star_count"], row["id"]))

    def _progress(self, state: dict, event: dict) -> int:
        quests = {
            row["id"]
            for row in self.masters.table("quest_data")
            if row["chapter"] == event["chapter"]
        }
        records = [row for row in state["quest_records"] if row["quest_id"] in quests]
        # Subjugation events grow with the best training rank instead of stars.
        if event["event_type"] == UNWISH_EVENT:
            return max((row["t_best_rank"] for row in records), default=0)
        return sum(row["record_rating"] for row in records)

    def _strengthen(self, state: dict, row: dict, now: int) -> bool:
        unit = next(
            (unit for unit in state["units"] if unit["card_id"] == row["card_id"]), None
        )
        if unit is None:
            return False
        value = row["value"]
        boosted = {
            LEVEL: lambda: self.boosts.level(state, unit, value, now),
            AWAKENING: lambda: self.boosts.awaken(unit, value),
            CLASS_CHANGE: lambda: self.boosts.promote(unit, value),
        }[row["category"]]()
        if boosted:
            unit["updated_at"] = now
        return boosted

    def confirm(self, state: dict, payload: dict, now: int) -> Result:
        event, rows = self._event(payload["event_id"], now)
        progress = self._progress(state, event)
        before = deepcopy(state)
        granted = state["event_growth"].setdefault(str(event["id"]), [])
        alternatives = self._settings()["alternatives"]
        source = f"{SOURCE}:{event['id']}"
        achieved, substitutes = [], []
        for row in rows:
            if row["star_count"] > progress or row["id"] in granted:
                continue
            category = row["category"]
            if category in (JOIN, GOLD_PRIZE, RESOURCE):
                rid = {
                    JOIN: f"{UNIT_PREFIX}{row['value']}",
                    GOLD_PRIZE: GOLD,
                    RESOURCE: row["reward"],
                }[category]
                order = {"rid": rid, "val": 1 if category == JOIN else row["value"]}
                # A joining unit has to exist before the rows that train it.
                self.resources.grant(state, [order], now, policy="mail", source=source)
            elif not self._strengthen(state, row, now):
                substitutes += deepcopy(alternatives[category])
            achieved.append(row["id"])
        self.resources.grant(state, substitutes, now, policy="mail", source=source)
        granted.extend(achieved)
        return Result(
            {
                "achieved_list": achieved,
                "alternative_list": substitutes,
                "sum_star_count": progress,
                "last_id": max(granted, default=0),
            },
            diff_state(before, state),
        )
