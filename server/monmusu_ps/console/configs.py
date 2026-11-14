import json
import os
from pathlib import Path
import shutil
import time

from ..access import child


SCHEDULE = "schedule.json"
OVERRIDES = "overrides.json"
FILES = (
    "settings.json",
    SCHEDULE,
    OVERRIDES,
    "box_gacha.json",
    "social_roster.json",
)
SHARED = ("platform.json", "social.json", "data_links.json", "credentials.json")
EMPTY = "{}"
NUMBERS = (int, float)
TYPE, MISSING = "type", "missing"


class MissingKeys(Exception):
    def __init__(self, keys: list[str]):
        super().__init__(f"Keys would be removed: {', '.join(keys)}")
        self.keys = keys


def write_text(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(text.encode("utf-8"))
    os.replace(temporary, path)


def json_kind(value) -> str:
    return "number" if type(value) in NUMBERS else type(value).__name__


def compare(current, proposed, path: str = "$"):
    # null stands for "not set", so either side may hold anything in its place.
    if current is None or proposed is None:
        return
    if json_kind(current) != json_kind(proposed):
        yield TYPE, f"{path} has to stay a {json_kind(current)}"
    elif isinstance(current, dict):
        for key, value in current.items():
            if key in proposed:
                yield from compare(value, proposed[key], f"{path}.{key}")
            else:
                yield MISSING, f"{path}.{key}"
    elif isinstance(current, list) and current and isinstance(current[0], dict):
        for index, row in enumerate(proposed):
            yield from compare(current[0], row, f"{path}[{index}]")


class JsonFiles:
    def __init__(
        self,
        directory: Path,
        history: Path,
        names: tuple[str, ...],
        *,
        validators: dict | None = None,
        errors: dict | None = None,
        applied: dict | None = None,
    ):
        self.directory = directory
        self.history = history
        self.names = names
        self._validators = validators or {}
        self._errors = errors or {}
        self._applied = applied or {}

    def _path(self, name: str) -> Path:
        if name not in self.names:
            raise KeyError(f"Unknown file: {name}")
        return self.directory / name

    @staticmethod
    def _text(path: Path) -> str:
        # The server writes these the first time it needs them.
        return path.read_text(encoding="utf-8") if path.exists() else EMPTY

    def versions(self, name: str) -> list[dict]:
        folder = self.history / self._path(name).name
        rows = [
            {
                "version": path.name,
                "modified": int(path.stat().st_mtime),
                "size": path.stat().st_size,
            }
            for path in folder.glob("*.json")
        ]
        return sorted(rows, key=lambda row: row["version"], reverse=True)

    def read(self, name: str) -> dict:
        return {
            "name": name,
            "text": self._text(self._path(name)),
            "error": self._errors.get(name, lambda: None)(),
            "versions": self.versions(name),
        }

    def version(self, name: str, version: str) -> str:
        folder = self.history / self._path(name).name
        return child(folder, version).read_text(encoding="utf-8")

    def _check(self, name: str, document, allow_missing: bool) -> None:
        if name in self._validators:
            self._validators[name](document)
            return
        problems = list(compare(json.loads(self._text(self._path(name))), document))
        mismatched = [text for kind, text in problems if kind == TYPE]
        if mismatched:
            raise ValueError("; ".join(mismatched))
        missing = [text for kind, text in problems if kind == MISSING]
        if missing and not allow_missing:
            raise MissingKeys(missing)

    def _keep(self, path: Path) -> None:
        if not path.exists():
            return
        folder = self.history / path.name
        folder.mkdir(parents=True, exist_ok=True)
        stem, attempt = time.strftime("%Y%m%d-%H%M%S"), 1
        target = folder / f"{stem}.json"
        while target.exists():
            attempt += 1
            target = folder / f"{stem}.{attempt}.json"
        shutil.copyfile(path, target)

    def write(self, name: str, text: str, allow_missing: bool = False) -> None:
        path = self._path(name)
        self._check(name, json.loads(text), allow_missing)
        self._keep(path)
        write_text(path, text if text.endswith("\n") else f"{text}\n")
        self._applied.get(name, lambda: None)()

    def restore(self, name: str, version: str) -> None:
        self.write(name, self.version(name, version), allow_missing=True)
