import asyncio
from pathlib import Path
import socket
import subprocess
import sys
from urllib.request import urlopen

from aiohttp.test_utils import TestClient, TestServer
import pytest
from yarl import URL

from monmusu_console.app import Site
from monmusu_console.client import EXECUTABLE, GameClient, listed, tasklist
from monmusu_console.desk import create_app
from monmusu_console.game import GameServer


ROOT = Path(__file__).resolve().parents[1]
STUB = Path(__file__).with_name("stub_server.py")
HOST = "127.0.0.1"
PID = 4242
HARMLESS = [sys.executable, "-c", "raise SystemExit(9)"]
OPENS = (
    "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text(' '.join(sys.argv[2:]))"
)
FAILS = "print('the plugin did not build'); raise SystemExit(2)"
QUIET = "print('nothing to do')"
SLEEPS = "import time; time.sleep(30)"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def local(port: int) -> URL:
    return URL.build(scheme="http", host=HOST, port=port)


def stub(port: int, manner: str) -> list[str]:
    return [sys.executable, str(STUB), str(port), manner]


class Game:
    def __init__(self, folder: Path, manner: str = "obedient"):
        self.marker = folder / "game-is-open"
        self.manner = manner
        self.ended: list[tuple[int, bool]] = []

    def tasks(self) -> str:
        if self.marker.exists():
            return f'"{EXECUTABLE}","{PID}","Console","1","512,000 K"\n'
        return "INFO: No tasks are running which match the specified criteria.\n"

    def end(self, pid: int, force: bool) -> None:
        self.ended.append((pid, force))
        if self.manner == "obedient" or (force and self.manner == "stubborn"):
            self.marker.unlink()

    def client(self, code: str = OPENS, **options) -> GameClient:
        launcher = [sys.executable, "-c", code, str(self.marker)]
        options = {"close_timeout": 0.4, "poll": 0.05, **options}
        return GameClient(
            ROOT, launcher=launcher, tasks=self.tasks, end=self.end, **options
        )


@pytest.fixture
def game(tmp_path):
    return Game(tmp_path)


@pytest.fixture
async def desk(tmp_path, game):
    opened = []

    async def open_desk(root=ROOT, client=None, **options):
        # No test may start the project's real server or game by accident.
        options.setdefault("command", HARMLESS)
        logs = tmp_path / "logs"
        logs.mkdir(exist_ok=True)
        server = GameServer(root, logs / "server.log", **options)
        application = create_app(server, client or game.client(QUIET), logs)
        page = TestClient(TestServer(application))
        await page.start_server()
        page.managed, page.logs = server, logs
        opened.append(page)
        return page

    yield open_desk
    for page in opened:
        await page.managed.stop()
        await page.close()


@pytest.fixture
async def upstream(game_app):
    async with TestServer(game_app()) as server:
        yield server


async def get(page, path, expect=200, **query):
    response = await page.get(f"/api/{path}", params=query)
    assert response.status == expect, await response.text()
    return await response.json()


async def post(page, path, body, expect=200):
    response = await page.post(f"/api/{path}", json=body)
    assert response.status == expect, await response.text()
    return await response.json()


async def server_state(page) -> dict:
    return (await get(page, "app"))["server"]


async def order(page, action, expect=200) -> dict:
    answer = await post(page, "app/server", {"action": action}, expect)
    return answer["server"] if expect == 200 else answer


async def play(page, action, expect=200, **options) -> dict:
    return await post(page, "app/client", {"action": action, **options}, expect)


async def settled(page, state):
    for _ in range(100):
        server = await server_state(page)
        if server["state"] == state:
            return server
        await asyncio.sleep(0.1)
    raise AssertionError(f"The server never became {state}")


async def test_the_window_gets_its_pages_from_the_console_itself(desk):
    page = await desk(address=local(free_port()))
    home = await page.get("/")
    assert home.status == 200 and home.content_type == "text/html"
    script = await page.get("/static/app.js")
    assert script.status == 200 and "javascript" in script.content_type
    foreign = await page.get("/api/app", headers={"Host": "evil.example"})
    assert foreign.status == 403
    cross = await page.post(
        "/api/app/server",
        json={"action": "start"},
        headers={"Origin": "http://evil.example"},
    )
    assert cross.status == 403
    form = await page.post("/api/app/server", data={"action": "start"})
    assert form.status == 415
    for target in ("app/server", "app/client"):
        await post(page, target, {"action": "dance"}, expect=400)
        await post(page, target, [], expect=400)


async def test_a_running_server_is_reported_and_reached_through_the_console(
    desk, upstream
):
    page = await desk(address=local(upstream.port))
    server = await server_state(page)
    assert server["state"] == "running" and server["owned"] is False
    assert server["pid"] is None
    assert server["address"] == f"http://{HOST}:{upstream.port}"
    assert server["status"]["endpoints"] == server["status"]["contracts"] == 163
    assert (await get(page, "saves")) == {"saves": [], "backups": []}
    assert (await post(page, "service", {"action": "pause"}))["paused"] is True
    assert (await server_state(page))["status"]["paused"] is True
    missing = await get(page, "config", expect=404, name="passwords.json")
    assert "passwords.json" in missing["error"]
    assert "?profile=" in (await get(page, "player", expect=400))["error"]
    listed_ = await get(page, "schedule/catalog", kind="bingo", status="ended")
    assert listed_["total"] == 10


