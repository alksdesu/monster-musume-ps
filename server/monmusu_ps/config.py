from dataclasses import dataclass
import json
import logging
from pathlib import Path


LOGGER = logging.getLogger("monmusu.config")


@dataclass(frozen=True)
class Settings:
    root: Path
    saves: Path
    host: str
    port: int
    public_url: str

    @classmethod
    def load(cls, root: Path, saves: Path | None = None) -> "Settings":
        root = root.resolve()
        data = json.loads(
            (root / "server/data/settings.json").read_text(encoding="utf-8")
        )
        return cls(
            root,
            saves or root / "saves",
            data["host"],
            data["port"],
            data["public_url"].rstrip("/"),
        )


class RuntimeConfig:
    def __init__(self, path: Path):
        self.path = path
        self._stamp = None
        self._data = None
        self.error: str | None = None

    def read(self) -> dict:
        status = self.path.stat()
        stamp = (status.st_mtime_ns, status.st_size)
        if stamp != self._stamp:
            self._stamp = stamp
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                self.error = None
            except ValueError as error:
                # A file caught mid-edit must not take the running server down.
                if self._data is None:
                    raise
                self.error = str(error)
                LOGGER.error(
                    "%s rejected, keeping the previous one: %s", self.path.name, error
                )
        return self._data
