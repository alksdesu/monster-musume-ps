import argparse
import inspect
import logging
from pathlib import Path
import sys

from aiohttp import web

from .config import Settings
from .console import CONSOLE, Folders, mount
from .console.service import LogRing
from .core.context import Context
from .core.errors import ApiConflict, ApiNotFound, ApiRejected
from .core.platform import PlatformError
from .core.sync import envelope
from .core.transport import (
    Protocol,
    ProtocolError,
    content_type,
    decode_request,
    encode_response,
)
from .runtime import Runtime


LOGGER = logging.getLogger("monmusu")
RUNTIME = web.AppKey("runtime", Runtime)
LOG_CAPACITY = 2000
SHIPPED_DATES = "shipped"


def api_error(http_status: int, code: int, message: str, data=None) -> web.Response:
    payload = {"status": {"code": code, "message": message}}
    if data is not None:
        payload["data"] = data
    return web.json_response(
        payload,
        status=http_status,
        headers={"x-ag-protocol": "1", "Cache-Control": "no-store"},
    )


async def dispatch(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME]
    runtime.registry.refresh()
    runtime.overrides.reload()
    runtime.schedule.reload()
    endpoint = request.match_info["endpoint"]
    contract = runtime.contracts.get(endpoint)
    if contract is None:
        raise web.HTTPNotFound(text=f"Unknown endpoint: {endpoint}")
    if request.method != contract["method"]:
        raise web.HTTPMethodNotAllowed(request.method, [contract["method"]])
    handler = runtime.registry.handlers.get(endpoint)
    if handler is None:
        LOGGER.error("Endpoint pending implementation: %s", endpoint)
        raise web.HTTPNotImplemented(
            text=f"Endpoint pending implementation: {endpoint}"
        )
    try:
        protocol = Protocol(int(request.headers.get("x-ag-protocol", "1")))
    except ValueError as error:
        raise web.HTTPBadRequest(text="Invalid x-ag-protocol") from error
    if protocol != contract["protocol"]:
        raise web.HTTPBadRequest(text="Protocol does not match the endpoint")
    session = None
    if contract["auth"]:
        try:
            session = runtime.sessions.get(request.headers.get("x-ag-token", ""))
        except KeyError:
            return api_error(401, 90200002, "Invalid session token")
    request_body = await request.read()
    try:
        decoded = decode_request(
            request_body, protocol, session.password if session else None
        )
    except ProtocolError as error:
        LOGGER.warning(
            "Rejected transport %s protocol=%s length=%s: %s",
            endpoint,
            int(protocol),
            len(request_body),
            error,
        )
        raise web.HTTPBadRequest(text=str(error)) from error
    context = Context(runtime, decoded.payload, None, session, int(runtime.clock()))
    try:
        if contract["auth"]:
            async with runtime.store.transaction(session.profile) as state:
                context.state = state
                result = handler(context)
                if inspect.isawaitable(result):
                    result = await result
                result.include_data = runtime.has_response_data(endpoint)
                result.sync = runtime.outbox.collect(session.profile, result.sync)
                body = encode_response(
                    envelope(result, context.now), decoded, session.password
                )
        else:
            result = handler(context)
            if inspect.isawaitable(result):
                result = await result
            result.include_data = runtime.has_response_data(endpoint)
            body = encode_response(envelope(result, context.now), decoded, None)
    except ApiRejected as error:
        LOGGER.warning("Rejected %s: %s", endpoint, error)
        if "422" not in contract["response_models"]:
            raise web.HTTPBadRequest(text=str(error)) from error
        return api_error(422, 90000006, str(error), {"rejected_reason": error.reason})
    except ApiNotFound as error:
        LOGGER.warning("Rejected %s: %s", endpoint, error)
        if "404" not in contract["response_models"]:
            raise web.HTTPBadRequest(text=str(error)) from error
        return api_error(404, 90000004, str(error), {"resources": error.resources})
    except ApiConflict as error:
        LOGGER.warning("Rejected %s: %s", endpoint, error)
        if "409" not in contract["response_models"]:
            raise web.HTTPBadRequest(text=str(error)) from error
        return api_error(409, 90000005, str(error), {"resources": error.resources})
    except NotImplementedError as error:
        LOGGER.error("Rule pending implementation in %s: %s", endpoint, error)
        raise web.HTTPNotImplemented(text=str(error)) from error
    except PlatformError as error:
        if error.code in (101, 204) and "422" in contract["response_models"]:
            return api_error(
                422,
                90000006,
                "Invalid platform one-time token",
                {"rejected_reason": "INVALID_GP_ONETIME_TOKEN"},
            )
        raise web.HTTPUnauthorized(text=str(error)) from error
    except PermissionError as error:
        raise web.HTTPUnauthorized(text=str(error)) from error
    except (KeyError, ValueError, StopIteration) as error:
        LOGGER.warning("Rejected %s: %s", endpoint, error)
        raise web.HTTPBadRequest(text=str(error)) from error
    headers = {
        "Content-Type": content_type(protocol),
        "x-ag-protocol": str(int(protocol)),
        "Cache-Control": "no-store",
    }
    if contract["auth"]:
        headers["x-ag-refreshtoken"] = runtime.sessions.refresh(session)
    LOGGER.info("%s %s -> 200 protocol=%s", request.method, endpoint, int(protocol))
    return web.Response(body=body, headers=headers)


