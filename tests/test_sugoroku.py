import pytest

from monmusu_ps.core.errors import ApiNotFound
from monmusu_ps.core.sugoroku_stages import BONUS, MAIN, Board, Stage
from test_battle import NOW, runtime, state
from test_quest_gates import give


__all__ = ["runtime", "state"]

STAGE, VERSION = 10019, "board-1"
TICKET, GOLD = "wallet.event.pt_sugoroku260727", "wallet.point.gold"
PASSING, EXACT = 37, 38
BOSS = [100072, 100072, 100072]
MOB = [100070, 100070, 100070]
TAG = f"sugoroku.reward:{STAGE}"


def cell(identity, category, exits, values=(), flags=0):
    return {
        "id": identity,
        "category": category,
        "values": list(values),
        "connections": list(exits),
        "flags": flags,
    }


MAIN_CELLS = [
    cell(1, 1, [2]),
    cell(2, 30200, [3], [PASSING]),
    cell(3, 20100, [4], [2]),
    cell(4, 30000, [5], [2]),
    cell(5, 20200, [6], [1]),
    cell(6, 20400, [7, 10]),
    cell(7, 40000, [8], [4, 2]),
    cell(8, 30100, [9], [1]),
    cell(9, 10100, [12], MOB),
    cell(10, 10000, [11], MOB),
    cell(11, 30400, [12], [PASSING]),
    cell(12, 20000, [13]),
    cell(13, 2, [], BOSS),
]
BONUS_CELLS = [
    cell(1, 1, [2]),
    cell(2, 30200, [3], [PASSING]),
    cell(3, 30500, [2], [EXACT, PASSING], flags=1),
]


@pytest.fixture
def game(runtime, state, monkeypatch):
    abilities = {
        row["cell_category"]: row["ability_id"]
        for row in runtime.masters.table("sugoroku_cell_info_data")
    }
    boards = {MAIN: Board(MAIN_CELLS, abilities), BONUS: Board(BONUS_CELLS, abilities)}
    stages = runtime.sugoroku.stages
    board, shipped = Stage(VERSION, boards), stages.stage
    monkeypatch.setattr(
        stages, "stage", lambda row: board if row["stage_id"] == STAGE else shipped(row)
    )
    give(runtime, state, TICKET, 2)
    return runtime.sugoroku


def begin(game, state, **fields):
    payload = {
        "stage_id": STAGE,
        "stage_file_ver": VERSION,
        "game_lv": 1,
        "cost_batch": 1,
        "piece_chara_id": 11,
        **fields,
    }
    return game.start(state, payload, NOW)


def resume(game, state, **fields):
    return begin(game, state, game_lv=0, cost_batch=0, piece_chara_id=0, **fields)


def play(state):
    return state["sugoroku"]["session"]


def exec_turn(game, state, item=None, branch=0):
    payload = {
        "txid": play(state)["txid"],
        "selected_item_for_roll": item,
        "select_branche": branch,
    }
    return game.advance(state, payload)["context"]


def roll(game, state, face):
    name = f"fix_dice_{face}"
    play(state)["items"][name] = play(state)["items"].get(name, 0) + 1
    return exec_turn(game, state, name)


def close(game, state):
    game.end_turn(state, {"txid": play(state)["txid"]})


def path(context):
    return [point["id"] for point in context["turn_ctx"]["waypoints"]]


def battle(game, state, result="COMPLETE", units=(), rating=3):
    payload = {
        "txid": play(state)["txid"],
        "result": result,
        "statement": {"rating": rating},
        "used_unit_instances": list(units),
    }
    return game.finish_battle(state, payload)


def settle(game, state, txid=None):
    return game.finish(state, {"txid": txid or play(state)["txid"]}, NOW)


def fixed(runtime, lottery, lap):
    return sum(
        row["item_num"]
        for row in runtime.masters.table("sugoroku_reward_data")
        if row["lottery_id"] == lottery and row["loop_start"] == lap
    )


def prize(cell_id, step, amount, board=MAIN):
    return {
        "mno": board,
        "cid": cell_id,
        "step": step,
        "rewards": [{"rid": GOLD, "val": amount}],
    }


def at_branch(game, state):
    roll(game, state, 5)
    close(game, state)


def at_goal(game, state):
    at_branch(game, state)
    roll(game, state, 6)
    return exec_turn(game, state, branch=10)


def in_bonus(game, state):
    at_goal(game, state)
    return battle(game, state)


