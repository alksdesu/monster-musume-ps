from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import uuid

from aiohttp.test_utils import TestClient, TestServer
import pytest

from monmusu_ps.app import RUNTIME, create_app
from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.transport import DecodedRequest, Protocol, encode_response
from monmusu_ps.runtime import Runtime
from test_server import NativeClient


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
CODE = "BATTLE01"
DECK = {"section": "unit_deck", "no": 1}
RECORD_FIELDS = [
    "category",
    "section",
    "quest_id",
    "record_rating",
    "wins",
    "tries",
    "flags",
    "ec_best_score",
    "ec_total_score",
    "t_best_rank",
    "wh_best_score",
    "wh_total_score",
    "log_key",
]
BATTLE_ENDPOINTS = {
    "quest/start",
    "quest/end",
    "quest/cycle_exec",
    "player/restore/stamina/coin",
    "player/restore/stamina/item",
    "tutorial/skip",
}


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
    return create_player("battle", CODE, runtime.masters, runtime.config.read(), NOW)


def opening(quest_id, **statement):
    return {
        "quest_id": quest_id,
        "statement": {
            "use_deck": DECK,
            "rental_unit": None,
            "rental_unit_iid": 0,
            "t_menus": [],
            "r_mode": 0,
            **statement,
        },
    }


def closing(txid, result="COMPLETE", rating=3, loot=(), log=()):
    return {
        "txid": txid,
        "result": result,
        "statement": {
            "rating": rating,
            "ec_score": 0,
            "tower": None,
            "loot_list": list(loot),
            "log_data": list(log),
        },
    }


def sweep(quest_id, count):
    return {
        "quest_id": quest_id,
        "num_of_exec": count,
        "statement": {"use_deck": DECK},
    }


def clear(runtime, state, quest_id, rating=3, now=NOW):
    started = runtime.battles.start(state, opening(quest_id), now)
    return runtime.battles.finish(
        state, closing(started.data["txid"], rating=rating), now
    )


def enlist(state, card_id):
    unit = next(row for row in state["units"] if row["card_id"] == card_id)
    deck = state["compositions"][0]
    number = sum(json.loads(raw)["t"] == 1 for raw in deck["slots"])
    slot = {
        "t": 1,
        "sn": number,
        "iid": unit["instance_id"],
        "sauto": True,
        "skillAuto": 1,
        "classActionAuto": 1,
        "sskills": {"s1": 0, "s2": 0},
        "exskill": 1,
    }
    deck["slots"].insert(number, json.dumps(slot, separators=(",", ":")))


def play_route(runtime, state, ratings):
    results = []
    for quest_id, rating in zip(range(10000, 10005), ratings):
        results.append(clear(runtime, state, quest_id, rating))
        for reward in results[-1].data["report"].get("initial_rewards", []):
            enlist(state, int(reward["rid"].removeprefix("unit.card.")))
    return results


def quest(runtime, quest_id=10000):
    return runtime.masters.lookup("quest_data", "id", quest_id)


def reward_row(name, count, chance, first_chance=0):
    return {
        "m_walletCategory": "item",
        "m_walletName": name,
        "m_count": count,
        "m_probability": chance,
        "m_firstProbability": first_chance,
    }


def master_time(moment):
    zone = timezone(timedelta(hours=9))
    return datetime.fromtimestamp(moment, zone).strftime("%Y/%m/%d %H:%M:%S")


def wallet(state, name, category="point"):
    return next(
        row
        for row in state["wallets"]
        if row["category"] == category and row["name"] == name
    )


def stamina(state):
    return state["modules"]["stamina"][0]


def experience(state):
    return state["modules"]["experience"][0]


def record(state, quest_id):
    return next(row for row in state["quest_records"] if row["quest_id"] == quest_id)


def progress(state):
    return {row["card_id"]: (row["t_exp"], row["r_unit_lv"]) for row in state["units"]}


def rejection(call):
    with pytest.raises(ApiRejected) as caught:
        call()
    return caught.value.reason


def test_start_returns_the_native_statement_and_counts_the_attempt(runtime, state):
    result = runtime.battles.start(state, opening(10000), NOW)
    txid = result.data["txid"]
    assert txid.startswith("questexec-")
    uuid.UUID(txid.removeprefix("questexec-"))
    assert result.data == {
        "txid": txid,
        "prepaid_costs": [],
        "postpaid_costs": [{"rid": "stamina.quest", "val": 8}],
        "drop_list": [],
        "sub_skill_list": [],
    }
    assert set(result.sync) == {"quest_record"}
    assert result.sync["quest_record"][0]["scope"] == f"player@{CODE}"
    row = result.sync["quest_record"][0]["data"][0]
    assert list(row) == RECORD_FIELDS
    assert {name: row[name] for name in RECORD_FIELDS[:-1]} == {
        "category": 1,
        "section": 1,
        "quest_id": 10000,
        "record_rating": 0,
        "wins": 0,
        "tries": 1,
        "flags": 0,
        "ec_best_score": 0,
        "ec_total_score": 0,
        "t_best_rank": 0,
        "wh_best_score": 0,
        "wh_total_score": 0,
    }
    assert len(row["log_key"]) == 15
    assert all(type(value) is int and 0 <= value <= 255 for value in row["log_key"])
    assert stamina(state)["amount"] == 70
    assert list(state["quest_transactions"]) == [txid]
    assert state["quest_transactions"][txid]["status"] == "active"


