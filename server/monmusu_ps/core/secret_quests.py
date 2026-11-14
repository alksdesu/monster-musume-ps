from datetime import datetime
import logging
import math

from .errors import ApiRejected
from .quest_gates import OUT_OF_SCHEDULE, quest_record


LOGGER = logging.getLogger(__name__)
AGGREGATING, INVALID_STATEMENT = "AGGREGATING", "INVALID_STATEMENT"
# The client refuses event hunts from 04:30:00 until the day changes at 05:00.
AGGREGATION = range(4 * 3600 + 30 * 60, 5 * 3600 - 1)
SCORE_BYTES = 4
SCORE_CAP = 47
SOURCE = "quest.wild_hunt"
REJECT = "reject"


class SecretQuests:
    def __init__(self, masters, config, resources, mailbox, tracks, store):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.mailbox = mailbox
        self.tracks = tracks
        self.store = store

    def _settings(self) -> dict:
        return self.config.read()["battle"]

    def _categories(self) -> list[dict]:
        rows = self.masters.table("quest_secret_category_table")
        # The official listing orders the categories by their id as text.
        return sorted(rows, key=lambda row: str(row["id"]))

    def category(self, quest_id: int) -> dict | None:
        try:
            group = self.masters.lookup(
                "quest_secret_group_table", "quest_id", quest_id
            )
        except KeyError:
            return None
        return self.masters.lookup(
            "quest_secret_category_table", "id", group["category_id"]
        )

    def _aggregating(self, category: dict, now: int) -> bool:
        if not category["is_hunt_event"] or not self._settings()["hunt_aggregation"]:
            return False
        moment = datetime.fromtimestamp(now, self.masters.zone())
        return moment.hour * 3600 + moment.minute * 60 + moment.second in AGGREGATION

    def gate(self, quest: dict, now: int) -> None:
        category = self.category(quest["id"])
        if category is None:
            return
        if not self.masters.in_term(category["start_at"], category["end_at"], now):
            raise ApiRejected(OUT_OF_SCHEDULE)
        if quest["isWildHunt"] and self._aggregating(category, now):
            raise ApiRejected(AGGREGATING)

    def _summary(self, state: dict, category: dict) -> dict:
        stars = best = total = 0
        for group in self.masters.table("quest_secret_group_table"):
            if group["category_id"] != category["id"]:
                continue
            record = quest_record(state, group["quest_id"])
            if record is None:
                continue
            quest = self.masters.lookup("quest_data", "id", group["quest_id"])
            if quest["isWildHunt"]:
                best, total = record["wh_best_score"], record["wh_total_score"]
            else:
                stars += record["record_rating"]
        return {
            "category_id": category["id"],
            "sum_star_count": stars,
            "max_hunt": best,
            "total_hunt": total,
        }

    def refresh(self, state: dict, quest: dict) -> None:
        category = self.category(quest["id"])
        if category is None:
            return
        summary = self._summary(state, category)
        rows = state["quest_secrets"]
        kept = next((row for row in rows if row["category_id"] == category["id"]), None)
        if kept is None:
            rows.append(summary)
        else:
            kept.update(summary)

    @staticmethod
    def _rank(scores: list[int], own: int) -> int:
        if own <= 0:
            return 0
        place = 1 + sum(score > own for score in scores)
        # The client shows the rank as the top percentage with two decimals.
        return max(1, math.ceil(place * 10000 / len(scores)))

    def listing(self, state: dict) -> dict:
        saves = {
            document["state"]["player"]["player_code"]: document["state"]
            for document in self.store.documents()
        }
        saves[state["player"]["player_code"]] = state
        summaries, ranks = [], []
        for category in self._categories():
            summary = self._summary(state, category)
            scores = [
                score
                for save in saves.values()
                if (score := self._summary(save, category)["max_hunt"]) > 0
            ]
            summaries.append(summary)
            ranks.append(
                {
                    "category_id": category["id"],
                    "rank": self._rank(scores, summary["max_hunt"]),
                }
            )
        return {"quest_secrets": summaries, "wild_hunt_ranks": ranks}

    def score(self, quest: dict, statement: dict) -> int:
        if not quest["isWildHunt"]:
            return 0
        return min(statement["ec_score"], self.masters.property("Int", SCORE_CAP))

    @staticmethod
    def valid_log(log_data) -> bool:
        return (
            isinstance(log_data, list)
            and len(log_data) == SCORE_BYTES
            and all(type(value) is int and 0 <= value <= 255 for value in log_data)
        )

    @staticmethod
    def decode(log_data: list[int], log_key: list[int]) -> int:
        size = len(log_key)
        # A zero byte stays zero, and a byte equal to its key hides that key itself.
        plain = bytes(
            cipher
            if not size or cipher in (0, log_key[index % size])
            else cipher ^ log_key[index % size]
            for index, cipher in enumerate(log_data)
        )
        return int.from_bytes(plain, "big", signed=True)

    def _verify(self, transaction: dict, statement: dict) -> None:
        decoded = self.decode(statement["log_data"], transaction["log_key"])
        if decoded == statement["ec_score"]:
            return
        if self._settings()["hunt_score_mismatch"] == REJECT:
            raise ApiRejected(INVALID_STATEMENT)
        LOGGER.warning(
            "Hunt score %s differs from its log %s", statement["ec_score"], decoded
        )

    def _crossed(self, table_id: int, before: int, after: int) -> list[dict]:
        return [
            order
            for node in self.tracks.crossed(table_id, before, after)
            for order in node["rewards"]
        ]

    def settle(
        self, state: dict, record: dict, transaction: dict, statement: dict, now: int
    ) -> dict:
        quest = transaction["quest"]
        category = self.category(quest["id"])
        self._verify(transaction, statement)
        if self._aggregating(category, now):
            return {"is_aggregating": True}
        score = self.score(quest, statement)
        best, total = record["wh_best_score"], record["wh_total_score"]
        record.update(wh_best_score=max(best, score), wh_total_score=total + score)
        high = self._crossed(
            category["hunt_track_rewards_id"], best, record["wh_best_score"]
        )
        if not category["is_hunt_event"]:
            self.resources.grant(state, high, now, policy="mail")
            return {"wild_hunt_rewards": high} if high else {}
        groups = {
            "wild_hunt_best_rewards": high,
            "wild_hunt_total_rewards": self._crossed(
                category["hunt_track_total_rewards_id"], total, record["wh_total_score"]
            ),
        }
        source = f"{SOURCE}:{category['id']}"
        report = {"is_aggregating": False}
        for name, orders in groups.items():
            if not orders:
                continue
            # The client announces event score rewards as sent to the present box.
            self.resources.check(orders)
            for order in orders:
                self.mailbox.post(state, dict(order), source, now)
            report[name] = orders
        return report
