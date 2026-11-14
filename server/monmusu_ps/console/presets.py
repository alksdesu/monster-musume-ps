from dataclasses import dataclass
from datetime import datetime
import json

from ..core.masterdata import FULL_LAYOUT
from ..core.resources import FREE_COSTS
from .language import SETTINGS as SETTINGS_FILE


FOREVER = "2999/12/31 23:59:59"
FREE = "free_costs"
ALL_SUFFIX, PICK_SUFFIX = "（全部开放）", "（挑选开放）"
OPEN_ALL = "全部开放"


@dataclass(frozen=True)
class Opener:
    key: str
    group: str
    kind: str
    title: str
    blurb: str


@dataclass(frozen=True)
class Knob:
    key: str
    group: str
    path: str
    title: str
    blurb: str
    kind: str = "number"


# Operator settings that belong to a content group are turned from the group, not the raw file.
KNOBS = (
    Knob(
        "rate_ssr",
        "gacha",
        "gacha.rates.SSR",
        "SSR 出货率（%）",
        "四档加起来按比例算，不必正好 100。",
    ),
    Knob(
        "rate_sr",
        "gacha",
        "gacha.rates.SR",
        "SR 出货率（%）",
        "四档加起来按比例算，不必正好 100。",
    ),
    Knob(
        "rate_hr",
        "gacha",
        "gacha.rates.HR",
        "HR 出货率（%）",
        "四档加起来按比例算，不必正好 100。",
    ),
    Knob(
        "rate_r",
        "gacha",
        "gacha.rates.R",
        "R 出货率（%）",
        "四档加起来按比例算，不必正好 100。",
    ),
    Knob(
        "pickup_weight",
        "gacha",
        "gacha.pickup_weight",
        "pickup 权重",
        "同稀有度里 pickup 角色占的份量；越大越容易出 pickup。",
    ),
    Knob(
        "other_rate",
        "gacha",
        "gacha.other_rate",
        "非角色奖品占比（%）",
        "附赠道具一类奖品在每次抽取里占的比例。",
    ),
    Knob(
        "gacha_history",
        "gacha",
        "gacha.history",
        "抽卡历史条数",
        "游戏里能翻到的最近抽卡记录条数。",
    ),
    Knob(
        "stamina_cap",
        "quest",
        "stamina_max_capacity",
        "体力可持有上限",
        "回复和道具能把体力补到的最大值。",
    ),
    Knob(
        "drops_per_clear",
        "quest",
        "subskill_prizes.drops_per_clear",
        "每次通关副技能掉落数",
        "迷宫关卡每次通关掉的副技能个数。",
    ),
    Knob(
        "unit_capacity",
        "item",
        "resources.unit_capacity",
        "角色栏位上限",
        "存档里最多能放多少个角色。",
    ),
    Knob(
        "attachable_capacity",
        "item",
        "resources.attachable_capacity",
        "副技能仓库上限",
        "副技能最多持有多少个。",
    ),
    Knob(
        "potential_cap",
        "item",
        "resources.duplicate_unit.potential_cap",
        "潜能点上限",
        "重复角色能攒的潜能点上限。",
    ),
    Knob(
        "comeback_days",
        "login",
        "comeback.away_days",
        "回归活动触发天数",
        "离开多少天算回归；签到时一并开。",
    ),
    Knob(
        "comeback_with_login",
        "login",
        "comeback.with_login_bonus",
        "签到时直接开回归活动",
        "打开后没有回归记录的玩家签到即进第一期。",
        "flag",
    ),
    Knob(
        "bingo_draws",
        "event",
        "bingo.max_draws",
        "宾果每日抽取次数",
        "每天最多能抽几次宾果号。",
    ),
    Knob(
        "sugoroku_tickets",
        "event",
        "sugoroku.charge_tickets",
        "双六扣骰子券",
        "关掉后掷骰不扣券。",
        "flag",
    ),
    Knob(
        "recommend_limit",
        "notice",
        "social.recommend_limit",
        "推荐好友条数",
        "好友推荐列表一次给多少人。",
    ),
    Knob(
        "mosaic",
        "story",
        "client.mosaic",
        "剧情马赛克格子",
        "游戏运行时盖在 Spine 场景上的格子大小：1 原样，0.3 之类变细，0 去掉这一层（图里画好的码仍在）。重开游戏生效。",
    ),
)


@dataclass(frozen=True)
class Link:
    key: str
    group: str
    page: str
    title: str
    blurb: str


