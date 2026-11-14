from collections import deque
from copy import deepcopy

from .calendar import FOREVER
from .errors import ApiRejected
from .journal import (
    BATTLE_LOG_SHARE,
    BOM_ENHANCED,
    LOGIN,
    QUEST_COMPLETE,
    QUEST_CYCLE,
    SOCIAL_FOLLOW,
    SOCIAL_LENDING,
    TUTORIAL_PROGRESS,
    UNIT_ENHANCED,
)
from .progression import player_rank
from .resources import ResourceCapacityError
from .sync import Result, diff_state
from .tutorial import COMPLETE


DAY = 86400
CATEGORIES = range(1, 8)
DAILY = 2
IN_PROGRESS, ACHIEVED, RECEIVED = 0, 1, 2
ANY_CLEAR, QUEST_CLEAR, GROUP_CLEAR = 1, 2, 4
UNIT_LEVELS, EXSKILLS_ENHANCED, CLASS_CHANGES = 10, 11, 13
PLAYER_RANK, LOGIN_DAYS, BOM_SKILL_LEVEL = 14, 21, 34
JOB_LEVEL, MISSIONS_ACHIEVED, TUTORIAL_FINISHED, PLATFORM = 35, 36, 37, 70
PLAYERS_FOLLOWED, UNITS_LENT, LOG_SHARED = 32, 33, 38
LISTED_CLEAR, KILLS_BEST, KILLS_TOTAL = 29, 50, 51
CLASS_TEAM, RARITY_TEAM, ELEMENT_TEAM, SMALL_TEAM = 41, 42, 43, 71
TEAM_KINDS = frozenset({CLASS_TEAM, RARITY_TEAM, ELEMENT_TEAM, SMALL_TEAM})
TEAM_MARKERS = {
    CLASS_TEAM: "のみの編成",
    RARITY_TEAM: "以下の編成",
    ELEMENT_TEAM: "属性のみの編成",
}
QUEST_KINDS = frozenset(
    {
        ANY_CLEAR,
        QUEST_CLEAR,
        GROUP_CLEAR,
        UNIT_LEVELS,
        PLAYER_RANK,
        JOB_LEVEL,
        LISTED_CLEAR,
        KILLS_BEST,
        KILLS_TOTAL,
        *TEAM_KINDS,
    }
)
LOGIN_KINDS = frozenset({LOGIN_DAYS, TUTORIAL_FINISHED, PLATFORM})
TRAINING_KINDS = frozenset({UNIT_LEVELS, JOB_LEVEL, EXSKILLS_ENHANCED, CLASS_CHANGES})
QUEST_GROUPS = {
    "デイリー": 3,
    "イベント": 4,
    "ノーマルダンジョン": 6,
    "プレミアムダンジョン": 7,
}
PLAY_STORY = 1
INVALID_STATUS = "INVALID_MISSION_STATUS"
OVERFLOW = "ASSET_CAPACITY_OVERFLOW"
BAD_REWARD = "REWARD_DATA_ERROR"
SOURCES = ("mission_data", "mission_panel_content")


