from collections.abc import Callable, Sequence
from operator import is_
from pathlib import Path

from monmusu_l10n import catalog, scenes
from monmusu_l10n.markup import JAPANESE
from monmusu_l10n.memory import Memory


SECTION, LANGUAGE = "localization", "language"
WORKSPACE = "localization"
SCENES = "scenes.jsonl"
SHEET = "#"
# Each unit, token and enemy is a file of its own; the game shows their names and titles.
FOLDERS = ("unitcard", "bomcard", "enemydata")
IDENTITY, RESOURCE, NAME = "id", "resource", "charaName"


def sheet(origin: str) -> str:
    # A workbook's own sheet is named after it; any other carries its name behind a mark.
    place = origin.partition(":")[2]
    return place.partition(SHEET)[2] or place.rpartition("/")[2]


def staged(path: Path) -> dict[str, scenes.Scene]:
    return {sheet(scene.origin): scene for scene in scenes.load(path)}


def unchanged(kept: Sequence, items: Sequence) -> bool:
    return len(kept) == len(items) and all(map(is_, kept, items))


class Fresh[Loaded]:
    def __init__(self, load: Callable[[Path], Loaded]):
        self.load = load
        self._stamp: tuple | None = None
        self._value: Loaded | None = None

    def of(self, path: Path) -> Loaded | None:
        if not path.exists():
            return None
        status = path.stat()
        stamp = (path, status.st_mtime_ns, status.st_size)
        if stamp != self._stamp:
            self._stamp, self._value = stamp, self.load(path)
        return self._value


class Localization:
    def __init__(self, masters, config, root: Path):
        self.masters = masters
        self.config = config
        self.root = root
        self._kept = Fresh(Memory)
        self._staged = Fresh(staged)
        self._shown: dict[tuple[str, int], tuple[dict, Memory, dict]] = {}
        self._touched: tuple[Memory, list[dict], list[str]] | None = None
        self._objects: dict[
            str, tuple[Memory, list[dict], list[tuple[dict, dict]]]
        ] = {}

    @property
    def workspace(self) -> Path:
        return self.root / WORKSPACE

    def _memory(self) -> Memory | None:
        language = self.config.read().get(SECTION, {}).get(LANGUAGE)
        return self._kept.of(self.workspace / f"{language}.jsonl") if language else None

    # Talk is only read once a scene is played, long after the tables are handed over.
    def stories(self) -> dict[str, scenes.Scene]:
        return self._staged.of(self.workspace / SCENES) or {}

    def _rendered(self, memory: Memory, name: str, node, trail: tuple[str, ...]):
        if isinstance(node, str):
            if not JAPANESE.search(node):
                return node
            shown = catalog.shown(catalog.origin(name, trail), node)
            return memory.get(node) or node if shown else node
        if isinstance(node, dict):
            made = {
                key: self._rendered(memory, name, value, (*trail, key))
                for key, value in node.items()
            }
            same = all(made[key] is value for key, value in node.items())
            return node if same else made
        if isinstance(node, list):
            inner = (*trail, catalog.EACH)
            made = [self._rendered(memory, name, value, inner) for value in node]
            same = all(new is old for new, old in zip(made, node))
            return node if same else made
        return node

    def apply(self, name: str, base: dict) -> dict:
        memory = self._memory()
        if memory is None:
            return base
        cached = self._shown.get((name, id(base)))
        if cached is None or cached[0] is not base or cached[1] is not memory:
            # Rows keep their order and keys, so the game's rows are still found by them.
            rendered = self._rendered(memory, name, base, ())
            self._shown[name, id(base)] = base, memory, rendered
        return self._shown[name, id(base)][2]

    def touched(self) -> list[str]:
        memory = self._memory()
        if memory is None:
            return []
        names = sorted(path.stem for path in self.masters.data_root.glob("*.json"))
        bases = [self.masters.base(name) for name in names]
        kept = self._touched
        # Asked for at every request, while the answer changes only with a table or the translations.
        if not (kept and kept[0] is memory and unchanged(kept[1], bases)):
            found = [
                name
                for name, base in zip(names, bases)
                if self.apply(name, base) is not base
            ]
            self._touched = kept = memory, bases, found
        return kept[2]

    def objects(self, group: str) -> list[tuple[dict, dict]]:
        memory = self._memory()
        if memory is None:
            return []
        shipped = self.masters.assets(group)
        kept = self._objects.get(group)
        if not (kept and kept[0] is memory and unchanged(kept[1], shipped)):
            pairs = [(base, self.apply(group, base)) for base in shipped]
            self._objects[group] = kept = memory, shipped, pairs
        return kept[2]

    def folders(self) -> list[str]:
        return [
            group
            for group in FOLDERS
            if any(after is not before for before, after in self.objects(group))
        ]

    # A caller with many texts holds the memory itself rather than checking the file for each.
    def memory(self) -> Memory | None:
        return self._memory()

    def text(self, source: str | None) -> str | None:
        memory = self._memory()
        if memory is None or not source:
            return source
        return memory.get(source) or source

    # One look at the memory serves every text the caller goes on to ask about.
    def speaker(self) -> Callable[[str], str]:
        memory = self._memory()
        if memory is None:
            return lambda source: source
        return lambda source: memory.get(source) or source

    def names(self) -> dict[str, str]:
        memory = self._memory()
        if memory is None:
            return {}
        spoken = {
            line.speaker for scene in self.stories().values() for line in scene.lines
        }
        named = {name: memory.get(name) for name in sorted(spoken) if name}
        return {name: target for name, target in named.items() if target}

    # The story engine opens a unit's profile only when her card still carries the name the script says.
    def cards(self) -> dict[str, list[list]]:
        if self._memory() is None:
            return {}
        owners: dict[str, list[list]] = {}
        for card in self.masters.assets(FOLDERS[0]):
            owners.setdefault(str(card[RESOURCE]), []).append(
                [card[IDENTITY], card[NAME]]
            )
        return owners

    def scene(self, name: str) -> dict[str, str]:
        memory, scene = self._memory(), self.stories().get(name)
        if memory is None or scene is None:
            return {}
        lines = {line.text: memory.get(line.text) for line in scene.lines}
        return {source: target for source, target in lines.items() if target}