def test_a_new_game_pays_its_tickets_and_opens_on_the_start_cell(runtime, state, game):
    opened = begin(game, state, cost_batch=2)
    wallet = opened.sync["wallet"][0]["data"]
    assert [(row["category"], row["name"], row["val"]) for row in wallet] == [
        ("event", "pt_sugoroku260727", 0)
    ]
    assert set(opened.data) == {"txid", "context", "saved_deck", "piece_chara_id"}
    assert (opened.data["saved_deck"], opened.data["piece_chara_id"]) == (None, 11)
    assert opened.data["context"] == {
        "game_lv": 1,
        "cost_batch": 2,
        "map_ctx": {
            "map_no": 1,
            "laps": 0,
            "steps": 0,
            "times_of_rolled": 0,
            "times_of_battle_win": 0,
            "flags": 0,
        },
        "turn_ctx": {
            "turn": 1,
            "phase": "READY",
            "start_step": 0,
            "dice_roll": 0,
            "effect_items": [],
            "waypoints": [{"id": 1, "ex": None}],
            "rewards_in_turn": [],
            "inv_items_in_turn": [],
            "branches": [],
        },
        "num_of_dice": 15,
        "used_units": [],
        "item_inventory": [
            {"name": f"fix_dice_{face}", "quantity": 1} for face in range(1, 7)
        ],
        "reserved_rewards": [],
    }
    assert game.query(state) == {"stage_id": STAGE, "stage_list": []}


def test_an_open_game_is_resumed_without_paying_again(runtime, state, game):
    opened = begin(game, state)
    roll(game, state, 1)
    for again in (resume(game, state), begin(game, state)):
        assert again.sync is None
        assert again.data["txid"] == opened.data["txid"]
        assert again.data["piece_chara_id"] == 11
        assert again.data["context"]["turn_ctx"]["phase"] == "WAIT_ARRIVAL"
        assert path(again.data["context"]) == [1, 2]
    assert runtime.resources.balance(state, TICKET) == 1
    stale = resume(game, state, stage_file_ver="board-0")
    assert stale.data["context"]["turn_ctx"]["phase"] == "DIFF_STAGE_FILE_VER"
    assert play(state)["turn"]["phase"] == "WAIT_ARRIVAL"
    with pytest.raises(ValueError):
        resume(game, state, stage_id=10018)


@pytest.mark.parametrize(
    "fields",
    [
        {"stage_file_ver": "board-0"},
        {"cost_batch": 0},
        {"cost_batch": 3},
        {"cost_batch": 6},
        {"game_lv": 0},
        {"game_lv": "1"},
        {"piece_chara_id": None},
        {"stage_id": 999999},
        {"game_lv": 0, "cost_batch": 0, "piece_chara_id": 0},
    ],
)
def test_starts_that_cannot_be_played_are_refused(runtime, state, game, fields):
    with pytest.raises((ValueError, KeyError)):
        begin(game, state, **fields)
    assert play(state) is None
    assert runtime.resources.balance(state, TICKET) == 2


def test_stage_requirements_and_dates_follow_the_settings(runtime, state, game):
    settings = runtime.config.read()["sugoroku"]
    row = runtime.masters.lookup("sugoroku_data", "stage_id", STAGE)
    row["need_stage_id"] = 10018
    with pytest.raises(ValueError):
        begin(game, state)
    state["sugoroku"]["cleared"].append(10018)
    settings["enforce_master_dates"] = True
    with pytest.raises(ValueError):
        begin(game, state)
    settings.update(enforce_master_dates=False, charge_tickets=False)
    assert begin(game, state, cost_batch=5).sync is None
    assert runtime.resources.balance(state, TICKET) == 2


def test_a_roll_walks_the_route_and_reserves_the_chest(runtime, state, game):
    begin(game, state)
    context = exec_turn(game, state, "fix_dice_1")
    chest = prize(2, 1, fixed(runtime, PASSING, 0))
    assert path(context) == [1, 2]
    assert context["turn_ctx"]["phase"] == "WAIT_ARRIVAL"
    assert context["turn_ctx"]["dice_roll"] == 1
    assert context["turn_ctx"]["rewards_in_turn"] == [chest]
    assert context["reserved_rewards"] == [chest]
    assert context["num_of_dice"] == 14
    assert (context["map_ctx"]["steps"], context["map_ctx"]["times_of_rolled"]) == (
        1,
        1,
    )
    assert "fix_dice_1" not in {row["name"] for row in context["item_inventory"]}
    close(game, state)
    turn = play(state)["turn"]
    assert (turn["turn"], turn["phase"], turn["start_step"]) == (2, "READY", 1)
    assert turn["waypoints"] == [{"id": 2, "ex": None}]
    assert turn["rewards_in_turn"] == []
    for unheld in ("fix_dice_1", "fix_dice_7", "dice", 3, ["fix_dice_2"]):
        with pytest.raises(ValueError):
            exec_turn(game, state, unheld)
    assert play(state)["num_of_dice"] == 14


