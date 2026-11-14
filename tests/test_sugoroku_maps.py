import json
from pathlib import Path

import pytest

from monmusu_ps.core.sugoroku import RANDOM
from monmusu_ps.features.sugoroku import ROUTES
from test_battle import CODE, NOW, runtime, state
from test_catalog import Context
from test_quest_gates import give
from test_sugoroku import (
    STAGE,
    TICKET,
    VERSION,
    at_goal,
    begin,
    game,
    play,
)


__all__ = ["game", "runtime", "state"]

ROOT = Path(__file__).resolve().parents[1]
STAGES = range(10000, 10020)
CURRENT = {
    10017: "1784879263-f280",
    10018: "1784879277-8ef8",
    10019: "1784771926-6e7b",
}


def stage_of(runtime, stage_id):
    row = runtime.masters.lookup("sugoroku_data", "stage_id", stage_id)
    return row, runtime.sugoroku.stages.stage(row)


@pytest.mark.parametrize("stage_id", STAGES)
def test_every_stage_has_both_of_its_maps(runtime, stage_id):
    row, stage = stage_of(runtime, stage_id)
    assert stage.version
    for board in stage.boards.values():
        assert board.cell(1)["category"] == 1
        for identity, cell in board._cells.items():
            assert all(following in board._cells for following in cell["connections"])
            assert all(
                identity in board.exits(before) for before in board.entries(identity)
            )
    assert row["start_dice_count"] > 0


def test_the_current_stages_carry_the_versions_the_client_sends(runtime):
    for stage_id, version in CURRENT.items():
        assert stage_of(runtime, stage_id)[1].version == version


def linked(board, before, after):
    return after in board.exits(before) or after in board.entries(before)


@pytest.mark.parametrize("stage_id", STAGES)
def test_a_whole_game_keeps_the_route_the_client_can_follow(runtime, state, stage_id):
    settings = runtime.config.read()["sugoroku"]
    settings.update(charge_tickets=False, boss_skip_requires_clear=False)
    row, stage = stage_of(runtime, stage_id)
    state["sugoroku"]["cleared"].append(row["need_stage_id"])
    sugoroku = runtime.sugoroku
    opened = sugoroku.start(
        state,
        {
            "stage_id": stage_id,
            "stage_file_ver": stage.version,
            "game_lv": 1,
            "cost_batch": 1,
            "piece_chara_id": 11,
        },
        NOW,
    )
    txid = opened.data["txid"]
    position, maps = 1, {1}
    for _ in range(400):
        session = play(state)
        board = stage.boards[session["map_no"]]
        phase = session["turn"]["phase"]
        if phase.startswith("ENDED"):
            break
        if phase == "WAIT_BATTLE":
            result = "SKIP" if not board.exits(session["cell"]) else "COMPLETE"
            fought = sugoroku.finish_battle(
                state,
                {
                    "txid": txid,
                    "result": result,
                    "statement": {"rating": 1},
                    "used_unit_instances": [],
                },
            )
            following = fought["next_stage_context"]
            if following is not None:
                assert following["turn_ctx"]["waypoints"] == [{"id": 1, "ex": None}]
                position = 1
            maps.add(play(state)["map_no"])
            continue
        branch = 0
        if phase == "WAIT_BRANCH":
            branch = runtime.rng.choice(board.exits(session["cell"]))
        context = sugoroku.advance(
            state,
            {"txid": txid, "selected_item_for_roll": None, "select_branche": branch},
        )["context"]
        turn = context["turn_ctx"]
        points = [point["id"] for point in turn["waypoints"]]
        assert points[0] == position or branch
        assert all(linked(board, *pair) for pair in zip(points, points[1:]))
        assert context["map_ctx"]["steps"] == turn["start_step"] + len(points) - 1
        for key in ("effect_items", "rewards_in_turn", "inv_items_in_turn", "branches"):
            assert isinstance(turn[key], list)
        for entry in turn["rewards_in_turn"] + turn["inv_items_in_turn"]:
            index = entry["step"] - turn["start_step"]
            assert points[index] == entry["cid"]
        landed = turn["waypoints"][-1]
        drew = board.ability(landed["id"]) == RANDOM and turn["phase"] != "WAIT_BRANCH"
        if drew:
            assert (
                json.loads(landed["ex"])["ref_id"] in board.cell(landed["id"])["values"]
            )
        assert context["num_of_dice"] >= 0
        if turn["phase"] != "WAIT_BRANCH":
            position = points[-1]
            sugoroku.end_turn(state, {"txid": txid})
    else:
        pytest.fail("The game never ended")
    ended = sugoroku.finish(state, {"txid": txid}, NOW)
    assert [row["map_no"] for row in ended.data["map_summaries"]] == sorted(maps)
    assert isinstance(ended.data["rewards"], list)
    assert (stage_id in state["sugoroku"]["cleared"]) == (2 in maps)


