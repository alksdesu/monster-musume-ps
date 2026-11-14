import base64
import json
from pathlib import Path
from urllib.parse import urlsplit

from .configs import json_kind
from ..access import child


REQUEST, RESPONSE = "req", "resp-b64"
PATTERN = "session_*.jsonl"
API = "/api/"
ABSENT = "missing"


def differences(left, right, path: str = "$"):
    if json_kind(left) != json_kind(right):
        yield {"path": path, "left": json_kind(left), "right": json_kind(right)}
    elif isinstance(left, dict):
        for key in sorted(left.keys() | right.keys()):
            inner = f"{path}.{key}"
            if key not in left:
                yield {"path": inner, "left": ABSENT, "right": json_kind(right[key])}
            elif key not in right:
                yield {"path": inner, "left": json_kind(left[key]), "right": ABSENT}
            else:
                yield from differences(left[key], right[key], inner)
    elif isinstance(left, list) and left and right:
        yield from differences(left[0], right[0], f"{path}[]")


class Captures:
    def __init__(self, directory: Path):
        self.directory = directory
        self._indexes: dict[str, tuple[tuple[int, int], list[dict]]] = {}

    def sessions(self) -> list[dict]:
        rows = [
            {
                "name": path.name,
                "size": path.stat().st_size,
                "modified": int(path.stat().st_mtime),
            }
            for path in self.directory.glob(PATTERN)
            if path.stat().st_size
        ]
        return sorted(rows, key=lambda row: row["name"], reverse=True)

    @staticmethod
    def _endpoint(url: str) -> str:
        path = urlsplit(url).path
        return path.split(API, 1)[1] if API in path else path

    def _index(self, name: str) -> list[dict]:
        path = child(self.directory, name)
        status = path.stat()
        stamp = (status.st_mtime_ns, status.st_size)
        cached = self._indexes.get(name)
        if cached is None or cached[0] != stamp:
            entries, offset = [], 0
            with path.open("rb") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        # A session still being recorded can end in half a line.
                        break
                    if row["kind"] == REQUEST:
                        entries.append(
                            {
                                "time": row["t"],
                                "endpoint": self._endpoint(row["data"]["url"]),
                                "request": offset,
                                "response": None,
                            }
                        )
                    elif row["kind"] == RESPONSE and entries:
                        # The client talks in turns, so an answer belongs to the last request.
                        if entries[-1]["response"] is None:
                            entries[-1]["response"] = offset
                    offset += len(line)
            self._indexes[name] = stamp, entries
        return self._indexes[name][1]

    def entries(self, name: str) -> list[dict]:
        return [
            {
                "index": index,
                "time": entry["time"],
                "endpoint": entry["endpoint"],
                "answered": entry["response"] is not None,
            }
            for index, entry in enumerate(self._index(name))
        ]

    @staticmethod
    def _parsed(text: str):
        try:
            return json.loads(text)
        except ValueError:
            return text

    def _row(self, name: str, offset: int) -> dict:
        with child(self.directory, name).open("rb") as stream:
            stream.seek(offset)
            return json.loads(stream.readline())["data"]

    def entry(self, name: str, index: int) -> dict:
        entry = self._index(name)[index]
        response = None
        if entry["response"] is not None:
            raw = base64.b64decode(self._row(name, entry["response"])["b64"])
            response = self._parsed(raw.decode("utf-8", "replace"))
        return {
            "time": entry["time"],
            "endpoint": entry["endpoint"],
            "request": self._parsed(self._row(name, entry["request"])["payload"]),
            "response": response,
        }

    def compare(self, left: dict, right: dict) -> list[dict]:
        first = self.entry(left["name"], left["index"])["response"]
        second = self.entry(right["name"], right["index"])["response"]
        return list(differences(first, second))
