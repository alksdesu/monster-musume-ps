from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.features.event import ROUTES
from monmusu_ps.runtime import Runtime
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
CODE = "GROWTH01"
LABYRINTH, FLOORS = 34, (57000, 57001, 57002)
TENTACLES, STAGES, GIRL = 137, range(71720, 71727), 483
SUBJUGATION, ARENA = 140, 100136
RACE = 139
GOLD = "wallet.point.gold"
MILK = "wallet.unit_exp.ssr_all"
MEDAL = "wallet.gachamedal.gmedal_common"


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
    return create_player("growth", CODE, runtime.masters, runtime.config.read(), NOW)


def clear(state, quest_id, stars=3, rank=0):
    state["quest_records"].append(
        {"quest_id": quest_id, "record_rating": stars, "t_best_rank": rank}
    )


def confirm(runtime, state, event_id, now=NOW):
    return runtime.event_growth.confirm(state, {"event_id": event_id}, now)


def stock(runtime, state, rid):
    return runtime.resources.balance(state, rid)


def girl(state):
    return next(unit for unit in state["units"] if unit["card_id"] == GIRL)


def rejection(action):
    with pytest.raises(ApiRejected) as refused:
        action()
    return refused.value.reason


def test_labyrinth_stars_pay_out_their_rows_once(runtime, state):
    empty = confirm(runtime, state, LABYRINTH)
    assert empty.data == {
        "achieved_list": [],
        "alternative_list": [],
        "sum_star_count": 0,
        "last_id": 0,
    }
    assert empty.sync is None
    clear(state, FLOORS[0])
    clear(state, FLOORS[1], stars=2)
    gold = stock(runtime, state, GOLD)
    result = confirm(runtime, state, LABYRINTH)
    assert result.data == {
        "achieved_list": [50000, 50001, 50002, 50003, 50004],
        "alternative_list": [],
        "sum_star_count": 5,
        "last_id": 50004,
    }
    assert stock(runtime, state, "item.material.Uwsra1") == 2
    assert stock(runtime, state, "item.material.Uwcb1") == 15
    assert (stock(runtime, state, MILK), stock(runtime, state, GOLD)) == (
        20,
        gold + 20000,
    )
    assert {"item", "wallet"} <= set(result.sync)
    again = confirm(runtime, state, LABYRINTH)
    assert (again.data["achieved_list"], again.data["last_id"]) == ([], 50004)
    assert again.sync is None
    clear(state, FLOORS[2], stars=1)
    assert confirm(runtime, state, LABYRINTH).data["achieved_list"] == [50005]
    assert state["event_growth"] == {"34": [50000, 50001, 50002, 50003, 50004, 50005]}


def test_a_growth_event_brings_the_girl_and_trains_her(runtime, state):
    clear(state, STAGES[0], stars=1)
    joined = confirm(runtime, state, TENTACLES)
    assert joined.data["achieved_list"] == [1499]
    assert (girl(state)["r_unit_lv"], girl(state)["rank"]) == (1, 0)
    assert "unit" in joined.sync
    for stage in STAGES[1:5]:
        clear(state, stage)
    gold = stock(runtime, state, GOLD)
    trained = confirm(runtime, state, TENTACLES)
    assert trained.data["sum_star_count"] == 13
    assert trained.data["achieved_list"] == list(range(1500, 1510))
    assert trained.data["alternative_list"] == []
    unit = girl(state)
    assert (unit["r_unit_lv"], unit["rank"], unit["upa"]["all_pt"]) == (35, 1, 2)
    assert unit["updated_at"] == NOW
    assert stock(runtime, state, GOLD) == gold
    assert trained.sync["unit"][0]["data"] == [unit]


def test_training_already_surpassed_is_replaced_by_other_rewards(runtime, state):
    runtime.resources.grant(state, [{"rid": f"unit.card.{GIRL}", "val": 1}], NOW)
    unit = girl(state)
    unit["rank"] = 2
    unit.update(r_unit_lv=80, t_exp=runtime.progression.level_exp(unit, 80))
    unit["upa"]["all_pt"] = 5
    clear(state, STAGES[0], stars=1)
    for stage in STAGES[1:5]:
        clear(state, stage)
    held = [stock(runtime, state, rid) for rid in (MILK, MEDAL, GOLD)]
    result = confirm(runtime, state, TENTACLES)
    assert result.data["achieved_list"] == list(range(1499, 1510))
    settings = runtime.config.read()
    alternatives = settings["event_growth"]["alternatives"]
    assert result.data["alternative_list"] == [
        *alternatives["2"] * 2,
        *alternatives["3"],
        *alternatives["2"] * 2,
        *alternatives["3"],
        *alternatives["2"] * 2,
        *alternatives["4"],
        *alternatives["2"],
    ]
    repeated = settings["resources"]["duplicate_unit"]["compensation"]["SR"]
    assert [stock(runtime, state, rid) for rid in (MILK, MEDAL, GOLD)] == [
        held[0] + 7,
        held[1] + 200 + repeated,
        held[2] + 10000,
    ]
    assert (unit["r_unit_lv"], unit["rank"], unit["upa"]["all_pt"]) == (80, 2, 5)
    assert sum(row["card_id"] == GIRL for row in state["units"]) == 1


def test_subjugation_events_grow_with_the_best_training_rank(runtime, state):
    arena = next(
        row["id"]
        for row in runtime.masters.table("quest_data")
        if row["chapter"] == ARENA
    )
    clear(state, arena, stars=3, rank=0)
    assert confirm(runtime, state, SUBJUGATION).data["achieved_list"] == []
    state["quest_records"][-1]["t_best_rank"] = 2
    result = confirm(runtime, state, SUBJUGATION)
    assert result.data["achieved_list"] == [90120, 90121]
    assert result.data["sum_star_count"] == 2


def test_events_without_growth_rows_are_refused(runtime, state):
    assert rejection(lambda: confirm(runtime, state, RACE)) == "INVALID_EVENT_ID"
    assert rejection(lambda: confirm(runtime, state, 999999)) == "INVALID_EVENT_ID"
    with pytest.raises(ValueError):
        confirm(runtime, state, "34")
    runtime.config.read()["event_growth"]["follow_master_schedule"] = True
    assert rejection(lambda: confirm(runtime, state, TENTACLES)) == "OUT_OF_SCHEDULE"
    assert confirm(runtime, state, LABYRINTH).data["achieved_list"] == []
    assert state["event_growth"] == {"34": []}


def test_response_field_names_match_client_dto(runtime, state):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    endpoint = "event/grow_chara/confirm"
    payload = {"event_id": LABYRINTH}
    context = Context(runtime, payload, state, runtime.sessions.create(CODE), NOW)
    listing = ROUTES[endpoint](context).data
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(field for field in schema[model]["fields"] if field["name"] == "data")
    assert set(listing) == {row["name"] for row in schema[data["type"]]["fields"]}
