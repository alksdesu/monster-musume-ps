from .errors import ApiRejected


NOT_QUALIFIED, OUT_OF_SCHEDULE = "NOT_QUALIFIED", "OUT_OF_SCHEDULE"
LIMIT_OVER = "LIMIT_OVER_OF_QUEST_EXEC"
LIMITED_FIELDS = {"game_try": "tries", "game_clear": "wins"}
QUEST_RATED, STORY_READ = 0, 1


def quest_record(state: dict, quest_id: int) -> dict | None:
    return next(
        (row for row in state["quest_records"] if row["quest_id"] == quest_id), None
    )


class QuestGates:
    def __init__(self, masters, secrets):
        self.masters = masters
        self.secrets = secrets

    @staticmethod
    def _story_read(state: dict, category: int, identity: int) -> bool:
        prefix, suffix = f"story.{category}.", f".{identity}"
        return any(
            row["is_already_read"]
            and row["id"].startswith(prefix)
            and row["id"].endswith(suffix)
            for row in state["stories"]
        )

    def _met(self, state: dict, condition: dict) -> bool:
        kind, values = condition["check_type"], condition["check_values"]
        if kind == QUEST_RATED:
            record = quest_record(state, values[0])
            return record is not None and record["record_rating"] > 0
        if kind == STORY_READ:
            return self._story_read(state, values[0], values[1])
        return False

    def _released(self, state: dict, condition_id: int) -> bool:
        conditions = [
            condition
            for group in self.masters.table("quest_release_condition")
            for condition in group["releaseConditionData"]
            if condition["condition_id"] == condition_id
        ]
        return bool(conditions) and all(
            self._met(state, condition) for condition in conditions
        )

    def _qualified(self, state: dict, quest: dict) -> bool:
        rank = next(
            row
            for row in state["modules"]["experience"]
            if row["section"] == "player_rank"
        )
        if rank["level"] < quest["needRank"]:
            return False
        if quest["needQuestIndex"]:
            previous = quest_record(state, quest["needQuestIndex"])
            if previous is None or previous["wins"] == 0:
                return False
        member = quest["limitMemberId"]
        if member and all(unit["card_id"] != member for unit in state["units"]):
            return False
        return not quest["releaseCondId"] or self._released(
            state, quest["releaseCondId"]
        )

    def check(self, state: dict, quest: dict, now: int, attempts: int = 1) -> None:
        if not self._qualified(state, quest):
            raise ApiRejected(NOT_QUALIFIED)
        if not self.masters.in_term(quest["openTime"], quest["closeTime"], now):
            raise ApiRejected(OUT_OF_SCHEDULE)
        self.secrets.gate(quest, now)
        counted = LIMITED_FIELDS.get(quest["questlimit_timing"])
        if counted is not None:
            record = quest_record(state, quest["id"])
            used = record[counted] if record else 0
            if used + attempts > quest["questlimit_max_times"]:
                raise ApiRejected(LIMIT_OVER)
