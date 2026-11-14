from copy import deepcopy

from .sync import Result, diff_state


class Ranch:
    def __init__(self, masters, resources):
        self.masters = masters
        self.resources = resources

    def _tier(self, state: dict) -> dict:
        cleared = {row["quest_id"] for row in state["quest_records"] if row["wins"]}
        reached = [
            row
            for row in self.masters.table("ranch_reward_table")
            if not row["progress_quest_id"] or row["progress_quest_id"] in cleared
        ]
        return max(reached, key=lambda row: row["id"])

    def _milk(self, points: int) -> list[dict]:
        bottles = sorted(
            (
                row
                for row in self.masters.table("wallet_template")
                if row["category"] == "unit_exp"
            ),
            key=lambda row: row["exp_point"],
            reverse=True,
        )
        orders = []
        for bottle in bottles:
            count, points = divmod(points, bottle["exp_point"])
            if count:
                orders.append(
                    {"rid": f"wallet.unit_exp.{bottle['name']}", "val": count}
                )
        return orders

    def harvest(self, state: dict, payload: dict, now: int) -> Result:
        section = payload["section"]
        prop = next(
            (row for row in state["modules"]["farming"] if row["section"] == section),
            None,
        )
        if prop is None:
            raise ValueError(f"Unknown farming section: {section}")
        elapsed = min(max(0, now - prop["start_at"]), prop["term_min"] * 60)
        rounds = elapsed // (prop["interval_min"] * 60)
        if not rounds:
            return Result({"rewards": []})
        before = deepcopy(state)
        tier = self._tier(state)
        orders = [
            {"rid": "wallet.point.gold", "val": rounds * tier["reward_gold"]},
            *self._milk(rounds * tier["reward_unit_exp"]),
        ]
        changes = self.resources.grant(state, orders, now, policy="discard")
        prop.update(tier=tier["id"], start_at=now)
        return Result({"rewards": changes.deliveries}, diff_state(before, state))
