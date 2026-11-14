import asyncio
import sys

from aiohttp import web


OBEDIENT = web.AppKey("obedient", bool)
LEAVE_DELAY = 0.1


def leave() -> None:
    raise web.GracefulExit()


async def status(request: web.Request) -> web.Response:
    return web.json_response({"paused": False, "stub": True})


async def service(request: web.Request) -> web.Response:
    body = await request.json()
    if body["action"] == "stop" and request.app[OBEDIENT]:
        asyncio.get_running_loop().call_later(LEAVE_DELAY, leave)
    return web.json_response({})


def main() -> None:
    port, manner = int(sys.argv[1]), sys.argv[2]
    print(f"stub server asked to be {manner}", flush=True)
    if manner == "broken":
        sys.exit(3)
    app = web.Application()
    app[OBEDIENT] = manner == "obedient"
    app.router.add_get("/console/api/status", status)
    app.router.add_post("/console/api/service", service)
    web.run_app(app, host="127.0.0.1", port=port, print=None, access_log=None)


if __name__ == "__main__":
    main()
