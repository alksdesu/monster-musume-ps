from copy import deepcopy
from dataclasses import dataclass
from functools import partial
import hashlib
import json
import logging

from .masterdata import FULL_LAYOUT, exact


LOGGER = logging.getLogger("monmusu.schedule")
LAYOUT = FULL_LAYOUT
RAID = 4
EVENT_MISSIONS = 4
QUEST_MISSIONS = (1, 2, 29, 50, 51)
STORES = ("dmm", "play_store", "app_store")
VOTES = "votes"
ASSET_SUFFIX = ".asset"


@dataclass(frozen=True)
class Table:
    # None for rows only the server reads.
    row_class: str | None
    keys: tuple[str, ...]
    # Each date field maps to the field the client caches its parsed value in.
    dates: dict[str, str | None]
    # Field of the outer rows that holds the dated rows.
    nest: str | None = None

    def rows(self, outer: list[dict]) -> list[dict]:
        if self.nest is None:
            return outer
        return [row for holder in outer for row in holder[self.nest]]


PASS_DATES = {"start_dt": "m_startDt", "end_dt": "m_endDt", "close_dt": "m_closeDt"}
TABLES = {
    "sugoroku_data": Table(
        "cSugorokuData",
        ("stage_id",),
        {
            "start_date": "startDateTime",
            "end_date": "endDateTime",
            "close_date": "closeDateTime",
        },
    ),
    "event_list_data": Table(
        "cEventData", ("id",), {"open_time": None, "close_time": None}
    ),
    "everyones_killevent_data": Table(
        "cEveryonesKillEventData",
        ("dataId",),
        {"st_date": "StDate", "ed_date": "EdDate"},
    ),
    "bingo_data": Table(
        "cBingoData",
        ("id",),
        {"start_dt": "startDateTime", "end_dt": "endDateTime"},
    ),
    "quest_data": Table("cQuestData", ("id",), {"openTime": None, "closeTime": None}),
    "quest_secret_category_table": Table(
        "QuestSecretCategoryData",
        ("id",),
        {"start_at": "startDateTime", "end_at": "endDateTime"},
    ),
    "login_bonus": Table(
        "cLoginBonus", ("id",), {"start_dt": "startAt", "end_dt": "endAt"}
    ),
    "mission_data": Table(
        "cMissionData", ("id",), {"startDate": None, "endDate": None}
    ),
    "wallet_template": Table(
        "cWalletTemplate", ("category", "name"), {"expire_at": None}
    ),
    "shop_tab": Table(
        "cShopTab",
        ("id",),
        {"startDate": "startAt", "endDate": "endAt", "view_endDate": "viewEndAt"},
    ),
    "shop_data": Table(
        "cShopData", ("id",), {"startDate": "startAt", "endDate": "endAt"}
    ),
    "gacha_data": Table(
        "cGachaEnclosureData",
        ("id",),
        {"start_at": "startDate", "end_at": "endDate"},
        nest="enclosure",
    ),
    "banner_data": Table("cBannerData", ("id",), {"startAt": None, "endAt": None}),
    "bonus_pass_data": Table("cBonusPassData", ("id",), PASS_DATES),
    "season_pass_data": Table("cSeasonPassData", ("id",), PASS_DATES),
    "announce_image_data": Table(
        "cAnnounceImageData",
        ("id",),
        {"start_date": "startDateTime", "end_date": "endDateTime"},
    ),
    VOTES: Table(None, ("campaign_id", "round_no"), {"start_at": None, "end_at": None}),
}


@dataclass(frozen=True)
class Entrance:
    table: str
    key: str
    label: str
    start: str
    ends: tuple[str, ...]


ENTRANCES = {
    "sugoroku": Entrance(
        "sugoroku_data", "stage_id", "name", "start_date", ("end_date", "close_date")
    ),
    "bingo": Entrance("bingo_data", "id", "name", "start_dt", ("end_dt",)),
    "event": Entrance("event_list_data", "id", "name", "open_time", ("close_time",)),
    "quest": Entrance("quest_data", "id", "name", "openTime", ("closeTime",)),
    "secret": Entrance(
        "quest_secret_category_table", "id", "full_title", "start_at", ("end_at",)
    ),
    "banner": Entrance("banner_data", "id", "imagePath", "startAt", ("endAt",)),
    "loginbonus": Entrance("login_bonus", "id", "name", "start_dt", ("end_dt",)),
    "shop_tab": Entrance(
        "shop_tab", "id", "tab_name", "startDate", ("endDate", "view_endDate")
    ),
    "goods": Entrance("shop_data", "id", "name", "startDate", ("endDate",)),
    "mission": Entrance("mission_data", "id", "text", "startDate", ("endDate",)),
    "announce": Entrance(
        "announce_image_data", "id", "id", "start_date", ("end_date",)
    ),
}
PLAIN = ("banner", "loginbonus", "goods", "mission", "announce")


