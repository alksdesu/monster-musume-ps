import json

from .configs import SCHEDULE


UPCOMING, RUNNING, ENDED = "upcoming", "running", "ended"
SAMPLE = 20


class Planner:
    def __init__(self, runtime, files):
        self.runtime = runtime
        self.files = files

    def document(self) -> dict:
        schedule = self.runtime.schedule
        payload = schedule.payload()
        return {
            "document": schedule.config.read(),
            "kinds": schedule.kinds,
            "error": schedule.config.error or schedule.error,
            "revision": payload["revision"],
            "tables": {
                table["asset"]: len(table["rows"]) for table in payload["tables"]
            },
        }

    def preview(self, entry: dict) -> dict:
        changes = self.runtime.schedule.preview(
            {"activities": [{**entry, "enabled": True}], "rows": []}
        )
        return {
            "tables": {name: len(rows) for name, rows in changes.items()},
            "sample": [
                {"table": name, **row}
                for name, rows in changes.items()
                for row in rows[:SAMPLE]
            ],
        }

    def save(self, document: dict) -> dict:
        text = json.dumps(document, ensure_ascii=False, indent=2)
        self.files.write(SCHEDULE, text)
        self.runtime.schedule.reload()
        return self.document()

    def _status(self, window: dict, now: int) -> str:
        moment = self.runtime.masters.timestamp
        if window["start"] and now < moment(window["start"]):
            return UPCOMING
        if window["end"] and moment(window["end"]) <= now:
            return ENDED
        return RUNNING

    def catalog(
        self, kind: str, query: str, status: str, offset: int, limit: int
    ) -> dict:
        schedule = self.runtime.schedule
        now = int(self.runtime.clock())
        current = {row["id"]: row for row in schedule.entrances(kind, scheduled=True)}
        needle = query.casefold()
        rows = []
        for shipped in schedule.entrances(kind):
            live = current[shipped["id"]]
            state = self._status(live, now)
            listed = f"{shipped['id']} {shipped['name']}".casefold()
            if needle in listed and status in ("", state):
                rows.append(
                    {
                        "id": shipped["id"],
                        "name": shipped["name"],
                        "shipped": {"start": shipped["start"], "end": shipped["end"]},
                        "current": {"start": live["start"], "end": live["end"]},
                        "status": state,
                        "moved": live != shipped,
                    }
                )
        return {"total": len(rows), "rows": rows[offset : offset + limit]}
