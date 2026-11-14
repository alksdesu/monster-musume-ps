import json

from ..core.layout import LOCKED, Layout
from ..core.overrides import TABLES, parts
from . import catalog
from .configs import OVERRIDES, json_kind
from .naming import Names, Reading


PAGE = 50
SCALARS = (int, float, str)


def value_at(row: dict, path: str):
    holder = row
    for step in parts(path):
        holder = holder[step]
    return holder


def joined(parent: str, step) -> str:
    if type(step) is int:
        return f"{parent}[{step}]"
    return f"{parent}.{step}" if parent else step


# The entries of a list are all read alike, so the way to a field leaves their positions out.
def onward(way: str, step) -> str:
    if type(step) is int:
        return way
    return f"{way}.{step}" if way else step


# Each value the game shows under another name, by the path that leads to it.
def hints(
    table: str, node, named: Reading, deep: bool = True, parent: str = "", way: str = ""
):
    for step in node if isinstance(node, dict) else range(len(node)):
        value, path, inner = node[step], joined(parent, step), onward(way, step)
        if isinstance(value, dict | list):
            if deep:
                yield from hints(table, value, named, deep, path, inner)
        else:
            said = named.said(table, inner, node, step)
            if said:
                yield path, said


def hint_at(table: str, row: dict | None, path: str, named: Reading) -> str:
    try:
        holder, way, *steps = row, "", *parts(path)
        for step in steps[:-1]:
            holder, way = holder[step], onward(way, step)
        return named.said(table, onward(way, steps[-1]), holder, steps[-1])
    except (KeyError, IndexError, TypeError, ValueError):
        return ""


