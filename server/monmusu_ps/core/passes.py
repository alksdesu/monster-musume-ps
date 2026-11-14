from collections import defaultdict
from copy import deepcopy

from .errors import ApiNotFound
from .sync import Result, diff_state


FREE, PREMIUM = 1, 2
TRACKS = ((FREE, "free_track"), (PREMIUM, "premium_track"))
MAIL_SOURCE = "bnspass.reward"
TABLES = ("bonus_pass_data", "bonus_pass_tier_data", "season_pass_data")
REWARDS = "bonus_pass_track_reward_data"


def blank(pass_id: int, now: int) -> dict:
    return {
        "pass_id": pass_id,
        "tier": 0,
        "progression": 0,
        "free_track": {"is_locked": False, "received_tier": 0, "remind_tier": 0},
        "premium_track": {"is_locked": True, "received_tier": 0, "remind_tier": 0},
        "tier_progressed_at": now,
    }


def find(records: list[dict], pass_id: int) -> dict | None:
    return next((row for row in records if row["pass_id"] == pass_id), None)


def record(records: list[dict], pass_id: int, now: int) -> dict:
    row = find(records, pass_id)
    if row is None:
        row = blank(pass_id, now)
        records.append(row)
    return row


class PassCatalog:
    def __init__(self, masters):
        self.masters = masters
        self._documents = ()
        self._rows: dict[int, dict] = {}
        self._by_point: dict[str, list[dict]] = {}
        self._by_unlock: dict[str, list[dict]] = {}
        self._steps: dict[int, list[int]] = {}
        self._seasons: dict[int, dict] = {}
        self._reward_document = None
        self._rewards: dict[tuple[int, int, int], dict] = {}

    def _refresh(self) -> None:
        documents = tuple(self.masters.document(name) for name in TABLES)
        if self._documents and all(
            current is previous for current, previous in zip(documents, self._documents)
        ):
            return
        passes, tiers, seasons = (document["table"] for document in documents)
        by_point, by_unlock, steps = (
            defaultdict(list),
            defaultdict(list),
            defaultdict(list),
        )
        for row in passes:
            by_point[row["ref_prog_point_rid"]].append(row)
            by_unlock[row["ref_premium_track_unlock_rid"]].append(row)
        for row in tiers:
            steps[row["pass_id"]].append(row["req"])
        for requirements in steps.values():
            requirements.sort()
        self._rows = {row["id"]: row for row in passes}
        self._by_point, self._by_unlock, self._steps = by_point, by_unlock, steps
        self._seasons = {row["pass_id"]: row for row in seasons}
        self._documents = documents

    def get(self, pass_id) -> dict:
        self._refresh()
        if type(pass_id) is not int or pass_id not in self._rows:
            raise ApiNotFound(str(pass_id))
        return self._rows[pass_id]

    def point_rids(self) -> list[str]:
        self._refresh()
        return list(self._by_point)

    def unlock_rids(self) -> list[str]:
        self._refresh()
        return list(self._by_unlock)

    def _term(self, row: dict) -> tuple[str, str, str]:
        season = self._seasons.get(row["id"])
        source = season if season is not None and not row["start_dt"] else row
        return source["start_dt"], source["end_dt"], source["close_dt"]

    def earning(self, row: dict, now: int) -> bool:
        start, end, _ = self._term(row)
        return self.masters.in_term(start, end, now)

    def open(self, row: dict, now: int) -> bool:
        start, end, close = self._term(row)
        return self.masters.in_term(start, close or end, now)

    def _earning_by(self, index: dict[str, list[dict]], rid: str, now: int):
        rows = [row for row in index.get(rid, ()) if self.earning(row, now)]
        if len(rows) > 1:
            raise ValueError(f"Overlapping bonus passes for {rid}")
        return rows[0] if rows else None

    def earning_by(self, rid: str, now: int) -> dict | None:
        self._refresh()
        return self._earning_by(self._by_point, rid, now)

    def unlocked_by(self, rid: str, now: int) -> dict | None:
        self._refresh()
        return self._earning_by(self._by_unlock, rid, now)

    def unlockable(self, records: list[dict], rid: str, now: int) -> bool:
        active = self.unlocked_by(rid, now)
        if active is None:
            return False
        row = find(records, active["id"])
        return row is None or row["premium_track"]["is_locked"]

    def listed(self, now: int) -> list[dict]:
        self._refresh()
        return [row for _, row in sorted(self._rows.items()) if self.open(row, now)]

    def tier(self, pass_id: int, progression: int) -> int:
        self._refresh()
        return sum(step <= progression for step in self._steps.get(pass_id, ()))

    def rewards(self, pass_id: int, track: int, tier: int) -> list[dict]:
        document = self.masters.document(REWARDS)
        if document is not self._reward_document:
            self._rewards = {
                (row["pass_id"], row["track"], row["tier"]): row
                for row in document["table"]
            }
            self._reward_document = document
        row = self._rewards.get((pass_id, track, tier))
        return [] if row is None else row["rewards"]

    def advance(self, row: dict, amount: int, now: int) -> int:
        self._refresh()
        steps = self._steps.get(row["pass_id"], ())
        reached = row["progression"] + amount
        total = min(reached, steps[-1]) if steps else reached
        gained = max(0, total - row["progression"])
        row["progression"] += gained
        tier = self.tier(row["pass_id"], row["progression"])
        if tier != row["tier"]:
            row.update(tier=tier, tier_progressed_at=now)
        return gained