def test_an_unaided_roll_uses_a_die_face(runtime, state, game):
    begin(game, state)
    context = exec_turn(game, state)
    assert 1 <= context["turn_ctx"]["dice_roll"] <= 6
    assert len(context["item_inventory"]) == 6
    assert context["num_of_dice"] >= 14


def test_a_roll_overtakes_an_unfinished_turn(runtime, state, game):
    begin(game, state)
    roll(game, state, 1)
    context = roll(game, state, 4)
    assert (context["turn_ctx"]["turn"], context["turn_ctx"]["start_step"]) == (2, 1)
    assert path(context) == [2, 3, 4, 5, 6]
    close(game, state)
    close(game, state)
    assert play(state)["turn"]["turn"] == 3


def test_move_cells_chain_until_the_piece_settles(runtime, state, game):
    begin(game, state)
    context = roll(game, state, 2)
    assert path(context) == [1, 2, 3, 4, 5, 4]
    assert context["turn_ctx"]["phase"] == "WAIT_ARRIVAL"
    assert context["turn_ctx"]["rewards_in_turn"] == []
    assert context["turn_ctx"]["inv_items_in_turn"] == [
        {"mno": 1, "cid": 4, "step": 5, "items": [{"name": "dice", "quantity": 2}]}
    ]
    assert context["num_of_dice"] == 16
    assert context["map_ctx"]["steps"] == 5
    assert all(row["name"] != "dice" for row in context["item_inventory"])


def test_a_chain_stops_at_its_limit(runtime, state, game):
    runtime.config.read()["sugoroku"]["chain_limit"] = 1
    begin(game, state)
    context = roll(game, state, 2)
    assert path(context) == [1, 2, 3, 4, 5]
    assert context["num_of_dice"] == 14


def test_a_branch_waits_for_the_chosen_exit(runtime, state, game):
    begin(game, state)
    at_branch(game, state)
    waiting = roll(game, state, 2)
    assert path(waiting) == [6]
    assert waiting["turn_ctx"]["phase"] == "WAIT_BRANCH"
    assert waiting["turn_ctx"]["branches"] == []
    assert "remain" not in waiting["turn_ctx"]
    assert resume(game, state).data["context"]["turn_ctx"]["phase"] == "WAIT_BRANCH"
    for wrong in (9, 6, 99):
        with pytest.raises(ValueError):
            exec_turn(game, state, branch=wrong)
    with pytest.raises(ValueError):
        exec_turn(game, state, branch="10")
    with pytest.raises(ValueError):
        roll(game, state, 1)
    moved = exec_turn(game, state, branch=10)
    assert path(moved) == [6, 10, 11]
    assert moved["turn_ctx"]["phase"] == "WAIT_ARRIVAL"
    assert moved["turn_ctx"]["rewards_in_turn"] == [
        prize(11, 7, fixed(runtime, PASSING, 0))
    ]
    with pytest.raises(ValueError):
        exec_turn(game, state, branch=11)


def test_a_walk_keeps_its_route_across_a_branch(runtime, state, game):
    begin(game, state)
    waiting = roll(game, state, 6)
    assert path(waiting) == [1, 2, 3, 4, 5, 6]
    assert waiting["turn_ctx"]["phase"] == "WAIT_BRANCH"
    moved = exec_turn(game, state, branch=10)
    assert path(moved) == [1, 2, 3, 4, 5, 6, 10]
    assert moved["turn_ctx"]["phase"] == "WAIT_ARRIVAL_OR_BATTLE"
    assert moved["turn_ctx"]["dice_roll"] == 6