OPENERS = (
    Opener(
        "open_shop_tab",
        "shop",
        "shop_tab",
        "商店页签全部开放",
        "过期或还没开的商店页签全部开到永远。",
    ),
    Opener("open_goods", "shop", "goods", "商品全部上架", "每一件限时商品都开到永远。"),
    Opener(
        "open_gacha",
        "gacha",
        "gacha",
        "卡池全部开放",
        "结束了的和还没开始的卡池全部开到永远。",
    ),
    Opener(
        "open_mission", "mission", "mission", "任务全部开放", "限时任务全部开到永远。"
    ),
    Opener(
        "open_loginbonus",
        "login",
        "loginbonus",
        "签到表全部开放",
        "每一张签到表都开到永远。",
    ),
    Opener(
        "open_event",
        "event",
        "event",
        "活动全部开放",
        "活动列表里的每个活动都开到永远，连带它的关卡、任务和商店。",
    ),
    Opener("open_bingo", "event", "bingo", "宾果全部开放", "每一期宾果都开到永远。"),
    Opener(
        "open_sugoroku", "event", "sugoroku", "双六全部开放", "每一期双六都开到永远。"
    ),
    Opener(
        "open_secret",
        "event",
        "secret",
        "ヒミツ狩猎全部开放",
        "限时的ヒミツ分类全部开到永远。",
    ),
    Opener("open_vote", "event", "vote", "人气投票全部开放", "每一届投票都开到永远。"),
    Opener(
        "open_pass",
        "pass",
        "pass",
        "通行证与月票全部开放",
        "每一期通行证和月票都开到永远。",
    ),
    Opener("open_quest", "quest", "quest", "关卡全部开放", "限时关卡全部开到永远。"),
    Opener("open_banner", "notice", "banner", "横幅全部开放", "首页横幅全部开到永远。"),
    Opener(
        "open_announce", "notice", "announce", "公告图全部开放", "公告图全部开到永远。"
    ),
)
# One switch waives every price the game asks, so the groups that charge all show it.
FREE_GROUPS = ("shop", "gacha", "growth", "quest")
LINKS = (
    Link(
        "missions_all",
        "mission",
        "shortcuts",
        "任务全部达成",
        "在「一键」页按存档执行。",
    ),
    Link("quests_all", "quest", "shortcuts", "关卡全通", "在「一键」页按存档执行。"),
    Link(
        "stories_all",
        "story",
        "shortcuts",
        "剧情全开（含 H）",
        "在「一键」页按存档执行。",
    ),
    Link(
        "stories_read", "story", "shortcuts", "剧情全部已读", "在「一键」页按存档执行。"
    ),
    Link(
        "units_all", "story", "roster", "添加角色与换皮肤", "在「角色」页按存档操作。"
    ),
    Link(
        "units_max",
        "growth",
        "roster",
        "角色逐个调或全员拉满",
        "在「角色」页按存档操作。",
    ),
    Link("wallets_max", "item", "shortcuts", "货币拉满", "在「一键」页按存档执行。"),
    Link("items_max", "item", "shortcuts", "道具全满", "在「一键」页按存档执行。"),
    Link("skins_all", "item", "shortcuts", "皮肤全开", "在「一键」页按存档执行。"),
)


def _named(opener: Opener, suffix: str) -> str:
    return opener.title.removesuffix(OPEN_ALL) + suffix


