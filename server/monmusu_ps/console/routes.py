from dataclasses import dataclass
import json
from pathlib import Path

from aiohttp import web

from ..core.errors import ApiRejected
from ..access import HOSTS, LOOPBACK, failures, local_only
from .captures import Captures
from .content import Content
from .language import Language
from .configs import (
    FILES,
    OVERRIDES,
    SCHEDULE,
    SHARED,
    JsonFiles,
    MissingKeys,
)
from .masters import MasterTool
from .planner import Planner
from .presets import Presets
from .players import Players
from .roster import Roster
from .saves import Saves
from .shortcuts import Shortcuts
from .service import LogRing, Service


PREFIX = "/console"
PAGE = 50
ACTIONS = ("pause", "resume", "restart", "stop")


@dataclass(frozen=True)
class Folders:
    backups: Path
    data: Path
    captures: Path

    @classmethod
    def under(cls, root: Path) -> "Folders":
        return cls(root / "backups", root / "server/data", root / "captures")


class Console:
    def __init__(self, runtime, ring: LogRing, folders: Folders):
        self.service = Service(runtime, ring)
        self.saves = Saves(runtime, folders.backups)
        self.players = Players(runtime)
        self.roster = Roster(runtime)
        self.shortcuts = Shortcuts(runtime, self.roster, self.saves.backup)
        schedule = runtime.schedule
        self.files = JsonFiles(
            folders.data,
            folders.backups / "config",
            FILES,
            validators={SCHEDULE: schedule.preview, OVERRIDES: runtime.overrides.check},
            errors={
                "settings.json": lambda: runtime.config.error,
                SCHEDULE: lambda: schedule.config.error or schedule.error,
                OVERRIDES: runtime.overrides.report,
                "box_gacha.json": lambda: runtime.boxes.error,
            },
        )
        self.shared = JsonFiles(
            runtime.settings.saves,
            folders.backups / "shared",
            SHARED,
            applied={"social.json": runtime.social.graph.reload},
        )
        self.planner = Planner(runtime, self.files)
        self.content = Content(runtime, self.files)
        self.presets = Presets(runtime, self.files, self.planner)
        self.language = Language(runtime, self.files)
        self.captures = Captures(folders.captures)
        self.masters = MasterTool(runtime.settings.root)


CONSOLE = web.AppKey("console", Console)


@web.middleware
async def rejections(request: web.Request, handler):
    try:
        return await handler(request)
    except MissingKeys as error:
        return web.json_response(
            {"error": str(error), "missing": error.keys}, status=409
        )
    except ApiRejected as error:
        return web.json_response({"error": str(error)}, status=400)


def _console(request: web.Request) -> Console:
    return request.app[CONSOLE]


async def _body(request: web.Request, *names: str) -> list:
    body = await request.json()
    if not isinstance(body, dict) or any(name not in body for name in names):
        raise ValueError(f"The request needs: {', '.join(names)}")
    return [body[name] for name in names]


def _query(request: web.Request, name: str) -> str:
    if name not in request.query:
        raise ValueError(f"The request needs ?{name}=")
    return request.query[name]


async def status(request: web.Request) -> web.Response:
    return web.json_response(_console(request).service.status())


async def control(request: web.Request) -> web.Response:
    service = _console(request).service
    (action,) = await _body(request, "action")
    if action not in ACTIONS:
        raise ValueError(f"Unknown action: {action}")
    if action in ("pause", "resume"):
        service.pause(action == "pause")
    else:
        service.leave(restart=action == "restart")
    return web.json_response(service.status())


async def log_rows(request: web.Request) -> web.Response:
    after = int(request.query.get("after", 0))
    ring = _console(request).service.ring
    return web.json_response({"rows": ring.after(after), "latest": ring.latest})


async def saves(request: web.Request) -> web.Response:
    console = _console(request)
    return web.json_response(
        {"saves": console.saves.rows(), "backups": console.saves.backups()}
    )


async def backup(request: web.Request) -> web.Response:
    (label,) = await _body(request, "label")
    return web.json_response({"name": await _console(request).saves.backup(label)})


