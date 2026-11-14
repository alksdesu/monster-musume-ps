import json
from pathlib import Path


PRELOADED, LATE = "main", "not-preloaded"
HEAPED = "class"
HOLDERS = ("class", "struct")
SEQUENCES = ("array", "list")
SCALAR = "scalar"
# Each unit, token and enemy is an object of its own, kept by the game under these folder names.
FOLDERS = {"unitcard": "UnitCard", "bomcard": "BomCard", "enemydata": "EnemyData"}
GROUP = "@"


class ClientLayout:
    def __init__(self, path: Path):
        self.path = path
        self._documents: dict | None = None

    def _document(self, name: str) -> dict:
        if self._documents is None:
            self._documents = json.loads(self.path.read_text(encoding="utf-8"))
        return self._documents[name]

    @staticmethod
    def named(group: str) -> str:
        return GROUP + group

    def asset(self, name: str) -> str:
        return self._document(name)["asset"]

    def folder(self, group: str) -> str:
        return FOLDERS[self._document(self.named(group))["asset_group"]]

    def late(self, name: str) -> bool:
        # The objects of a folder are read in with the main preload, which the layout leaves unsaid.
        loaded = self._document(name).get("preload", PRELOADED)
        if loaded not in (PRELOADED, LATE):
            raise ValueError(f"{name} is used up before the game can be reached")
        return loaded == LATE

    @staticmethod
    def _step(node: dict, step) -> dict:
        if isinstance(step, int):
            if node["kind"] not in SEQUENCES:
                raise ValueError("the game keeps no list here")
            return node["element"]
        if node["kind"] not in HOLDERS:
            raise ValueError("the game keeps no fields here")
        for field in node["fields"]:
            if field["name"] == step:
                return field
        raise ValueError(f"the game has no field named {step}")

    def row(self, name: str, container: tuple[str, ...]) -> dict:
        node = self._document(name)["layout"]
        for step in container:
            node = self._step(node, step)["element"]
        return node

    def leaf(self, name: str, container: tuple[str, ...], steps: list) -> dict:
        node = host = self.row(name, container)
        for step in steps:
            node = self._step(node, step)
            if node["kind"] == HEAPED:
                host = node
        if node["kind"] != SCALAR:
            raise ValueError("the game keeps more than one value here")
        # What the game works out from a row is kept beside it until it is cleared.
        return {"type": node["type"], "forget": self._kept(host)}

    @staticmethod
    def _kept(node: dict) -> list[str]:
        return [extra["name"] for extra in node.get("runtime_only", [])]

    def indexes(self, name: str) -> list[str]:
        return self._kept(self._document(name)["layout"])
