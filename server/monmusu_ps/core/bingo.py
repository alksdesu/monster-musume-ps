from copy import deepcopy

from .errors import ApiRejected
from .sync import Result, diff_state


SIZE = 6
CELLS = SIZE * SIZE
LINE_TYPES = ("Row", "Col", "Slash", "BackSlash")
IN_PROGRESS, COMPLETE, RECEIVED = "in_progress", "complete", "received"
SOURCE = "bingo.reward"
INVALID_BINGO, OUT_OF_SCHEDULE = "INVALID_BINGO", "OUT_OF_SCHEDULE"
NOT_ENOUGH_COST, OVER_EXECUTE = "NOT_ENOUGH_COST", "OVER_EXECUTE"
ALREADY_OPEN = "ALREADY_OPEN_INDEX"
INVALID_LINE, NOT_COMPLETE = "INVALID_BINGO_LINE", "NOT_COMPLETE"
ALREADY_RECEIVED = "ALREADY_RECEIVED"


def _cells(kind: str, index: int) -> tuple[int, ...]:
    steps = range(SIZE)
    if kind == "Row":
        return tuple(index * SIZE + step for step in steps)
    if kind == "Col":
        return tuple(step * SIZE + index for step in steps)
    if kind == "Slash":
        return tuple((step + 1) * (SIZE - 1) for step in steps)
    return tuple(step * (SIZE + 1) for step in steps)


LINES = {
    (kind, index): _cells(kind, index)
    for kind, count in zip(LINE_TYPES, (SIZE, SIZE, 1, 1))
    for index in range(count)
}


