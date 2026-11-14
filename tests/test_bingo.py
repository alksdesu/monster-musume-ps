from copy import deepcopy
import json
from pathlib import Path
import random
from types import SimpleNamespace

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.features.bingo import ROUTES
from monmusu_ps.runtime import Runtime
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
CODE = "BINGO001"
BINGO, FIRST = 10, 1
TICKET = "wallet.ticket.bingo_lotteryticket221107"
POINTS = "wallet.point.duplicate221107"
GOLD = "wallet.point.gold"
PRIZE_CARD = 349


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: NOW, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def state(runtime):
    return create_player("bingo", CODE, runtime.masters, runtime.config.read(), NOW)


def give(runtime, state, rid, amount=1):
    runtime.resources.grant(state, [{"rid": rid, "val": amount}], NOW, policy="discard")


def stock(runtime, state, rid):
    return runtime.resources.balance(state, rid)


def rig(runtime, *cells):
    upcoming = iter(cells)
    runtime.bingo.rng = SimpleNamespace(randrange=lambda _: next(upcoming))


def sheet(state, *cells, bingo=BINGO):
    state["bingo"][str(bingo)] = {
        "open_indexes": sorted(cells),
        "received_lines": [],
        "complete_received": False,
    }


def rejection(action):
    with pytest.raises(ApiRejected) as refused:
        action()
    return refused.value.reason


def draw(runtime, state, times, bingo=BINGO):
    return runtime.bingo.lottery(state, {"bingo_id": bingo, "exec_num": times}, NOW)


def punch(runtime, state, cell, bingo=BINGO):
    return runtime.bingo.open(state, {"bingo_id": bingo, "target_index": cell}, NOW)


def claim(runtime, state, kind, index, bingo=BINGO):
    payload = {"bingo_id": bingo, "line_type": kind, "index": index}
    return runtime.bingo.receive_line(state, payload, NOW)


def test_new_players_have_no_bingo_records(runtime, state):
    assert runtime.bingo.records(state) == {"bingo_records": []}


def test_each_draw_costs_a_ticket_and_repeats_earn_a_duplicate_point(runtime, state):
    give(runtime, state, TICKET, 4)
    rig(runtime, 0, 7, 0)
    result = draw(runtime, state, 3)
    record = {
        "bingo_id": BINGO,
        "open_indexes": [0, 7],
        "line_statuses": [],
        "bingo_status": "in_progress",
    }
    assert result.data == {
        "open_index": 0,
        "open_indexes": [0, 7, 0],
        "is_new_index": False,
        "complete_lines": [],
        "is_complete_bingo": False,
        "updated_record": record,
    }
    assert (stock(runtime, state, TICKET), stock(runtime, state, POINTS)) == (1, 1)
    assert {
        (row["category"], row["name"], row["val"])
        for row in result.sync["wallet"][0]["data"]
    } == {("ticket", "bingo_lotteryticket221107", 1), ("point", "duplicate221107", 1)}
    assert runtime.bingo.records(state) == {"bingo_records": [record]}


def test_duplicate_points_stop_at_the_wallet_cap(runtime, state):
    give(runtime, state, TICKET, 1)
    give(runtime, state, POINTS, 99)
    sheet(state, 3)
    rig(runtime, 3)
    draw(runtime, state, 1)
    assert stock(runtime, state, POINTS) == 99


def test_lottery_refusals(runtime, state):
    assert rejection(lambda: draw(runtime, state, 1)) == "NOT_ENOUGH_COST"
    give(runtime, state, TICKET, 20)
    assert rejection(lambda: draw(runtime, state, 11)) == "OVER_EXECUTE"
    assert rejection(lambda: draw(runtime, state, 1, bingo=99)) == "INVALID_BINGO"
    for times in (0, -1, "1", None):
        with pytest.raises(ValueError):
            draw(runtime, state, times)
    with pytest.raises(ValueError):
        draw(runtime, state, 1, bingo="10")
    runtime.config.read()["bingo"]["follow_master_schedule"] = True
    assert rejection(lambda: draw(runtime, state, 1)) == "OUT_OF_SCHEDULE"
    assert stock(runtime, state, TICKET) == 20


def test_a_chosen_cell_costs_duplicate_points(runtime, state):
    sheet(state, 0, 1, 2, 3, 4)
    assert rejection(lambda: punch(runtime, state, 5)) == "NOT_ENOUGH_COST"
    give(runtime, state, POINTS, 7)
    assert rejection(lambda: punch(runtime, state, 4)) == "ALREADY_OPEN_INDEX"
    for cell in (36, -1, "5", None):
        with pytest.raises(ValueError):
            punch(runtime, state, cell)
    result = punch(runtime, state, 5)
    assert result.data == {
        "open_index": 5,
        "is_new_index": True,
        "complete_lines": [{"line_type": "Row", "line_index": 0}],
        "is_complete_bingo": False,
        "updated_record": {
            "bingo_id": BINGO,
            "open_indexes": [0, 1, 2, 3, 4, 5],
            "line_statuses": [
                {"line_type": "Row", "line_index": 0, "line_status": "complete"}
            ],
            "bingo_status": "in_progress",
        },
    }
    assert stock(runtime, state, POINTS) == 2
    assert [
        (row["category"], row["name"], row["val"])
        for row in result.sync["wallet"][0]["data"]
    ] == [("point", "duplicate221107", 2)]