class BonusPasses:
    def __init__(self, catalog: PassCatalog, resources, mailbox):
        self.catalog = catalog
        self.resources = resources
        self.mailbox = mailbox

    def progressions(self, state: dict, now: int) -> list[dict]:
        records = state["bonus_passes"]
        return [record(records, row["id"], now) for row in self.catalog.listed(now)]

    @staticmethod
    def _claims(row: dict) -> list[tuple[int, int]]:
        claims = []
        for number, key in TRACKS:
            status = row[key]
            if status["is_locked"]:
                continue
            claims += [
                (number, tier)
                for tier in range(status["received_tier"] + 1, row["tier"] + 1)
            ]
            status["received_tier"] = max(status["received_tier"], row["tier"])
        return sorted(claims, key=lambda claim: (claim[1], claim[0]))

    def _award(self, state: dict, pass_id: int, track: int, tier: int, now: int):
        source = f"{MAIL_SOURCE}:{pass_id}:{track}:{tier}"
        rewards = []
        for reward in self.catalog.rewards(pass_id, track, tier):
            order = self.resources.reward_order(reward)
            if order is None:
                continue
            self.mailbox.post(state, order, source, now)
            rewards.append({**order, "inc": 0, "inbox": order["val"], "over": 0})
        return {"track": track, "tier": tier, "rewards": rewards}

    def receive(self, state: dict, payload: dict, now: int) -> Result:
        definition = self.catalog.get(payload["pass_id"])
        pass_id = definition["id"]
        before = deepcopy(state)
        row = find(state["bonus_passes"], pass_id)
        claims = []
        if row is not None and self.catalog.open(definition, now):
            claims = self._claims(row)
        received = [
            self._award(state, pass_id, track, tier, now) for track, tier in claims
        ]
        return Result(
            {"pass_id": pass_id, "received_list": received},
            diff_state(before, state),
        )

    def checkoff(self, state: dict, payload: dict, now: int) -> Result:
        definition = self.catalog.get(payload["pass_id"])
        row = find(state["bonus_passes"], definition["id"])
        if row is None:
            raise ApiNotFound(str(definition["id"]))
        tier = payload["tier"]
        if type(tier) is not int or not 0 <= tier <= row["tier"]:
            raise ValueError("tier must be a tier the pass has reached")
        before = deepcopy(state)
        for _, key in TRACKS:
            row[key]["remind_tier"] = max(row[key]["remind_tier"], tier)
        return Result(sync=diff_state(before, state))
