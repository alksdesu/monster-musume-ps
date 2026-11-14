from ..core.context import Context
from ..core.sync import Result


def handshake(ctx: Context) -> Result:
    return Result({"token": ctx.runtime.credentials.handshake()})


async def issue_credential(ctx: Context) -> Result:
    payload = ctx.payload
    return Result(
        await ctx.runtime.credentials.issue(payload["provider"], payload["token"])
    )


async def _login(ctx: Context, login: str) -> Result:
    profile = ctx.runtime.data_links.resolve(login)
    await ctx.runtime.store.read(profile)
    ctx.session = ctx.runtime.sessions.create(profile, login)
    return Result({"access_token": ctx.session.token, "kdfp": ctx.session.password})


async def login_dmm(ctx: Context) -> Result:
    login = ctx.runtime.platform.authenticate(
        ctx.payload["id"], ctx.payload["ot"], ctx.payload["ap"]
    )
    return await _login(ctx, login)


async def login_credential(ctx: Context) -> Result:
    payload = ctx.payload
    login = await ctx.runtime.credentials.authenticate(
        payload["provider"], payload["id"], payload["psw"], payload["token"]
    )
    return await _login(ctx, login)


ROUTES = {
    "auth/handshake": handshake,
    "auth/credential/issue": issue_credential,
    "auth/login/credential": login_credential,
    "auth/login/dmm_gp": login_dmm,
}
