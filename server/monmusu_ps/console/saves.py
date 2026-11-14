import asyncio
from collections import defaultdict
import json
from pathlib import Path
import re
import shutil
import time

from ..core.store import write_json
from ..access import child


LABEL = re.compile("[A-Za-z0-9_-]{0,32}")
PROFILE = re.compile("[a-z]{1,16}:[A-Za-z0-9_.-]{1,64}")
STATE = "state.json"


class Saves:
    def __init__(self, runtime, backups: Path):
        self.runtime = runtime
        self.directory = backups

    def rows(self) -> list[dict]:
        runtime = self.runtime
        bindings = runtime.data_links.bindings()
        logins = defaultdict(list)
        for login, profile in bindings.items():
            logins[profile].append(login)
        online = {session.profile for session in runtime.sessions.active()}
        rows = []
        for document in runtime.store.documents():
            profile, player = document["profile"], document["state"]["player"]
            path = runtime.store.path(profile)
            rows.append(
                {
                    "profile": profile,
                    "folder": path.parent.name,
                    "player_code": player["player_code"],
                    "nickname": player["nickname"],
                    "tutorial": player["tutorial"],
                    "revision": document["revision"],
                    "modified": int(path.stat().st_mtime),
                    "logins": sorted(logins[profile]),
                    "rebound_to": bindings.get(profile),
                    "online": profile in online,
                }
            )
        return rows

    def bind(self, login: str, profile: str) -> None:
        runtime = self.runtime
        if not runtime.store.exists(profile):
            raise KeyError(f"Unknown save: {profile}")
        runtime.data_links.bind(login, profile, int(runtime.clock()))
        # A client still holding the old save has to come back through the title.
        for session in runtime.sessions.active():
            if session.login == login:
                runtime.sessions.close(session.profile)

    def _known(self, profile: str) -> str:
        if not self.runtime.store.exists(profile):
            raise KeyError(f"Unknown save: {profile}")
        return profile

    async def create(self, profile: str) -> None:
        if not isinstance(profile, str) or not PROFILE.fullmatch(profile):
            raise ValueError("A save is named like dmm:someone")
        if self.runtime.store.exists(profile):
            raise ValueError(f"The save already exists: {profile}")
        async with self.runtime.store.transaction(profile):
            pass

    def _forget(self, profile: str) -> None:
        # A client still holding the old save has to come back through the title.
        self.runtime.outbox.discard(profile)
        self.runtime.sessions.close(profile)

    async def delete(self, profile: str) -> str:
        kept = await self.backup("before-delete")
        async with self.runtime.store.exclusive(self._known(profile)) as path:
            shutil.rmtree(path.parent)
        self._forget(profile)
        return kept

    def kick(self, profile: str) -> None:
        self.runtime.sessions.close(self._known(profile))

    @staticmethod
    def _described(path: Path) -> dict:
        document = json.loads(path.read_text(encoding="utf-8"))
        player = document["state"]["player"]
        return {
            "folder": path.parent.name,
            "profile": document["profile"],
            "player_code": player["player_code"],
            "nickname": player["nickname"],
            "schema_version": document["schema_version"],
        }

    def backups(self) -> list[dict]:
        if not self.directory.exists():
            return []
        rows = []
        for directory in self.directory.iterdir():
            saves = sorted(directory.glob(f"*/{STATE}")) if directory.is_dir() else []
            if saves:
                rows.append(
                    {
                        "name": directory.name,
                        "created": int(directory.stat().st_mtime),
                        "saves": [self._described(path) for path in saves],
                    }
                )
        return sorted(rows, key=lambda row: row["created"], reverse=True)

    async def backup(self, label: str = "") -> str:
        if not isinstance(label, str) or not LABEL.fullmatch(label):
            raise ValueError("A backup label takes letters, digits, - and _ only")
        stem = time.strftime("console-%Y%m%d-%H%M%S") + (f"-{label}" if label else "")
        target, attempt = self.directory / stem, 1
        while target.exists():
            attempt += 1
            target = self.directory / f"{stem}.{attempt}"
        await asyncio.to_thread(
            shutil.copytree,
            self.runtime.settings.saves,
            target,
            # A save being written leaves a temporary file that may vanish mid-copy.
            ignore=shutil.ignore_patterns("*.tmp"),
        )
        return target.name

    async def restore(self, backup: str, folder: str) -> str:
        runtime = self.runtime
        source = child(child(self.directory, backup), folder) / STATE
        document = json.loads(source.read_text(encoding="utf-8"))
        profile = document["profile"]
        if not 1 <= document["schema_version"] <= runtime.store.schema_version:
            raise ValueError("The backup was written by a newer server")
        if runtime.store.path(profile).parent.name != folder:
            raise ValueError("The backup folder does not belong to its profile")
        kept = await self.backup("before-restore")
        async with runtime.store.exclusive(profile) as path:
            write_json(path, document)
        self._forget(profile)
        return kept
