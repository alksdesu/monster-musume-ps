import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time

import aiohttp
from yarl import URL

from monmusu_ps.config import Settings

from .logs import tail
from .process import (
    NO_WINDOW,
    RUNNING,
    STARTING,
    STOPPED,
    STOPPING,
    Failure,
    Supervised,
)


API = "/console/api/"
WILDCARDS = ("0.0.0.0", "::")
LOCALHOST = "127.0.0.1"
# Windows keeps retrying a refused local connection for about two seconds.
CONNECT_TIMEOUT = 0.5
PROBE_TIMEOUT = aiohttp.ClientTimeout(total=5, sock_connect=CONNECT_TIMEOUT)
RELAY_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=CONNECT_TIMEOUT)
POLL = 0.25
START_TIMEOUT = 90
STOP_TIMEOUT = 20
TAIL_BYTES = 4096


class GameServer(Supervised):
    def __init__(
        self,
        root: Path,
        log: Path,
        saves: Path | None = None,
        *,
        command: list[str] | None = None,
        address: URL | None = None,
        start_timeout: float = START_TIMEOUT,
        stop_timeout: float = STOP_TIMEOUT,
    ):
        super().__init__()
        self.root = root
        self.log = log
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.address = address
        self.public_url = None if address is None else str(address)
        self._fixed = address is not None
        self._command = command or [
            sys.executable,
            *("-X", "utf8", "-u", "-m", "monmusu_ps.app", "--root", str(root)),
            *(("--saves", str(saves)) if saves else ()),
        ]
        self._process: subprocess.Popen | None = None
        self._http: aiohttp.ClientSession | None = None
        try:
            self._locate()
        except Failure as error:
            self.failure = error.payload()

    async def open(self) -> None:
        self._http = aiohttp.ClientSession(timeout=RELAY_TIMEOUT)

    async def close(self) -> None:
        await self._http.close()

    def _locate(self) -> None:
        if self._fixed:
            return
        try:
            settings = Settings.load(self.root)
        except (OSError, ValueError, KeyError) as error:
            raise Failure(
                "settings", f"server/data/settings.json is unreadable: {error!r}"
            ) from error
        host = LOCALHOST if settings.host in WILDCARDS else settings.host
        self.address = URL.build(scheme="http", host=host, port=settings.port)
        self.public_url = settings.public_url

    def request(self, method: str, path: str, **options):
        if self.address is None:
            raise aiohttp.ClientConnectionError("The address of the server is unknown")
        return self._http.request(method, self.address.with_path(API + path), **options)

    async def probe(self) -> dict | None:
        try:
            async with self.request("GET", "status", timeout=PROBE_TIMEOUT) as answer:
                return await answer.json() if answer.status == 200 else None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return None

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    async def state(self) -> dict:
        status = await self.probe()
        owned = self._alive()
        return {
            "state": self._phase or (RUNNING if status else STOPPED),
            "address": None if self.address is None else str(self.address),
            "owned": owned,
            "pid": self._process.pid if owned else None,
            "status": status,
            "failure": self.failure,
        }

    def _tail(self) -> str:
        try:
            return tail(self.log, TAIL_BYTES)
        except OSError:
            return ""

    def _spawn(self) -> None:
        source = str(self.root / "server")
        inherited = os.environ.get("PYTHONPATH")
        path = os.pathsep.join((source, inherited)) if inherited else source
        self.log.parent.mkdir(parents=True, exist_ok=True)
        try:
            # The server writes straight to the file so it can outlive this console.
            with self.log.open("ab") as stream:
                self._process = subprocess.Popen(
                    self._command,
                    cwd=self.root,
                    env={**os.environ, "PYTHONPATH": path},
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    creationflags=NO_WINDOW,
                )
        except OSError as error:
            raise Failure("start_failed", str(error)) from error

    async def _start(self) -> None:
        if await self.probe():
            return
        self._locate()
        self._phase = STARTING
        try:
            self._spawn()
            deadline = time.monotonic() + self.start_timeout
            while not await self.probe():
                code = self._process.poll()
                if code is not None:
                    raise Failure(
                        "start_failed",
                        f"The server left with code {code} while starting",
                        self._tail(),
                    )
                if time.monotonic() > deadline:
                    raise Failure(
                        "start_timeout", "The server is not answering yet", self._tail()
                    )
                await asyncio.sleep(POLL)
        finally:
            self._phase = None

    async def _ask_to_leave(self) -> None:
        try:
            async with self.request(
                "POST", "service", json={"action": "stop"}, timeout=PROBE_TIMEOUT
            ):
                pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass

    async def _stop(self) -> None:
        if not (self._alive() or await self.probe()):
            return
        self._phase = STOPPING
        try:
            await self._ask_to_leave()
            deadline = time.monotonic() + self.stop_timeout
            while self._alive() or await self.probe():
                if time.monotonic() > deadline:
                    if not self._alive():
                        raise Failure("stop_timeout", "The server did not stop in time")
                    # Only a server this console started can be ended from outside.
                    self._process.terminate()
                    await asyncio.to_thread(self._process.wait)
                await asyncio.sleep(POLL)
        finally:
            self._phase = None

    async def start(self) -> None:
        await self._run(self._start)

    async def stop(self) -> None:
        await self._run(self._stop)

    async def restart(self) -> None:
        await self._run(self._stop, self._start)
