from pathlib import Path
import shutil

import pytest

from monmusu_ps.app import RUNTIME, create_app
from monmusu_ps.config import Settings
from monmusu_ps.console import Folders
from monmusu_ps.console.configs import FILES
from monmusu_ps.core import localization
from monmusu_ps.core.schedule import Schedule


ROOT = Path(__file__).resolve().parents[1]
UNSCHEDULED = {"activities": [], "rows": []}


@pytest.fixture(autouse=True)
def unscheduled(monkeypatch):
    # Reopened activities would change what every other test reads from master data.
    monkeypatch.setattr(Schedule, "_document", lambda self: UNSCHEDULED)


@pytest.fixture(autouse=True)
def untranslated(monkeypatch, tmp_path):
    # The project's own translations would rewrite what every other test is handed.
    monkeypatch.setattr(localization, "WORKSPACE", tmp_path / "localization")


@pytest.fixture
def folders(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    for name in FILES:
        shutil.copyfile(ROOT / "server/data" / name, data / name)
    (data / "schedule.json").write_text('{"activities": [], "rows": []}', "utf-8")
    captures = tmp_path / "captures"
    captures.mkdir()
    return Folders(tmp_path / "backups", data, captures)


@pytest.fixture
def game_app(tmp_path, folders):
    # Nothing a test does through the console may reach the project's own files.
    def build(clock=None):
        settings = Settings.load(ROOT, tmp_path / "saves")
        app = create_app(settings, clock=clock, folders=folders)
        runtime = app[RUNTIME]
        runtime.config.path = folders.data / "settings.json"
        runtime.schedule.config.path = folders.data / "schedule.json"
        runtime.overrides.config.path = folders.data / "overrides.json"
        return app

    return build
