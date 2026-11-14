import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .markup import JAPANESE


TABLES = "data"
EACH = "[]"
# The game sorts names by their readings without showing them.
READING = re.compile(r"(^|[:.\]])kana$")
# The game compares these with what it expects or looks things up by them, as they are written.
KEYS = frozenset(
    {
        "ability_data:table[].talentList[].activeData[].option[]",
        "ability_data:table[].talentList[].triggerData[].option[]",
        "beast_gods_blessing_table:table[].blessing_type",
        "bonus_pass_data:table[].unlock_item_name",
        "detail_text_data:table[].id",
        "gear_rarity:table[].name",
        "help_window_data:table[].helpId",
        "mission_data:table[].target",
        "shop_data:table[].sales_conditions",
    }
)
# Items and wallets are told apart by the name on their icon; these are compared with as written.
KEPT = {
    "wallet_template:table[].wallet_name": frozenset(
        {"DMMポイント", "フレンドポイント"}
    ),
    "item_template:table[].display_name": frozenset({"フレンドポイント"}),
    "message_data:table[].message": frozenset({"DMMポイント"}),
}


@dataclass(frozen=True)
class Entry:
    text: str
    origins: tuple[str, ...]

    @property
    def origin(self) -> str:
        return self.origins[0]


def group(path: Path, root: Path) -> str:
    # Each unit, enemy and token has a file of its own; they are told apart by folder.
    folder = path.relative_to(root).parts[0]
    return path.stem if folder == TABLES else folder


def origin(name: str, trail: tuple[str, ...]) -> str:
    steps = "".join(step if step == EACH else f".{step}" for step in trail)
    return f"{name}:{steps.lstrip('.')}"


def shown(place: str, text: str | None = None) -> bool:
    if place in KEYS or READING.search(place):
        return False
    return text not in KEPT.get(place, ())


def strings(node, trail: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], str]]:
    if isinstance(node, str):
        if JAPANESE.search(node):
            yield trail, node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from strings(value, (*trail, key))
    elif isinstance(node, list):
        for value in node:
            yield from strings(value, (*trail, EACH))


def collect(root: Path, elsewhere: Iterable[tuple[str, str]] = ()) -> list[Entry]:
    found: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.json")):
        name = group(path, root)
        document = json.loads(path.read_text(encoding="utf-8"))
        for trail, text in strings(document):
            place = origin(name, trail)
            if shown(place):
                found.setdefault(text, set()).add(place)
    for place, text in elsewhere:
        found.setdefault(text, set()).add(place)
    entries = (Entry(text, tuple(sorted(places))) for text, places in found.items())
    return sorted(entries, key=lambda entry: (entry.origin, entry.text))


def save(entries: Iterable[Entry], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for entry in entries:
            line = {"text": entry.text, "origins": list(entry.origins)}
            file.write(json.dumps(line, ensure_ascii=False) + "\n")


def load(path: Path) -> list[Entry]:
    with path.open(encoding="utf-8") as file:
        lines = (json.loads(line) for line in file if line.strip())
        return [Entry(line["text"], tuple(line["origins"])) for line in lines]