def _listed(identity, name, start: str, end: str) -> dict:
    return {"id": identity, "name": str(name), "start": start, "end": end}


def _moment(value) -> str:
    if value != "":
        if not isinstance(value, str):
            raise ValueError(f"A schedule time must be text: {value!r}")
        if not exact(value):
            raise ValueError(f"A schedule time must look like {LAYOUT}: {value!r}")
    return value


def _grants(mission: dict) -> set[str]:
    return {
        f"{reward['m_walletCategory']}.{reward['m_walletName']}"
        for reward in mission["rewardtable"]["m_rewards"]
    }


def _targets(mission: dict) -> set[int]:
    aimed = mission["category"] == EVENT_MISSIONS
    if not aimed or mission["condition"] not in QUEST_MISSIONS:
        return set()
    return {int(part) for part in mission["target"].split(",") if part.isdigit()}


class _Window:
    def __init__(self, entry: dict, timestamp):
        self.start = _moment(entry["start"]) if "start" in entry else None
        self.end = _moment(entry["end"]) if "end" in entry else None
        self._timestamp = timestamp

    def fields(self, row: dict, start: str | None, *ends: str) -> dict:
        fields = {}
        if start and self.start is not None:
            fields[start] = self.start
        if self.end is not None:
            # A side that never closed needs nothing to reopen it.
            fields.update((name, self.end) for name in ends if row[name])
        return fields

    def _outlasts(self, moment: str) -> bool:
        return not self.end or self._timestamp(moment) < self._timestamp(self.end)

    def extend(self, row: dict, *ends: str) -> dict:
        # Rows behind an entrance must last as long as it does and are never cut short.
        if self.end is None:
            return {}
        return {
            name: self.end for name in ends if row[name] and self._outlasts(row[name])
        }


