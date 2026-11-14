import json
from dataclasses import dataclass
from pathlib import Path


MAIN, BONUS = 1, 2
LOOP_POINT = 1


class Board:
    def __init__(self, cells: list[dict], abilities: dict[int, int]):
        self._cells = {cell["id"]: cell for cell in cells}
        self._abilities = abilities
        self._entries: dict[int, list[int]] = {}
        for cell in cells:
            for following in cell["connections"]:
                self._entries.setdefault(following, []).append(cell["id"])

    def cell(self, identity: int) -> dict:
        return self._cells[identity]

    def ability(self, identity: int) -> int:
        return self._abilities.get(self._cells[identity]["category"], 0)

    def exits(self, identity: int) -> list[int]:
        return self._cells[identity]["connections"]

    def entries(self, identity: int) -> list[int]:
        return self._entries.get(identity, [])

    def loops(self, identity: int) -> bool:
        return bool(self._cells[identity]["flags"] & LOOP_POINT)


@dataclass(frozen=True)
class Stage:
    version: str
    boards: dict[int, Board]


class Stages:
    def __init__(self, directory: Path, masters):
        self.directory = directory
        self.masters = masters
        self._stages: dict[int, tuple[tuple, list, Stage]] = {}

    def stage(self, row: dict) -> Stage:
        identity = row["stage_id"]
        path = self.directory / f"stage_{identity}.json"
        status = path.stat()
        boards = (
            status.st_mtime_ns,
            status.st_size,
            row["map_id"],
            row["bonus_map_id"],
        )
        infos = self.masters.table("sugoroku_cell_info_data")
        cached = self._stages.get(identity)
        if cached is None or cached[0] != boards or cached[1] is not infos:
            document = json.loads(path.read_text(encoding="utf-8"))
            abilities = {info["cell_category"]: info["ability_id"] for info in infos}
            maps = {board["id"]: board["cells"] for board in document["maps"]}
            stage = Stage(
                document["version"],
                {
                    MAIN: Board(maps[row["map_id"]], abilities),
                    BONUS: Board(maps[row["bonus_map_id"]], abilities),
                },
            )
            self._stages[identity] = boards, infos, stage
        return self._stages[identity][2]
