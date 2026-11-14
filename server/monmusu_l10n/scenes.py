import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from .markup import JAPANESE


SCRIPTS = "assets/advscene/scenarioexcel"
SETTINGS = "assets/advscene/scenariochapter/config.chapter.json"
SCRIPT = ".book.json"
STORIES = (
    "story_data_unit",
    "story_data_main",
    "story_data_other",
    "story_data_event",
    "story_data_group",
)
PLAIN, ADULT = "scenario", "scenario_r18"
CAST = "scenario_cast:name"
CHOICE, SETTING = "Selection", "Param"
CHARACTERS, PARAMETERS = "Character", "Param"
STRING = "String"
# The engine trims a command's argument before reading it.
ASSIGNED = re.compile(r'\s*(\w+)\s*=\s*"(.*)"\s*')
PARAMETER = re.compile(r"<param=(\w+)>")


@dataclass(frozen=True)
class Line:
    speaker: str
    text: str
    choice: bool = False


@dataclass(frozen=True)
class Scene:
    origin: str
    title: str
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Story:
    title: str = ""
    adult: bool = False


@dataclass(frozen=True)
class Cast:
    shown: Mapping[str, str]
    values: Mapping[str, str]

    def name(self, label: str, values: Mapping[str, str]) -> str:
        # A label the sheet does not list is put on screen as it is written.
        shown = self.shown.get(label, label)
        return PARAMETER.sub(lambda found: values.get(found[1], found[0]), shown)


def _rows(grid: dict) -> Iterator[dict[str, str]]:
    rows = grid["rows"]
    head = rows[grid["headerRow"]]["strings"] if rows else []
    for row in rows[grid["headerRow"] + 1 :]:
        if not row["isCommentOut"]:
            yield dict(zip(head, row["strings"]))


def _sheet(grid: dict) -> str:
    return grid["name"].rpartition(":")[2]


def stories(tables: Path) -> dict[str, Story]:
    found: dict[str, Story] = {}
    for name in STORIES:
        path = tables / f"{name}.json"
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8"))["table"]:
            known = found.get(row["scenario_name"], Story(row["title"]))
            # A script told in several places is adult once one of them marks it so.
            found[row["scenario_name"]] = Story(
                known.title, known.adult or bool(row["r18"])
            )
    return found


def cast(root: Path) -> Cast:
    document = json.loads((root / SETTINGS).read_text(encoding="utf-8"))
    sheets = {_sheet(grid): list(_rows(grid)) for grid in document["settingList"]}
    shown = {
        row["CharacterName"]: row["NameText"]
        for row in sheets.get(CHARACTERS, ())
        if row.get("CharacterName") and row.get("NameText")
    }
    values = {
        row["Label"]: row.get("Value", "")
        for row in sheets.get(PARAMETERS, ())
        if row.get("Label") and row.get("Type") == STRING
    }
    return Cast(shown, values)


def _lines(grid: dict, speaking: Cast) -> Iterator[Line]:
    values = dict(speaking.values)
    for row in _rows(grid):
        command, label, text = (
            row.get("Command", ""),
            row.get("Arg1", ""),
            row.get("Text", ""),
        )
        assigned = ASSIGNED.fullmatch(label) if command == SETTING else None
        if assigned:
            # A script renames who is speaking from that row on.
            values[assigned[1]] = assigned[2]
        elif command in ("", CHOICE) and JAPANESE.search(text):
            speaker = "" if command else speaking.name(label, values)
            yield Line(speaker, text, bool(command))


def collect(root: Path, tables: Path) -> list[Scene]:
    if not (root / SETTINGS).exists():
        return []
    told, speaking = stories(tables), cast(root)
    found = []
    for path in sorted((root / SCRIPTS).rglob(f"*{SCRIPT}")):
        name = path.name.removesuffix(SCRIPT)
        story = told.get(name, Story())
        place = path.relative_to(root / SCRIPTS).with_name(name).as_posix()
        for grid in json.loads(path.read_text(encoding="utf-8"))["importGridList"]:
            lines = tuple(_lines(grid, speaking))
            if not lines:
                continue
            # A workbook holds one scene a sheet; the sheet named after it is the scene itself.
            sheet = _sheet(grid)
            part = place if sheet == name else f"{place}#{sheet}"
            group = ADULT if story.adult else PLAIN
            found.append(Scene(f"{group}:{part}", story.title, lines))
    return found


def names(scenes: Iterable[Scene]) -> list[tuple[str, str]]:
    shown = {line.speaker for scene in scenes for line in scene.lines}
    return [(CAST, name) for name in sorted(shown) if JAPANESE.search(name)]


def save(scenes: Iterable[Scene], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for scene in scenes:
            file.write(json.dumps(asdict(scene), ensure_ascii=False) + "\n")


def load(path: Path) -> list[Scene]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as file:
        scenes = (json.loads(line) for line in file if line.strip())
        return [
            Scene(
                scene["origin"],
                scene["title"],
                tuple(Line(**line) for line in scene["lines"]),
            )
            for scene in scenes
        ]
