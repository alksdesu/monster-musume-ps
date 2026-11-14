import argparse
import asyncio
import logging
from pathlib import Path
import threading

from aiohttp import web

from .client import GameClient
from .desk import create_app
from .game import LOCALHOST, GameServer


TITLE = "魔物娘TD 控制台"
SIZE = (1360, 900)
MINIMUM = (960, 640)
LOGGER = logging.getLogger("monmusu.console")


class Site:
    def __init__(self, application: web.Application, port: int = 0):
        self._application = application
        self._port = port
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._error: OSError | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing: asyncio.Event | None = None
        self.url = ""

    def open(self) -> str:
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            raise self._error
        return self.url

    def close(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._closing.set)
            self._thread.join()

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        runner = web.AppRunner(self._application, access_log=None)
        await runner.setup()
        try:
            try:
                await web.TCPSite(runner, LOCALHOST, self._port).start()
                self.url = f"http://{LOCALHOST}:{runner.addresses[0][1]}/"
                self._loop = asyncio.get_running_loop()
                self._closing = asyncio.Event()
            except OSError as error:
                self._error = error
                return
            finally:
                self._ready.set()
            await self._closing.wait()
        finally:
            await runner.cleanup()


def show(url: str) -> None:
    try:
        import webview
    except ImportError as error:
        raise SystemExit(
            "The window needs pywebview: python -m pip install pywebview"
        ) from error
    webview.create_window(
        TITLE, url, width=SIZE[0], height=SIZE[1], min_size=MINIMUM, text_select=True
    )
    webview.start()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--saves", type=Path)
    parser.add_argument(
        "--serve",
        type=int,
        metavar="PORT",
        help="serve the pages on this local port instead of opening a window",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=logs / "console.log",
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    game = GameServer(root, logs / "server.log", args.saves)
    site = Site(create_app(game, GameClient(root), logs), args.serve or 0)
    url = site.open()
    LOGGER.info("Console pages on %s", url)
    try:
        if args.serve is None:
            show(url)
        else:
            print(url, flush=True)
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        site.close()
