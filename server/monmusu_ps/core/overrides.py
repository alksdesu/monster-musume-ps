from copy import deepcopy
import logging
import re

from .layout import Layouts
from .masterdata import exact, moment


LOGGER = logging.getLogger("monmusu.overrides")
TABLES = "tables"
EMPTY = {TABLES: {}}
PATH = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\[\d+\])*")
PART = re.compile(r"[A-Za-z_]\w*|\[(\d+)\]")
INT32 = range(-(2**31), 2**31)
INT64 = range(-(2**63), 2**63)
LIMITS = {
    "int": INT32,
    "enum": INT32,
    "uint": range(2**32),
    "long": INT64,
    "bool": range(2),
}
SCALARS = (int, float, str)
EACH = "[]"
SHOWN_PROBLEMS = 5


def parts(path: str) -> list:
    if not isinstance(path, str) or not PATH.fullmatch(path):
        raise ValueError(f"not a field path: {path!r}")
    return [
        int(part.group(1)) if part.group(1) else part.group(0)
        for part in PART.finditer(path)
    ]


def coerced(current, value, kind: str | None = None):
    if type(current) is int:
        # Where the client's own type is unknown, a number already wider stays wide.
        limit = LIMITS.get(kind) or (INT32 if current in INT32 else INT64)
        if type(value) is not int or value not in limit:
            raise ValueError("has to stay a whole number the game can hold")
        return value
    if type(current) is float:
        if type(value) not in (int, float):
            raise ValueError("has to stay a number")
        return float(value)
    if type(current) is str:
        if type(value) is not str:
            raise ValueError("has to stay text")
        return value
    raise ValueError("is not a single value")


def texts(value, trail: tuple = ()):
    if isinstance(value, dict):
        for name, inner in value.items():
            yield from texts(inner, (*trail, name))
    elif isinstance(value, list):
        for inner in value:
            yield from texts(inner, (*trail, EACH))
    elif type(value) is str:
        yield trail, value


def dated(rows: list[dict]) -> dict[tuple, bool]:
    dates, blank, plain = set(), set(), set()
    for row in rows:
        for trail, text in texts(row):
            if trail in plain:
                continue
            if not text:
                blank.add(trail)
                continue
            try:
                moment(text)
            except ValueError:
                plain.add(trail)
            else:
                dates.add(trail)
    return {trail: trail in blank for trail in dates - plain}


def timed(value: str, blank: bool) -> None:
    if value == "" and blank:
        return
    # A date the game cannot parse stalls its loading screen.
    if not exact(value):
        raise ValueError("has to be a date written like 2026/01/31 05:00:00")


def slot(row: dict, steps: list) -> tuple:
    *way, last = steps
    holder = row
    try:
        for step in way:
            holder = holder[step]
        holder[last]
    except (KeyError, IndexError, TypeError):
        raise ValueError("is not in this row") from None
    return holder, last


