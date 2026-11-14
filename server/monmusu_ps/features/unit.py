from ..core.context import Context
from ..core.sync import Result


def enhance(ctx: Context) -> Result:
    return ctx.runtime.training.enhance(ctx.state, ctx.payload, ctx.now)


def enhance_all(ctx: Context) -> Result:
    return ctx.runtime.training.enhance_all(ctx.state, ctx.payload, ctx.now)


def upgrade(ctx: Context) -> Result:
    return ctx.runtime.training.upgrade(ctx.state, ctx.payload, ctx.now)


def awaken(ctx: Context) -> Result:
    return ctx.runtime.training.awaken(ctx.state, ctx.payload, ctx.now)


def enhance_exskill(ctx: Context) -> Result:
    return ctx.runtime.training.enhance_exskill(ctx.state, ctx.payload, ctx.now)


def forge(ctx: Context) -> Result:
    return ctx.runtime.training.forge(ctx.state, ctx.payload, ctx.now)


def protect(ctx: Context) -> Result:
    return ctx.runtime.training.protect(ctx.state, ctx.payload, ctx.now)


def discard(ctx: Context) -> Result:
    return ctx.runtime.training.discard(ctx.state, ctx.payload, ctx.now)


def equip_gear(ctx: Context) -> Result:
    return ctx.runtime.armory.equip_gear(ctx.state, ctx.payload, ctx.now)


def attach_skill(ctx: Context) -> Result:
    return ctx.runtime.armory.attach_skill(ctx.state, ctx.payload, ctx.now)


def detach_skill(ctx: Context) -> Result:
    return ctx.runtime.armory.detach_skill(ctx.state, ctx.payload, ctx.now)


def attach_charm(ctx: Context) -> Result:
    return ctx.runtime.armory.attach_charm(ctx.state, ctx.payload, ctx.now)


def skins(ctx: Context) -> Result:
    return Result({"skins": ctx.runtime.armory.skins(ctx.state)})


def equip_skin(ctx: Context) -> Result:
    return ctx.runtime.armory.equip_skin(ctx.state, ctx.payload, ctx.now)


def equip_exskill(ctx: Context) -> Result:
    return ctx.runtime.armory.equip_exskill(ctx.state, ctx.payload, ctx.now)


ROUTES = {
    "unit/enhancement": enhance,
    "unit/enhancement/all": enhance_all,
    "unit/upgrade": upgrade,
    "unit/awakening": awaken,
    "unit/enhancement/exskill": enhance_exskill,
    "unit/enhancement/uniqueWeapon": forge,
    "unit/protection": protect,
    "unit/discard": discard,
    "unit/gear/equip": equip_gear,
    "unit/skill/attach": attach_skill,
    "unit/skill/detach": detach_skill,
    "unit/charm/attach": attach_charm,
    "skin/all": skins,
    "unit/skin/equip": equip_skin,
    "unit/exskill/equip": equip_exskill,
}