async def restore(request: web.Request) -> web.Response:
    name, folder = await _body(request, "backup", "folder")
    kept = await _console(request).saves.restore(name, folder)
    return web.json_response({"kept": kept})


async def create_save(request: web.Request) -> web.Response:
    (profile,) = await _body(request, "profile")
    await _console(request).saves.create(profile)
    return web.json_response({})


async def delete_save(request: web.Request) -> web.Response:
    (profile,) = await _body(request, "profile")
    return web.json_response({"kept": await _console(request).saves.delete(profile)})


async def kick(request: web.Request) -> web.Response:
    (profile,) = await _body(request, "profile")
    _console(request).saves.kick(profile)
    return web.json_response({})


async def bind(request: web.Request) -> web.Response:
    login, profile = await _body(request, "login", "profile")
    _console(request).saves.bind(login, profile)
    return web.json_response({})


async def player(request: web.Request) -> web.Response:
    players = _console(request).players
    return web.json_response(await players.summary(_query(request, "profile")))


async def section(request: web.Request) -> web.Response:
    players = _console(request).players
    value = await players.section(_query(request, "profile"), _query(request, "name"))
    return web.json_response({"value": value})


async def replace_section(request: web.Request) -> web.Response:
    profile, name, value = await _body(request, "profile", "name", "value")
    outcome = await _console(request).players.replace(profile, name, value)
    return web.json_response(outcome)


async def rename(request: web.Request) -> web.Response:
    profile, nickname = await _body(request, "profile", "nickname")
    return web.json_response(await _console(request).players.rename(profile, nickname))


async def balance(request: web.Request) -> web.Response:
    profile, rid, value = await _body(request, "profile", "rid", "value")
    outcome = await _console(request).players.set_balance(profile, rid, value)
    return web.json_response(outcome)


async def grant(request: web.Request) -> web.Response:
    fields = await _body(request, "profiles", "orders", "delivery", "message")
    return web.json_response(await _console(request).players.grant(*fields))


async def roster_units(request: web.Request) -> web.Response:
    roster = _console(request).roster
    return web.json_response({"units": await roster.units(_query(request, "profile"))})


async def roster_cards(request: web.Request) -> web.Response:
    roster = _console(request).roster
    return web.json_response({"cards": await roster.cards(_query(request, "profile"))})


async def roster_add(request: web.Request) -> web.Response:
    profile, card_ids = await _body(request, "profile", "card_ids")
    return web.json_response(await _console(request).roster.add(profile, card_ids))


async def roster_unit(request: web.Request) -> web.Response:
    profile, instance_id, fields = await _body(
        request, "profile", "instance_id", "fields"
    )
    outcome = await _console(request).roster.edit(profile, instance_id, fields)
    return web.json_response(outcome)


async def roster_fill(request: web.Request) -> web.Response:
    body = await request.json()
    profile, aspects = await _body(request, "profile", "aspects")
    outcome = await _console(request).roster.fill_all(
        profile, aspects, body.get("instance_ids")
    )
    return web.json_response(outcome)


async def shortcuts(request: web.Request) -> web.Response:
    console = _console(request)
    profile = _query(request, "profile")
    return web.json_response(
        {
            "catalog": console.shortcuts.catalog(),
            "status": await console.shortcuts.status(profile),
        }
    )


async def run_shortcut(request: web.Request) -> web.Response:
    profile, key = await _body(request, "profile", "key")
    return web.json_response(await _console(request).shortcuts.run(profile, key))


async def resources(request: web.Request) -> web.Response:
    return web.json_response({"rows": _console(request).players.catalog()})


async def schedule(request: web.Request) -> web.Response:
    return web.json_response(_console(request).planner.document())


async def save_schedule(request: web.Request) -> web.Response:
    (document,) = await _body(request, "document")
    return web.json_response(_console(request).planner.save(document))


async def preview(request: web.Request) -> web.Response:
    (entry,) = await _body(request, "entry")
    return web.json_response(_console(request).planner.preview(entry))


