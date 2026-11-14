from pathlib import Path
import re
from urllib.parse import urlsplit

from aiohttp import web


LOOPBACK = ("127.0.0.1", "localhost", "::1")
NAME = re.compile("[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
HOSTS = web.AppKey("hosts", frozenset)


def child(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(f"Not a plain file name: {name!r}")
    return directory / name


@web.middleware
async def local_only(request: web.Request, handler):
    # A page on another site may point its own host name at this address.
    if urlsplit(f"//{request.host}").hostname not in request.app[HOSTS]:
        raise web.HTTPForbidden(text="The console answers on its own address")
    if request.method != "GET":
        origin = request.headers.get("Origin")
        if origin is not None and urlsplit(origin).netloc != request.host:
            raise web.HTTPForbidden(text="Requests from other sites are refused")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="The console takes JSON")
    return await handler(request)


@web.middleware
async def failures(request: web.Request, handler):
    try:
        return await handler(request)
    except (LookupError, FileNotFoundError) as error:
        return web.json_response({"error": f"Not found: {error}"}, status=404)
    except (ValueError, TypeError, NotImplementedError) as error:
        return web.json_response({"error": str(error)}, status=400)
