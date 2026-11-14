from pathlib import Path

import aiohttp
from aiohttp import web

from monmusu_ps.access import HOSTS, LOOPBACK, child, failures, local_only

from .client import GameClient
from .game import GameServer
from .logs import listing, tail
from .process import Failure


STATIC = Path(__file__).with_name("static")
TAIL_BYTES = 65536
ACTIONS = ("start", "stop", "restart")
DEFAULT_VIEWER = "local"
OFFLINE = {"code": "offline", "error": "The game server is not running"}
GAME = web.AppKey("game", GameServer)
CLIENT = web.AppKey("client", GameClient)
LOGS = web.AppKey("logs", Path)


async def page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


async def _state(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "server": await request.app[GAME].state(),
            "client": await request.app[CLIENT].state(),
        }
    )


async def _order(request: web.Request) -> dict:
    body = await request.json()
    if not isinstance(body, dict) or body.get("action") not in ACTIONS:
        raise ValueError(f"The request needs an action: {', '.join(ACTIONS)}")
    return body


async def control_server(request: web.Request) -> web.Response:
    order = await _order(request)
    try:
        await getattr(request.app[GAME], order["action"])()
    except Failure as error:
        return web.json_response(error.payload(), status=502)
    return await _state(request)


async def control_client(request: web.Request) -> web.Response:
    order = await _order(request)
    game, client = request.app[GAME], request.app[CLIENT]
    try:
        if order["action"] == "stop":
            await client.stop()
        else:
            viewer = client.checked(order.get("viewer", DEFAULT_VIEWER))
            # The game has nothing to talk to until its server answers.
            await game.start()
            await getattr(client, order["action"])(
                viewer, game.public_url, order.get("shipped_dates") is True
            )
    except Failure as error:
        return web.json_response(error.payload(), status=502)
    return await _state(request)


async def log_files(request: web.Request) -> web.Response:
    return web.json_response({"files": listing(request.app[LOGS])})


async def log_file(request: web.Request) -> web.Response:
    if "name" not in request.query:
        raise ValueError("The request needs ?name=")
    path = child(request.app[LOGS], request.query["name"])
    return web.json_response({"text": tail(path, TAIL_BYTES)})


async def relay(request: web.Request) -> web.Response:
    options = {"params": request.query}
    if request.can_read_body:
        options["data"] = await request.read()
        options["headers"] = {"Content-Type": request.content_type}
    try:
        async with request.app[GAME].request(
            request.method, request.match_info["path"], **options
        ) as answer:
            return web.Response(
                status=answer.status,
                body=await answer.read(),
                content_type=answer.content_type,
                charset=answer.charset,
            )
    except aiohttp.ClientConnectionError:
        return web.json_response(OFFLINE, status=503)


def create_app(game: GameServer, client: GameClient, logs: Path) -> web.Application:
    app = web.Application(middlewares=[local_only, failures])
    app[HOSTS] = frozenset(LOOPBACK)
    app[GAME] = game
    app[CLIENT] = client
    app[LOGS] = logs

    async def session(_: web.Application):
        await game.open()
        yield
        await game.close()

    app.cleanup_ctx.append(session)
    app.router.add_get("/", page)
    app.router.add_static("/static", STATIC)
    app.router.add_get("/api/app", _state)
    app.router.add_post("/api/app/server", control_server)
    app.router.add_post("/api/app/client", control_client)
    app.router.add_get("/api/logs/files", log_files)
    app.router.add_get("/api/logs/file", log_file)
    # Everything else is answered by the game server itself.
    app.router.add_route("*", "/api/{path:.+}", relay)
    return app
