import json

from monmusu_l10n.markup import JAPANESE

from ..core.layout import LOCKED, Layout
from ..core.overrides import TABLES, parts
from . import catalog
from .configs import OVERRIDES, json_kind


PAGE = 50
SCALARS = (int, float, str)


def value_at(row: dict, path: str):
    holder = row
    for step in parts(path):
        holder = holder[step]
    return holder


class Content:
    def __init__(self, runtime, files):
        self.runtime = runtime
        self.files = files

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

    def _spoken(self, row: dict) -> dict[str, str]:
        spoken = self.runtime.localization
        hints = {}
        for field, value in row.items():
            if type(value) is str and JAPANESE.search(value):
                said = spoken.text(value)
                if said != value:
                    hints[field] = said
        return hints

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

    def _matching(
        self,
        name: str,
        layout: Layout,
        rows: list,
        touched: dict,
        query: str,
        changed: bool,
    ) -> list[tuple]:
        needle = query.strip().lower()
        found = []
        for position, row in enumerate(rows):
            key = layout.key(row, position)
            if changed and key not in touched:
                continue
            shown = self._shown(name, row)
            if needle and not any(
                needle in str(value).lower()
                for value in shown.values()
                if type(value) in SCALARS
            ):
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
        found = self._matching(name, layout, current, touched, query, changed)
        return {
            "total": len(found),
            "key": list(layout.keys),
            "columns": self._columns(name, shipped[0]),
            "rows": [
                {
                    "key": dict(zip(layout.keys, key)),
                    "title": catalog.row_title(row),
                    "values": row,
                    "spoken": self._spoken(row),
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
        return {
            "key": key,
            "title": catalog.row_title(now),
            "columns": self._columns(name, kept),
            "shipped": kept,
            "current": now,
            "spoken": self._spoken(kept),
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
            name, layout, current, touched, query, changed
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
        listed = []
        for name, entries in self._document()[TABLES].items():
            try:
                layout, shipped, _ = self._open(name)
            except (KeyError, ValueError):
                layout, shipped = None, []
            index = {
                layout.key(row, position): row for position, row in enumerate(shipped)
            }
            for entry in entries:
                row = (
                    index.get(self._identity(layout, entry["key"])) if layout else None
                )
                for path, value in entry["fields"].items():
                    try:
                        before = value_at(row, path)
                    except (KeyError, IndexError, TypeError, ValueError):
                        before = None
                    listed.append(
                        {
                            "table": name,
                            "key": entry["key"],
                            "path": path,
                            "shipped": before,
                            "value": value,
                        }
                    )
        return listed