@pytest.mark.parametrize(("weights", "drawn"), [({"30000": 0}, 2), ({"30200": 0}, 4)])
def test_a_random_cell_names_the_cell_it_drew(runtime, state, game, weights, drawn):
    runtime.config.read()["sugoroku"]["random_cell_weights"] = weights
    begin(game, state)
    roll(game, state, 6)
    context = exec_turn(game, state, branch=7)
    turn = context["turn_ctx"]
    assert path(context) == [1, 2, 3, 4, 5, 6, 7]
    assert turn["waypoints"][-1]["ex"] == f'{{"ref_id": {drawn}}}'
    assert all(point["ex"] is None for point in turn["waypoints"][:-1])
    if drawn == 2:
        assert turn["rewards_in_turn"] == [prize(7, 6, fixed(runtime, PASSING, 0))]
        assert context["num_of_dice"] == 14
    else:
        assert turn["inv_items_in_turn"] == [
            {"mno": 1, "cid": 7, "step": 6, "items": [{"name": "dice", "quantity": 2}]}
        ]
        assert context["num_of_dice"] == 16


def test_dice_cells_never_leave_a_negative_count(runtime, state, game):
    begin(game, state)
    at_branch(game, state)
    play(state)["num_of_dice"] = 1
    roll(game, state, 2)
    context = exec_turn(game, state, branch=7)
    assert path(context) == [6, 7, 8]
    assert context["num_of_dice"] == 0
    assert context["turn_ctx"]["inv_items_in_turn"][0]["items"] == [
        {"name": "dice", "quantity": -1}
    ]


def test_a_forced_battle_stops_the_walk(runtime, state, game):
    begin(game, state)
    at_branch(game, state)
    roll(game, state, 6)
    context = exec_turn(game, state, branch=7)
    assert path(context) == [6, 7, 8, 9]
    assert context["turn_ctx"]["phase"] == "WAIT_BATTLE"
    assert context["num_of_dice"] == 13
    close(game, state)
    assert play(state)["turn"]["phase"] == "WAIT_BATTLE"
    with pytest.raises(ValueError):
        roll(game, state, 1)
    won = battle(game, state, units=[1, 0, 2])
    assert won["next_stage_context"] is None
    assert won["context"]["turn_ctx"]["phase"] == "READY"
    assert path(won["context"]) == [6, 7, 8, 9]
    assert won["context"]["used_units"] == [1, 2]
    assert won["context"]["map_ctx"]["times_of_battle_win"] == 1
    turn = play(state)["turn"]
    assert (turn["phase"], turn["waypoints"]) == ("READY", [{"id": 9, "ex": None}])
    with pytest.raises(ValueError):
        battle(game, state)
    leaving = roll(game, state, 3)
    assert path(leaving) == [9, 12, 13]
    assert leaving["turn_ctx"]["phase"] == "WAIT_BATTLE"


def test_a_lost_forced_battle_ends_the_game_with_its_rewards(runtime, state, game):
    begin(game, state)
    roll(game, state, 1)
    close(game, state)
    roll(game, state, 4)
    close(game, state)
    roll(game, state, 6)
    exec_turn(game, state, branch=7)
    lost = battle(game, state, "FAILURE", rating=0)
    assert lost["context"]["turn_ctx"]["phase"] == "ENDED_WITH_DEFEAT"
    assert lost["next_stage_context"] is None
    assert resume(game, state).data["context"]["turn_ctx"]["phase"] == (
        "ENDED_WITH_DEFEAT"
    )
    with pytest.raises(ValueError):
        roll(game, state, 1)
    ended = settle(game, state)
    amount = fixed(runtime, PASSING, 0)
    assert ended.data["rewards"] == [
        {"rid": GOLD, "val": amount, "inc": 0, "inbox": amount, "over": 0}
    ]
    assert [row["map_no"] for row in ended.data["map_summaries"]] == [1]
    assert game.query(state) == {"stage_id": 0, "stage_list": []}


def offered(game, state):
    begin(game, state)
    at_branch(game, state)
    roll(game, state, 1)
    return exec_turn(game, state, branch=10)


def test_an_offered_battle_may_be_left_alone(runtime, state, game):
    context = offered(game, state)
    assert path(context) == [6, 10]
    assert context["turn_ctx"]["phase"] == "WAIT_ARRIVAL_OR_BATTLE"
    lost = battle(game, state, "FAILURE", rating=0)
    assert lost["context"]["turn_ctx"]["phase"] == "WAIT_ARRIVAL_OR_BATTLE"
    close(game, state)
    assert play(state)["turn"]["phase"] == "READY"
    assert play(state)["maps"]["1"]["times_of_battle_win"] == 0