class Overrides:
    def __init__(
        self,
        masters,
        config,
        locked: dict[str, str] | None = None,
        client=None,
        guarded: dict[str, frozenset[str]] | None = None,
    ):
        self.masters = masters
        self.config = config
        self.locked = locked or {}
        self.client = client
        self.guarded = guarded or {}
        self.layouts = Layouts()
        self.error: str | None = None
        self._source: dict | None = None
        self._entries: dict[str, list] = {}
        self._patched: dict[str, tuple[dict, list, dict]] = {}
        self._problems: dict[str, list[str]] = {}
        self._dated: dict[str, tuple[dict, dict[tuple, bool]]] = {}

    def _document(self) -> dict:
        try:
            return self.config.read()
        except FileNotFoundError:
            return EMPTY

    @staticmethod
    def _shaped(document) -> dict[str, list]:
        tables = document.get(TABLES, {}) if isinstance(document, dict) else None
        if not isinstance(tables, dict):
            raise ValueError("Content overrides need a tables object")
        for name, entries in tables.items():
            well_formed = isinstance(entries, list) and all(
                isinstance(entry, dict)
                and isinstance(entry.get("key"), dict)
                and isinstance(entry.get("fields"), dict)
                for entry in entries
            )
            if not well_formed:
                raise ValueError(f"{name} needs a list of key and fields entries")
        return tables

    def reload(self) -> None:
        document = self._document()
        if document is self._source:
            return
        self._source = document
        try:
            self._entries = self._shaped(document)
            self.error = None
        except ValueError as error:
            # A broken edit keeps the overrides that were last known to work.
            LOGGER.error(
                "Content overrides rejected, keeping the previous ones: %s", error
            )
            self.error = str(error)

    def _patch(self, name: str, base: dict, entries: list, problems: list[str]) -> dict:
        if name in self.locked:
            problems.append(f"{name} is read-only: {self.locked[name]}")
            return base
        try:
            layout = self.layouts.of(name, base)
        except ValueError as error:
            problems.append(str(error))
            return base
        document = deepcopy(base)
        index = {
            layout.key(row, position): row
            for position, row in enumerate(layout.rows(document))
        }
        written = set()
        for entry in entries:
            key = entry["key"]
            named = set(key) == set(layout.keys)
            row = (
                index.get(tuple(key[field] for field in layout.keys)) if named else None
            )
            if row is None:
                problems.append(f"{name} has no row {key}")
                continue
            for path, value in entry["fields"].items():
                where = f"{name} {key} {path}"
                try:
                    steps = parts(path)
                    if steps[0] in layout.keys:
                        raise ValueError("names the row and cannot change")
                    if steps[0] in self.guarded.get(name, ()):
                        raise ValueError("is battle data and stays as shipped")
                    if (id(row), path) in written:
                        raise ValueError("is set twice")
                    holder, last = slot(row, steps)
                    kind = self._kind(name, layout, steps, holder[last])
                    value = coerced(holder[last], value, kind)
                    trail = tuple(EACH if type(step) is int else step for step in steps)
                    blank = self._dates(name, base, layout).get(trail)
                    if blank is not None:
                        timed(value, blank)
                    holder[last] = value
                    written.add((id(row), path))
                except ValueError as error:
                    problems.append(f"{where} {error}")
        return document

    def _dates(self, name: str, base: dict, layout) -> dict[tuple, bool]:
        cached = self._dated.get(name)
        if cached is None or cached[0] is not base:
            self._dated[name] = base, dated(layout.rows(base))
        return self._dated[name][1]

    def _kind(self, name: str, layout, steps: list, current) -> str | None:
        if self.client is None or type(current) not in SCALARS:
            return None
        try:
            return self.client.leaf(name, layout.container, steps)["type"]
        except (KeyError, ValueError):
            raise ValueError(
                "is kept by the game in a form that cannot be rewritten"
            ) from None

    def apply(self, name: str, base: dict) -> dict:
        # Master tables are read far too often to check the file on every read.
        if self._source is None:
            self.reload()
        entries = self._entries.get(name)
        if not entries:
            return base
        cached = self._patched.get(name)
        if cached is None or cached[0] is not base or cached[1] is not entries:
            problems: list[str] = []
            document = self._patch(name, base, entries, problems)
            for problem in problems:
                LOGGER.warning("Content override skipped: %s", problem)
            self._problems[name] = problems
            self._patched[name] = base, entries, document
        return self._patched[name][2]

    def problems(self) -> list[str]:
        return [
            problem
            for name in self._entries
            for problem in self._problems.get(name, [])
        ]

    def report(self) -> str | None:
        problems = "; ".join(self.problems()[:SHOWN_PROBLEMS])
        return self.config.error or self.error or problems or None

    def check(self, document) -> dict[str, int]:
        problems: list[str] = []
        tables = self._shaped(document)
        for name, entries in tables.items():
            try:
                base = self.masters.base(name)
            except FileNotFoundError:
                problems.append(f"{name} is not a master table")
                continue
            self._patch(name, base, entries, problems)
        if problems:
            more = len(problems) - SHOWN_PROBLEMS
            shown = "; ".join(problems[:SHOWN_PROBLEMS])
            raise ValueError(f"{shown}; and {more} more" if more > 0 else shown)
        return {name: len(entries) for name, entries in tables.items()}

    def touched(self) -> list[str]:
        if self._source is None:
            self.reload()
        return sorted(name for name, entries in self._entries.items() if entries)