async def master_patch(request: web.Request) -> web.Response:
    dated = request.query.get("dates") != SHIPPED_DATES
    body = request.app[RUNTIME].patch.encoded(dated)
    return web.Response(body=body, content_type="application/json")


async def localized_names(request: web.Request) -> web.Response:
    spoken = request.app[RUNTIME].localization
    return web.json_response({"names": spoken.names(), "cards": spoken.cards()})


async def localized_scene(request: web.Request) -> web.Response:
    lines = request.app[RUNTIME].localization.scene(request.match_info["sheet"])
    return web.json_response({"lines": lines})


async def health(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME]
    runtime.registry.refresh()
    return web.json_response(
        {
            "status": "ready",
            "implemented_endpoints": len(runtime.registry.handlers),
            "contract_endpoints": len(runtime.contracts),
        }
    )


async def platform_request(request: web.Request) -> web.Response:
    runtime = request.app[RUNTIME]
    try:
        payload = await request.json()
        command = payload["command"]
        identity, token = payload["viewer_id"], payload["access_token"]
        if request.path.endswith("/updateToken"):
            if command != "updatetoken":
                raise PlatformError(200, "Invalid token update command")
            value = runtime.platform.refresh(identity, token)
        elif command == "checklogin":
            value = runtime.platform.check_login(
                identity, token, payload["onetime_token"]
            )
        elif command in ("getprofile", "getpoint", "getchip"):
            value = runtime.platform.profile(identity, token)
        elif command == "payment":
            value = runtime.platform.profile(identity, token)
            value["payment_id"] = await runtime.order(
                identity, payload["itemId"], payload["quantity"]
            )
        else:
            raise PlatformError(200, "Unsupported platform command")
    except PlatformError as error:
        value = {"result_code": error.code}
    except (ValueError, LookupError, TypeError, NotImplementedError):
        value = {"result_code": 200}
    LOGGER.info("POST %s -> platform result=%s", request.path, value["result_code"])
    return web.json_response(value)


@web.middleware
async def gate(request: web.Request, handler):
    if request.path.startswith("/api/") and request.app[CONSOLE].service.paused:
        raise web.HTTPServiceUnavailable(text="The game service is paused")
    return await handler(request)


async def cleanup(app: web.Application) -> None:
    logging.getLogger().removeHandler(app[CONSOLE].service.ring)
    app[RUNTIME].registry.close()


async def battle_log_file(request: web.Request) -> web.Response:
    replay = request.app[RUNTIME].battle_logs.download(request.match_info["name"])
    if replay is None:
        raise web.HTTPNotFound(text="Unknown battle log")
    return web.Response(text=replay, content_type="text/plain", charset="utf-8")


def create_app(
    settings: Settings, clock=None, rng=None, folders: Folders | None = None
) -> web.Application:
    app = web.Application(middlewares=[gate])
    runtime = (
        Runtime(settings, rng=rng) if clock is None else Runtime(settings, clock, rng)
    )
    app[RUNTIME] = runtime
    ring = LogRing(LOG_CAPACITY)
    logging.getLogger().addHandler(ring)
    app[CONSOLE] = mount(app, runtime, ring, folders)
    app.on_cleanup.append(cleanup)
    app.router.add_get("/health", health)
    app.router.add_get("/masters/patch", master_patch)
    app.router.add_get("/localization/names", localized_names)
    app.router.add_get("/localization/scenes/{sheet}", localized_scene)
    app.router.add_get("/battle_log/{name}", battle_log_file)
    app.router.add_post("/api/sdk/request", platform_request)
    app.router.add_post("/api/sdk/updateToken", platform_request)
    app.router.add_route("*", "/api/{endpoint:.*}", dispatch)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--saves", type=Path)
    args = parser.parse_args()
    settings = Settings.load(args.root, args.saves)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    app = create_app(settings)
    # The game waits on this at its loading screen, so it is worked out before the door opens.
    app[RUNTIME].patch.encoded()
    web.run_app(app, host=settings.host, port=settings.port, access_log=None)
    sys.exit(app[CONSOLE].service.exit_code)


if __name__ == "__main__":
    main()
