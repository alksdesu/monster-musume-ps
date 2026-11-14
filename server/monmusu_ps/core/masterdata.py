from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path


TIME_LAYOUTS = ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d")
FULL_LAYOUT = TIME_LAYOUTS[0]
LISTING = "ablist.json"


def bundle_file(bundle: str) -> str:
    # The asset listing names a bundle by the md5 of its lower-cased, dash-joined path.
    return hashlib.md5(bundle.lower().replace("/", "-").encode()).hexdigest() + ".bytes"


def moment(text: str) -> datetime:
    for layout in TIME_LAYOUTS:
        try:
            return datetime.strptime(text, layout)
        except ValueError:
            continue
    raise ValueError(f"Unreadable master time: {text!r}")


def exact(text: str) -> bool:
    # The client parses dates with its own culture; only the full layout is safe to hand it.
    try:
        return datetime.strptime(text, FULL_LAYOUT).strftime(FULL_LAYOUT) == text
    except ValueError:
        return False


class MasterData:
    def __init__(self, root: Path):
        self.root = root
        self.asset_root = root / "assets/resources/mdf_output"
        self.data_root = self.asset_root / "data"
        self._cache: dict[Path, tuple[tuple[int, int], dict]] = {}
        self._indexes: dict[tuple[str, str], tuple[list, dict]] = {}
        self._bundles: tuple[dict, frozenset[str]] | None = None
        self.overlays: list = []

    def base(self, name: str) -> dict:
        return self._read(self.data_root / f"{name}.json")

    def document(self, name: str, before=None) -> dict:
        document = self.base(name)
        for overlay in self.overlays:
            if overlay is before:
                break
            document = overlay.apply(name, document)
        return document

    def asset(self, group: str, identity: int) -> dict:
        return self._read(self.asset_root / group / f"{int(identity)}.json")

    # None when the asset listing was never downloaded, so nothing can be ruled out.
    def bundles(self) -> frozenset[str] | None:
        path = self.root / LISTING
        if not path.exists():
            return None
        listing = self._read(path)
        if self._bundles is None or self._bundles[0] is not listing:
            names = frozenset(row["path"] for row in listing["data"])
            self._bundles = (listing, names)
        return self._bundles[1]

    def has_bundle(self, bundle: str) -> bool:
        listed = self.bundles()
        return listed is None or bundle_file(bundle) in listed

    def assets(self, group: str) -> list[dict]:
        paths = (self.asset_root / group).glob("*.json")
        return [
            self._read(path) for path in sorted(paths, key=lambda path: int(path.stem))
        ]

    def _read(self, path: Path) -> dict:
        status = path.stat()
        stamp = (status.st_mtime_ns, status.st_size)
        cached = self._cache.get(path)
        if cached is None or cached[0] != stamp:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._cache[path] = (stamp, data)
        return self._cache[path][1]

    def table(self, name: str) -> list:
        return self.document(name)["table"]

    def lookup(self, table: str, key: str, value):
        rows = self.table(table)
        cached = self._indexes.get((table, key))
        if cached is None or cached[0] is not rows:
            index = {row[key]: row for row in rows}
            if len(index) != len(rows):
                raise ValueError(f"{table}.{key} contains duplicate keys")
            self._indexes[table, key] = rows, index
        return self._indexes[table, key][1][value]

    def property(self, kind: str, key: int):
        return next(
            row["m_value"]
            for row in self.document("property_sheet")[f"m_property{kind}"]
            if row["m_key"] == key
        )

    def zone(self) -> timezone:
        return timezone(timedelta(hours=self.property("Int", 3)))

    def timestamp(self, text: str) -> int:
        return int(moment(text).replace(tzinfo=self.zone()).timestamp())

    def in_term(self, start: str, end: str, now: int) -> bool:
        started = not start or self.timestamp(start) <= now
        return started and (not end or now < self.timestamp(end))