async def catalog(request: web.Request) -> web.Response:
    query = request.query
    listed = _console(request).planner.catalog(
        _query(request, "kind"),
        query.get("q", ""),
        query.get("status", ""),
        int(query.get("offset", 0)),
        int(query.get("limit", PAGE)),
    )
    return web.json_response(listed)


def _file_routes(listing: str, base: str, pick) -> tuple:
    async def names(request: web.Request) -> web.Response:
        return web.json_response({"files": pick(_console(request)).names})

    async def read(request: web.Request) -> web.Response:
        files = pick(_console(request))
        return web.json_response(files.read(_query(request, "name")))

    async def write(request: web.Request) -> web.Response:
        files = pick(_console(request))
        name, text = await _body(request, "name", "text")
        body = await request.json()
        files.write(name, text, allow_missing=body.get("allow_missing") is True)
        return web.json_response(files.read(name))

    async def version(request: web.Request) -> web.Response:
        files = pick(_console(request))
        text = files.version(_query(request, "name"), _query(request, "version"))
        return web.json_response({"text": text})

    async def restore(request: web.Request) -> web.Response:
        files = pick(_console(request))
        name, kept = await _body(request, "name", "version")
        files.restore(name, kept)
        return web.json_response(files.read(name))

    return (
        ("GET", listing, names),
        ("GET", base, read),
        ("POST", base, write),
        ("GET", f"{base}/version", version),
        ("POST", f"{base}/restore", restore),
    )


async def presets(request: web.Request) -> web.Response:
    return web.json_response({"actions": _console(request).presets.catalog()})


async def run_preset(request: web.Request) -> web.Response:
    body = await request.json()
    (key,) = await _body(request, "key")
    outcome = _console(request).presets.run(key, body.get("on"), body.get("value"))
    return web.json_response(outcome)


async def preset_items(request: web.Request) -> web.Response:
    query = request.query
    listed = _console(request).presets.items(
        _query(request, "key"),
        query.get("q", ""),
        query.get("status", ""),
        int(query.get("offset", 0)),
        int(query.get("limit", 50)),
    )
    return web.json_response(listed)


async def pick_preset(request: web.Request) -> web.Response:
    key, ids, on = await _body(request, "key", "ids", "on")
    return web.json_response(_console(request).presets.pick(key, ids, bool(on)))


async def language(request: web.Request) -> web.Response:
    return web.json_response(_console(request).language.document())


async def choose_language(request: web.Request) -> web.Response:
    (code,) = await _body(request, "code")
    return web.json_response(_console(request).language.choose(code))


async def content_tables(request: web.Request) -> web.Response:
    content = _console(request).content
    return web.json_response({"groups": content.groups(), "tables": content.tables()})


async def content_rows(request: web.Request) -> web.Response:
    query = request.query
    listed = _console(request).content.rows(
        _query(request, "table"),
        query.get("q", ""),
        int(query.get("offset", 0)),
        int(query.get("limit", PAGE)),
        query.get("changed") == "1",
    )
    return web.json_response(listed)


async def content_row(request: web.Request) -> web.Response:
    key = json.loads(_query(request, "key"))
    row = _console(request).content.row(_query(request, "table"), key)
    return web.json_response(row)


async def edit_content(request: web.Request) -> web.Response:
    table, key, fields = await _body(request, "table", "key", "fields")
    return web.json_response(_console(request).content.edit(table, key, fields))


async def bulk_content(request: web.Request) -> web.Response:
    body = await request.json()
    table, path, value = await _body(request, "table", "path", "value")
    outcome = _console(request).content.bulk(
        table, body.get("q", ""), bool(body.get("changed")), path, value
    )
    return web.json_response(outcome)


async def reset_content(request: web.Request) -> web.Response:
    (table,) = await _body(request, "table")
    body = await request.json()
    reset = _console(request).content.reset(table, body.get("key"))
    return web.json_response({"reset": reset})


async def content_changes(request: web.Request) -> web.Response:
    return web.json_response({"changes": _console(request).content.changes()})


async def captures(request: web.Request) -> web.Response:
    return web.json_response({"sessions": _console(request).captures.sessions()})


