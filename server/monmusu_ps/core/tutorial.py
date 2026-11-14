from .journal import TUTORIAL_PROGRESS, Journal
from .masterdata import MasterData


QUEST, DECK, GACHA, LOGIN_BONUS = "quest", "deck", "gacha", "login_bonus"
START = 0
COMPLETE = 9999
# The client maps only these values to steps; any other reads as finished.
ROUTE = (
    (100, QUEST),
    (200, QUEST),
    (250, DECK),
    (300, QUEST),
    (350, DECK),
    (500, QUEST),
    (600, GACHA),
    (700, DECK),
    (900, QUEST),
    (COMPLETE, LOGIN_BONUS),
)
QUEST_PROPERTIES = range(50, 55)

_QUEST_PROGRESS = tuple(progress for progress, action in ROUTE if action == QUEST)
_NEXT_STEP = dict(zip((START, *(progress for progress, _ in ROUTE)), ROUTE))
_STEP_ACTIONS = {action for _, action in ROUTE} - {QUEST}


class Tutorial:
    def __init__(self, masters: MasterData, journal: Journal):
        self.masters = masters
        self.journal = journal

    @staticmethod
    def started(state: dict) -> bool:
        return state["player"]["tutorial"] != START

    @staticmethod
    def finished(state: dict) -> bool:
        return state["player"]["tutorial"] == COMPLETE

    def _advance(self, state: dict, progress: int, now: int) -> None:
        state["player"]["tutorial"] = progress
        self.journal.record(
            state,
            f"tutorial-{progress}",
            TUTORIAL_PROGRESS,
            now,
            {"progress": progress},
        )

    def finish(self, state: dict, now: int) -> None:
        self._advance(state, COMPLETE, now)

    def quests(self) -> list[int]:
        return [self.masters.property("Int", key) for key in QUEST_PROPERTIES]

    def complete_quest(self, state: dict, quest_id: int, now: int) -> bool:
        quests = self.quests()
        if quest_id not in quests:
            return False
        progress = _QUEST_PROGRESS[quests.index(quest_id)]
        if state["player"]["tutorial"] >= progress:
            return False
        self._advance(state, progress, now)
        return True

    def gacha(self) -> str:
        return self.masters.property("String", 2)

    def complete_gacha(self, state: dict, gacha_id: str, now: int) -> bool:
        return gacha_id == self.gacha() and self.complete_step(state, GACHA, now)

    def complete_step(self, state: dict, action: str, now: int) -> bool:
        if action not in _STEP_ACTIONS:
            raise ValueError(f"Unknown tutorial step: {action}")
        progress, pending = _NEXT_STEP.get(state["player"]["tutorial"], (None, None))
        if pending != action:
            return False
        self._advance(state, progress, now)
        return True
