from collections import defaultdict
from collections.abc import Callable


QUEST_COMPLETE = "quest_complete"
QUEST_CYCLE = "quest_cycle"
GACHA_DRAW = "gacha_draw"
SHOP_EXCHANGE = "shop_exchange"
PLAYER_RANK_UP = "player_rank_up"
TUTORIAL_PROGRESS = "tutorial_progress"
LOGIN = "login"
BATTLE_LOG_SHARE = "battle_log_share"
SOCIAL_FOLLOW = "social_follow"
SOCIAL_LENDING = "social_lending"
UNIT_ENHANCED = "unit_enhanced"
BOM_ENHANCED = "bom_enhanced"

Listener = Callable[[dict, dict], None]


class Journal:
    def __init__(self, config):
        self.config = config
        self._listeners: dict[str, list[Listener]] = defaultdict(list)

    def subscribe(self, kind: str, listener: Listener) -> None:
        self._listeners[kind].append(listener)

    def record(
        self, state: dict, identity: str, kind: str, now: int, data: dict
    ) -> None:
        event = {"id": identity, "type": kind, "at": now, "data": data}
        events = state["events"]
        events.append(event)
        del events[: max(0, len(events) - self.config.read()["retention"]["events"])]
        for listener in self._listeners[kind]:
            listener(state, event)
