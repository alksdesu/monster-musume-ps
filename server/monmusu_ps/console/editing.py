from contextlib import asynccontextmanager
from copy import deepcopy

from ..core.sync import COLLECTIONS, diff_state
from . import catalog


SYNCED = {"player", "modules", *(state_key for state_key, _ in COLLECTIONS.values())}
# Bookkeeping the game never sees, so a change there is nothing the operator has to wait for.
INTERNAL = frozenset({"events", "resource_sequences"})


def dropped(before: dict, after: dict) -> bool:
    for state_key, keys in COLLECTIONS.values():
        kept = {tuple(row[key] for key in keys) for row in after[state_key]}
        if any(
            tuple(row[key] for key in keys) not in kept for row in before[state_key]
        ):
            return True
    return False


# Every console edit of a save goes through here, so the game is told the same way each time.
class Editing:
    def __init__(self, runtime):
        self.runtime = runtime

    def known(self, profile: str) -> str:
        if not self.runtime.store.exists(profile):
            raise KeyError(f"Unknown save: {profile}")
        return profile

    @asynccontextmanager
    async def edit(self, profile: str):
        runtime = self.runtime
        async with runtime.store.transaction(self.known(profile)) as state:
            before, outcome = deepcopy(state), {}
            yield state, outcome
            sync = diff_state(before, state)
            outcome["synced"] = sync is not None
            # The client learns of removed rows only by loading the save again.
            outcome["dropped"] = dropped(before, state)
            outcome["unsynced"] = [
                {"name": name, "title": catalog.section_label(name)}
                for name in sorted(state)
                if name not in SYNCED
                and name not in INTERNAL
                and state[name] != before.get(name)
            ]
            runtime.outbox.post(profile, sync)
