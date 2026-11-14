from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import uuid

from ..core.journal import QUEST_COMPLETE
from ..core.loginbonus import UNLOCK_PREFIX, holds
from ..core.progression import player_rank
from ..core.story import SOURCES as STORY_TABLES
from .editing import Editing
from .roster import ASPECTS, PLAYABLE, SKIN_PREFIX, Roster


SOURCE = "console.shortcut"
# The game reads bit 1 as a full rating earned without a support unit.
FULL_RATING, SELF_CLEAR = 1, 2
BOM_PREFIX = "bom.card."
SUB_SKILL = "skill.attachable."
# The stone wallet is the free and paid coins counted together; an instant one is spent as it arrives.
UNHELD = frozenset({("point", "stone"), ("instant", "pexp"), ("instant", "stamina")})
PLAYER_EXP = 10**9


@dataclass(frozen=True)
class Shortcut:
    key: str
    title: str
    blurb: str
    touches: str


CATALOG = (
    Shortcut(
        "tutorial_done", "跳过教程", "把教程标成已完成，首页所有入口打开。", "玩家"
    ),
    Shortcut(
        "units_all",
        "角色全开",
        "把图鉴里的每一个可获得角色都放进存档，已有的不动。",
        "角色",
    ),
    Shortcut(
        "units_max",
        "全员拉满",
        "每个角色：觉醒到顶、等级到顶、EX 技能全部开启并到顶、专武到顶、潜能点拉满。",
        "角色",
    ),
    Shortcut("boms_all", "召唤物全开", "把所有召唤物放进存档，已有的不动。", "召唤物"),
    Shortcut(
        "skins_all",
        "皮肤全开",
        "发放每个角色的全部皮肤道具；换装到角色页里挑。",
        "道具",
    ),
    Shortcut(
        "stories_all",
        "剧情全开（含 H）",
        "角色全开、皮肤全开，把每个角色的等级拉到剧情要求，并把全部剧情登记为可看。",
        "角色、道具、剧情记录",
    ),
    Shortcut(
        "stories_read",
        "剧情全部已读",
        "把全部剧情标记为已读，图鉴与画廊里的 CG 全部点亮；剧情奖励不发。",
        "剧情记录",
    ),
    Shortcut(
        "quests_all",
        "关卡全通",
        "每一关都按三星首通登记，顺带触发首通解锁的商店、剧情和任务。",
        "关卡记录、事件",
    ),
    Shortcut(
        "missions_all",
        "任务全部达成",
        "把现在开放的每个任务都标成已达成，奖励到游戏的任务页里领。",
        "任务记录",
    ),
    Shortcut(
        "wallets_max", "货币拉满", "金币、钻石、各种点数和奖章全部补到上限。", "货币"
    ),
    Shortcut(
        "items_max",
        "道具全满",
        "素材、EX 技能钥匙、配方、装备、皮肤、副技能全部补到持有上限。",
        "道具、装备、副技能",
    ),
    Shortcut(
        "passes_all",
        "通行证付费轨全解锁",
        "现在在期的每一期通行证和月票都按已购买处理，付费轨奖励可领。",
        "通行证记录",
    ),
    Shortcut(
        "sheets_all",
        "付费签到表全解锁",
        "需要购买才能签的签到表全部按已解锁处理。",
        "签到记录",
    ),
    Shortcut("stamina_full", "体力回满", "把体力补到可持有的最大值。", "体力"),
    Shortcut("player_max", "玩家满级", "玩家等级升到顶，体力上限随之提高。", "玩家"),
    Shortcut(
        "everything", "全部一次做完", "按上面的顺序把每一项都做一遍。", "以上全部"
    ),
)
ORDER = tuple(entry.key for entry in CATALOG if entry.key != "everything")