async def test_a_stopped_server_leaves_the_console_working(desk):
    page = await desk(address=local(free_port()))
    app = await get(page, "app")
    assert app["server"]["state"] == "stopped" and app["server"]["status"] is None
    assert app["server"]["failure"] is None
    assert app["client"] == {"state": "stopped", "pids": [], "failure": None}
    offline = await get(page, "saves", expect=503)
    assert offline["code"] == "offline"
    assert (await post(page, "service", {"action": "pause"}, 503)) == offline
    await order(page, "stop")


async def test_log_files_are_read_without_the_server(desk):
    page = await desk(address=local(free_port()))
    assert (await get(page, "logs/files"))["files"] == []
    (page.logs / "server.log").write_text("x" * 70000 + "tail", "utf-8")
    (page.logs / "notes.txt").write_text("not a log", "utf-8")
    files = (await get(page, "logs/files"))["files"]
    assert [(row["name"], row["size"]) for row in files] == [("server.log", 70004)]
    text = (await get(page, "logs/file", name="server.log"))["text"]
    assert len(text) == 65536 and text.endswith("tail")
    await get(page, "logs/file", name="../settings.json", expect=400)
    await get(page, "logs/file", name="missing.log", expect=404)
    await get(page, "logs/file", expect=400)
    assert (await get(page, "logs", expect=503))["code"] == "offline"


async def test_the_console_starts_restarts_and_stops_its_own_server(desk):
    port = free_port()
    page = await desk(address=local(port), command=stub(port, "obedient"))
    started = await order(page, "start")
    assert started["state"] == "running" and started["owned"] is True
    assert started["status"] == {"paused": False, "stub": True}
    assert (await get(page, "status"))["stub"] is True
    assert (await order(page, "start"))["pid"] == started["pid"]
    restarted = await order(page, "restart")
    assert restarted["state"] == "running" and restarted["pid"] != started["pid"]
    stopped = await order(page, "stop")
    assert stopped["state"] == "stopped" and stopped["owned"] is False
    assert stopped["pid"] is None and stopped["failure"] is None
    log = (page.logs / "server.log").read_text("utf-8")
    assert log.count("stub server asked to be obedient") == 2


async def test_a_server_that_cannot_start_shows_why(desk):
    port = free_port()
    page = await desk(address=local(port), command=stub(port, "broken"))
    failed = await order(page, "start", expect=502)
    assert failed["code"] == "start_failed" and "code 3" in failed["error"]
    assert "stub server asked to be broken" in failed["log"]
    server = await server_state(page)
    assert server["state"] == "stopped" and server["failure"] == failed
    missing = await desk(address=local(port), command=[str(ROOT / "no-such-python")])
    failed = await order(missing, "start", expect=502)
    assert failed["code"] == "start_failed" and failed["log"] == ""


async def test_a_server_that_never_answers_is_reported_as_slow(desk):
    silent = [sys.executable, "-c", SLEEPS]
    page = await desk(
        address=local(free_port()), command=silent, start_timeout=0.5, stop_timeout=0.5
    )
    assert (await order(page, "start", expect=502))["code"] == "start_timeout"
    assert (await server_state(page))["owned"] is True


async def test_an_unresponsive_server_of_its_own_is_ended(desk):
    port = free_port()
    page = await desk(
        address=local(port), command=stub(port, "stubborn"), stop_timeout=1
    )
    assert (await order(page, "start"))["state"] == "running"
    stopped = await order(page, "stop")
    assert stopped["state"] == "stopped" and stopped["failure"] is None


async def test_an_unresponsive_server_started_elsewhere_is_left_running(desk):
    port = free_port()
    elsewhere = subprocess.Popen(stub(port, "stubborn"), stdout=subprocess.DEVNULL)
    try:
        page = await desk(address=local(port), stop_timeout=1)
        assert (await settled(page, "running"))["owned"] is False
        assert (await order(page, "stop", expect=502))["code"] == "stop_timeout"
        assert elsewhere.poll() is None
        assert (await server_state(page))["state"] == "running"
        elsewhere.terminate()
        elsewhere.wait()
        assert (await server_state(page))["state"] == "stopped"
    finally:
        elsewhere.kill()
        elsewhere.wait()


async def test_an_unknown_address_is_reported_before_anything_starts(desk, tmp_path):
    page = await desk(root=tmp_path)
    server = await server_state(page)
    assert server["state"] == "stopped" and server["address"] is None
    assert server["failure"]["code"] == "settings"
    assert (await get(page, "saves", expect=503))["code"] == "offline"
    failed = await order(page, "start", expect=502)
    assert failed["code"] == "settings" and "settings.json" in failed["error"]


