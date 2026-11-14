from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.features.mileage import ROUTES
from monmusu_ps.runtime import Runtime
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
CODE = "MILES001"
PROGRAM = "mileage.spring"
BROWN, GREEN = "item.material.Brown1", "item.material.Green1"
GOLD = "wallet.point.gold"
HALFWAY = {"point": 50, "reward": {"rid": GOLD, "val": 500}}
LAP = {"point": 100, "reward": {"rid": GREEN, "val": 2}}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: NOW, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    settings["mileage_programs"] = {
        PROGRAM: {
            "start_at": "",
            "end_at": "",
            "materials": {BROWN: 10},
            "lap_points": 100,
            "benefits": [HALFWAY, LAP],
        },
        "mileage.autumn": {
            "start_at": "2027/09/01 00:00:00",
            "end_at": "",
            "materials": {},
            "lap_points": 0,
            "benefits": [],
        },
    }
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def state(runtime):
    return create_player("miles", CODE, runtime.masters, runtime.config.read(), NOW)


def give(runtime, state, rid, amount):
    runtime.resources.grant(state, [{"rid": rid, "val": amount}], NOW)


def stock(runtime, state, rid):
    return runtime.resources.balance(state, rid)


def commit(runtime, state, amount, rid=BROWN, program=PROGRAM):
    payload = {"program_id": program, "items": [{"rid": rid, "val": amount}]}
    return runtime.mileage.commit(state, payload, NOW)


def rejection(action):
    with pytest.raises(ApiRejected) as refused:
        action()
    return refused.value.reason


def test_programs_start_from_an_empty_record(runtime, state):
    assert runtime.mileage.get(state, {"program_id": PROGRAM}, NOW) == {
        "program_record": {"program_id": PROGRAM, "point": 0, "iterations": 0}
    }
    for unknown in ("mileage.none", None, 5):
        with pytest.raises(ApiNotFound):
            runtime.mileage.get(state, {"program_id": unknown}, NOW)
    closed = {"program_id": "mileage.autumn"}
    assert rejection(lambda: runtime.mileage.get(state, closed, NOW)) == (
        "OUT_OF_SCHEDULE"
    )


def test_committed_materials_become_points_and_cross_benefits(runtime, state):
    give(runtime, state, BROWN, 8)
    gold = stock(runtime, state, GOLD)
    result = commit(runtime, state, 6)
    assert result.data == {
        "committed_point": [{"rid": BROWN, "gain": 60}],
        "benefits": [HALFWAY],
        "program_record": {"program_id": PROGRAM, "point": 60, "iterations": 0},
    }
    assert (stock(runtime, state, BROWN), stock(runtime, state, GOLD)) == (
        2,
        gold + 500,
    )
    assert {"item", "wallet"} <= set(result.sync)
    assert commit(runtime, state, 1).data["benefits"] == []
    assert state["mileage"] == {PROGRAM: {"point": 70, "iterations": 0}}


def test_points_roll_over_into_further_laps(runtime, state):
    give(runtime, state, BROWN, 21)
    commit(runtime, state, 6)
    result = commit(runtime, state, 15)
    assert result.data["benefits"] == [LAP, HALFWAY, LAP]
    assert result.data["program_record"] == {
        "program_id": PROGRAM,
        "point": 10,
        "iterations": 2,
    }
    assert stock(runtime, state, GREEN) == 4


def test_programs_without_laps_keep_accumulating(runtime, state):
    runtime.config.read()["mileage_programs"][PROGRAM]["lap_points"] = 0
    give(runtime, state, BROWN, 30)
    first = commit(runtime, state, 12)
    assert first.data["benefits"] == [HALFWAY, LAP]
    second = commit(runtime, state, 18)
    assert second.data["benefits"] == []
    assert second.data["program_record"] == {
        "program_id": PROGRAM,
        "point": 300,
        "iterations": 0,
    }