def test_restart_rotates_the_log_key_and_supersedes_the_open_battle(runtime, state):
    first = runtime.battles.start(state, opening(10000), NOW)
    key = list(record(state, 10000)["log_key"])
    second = runtime.battles.start(state, opening(10000), NOW)
    assert record(state, 10000)["tries"] == 2
    assert record(state, 10000)["log_key"] != key
    assert len(state["quest_records"]) == 1
    assert list(state["quest_transactions"]) == [second.data["txid"]]
    assert (
        rejection(
            lambda: runtime.battles.finish(state, closing(first.data["txid"]), NOW)
        )
        == "INVALID_QUEST"
    )


def test_unknown_and_locked_quests_are_rejected(runtime, state):
    battles = runtime.battles
    assert (
        rejection(lambda: battles.start(state, opening(999999999), NOW))
        == "INVALID_QUEST"
    )
    assert (
        rejection(lambda: battles.start(state, opening(10001), NOW)) == "NOT_QUALIFIED"
    )
    battles.start(state, opening(10000), NOW)
    assert (
        rejection(lambda: battles.start(state, opening(10001), NOW)) == "NOT_QUALIFIED"
    )
    quest(runtime)["needRank"] = 2
    assert (
        rejection(lambda: battles.start(state, opening(10000), NOW)) == "NOT_QUALIFIED"
    )
    experience(state)["level"] = 2
    battles.start(state, opening(10000), NOW)


@pytest.mark.parametrize(
    "field,offset,open",
    [
        ("openTime", 0, True),
        ("openTime", 1, False),
        ("closeTime", 1, True),
        ("closeTime", 0, False),
    ],
)
def test_schedule_is_start_inclusive_and_end_exclusive(
    runtime, state, field, offset, open
):
    quest(runtime)[field] = master_time(NOW + offset)
    if open:
        runtime.battles.start(state, opening(10000), NOW)
    else:
        assert (
            rejection(lambda: runtime.battles.start(state, opening(10000), NOW))
            == "OUT_OF_SCHEDULE"
        )


@pytest.mark.parametrize(
    "deck",
    [
        {"section": "unit_deck", "no": 2},
        {"section": "unit_deck", "no": 99},
        {"section": "missing", "no": 1},
    ],
)
def test_battle_needs_a_filled_deck(runtime, state, deck):
    assert (
        rejection(
            lambda: runtime.battles.start(state, opening(10000, use_deck=deck), NOW)
        )
        == "INVALID_STATEMENT"
    )


@pytest.mark.parametrize("instance", [1, 9999])
def test_deck_units_must_be_owned_and_distinct(runtime, state, instance):
    slots = state["compositions"][0]["slots"]
    slots[1] = json.dumps({**json.loads(slots[1]), "iid": instance})
    assert (
        rejection(lambda: runtime.battles.start(state, opening(10000), NOW))
        == "INVALID_STATEMENT"
    )


def test_entry_checks_the_postpaid_cost(runtime, state):
    stamina(state)["amount"] = 7
    assert (
        rejection(lambda: runtime.battles.start(state, opening(10000), NOW))
        == "NOT_ENOUGH_COST"
    )
    stamina(state)["amount"] = 8
    runtime.battles.start(state, opening(10000), NOW)


def test_first_clear_pays_the_recorded_tutorial_settlement(runtime, state):
    started = runtime.battles.start(state, opening(10000), NOW)
    result = runtime.battles.finish(state, closing(started.data["txid"]), NOW)
    assert result.data == {
        "quest_id": 10000,
        "result": "COMPLETE",
        "postpaid_costs": [],
        "report": {
            "regular_rewards": {
                "gold": 120,
                "rank_exp": 160,
                "unit_exp": 40,
                "unit_pt": 10,
            }
        },
        "team_log_key": f"quest.10000#{CODE}",
    }
    assert wallet(state, "gold")["val"] == 620
    assert stamina(state)["amount"] == 62
    assert experience(state)["exp"] == 160
    assert progress(state) == {11: (40, 2), 22: (40, 2)}
    row = record(state, 10000)
    assert (row["wins"], row["tries"], row["record_rating"], row["flags"]) == (
        1,
        1,
        3,
        3,
    )
    assert state["player"]["tutorial"] == 100
    assert set(result.sync) == {
        "player",
        "module_prop",
        "unit",
        "wallet",
        "quest_record",
        "mission_record",
        "mission_counter",
    }
    assert set(result.sync["module_prop"][0]["data"]) == {"stamina", "experience"}
    assert [row["name"] for row in result.sync["wallet"][0]["data"]] == ["gold"]