def test_battle_cells_pay_what_the_settings_and_the_quest_grant(runtime, state, game):
    runtime.config.read()["sugoroku"]["battle_cell_rewards"] = {
        "10000": [{"rid": GOLD, "val": 3000}]
    }
    runtime.masters.lookup("sugoroku_quest_data", "id", MOB[0])["dice_add"] = 2
    offered(game, state)
    won = battle(game, state, units=[1])
    turn = won["context"]["turn_ctx"]
    assert turn["rewards_in_turn"] == [prize(10, 6, 3000)]
    assert turn["inv_items_in_turn"] == [
        {"mno": 1, "cid": 10, "step": 6, "items": [{"name": "dice", "quantity": 2}]}
    ]
    assert won["context"]["num_of_dice"] == 15
    assert won["context"]["reserved_rewards"] == [prize(10, 6, 3000)]
    assert play(state)["turn"]["rewards_in_turn"] == []


@pytest.mark.parametrize(
    "fields",
    [
        {"units": [1, 1]},
        {"units": [99]},
        {"units": [1, 2]},
        {"units": "1"},
        {"units": [1.0]},
        {"rating": 0},
        {"rating": "3"},
        {"result": "SKIP"},
        {"result": "RETIRE"},
    ],
)
def test_battle_reports_are_checked(runtime, state, game, fields):
    runtime.masters.lookup("sugoroku_quest_data", "id", MOB[0])["deck_max"] = 1
    offered(game, state)
    with pytest.raises(ValueError):
        battle(game, state, **fields)
    assert play(state)["used_units"] == []


def test_a_unit_wins_one_battle_in_a_game(runtime, state, game):
    offered(game, state)
    battle(game, state, units=[1])
    roll(game, state, 3)
    with pytest.raises(ValueError):
        battle(game, state, units=[1])
    assert battle(game, state, units=[2])["context"]["used_units"] == [1, 2]


def test_beating_the_boss_opens_the_bonus_map(runtime, state, game):
    begin(game, state)
    arrived = at_goal(game, state)
    passed = prize(11, 7, fixed(runtime, PASSING, 0))
    assert path(arrived) == [6, 10, 11, 12, 13]
    assert arrived["turn_ctx"]["phase"] == "WAIT_BATTLE"
    assert arrived["turn_ctx"]["rewards_in_turn"] == [passed]
    assert arrived["map_ctx"]["flags"] == 0
    with pytest.raises(ValueError):
        battle(game, state, "SKIP", rating=1)
    won = battle(game, state, units=[1, 2])
    assert won["context"]["map_ctx"]["map_no"] == 1
    assert won["context"]["turn_ctx"]["phase"] == "READY"
    assert path(won["context"]) == [6, 10, 11, 12, 13]
    bonus = won["next_stage_context"]
    assert bonus["map_ctx"] == {
        "map_no": 2,
        "laps": 0,
        "steps": 0,
        "times_of_rolled": 0,
        "times_of_battle_win": 0,
        "flags": 0,
    }
    assert bonus["turn_ctx"]["phase"] == "READY"
    assert bonus["turn_ctx"]["waypoints"] == [{"id": 1, "ex": None}]
    assert bonus["turn_ctx"]["start_step"] == 0
    assert bonus["num_of_dice"] == 13 + 4
    assert bonus["used_units"] == [1, 2]
    assert bonus["reserved_rewards"] == [passed]
    assert resume(game, state).data["context"] == bonus
    close(game, state)
    assert play(state)["turn"]["phase"] == "READY"


def test_a_beaten_boss_can_be_skipped(runtime, state, game):
    state["sugoroku"]["cleared"].append(STAGE)
    opened = begin(game, state)
    assert opened.data["context"]["map_ctx"]["flags"] == 4
    at_branch(game, state)
    roll(game, state, 6)
    exec_turn(game, state, branch=7)
    battle(game, state, units=[1, 2])
    roll(game, state, 3)
    skipped = battle(game, state, "SKIP", units=[1, 2], rating=1)
    assert skipped["next_stage_context"]["map_ctx"]["map_no"] == 2
    assert skipped["next_stage_context"]["map_ctx"]["flags"] == 0
    assert skipped["next_stage_context"]["used_units"] == [1, 2]
    assert play(state)["maps"]["1"]["times_of_battle_win"] == 2