class Schedule:
    def __init__(self, masters, config, settings):
        self.masters = masters
        self.config = config
        self.settings = settings
        self._sources: tuple = ()
        self._read: dict[str, dict] = {}
        self._changes: dict[str, list[dict]] = {}
        self._patched: dict[str, tuple[dict, list, dict]] = {}
        self.error: str | None = None
        self._kinds = {
            "sugoroku": self._sugoroku,
            "bingo": self._bingo,
            "event": self._event,
            "quest": self._quest,
            "secret": self._secret,
            "gacha": self._gacha,
            "pass": self._pass,
            "shop_tab": self._shop_tab,
            "vote": self._vote,
            **{kind: partial(self._plain, kind) for kind in PLAIN},
        }

    @property
    def kinds(self) -> list[str]:
        return list(self._kinds)

    def _document(self) -> dict:
        return self.config.read()

    def _outer(self, name: str, scheduled: bool = False) -> list[dict]:
        if name == VOTES:
            campaigns = self.settings.read()[VOTES]["campaigns"]
            return self.adjust(VOTES, campaigns) if scheduled else campaigns
        if scheduled:
            return self.masters.document(name)["table"]
        self._read[name] = self.masters.document(name, before=self)
        return self._read[name]["table"]

    def _table(self, name: str, scheduled: bool = False) -> list[dict]:
        outer = self._outer(name, scheduled)
        return TABLES[name].rows(outer) if name in TABLES else outer

    @staticmethod
    def _pick(rows: list[dict], name: str, **key) -> dict:
        for row in rows:
            if all(row[field] == value for field, value in key.items()):
                return row
        raise ValueError(f"The schedule names a missing {name} row: {key}")

    def _row(self, name: str, **key) -> dict:
        return self._pick(self._table(name), name, **key)

    def _wallet(self, category: str, name: str, window: _Window):
        for row in self._table("wallet_template"):
            if (row["category"], row["name"]) == (category, name):
                yield (
                    "wallet_template",
                    (category, name),
                    window.extend(row, "expire_at"),
                )

    def _banners(self, option: str, window: _Window):
        for banner in self._table("banner_data"):
            if option and banner["option"] == option:
                fields = window.fields(banner, "startAt", "endAt")
                yield "banner_data", (banner["id"],), fields

    def _missions(self, missions: list[dict], window: _Window):
        for mission in missions:
            yield "mission_data", (mission["id"],), window.extend(mission, "endDate")

    def _plain(self, kind: str, identity, window: _Window):
        entrance = ENTRANCES[kind]
        row = self._row(entrance.table, **{entrance.key: identity})
        fields = window.fields(row, entrance.start, *entrance.ends)
        yield entrance.table, (identity,), fields

    def _pass_term(self, row: dict, scheduled: bool) -> dict:
        seasons = self._table("season_pass_data", scheduled)
        dated = {season["pass_id"]: season for season in seasons}
        return row if row["end_dt"] else dated.get(row["id"], row)

    def _vote_term(self, rounds: list[dict]) -> tuple[str, str]:
        moments = self.masters.timestamp
        first = min(rounds, key=lambda row: moments(row["start_at"]))
        last = max(rounds, key=lambda row: moments(row["end_at"]))
        return first["start_at"], last["end_at"]

    def entrances(self, kind: str, scheduled: bool = False) -> list[dict]:
        if kind == "gacha":
            return [
                _listed(group["id"], pool["title"], pool["start_at"], pool["end_at"])
                for group in self._outer("gacha_data", scheduled)
                for pool in group["enclosure"][:1]
            ]
        if kind == "pass":
            return [
                _listed(
                    row["id"], row["display_name"], term["start_dt"], term["end_dt"]
                )
                for row in self._table("bonus_pass_data", scheduled)
                for term in (self._pass_term(row, scheduled),)
            ]
        if kind == "vote":
            campaigns: dict[int, list[dict]] = {}
            for row in self._table(VOTES, scheduled):
                campaigns.setdefault(row["campaign_id"], []).append(row)
            return [
                _listed(identity, rounds[0]["message"], *self._vote_term(rounds))
                for identity, rounds in campaigns.items()
            ]
        entrance = ENTRANCES[kind]
        return [
            _listed(
                row[entrance.key],
                row[entrance.label],
                row[entrance.start],
                row[entrance.ends[0]],
            )
            for row in self._table(entrance.table, scheduled)
        ]

    def _ids(self, entry: dict) -> list:
        if ("ids" in entry) == ("running_at" in entry):
            raise ValueError(
                f"A schedule entry takes either ids or running_at: {entry}"
            )
        if "ids" in entry:
            return entry["ids"]
        moment = self.masters.timestamp(_moment(entry["running_at"]))
        return [
            entrance["id"]
            for entrance in self.entrances(entry["kind"])
            # What never ends needs nothing to keep it running.
            if entrance["end"]
            and self.masters.in_term(entrance["start"], entrance["end"], moment)
        ]

    def _granting_sheets(self, category: str, name: str) -> list[dict]:
        groups = {
            group["dayData"][0]["id"]: group["dayData"]
            for group in self._table("login_bonus_cell")
        }
        return [
            sheet
            for sheet in self._table("login_bonus")
            if any(
                (reward["m_walletCategory"], reward["m_walletName"]) == (category, name)
                for cell in groups.get(sheet["table_id"], ())
                for reward in cell["rewards"]["m_rewards"]
            )
        ]

    def _sugoroku(self, stage_id: int, window: _Window):
        stage = self._row("sugoroku_data", stage_id=stage_id)
        yield from self._plain("sugoroku", stage_id, window)
        ticket = stage["consumption_category"], stage["consumption_name"]
        yield from self._wallet(*ticket, window)
        # Without its sign-in sheet a reopened stage has no source of tickets.
        for sheet in self._granting_sheets(*ticket):
            yield from self._plain("loginbonus", sheet["id"], window)

    def _bingo(self, bingo_id: int, window: _Window):
        sheet = self._row("bingo_data", id=bingo_id)
        yield from self._plain("bingo", bingo_id, window)
        for prefix in ("price", "dupli"):
            yield from self._wallet(
                sheet[f"{prefix}_category"], sheet[f"{prefix}_name"], window
            )

    def _event_missions(self, event: dict, quests: set[int]) -> list[dict]:
        groups = {
            group["id"]
            for group in self._table("mission_category_collective_data")
            if group["event_id"] == event["id"] or group["title"] == event["name"]
        }
        return [
            mission
            for mission in self._table("mission_data")
            if mission["category_collective"] in groups or quests & _targets(mission)
        ]

    def _event(self, event_id: int, window: _Window):
        event = self._row("event_list_data", id=event_id)
        yield from self._plain("event", event_id, window)
        chapters = {event["chapter"], *event["contain_chapters"]} - {0}
        quests = [
            quest for quest in self._table("quest_data") if quest["chapter"] in chapters
        ]
        # The client hides each stage by its own closing time, not the event's.
        for quest in quests:
            yield "quest_data", (quest["id"],), window.extend(quest, "closeTime")
        for tab in self._table("shop_tab"):
            if event["shop_id"] and tab["id"] == event["shop_id"]:
                fields = window.extend(tab, "endDate", "view_endDate")
                yield "shop_tab", (tab["id"],), fields
                yield from self._shop_rows(tab, window)
        if event["event_type"] == RAID:
            for raid in self._table("everyones_killevent_data"):
                if raid["dataId"] == event["event_dataTable_id"]:
                    fields = window.fields(raid, "st_date", "ed_date")
                    yield "everyones_killevent_data", (raid["dataId"],), fields
        yield from self._banners(event["tag"], window)
        if event["gacha_id"]:
            yield from self._gacha(event["gacha_id"], window)
        missions = self._event_missions(event, {quest["id"] for quest in quests})
        yield from self._missions(missions, window)
        points = set().union(*map(_grants, missions))
        for row in self._table("bonus_pass_data"):
            # Monthly passes are fed from everywhere and keep their own seasons.
            if row["ref_prog_point_rid"] in points and row["end_dt"]:
                yield from self._pass_rows(row, window)

    def _quest(self, quest_id: int, window: _Window):
        yield from self._plain("quest", quest_id, window)
        missions = [
            mission
            for mission in self._table("mission_data")
            if quest_id in _targets(mission)
        ]
        yield from self._missions(missions, window)

    def _secret(self, category_id: int, window: _Window):
        yield from self._plain("secret", category_id, window)
        for group in self._table("quest_secret_group_table"):
            if group["category_id"] == category_id:
                quest = self._row("quest_data", id=group["quest_id"])
                yield "quest_data", (quest["id"],), window.extend(quest, "closeTime")

    def _gacha(self, group_id: str, window: _Window):
        group = self._pick(self._outer("gacha_data"), "gacha_data", id=group_id)
        for pool in group["enclosure"]:
            yield "gacha_data", (pool["id"],), window.fields(pool, "start_at", "end_at")
            yield from self._wallet(pool["cost_type"], pool["cost_name"], window)
        yield from self._banners(group["enclosure_id"], window)

    def _shop_rows(self, tab: dict, window: _Window):
        for side in ("main", "sub"):
            yield from self._wallet(
                tab[f"{side}_draw_wallet_category"],
                tab[f"{side}_draw_wallet_name"],
                window,
            )
        for item in self._table("shop_data"):
            if item["tab_category"] == tab["id"]:
                yield "shop_data", (item["id"],), window.extend(item, "endDate")

    def _shop_tab(self, tab_id: int, window: _Window):
        yield from self._plain("shop_tab", tab_id, window)
        yield from self._shop_rows(self._row("shop_tab", id=tab_id), window)

    def _pass_rows(self, row: dict, window: _Window):
        # An undated pass runs on its season; a start of its own would detach it.
        start = "start_dt" if row["end_dt"] else None
        fields = window.fields(row, start, "end_dt", "close_dt")
        yield "bonus_pass_data", (row["id"],), fields
        for season in self._table("season_pass_data"):
            if season["pass_id"] == row["id"]:
                fields = window.fields(season, "start_dt", "end_dt", "close_dt")
                yield "season_pass_data", (season["id"],), fields
        goods = {row[f"ref_shop_id_{store}"] for store in STORES}
        for item in self._table("shop_data"):
            if item["id"] in goods:
                yield "shop_data", (item["id"],), window.extend(item, "endDate")

    def _pass(self, pass_id: int, window: _Window):
        row = self._row("bonus_pass_data", id=pass_id)
        yield from self._pass_rows(row, window)
        missions = [
            mission
            for mission in self._table("mission_data")
            if row["ref_prog_point_rid"] in _grants(mission)
        ]
        yield from self._missions(missions, window)

    def _vote(self, campaign_id: int, window: _Window):
        rounds = [
            row for row in self._table(VOTES) if row["campaign_id"] == campaign_id
        ]
        if not rounds:
            raise ValueError(f"The schedule names a missing vote: {campaign_id}")
        for row in rounds:
            fields = window.fields(row, "start_at", "end_at")
            yield VOTES, (campaign_id, row["round_no"]), fields

    def _direct(self, entry: dict):
        table = TABLES[entry["table"]]
        key, fields = entry["key"], entry["fields"]
        if tuple(key) != table.keys or not set(fields) <= set(table.dates):
            raise ValueError(f"The schedule row does not fit {entry['table']}: {entry}")
        self._row(entry["table"], **key)
        moments = {name: _moment(value) for name, value in fields.items()}
        yield entry["table"], tuple(key.values()), moments

    def _expand(self, document: dict) -> dict[str, list[dict]]:
        merged: dict[str, dict[tuple, dict]] = {}
        changes = []
        for entry in document["activities"]:
            if entry.get("enabled", True):
                window = _Window(entry, self.masters.timestamp)
                for identity in self._ids(entry):
                    changes.extend(self._kinds[entry["kind"]](identity, window))
        for entry in document["rows"]:
            changes.extend(self._direct(entry))
        for name, key, fields in changes:
            if fields:
                merged.setdefault(name, {}).setdefault(key, {}).update(fields)
        return {
            name: [
                {"key": dict(zip(TABLES[name].keys, key)), "fields": fields}
                for key, fields in rows.items()
            ]
            for name, rows in merged.items()
        }

    def preview(self, document: dict) -> dict[str, list[dict]]:
        return self._expand(document)

    def reload(self) -> None:
        sources = (
            self._document(),
            self.settings.read(),
            *(self.masters.document(name, before=self) for name in self._read),
        )
        if len(sources) == len(self._sources) and all(
            current is previous for current, previous in zip(sources, self._sources)
        ):
            return
        self._read = {}
        try:
            self._changes = self._expand(sources[0])
            self.error = None
        except (KeyError, TypeError, ValueError) as error:
            # A broken edit keeps the schedule that was last known to work.
            LOGGER.error("Schedule rejected, keeping the previous one: %r", error)
            self.error = f"{type(error).__name__}: {error}"
        self._sources = (*sources[:2], *self._read.values())

    def rows(self, name: str) -> list[dict]:
        # Master tables are read far too often to check the file on every read.
        if not self._sources:
            self.reload()
        return self._changes.get(name, [])

    def touched(self) -> list[str]:
        if not self._sources:
            self.reload()
        return sorted(
            name
            for name, rows in self._changes.items()
            if rows and TABLES[name].row_class
        )

    def _moved(self, name: str, rows: list[dict]) -> dict[int, dict]:
        keys = TABLES[name].keys
        index = {tuple(row[field] for field in keys): row for row in rows}
        moved = {}
        for change in self.rows(name):
            row = index.get(tuple(change["key"].values()))
            # A table replaced since the schedule was read may have lost the row.
            if row is not None:
                moved[id(row)] = change["fields"]
        return moved

    def apply(self, name: str, base: dict) -> dict:
        changes = self.rows(name) if name in TABLES else []
        if not changes:
            return base
        cached = self._patched.get(name)
        if cached is None or cached[0] is not base or cached[1] is not changes:
            document = deepcopy(base)
            rows = TABLES[name].rows(document["table"])
            moved = self._moved(name, rows)
            for row in rows:
                row.update(moved.get(id(row), {}))
            self._patched[name] = base, changes, document
        return self._patched[name][2]

    def adjust(self, name: str, rows: list[dict]) -> list[dict]:
        moved = self._moved(name, rows)
        return [{**row, **moved.get(id(row), {})} for row in rows]

    def payload(self) -> dict:
        self.reload()
        tables = [
            {
                "asset": f"{name}{ASSET_SUFFIX}",
                "nest": TABLES[name].nest,
                "rows": [
                    {
                        "key": row["key"],
                        "strings": row["fields"],
                        "caches": [
                            TABLES[name].dates[field]
                            for field in row["fields"]
                            if TABLES[name].dates[field]
                        ],
                    }
                    for row in rows
                ],
            }
            for name, rows in sorted(self._changes.items())
            if TABLES[name].row_class
        ]
        digest = hashlib.sha256(json.dumps(tables, sort_keys=True).encode()).hexdigest()
        return {"revision": digest[:16], "tables": tables}
