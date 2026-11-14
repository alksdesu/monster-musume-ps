from copy import deepcopy

from .errors import ApiRejected
from .sync import Result, diff_state


RAID = 4
EVERYONE, SELF, BEST = 0, 1, 2
SCENES = ("login", "quest")
SOURCE = "event.sd_raid_prg_reward"
INVALID_EVENT, OUT_OF_SCHEDULE = "INVALID_EVENT_ID", "OUT_OF_SCHEDULE"


class SubdueRaids:
    def __init__(self, masters, config, resources, mailbox, store):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.mailbox = mailbox
        self.store = store

    def _settings(self) -> dict:
        return self.config.read()["subdue_raid"]

    def _event(self, event_id, now: int) -> tuple[dict, dict]:
        if type(event_id) is not int:
            raise ValueError("event_id must be an integer")
        try:
            event = self.masters.lookup("event_list_data", "id", event_id)
            raid = self.masters.lookup(
                "everyones_killevent_data", "dataId", event["event_dataTable_id"]
            )
        except KeyError as error:
            raise ApiRejected(INVALID_EVENT) from error
        if event["event_type"] != RAID:
            raise ApiRejected(INVALID_EVENT)
        scheduled = self._settings()["follow_master_schedule"]
        if scheduled and not self.masters.in_term(
            raid["st_date"], raid["ed_date"], now
        ):
            raise ApiRejected(OUT_OF_SCHEDULE)
        return event, raid

    def _kills(self, state: dict, event: dict) -> tuple[int, int]:
        stages = {
            row["id"]
            for row in self.masters.table("quest_data")
            if row["chapter"] == event["chapter"]
        }
        records = [row for row in state["quest_records"] if row["quest_id"] in stages]
        return (
            sum(row["ec_total_score"] for row in records),
            max((row["ec_best_score"] for row in records), default=0),
        )

    def _saves(self, state: dict) -> list[dict]:
        saves = {
            document["state"]["player"]["player_code"]: document["state"]
            for document in self.store.documents()
        }
        saves[state["player"]["player_code"]] = state
        return list(saves.values())

    def _everyone(self, saves: list[dict], event: dict) -> int:
        settings = self._settings()["everyone"]
        local = sum(self._kills(save, event)[0] for save in saves)
        # A lone server stands in for the whole player base by scaling its own kills.
        return settings["base"] + local * settings["multiplier"]

    def episode(self, story_id: int) -> dict | None:
        groups = self.masters.table("everyones_killevent_episode")
        rows = (row for group in groups for row in group["episodeData"])
        return next((row for row in rows if row["story_id"] == story_id), None)

    def shared_kills(self, state: dict, episode: dict) -> int:
        raids = {
            raid["dataId"]
            for raid in self.masters.table("everyones_killevent_data")
            if raid["episode_table_id"] == episode["episode_table_id"]
        }
        saves = self._saves(state)
        return max(
            (
                self._everyone(saves, event)
                for event in self.masters.table("event_list_data")
                if event["event_type"] == RAID and event["event_dataTable_id"] in raids
            ),
            default=0,
        )

    def _tiers(self, raid: dict) -> list[dict]:
        return next(
            (
                group["rewardData"]
                for group in self.masters.table("everyones_killevent_reward")
                if group["rewardData"][0]["reward_table_id"] == raid["reward_table_id"]
            ),
            [],
        )

    def confirm(self, state: dict, payload: dict, now: int) -> Result:
        if payload["scene"] not in SCENES:
            raise ValueError("scene must be login or quest")
        event, raid = self._event(payload["event_id"], now)
        total, best = self._kills(state, event)
        shared = self._everyone(self._saves(state), event)
        progress = {EVERYONE: shared, SELF: total, BEST: best}
        before = deepcopy(state)
        granted = state["subdue_raid"].setdefault(str(event["id"]), [])
        source = f"{SOURCE}:{event['id']}"
        rewards: dict[int, list[dict]] = {kind: [] for kind in progress}
        for tier in self._tiers(raid):
            kind = tier["reward_kind"]
            if tier["id"] in granted or tier["target_num"] > progress[kind]:
                continue
            order = self.resources.reward_order(tier["reward"])
            self.resources.check([order])
            self.mailbox.post(state, dict(order), source, now, raid["box_message"])
            granted.append(tier["id"])
            # The client shows the amount that reached the present box.
            delivery = {**order, "inc": 0, "inbox": order["val"], "over": 0}
            rewards[kind].append(
                {"progress_num": tier["target_num"], "rewards": [delivery]}
            )
        return Result(
            {
                "progress_of_self": total,
                "progress_of_best": best,
                "progress_of_everyone": progress[EVERYONE],
                "rewards_of_self": rewards[SELF],
                "rewards_of_best": rewards[BEST],
                "rewards_of_everyone": rewards[EVERYONE],
            },
            diff_state(before, state),
        )