class Content:
    def __init__(self, runtime, files, names: Names):
        self.runtime = runtime
        self.files = files
        self.names = names

    def _open(self, name: str) -> tuple[Layout, list[dict], list[dict]]:
        runtime = self.runtime
        # Battle data stays as shipped, so the console does not offer it at all.
        if name in LOCKED:
            raise KeyError(f"Unknown table: {name}")
        try:
            shipped = runtime.masters.base(name)
        except FileNotFoundError:
            raise KeyError(f"Unknown table: {name}") from None
        layout = runtime.overrides.layouts.of(name, shipped)
        current = runtime.masters.document(name, before=runtime.schedule)
        return layout, layout.rows(shipped), layout.rows(current)

    def _shown(self, name: str, row: dict) -> dict:
        # Battle values inside an operations table stay as shipped and are left out.
        hidden = self.runtime.overrides.guarded.get(name)
        if not hidden:
            return row
        return {field: value for field, value in row.items() if field not in hidden}

    def _document(self) -> dict:
        document = json.loads(self.files.read(OVERRIDES)["text"])
        document.setdefault(TABLES, {})
        return document

    def _write(self, document: dict) -> None:
        text = json.dumps(document, ensure_ascii=False, indent=2)
        self.files.write(OVERRIDES, text, allow_missing=True)
        self.runtime.overrides.reload()

    @staticmethod
    def _identity(layout: Layout, key) -> tuple | None:
        if not isinstance(key, dict) or set(key) != set(layout.keys):
            return None
        return tuple(key[field] for field in layout.keys)

    def _position(self, layout: Layout, rows: list[dict], key) -> int:
        wanted = self._identity(layout, key)
        for position, row in enumerate(rows):
            if layout.key(row, position) == wanted:
                return position
        raise KeyError(f"No row {key}")

    def _columns(self, name: str, sample: dict) -> list[dict]:
        return [
            {
                "name": field,
                "label": catalog.field_label(field),
                "kind": catalog.kind(field, value),
            }
            for field, value in self._shown(name, sample).items()
        ]

    @staticmethod
    def groups() -> list[dict]:
        return [group.__dict__ for group in catalog.GROUPS]

    # Fields nested inside a row have no column to carry their label, so the page is handed them all.
    @staticmethod
    def fields() -> dict[str, str]:
        return catalog.FIELDS

    def tables(self) -> list[dict]:
        changed = self._document()[TABLES]
        listed = []
        for path in sorted(self.runtime.masters.data_root.glob("*.json")):
            name = path.stem
            try:
                layout, shipped, _ = self._open(name)
            except (KeyError, ValueError):
                continue
            group, label = catalog.grouped(name)
            listed.append(
                {
                    "name": name,
                    "label": label,
                    "group": group,
                    "order": catalog.order(name),
                    "rows": len(shipped),
                    "fields": len(self._shown(name, shipped[0])),
                    "key": list(layout.keys),
                    "changed": len(changed.get(name, [])),
                }
            )
        return listed

    def _touched(self, layout: Layout, name: str) -> dict:
        return {
            self._identity(layout, entry["key"]): entry["fields"]
            for entry in self._document()[TABLES].get(name, [])
        }

    # A row is found by what it holds or by what the page shows beside it.
    @staticmethod
    def _holds(name: str, shown: dict, needle: str, named: Reading) -> bool:
        return any(
            needle in str(value).lower()
            for value in shown.values()
            if type(value) in SCALARS
        ) or any(
            needle in said.lower() for _, said in hints(name, shown, named, deep=False)
        )

    def _matching(
        self,
        name: str,
        layout: Layout,
        rows: list,
        touched: dict,
        query: str,
        changed: bool,
        named: Reading,
    ) -> list[tuple]:
        needle = query.strip().lower()
        found = []
        for position, row in enumerate(rows):
            key = layout.key(row, position)
            if changed and key not in touched:
                continue
            shown = self._shown(name, row)
            if needle and not self._holds(name, shown, needle, named):
                continue
            found.append((position, key, shown))
        return found

    def rows(
        self,
        name: str,
        query: str = "",
        offset: int = 0,
        limit: int = PAGE,
        changed: bool = False,
    ) -> dict:
        layout, shipped, current = self._open(name)
        touched = self._touched(layout, name)
        named = self.names.reading()
        found = self._matching(name, layout, current, touched, query, changed, named)
        return {
            "total": len(found),
            "key": list(layout.keys),
            "columns": self._columns(name, shipped[0]),
            "rows": [
                {
                    "key": dict(zip(layout.keys, key)),
                    "title": named.title(name, row),
                    "values": row,
                    # The list shows a row's own fields only, so what is nested is left for the row's page.
                    "hints": dict(hints(name, row, named, deep=False)),
                    "changed": sorted(touched.get(key, {})),
                }
                for _, key, row in found[offset : offset + limit]
            ],
        }

    def row(self, name: str, key) -> dict:
        layout, shipped, current = self._open(name)
        position = self._position(layout, shipped, key)
        entries = self._document()[TABLES].get(name, [])
        fields = next((entry["fields"] for entry in entries if entry["key"] == key), {})
        kept, now = (
            self._shown(name, shipped[position]),
            self._shown(name, current[position]),
        )
        named = self.names.reading()
        return {
            "key": key,
            "title": named.title(name, now),
            "columns": self._columns(name, kept),
            "shipped": kept,
            "current": now,
            "hints": dict(hints(name, kept, named)),
            "changed": sorted(fields),
        }

    def edit(self, name: str, key, fields: dict) -> dict:
        layout, shipped, _ = self._open(name)
        row = shipped[self._position(layout, shipped, key)]
        if not isinstance(fields, dict) or not fields:
            raise ValueError("Nothing to change")
        document = self._document()
        entries = document[TABLES].setdefault(name, [])
        entry = next((entry for entry in entries if entry["key"] == key), None)
        if entry is None:
            entry = {"key": key, "fields": {}}
            entries.append(entry)
        for path, value in fields.items():
            try:
                same = value is None or value == value_at(row, path)
            except (KeyError, IndexError, TypeError):
                raise ValueError(f"{name} {key} {path} is not in this row") from None
            # A value put back to what the game ships is no longer an override.
            if same:
                entry["fields"].pop(path, None)
            else:
                entry["fields"][path] = value
        self._pruned(document, name)
        self._write(document)
        return self.row(name, key)

    # One value written into every matching row, so a whole table is changed in one go.
    def bulk(self, name: str, query: str, changed: bool, path: str, value) -> dict:
        layout, shipped, current = self._open(name)
        head = parts(path)[0] if parts(path) else None
        if head in layout.keys:
            raise ValueError("A field that finds the row cannot be changed")
        if head not in self._shown(name, shipped[0]):
            raise ValueError(f"{name} has no field {path}")
        document = self._document()
        entries = document[TABLES].setdefault(name, [])
        by_key = {self._identity(layout, entry["key"]): entry for entry in entries}
        touched = {key: entry["fields"] for key, entry in by_key.items()}
        written = 0
        for position, key, _ in self._matching(
            name, layout, current, touched, query, changed, self.names.reading()
        ):
            row = shipped[position]
            try:
                before = value_at(row, path)
            except (KeyError, IndexError, TypeError):
                raise ValueError(
                    f"{name} {dict(zip(layout.keys, key))} has no {path}"
                ) from None
            if json_kind(before) != json_kind(value):
                raise ValueError(
                    f"{path} holds {json_kind(before)}, not {json_kind(value)}"
                )
            entry = by_key.get(key)
            if entry is None:
                entry = by_key[key] = {"key": dict(zip(layout.keys, key)), "fields": {}}
                entries.append(entry)
            if value == before:
                entry["fields"].pop(path, None)
            else:
                entry["fields"][path] = value
            written += 1
        self._pruned(document, name)
        self._write(document)
        return {"rows": written}

    @staticmethod
    def _pruned(document: dict, name: str) -> None:
        kept = [entry for entry in document[TABLES][name] if entry["fields"]]
        if kept:
            document[TABLES][name] = kept
        else:
            del document[TABLES][name]

    def reset(self, name: str, key=None) -> int:
        document = self._document()
        entries = document[TABLES].get(name, [])
        dropped = [entry for entry in entries if key is None or entry["key"] == key]
        if not dropped:
            return 0
        for entry in dropped:
            entry["fields"] = {}
        self._pruned(document, name)
        self._write(document)
        return len(dropped)

    def changes(self) -> list[dict]:
        named = self.names.reading()
        listed = []
        for name, entries in self._document()[TABLES].items():
            try:
                layout, shipped, current = self._open(name)
            except (KeyError, ValueError):
                layout, shipped, current = None, [], []
            index = {
                layout.key(row, position): position
                for position, row in enumerate(shipped)
            }
            for entry in entries:
                position = (
                    index.get(self._identity(layout, entry["key"])) if layout else None
                )
                before, after = (
                    (None, None)
                    if position is None
                    else (shipped[position], current[position])
                )
                for path, value in entry["fields"].items():
                    try:
                        kept = value_at(before, path)
                    except (KeyError, IndexError, TypeError, ValueError):
                        kept = None
                    listed.append(
                        {
                            "table": name,
                            "key": entry["key"],
                            "title": named.title(name, after or {}),
                            "path": path,
                            "shipped": kept,
                            "value": value,
                            "hints": {
                                "shipped": hint_at(name, before, path, named),
                                "value": hint_at(name, after, path, named),
                            },
                        }
                    )
        return listed
