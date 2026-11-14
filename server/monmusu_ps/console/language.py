import json
from pathlib import Path

from monmusu_l10n.memory import Memory

from ..core.localization import LANGUAGE, SCENES as SCENES_FILE, SECTION, Fresh


SETTINGS = "settings.json"
SUFFIX = ".jsonl"
SCENES = "scenes"
SOURCE = "source"
NAMES = {"zh-Hans": "简体中文", "zh-Hant": "繁體中文", "en": "English"}


# The game shows the translation the settings name; none means Japanese as shipped.
class Language:
    def __init__(self, runtime, files):
        self.runtime = runtime
        self.files = files
        # Counted apart from the server's own copy, so looking does not evict what the game is served.
        self._library = Fresh(Memory)
        self._counted: tuple[Memory, dict, dict] | None = None

    def choices(self) -> list[dict]:
        workspace = self.runtime.localization.workspace
        if not workspace.exists():
            return []
        return [
            {"code": path.stem, "name": NAMES.get(path.stem, path.stem)}
            for path in sorted(workspace.glob(f"*{SUFFIX}"))
            if path.stem not in (SCENES, SOURCE)
        ]

    def current(self) -> str | None:
        return self.runtime.config.read().get(SECTION, {}).get(LANGUAGE)

    def library(self, code: str) -> dict | None:
        path: Path = self.runtime.localization.workspace / f"{code}{SUFFIX}"
        memory = self._library.of(path)
        if memory is None:
            return None
        staged = self.runtime.localization.stories()
        kept = self._counted
        if not (kept and kept[0] is memory and kept[1] is staged):
            lines = {line.text for scene in staged.values() for line in scene.lines}
            spoken = sum(text in memory for text in lines)
            self._counted = kept = (
                memory,
                staged,
                {
                    "texts": len(memory) - spoken,
                    "lines": spoken,
                    "all_lines": len(lines),
                    "scenes": len(staged),
                    "updated_at": int(path.stat().st_mtime),
                },
            )
        return kept[2]

    def document(self) -> dict:
        choices = self.choices()
        return {
            "current": self.current(),
            "choices": choices,
            "libraries": {row["code"]: self.library(row["code"]) for row in choices},
        }

    def choose(self, code) -> dict:
        if code is not None and code not in {entry["code"] for entry in self.choices()}:
            raise ValueError(f"No translation named {code}")
        settings = json.loads(self.files.read(SETTINGS)["text"])
        settings.setdefault(SECTION, {})[LANGUAGE] = code
        self.files.write(SETTINGS, json.dumps(settings, ensure_ascii=False, indent=2))
        return self.document()