def test_tutorial_route_reproduces_the_recorded_progress(runtime, state):
    first, second, third, fourth = play_route(runtime, state, (3, 2, 3, 3))
    assert "initial_rewards" not in first.data["report"]
    assert second.data["report"]["initial_rewards"] == [
        {"rid": "unit.card.6", "val": 1}
    ]
    assert second.data["report"]["regular_rewards"]["gold"] == 96
    assert third.data["report"]["initial_rewards"] == [
        {"rid": "unit.card.57", "val": 1}
    ]
    assert "initial_rewards" not in fourth.data["report"]
    assert "master_rewards" not in fourth.data["report"]
    assert [record(state, quest_id)["flags"] for quest_id in range(10000, 10004)] == [
        3,
        0,
        3,
        3,
    ]
    assert state["player"]["tutorial"] == 500
    assert wallet(state, "gold")["val"] == 956
    assert (experience(state)["exp"], experience(state)["level"]) == (640, 2)
    assert (stamina(state)["amount"], stamina(state)["capacity"]) == (112, 74)
    assert progress(state) == {11: (160, 3), 22: (160, 3), 6: (80, 2), 57: (40, 2)}
    fifth = clear(runtime, state, 10004)
    assert fifth.data["report"]["master_rewards"] == [
        {"rid": "wallet.point.free_coin", "val": 100}
    ]
    assert state["player"]["tutorial"] == 900
    assert wallet(state, "gold")["val"] == 1076
    assert wallet(state, "free_coin")["val"] == 400
    assert progress(state) == {11: (200, 4), 22: (200, 4), 6: (120, 3), 57: (80, 2)}
    assert sorted(state["team_logs"]) == [
        f"quest.{quest_id}#{CODE}" for quest_id in range(10000, 10005)
    ]


def test_tutorial_quests_raise_the_progress_to_their_step(runtime, state):
    reached = []
    for quest_id in range(10000, 10005):
        clear(runtime, state, quest_id)
        reached.append(state["player"]["tutorial"])
    assert reached == [100, 200, 300, 500, 900]


def test_tutorial_quests_never_lower_a_finished_tutorial(runtime, state):
    state["player"]["tutorial"] = 9999
    clear(runtime, state, 10000)
    assert state["player"]["tutorial"] == 9999
    state["player"]["tutorial"] = 700
    clear(runtime, state, 10000)
    assert state["player"]["tutorial"] == 700


def test_first_time_rewards_are_paid_once(runtime, state):
    play_route(runtime, state, (3, 3, 3, 3, 2))
    assert wallet(state, "free_coin")["val"] == 300
    assert record(state, 10004)["flags"] == 0
    improved = clear(runtime, state, 10004)
    assert improved.data["report"]["master_rewards"] == [
        {"rid": "wallet.point.free_coin", "val": 100}
    ]
    assert record(state, 10004)["flags"] == 3
    repeated = clear(runtime, state, 10004)
    assert "master_rewards" not in repeated.data["report"]
    assert wallet(state, "free_coin")["val"] == 400
    units = len(state["units"])
    again = clear(runtime, state, 10001)
    assert "initial_rewards" not in again.data["report"]
    assert len(state["units"]) == units
    assert record(state, 10001)["wins"] == 2


@pytest.mark.parametrize("rating,gold,flags", [(1, 80, 0), (2, 96, 0), (3, 120, 3)])
def test_rating_scales_gold_and_marks_a_mastered_quest(
    runtime, state, rating, gold, flags
):
    result = clear(runtime, state, 10000, rating)
    assert result.data["report"]["regular_rewards"] == {
        "gold": gold,
        "rank_exp": 160,
        "unit_exp": 40,
        "unit_pt": 10,
    }
    assert record(state, 10000)["record_rating"] == rating
    assert record(state, 10000)["flags"] == flags


def test_a_lower_rating_keeps_the_best_record(runtime, state):
    clear(runtime, state, 10000, 3)
    clear(runtime, state, 10000, 1)
    row = record(state, 10000)
    assert (row["record_rating"], row["flags"], row["wins"]) == (3, 3, 2)