class Missions:
    def __init__(self, masters, calendar, resources, mailbox, stories, journal):
        self.masters = masters
        self.calendar = calendar
        self.resources = resources
        self.mailbox = mailbox
        self.stories = stories
        self._documents = ()
        self._rows: dict[int, dict] = {}
        self._windows: dict[int, tuple[int | None, int | None]] = {}
        self._categories: dict[int, list[dict]] = {}
        self._successors: dict[int, list[dict]] = {}
        self._collectives: list[dict] = []
        self._panels: dict[int, str] = {}
        journal.subscribe(QUEST_COMPLETE, self._on_quest_complete)
        journal.subscribe(QUEST_CYCLE, self._on_quest_cycle)
        journal.subscribe(TUTORIAL_PROGRESS, self._on_tutorial_progress)
        journal.subscribe(LOGIN, self._on_login)
        journal.subscribe(BATTLE_LOG_SHARE, self._on_log_share)
        journal.subscribe(SOCIAL_FOLLOW, self._on_follow)
        journal.subscribe(SOCIAL_LENDING, self._on_lending)
        journal.subscribe(UNIT_ENHANCED, self._on_unit_enhanced)
        journal.subscribe(BOM_ENHANCED, self._on_bom_enhanced)

    def _refresh(self) -> None:
        documents = tuple(self.masters.document(name) for name in SOURCES)
        if len(documents) == len(self._documents) and all(
            current is previous for current, previous in zip(documents, self._documents)
        ):
            return
        missions, contents = (document["table"] for document in documents)
        moments: dict[str, int | None] = {}

        def moment(text: str) -> int | None:
            if text not in moments:
                moments[text] = self.masters.timestamp(text) if text else None
            return moments[text]

        rows, windows, categories, successors = {}, {}, {}, {}
        for row in missions:
            if row["id"] in rows:
                raise ValueError(f"Duplicate mission {row['id']}")
            rows[row["id"]] = row
            windows[row["id"]] = (moment(row["startDate"]), moment(row["endDate"]))
            categories.setdefault(row["category"], []).append(row)
            if row["disclosureMission"]:
                successors.setdefault(row["disclosureMission"], []).append(row)
        for group in categories.values():
            group.sort(key=lambda row: str(row["id"]))
        panels = {}
        for content in contents:
            panels[content["category_collective"]] = content["period_id"]
            panels[content["list_category_collective"]] = content["period_id"]
        self._rows, self._windows, self._categories = rows, windows, categories
        self._successors, self._panels = successors, panels
        self._collectives = sorted(
            (row for row in rows.values() if row["condition"] == MISSIONS_ACHIEVED),
            key=self._order,
        )
        self._documents = documents

    @staticmethod
    def _order(row: dict) -> tuple:
        return row["condition"], str(row["id"])

    def _released(self, row: dict, now: int) -> bool:
        start, end = self._windows[row["id"]]
        return (start is None or start <= now) and (end is None or now < end)

    @staticmethod
    def _name(row: dict) -> str:
        return f"{row['category']}.{row['counterName']}"

    def _expiry(self, row: dict, now: int) -> int:
        if row["category"] == DAILY:
            return self.calendar.day_start(now) + DAY
        return FOREVER

    @staticmethod
    def _record(state: dict, mission_id) -> dict | None:
        return next((row for row in state["missions"] if row["id"] == mission_id), None)

    @staticmethod
    def _counter(state: dict, name: str) -> dict | None:
        return next(
            (row for row in state["mission_counters"] if row["name"] == name), None
        )

    def _job_system(self, card_id: int) -> int:
        card = self.masters.asset("unitcard", card_id)
        return self.masters.lookup("job_data", "id", card["classId"])["system_id"]

    def sweep(self, state: dict, now: int) -> None:
        self._refresh()
        today = self.calendar.day_start(now)
        state["missions"] = [
            record
            for record in state["missions"]
            if (row := self._rows.get(record["id"])) is None
            or row["category"] != DAILY
            or record["achieved_at"] >= today
        ]
        state["mission_counters"] = [
            row for row in state["mission_counters"] if now < row["expired_at"]
        ]

    def records(self, state: dict, category, now: int) -> dict:
        if type(category) is not int or category not in CATEGORIES:
            raise ValueError(f"Unknown mission category: {category}")
        self.sweep(state, now)
        records = {row["id"]: row for row in state["missions"]}
        counters = {row["name"]: row for row in state["mission_counters"]}
        record_list, counter_list = [], {}
        for row in self._categories.get(category, ()):
            if not self._released(row, now):
                continue
            record_list.append(
                records.get(row["id"])
                or {
                    "id": row["id"],
                    "status": IN_PROGRESS,
                    "updated_at": now,
                    "achieved_at": 0,
                }
            )
            if row["counterName"]:
                name = self._name(row)
                if name not in counter_list:
                    counter_list[name] = counters.get(name) or {
                        "name": name,
                        "val": 0,
                        "expired_at": self._expiry(row, now),
                    }
        return {"record_list": record_list, "counter_list": list(counter_list.values())}

    def _candidate(self, state: dict, row: dict, now: int, achieved: set) -> bool:
        if row["id"] in achieved or not self._released(row, now):
            return False
        parent = row["disclosureMission"]
        if parent and parent not in achieved:
            return False
        period_id = self._panels.get(row["category_collective"])
        if period_id is None:
            return True
        period = next((row for row in state["periods"] if row["id"] == period_id), None)
        return period is not None and now < period["period"]["end_at"]

    def _write_counter(self, state: dict, row: dict, value: int, now: int) -> None:
        name = self._name(row)
        counter = self._counter(state, name)
        if counter is None:
            if value:
                state["mission_counters"].append(
                    {"name": name, "val": value, "expired_at": self._expiry(row, now)}
                )
        elif counter["val"] != value:
            counter["val"] = value

    def _achieved_targets(self, state: dict, row: dict) -> int:
        achieved = {record["id"] for record in state["missions"]}
        return sum(
            int(target) in achieved for target in row["target"].split(",") if target
        )

    def _advance_counter(self, state: dict, row: dict, event: dict) -> int:
        counter = self._counter(state, self._name(row))
        current = counter["val"] if counter else 0
        kind = row["condition"]
        if kind == ANY_CLEAR:
            return current + event["clears"]
        if kind == GROUP_CLEAR:
            matched = QUEST_GROUPS.get(row["target"]) == event["quest_category"]
            return current + (event["clears"] if matched else 0)
        if kind == LISTED_CLEAR:
            listed = str(event["quest_id"]) in row["target"].split(",")
            return current + (event["clears"] if listed else 0)
        if kind == KILLS_TOTAL:
            matched = row["target"] == str(event["quest_id"])
            return current + (event["kills"] if matched else 0)
        if kind == LOGIN_DAYS:
            return current + event["logins"]
        if kind == UNIT_LEVELS:
            level = int(row["target"])
            return sum(unit["r_unit_lv"] >= level for unit in state["units"])
        if kind == MISSIONS_ACHIEVED:
            return self._achieved_targets(state, row)
        if kind == PLAYERS_FOLLOWED:
            return current + event["follows"]
        if kind == EXSKILLS_ENHANCED:
            return current + event["exskills"]
        if kind == CLASS_CHANGES:
            return current + event["class_changes"]
        if kind == UNITS_LENT:
            return sum(1 for value in state["lending"].values() if value)
        raise NotImplementedError(f"Mission condition {kind} is pending implementation")

    def _team_fits(self, row: dict, team: list[int]) -> bool:
        kind = row["condition"]
        if kind == SMALL_TEAM:
            return len(team) <= row["count"]
        # Only the mission text names the class, rarity or element it asks for.
        wanted = row["text"].split(TEAM_MARKERS[kind])[0]
        if kind == CLASS_TEAM:
            jobs = self.masters.table("job_data")
            system = next(
                (job["system_id"] for job in jobs if job["name"] == wanted), None
            )
            return all(self._job_system(card_id) == system for card_id in team)
        cards = [self.masters.asset("unitcard", card_id) for card_id in team]
        if kind == RARITY_TEAM:
            orders = {
                rarity["id"]: rarity["orderVal"]
                for rarity in self.masters.table("unit_rarity")
            }
            limit = next(
                (
                    rarity["orderVal"]
                    for rarity in self.masters.table("unit_rarity")
                    if rarity["name"] == wanted
                ),
                0,
            )
            return all(orders[card["rarityId"]] <= limit for card in cards)
        elements = self.masters.document("attr_list")["m_table"]
        element = next(
            (row["m_id"] for row in elements if row["m_attr_name"] == wanted), None
        )
        return all(card["element"] == element for card in cards)

    def _satisfied(self, state: dict, row: dict, event: dict) -> bool:
        kind = row["condition"]
        if kind in TEAM_KINDS:
            team = event["team"]
            # A sweep fields no team, so it cannot meet a formation rule.
            return (
                bool(team)
                and event["quest_id"] == int(row["target"])
                and self._team_fits(row, team)
            )
        if kind == QUEST_CLEAR:
            quest_id = int(row["target"])
            return any(
                record["quest_id"] == quest_id and record["wins"] > 0
                for record in state["quest_records"]
            )
        if kind == KILLS_BEST:
            quest_id = int(row["target"])
            return any(
                record["quest_id"] == quest_id
                and record["ec_best_score"] >= row["count"]
                for record in state["quest_records"]
            )
        if kind == PLAYER_RANK:
            return player_rank(state) >= row["count"]
        if kind == JOB_LEVEL:
            system = int(row["target"])
            return any(
                unit["r_unit_lv"] >= row["count"]
                and self._job_system(unit["card_id"]) == system
                for unit in state["units"]
            )
        if kind == TUTORIAL_FINISHED:
            return state["player"]["tutorial"] == COMPLETE
        if kind == PLATFORM:
            return True
        if kind == LOG_SHARED:
            return int(row["target"]) in state["shared_quest_logs"]
        if kind == BOM_SKILL_LEVEL:
            card_id = int(row["target"])
            return any(
                bom["card_id"] == card_id and bom["exskill_lv"] >= row["count"]
                for bom in state["boms"]
            )
        raise NotImplementedError(f"Mission condition {kind} is pending implementation")

    def _fulfilled(
        self, state: dict, row: dict, now: int, event: dict, counters: dict
    ) -> bool:
        if not row["counterName"]:
            return self._satisfied(state, row, event)
        name = self._name(row)
        if name not in counters:
            counters[name] = self._advance_counter(state, row, event)
            self._write_counter(state, row, counters[name], now)
        return counters[name] >= row["count"]

    @staticmethod
    def _achieve(state: dict, row: dict, now: int, achieved: set) -> None:
        state["missions"].append(
            {"id": row["id"], "status": ACHIEVED, "updated_at": now, "achieved_at": now}
        )
        achieved.add(row["id"])

    # Collectives count achievements and disclose further missions, so the closure is taken in rounds.
    def achieve_all(self, state: dict, now: int) -> int:
        self._refresh()
        before, pending = len(state["missions"]), True
        plain = [
            row
            for row in sorted(self._rows.values(), key=self._order)
            if row["condition"] != MISSIONS_ACHIEVED
        ]
        while pending:
            achieved = {row["id"] for row in state["missions"]}
            known = len(achieved)
            for row in plain:
                if row["id"] not in achieved and self._candidate(
                    state, row, now, achieved
                ):
                    self._achieve(state, row, now, achieved)
            self._settle_collectives(state, now, achieved)
            pending = len(achieved) > known
        return len(state["missions"]) - before

    def open_count(self, state: dict, now: int) -> int:
        rehearsal = deepcopy(state)
        self.achieve_all(rehearsal, now)
        return len(rehearsal["missions"])

    def _settle_collectives(self, state: dict, now: int, achieved: set) -> None:
        pending = True
        while pending:
            pending = False
            for row in self._collectives:
                if not self._candidate(state, row, now, achieved):
                    continue
                value = self._achieved_targets(state, row)
                if row["counterName"]:
                    self._write_counter(state, row, value, now)
                if value >= row["count"]:
                    self._achieve(state, row, now, achieved)
                    pending = True

    def _round(self, state: dict, kinds: frozenset, now: int, event: dict) -> None:
        self.sweep(state, now)
        achieved = {record["id"] for record in state["missions"]}
        queue = deque(
            sorted(
                (row for row in self._rows.values() if row["condition"] in kinds),
                key=self._order,
            )
        )
        counters: dict[str, int] = {}
        processed = False
        while queue:
            row = queue.popleft()
            if not self._candidate(state, row, now, achieved):
                continue
            processed = True
            if self._fulfilled(state, row, now, event, counters):
                self._achieve(state, row, now, achieved)
                queue.extend(
                    child
                    for child in self._successors.get(row["id"], ())
                    if child["condition"] in kinds
                )
        if processed:
            self._settle_collectives(state, now, achieved)

    def _on_quest_complete(self, state: dict, event: dict) -> None:
        data = event["data"]
        if data["result"] == "COMPLETE":
            team = [
                unit["card_id"]
                for unit in state["units"]
                if unit["instance_id"] in data["unit_ids"]
            ]
            if data["support_card"]:
                team.append(data["support_card"])
            context = {
                "clears": 1,
                "quest_category": data["category"],
                "quest_id": data["quest_id"],
                "kills": data["kills"],
                "team": team,
            }
            self._round(state, QUEST_KINDS, event["at"], context)

    def _on_quest_cycle(self, state: dict, event: dict) -> None:
        context = {
            "clears": event["data"]["num_of_exec"],
            "quest_category": event["data"]["category"],
            "quest_id": event["data"]["quest_id"],
            "kills": 0,
            "team": [],
        }
        self._round(state, QUEST_KINDS, event["at"], context)

    def _on_tutorial_progress(self, state: dict, event: dict) -> None:
        if event["data"]["progress"] == COMPLETE:
            self._round(state, frozenset({TUTORIAL_FINISHED}), event["at"], {})

    def _on_unit_enhanced(self, state: dict, event: dict) -> None:
        action = event["data"]["action"]
        context = {
            "exskills": int(action == "exskill"),
            "class_changes": int(action == "class_change"),
        }
        self._round(state, TRAINING_KINDS, event["at"], context)

    def _on_bom_enhanced(self, state: dict, event: dict) -> None:
        self._round(state, frozenset({BOM_SKILL_LEVEL}), event["at"], {})

    def _on_follow(self, state: dict, event: dict) -> None:
        self._round(state, frozenset({PLAYERS_FOLLOWED}), event["at"], {"follows": 1})

    def _on_lending(self, state: dict, event: dict) -> None:
        self._round(state, frozenset({UNITS_LENT}), event["at"], {})

    def _on_log_share(self, state: dict, event: dict) -> None:
        self._round(state, frozenset({LOG_SHARED}), event["at"], {})

    def _on_login(self, state: dict, event: dict) -> None:
        logins = int(event["data"]["first_today"])
        self._round(state, LOGIN_KINDS, event["at"], {"logins": logins})

    def _rewards(self, row: dict) -> list[dict]:
        try:
            return [
                order
                for cell in row["rewardtable"]["m_rewards"]
                if (order := self.resources.reward_order(cell)) is not None
            ]
        except ValueError as error:
            raise ApiRejected(BAD_REWARD) from error

    def _mail(self, state: dict, row: dict, order: dict, now: int) -> dict:
        source = f"mission.reward:{row['id']}"
        self.mailbox.post(state, order, source, now, row["sendInboxMessage"] or None)
        return {
            "rid": order["rid"],
            "val": order["val"],
            "inc": 0,
            "inbox": order["val"],
            "over": 0,
        }

    def _claim(self, state: dict, mission_id: int, now: int) -> list[dict]:
        row = self._rows.get(mission_id)
        record = self._record(state, mission_id)
        if row is None or record is None or record["status"] != ACHIEVED:
            raise ApiRejected(INVALID_STATUS)
        orders = self._rewards(row)
        if row["sendInbox"]:
            deliveries = [self._mail(state, row, order, now) for order in orders]
        else:
            try:
                deliveries = self.resources.grant(state, orders, now).deliveries
            except ResourceCapacityError as error:
                raise ApiRejected(OVERFLOW) from error
        record.update(status=RECEIVED, updated_at=now)
        if row["add_action_type"] == PLAY_STORY:
            self.stories.unlock(
                state, int(row["add_action_key"]), int(row["add_action_value"])
            )
        return deliveries

    def receive(self, state: dict, payload: dict, now: int) -> Result:
        identities = payload["id_list"]
        if not isinstance(identities, list) or any(
            type(identity) is not int for identity in identities
        ):
            raise ValueError("id_list must hold mission IDs")
        before = deepcopy(state)
        self.sweep(state, now)
        received, refusals = [], []
        for mission_id in identities:
            try:
                rewards = self._claim(state, mission_id, now)
                received.append({"id": mission_id, "rewards": rewards})
            except ApiRejected as error:
                refusals.append({"id": mission_id, "reason": error.reason})
        if player_rank(state) > player_rank(before):
            self._round(state, frozenset({PLAYER_RANK}), now, {})
        return Result(
            {"received_list": received, "rejected_reasons": refusals},
            diff_state(before, state),
        )
