import asyncio
import csv
import io
from pathlib import Path
import re
import subprocess
import sys
import time

from .process import (
    NO_WINDOW,
    RUNNING,
    STARTING,
    STOPPED,
    STOPPING,
    Failure,
    Supervised,
)


EXECUTABLE = "monster_musume_tdx.exe"
LAUNCHER = "start-client.ps1"
VIEWER = re.compile("[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
SHELL = ("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File")
LAUNCH_TIMEOUT = 300
CLOSE_TIMEOUT = 15
POLL = 0.5
TAIL_CHARACTERS = 4000


def listed(output: str) -> list[int]:
    rows = csv.reader(io.StringIO(output))
    return [
        int(row[1]) for row in rows if len(row) > 1 and row[0].lower() == EXECUTABLE
    ]


def tasklist() -> str:
    if sys.platform != "win32":
        return ""
    done = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {EXECUTABLE}", "/FO", "CSV", "/NH"],
        capture_output=True,
        creationflags=NO_WINDOW,
    )
    # Its no-match notice comes in the system's own language and code page.
    return done.stdout.decode("ascii", "replace")


def taskkill(pid: int, force: bool) -> None:
    command = ["taskkill", "/PID", str(pid), *(("/F",) if force else ())]
    subprocess.run(command, capture_output=True, creationflags=NO_WINDOW)


def readable(output: bytes) -> str:
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError:
        return output.decode("mbcs" if sys.platform == "win32" else "latin-1")


class GameClient(Supervised):
    def __init__(
        self,
        root: Path,
        *,
        launcher: list[str] | None = None,
        tasks=tasklist,
        end=taskkill,
        launch_timeout: float = LAUNCH_TIMEOUT,
        close_timeout: float = CLOSE_TIMEOUT,
        poll: float = POLL,
    ):
        super().__init__()
        self.root = root
        self.launch_timeout = launch_timeout
        self.close_timeout = close_timeout
        self.poll = poll
        self._launcher = launcher or [*SHELL, str(root / LAUNCHER)]
        self._tasks = tasks
        self._end = end

    async def pids(self) -> list[int]:
        return listed(await asyncio.to_thread(self._tasks))

    async def state(self) -> dict:
        pids = await self.pids()
        return {
            "state": self._phase or (RUNNING if pids else STOPPED),
            "pids": pids,
            "failure": self.failure,
        }

    async def _launch(self, viewer: str, address: str, shipped_dates: bool) -> None:
        if await self.pids():
            return
        command = [*self._launcher, "-Profile", viewer, "-BaseUrl", address]
        if shipped_dates:
            command.append("-ShippedDates")
        self._phase = STARTING
        try:
            try:
                launcher = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=self.root,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    creationflags=NO_WINDOW,
                )
            except OSError as error:
                raise Failure("launch_failed", str(error)) from error
            try:
                output, _ = await asyncio.wait_for(
                    launcher.communicate(), self.launch_timeout
                )
            except asyncio.TimeoutError:
                launcher.kill()
                await launcher.wait()
                raise Failure(
                    "launch_timeout", "The launcher did not finish in time"
                ) from None
            log = readable(output)[-TAIL_CHARACTERS:]
            if launcher.returncode != 0:
                raise Failure(
                    "launch_failed",
                    f"The launcher left with code {launcher.returncode}",
                    log,
                )
            if not await self.pids():
                raise Failure("launch_failed", "The game did not stay open", log)
        finally:
            self._phase = None

    async def _end_all(self, pids: list[int], force: bool) -> None:
        for pid in pids:
            await asyncio.to_thread(self._end, pid, force)

    async def _close(self) -> None:
        pids = await self.pids()
        if not pids:
            return
        self._phase = STOPPING
        try:
            await self._end_all(pids, False)
            forced = False
            deadline = time.monotonic() + self.close_timeout
            while pids := await self.pids():
                if time.monotonic() > deadline:
                    if forced:
                        raise Failure("close_timeout", "The game did not close")
                    # The window ignored the request to close.
                    await self._end_all(pids, True)
                    forced = True
                    deadline = time.monotonic() + self.close_timeout
                await asyncio.sleep(self.poll)
        finally:
            self._phase = None

    @staticmethod
    def checked(viewer) -> str:
        if not isinstance(viewer, str) or not VIEWER.fullmatch(viewer):
            raise ValueError(f"Not a login name: {viewer!r}")
        return viewer

    async def start(self, viewer: str, address: str, shipped_dates: bool) -> None:
        viewer = self.checked(viewer)
        await self._run(lambda: self._launch(viewer, address, shipped_dates))

    async def stop(self) -> None:
        await self._run(self._close)

    async def restart(self, viewer: str, address: str, shipped_dates: bool) -> None:
        viewer = self.checked(viewer)
        await self._run(
            self._close, lambda: self._launch(viewer, address, shipped_dates)
        )