@pytest.mark.parametrize(
    ("items", "reason"),
    [
        ([], "HAS_CANT_CONSUMABLE_MATERIAL"),
        ([{"rid": GREEN, "val": 1}], "HAS_INVALID_MATERIAL"),
        ([{"rid": BROWN, "val": 0}], "HAS_INVALID_MATERIAL"),
        ([{"rid": BROWN, "val": "1"}], "HAS_INVALID_MATERIAL"),
        ([{"rid": [BROWN], "val": 1}], "HAS_INVALID_MATERIAL"),
        ([{"rid": BROWN, "val": 4}], "NOT_ENOUGH_MATERIAL"),
        ([{"rid": BROWN, "val": 2}, {"rid": BROWN, "val": 2}], "NOT_ENOUGH_MATERIAL"),
    ],
)
def test_unusable_materials_are_refused(runtime, state, items, reason):
    give(runtime, state, BROWN, 3)
    give(runtime, state, GREEN, 3)
    payload = {"program_id": PROGRAM, "items": items}
    refused = rejection(lambda: runtime.mileage.commit(state, payload, NOW))
    assert refused == reason
    assert stock(runtime, state, BROWN) == 3 and state["mileage"] == {}
    with pytest.raises(ValueError):
        runtime.mileage.commit(state, {"program_id": PROGRAM, "items": None}, NOW)


def test_records_are_listed_in_pages_by_program(runtime, state):
    give(runtime, state, BROWN, 3)
    commit(runtime, state, 3)
    session = runtime.sessions.create(CODE)

    def call(endpoint, **payload):
        return ROUTES[endpoint](Context(runtime, payload, state, session, NOW)).data

    first = call("mileage_program/record/list", range_names=[], per_view=1)
    assert first["items"] == [{"program_id": PROGRAM, "point": 30, "iterations": 0}]
    rest = call(
        "mileage_program/record/list/continue", sequence_token=first["sequence_token"]
    )
    assert rest == {
        "items": [{"program_id": "mileage.autumn", "point": 0, "iterations": 0}],
        "sequence_token": None,
    }
    scoped = call("mileage_program/record/list", range_names=["mileage"], per_view=5)
    assert len(scoped["items"]) == 2
    named = call(
        "mileage_program/record/list", range_names=["mileage.autumn"], per_view=5
    )
    assert [row["program_id"] for row in named["items"]] == ["mileage.autumn"]
    with pytest.raises(ValueError):
        call("mileage_program/record/list", range_names=None, per_view=5)


def test_a_server_without_programs_knows_none(tmp_path):
    runtime = Runtime(Settings.load(ROOT, tmp_path), clock=lambda: NOW)
    try:
        assert runtime.config.read()["mileage_programs"] == {}
        assert runtime.mileage.records({"mileage": {}}, []) == []
    finally:
        runtime.registry.close()


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("mileage_program", {"program_id": PROGRAM}),
        (
            "mileage_program/commit",
            {"program_id": PROGRAM, "items": [{"rid": BROWN, "val": 5}]},
        ),
        ("mileage_program/record/list", {"range_names": [], "per_view": 5}),
    ],
)
def test_response_field_names_match_client_dto(runtime, state, endpoint, payload):
    give(runtime, state, BROWN, 5)
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    context = Context(runtime, payload, state, runtime.sessions.create(CODE), NOW)
    listing = ROUTES[endpoint](context).data
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(field for field in schema[model]["fields"] if field["name"] == "data")
    assert set(listing) == {row["name"] for row in schema[data["type"]]["fields"]}
    record = listing.get("program_record") or listing["items"][0]
    assert set(record) == {
        row["name"] for row in schema["MileageProgramRecord"]["fields"]
    }
    if endpoint.endswith("commit"):
        assert set(listing["committed_point"][0]) == {
            row["name"] for row in schema["MileageItemCommittedPoint"]["fields"]
        }
        assert set(listing["benefits"][0]) == {
            row["name"] for row in schema["MileageBenefit"]["fields"]
        }