def names(schema, model):
    return {row["name"] for row in schema[model]["fields"]}


def data_model(runtime, schema, endpoint):
    model = runtime.contracts[endpoint]["response_models"]["200"]
    fields = {row["name"]: row["type"] for row in schema[model]["fields"]}
    return fields.get("data")


def test_response_field_names_match_client_dto(runtime, state, game):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    session = runtime.sessions.create(CODE)

    def call(endpoint, payload):
        context = Context(runtime, payload, state, session, NOW)
        return ROUTES[endpoint](context)

    opened = call(
        "sugoroku/start",
        {
            "stage_id": STAGE,
            "stage_file_ver": VERSION,
            "game_lv": 1,
            "cost_batch": 1,
            "piece_chara_id": 11,
        },
    )
    txid = opened.data["txid"]
    assert set(opened.data) == names(
        schema, data_model(runtime, schema, "sugoroku/start")
    )
    context = opened.data["context"]
    assert set(context) == names(schema, "SugorokuContext")
    assert set(context["map_ctx"]) == names(schema, "SugorokuMapContext")
    assert set(context["turn_ctx"]) == names(schema, "SugorokuTurnContext")
    assert set(context["item_inventory"][0]) == names(schema, "SugorokuInvItem")
    assert set(context["turn_ctx"]["waypoints"][0]) == {"id", "ex"}
    queried = call("sugoroku/session/query", {"_z0": 0})
    assert set(queried.data) == names(
        schema, data_model(runtime, schema, "sugoroku/session/query")
    )
    for endpoint, payload in (
        ("sugoroku/ingame/item/select", {"txid": txid, "item_name": "fix_dice_2"}),
        (
            "sugoroku/ingame/deck/save",
            {"txid": txid, "deck": {"slots": [], "exdata": ""}},
        ),
        ("sugoroku/ingame/turn/end", {"txid": txid}),
    ):
        silent = call(endpoint, payload)
        assert (silent.data, silent.include_data) == (None, False)
        assert data_model(runtime, schema, endpoint) is None
    assert set(play(state)["deck"]) == names(schema, "SugorokuBattleDeck")
    rolled = call(
        "sugoroku/ingame/turn/exec",
        {"txid": txid, "selected_item_for_roll": "fix_dice_2", "select_branche": 0},
    )
    assert set(rolled.data) == names(
        schema, data_model(runtime, schema, "sugoroku/ingame/turn/exec")
    )
    turn = rolled.data["context"]["turn_ctx"]
    assert set(turn["inv_items_in_turn"][0]) == names(schema, "SugorokuCellInvItem")
    state["sugoroku"]["session"] = None
    give(runtime, state, TICKET, 1)
    begin(game, state)
    at_goal(game, state)
    assert set(play(state)["reserved"][0]) == names(schema, "SugorokuCellReward")
    fought = call(
        "sugoroku/ingame/battle/end",
        {
            "txid": play(state)["txid"],
            "result": "COMPLETE",
            "statement": {"rating": 3},
            "used_unit_instances": [1],
        },
    )
    assert set(fought.data) == names(
        schema, data_model(runtime, schema, "sugoroku/ingame/battle/end")
    )
    assert set(fought.data["next_stage_context"]) == names(schema, "SugorokuContext")
    ended = call("sugoroku/end", {"txid": play(state)["txid"]})
    assert set(ended.data) == names(schema, data_model(runtime, schema, "sugoroku/end"))
    assert set(ended.data["map_summaries"][0]) == names(schema, "SugorokuMapContext")
    assert set(ended.data["rewards"][0]) == {"rid", "val", "inc", "inbox", "over"}
