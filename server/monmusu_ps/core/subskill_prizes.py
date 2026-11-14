from collections import defaultdict
from copy import deepcopy

from .lottery import percent
from .quest_gates import quest_record


PREFIX = "skill.attachable."


class SubskillPrizes:
    def __init__(self, masters, config, rng):
        self.masters = masters
        self.config = config
        self.rng = rng

    def _settings(self) -> dict:
        return self.config.read()["subskill_prizes"]

    @staticmethod
    def _unique(table: dict) -> dict | None:
        return next(
            (prize for prize in table["prizes"] if prize["limitation"] > 0), None
        )

    @staticmethod
    def _progress(record: dict | None, skill_id: int) -> dict | None:
        rows = (record or {}).get("subskills", ())
        return next((row for row in rows if row["id"] == skill_id), None)

    def table(self, record: dict | None, quest: dict) -> dict | None:
        tables = {
            row["id"]: row
            for row in self.masters.document("sub_skill_prize_table")["tables"]
        }
        won = {
            row["id"] for row in (record or {}).get("subskills", ()) if row["hit"] > 0
        }
        current, visited = tables.get(quest["subSkillPrizeTableId"]), set()
        # A table gives way to its successor once its limited prize is won.
        while current is not None and current["id"] not in visited:
            visited.add(current["id"])
            limited = self._unique(current)
            if (
                limited is None
                or limited["subSkillId"] not in won
                or limited["nextTableId"] not in tables
            ):
                break
            current = tables[limited["nextTableId"]]
        return current

    def shares(self, table: dict, record: dict | None = None) -> dict[int, float]:
        settings, unique = self._settings(), self._unique(table)
        progress = self._progress(record, unique["subSkillId"]) if unique else None
        # Each miss of the unique prize makes the next attempt likelier to win it.
        pity = settings["pity_weight"] * progress["miss"] if progress else 0
        weights: dict[int, float] = defaultdict(float)
        for prize in table["prizes"]:
            limited = prize["limitation"] > 0
            weights[prize["subSkillId"]] += (
                settings["limited_weight"] + pity if limited else settings["weight"]
            )
        total = sum(weights.values())
        return {
            skill_id: weight / total for skill_id, weight in weights.items() if weight
        }

    def _tally(self, record: dict, table: dict, skill_id: int) -> None:
        unique = self._unique(table)
        if unique is None:
            return
        progress = self._progress(record, unique["subSkillId"])
        if progress is None:
            progress = {"id": unique["subSkillId"], "hit": 0, "miss": 0}
            record.setdefault("subskills", []).append(progress)
        progress["hit" if skill_id == unique["subSkillId"] else "miss"] += 1

    def draw(self, record: dict | None, quest: dict) -> list[dict]:
        trial = {"subskills": deepcopy((record or {}).get("subskills", []))}
        orders = []
        for _ in range(self._settings()["drops_per_clear"]):
            table = self.table(trial, quest)
            if table is None:
                break
            shares = self.shares(table, trial)
            skill_id = self.rng.choices(list(shares), weights=list(shares.values()))[0]
            # A unique prize drawn now moves the later draws on to the next table.
            self._tally(trial, table, skill_id)
            orders.append({"rid": f"{PREFIX}{skill_id}", "val": 1})
        return orders

    def settle(self, record: dict, quest: dict, orders: list[dict]) -> None:
        for order in orders:
            table = self.table(record, quest)
            if table is not None:
                self._tally(record, table, int(order["rid"].removeprefix(PREFIX)))

    def listing(self, state: dict, payload: dict) -> dict:
        quest_id = payload["quest_id"]
        try:
            quest = self.masters.lookup("quest_data", "id", quest_id)
        except KeyError:
            return {"table_id": "", "prizes": []}
        if (payload["category"], payload["section"]) != (
            quest["category"],
            quest["chapter"],
        ):
            raise ValueError("Quest category and section do not match the quest")
        record = quest_record(state, quest_id)
        table = self.table(record, quest)
        if table is None:
            return {"table_id": "", "prizes": []}
        return {
            "table_id": table["id"],
            "prizes": [
                {"id": skill_id, "p": percent(share)}
                for skill_id, share in self.shares(table, record).items()
            ],
        }
