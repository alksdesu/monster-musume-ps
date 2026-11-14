from collections import defaultdict

from .errors import ApiNotFound


STEP = "step"
SOURCES = (
    "gacha_data",
    "gacha_step",
    "gacha_extra_prize_data",
    "gacha_rnd_extra_prize_data",
    "gacha_stamp_sheet_data",
    "gacha_stamp_cell_data",
)
STAGE_TEXTS = (
    "cost_type",
    "cost_name",
    "prize_table_id",
    "alt_prize_table_id",
    "ex_prize_group_id",
)
INHERITED_COST = -1


class GachaCatalog:
    def __init__(self, masters):
        self.masters = masters
        self._documents = ()
        self._pools: dict[str, dict] = {}
        self._windows: dict[str, tuple[int | None, int | None]] = {}
        self._stages: dict[str, list[dict]] = {}
        self._extras: dict[str, list[dict]] = {}
        self._randoms: dict[str, list[dict]] = {}
        self._sheets: dict[int, dict[int, dict]] = {}

    def _refresh(self) -> None:
        documents = tuple(self.masters.document(name) for name in SOURCES)
        if len(documents) == len(self._documents) and all(
            current is previous for current, previous in zip(documents, self._documents)
        ):
            return
        groups, steps, extras, randoms, sheets, cells = (
            document["table"] for document in documents
        )
        moments: dict[str, int | None] = {}

        def moment(text: str) -> int | None:
            if text not in moments:
                moments[text] = self.masters.timestamp(text) if text else None
            return moments[text]

        pools, windows = {}, {}
        for group in groups:
            for pool in group["enclosure"]:
                if pool["id"] in pools:
                    raise ValueError(f"Duplicate gacha pool {pool['id']}")
                pools[pool["id"]] = pool
                windows[pool["id"]] = (moment(pool["start_at"]), moment(pool["end_at"]))
        stages: dict[str, list[dict]] = defaultdict(list)
        for row in steps:
            stages[row["step_id"]].append(row)
        stamps: dict[int, dict[int, dict]] = {}
        for sheet in sheets:
            stamps.setdefault(sheet["id"], {})[sheet["step"]] = {
                "reset": bool(sheet["reset"]),
                "cells": {},
            }
        for cell in cells:
            stamps[cell["stamp_id"]][cell["step"]]["cells"][cell["cell_index"]] = cell
        self._pools = dict(sorted(pools.items()))
        self._windows = windows
        self._stages = dict(stages)
        self._extras = {group["id"]: group["prizes"] for group in extras}
        self._randoms = {group["id"]: group["prizes"] for group in randoms}
        self._sheets = stamps
        self._documents = documents

    def pool(self, identity) -> dict:
        self._refresh()
        if not isinstance(identity, str) or identity not in self._pools:
            raise ApiNotFound(str(identity))
        return self._pools[identity]

    def pools(self) -> list[dict]:
        self._refresh()
        return list(self._pools.values())

    def started(self, pool: dict, now: int) -> bool:
        start = self._windows[pool["id"]][0]
        return start is None or start <= now

    def ended(self, pool: dict, now: int) -> bool:
        end = self._windows[pool["id"]][1]
        return end is not None and end <= now

    def stage(self, pool: dict, number: int) -> dict:
        if pool["gacha_type"] != STEP:
            return pool
        self._refresh()
        rows = self._stages.get(pool["id"].rsplit(".", 1)[-1], ())
        reached = [row for row in rows if row["step_start"] <= number]
        if not reached:
            return pool
        step = reached[-1]
        stage = {**pool, **{field: step[field] for field in STAGE_TEXTS if step[field]}}
        if step["cost_num"] != INHERITED_COST:
            stage["cost_num"] = step["cost_num"]
        return stage

    def extras(self, group: str) -> list[dict]:
        self._refresh()
        if group not in self._extras:
            raise ValueError(f"Unknown gacha extra prize group {group}")
        return self._extras[group]

    def randoms(self, group: str) -> list[dict]:
        self._refresh()
        if group not in self._randoms:
            raise ValueError(f"Unknown gacha random extra prize group {group}")
        return self._randoms[group]

    def sheets(self, stamp_id: int) -> dict[int, dict]:
        self._refresh()
        if stamp_id not in self._sheets:
            raise ValueError(f"Unknown gacha stamp card {stamp_id}")
        return self._sheets[stamp_id]
