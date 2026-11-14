from ..core.context import Context
from ..core.sync import Result


def set_token(ctx: Context) -> Result:
    payload = ctx.payload
    ctx.runtime.platform.set_ticket(payload["id"], payload["ot"], payload["ap"])
    return Result(include_data=False)


def update_token(ctx: Context) -> Result:
    payload = ctx.payload
    ticket = ctx.runtime.platform.update_ticket(
        payload["id"], payload["ot"], payload["ap"]
    )
    return Result({"ot": ticket})


ROUTES = {
    "auth/set_api_token/dmm_gp": set_token,
    "auth/update_api_token/dmm_gp": update_token,
}
