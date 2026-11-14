import json

from . import catalog
from .configs import SCHEDULE
from .naming import Names


UPCOMING, RUNNING, ENDED = "upcoming", "running", "ended"
SAMPLE = 20


class Planner:
    def __init__(self, runtime, files, names: Names):
        self.runtime = runtime
        self.files = files
        self.names = names

    # An entry lists what it opens by id; the page shows each of them by its name.
    def _listed(self, document: dict) -> dict[str, dict[str, str]]:
        schedule, say = self.runtime.schedule, self.names.reading().say
        wanted: dict[str, set[str]] = {}
        for entry in document["activities"]:
            if entry["kind"] in schedule.kinds and "ids" in entry:
                wanted.setdefault(entry["kind"], set()).update(map(str, entry["ids"]))
        return {
            kind: {
                str(row["id"]): say(row["name"])
                for row in schedule.entrances(kind)
                if str(row["id"]) in ids
            }
            for kind, ids in wanted.items()
        }

    def _names(self, document: dict) -> dict[str, dict[str, str]]:
        try:
            return self._listed(document)
        except (AttributeError, KeyError, TypeError, ValueError):
            # A document the schedule turned down is still shown, so that it can be put right.
            return {}

    def document(self) -> dict:
        schedule = self.runtime.schedule
        payload = schedule.payload()
        document = schedule.config.read()
        return {
            "document": document,
            "kinds": schedule.kinds,
            "error": schedule.config.error or schedule.error,
            "revision": payload["revision"],
            "tables": {
                table["asset"]: len(table["rows"]) for table in payload["tables"]
            },
            "names": self._names(document),
        }

    def preview(self, entry: dict) -> dict:
        schedule = self.runtime.schedule
        changes = schedule.preview(
            {"activities": [{**entry, "enabled": True}], "rows": []}
        )
        named = self.names.reading()
        return {
            "tables": [
                {"name": name, "label": catalog.label(name), "rows": len(rows)}
                for name, rows in changes.items()
            ],
            "sample": [
                {
                    "table": name,
                    "label": catalog.label(name),
                    "key": row["key"],
                    "title": named.title(name, schedule.row(name, row["key"])),
                    "fields": [
                        {
                            "name": field,
                            "label": catalog.field_label(field),
                            "value": value,
                        }
                        for field, value in row["fields"].items()
                    ],
                }
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
        say = self.names.reading().say
        current = {row["id"]: row for row in schedule.entrances(kind, scheduled=True)}
        needle = query.casefold()
        rows = []
        for shipped in schedule.entrances(kind):
            live = current[shipped["id"]]
            state = self._status(live, now)
            name = say(shipped["name"])
            # Found by its id, by the name the game ships or by the name it shows now.
            listed = f"{shipped['id']} {shipped['name']} {name}".casefold()
            if needle in listed and status in ("", state):
                rows.append(
                    {
                        "id": shipped["id"],
                        "name": name,
                        "shipped": {"start": shipped["start"], "end": shipped["end"]},
                        "current": {"start": live["start"], "end": live["end"]},
                        "status": state,
                        "moved": live != shipped,
                    }
                )
        return {"total": len(rows), "rows": rows[offset : offset + limit]}