class Presets:
    def __init__(self, runtime, files, planner):
        self.runtime = runtime
        self.files = files
        self.planner = planner
        self._moments: dict[str, int] = {}

    def _settings(self) -> dict:
        return json.loads(self.files.read(SETTINGS_FILE)["text"])

    @staticmethod
    def _knob(key: str) -> Knob:
        knob = next((entry for entry in KNOBS if entry.key == key), None)
        if knob is None:
            raise KeyError(f"Unknown preset: {key}")
        return knob

    def _knob_value(self, knob: Knob):
        holder = self.runtime.config.read()
        for step in knob.path.split("."):
            holder = holder[step]
        return holder

    def turn(self, knob: Knob, value) -> dict:
        wanted = bool if knob.kind == "flag" else (int, float)
        if type(value) is bool and knob.kind != "flag" or not isinstance(value, wanted):
            raise ValueError(
                f"{knob.title} takes a {'yes or no' if knob.kind == 'flag' else 'number'}"
            )
        if knob.kind != "flag" and value < 0:
            raise ValueError(f"{knob.title} cannot be negative")
        settings = self._settings()
        *steps, last = knob.path.split(".")
        holder = settings
        for step in steps:
            holder = holder[step]
        holder[last] = value
        self.files.write(
            SETTINGS_FILE, json.dumps(settings, ensure_ascii=False, indent=2)
        )
        return {"value": self._knob_value(knob)}

    def free(self) -> bool:
        return bool(self.runtime.config.read()["resources"].get(FREE_COSTS))

    def set_free(self, on: bool) -> bool:
        settings = self._settings()
        settings["resources"][FREE_COSTS] = on
        self.files.write(
            SETTINGS_FILE, json.dumps(settings, ensure_ascii=False, indent=2)
        )
        return self.free()

    @staticmethod
    def _opener(key: str) -> Opener:
        opener = next((entry for entry in OPENERS if entry.key == key), None)
        if opener is None:
            raise KeyError(f"Unknown preset: {key}")
        return opener

    def _today(self) -> str:
        moment = self.runtime.calendar.day_start(int(self.runtime.clock()))
        zone = self.runtime.masters.zone()
        return datetime.fromtimestamp(moment, zone).strftime(FULL_LAYOUT)

    # The same few dates stand on thousands of rows, so each is parsed once.
    def _moment(self, text: str) -> int:
        kept = self._moments.get(text)
        if kept is None:
            kept = self._moments[text] = self.runtime.masters.timestamp(text)
        return kept

    def _is_running(self, window: dict, now: int) -> bool:
        started = not window["start"] or self._moment(window["start"]) <= now
        return started and (not window["end"] or now < self._moment(window["end"]))

    def _running(self, kind: str) -> tuple[int, int]:
        now = int(self.runtime.clock())
        rows = self.runtime.schedule.entrances(kind, scheduled=True)
        return sum(self._is_running(row, now) for row in rows), len(rows)

    def _entries(self) -> list[dict]:
        return self.runtime.schedule.config.read()["activities"]

    def _entry(self, name: str) -> dict | None:
        return next((entry for entry in self._entries() if entry["name"] == name), None)

    # The entry is rewritten whole each time: it starts today and never ends.
    def _put(self, name: str, kind: str, ids: list) -> None:
        document = self.runtime.schedule.config.read()
        kept = [entry for entry in document["activities"] if entry["name"] != name]
        if ids:
            kept.append(
                {
                    "name": name,
                    "kind": kind,
                    "ids": sorted(ids, key=str),
                    "start": self._today(),
                    "end": FOREVER,
                    "enabled": True,
                }
            )
        self.planner.save({**document, "activities": kept})

    def open_all(self, key: str) -> dict:
        opener = self._opener(key)
        ids = [
            entrance["id"]
            for entrance in self.runtime.schedule.entrances(opener.kind)
            if entrance["end"]
        ]
        self._put(_named(opener, ALL_SUFFIX), opener.kind, ids)
        return {"opened": len(ids)}

    def close_all(self, key: str) -> dict:
        opener = self._opener(key)
        entry = self._entry(_named(opener, ALL_SUFFIX))
        if entry is not None:
            self._put(_named(opener, ALL_SUFFIX), opener.kind, [])
        return {"dropped": len(entry["ids"]) if entry else 0}

    def picked(self, key: str) -> list:
        entry = self._entry(_named(self._opener(key), PICK_SUFFIX))
        return list(entry["ids"]) if entry else []

    def pick(self, key: str, ids: list, on: bool) -> dict:
        opener = self._opener(key)
        if not isinstance(ids, list) or not ids:
            raise ValueError("ids has to be a list of entrance ids")
        known = {
            entrance["id"] for entrance in self.runtime.schedule.entrances(opener.kind)
        }
        unknown = [identity for identity in ids if identity not in known]
        if unknown:
            raise ValueError(f"No {opener.kind} with id {unknown[0]}")
        current = set(self.picked(key))
        chosen = current | set(ids) if on else current - set(ids)
        self._put(_named(opener, PICK_SUFFIX), opener.kind, list(chosen))
        return {"picked": len(chosen), "changed": len(chosen ^ current)}

    def items(self, key: str, query: str, status: str, offset: int, limit: int) -> dict:
        opener = self._opener(key)
        listed = self.planner.catalog(opener.kind, query, status, offset, limit)
        chosen = set(self.picked(key))
        for row in listed["rows"]:
            row["picked"] = row["id"] in chosen
        return listed

    def catalog(self) -> list[dict]:
        listed = []
        for opener in OPENERS:
            running, total = self._running(opener.kind)
            listed.append(
                {
                    "key": opener.key,
                    "group": opener.group,
                    "action": "open",
                    "title": opener.title,
                    "blurb": opener.blurb,
                    "running": running,
                    "total": total,
                    "opened": self._entry(_named(opener, ALL_SUFFIX)) is not None,
                    "picked": self.picked(opener.key),
                }
            )
        free = self.free()
        for group in FREE_GROUPS:
            listed.append(
                {
                    "key": FREE,
                    "group": group,
                    "action": "toggle",
                    "title": "消耗全免",
                    "blurb": "买东西、抽卡、养成、出击都不扣金币、材料、体力和券；存档里的数量照旧。",
                    "on": free,
                }
            )
        for knob in KNOBS:
            listed.append(
                {
                    "key": knob.key,
                    "group": knob.group,
                    "action": "knob",
                    "kind": knob.kind,
                    "title": knob.title,
                    "blurb": knob.blurb,
                    "value": self._knob_value(knob),
                }
            )
        for link in LINKS:
            listed.append(
                {
                    "key": link.key,
                    "group": link.group,
                    "action": "link",
                    "page": link.page,
                    "title": link.title,
                    "blurb": link.blurb,
                }
            )
        return listed

    def run(self, key: str, on, value=None) -> dict:
        if any(entry.key == key for entry in KNOBS):
            return self.turn(self._knob(key), value)
        if key == FREE:
            if type(on) is not bool:
                raise ValueError("free_costs takes on: true or false")
            return {"on": self.set_free(on)}
        return self.close_all(key) if on is False else self.open_all(key)
