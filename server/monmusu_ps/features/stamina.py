from copy import deepcopy

from ..core.context import Context
from ..core.errors import ApiRejected
from ..core.sync import Result, diff_state


def _count(ctx: Context) -> int:
    count = ctx.payload["num"]
    if type(count) is not int or not 1 <= count <= 2147483647:
        raise ValueError("Restoration count must be a positive Int32")
    return count


def _stamina(state: dict) -> dict:
    return next(row for row in state["modules"]["stamina"] if row["section"] == "quest")


def _restore(ctx: Context, cost: dict, recovery: int) -> Result:
    before = deepcopy(ctx.state)
    ctx.runtime.progression.recover_stamina(ctx.state, ctx.now)
    stamina = _stamina(ctx.state)
    remaining = max(0, stamina["max_capacity"] - stamina["amount"])
    if recovery > remaining:
        raise ApiRejected("EXCESSIVE_STAMINA")
    ctx.runtime.resources.spend(ctx.state, [cost], ctx.now)
    ctx.runtime.resources.grant(
        ctx.state, [{"rid": "stamina.quest", "val": recovery}], ctx.now
    )
    return Result({"data": {"_z0": 0}}, sync=diff_state(before, ctx.state))


def restore_coin(ctx: Context) -> Result:
    count = _count(ctx)
    price = ctx.runtime.masters.property("Int", 18)
    return _restore(
        ctx,
        {"rid": "wallet.point.stone", "val": price * count},
        _stamina(ctx.state)["capacity"] * count,
    )


def restore_item(ctx: Context) -> Result:
    count = _count(ctx)
    potion = next(
        row
        for row in ctx.runtime.masters.table("wallet_template")
        if row["category"] == "potion" and row["name"] == ctx.payload["name"]
    )
    if potion["potion_type"] != "stamina":
        raise ValueError("The selected potion does not restore stamina")
    if potion["potion_eff_type"] == "A":
        recovery = potion["potion_eff_val"]
    elif potion["potion_eff_type"] == "P":
        recovery = _stamina(ctx.state)["capacity"] * (potion["potion_eff_val"] // 100)
    else:
        raise ValueError(f"Unknown potion effect: {potion['potion_eff_type']}")
    return _restore(
        ctx,
        {"rid": f"wallet.potion.{potion['name']}", "val": count},
        recovery * count,
    )


ROUTES = {
    "player/restore/stamina/coin": restore_coin,
    "player/restore/stamina/item": restore_item,
}
