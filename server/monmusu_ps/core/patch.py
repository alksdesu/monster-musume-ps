import hashlib
import json
import logging
from operator import is_

from .layout import INDEX
from .localization import IDENTITY


LOGGER = logging.getLogger("monmusu.patch")


def differences(shipped, current, path: tuple = ()):
    if isinstance(shipped, dict):
        for name, value in shipped.items():
            yield from differences(value, current[name], (*path, name))
    elif isinstance(shipped, list):
        for position, value in enumerate(shipped):
            yield from differences(value, current[position], (*path, position))
    elif shipped != current:
        yield list(path), current


class ClientPatch:
    def __init__(self, masters, overrides, schedule, client, localization):
        self.masters = masters
        self.overrides = overrides
        self.schedule = schedule
        self.client = client
        self.localization = localization
        self._tables: dict[tuple[str, bool], tuple[dict, dict, dict | None]] = {}
        self._folders: dict[str, tuple[list, dict | None]] = {}
        self._payloads: dict[bool, tuple[list, dict, bytes]] = {}

    def _values(
        self, name: str, container: tuple, before: dict, after: dict
    ) -> list[dict]:
        values = []
        for path, value in differences(before, after):
            try:
                leaf = self.client.leaf(name, container, path)
            except (KeyError, ValueError) as error:
                # The server still uses the value; the game keeps showing its own.
                LOGGER.warning("%s %s stays on the server: %s", name, path, error)
                continue
            values.append({"path": path, "value": value, "forget": leaf["forget"]})
        return values

    def _changes(self, name: str, layout, shipped: dict, current: dict) -> list[dict]:
        changes = []
        pairs = zip(layout.rows(shipped), layout.rows(current))
        for position, (before, after) in enumerate(pairs):
            values = self._values(name, layout.container, before, after)
            if values:
                changes.append(
                    {"at": list(layout.key(before, position)), "set": values}
                )
        return changes

    def _table(self, name: str, dated: bool) -> dict | None:
        shipped = self.masters.base(name)
        below = None if dated else self.schedule
        # The server goes on reading Japanese; only what the game is handed is translated.
        current = self.localization.apply(
            name, self.masters.document(name, before=below)
        )
        if current is shipped:
            return None
        cached = self._tables.get((name, dated))
        if cached is None or cached[0] is not shipped or cached[1] is not current:
            described = self._described(name, shipped, current)
            self._tables[name, dated] = shipped, current, described
        return self._tables[name, dated][2]

    def _described(self, name: str, shipped: dict, current: dict) -> dict | None:
        try:
            late = self.client.late(name)
            layout = self.overrides.layouts.of(name, shipped)
        except ValueError as error:
            # The game is done with such a table before anything can reach it, or holds it in no rows.
            LOGGER.warning("%s stays as shipped: %s", name, error)
            return None
        changes = self._changes(name, layout, shipped, current)
        if not changes:
            return None
        return {
            "asset": self.client.asset(name),
            "rows": list(layout.container),
            "key": [] if layout.keys == (INDEX,) else list(layout.keys),
            "count": len(layout.rows(shipped)),
            "late": late,
            "forget": self.client.indexes(name),
            "changes": changes,
        }

    def _folder(self, group: str) -> dict | None:
        pairs = self.localization.objects(group)
        cached = self._folders.get(group)
        if cached is None or cached[0] is not pairs:
            name = self.client.named(group)
            changes = [
                {"at": [before[IDENTITY]], "set": values}
                for before, after in pairs
                if after is not before
                and (values := self._values(name, (), before, after))
            ]
            described = {
                "folder": self.client.folder(group),
                "key": [IDENTITY],
                "count": len(pairs),
                "late": self.client.late(name),
                # A folder is a bare list; what the game works out from an object sits on the object.
                "forget": [],
                "changes": changes,
            }
            self._folders[group] = pairs, described if changes else None
        return self._folders[group][1]

    def _built(self, dated: bool) -> tuple[dict, bytes]:
        self.overrides.reload()
        self.schedule.reload()
        touched = (self.overrides, self.schedule, self.localization)
        names = sorted({name for layer in touched for name in layer.touched()})
        tables = [table for name in names if (table := self._table(name, dated))]
        folders = self.localization.folders()
        tables += [table for group in folders if (table := self._folder(group))]
        kept = self._payloads.get(dated)
        # Tens of thousands of values are too many to write out for every request.
        if (
            kept is None
            or len(kept[0]) != len(tables)
            or not all(map(is_, kept[0], tables))
        ):
            digest = hashlib.sha256(json.dumps(tables, sort_keys=True).encode())
            payload = {"revision": digest.hexdigest()[:16], "tables": tables}
            body = json.dumps(payload, ensure_ascii=False).encode()
            self._payloads[dated] = kept = tables, payload, body
        return kept[1], kept[2]

    def payload(self, dated: bool = True) -> dict:
        return self._built(dated)[0]

    def encoded(self, dated: bool = True) -> bytes:
        return self._built(dated)[1]
