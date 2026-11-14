from copy import deepcopy

from .errors import ApiRejected
from .journal import QUEST_COMPLETE
from .resources import ResourceCapacityError
from .sync import Result, diff_state


MAIN, EVENT, OTHER, UNIT, COSTUME, EPISODE, GROUP = 1, 2, 3, 4, 5, 6, 11
TABLES = {
    MAIN: "story_data_main",
    EVENT: "story_data_event",
    OTHER: "story_data_other",
    UNIT: "story_data_unit",
    COSTUME: "story_data_unit",
    EPISODE: "story_data_event",
    GROUP: "story_data_group",
}
SOURCES = tuple(dict.fromkeys(TABLES.values()))
QUEST_CONDITIONS = (1, 2)


class Stories:
    def __init__(self, masters, resources, journal, raids):
        self.masters = masters
        self.resources = resources
        self.raids = raids
        self._documents = ()
        self._rows: dict[tuple[int, int], dict] = {}
        self._embedded: dict[int, list[dict]] = {}
        self._conditional: dict[int, list[dict]] = {}
        journal.subscribe(QUEST_COMPLETE, self._on_quest_complete)

    def _refresh(self) -> None:
        documents = tuple(self.masters.document(name) for name in SOURCES)
        if len(documents) == len(self._documents) and all(
            current is previous for current, previous in zip(documents, self._documents)
        ):
            return
        tables = {name: document["table"] for name, document in zip(SOURCES, documents)}
        rows = {}
        for table in tables.values():
            for row in table:
                key = row["story_category"], row["id"]
                if key in rows:
                    raise ValueError(f"Duplicate story {key[1]} in category {key[0]}")
                rows[key] = row
        scenarios: dict[str, list[dict]] = {}
        for row in tables[TABLES[UNIT]]:
            scenarios.setdefault(row["scenario_name"], []).append(row)
        embedded: dict[int, list[dict]] = {}
        for row in tables[TABLES[MAIN]]:
            if row["scenario_name"] in scenarios:
                embedded.setdefault(int(row["story_id"]), []).extend(
                    scenarios[row["scenario_name"]]
                )
        conditional: dict[int, list[dict]] = {}
        for row in tables[TABLES[OTHER]]:
            if row["unlock_condition_type"] in QUEST_CONDITIONS:
                quest_id = int(row["unlock_condition_value"])
                conditional.setdefault(quest_id, []).append(row)
        self._rows, self._embedded, self._conditional = rows, embedded, conditional
        self._documents = documents

    @staticmethod
    def _category(value) -> int:
        if type(value) is not int or value not in TABLES:
            raise ApiRejected("HAS_INVALID_CATEGORY_ID")
        return value

    def _story(self, category: int, story_id, identity) -> dict:
        self._refresh()
        row = None
        if type(story_id) is int and type(identity) is int:
            row = self._rows.get((category, identity))
        if row is None or row["story_id"] != str(story_id):
            raise ApiRejected("HAS_INVALID_STORY_ID")
        return row

    @staticmethod
    def _identity(row: dict) -> str:
        return f"story.{row['story_category']}.{row['story_id']}.{row['id']}"

    @staticmethod
    def _record(state: dict, identity: str) -> dict | None:
        return next((row for row in state["stories"] if row["id"] == identity), None)

    def _rewards(self, row: dict) -> list[dict]:
        return [
            order
            for reward in row["rewardtable"]["m_rewards"]
            if (order := self.resources.reward_order(reward)) is not None
        ]

    @staticmethod
    def _trained(state: dict, card_id: int, level: int) -> bool:
        return any(
            unit["card_id"] == card_id and unit["r_unit_lv"] >= level
            for unit in state["units"]
        )

    @staticmethod
    def _cleared(state: dict, quest_id: int) -> bool:
        return any(
            row["quest_id"] == quest_id and row["wins"] > 0
            for row in state["quest_records"]
        )

    def _unit_story_open(self, state: dict, row: dict) -> bool:
        required = self.resources.reward_order(
            {
                "m_walletCategory": row["requireItemCategory"],
                "m_walletName": row["requireItemName"],
                "m_count": 1,
            }
        )
        return (
            bool(row["always"])
            and self._trained(state, int(row["story_id"]), row["unlockLevel"])
            and (
                required is None
                or self.resources.balance(state, required["rid"]) >= required["val"]
            )
        )

    def _group_story_open(self, state: dict, row: dict) -> bool:
        quest_id = row["need_quest_id"]
        return (
            bool(row["always"])
            and all(
                self._trained(state, member["id"], member["level"])
                for member in row["units"]
            )
            and (not quest_id or self._cleared(state, quest_id))
        )

    def _episode_open(self, state: dict, row: dict, now: int) -> bool:
        episode = self.raids.episode(row["id"])
        if episode is None or not self.masters.in_term(episode["releaseData"], "", now):
            return False
        if episode["releaseId"]:
            earlier = self._rows.get((EPISODE, episode["releaseId"]))
            record = self._record(state, self._identity(earlier)) if earlier else None
            if record is None or not record["is_already_read"]:
                return False
        return self.raids.shared_kills(state, episode) >= episode["releaseCount"]

    def _open(self, state: dict, row: dict, now: int) -> bool:
        category = row["story_category"]
        if category in (UNIT, COSTUME):
            return self._unit_story_open(state, row)
        if category == GROUP:
            return self._group_story_open(state, row)
        if category == EPISODE:
            return self._episode_open(state, row, now)
        return False

    def unlock(self, state: dict, category: int, identity: int) -> None:
        self._refresh()
        record_id = self._identity(self._rows[category, identity])
        if self._record(state, record_id) is None:
            state["stories"].append({"id": record_id, "is_already_read": False})

    def records(self, state: dict, category) -> list[dict]:
        prefix = f"story.{self._category(category)}."
        return [row for row in state["stories"] if row["id"].startswith(prefix)]

    def read(self, state: dict, payload: dict, now: int) -> Result:
        category = self._category(payload["story_category"])
        row = self._story(category, payload["story_id"], payload["id"])
        identity = self._identity(row)
        record = self._record(state, identity)
        if record is None:
            if not self._open(state, row, now):
                raise ApiRejected("HAS_INVALID_STORY_ID")
        elif record["is_already_read"]:
            raise ApiRejected("IS_ALREADY_READ")
        before = deepcopy(state)
        try:
            self.resources.grant(state, self._rewards(row), now)
        except ResourceCapacityError as error:
            raise ApiRejected("ASSET_CAPACITY_OVERFLOW") from error
        except ValueError as error:
            raise ApiRejected("REWARD_DATA_ERROR") from error
        if record is None:
            record = {"id": identity}
            state["stories"].append(record)
        record["is_already_read"] = True
        return Result(sync=diff_state(before, state))

    def _on_quest_complete(self, state: dict, event: dict) -> None:
        if not event["data"]["first_clear"]:
            return
        self._refresh()
        quest_id = event["data"]["quest_id"]
        # The client only reads rewarded stories, so others would stay unread forever.
        unlocked = [
            row for row in self._embedded.get(quest_id, ()) if self._rewards(row)
        ]
        unlocked += self._conditional.get(quest_id, ())
        for row in unlocked:
            identity = self._identity(row)
            if self._record(state, identity) is None:
                state["stories"].append({"id": identity, "is_already_read": False})