class Bingo:
    def __init__(self, masters, config, resources, rng):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.rng = rng

    def _settings(self) -> dict:
        return self.config.read()["bingo"]

    def _campaign(self, bingo_id, now: int) -> dict:
        if type(bingo_id) is not int:
            raise ValueError("bingo_id must be an integer")
        try:
            campaign = self.masters.lookup("bingo_data", "id", bingo_id)
        except KeyError as error:
            raise ApiRejected(INVALID_BINGO) from error
        term = campaign["start_dt"], campaign["end_dt"]
        scheduled = self._settings()["follow_master_schedule"]
        if scheduled and not self.masters.in_term(*term, now):
            raise ApiRejected(OUT_OF_SCHEDULE)
        return campaign

    @staticmethod
    def _wallet(category: str, name: str) -> str:
        return f"wallet.{category}.{name}"

    @staticmethod
    def _progress(state: dict, campaign: dict) -> dict:
        return state["bingo"].setdefault(
            str(campaign["id"]),
            {"open_indexes": [], "received_lines": [], "complete_received": False},
        )

    @staticmethod
    def _made(progress: dict) -> list[tuple[str, int]]:
        opened = set(progress["open_indexes"])
        return [line for line, cells in LINES.items() if opened.issuperset(cells)]

    def _record(self, bingo_id: int, progress: dict) -> dict:
        made, received = self._made(progress), progress["received_lines"]
        status = IN_PROGRESS
        if len(made) == len(LINES):
            status = RECEIVED if progress["complete_received"] else COMPLETE
        return {
            "bingo_id": bingo_id,
            "open_indexes": list(progress["open_indexes"]),
            "line_statuses": [
                {
                    "line_type": kind,
                    "line_index": index,
                    "line_status": RECEIVED if [kind, index] in received else COMPLETE,
                }
                for kind, index in made
            ],
            "bingo_status": status,
        }

    def records(self, state: dict) -> dict:
        return {
            "bingo_records": [
                self._record(int(identity), progress)
                for identity, progress in state["bingo"].items()
            ]
        }

    def _opened(self, campaign: dict, progress: dict, made: list) -> dict:
        return {
            "complete_lines": [
                {"line_type": kind, "line_index": index}
                for kind, index in self._made(progress)
                if (kind, index) not in made
            ],
            "is_complete_bingo": len(progress["open_indexes"]) == CELLS,
            "updated_record": self._record(campaign["id"], progress),
        }

    def lottery(self, state: dict, payload: dict, now: int) -> Result:
        campaign = self._campaign(payload["bingo_id"], now)
        draws = payload["exec_num"]
        if type(draws) is not int or draws < 1:
            raise ValueError("exec_num must be a positive integer")
        progress = self._progress(state, campaign)
        opened = progress["open_indexes"]
        if draws > self._settings()["max_draws"] or len(opened) == CELLS:
            raise ApiRejected(OVER_EXECUTE)
        ticket = self._wallet(campaign["price_category"], campaign["price_name"])
        if self.resources.balance(state, ticket) < campaign["price"] * draws:
            raise ApiRejected(NOT_ENOUGH_COST)
        before, made = deepcopy(state), self._made(progress)
        drawn, repeats, fresh = [], 0, False
        # A sheet completed midway ends the run; only the draws made are paid for.
        while len(drawn) < draws and len(opened) < CELLS:
            index = self.rng.randrange(CELLS)
            fresh = index not in opened
            drawn.append(index)
            if fresh:
                opened.append(index)
            else:
                repeats += 1
        opened.sort()
        costs = [{"rid": ticket, "val": campaign["price"] * len(drawn)}]
        self.resources.charge(state, costs, now, NOT_ENOUGH_COST)
        if repeats:
            # The client credits one duplicate point for every repeated cell itself.
            points = self._wallet(campaign["dupli_category"], campaign["dupli_name"])
            consolation = [{"rid": points, "val": repeats}]
            self.resources.grant(state, consolation, now, policy="discard")
        return Result(
            {
                "open_index": drawn[-1],
                "open_indexes": drawn,
                "is_new_index": fresh,
                **self._opened(campaign, progress, made),
            },
            diff_state(before, state),
        )

    def open(self, state: dict, payload: dict, now: int) -> Result:
        campaign = self._campaign(payload["bingo_id"], now)
        target = payload["target_index"]
        if type(target) is not int or not 0 <= target < CELLS:
            raise ValueError("target_index must be a cell of the sheet")
        progress = self._progress(state, campaign)
        if target in progress["open_indexes"]:
            raise ApiRejected(ALREADY_OPEN)
        before, made = deepcopy(state), self._made(progress)
        points = self._wallet(campaign["dupli_category"], campaign["dupli_name"])
        costs = [{"rid": points, "val": campaign["dupli_point"]}]
        self.resources.charge(state, costs, now, NOT_ENOUGH_COST)
        progress["open_indexes"] = sorted([*progress["open_indexes"], target])
        return Result(
            {
                "open_index": target,
                "is_new_index": True,
                **self._opened(campaign, progress, made),
            },
            diff_state(before, state),
        )

    def _reward(self, state: dict, campaign: dict, rows: list[dict], now: int) -> None:
        orders = [order for row in rows if (order := self.resources.reward_order(row))]
        source = f"{SOURCE}:{campaign['id']}"
        self.resources.grant(state, orders, now, policy="mail", source=source)

    def _line_prize(self, campaign: dict, line: list) -> dict:
        groups = self.masters.document("bingo_line_data")["table"]
        prizes = next(
            (
                group["lineData"]
                for group in groups
                if group["lineData"][0]["group_id"] == campaign["table_id"]
            ),
            (),
        )
        for prize in prizes:
            if [LINE_TYPES[prize["lineType"]], prize["lineIndex"]] == line:
                return prize
        raise ApiRejected(INVALID_LINE)

    def receive_line(self, state: dict, payload: dict, now: int) -> Result:
        campaign = self._campaign(payload["bingo_id"], now)
        line = [payload["line_type"], payload["index"]]
        prize = self._line_prize(campaign, line)
        progress = self._progress(state, campaign)
        if tuple(line) not in self._made(progress):
            raise ApiRejected(NOT_COMPLETE)
        if line in progress["received_lines"]:
            raise ApiRejected(ALREADY_RECEIVED)
        before = deepcopy(state)
        self._reward(state, campaign, prize["reward"]["m_rewards"], now)
        progress["received_lines"].append(line)
        return Result(
            {"updated_record": self._record(campaign["id"], progress)},
            diff_state(before, state),
        )

    def receive_complete(self, state: dict, payload: dict, now: int) -> Result:
        campaign = self._campaign(payload["bingo_id"], now)
        progress = self._progress(state, campaign)
        if len(progress["open_indexes"]) < CELLS:
            raise ApiRejected(NOT_COMPLETE)
        if progress["complete_received"]:
            raise ApiRejected(ALREADY_RECEIVED)
        before = deepcopy(state)
        self._reward(state, campaign, campaign["rewards"]["m_rewards"], now)
        progress["complete_received"] = True
        return Result(
            {"updated_record": self._record(campaign["id"], progress)},
            diff_state(before, state),
        )
