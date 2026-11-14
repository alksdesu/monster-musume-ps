from bisect import bisect_right
from collections.abc import Iterable
import uuid

from ..config import RuntimeConfig
from .journal import PLAYER_RANK_UP, Journal
from .masterdata import MasterData


def player_name(state: dict) -> str:
    return state["player_text"]["name"] or state["player"]["nickname"]


def player_rank(state: dict) -> int:
    return next(
        row["level"]
        for row in state["modules"]["experience"]
        if row["section"] == "player_rank"
    )


class Progression:
    def __init__(self, masters: MasterData, config: RuntimeConfig, journal: Journal):
        self.masters = masters
        self.config = config
        self.journal = journal

    @staticmethod
    def _experience(total: int, amount: int, thresholds: list[int], cap: int):
        if not 1 <= cap <= len(thresholds):
            raise ValueError("Level cap is outside the experience table")
        experience = min(total + amount, thresholds[cap - 1])
        return experience, bisect_right(thresholds, experience, hi=cap)

    def recover_stamina(self, state: dict, now: int) -> dict:
        config = self.config.read()
        changed = []
        for stamina in state["modules"]["stamina"]:
            before = stamina.copy()
            if stamina["section"] == "quest":
                stamina["max_capacity"] = config["stamina_max_capacity"]
            elapsed = now - stamina["latest_recovered_at"]
            if elapsed > 0:
                if stamina["amount"] >= stamina["capacity"]:
                    stamina["latest_recovered_at"] = now
                else:
                    ticks = elapsed // stamina["recover_interval"]
                    if ticks:
                        stamina["amount"] = min(
                            stamina["capacity"],
                            stamina["amount"] + ticks * stamina["recover_val"],
                        )
                        stamina["latest_recovered_at"] += (
                            ticks * stamina["recover_interval"]
                        )
            if stamina != before:
                changed.append(stamina)
        return {"module_prop": {"stamina": changed}} if changed else {}

    def add_player_exp(self, state: dict, amount: int, now: int) -> dict:
        if amount < 0:
            raise ValueError("Experience gains must not be negative")
        experience = next(
            row
            for row in state["modules"]["experience"]
            if row["section"] == "player_rank"
        )
        thresholds = self.masters.document("player_rank_exp_table")["table"]["table"]
        new_exp, new_level = self._experience(
            experience["exp"], amount, thresholds, experience["level_cap"]
        )
        updates = self.recover_stamina(state, now)
        old_level = experience["level"]
        if (new_exp, new_level) == (experience["exp"], old_level):
            return updates

        experience.update(exp=new_exp, level=new_level)
        modules = updates.setdefault("module_prop", {})
        modules["experience"] = [experience]
        if new_level > old_level:
            ranks = self.masters.table("player_rank_info_table")
            stamina = next(
                row for row in state["modules"]["stamina"] if row["section"] == "quest"
            )
            before = stamina.copy()
            stamina["capacity"] = ranks[new_level - 1]["staminaCap"]
            stamina["amount"] = min(
                stamina["max_capacity"],
                stamina["amount"]
                + sum(row["staminaCap"] for row in ranks[old_level:new_level]),
            )
            if stamina != before:
                changed = modules.setdefault("stamina", [])
                if stamina not in changed:
                    changed.append(stamina)
        return updates

    def promote(self, state: dict, previous: int, now: int) -> None:
        rank = player_rank(state)
        if rank > previous:
            self.journal.record(
                state,
                f"rankup-{uuid.uuid4()}",
                PLAYER_RANK_UP,
                now,
                {"from": previous, "to": rank},
            )

    def award_player_exp(self, state: dict, amount: int, now: int) -> dict:
        previous = player_rank(state)
        updates = self.add_player_exp(state, amount, now)
        self.promote(state, previous, now)
        return updates

    def level_cap(self, unit: dict) -> int:
        card = self.masters.asset("unitcard", unit["card_id"])
        rarity = self.masters.lookup("unit_rarity", "id", card["rarityId"])
        rank = self.masters.lookup("unit_rank_info_table", "rank", unit["rank"])
        return rarity["maxLevel"] + rank["addMaxLevel"]

    def level_exp(self, unit: dict, level: int) -> int:
        card = self.masters.asset("unitcard", unit["card_id"])
        thresholds = next(
            row["table"]
            for row in self.masters.document("unit_level_exp_table")["tables"]
            if row["rarityId"] == card["rarityId"]
        )
        return thresholds[min(level, self.level_cap(unit)) - 1]

    def add_unit_exp(
        self, state: dict, instance_ids: Iterable[int], amount: int, now: int
    ) -> dict:
        if amount < 0:
            raise ValueError("Experience gains must not be negative")
        by_id = {row["instance_id"]: row for row in state["units"]}
        units = [by_id[identity] for identity in dict.fromkeys(instance_ids)]
        tables = {
            row["rarityId"]: row["table"]
            for row in self.masters.document("unit_level_exp_table")["tables"]
        }
        changed = []
        for unit in units:
            card = self.masters.asset("unitcard", unit["card_id"])
            new_exp, new_level = self._experience(
                unit["t_exp"], amount, tables[card["rarityId"]], self.level_cap(unit)
            )
            if (new_exp, new_level) != (unit["t_exp"], unit["r_unit_lv"]):
                unit.update(t_exp=new_exp, r_unit_lv=new_level)
                changed.append(unit)
        return {"unit": changed} if changed else {}

    def stamina_cost(self, state: dict, quest: dict) -> list[dict]:
        amount = quest["mana"]
        if amount <= 0:
            return []
        category, name = quest["consumption_category"], quest["consumption_name"]
        if category and name:
            wallet = next(
                row
                for row in self.masters.table("wallet_template")
                if row["category"] == category and row["name"] == name
            )
            return [
                {"rid": f"wallet.{wallet['category']}.{wallet['name']}", "val": amount}
            ]

        record = next(
            (row for row in state["quest_records"] if row["quest_id"] == quest["id"]),
            None,
        )
        if record is None or record["wins"] == 0:
            amount = max(0, amount * self.masters.property("Int", 17) // 100)
        return [{"rid": "stamina.quest", "val": amount}] if amount else []
