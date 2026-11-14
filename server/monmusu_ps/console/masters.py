import asyncio
from collections import Counter
from pathlib import Path
import sys


TOOL = "tools/fetch_master.py"
TABLES = "masterdata/assets/resources/mdf_output"
LISTING = "masterdata/ablist.json"


class MasterTool:
    def __init__(self, root: Path):
        self.root = root
        self._process = None
        self._follower = None
        self._lines: list[str] = []
        self._code: int | None = None

    def summary(self) -> dict:
        tables = self.root / TABLES
        folders = Counter(path.parent.name for path in tables.glob("*/*.json"))
        listing = self.root / LISTING
        return {
            "folders": dict(sorted(folders.items())),
            "files": sum(folders.values()),
            "fetched_at": int(listing.stat().st_mtime) if listing.exists() else None,
            "tool": TOOL,
        }

    @property
    def running(self) -> bool:
        return self._process is not None and self._code is None

    def progress(self) -> dict:
        return {"running": self.running, "code": self._code, "lines": self._lines}

    async def _follow(self) -> None:
        async for line in self._process.stdout:
            self._lines.append(line.decode("utf-8", "replace").rstrip())
        self._code = await self._process.wait()

    async def start(self) -> None:
        if self.running:
            raise ValueError("The master data update is already running")
        self._lines, self._code = [], None
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-X",
            "utf8",
            str(self.root / TOOL),
            cwd=self.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._follower = asyncio.create_task(self._follow())

    async def close(self) -> None:
        if self.running:
            self._process.terminate()
            await self._follower
