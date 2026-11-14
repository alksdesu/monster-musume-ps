from .errors import ApiRejected
from .events import chapter_event
from .quest_gates import quest_record


TRAINING, EXORCISM = 3, 8
QUEST_CATEGORY = 4
INVALID_STATEMENT = "INVALID_STATEMENT"


class TrainingMenus:
    def __init__(self, masters):
        self.masters = masters

    def event(self, quest: dict) -> dict | None:
        if quest["category"] != QUEST_CATEGORY:
            return None
        return chapter_event(self.masters, quest["chapter"], (TRAINING, EXORCISM))

    def _unlocked(self, state: dict, event: dict, quest: dict, menu: dict) -> bool:
        premise = menu["premise_menu_id"]
        if event["event_type"] == EXORCISM:
            # Exorcism menus open after a clear, and name a best rank as premise.
            record = quest_record(state, quest["id"])
            return (
                record is not None
                and record["wins"] > 0
                and record["t_best_rank"] >= premise
            )
        if not premise:
            return True
        try:
            required = self.masters.lookup("training_menu", "id", premise)
        except KeyError:
            return False
        record = quest_record(state, required["quest_id"])
        return record is not None and premise in record.get("t_cleared_menus", ())

    def selection(self, state: dict, quest: dict, menus) -> dict | None:
        if (
            not isinstance(menus, list)
            or any(type(menu) is not int for menu in menus)
            or len(set(menus)) != len(menus)
        ):
            raise ApiRejected(INVALID_STATEMENT)
        event = self.event(quest)
        if event is None:
            if menus:
                raise ApiRejected(INVALID_STATEMENT)
            return None
        offered = {
            row["id"]: row
            for row in self.masters.table("training_menu")
            if row["quest_id"] == quest["id"]
        }
        if not set(menus) <= set(offered):
            raise ApiRejected(INVALID_STATEMENT)
        chosen = [offered[menu] for menu in menus]
        exclusive = len({menu["type"] for menu in chosen}) == len(chosen)
        if not exclusive or not all(
            self._unlocked(state, event, quest, menu) for menu in chosen
        ):
            raise ApiRejected(INVALID_STATEMENT)
        return {
            "event_id": event["id"],
            "rank": sum(menu["rank"] for menu in chosen),
            "menus": sorted(menus),
        }

    def swept(self, state: dict, quest: dict) -> dict | None:
        event = self.event(quest)
        if event is None:
            return None
        record = quest_record(state, quest["id"])
        # A sweep pays out at the best rank the quest was ever cleared with.
        rank = record["t_best_rank"] if record else 0
        return {"event_id": event["id"], "rank": rank, "menus": []}

    def _rate(self, rank: int) -> int:
        try:
            return self.masters.lookup("training_rank", "rank", rank)["rate"]
        except KeyError:
            return 100

    def boost(self, training: dict | None, orders: list[dict]) -> list[dict]:
        if training is None:
            return orders
        event = self.masters.lookup("event_list_data", "id", training["event_id"])
        try:
            tab = self.masters.lookup("shop_tab", "id", event["shop_id"])
        except KeyError:
            return orders
        points = (
            f"wallet.{tab['main_draw_wallet_category']}.{tab['main_draw_wallet_name']}"
        )
        bonus = self._rate(training["rank"]) - 100
        return [
            {**order, "val": order["val"] + bonus * order["val"] // 100}
            if order["rid"] == points
            else order
            for order in orders
        ]

    @staticmethod
    def record(record: dict, training: dict | None) -> None:
        if training is None:
            return
        record["t_best_rank"] = max(record["t_best_rank"], training["rank"])
        cleared = set(record.get("t_cleared_menus", ())) | set(training["menus"])
        if cleared:
            record["t_cleared_menus"] = sorted(cleared)
