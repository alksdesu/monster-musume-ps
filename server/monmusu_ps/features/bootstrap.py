from ..core.context import Context
from ..core.sync import Result


def endpoint(ctx: Context) -> Result:
    return Result({"gapi_ep": ctx.runtime.settings.public_url})


def asset_version(ctx: Context) -> Result:
    return Result({"version": ctx.runtime.config.read()["asset_version"]})


def maintenance(ctx: Context) -> Result:
    return Result({"mode": ctx.runtime.config.read()["maintenance_mode"]})


def micro_maintenance(ctx: Context) -> Result:
    return Result({"micro_maintenance_list": []})


ROUTES = {
    "appenv/ep-conf": endpoint,
    "asset_bundle/version": asset_version,
    "maintenance/check": maintenance,
    "maintenance/micro/check": micro_maintenance,
}