def test_the_configured_address_is_where_the_server_is_looked_for(tmp_path):
    server = GameServer(ROOT, tmp_path / "server.log")
    assert str(server.address) == "http://127.0.0.1:18621"
    assert server.public_url == "http://127.0.0.1:18621"
    assert server.failure is None


async def test_starting_the_game_starts_its_server_first(desk, game):
    port = free_port()
    page = await desk(
        address=local(port), command=stub(port, "obedient"), client=game.client()
    )
    app = await play(page, "start", viewer="alt", shipped_dates=True)
    assert app["server"]["state"] == "running" and app["server"]["owned"] is True
    assert app["client"] == {"state": "running", "pids": [PID], "failure": None}
    asked = f"-Profile alt -BaseUrl http://{HOST}:{port} -ShippedDates"
    assert game.marker.read_text() == asked
    await play(page, "start", viewer="other")
    assert game.marker.read_text() == asked
    await play(page, "restart")
    assert game.marker.read_text() == f"-Profile local -BaseUrl http://{HOST}:{port}"
    assert game.ended == [(PID, False)]
    closed = await play(page, "stop")
    assert closed["client"]["state"] == "stopped" and closed["client"]["pids"] == []
    assert closed["server"]["state"] == "running"
    assert game.ended == [(PID, False), (PID, False)]
    await play(page, "stop")
    assert len(game.ended) == 2


async def test_the_game_is_not_started_for_a_bad_login_or_a_dead_server(desk, game):
    port = free_port()
    page = await desk(
        address=local(port), command=stub(port, "broken"), client=game.client()
    )
    await play(page, "start", expect=400, viewer="two words")
    await play(page, "start", expect=400, viewer=7)
    assert (await server_state(page))["failure"] is None
    failed = await play(page, "start", expect=502)
    assert failed["code"] == "start_failed"
    assert not game.marker.exists()


async def test_a_launcher_that_fails_shows_its_output(desk, game, upstream):
    page = await desk(address=local(upstream.port), client=game.client(FAILS))
    failed = await play(page, "start", expect=502)
    assert failed["code"] == "launch_failed" and "code 2" in failed["error"]
    assert "the plugin did not build" in failed["log"]
    assert (await get(page, "app"))["client"]["failure"] == failed
    closed = await desk(address=local(upstream.port), client=game.client(QUIET))
    failed = await play(closed, "start", expect=502)
    assert failed["code"] == "launch_failed" and "stay open" in failed["error"]
    assert "nothing to do" in failed["log"]
    slow = await desk(
        address=local(upstream.port), client=game.client(SLEEPS, launch_timeout=0.5)
    )
    assert (await play(slow, "start", expect=502))["code"] == "launch_timeout"
    absent = GameClient(ROOT, launcher=[str(ROOT / "no-such-shell")], tasks=game.tasks)
    gone = await desk(address=local(upstream.port), client=absent)
    assert (await play(gone, "start", expect=502))["code"] == "launch_failed"


async def test_a_game_that_ignores_the_request_to_close_is_ended(
    desk, tmp_path, upstream
):
    stubborn = Game(tmp_path, "stubborn")
    page = await desk(address=local(upstream.port), client=stubborn.client())
    await play(page, "start")
    assert (await play(page, "stop"))["client"]["state"] == "stopped"
    assert stubborn.ended == [(PID, False), (PID, True)]
    immortal = Game(tmp_path, "immortal")
    page = await desk(address=local(upstream.port), client=immortal.client())
    await play(page, "start")
    assert (await play(page, "stop", expect=502))["code"] == "close_timeout"
    client = (await get(page, "app"))["client"]
    assert client["state"] == "running" and client["failure"]["code"] == "close_timeout"


def test_running_games_are_read_from_the_task_list():
    rows = (
        f'"{EXECUTABLE}","1234","Console","1","600,000 K"\n'
        f'"{EXECUTABLE.upper()}","88","Console","1","1 K"\n'
        '"python.exe","5","Console","1","1 K"\n'
    )
    assert listed(rows) == [1234, 88]
    assert listed("INFO: No tasks are running.\n") == []
    assert listed("") == []
    assert isinstance(listed(tasklist()), list)


def test_the_pages_are_served_beside_the_window(tmp_path, game):
    server = GameServer(ROOT, tmp_path / "server.log", address=local(free_port()))
    site = Site(create_app(server, game.client(), tmp_path))
    url = site.open()
    try:
        with urlopen(url) as home:
            assert home.status == 200 and "<title>" in home.read().decode("utf-8")
        with urlopen(url + "api/app") as answer:
            assert b'"state": "stopped"' in answer.read()
    finally:
        site.close()
    with pytest.raises(OSError):
        urlopen(url, timeout=2)
    taken = socket.socket()
    taken.bind((HOST, 0))
    taken.listen()
    try:
        with pytest.raises(OSError):
            port = taken.getsockname()[1]
            Site(create_app(server, game.client(), tmp_path), port).open()
    finally:
        taken.close()
