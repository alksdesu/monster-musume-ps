import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable, Iterator


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(
                value,
                stream,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class PlayerStore:
    def __init__(
        self,
        root: Path,
        create_state: Callable[[str], dict],
        migrations: tuple[Callable[[dict], None], ...] = (),
    ):
        self.root = root
        self.create_state = create_state
        self.migrations = migrations
        self.schema_version = len(migrations) + 1
        self._locks: dict[str, asyncio.Lock] = {}

    def path(self, profile: str) -> Path:
        key = hashlib.sha256(profile.encode("utf-8")).hexdigest()
        return self.root / key / "state.json"

    def exists(self, profile: str) -> bool:
        return self.path(profile).exists()

    def documents(self) -> Iterator[dict]:
        for path in sorted(self.root.glob("*/state.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            self._migrate(document)
            yield document

    def _migrate(self, document: dict) -> bool:
        if not 1 <= document["schema_version"] <= self.schema_version:
            raise ValueError(f"Unsupported save schema: {document['schema_version']}")
        migrated = document["schema_version"] < self.schema_version
        while document["schema_version"] < self.schema_version:
            self.migrations[document["schema_version"] - 1](document["state"])
            document["schema_version"] += 1
        return migrated

    def _load(self, profile: str) -> tuple[dict, bool]:
        path = self.path(profile)
        if not path.exists():
            return {
                "schema_version": self.schema_version,
                "profile": profile,
                "revision": 0,
                "state": self.create_state(profile),
            }, True
        document = json.loads(path.read_text(encoding="utf-8"))
        if document["profile"] != profile:
            raise ValueError("Save profile does not match its directory")
        return document, self._migrate(document)

    @asynccontextmanager
    async def transaction(self, profile: str):
        async with self._locks.setdefault(profile, asyncio.Lock()):
            document, changed = self._load(profile)
            original = document["state"]
            state = deepcopy(original)
            yield state
            if state != original or changed:
                document["state"] = state
                document["revision"] += 1
                write_json(self.path(profile), document)

    @asynccontextmanager
    async def exclusive(self, profile: str):
        async with self._locks.setdefault(profile, asyncio.Lock()):
            yield self.path(profile)

    async def read(self, profile: str) -> dict:
        async with self.transaction(profile) as state:
            return deepcopy(state)
