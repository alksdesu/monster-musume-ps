import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path


NOWHERE = r"(?!)"


@dataclass(frozen=True)
class Brief:
    style: str
    names: str
    tables: dict[str, str] = field(default_factory=dict)
    terms: dict[str, str] = field(default_factory=dict)
    cast: str = NOWHERE
    proper: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Brief":
        document = json.loads(path.read_text(encoding="utf-8"))
        proper = document.get("proper", {})
        return cls(
            document["style"],
            document["names"],
            document.get("tables", {}),
            {**document.get("terms", {}), **proper},
            document.get("cast", NOWHERE),
            proper,
        )

    def context(self, origin: str) -> str:
        note = self.tables.get(origin.partition(":")[0])
        return f"{note}（{origin}）" if note else origin

    def named(self, origins: Iterable[str]) -> bool:
        # Fields that hold what something is called, not what is said about it.
        return any(re.search(self.names, origin, re.IGNORECASE) for origin in origins)

    def in_cast(self, origins: Iterable[str]) -> bool:
        # Fields that hold who someone is, the only names talk is held to.
        return any(re.search(self.cast, origin) for origin in origins)