@pytest.mark.parametrize(
    ("kind", "index", "cells"),
    [
        ("Row", 2, (12, 13, 14, 15, 16, 17)),
        ("Col", 4, (4, 10, 16, 22, 28, 34)),
        ("Slash", 0, (5, 10, 15, 20, 25, 30)),
        ("BackSlash", 0, (0, 7, 14, 21, 28, 35)),
    ],
)
def test_line_rewards_are_claimed_once_their_cells_are_open(
    runtime, state, kind, index, cells
):
    sheet(state, *cells[:-1])
    assert rejection(lambda: claim(runtime, state, kind, index)) == "NOT_COMPLETE"
    sheet(state, *cells)
    prize = runtime.bingo._line_prize(
        runtime.masters.lookup("bingo_data", "id", BINGO), [kind, index]
    )
    orders = [
        runtime.resources.reward_order(row) for row in prize["reward"]["m_rewards"]
    ]
    held = [stock(runtime, state, order["rid"]) for order in orders]
    result = claim(runtime, state, kind, index)
    assert result.data["updated_record"]["line_statuses"] == [
        {"line_type": kind, "line_index": index, "line_status": "received"}
    ]
    assert [stock(runtime, state, order["rid"]) for order in orders] == [
        before + order["val"] for before, order in zip(held, orders)
    ]
    assert result.sync
    assert rejection(lambda: claim(runtime, state, kind, index)) == "ALREADY_RECEIVED"


@pytest.mark.parametrize(
    ("kind", "index"),
    [("Row", 6), ("Slash", 1), ("Diagonal", 0), ("Row", "0"), (None, 0)],
)
def test_unknown_lines_are_refused(runtime, state, kind, index):
    sheet(state, *range(36))
    assert rejection(lambda: claim(runtime, state, kind, index)) == "INVALID_BINGO_LINE"


def test_the_run_ends_when_the_sheet_completes(runtime, state):
    give(runtime, state, TICKET, 5)
    sheet(state, *range(35))
    finish = {"bingo_id": BINGO}
    receive = runtime.bingo.receive_complete
    assert rejection(lambda: receive(state, finish, NOW)) == "NOT_COMPLETE"
    rig(runtime, 35, 1, 2)
    result = draw(runtime, state, 3)
    assert result.data["open_indexes"] == [35]
    assert result.data["is_new_index"] is True
    assert result.data["is_complete_bingo"] is True
    assert {
        (row["line_type"], row["line_index"]) for row in result.data["complete_lines"]
    } == {("Row", 5), ("Col", 5), ("BackSlash", 0)}
    record = result.data["updated_record"]
    assert (record["bingo_status"], len(record["line_statuses"])) == ("complete", 14)
    assert stock(runtime, state, TICKET) == 4
    assert rejection(lambda: draw(runtime, state, 1)) == "OVER_EXECUTE"
    received = receive(state, finish, NOW)
    assert received.data["updated_record"]["bingo_status"] == "received"
    assert state["units"][-1]["card_id"] == PRIZE_CARD
    assert "unit" in received.sync
    assert rejection(lambda: receive(state, finish, NOW)) == "ALREADY_RECEIVED"


def test_rewards_beyond_capacity_arrive_by_mail_under_the_bingo_tag(runtime, state):
    give(runtime, state, GOLD, 2_000_000_000)
    full = stock(runtime, state, GOLD)
    sheet(state, 6, 7, 8, 9, 10, 11, bingo=FIRST)
    claim(runtime, state, "Row", 1, bingo=FIRST)
    assert stock(runtime, state, GOLD) == full
    letter = state["inbox"][-1]
    assert letter["tags"][0] == f"bingo.reward:{FIRST}"
    assert letter["reward"] == {"rid": GOLD, "val": 50000}


@pytest.mark.parametrize(
    ("endpoint", "payload", "nested"),
    [
        ("bingo/record/all", {"_z0": 0}, None),
        ("bingo/lottery", {"bingo_id": BINGO, "exec_num": 1}, "updated_record"),
        ("bingo/open", {"bingo_id": BINGO, "target_index": 35}, "updated_record"),
        (
            "bingo/line/receive",
            {"bingo_id": BINGO, "line_type": "Row", "index": 0},
            "updated_record",
        ),
        ("bingo/complete/receive", {"bingo_id": BINGO}, "updated_record"),
    ],
)
def test_response_field_names_match_client_dto(
    runtime, state, endpoint, payload, nested
):
    give(runtime, state, TICKET, 1)
    give(runtime, state, POINTS, 5)
    sheet(state, *range(35))
    if endpoint == "bingo/complete/receive":
        sheet(state, *range(36))
    rig(runtime, 35)
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    context = Context(runtime, payload, state, runtime.sessions.create(CODE), NOW)
    result = ROUTES[endpoint](context)
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(field for field in schema[model]["fields"] if field["name"] == "data")
    assert set(result.data) == {row["name"] for row in schema[data["type"]]["fields"]}
    record = result.data[nested] if nested else result.data["bingo_records"][0]
    assert set(record) == {row["name"] for row in schema["BingoRecord"]["fields"]}
    assert set(record["line_statuses"][0]) == {
        row["name"] for row in schema["LineStatus"]["fields"]
    }