def test_battle_rules_are_bound_when_the_battle_starts(runtime, state):
    rules = runtime.config.read()["battle"]
    rules.update(
        gold_multiplier=2,
        rank_exp_multiplier=3,
        unit_exp_multiplier=0.5,
        unit_points=7,
    )
    started = runtime.battles.start(state, opening(10000), NOW)
    rules.update(gold_multiplier=9, rank_exp_multiplier=9, unit_points=1)
    result = runtime.battles.finish(state, closing(started.data["txid"]), NOW)
    assert result.data["report"]["regular_rewards"] == {
        "gold": 240,
        "rank_exp": 480,
        "unit_exp": 20,
        "unit_pt": 7,
    }
    assert wallet(state, "gold")["val"] == 740
    assert experience(state)["exp"] == 480


@pytest.mark.parametrize("percent,remaining", [(0, 70), (50, 66), (100, 62)])
def test_failure_grants_nothing_and_charges_the_configured_share(
    runtime, state, percent, remaining
):
    runtime.config.read()["battle"]["failure_cost_percent"] = percent
    started = runtime.battles.start(state, opening(10000), NOW)
    result = runtime.battles.finish(
        state, closing(started.data["txid"], "FAILURE", 0), NOW
    )
    assert result.data == {
        "quest_id": 10000,
        "result": "FAILURE",
        "postpaid_costs": [],
        "report": {
            "regular_rewards": {"gold": 0, "rank_exp": 0, "unit_exp": 0, "unit_pt": 0}
        },
        "team_log_key": None,
    }
    assert stamina(state)["amount"] == remaining
    assert wallet(state, "gold")["val"] == 500
    assert experience(state)["exp"] == 0
    assert progress(state) == {11: (0, 1), 22: (0, 1)}
    row = record(state, 10000)
    assert (row["wins"], row["tries"], row["record_rating"], row["flags"]) == (
        0,
        1,
        0,
        0,
    )
    assert state["player"]["tutorial"] == 0
    assert state["team_logs"] == {}
    assert state["events"][-1]["data"]["result"] == "FAILURE"
    assert state["events"][-1]["data"]["costs"] == [
        {"rid": "stamina.quest", "val": 8 * percent // 100}
    ]


def test_settlement_rechecks_the_postpaid_cost(runtime, state):
    started = runtime.battles.start(state, opening(10000), NOW)
    stamina(state)["amount"] = 3
    assert (
        rejection(
            lambda: runtime.battles.finish(state, closing(started.data["txid"]), NOW)
        )
        == "NOT_ENOUGH_COST"
    )


@pytest.mark.parametrize(
    "result,rating",
    [
        ("RETIRE", 3),
        ("complete", 3),
        ("COMPLETE", 0),
        ("COMPLETE", 4),
        ("COMPLETE", -1),
        ("COMPLETE", True),
        ("COMPLETE", "3"),
        ("COMPLETE", 2.0),
        ("FAILURE", 4),
    ],
)
def test_invalid_results_and_ratings_are_rejected(runtime, state, result, rating):
    started = runtime.battles.start(state, opening(10000), NOW)
    before = deepcopy(state)
    assert (
        rejection(
            lambda: runtime.battles.finish(
                state, closing(started.data["txid"], result, rating), NOW
            )
        )
        == "INVALID_STATEMENT"
    )
    assert state == before


def test_unknown_transactions_are_rejected(runtime, state):
    assert (
        rejection(
            lambda: runtime.battles.finish(
                state, closing(f"questexec-{uuid.uuid4()}"), NOW
            )
        )
        == "INVALID_QUEST"
    )


def test_loot_is_fixed_at_entry_and_paid_only_when_collected(runtime, state):
    quest(runtime)["droptable"]["m_rewards"] = [
        reward_row("item.material.Bom2", 2, 100),
        reward_row("item.material.Bom2", 5, 0),
        reward_row("item.material.Bom2", 3, 100),
    ]
    started = runtime.battles.start(state, opening(10000), NOW)
    assert started.data["drop_list"] == [
        {"slot": 0, "item": {"rid": "item.material.Bom2", "val": 2}},
        {"slot": 2, "item": {"rid": "item.material.Bom2", "val": 3}},
    ]
    quest(runtime)["droptable"]["m_rewards"] = []
    txid = started.data["txid"]
    for loot in ([1], [0, 0], [0, 3]):
        assert (
            rejection(
                lambda: runtime.battles.finish(state, closing(txid, loot=loot), NOW)
            )
            == "INVALID_STATEMENT"
        )
    result = runtime.battles.finish(state, closing(txid, loot=[2]), NOW)
    assert result.data["report"]["loot_rewards"] == [
        {"rid": "item.material.Bom2", "val": 3}
    ]
    assert runtime.resources.balance(state, "item.material.Bom2") == 3
    assert result.sync["item"][0]["data"] == [{"id": "item.material.Bom2", "val": 3}]


def test_uncollected_loot_is_not_paid(runtime, state):
    quest(runtime)["droptable"]["m_rewards"] = [
        reward_row("item.material.Bom2", 2, 100)
    ]
    started = runtime.battles.start(state, opening(10000), NOW)
    result = runtime.battles.finish(state, closing(started.data["txid"]), NOW)
    assert "loot_rewards" not in result.data["report"]
    assert runtime.resources.balance(state, "item.material.Bom2") == 0


def test_first_clear_chance_applies_until_the_first_win(runtime, state):
    quest(runtime)["droptable"]["m_rewards"] = [
        reward_row("item.material.Bom2", 1, 0, 100)
    ]
    lost = runtime.battles.start(state, opening(10000), NOW)
    assert len(lost.data["drop_list"]) == 1
    runtime.battles.finish(state, closing(lost.data["txid"], "FAILURE", 0), NOW)
    won = runtime.battles.start(state, opening(10000), NOW)
    assert len(won.data["drop_list"]) == 1
    runtime.battles.finish(state, closing(won.data["txid"], loot=[0]), NOW)
    assert runtime.battles.start(state, opening(10000), NOW).data["drop_list"] == []


def test_disabled_random_rewards_skip_drops_and_clear_rolls(runtime, state):
    row = quest(runtime)
    row["droptable"]["m_rewards"] = [reward_row("item.material.Bom2", 1, 100)]
    row["cleartable"]["m_rewards"] = [reward_row("item.material.Bom2", 1, 100)]
    started = runtime.battles.start(state, opening(10000), NOW, random_rewards=False)
    assert started.data["drop_list"] == []
    result = runtime.battles.finish(state, closing(started.data["txid"]), NOW)
    assert "clear_rewards" not in result.data["report"]
    assert state["items"] == []


def test_clear_rewards_list_every_winning_roll(runtime, state):
    quest(runtime)["cleartable"]["m_rewards"] = [
        reward_row("item.material.Bom2", 1, 100),
        reward_row("item.material.Bom2", 0, 100),
        reward_row("item.material.Bom2", 1, 100),
        reward_row("item.material.Bom2", 4, 0),
    ]
    result = clear(runtime, state, 10000)
    assert result.data["report"]["clear_rewards"] == [
        {"rid": "item.material.Bom2", "val": 1},
        {"rid": "item.material.Bom2", "val": 1},
    ]
    assert runtime.resources.balance(state, "item.material.Bom2") == 2


def test_disabled_costs_leave_stamina_untouched(runtime, state):
    started = runtime.battles.start(state, opening(10000), NOW, costs_enabled=False)
    assert started.data["postpaid_costs"] == []
    runtime.battles.finish(state, closing(started.data["txid"]), NOW)
    assert stamina(state)["amount"] == 70


def test_repeated_completion_is_replayed_without_another_settlement(runtime, state):
    started = runtime.battles.start(state, opening(10000), NOW)
    payload = closing(started.data["txid"])
    first = runtime.battles.finish(state, payload, NOW)
    settled = deepcopy(state)
    again = runtime.battles.finish(state, deepcopy(payload), NOW + 5)
    assert again.data == first.data
    assert set(again.sync) == set(first.sync)
    assert again.sync["player"][0]["data"] == [state["player"]]
    assert state == settled
    assert (
        rejection(
            lambda: runtime.battles.finish(
                state, closing(started.data["txid"], rating=2), NOW
            )
        )
        == "INVALID_STATEMENT"
    )
    assert state == settled


def test_completed_battles_keep_replay_data_within_the_retention_window(runtime, state):
    runtime.config.read()["retention"]["completed_quest_transactions"] = 3
    stamina(state)["amount"] = 999
    txids = []
    for _ in range(5):
        started = runtime.battles.start(state, opening(10000), NOW)
        txids.append(started.data["txid"])
        runtime.battles.finish(state, closing(txids[-1]), NOW)
    assert list(state["quest_transactions"]) == txids[-3:]
    for row in state["quest_transactions"].values():
        assert set(row) == {
            "status",
            "completed_at",
            "quest_id",
            "team",
            "completion_request",
            "response_data",
            "sync_keys",
        }
    assert (
        rejection(lambda: runtime.battles.finish(state, closing(txids[0]), NOW))
        == "INVALID_QUEST"
    )
    replay = runtime.battles.finish(state, closing(txids[-1]), NOW)
    assert replay.data["quest_id"] == 10000
    opened = runtime.battles.start(state, opening(10000), NOW)
    assert list(state["quest_transactions"]) == [*txids[-3:], opened.data["txid"]]
    runtime.battles.finish(state, closing(opened.data["txid"]), NOW)
    assert list(state["quest_transactions"]) == [*txids[-2:], opened.data["txid"]]


def test_team_log_keeps_one_entry_per_quest(runtime, state):
    clear(runtime, state, 10000)
    fielded = deepcopy(state["units"][:2])
    started = runtime.battles.start(state, opening(10000), NOW + 60)
    result = runtime.battles.finish(
        state, closing(started.data["txid"], log=[7, 8]), NOW + 60
    )
    key = f"quest.10000#{CODE}"
    assert result.data["team_log_key"] == key
    entry = state["team_logs"][key]
    team = entry.pop("team")
    assert state["team_logs"] == {
        key: {
            "quest_id": 10000,
            "created_at": NOW + 60,
            "rating": 3,
            "wild_hunt_score": 0,
            "txid": started.data["txid"],
            "log_data": [7, 8],
        }
    }
    assert [json.loads(slot["unit"]) for slot in team["units"]] == fielded
    assert [json.loads(slot["bom"]) for slot in team["boms"]] == state["boms"]
    assert team["rental"] is None


def test_team_log_keeps_the_best_rated_clear(runtime, state):
    clear(runtime, state, 10000)
    clear(runtime, state, 10000, rating=2, now=NOW + 60)
    key = f"quest.10000#{CODE}"
    assert (
        state["team_logs"][key]["rating"],
        state["team_logs"][key]["created_at"],
    ) == (
        3,
        NOW,
    )
    clear(runtime, state, 10000, now=NOW + 120)
    assert state["team_logs"][key]["created_at"] == NOW + 120


def test_team_snapshots_carry_the_deck_slot_skills(runtime, state):
    deck = state["compositions"][0]
    slot = json.loads(deck["slots"][0])
    slot.update(sskills={"s1": 1001, "s2": 0}, exskill=2)
    deck["slots"][0] = json.dumps(slot, separators=(",", ":"))
    state["units"][0]["aa"]["s2"] = 1002
    clear(runtime, state, 10000)
    team = state["team_logs"][f"quest.10000#{CODE}"]["team"]
    fielded = json.loads(team["units"][0]["unit"])
    assert fielded["aa"] == {"s1": 1001, "s2": 0, "s3": 0, "s4": 0}
    assert fielded["primary_exskill"] == 2
    assert state["units"][0]["aa"]["s2"] == 1002


def test_quests_without_team_logging_return_no_key(runtime, state):
    quest(runtime)["is_team_loging"] = 0
    result = clear(runtime, state, 10000)
    assert result.data["team_log_key"] is None
    assert state["team_logs"] == {}


def test_journal_keeps_the_latest_events(runtime, state):
    runtime.config.read()["retention"]["events"] = 2
    stamina(state)["amount"] = 999
    txids = []
    for _ in range(3):
        started = runtime.battles.start(state, opening(10000), NOW)
        txids.append(started.data["txid"])
        runtime.battles.finish(state, closing(txids[-1]), NOW)
    assert [event["id"] for event in state["events"]] == txids[-2:]
    assert state["events"][-1] == {
        "id": txids[-1],
        "type": "quest_complete",
        "at": NOW,
        "data": {
            "quest_id": 10000,
            "category": 1,
            "section": 1,
            "rating": 3,
            "result": "COMPLETE",
            "first_clear": False,
            "costs": [{"rid": "stamina.quest", "val": 8}],
            "kills": 0,
            "unit_ids": [1, 2],
            "support_owner": None,
            "support_card": None,
        },
    }


@pytest.mark.parametrize("count", [0, -1, 101, "2", True, 1.5])
def test_cycle_rejects_counts_outside_the_native_limit(runtime, state, count):
    clear(runtime, state, 10000)
    assert (
        rejection(lambda: runtime.battles.cycle(state, sweep(10000, count), NOW))
        == "TOO_LARGE_NUM_OF_CYCLE_EXEC"
    )


def test_cycle_requires_a_mastered_quest(runtime, state):
    assert (
        rejection(lambda: runtime.battles.cycle(state, sweep(10000, 1), NOW))
        == "NOT_QUALIFIED"
    )
    clear(runtime, state, 10000, 2)
    assert (
        rejection(lambda: runtime.battles.cycle(state, sweep(10000, 1), NOW))
        == "NOT_QUALIFIED"
    )


@pytest.mark.parametrize("tickets,amount", [(2, 70), (3, 23)])
def test_cycle_checks_the_whole_cost_before_paying(runtime, state, tickets, amount):
    clear(runtime, state, 10000)
    runtime.resources.grant(
        state, [{"rid": "wallet.ticket.quest_skip", "val": tickets}], NOW
    )
    stamina(state)["amount"] = amount
    before = deepcopy(state)
    assert (
        rejection(lambda: runtime.battles.cycle(state, sweep(10000, 3), NOW))
        == "NOT_ENOUGH_COST"
    )
    assert state == before


def test_cycle_settles_every_run_without_leaving_battle_records(runtime, state):
    clear(runtime, state, 10000)
    runtime.resources.grant(state, [{"rid": "wallet.ticket.quest_skip", "val": 5}], NOW)
    before = deepcopy(state)
    result = runtime.battles.cycle(state, sweep(10000, 3), NOW)
    assert result.data == {
        "quest_id": 10000,
        "num_of_exec": 3,
        "costs": [
            {"rid": "stamina.quest", "val": 24},
            {"rid": "wallet.ticket.quest_skip", "val": 3},
        ],
        "report": {
            "regular_rewards": {
                "gold": 360,
                "rank_exp": 480,
                "unit_exp": 120,
                "unit_pt": 30,
            },
            "clear_rewards": [],
            "loot_rewards": [],
            "skip_rewards": [{"rid": "wallet.unit_exp.sr_all", "val": 3}],
            "sub_skill_rewards": [],
        },
    }
    assert wallet(state, "gold")["val"] == 980
    assert wallet(state, "quest_skip", "ticket")["val"] == 2
    assert wallet(state, "sr_all", "unit_exp")["val"] == 3
    assert (experience(state)["exp"], experience(state)["level"]) == (640, 2)
    assert stamina(state)["amount"] == 112
    assert progress(state) == {11: (160, 3), 22: (160, 3)}
    row = record(state, 10000)
    assert (row["wins"], row["tries"], row["record_rating"]) == (4, 4, 3)
    assert row["log_key"] != record(before, 10000)["log_key"]
    assert state["quest_transactions"] == before["quest_transactions"]
    assert state["team_logs"] == before["team_logs"]
    added = state["events"][len(before["events"]) :]
    assert [row["type"] for row in added] == ["player_rank_up", "quest_cycle"]
    assert added[0]["data"] == {"from": 1, "to": 2}
    event = added[-1]
    assert event["id"].startswith("questcycle-")
    assert (event["type"], event["at"]) == ("quest_cycle", NOW)
    assert event["data"] == {
        "quest_id": 10000,
        "category": 1,
        "section": 1,
        "num_of_exec": 3,
        "costs": result.data["costs"],
        "unit_ids": [1, 2],
    }
    assert {"module_prop", "unit", "wallet", "quest_record"} <= set(result.sync)


def test_cycle_merges_rolled_rewards_by_resource(runtime, state):
    clear(runtime, state, 10000)
    quest(runtime)["cleartable"]["m_rewards"] = [
        reward_row("item.material.Bom2", 2, 100)
    ]
    runtime.resources.grant(state, [{"rid": "wallet.ticket.quest_skip", "val": 4}], NOW)
    result = runtime.battles.cycle(state, sweep(10000, 4), NOW)
    assert result.data["report"]["clear_rewards"] == [
        {"rid": "item.material.Bom2", "val": 8}
    ]
    assert runtime.resources.balance(state, "item.material.Bom2") == 8


def test_cycle_does_not_disturb_an_open_battle(runtime, state):
    clear(runtime, state, 10000)
    runtime.resources.grant(state, [{"rid": "wallet.ticket.quest_skip", "val": 1}], NOW)
    opened = runtime.battles.start(state, opening(10000), NOW)
    runtime.battles.cycle(state, sweep(10000, 1), NOW)
    result = runtime.battles.finish(state, closing(opened.data["txid"]), NOW)
    assert result.data["result"] == "COMPLETE"


async def connect(tmp_path):
    app = create_app(Settings.load(ROOT, tmp_path), clock=lambda: NOW)
    http = TestClient(TestServer(app))
    await http.start_server()
    client = NativeClient(http)
    await client.login()
    runtime = app[RUNTIME]
    return http, client, runtime, runtime.sessions.get(client.token).profile


async def post(http, client, endpoint, payload):
    body = encode_response(
        payload, DecodedRequest({}, Protocol.AES, None), client.password
    )
    return await http.post(
        "/api/" + endpoint,
        data=body,
        headers={"x-ag-protocol": "2", "x-ag-token": client.token},
    )


async def test_battle_endpoints_are_registered(tmp_path):
    http, _, runtime, _ = await connect(tmp_path)
    try:
        assert BATTLE_ENDPOINTS <= set(runtime.registry.handlers)
    finally:
        await http.close()


async def test_native_battle_round_trip_persists_the_settlement(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        started = await client.call("quest/start", opening(10000))
        assert set(started) == {"status", "data", "sync", "log", "timesync", "to"}
        assert started["status"] == {"code": 1, "message": "succeeded"}
        assert started["timesync"] == NOW
        assert started["data"]["postpaid_costs"] == [{"rid": "stamina.quest", "val": 8}]
        ended = await client.call("quest/end", closing(started["data"]["txid"]))
        code = ended["sync"]["player"][0]["data"][0]["player_code"]
        assert ended["data"]["team_log_key"] == f"quest.10000#{code}"
        assert ended["data"]["report"]["regular_rewards"] == {
            "gold": 120,
            "rank_exp": 160,
            "unit_exp": 40,
            "unit_pt": 10,
        }
        assert ended["sync"]["player"][0]["data"][0]["tutorial"] == 100
        replayed = await client.call("quest/end", closing(started["data"]["txid"]))
        assert replayed["data"] == ended["data"]
        saved = await runtime.store.read(profile)
        assert wallet(saved, "gold")["val"] == 620
        assert stamina(saved)["amount"] == 62
        listed = await client.call(
            "quest/record/list", {"range_names": [], "per_view": 100}
        )
        assert listed["data"]["items"] == saved["quest_records"]
        assert listed["data"]["items"][0]["wins"] == 1
    finally:
        await http.close()


async def test_native_tutorial_walk_follows_the_client_route(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        reached = []
        for quest_id in range(10000, 10004):
            started = await client.call("quest/start", opening(quest_id))
            ended = await client.call("quest/end", closing(started["data"]["txid"]))
            reached.append(ended["sync"]["player"][0]["data"][0]["tutorial"])
            saved = await runtime.store.read(profile)
            for reward in ended["data"]["report"].get("initial_rewards", []):
                enlist(saved, int(reward["rid"].removeprefix("unit.card.")))
                updated = await client.call(
                    "composition/update", {"composition": saved["compositions"][0]}
                )
                reached.append(updated["sync"]["player"][0]["data"][0]["tutorial"])
        assert reached == [100, 200, 250, 300, 350, 500]
        waiting = await client.call(
            "composition/update", {"composition": saved["compositions"][0]}
        )
        assert set(waiting["sync"]) == {"composition"}
        saved = await runtime.store.read(profile)
        assert saved["player"]["tutorial"] == 500
        assert (
            sum(json.loads(raw)["t"] == 1 for raw in saved["compositions"][0]["slots"])
            == 4
        )
    finally:
        await http.close()


@pytest.mark.parametrize(
    "endpoint,payload,reason",
    [
        ("quest/start", opening(10001), "NOT_QUALIFIED"),
        ("quest/end", closing("questexec-missing"), "INVALID_QUEST"),
        ("quest/cycle_exec", sweep(10000, 1), "NOT_QUALIFIED"),
    ],
)
async def test_rejections_use_the_native_422_body_and_keep_the_save(
    tmp_path, endpoint, payload, reason
):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        before = await runtime.store.read(profile)
        response = await post(http, client, endpoint, payload)
        assert response.status == 422
        assert response.headers["x-ag-protocol"] == "1"
        assert await response.json() == {
            "status": {"code": 90000006, "message": reason},
            "data": {"rejected_reason": reason},
        }
        assert await runtime.store.read(profile) == before
        await client.call("unit/all", {"_z0": 0})
    finally:
        await http.close()


async def test_pending_rules_answer_501_and_keep_the_save(tmp_path, monkeypatch):
    http, client, runtime, profile = await connect(tmp_path)

    def pending(*_):
        raise NotImplementedError("Quest rule is pending implementation")

    try:
        before = await runtime.store.read(profile)
        monkeypatch.setattr(runtime.battles.gates, "check", pending)
        response = await post(http, client, "quest/start", opening(10000))
        assert response.status == 501
        assert "pending implementation" in await response.text()
        assert await runtime.store.read(profile) == before
        monkeypatch.undo()
        await client.call("quest/start", opening(10000))
    finally:
        await http.close()


async def test_rejection_without_a_422_contract_is_a_bad_request(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        async with runtime.store.transaction(profile) as state:
            state["compositions"][0]["slots"] = []
        before = await runtime.store.read(profile)
        response = await post(http, client, "tutorial/skip", {"_z0": 0})
        assert response.status == 400
        assert await runtime.store.read(profile) == before
    finally:
        await http.close()


async def test_native_tutorial_skip_then_cycle(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        skipped = await client.call("tutorial/skip", {"_z0": 0})
        assert "data" not in skipped
        assert skipped["sync"]["player"][0]["data"][0]["tutorial"] == 9999
        async with runtime.store.transaction(profile) as state:
            runtime.resources.grant(
                state, [{"rid": "wallet.ticket.quest_skip", "val": 2}], NOW
            )
        swept = await client.call("quest/cycle_exec", sweep(10000, 2))
        assert swept["data"]["num_of_exec"] == 2
        assert swept["data"]["costs"] == [
            {"rid": "stamina.quest", "val": 16},
            {"rid": "wallet.ticket.quest_skip", "val": 2},
        ]
        saved = await runtime.store.read(profile)
        assert record(saved, 10000)["wins"] == 3
        assert wallet(saved, "quest_skip", "ticket")["val"] == 0
    finally:
        await http.close()