class Shortcuts:
    def __init__(self, runtime, roster: Roster, keep: Callable[[str], Awaitable[str]]):
        self.runtime = runtime
        self.roster = roster
        self.keep = keep
        self.editing = Editing(runtime)

    def catalog(self) -> list[dict]:
        return [entry.__dict__ for entry in CATALOG]

    # Counts the operator sees next to each shortcut, so the effect of pressing it is plain.
    async def status(self, profile: str) -> dict:
        runtime = self.runtime
        state = await runtime.store.read(self.editing.known(profile))
        masters, now = runtime.masters, int(runtime.clock())
        books = self.roster.books()
        playable = [
            card for card in masters.assets("unitcard") if card["type"] == PLAYABLE
        ]
        owned = {unit["card_id"] for unit in state["units"]}
        stories = self._stories()
        recorded = {row["id"]: row for row in state["stories"]}
        quests = masters.table("quest_data")
        cleared = {row["quest_id"] for row in state["quest_records"] if row["wins"] > 0}
        wallets = self._wallets()
        # Counts read the save directly; asking the resource module for each row would check its tables every time.
        held = {(row["category"], row["name"]): row["val"] for row in state["wallets"]}
        full = sum(
            held.get((row["category"], row["name"]), 0) >= row["val_cap"]
            for row in wallets
        )
        skins = self._skins()
        items = {row["id"]: row["val"] for row in state["items"]}
        held_skins = sum(items.get(rid, 0) > 0 for rid in skins)
        stamina = next(
            row for row in state["modules"]["stamina"] if row["section"] == "quest"
        )
        ranks = masters.table("player_rank_info_table")
        boms = {row["card_id"] for row in state["boms"]}
        return {
            "units": {
                "owned": len(owned & {card["id"] for card in playable}),
                "total": len(playable),
            },
            "units_filled": {
                "done": sum(
                    self.roster.topped(self.roster.describe(unit, books))
                    for unit in state["units"]
                ),
                "total": len(state["units"]),
            },
            "boms": {"owned": len(boms), "total": len(masters.assets("bomcard"))},
            "skins": {"held": held_skins, "total": len(skins)},
            "stories": {
                "open": sum(identity in recorded for identity in stories),
                "read": sum(
                    recorded[identity]["is_already_read"]
                    for identity in stories
                    if identity in recorded
                ),
                "total": len(stories),
            },
            "missions": {
                "done": sum(row["status"] > 0 for row in state["missions"]),
                "total": runtime.missions.open_count(state, int(runtime.clock())),
            },
            "quests": {
                "cleared": len(cleared & {row["id"] for row in quests}),
                "total": len(quests),
            },
            "wallets": {"full": full, "total": len(wallets)},
            "passes": {
                "done": sum(
                    not runtime.resources.redeemable(state, rid, now)
                    for rid in self._pass_unlocks(now)
                ),
                "total": len(self._pass_unlocks(now)),
            },
            "sheets": {
                "done": sum(
                    holds(state["login_bonus"], int(rid.removeprefix(UNLOCK_PREFIX)))
                    for rid in self._sheet_unlocks()
                ),
                "total": len(self._sheet_unlocks()),
            },
            "stamina": {"amount": stamina["amount"], "max": stamina["max_capacity"]},
            "player": {"rank": player_rank(state), "max": len(ranks)},
            "tutorial": runtime.tutorial.finished(state),
        }

    def _wallets(self) -> list[dict]:
        return [
            row
            for row in self.runtime.masters.table("wallet_template")
            if row["val_cap"] > 0 and (row["category"], row["name"]) not in UNHELD
        ]

    def _stories(self) -> dict[str, dict]:
        masters = self.runtime.masters
        return {
            f"story.{row['story_category']}.{row['story_id']}.{row['id']}": row
            for table in STORY_TABLES
            for row in masters.table(table)
        }

    def _skins(self) -> list[str]:
        resources = self.runtime.resources
        known = set(resources.rids())
        rids = (
            f"{SKIN_PREFIX}{row['unit_card_id']}_{row['skin_id']}"
            for row in self.runtime.masters.table("unit_skin_data")
            if row["skin_id"]
        )
        return [rid for rid in dict.fromkeys(rids) if rid in known]

    def _grant_missing(self, state: dict, rids, now: int) -> int:
        resources, given = self.runtime.resources, 0
        for rid in rids:
            if resources.balance(state, rid) <= 0:
                resources.grant(
                    state,
                    [{"rid": rid, "val": 1}],
                    now,
                    policy="discard",
                    source=SOURCE,
                )
                given += 1
        return given

    def units_all(self, state: dict, now: int) -> dict:
        cards = [
            card["id"]
            for card in self.runtime.masters.assets("unitcard")
            if card["type"] == PLAYABLE
        ]
        return {"added": self.roster.give(state, cards, now)}

    def units_max(self, state: dict, now: int) -> dict:
        filled = sum(
            bool(self.roster.fill(state, unit, ASPECTS, now)) for unit in state["units"]
        )
        return {"filled": filled}

    def boms_all(self, state: dict, now: int) -> dict:
        owned = {row["card_id"] for row in state["boms"]}
        rids = [
            f"{BOM_PREFIX}{card['id']}"
            for card in self.runtime.masters.assets("bomcard")
            if card["id"] not in owned
        ]
        for rid in rids:
            self.runtime.resources.grant(
                state, [{"rid": rid, "val": 1}], now, policy="discard", source=SOURCE
            )
        return {"added": len(rids)}

    def skins_all(self, state: dict, now: int) -> dict:
        return {"given": self._grant_missing(state, self._skins(), now)}

    def stories_all(self, state: dict, now: int) -> dict:
        added = self.units_all(state, now)["added"]
        skins = self.skins_all(state, now)["given"]
        needed: dict[int, int] = {}
        for row in self.runtime.masters.table("story_data_unit"):
            card_id = int(row["story_id"])
            needed[card_id] = max(needed.get(card_id, 0), row["unlockLevel"])
        raised = 0
        for unit in state["units"]:
            wanted = needed.get(unit["card_id"], 0)
            if unit["r_unit_lv"] >= wanted:
                continue
            card = self.runtime.masters.asset("unitcard", unit["card_id"])
            # A rank gives the room the level needs; the lowest one that does is enough.
            for rank in range(unit["rank"], self.roster.rank_cap(card) + 1):
                self.roster.apply(state, unit, {"rank": rank}, now)
                if self.runtime.progression.level_cap(unit) >= wanted:
                    break
            self.roster.apply(
                state,
                unit,
                {"level": min(wanted, self.runtime.progression.level_cap(unit))},
                now,
            )
            raised += 1
        recorded = {row["id"] for row in state["stories"]}
        opened = [identity for identity in self._stories() if identity not in recorded]
        state["stories"].extend(
            {"id": identity, "is_already_read": False} for identity in opened
        )
        return {"added": added, "skins": skins, "raised": raised, "opened": len(opened)}

    def stories_read(self, state: dict, now: int) -> dict:
        recorded = {row["id"]: row for row in state["stories"]}
        marked = 0
        for identity in self._stories():
            record = recorded.get(identity)
            if record is None:
                state["stories"].append({"id": identity, "is_already_read": True})
                marked += 1
            elif not record["is_already_read"]:
                record["is_already_read"] = True
                marked += 1
        return {"marked": marked}

    def quests_all(self, state: dict, now: int) -> dict:
        runtime = self.runtime
        maximum = runtime.masters.property("Int", 41)
        records = {row["quest_id"]: row for row in state["quest_records"]}
        cleared = 0
        for quest in runtime.masters.table("quest_data"):
            record = records.get(quest["id"])
            if record is not None and record["wins"] > 0:
                continue
            if record is None:
                record = runtime.battles.new_record(quest)
                state["quest_records"].append(record)
                records[quest["id"]] = record
            record.update(wins=1, tries=record["tries"] + 1, record_rating=maximum)
            record["flags"] |= FULL_RATING | SELF_CLEAR
            runtime.journal.record(
                state,
                f"shortcut-{uuid.uuid4()}",
                QUEST_COMPLETE,
                now,
                {
                    "quest_id": quest["id"],
                    "category": quest["category"],
                    "section": quest["chapter"],
                    "rating": maximum,
                    "result": "COMPLETE",
                    "first_clear": True,
                    "costs": [],
                    "kills": 0,
                    "unit_ids": [],
                    "support_owner": None,
                    "support_card": None,
                },
            )
            cleared += 1
        return {"cleared": cleared}

    def missions_all(self, state: dict, now: int) -> dict:
        return {"achieved": self.runtime.missions.achieve_all(state, now)}

    def wallets_max(self, state: dict, now: int) -> dict:
        resources, filled = self.runtime.resources, 0
        for row in self._wallets():
            rid = f"wallet.{row['category']}.{row['name']}"
            short = row["val_cap"] - resources.balance(state, rid)
            if short > 0:
                resources.grant(
                    state,
                    [{"rid": rid, "val": short}],
                    now,
                    policy="discard",
                    source=SOURCE,
                )
                filled += 1
        return {"filled": filled}

    def items_max(self, state: dict, now: int) -> dict:
        resources, masters, filled = self.runtime.resources, self.runtime.masters, 0
        rids = [
            row["item_id"]
            for row in [*masters.table("item_template"), *masters.table("equipment")]
        ]
        rids.extend(rid for rid in resources.rids() if rid.startswith(SUB_SKILL))
        for rid in dict.fromkeys(rids):
            room = resources.room(state, rid, now)
            if room > 0:
                resources.grant(
                    state,
                    [{"rid": rid, "val": room}],
                    now,
                    policy="discard",
                    source=SOURCE,
                )
                filled += 1
        return {"filled": filled}

    def _pass_unlocks(self, now: int) -> list[str]:
        passes = self.runtime.resources.passes
        return [rid for rid in passes.unlock_rids() if passes.unlocked_by(rid, now)]

    def _sheet_unlocks(self) -> list[str]:
        return [
            rid
            for rid in self.runtime.resources.rids()
            if rid.startswith(UNLOCK_PREFIX)
        ]

    def passes_all(self, state: dict, now: int) -> dict:
        resources, unlocked = self.runtime.resources, 0
        for rid in self._pass_unlocks(now):
            if resources.redeemable(state, rid, now):
                resources.grant(
                    state,
                    [{"rid": rid, "val": 1}],
                    now,
                    policy="discard",
                    source=SOURCE,
                )
                unlocked += 1
        return {"unlocked": unlocked}

    def sheets_all(self, state: dict, now: int) -> dict:
        resources, unlocked = self.runtime.resources, 0
        for rid in self._sheet_unlocks():
            if not holds(state["login_bonus"], int(rid.removeprefix(UNLOCK_PREFIX))):
                resources.grant(
                    state,
                    [{"rid": rid, "val": 1}],
                    now,
                    policy="discard",
                    source=SOURCE,
                )
                unlocked += 1
        return {"unlocked": unlocked}

    def stamina_full(self, state: dict, now: int) -> dict:
        stamina = next(
            row for row in state["modules"]["stamina"] if row["section"] == "quest"
        )
        gained = stamina["max_capacity"] - stamina["amount"]
        if gained > 0:
            stamina["amount"] = stamina["max_capacity"]
            stamina["latest_recovered_at"] = now
        return {"gained": max(gained, 0)}

    def player_max(self, state: dict, now: int) -> dict:
        before = player_rank(state)
        self.runtime.progression.award_player_exp(state, PLAYER_EXP, now)
        return {"from": before, "to": player_rank(state)}

    def tutorial_done(self, state: dict, now: int) -> dict:
        tutorial = self.runtime.tutorial
        if tutorial.finished(state):
            return {"finished": False}
        tutorial.finish(state, now)
        return {"finished": True}

    def everything(self, state: dict, now: int) -> dict:
        return {key: getattr(self, key)(state, now) for key in ORDER}

    async def run(self, profile: str, key: str) -> dict:
        if key not in {entry.key for entry in CATALOG}:
            raise KeyError(f"Unknown shortcut: {key}")
        # A shortcut rewrites a lot at once, so the saves are copied before it runs.
        backup = await self.keep(key.replace("_", "-"))
        async with self.editing.edit(profile) as (state, outcome):
            outcome["result"] = getattr(self, key)(state, int(self.runtime.clock()))
            outcome["backup"] = backup
        return outcome
