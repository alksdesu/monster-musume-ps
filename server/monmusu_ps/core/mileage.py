from copy import deepcopy
import math

from .errors import ApiNotFound, ApiRejected
from .sync import Result, diff_state


SOURCE = "mileage.reward"
OUT_OF_SCHEDULE = "OUT_OF_SCHEDULE"
INVALID_MATERIAL = "HAS_INVALID_MATERIAL"
UNUSABLE_MATERIAL = "HAS_CANT_CONSUMABLE_MATERIAL"
NOT_ENOUGH_MATERIAL = "NOT_ENOUGH_MATERIAL"


class Mileage:
    def __init__(self, masters, config, resources):
        self.masters = masters
        self.config = config
        self.resources = resources

    def _programs(self) -> dict:
        return self.config.read()["mileage_programs"]

    def _program(self, program_id, now: int) -> dict:
        programs = self._programs()
        if not isinstance(program_id, str) or program_id not in programs:
            raise ApiNotFound(f"mileage_program.{program_id}")
        program = programs[program_id]
        if not self.masters.in_term(program["start_at"], program["end_at"], now):
            raise ApiRejected(OUT_OF_SCHEDULE)
        return program

    @staticmethod
    def _record(state: dict, program_id: str) -> dict:
        saved = state["mileage"].get(program_id, {"point": 0, "iterations": 0})
        return {"program_id": program_id, **saved}

    def get(self, state: dict, payload: dict, now: int) -> dict:
        self._program(payload["program_id"], now)
        return {"program_record": self._record(state, payload["program_id"])}

    @staticmethod
    def _advance(saved: dict, program: dict, gain: int) -> list[dict]:
        lap = program["lap_points"] or math.inf
        position, remaining, earned = saved["point"], gain, []
        while True:
            reach = min(position + remaining, lap)
            earned += [
                benefit
                for benefit in program["benefits"]
                if position < benefit["point"] <= reach
            ]
            if position + remaining < lap:
                saved["point"] = position + remaining
                return earned
            remaining -= lap - position
            position = 0
            saved["iterations"] += 1

    def commit(self, state: dict, payload: dict, now: int) -> Result:
        program_id, items = payload["program_id"], payload["items"]
        program = self._program(program_id, now)
        if not isinstance(items, list):
            raise ValueError("items must be a list of resource orders")
        if not items:
            raise ApiRejected(UNUSABLE_MATERIAL)
        rates, gains, costs = program["materials"], [], []
        for order in items:
            rid, amount = order["rid"], order["val"]
            if (
                not isinstance(rid, str)
                or rid not in rates
                or type(amount) is not int
                or amount < 1
            ):
                raise ApiRejected(INVALID_MATERIAL)
            gains.append({"rid": rid, "gain": rates[rid] * amount})
            costs.append({"rid": rid, "val": amount})
        before = deepcopy(state)
        self.resources.charge(state, costs, now, NOT_ENOUGH_MATERIAL)
        saved = state["mileage"].setdefault(program_id, {"point": 0, "iterations": 0})
        earned = self._advance(saved, program, sum(row["gain"] for row in gains))
        rewards = [deepcopy(benefit["reward"]) for benefit in earned]
        source = f"{SOURCE}:{program_id}"
        self.resources.grant(state, rewards, now, policy="mail", source=source)
        return Result(
            {
                "committed_point": gains,
                "benefits": [
                    {"reward": benefit["reward"], "point": benefit["point"]}
                    for benefit in earned
                ],
                "program_record": self._record(state, program_id),
            },
            diff_state(before, state),
        )

    def records(self, state: dict, range_names) -> list[dict]:
        if not isinstance(range_names, list):
            raise ValueError("range_names must be a list of program ids")
        return [
            self._record(state, program_id)
            for program_id in self._programs()
            if not range_names
            or any(
                program_id == name or program_id.startswith(f"{name}.")
                for name in range_names
            )
        ]
