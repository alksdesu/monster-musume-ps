import hashlib

from .calendar import FOREVER
from .cards import text


class News:
    def __init__(self, masters, config):
        self.masters = masters
        self.config = config

    def _published(self, row: dict) -> dict:
        return {
            "id": str(row["id"]),
            "category": row["tag"],
            "priority": 0,
            "title": row["title"],
            "article": row["message"],
            "thumbnail": "",
            "image": "",
            "start_at": self.masters.timestamp(row["date"]),
            "end_at": FOREVER,
            "exdata": "",
        }

    def listing(self, now: int) -> tuple[list[dict], str]:
        rows = [
            *map(self._published, self.masters.table("news_data")),
            *self.config.read()["news"]["items"],
        ]
        running = sorted(
            (row for row in rows if row["start_at"] <= now < row["end_at"]),
            key=lambda row: (-row["priority"], -row["start_at"], row["id"]),
        )
        version = hashlib.sha256(text(running).encode()).hexdigest()[:16]
        return running, version
