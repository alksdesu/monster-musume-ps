from math import prod

from .journal import LOGIN


TOWER_CATEGORY = 12
# Bonus rows are keyed by the names the result page looks them up with.
FACTORS = ("星獲得数", "編成人数", "出撃人数", "出撃トークン", "レンタル")
PERCENT = 100
SOURCE = "tower.daily_points"


class Tower:
    def __init__(self, masters, config, resources, mailbox, tracks, journal):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.mailbox = mailbox
        self.tracks = tracks
        journal.subscribe(LOGIN, self._on_login)

    def _settings(self) -> dict:
        return self.config.read()["tower"]

    @staticmethod
    def applies(quest: dict) -> bool:
        return quest["category"] == TOWER_CATEGORY

    @staticmethod
    def valid(report) -> bool:
        return isinstance(report, dict) and all(
            type(report.get(key)) is int and report[key] >= 0
            for key in ("deployed", "tokens")
        )

    def _control(self) -> dict:
        return self.masters.table("temple_tower_control")[-1]

    def _factor(self, category: int, value: int) -> int:
        steps = {
            row["category_value"]: row["value"]
            for row in self.masters.table("temple_tower_score_calc")
            if row["category"] == category
        }
        return steps[min(max(value, min(steps)), max(steps))]

    @staticmethod
    def _text(value: int, scale: int) -> str:
        return f"{value / scale:g}"

    def _generation(self) -> list[dict]:
        table_id = self._control()["generate_point_id1"]
        return [
            row
            for row in self.masters.table("temple_tower_point_generation")
            if row["table_id"] == table_id
        ]

    def _daily(self, total: int) -> dict:
        settings, rows = self._settings()["daily_points"], self._generation()
        reached = [row for row in rows if total >= row["total_score_threshold"]]
        amount = reached[-1]["generate_point"] if reached else 0
        if not settings["enabled"]:
            amount = 0
        wallet = next(row["wallet_name"] for row in rows if row["wallet_name"])
        return {"rid": wallet, "val": amount * settings["multiplier"]}

    def _scores(self, state: dict) -> list[dict]:
        floors = {
            row["id"]
            for row in self.masters.table("quest_data")
            if row["category"] == TOWER_CATEGORY
        }
        return [row for row in state["quest_records"] if row["quest_id"] in floors]

    def _refresh(self, state: dict) -> None:
        records = self._scores(state)
        total = sum(
            row["event_payloads"]["tower"]["score"]
            for row in records
            if "event_payloads" in row
        )
        progression = {
            "id": self._control()["id"],
            "total_score": total,
            "stars": sum(row["record_rating"] for row in records),
            "daily_pt_1": self._daily(total),
            "daily_pt_2": None,
            "daily_pt_3": None,
        }
        towers = state["player"].setdefault("towers", [])
        towers[:] = [row for row in towers if row["id"] != progression["id"]]
        towers.append(progression)

    def settle(
        self, state: dict, record: dict, transaction: dict, statement: dict, now: int
    ) -> dict:
        settings = self._settings()
        rented = transaction["support"] is not None
        members = len(transaction["party"])
        if rented and settings["rental_counts_as_member"]:
            members += 1
        report = statement["tower"]
        inputs = (
            statement["rating"],
            members,
            report["deployed"],
            report["tokens"],
            int(rented),
        )
        factors = [
            self._factor(category, value) for category, value in enumerate(inputs)
        ]
        score = prod(factors) // PERCENT ** (len(factors) - 1)
        previous = record.get("event_payloads", {}).get("tower", {}).get("score", 0)
        best = max(previous, score)
        crossed = self.tracks.crossed(
            self._control()["high_score_reward_id"], previous, best
        )
        rewards = [order for node in crossed for order in node["rewards"]]
        self.resources.grant(state, rewards, now, policy="mail")
        record["event_payloads"] = {"tower": {"score": best, "progress": best}}
        self._refresh(state)
        scales = settings["factor_scale"]
        return {
            "final_score": score,
            "record_score": best,
            "prev_record_score": previous,
            "bonus_details": [
                {
                    "category": name,
                    "org": value,
                    "factor": self._text(factor, scales[name]),
                }
                for name, value, factor in zip(FACTORS, inputs, factors)
            ],
            "floor_reward_ids": [node["ordinal"] for node in crossed],
        }

    def _on_login(self, state: dict, event: dict) -> None:
        if not event["data"]["first_today"]:
            return
        control = self._control()["id"]
        for progression in state["player"].get("towers", ()):
            points = self._daily(progression["total_score"])
            if progression["id"] != control or points["val"] <= 0:
                continue
            progression["daily_pt_1"] = points
            self.mailbox.post(state, dict(points), f"{SOURCE}:{control}", event["at"])