def test_skipping_needs_no_clear_when_the_setting_says_so(runtime, state, game):
    runtime.config.read()["sugoroku"]["boss_skip_requires_clear"] = False
    begin(game, state)
    at_goal(game, state)
    assert battle(game, state, "SKIP", rating=1)["next_stage_context"] is not None


def test_the_checkpoint_pays_by_the_lap(runtime, state, game):
    begin(game, state)
    in_bonus(game, state)
    first = roll(game, state, 2)
    exact = [fixed(runtime, EXACT, lap) for lap in range(3)]
    passing = [fixed(runtime, PASSING, lap) for lap in range(4)]
    assert path(first) == [1, 2, 3]
    assert first["map_ctx"]["laps"] == 1
    assert first["turn_ctx"]["rewards_in_turn"] == [
        prize(3, 2, exact[0] + passing[0], BONUS)
    ]
    close(game, state)
    second = roll(game, state, 2)
    assert path(second) == [3, 2, 3]
    assert second["map_ctx"]["laps"] == 2
    assert second["turn_ctx"]["rewards_in_turn"] == [
        prize(3, 4, exact[1] + passing[1], BONUS)
    ]
    close(game, state)
    chest = roll(game, state, 1)
    assert chest["turn_ctx"]["rewards_in_turn"] == [prize(2, 5, passing[2], BONUS)]
    close(game, state)
    passed = roll(game, state, 2)
    assert path(passed) == [2, 3, 2]
    assert passed["map_ctx"]["laps"] == 3
    assert passed["turn_ctx"]["rewards_in_turn"] == [
        prize(3, 6, passing[2], BONUS),
        prize(2, 7, passing[3], BONUS),
    ]


@pytest.mark.parametrize(("overflow", "paid"), [("last", True), ("none", False)])
def test_laps_past_the_table_follow_the_overflow_rule(
    runtime, state, game, overflow, paid
):
    runtime.config.read()["sugoroku"]["lap_overflow"] = overflow
    begin(game, state)
    in_bonus(game, state)
    play(state)["maps"]["2"]["laps"] = 30
    context = roll(game, state, 2)
    last = fixed(runtime, EXACT, 9) + fixed(runtime, PASSING, 9)
    expected = [prize(3, 2, last, BONUS)] if paid else []
    assert context["turn_ctx"]["rewards_in_turn"] == expected


def test_the_game_is_over_when_the_dice_run_out(runtime, state, game):
    begin(game, state)
    play(state)["num_of_dice"] = 1
    roll(game, state, 1)
    close(game, state)
    assert play(state)["turn"]["phase"] == "ENDED_WITH_THROW_UP"
    assert play(state)["turn"]["waypoints"] == [{"id": 2, "ex": None}]
    resumed = resume(game, state).data["context"]
    assert resumed["turn_ctx"]["phase"] == "ENDED_WITH_THROW_UP"
    with pytest.raises(ValueError):
        roll(game, state, 1)
    close(game, state)
    assert len(settle(game, state).data["rewards"]) == 1


def test_the_end_mails_the_reserved_rewards_once(runtime, state, game):
    opened = begin(game, state, cost_batch=2)
    in_bonus(game, state)
    roll(game, state, 2)
    txid = opened.data["txid"]
    total = 2 * (2 * fixed(runtime, PASSING, 0) + fixed(runtime, EXACT, 0))
    ended = settle(game, state)
    assert ended.sync is None
    assert ended.data["rewards"] == [
        {"rid": GOLD, "val": total, "inc": 0, "inbox": total, "over": 0}
    ]
    assert [row["map_no"] for row in ended.data["map_summaries"]] == [1, 2]
    assert ended.data["map_summaries"][1] == {
        "map_no": 2,
        "laps": 1,
        "steps": 2,
        "times_of_rolled": 1,
        "times_of_battle_win": 0,
        "flags": 0,
    }
    letters = [row for row in state["inbox"] if row["tags"][0] == TAG]
    title = runtime.masters.lookup("sugoroku_data", "stage_id", STAGE)["box_message"]
    assert [(row["message"], row["reward"]) for row in letters] == [
        (title, {"rid": GOLD, "val": total})
    ]
    assert game.query(state) == {"stage_id": 0, "stage_list": [STAGE]}
    assert settle(game, state, txid).data == ended.data
    assert len([row for row in state["inbox"] if row["tags"][0] == TAG]) == 1
    with pytest.raises(ValueError):
        settle(game, state, "sugoroku-unknown")
    with pytest.raises(ValueError):
        exec_turn_after_end(game, state, txid)