async def capture(request: web.Request) -> web.Response:
    entries = _console(request).captures.entries(_query(request, "name"))
    return web.json_response({"entries": entries})


async def capture_entry(request: web.Request) -> web.Response:
    entry = _console(request).captures.entry(
        _query(request, "name"), int(_query(request, "index"))
    )
    return web.json_response(entry)


async def compare_captures(request: web.Request) -> web.Response:
    left, right = await _body(request, "left", "right")
    rows = _console(request).captures.compare(left, right)
    return web.json_response({"differences": rows})


async def masters(request: web.Request) -> web.Response:
    tool = _console(request).masters
    return web.json_response({"summary": tool.summary(), "progress": tool.progress()})


async def update_masters(request: web.Request) -> web.Response:
    tool = _console(request).masters
    await tool.start()
    return web.json_response(tool.progress())


ROUTES = (
    ("GET", "/api/status", status),
    ("POST", "/api/service", control),
    ("GET", "/api/logs", log_rows),
    ("GET", "/api/saves", saves),
    ("POST", "/api/saves/backup", backup),
    ("POST", "/api/saves/restore", restore),
    ("POST", "/api/saves/bind", bind),
    ("POST", "/api/saves/create", create_save),
    ("POST", "/api/saves/delete", delete_save),
    ("POST", "/api/saves/kick", kick),
    ("GET", "/api/player", player),
    ("GET", "/api/player/section", section),
    ("POST", "/api/player/section", replace_section),
    ("POST", "/api/player/rename", rename),
    ("POST", "/api/player/balance", balance),
    ("POST", "/api/player/grant", grant),
    ("GET", "/api/resources", resources),
    ("GET", "/api/roster", roster_units),
    ("GET", "/api/roster/cards", roster_cards),
    ("POST", "/api/roster/add", roster_add),
    ("POST", "/api/roster/unit", roster_unit),
    ("POST", "/api/roster/fill", roster_fill),
    ("GET", "/api/shortcuts", shortcuts),
    ("POST", "/api/shortcuts/run", run_shortcut),
    ("GET", "/api/schedule", schedule),
    ("POST", "/api/schedule", save_schedule),
    ("POST", "/api/schedule/preview", preview),
    ("GET", "/api/schedule/catalog", catalog),
    *_file_routes("/api/configs", "/api/config", lambda console: console.files),
    *_file_routes(
        "/api/shared-files", "/api/shared-file", lambda console: console.shared
    ),
    ("GET", "/api/presets", presets),
    ("POST", "/api/presets/run", run_preset),
    ("GET", "/api/presets/items", preset_items),
    ("POST", "/api/presets/pick", pick_preset),
    ("GET", "/api/language", language),
    ("POST", "/api/language", choose_language),
    ("GET", "/api/content", content_tables),
    ("GET", "/api/content/rows", content_rows),
    ("GET", "/api/content/row", content_row),
    ("POST", "/api/content/row", edit_content),
    ("POST", "/api/content/bulk", bulk_content),
    ("POST", "/api/content/reset", reset_content),
    ("GET", "/api/content/changes", content_changes),
    ("GET", "/api/captures", captures),
    ("GET", "/api/capture", capture),
    ("GET", "/api/capture/entry", capture_entry),
    ("POST", "/api/capture/compare", compare_captures),
    ("GET", "/api/masters", masters),
    ("POST", "/api/masters/update", update_masters),
)


def mount(
    app: web.Application, runtime, ring: LogRing, folders: Folders | None = None
) -> Console:
    console = Console(runtime, ring, folders or Folders.under(runtime.settings.root))
    panel = web.Application(middlewares=[local_only, failures, rejections])
    panel[CONSOLE] = console
    panel[HOSTS] = frozenset({*LOOPBACK, runtime.settings.host})
    for method, path, handler in ROUTES:
        panel.router.add_route(method, path, handler)

    async def close(_: web.Application) -> None:
        await console.masters.close()

    panel.on_cleanup.append(close)
    app.add_subapp(PREFIX, panel)
    return console