def exec_turn_after_end(game, state, txid):
    payload = {"txid": txid, "selected_item_for_roll": None, "select_branche": 0}
    return game.advance(state, payload)


def test_rewards_can_be_granted_directly(runtime, state, game):
    runtime.config.read()["sugoroku"].update(
        delivery="direct", multiply_by_cost_batch=False
    )
    begin(game, state, cost_batch=2)
    roll(game, state, 1)
    held = runtime.resources.balance(state, GOLD)
    amount = fixed(runtime, PASSING, 0)
    ended = settle(game, state)
    assert ended.data["rewards"] == [
        {"rid": GOLD, "val": amount, "inc": amount, "inbox": 0, "over": 0}
    ]
    assert runtime.resources.balance(state, GOLD) == held + amount
    assert ended.sync["wallet"][0]["data"][0]["val"] == held + amount
    assert state["inbox"] == []
    runtime.config.read()["sugoroku"]["delivery"] = "courier"
    give(runtime, state, TICKET, 1)
    begin(game, state)
    with pytest.raises(ValueError):
        settle(game, state)


def test_a_game_without_rewards_still_reports_its_map(runtime, state, game):
    begin(game, state)
    ended = settle(game, state)
    assert ended.data["rewards"] == []
    assert len(ended.data["map_summaries"]) == 1
    assert state["sugoroku"]["cleared"] == []
    assert state["sugoroku"]["session"] is None


def test_the_battle_deck_is_kept_for_the_next_resume(runtime, state, game):
    opened = begin(game, state)
    txid = opened.data["txid"]
    deck = {"slots": ['{"unit":1}', '{"bom":2}'], "exdata": '{"skin":[]}'}
    game.save_deck(state, {"txid": txid, "deck": deck})
    assert resume(game, state).data["saved_deck"] == deck
    game.save_deck(state, {"txid": txid, "deck": {"slots": [], "exdata": None}})
    assert resume(game, state).data["saved_deck"] == {"slots": [], "exdata": None}
    malformed = [None, [], {"slots": None, "exdata": ""}, {"slots": [1], "exdata": ""}]
    for wrong in (*malformed, {"slots": [], "exdata": 5}):
        with pytest.raises(ValueError):
            game.save_deck(state, {"txid": txid, "deck": wrong})
    with pytest.raises(ApiNotFound):
        game.save_deck(state, {"txid": "sugoroku-unknown", "deck": deck})


def test_selecting_a_dice_item_twice_takes_it_back(runtime, state, game):
    opened = begin(game, state)
    txid = opened.data["txid"]

    def select(name):
        game.select_item(state, {"txid": txid, "item_name": name})
        return resume(game, state).data["context"]["turn_ctx"]["effect_items"]

    assert select("fix_dice_2") == ["fix_dice_2"]
    assert select("fix_dice_5") == ["fix_dice_5"]
    assert select("fix_dice_5") == []
    for unheld in ("fix_dice_9", "dice", None, 2):
        with pytest.raises(ValueError):
            select(unheld)
    assert select("fix_dice_3") == ["fix_dice_3"]
    context = exec_turn(game, state, "fix_dice_1")
    assert context["turn_ctx"]["effect_items"] == []
    assert select("fix_dice_4") == []
    with pytest.raises(ApiNotFound):
        game.select_item(state, {"txid": "sugoroku-unknown", "item_name": "fix_dice_2"})
    with pytest.raises(ApiNotFound):
        game.end_turn(state, {"txid": "sugoroku-unknown"})


def test_requests_for_another_game_are_refused(runtime, state, game):
    with pytest.raises(ValueError):
        settle(game, state, "sugoroku-unknown")
    with pytest.raises(ApiNotFound):
        game.end_turn(state, {"txid": "sugoroku-unknown"})
    begin(game, state)
    for action in (
        lambda: exec_turn_after_end(game, state, "sugoroku-unknown"),
        lambda: game.finish_battle(
            state,
            {
                "txid": "sugoroku-unknown",
                "result": "COMPLETE",
                "statement": {"rating": 3},
                "used_unit_instances": [],
            },
        ),
    ):
        with pytest.raises(ValueError):
            action()
    with pytest.raises(ValueError):
        battle(game, state)
