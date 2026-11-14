from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.store import write_json
from monmusu_ps.features.quest import ROUTES
from monmusu_ps.runtime import Runtime
from test_battle import closing, opening
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
CODE = "TEAMS001"
OTHER = "TEAMS002"
QUEST = 10000
DUNGEON, PREMIUM = 55000, 56000


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
    return create_player("teams", CODE, runtime.masters, runtime.config.read(), NOW)


def clear(runtime, state, quest_id=QUEST, rating=3, now=NOW):
    started = runtime.battles.start(state, opening(quest_id), now)
    runtime.battles.finish(state, closing(started.data["txid"], rating=rating), now)
    return started.data["txid"]


def save(runtime, state):
    profile = f"seed:{state['player']['player_code']}"
    write_json(
        runtime.store.path(profile),
        {
            "schema_version": runtime.store.schema_version,
            "profile": profile,
            "revision": 0,
            "state": state,
        },
    )


def neighbour(runtime, name="となり"):
    other = create_player("other", OTHER, runtime.masters, runtime.config.read(), NOW)
    other["player_text"]["name"] = name
    return other


def call(runtime, state, endpoint, **payload):
    context = Context(runtime, payload, state, runtime.sessions.create(CODE), NOW)
    return ROUTES[endpoint](context).data


def test_clear_teams_list_full_rating_clears_of_every_save(runtime, state):
    fielded = deepcopy(state["units"][:2])
    clear(runtime, state)
    other = neighbour(runtime)
    other["compositions"][0]["slots"].pop(1)
    clear(runtime, other, now=NOW + 60)
    save(runtime, other)
    rows = call(runtime, state, "quest/masters_team", quest_id=QUEST)["team_logs"]
    assert [(row["no"], row["player_code"], row["name"]) for row in rows] == [
        (1, OTHER, "となり"),
        (2, CODE, "ご主人"),
    ]
    assert [row["created_at"] for row in rows] == [NOW + 60, NOW]
    mine = rows[1]
    assert (mine["log_file_name"], mine["log_sharing_scope"]) == ("", None)
    assert mine["wild_hunt_score"] == 0
    assert [json.loads(slot["unit"]) for slot in mine["team"]["units"]] == fielded
    assert len(rows[0]["team"]["units"]) == 1


def test_lesser_clears_and_other_quests_are_left_out(runtime, state):
    clear(runtime, state, rating=2)
    assert call(runtime, state, "quest/masters_team", quest_id=QUEST) == {
        "team_logs": []
    }
    assert call(runtime, state, "quest/character/ranking", quest_id=QUEST) == {
        "ranks": []
    }
    assert call(runtime, state, "quest/masters_team", quest_id=424242) == {
        "team_logs": []
    }
    with pytest.raises(ValueError):
        call(runtime, state, "quest/masters_team", quest_id="10000")


def test_a_shared_battle_log_marks_its_clear_team(runtime, state):
    txid = clear(runtime, state)
    replay = json.dumps({"battleVersion": 37, "questId": QUEST, "version": "1.1.0"})
    upload = {
        "quest_id": QUEST,
        "result": "COMPLETE",
        "rating": 3,
        "defeat": -1,
        "defeat_max": -1,
        "wild_hunt_score": 0,
        "log_data": replay,
        "team_log_key": f"quest.{QUEST}#{CODE}",
    }
    name = runtime.battle_logs.put(state, upload, NOW).data["log_file_name"]
    assert runtime.battle_logs.shared(CODE, CODE, txid) is None
    runtime.battle_logs.share(
        state, {"log_file_name": name, "sharing_scope": "fwr", "comment": None}, NOW
    )
    row = call(runtime, state, "quest/masters_team", quest_id=QUEST)["team_logs"][0]
    assert (row["log_file_name"], row["log_sharing_scope"]) == (name, "fwr")
    save(runtime, state)
    visitor = neighbour(runtime)
    context = Context(runtime, {"quest_id": QUEST}, visitor, None, NOW)
    hidden = ROUTES["quest/masters_team"](context).data["team_logs"][0]
    assert (hidden["log_file_name"], hidden["log_sharing_scope"]) == ("", None)
    runtime.battle_logs.graph.link(OTHER, CODE, NOW)
    shown = ROUTES["quest/masters_team"](context).data["team_logs"][0]
    assert (shown["log_file_name"], shown["log_sharing_scope"]) == (name, "fwr")


def test_usage_ranks_cards_by_the_share_of_teams_fielding_them(runtime, state):
    clear(runtime, state)
    other = neighbour(runtime)
    other["compositions"][0]["slots"].pop(1)
    clear(runtime, other)
    save(runtime, other)
    assert call(runtime, state, "quest/character/ranking", quest_id=QUEST) == {
        "ranks": [
            {"rank": 1, "unit_card": "unit.card.11", "ratio": 10000},
            {"rank": 2, "unit_card": "unit.card.22", "ratio": 5000},
        ]
    }


def test_dungeon_prize_shares_come_from_the_configured_weights(runtime, state):
    listing = call(
        runtime,
        state,
        "quest/subskill_probabilities",
        category=6,
        section=0,
        quest_id=DUNGEON,
    )
    assert listing["table_id"] == "normal_dungeon_1"
    assert len(listing["prizes"]) == 12
    assert {row["p"] for row in listing["prizes"]} == {"8.333%"}
    assert listing["prizes"][0] == {"id": 1003, "p": "8.333%"}


def test_a_limited_prize_is_rarer_until_it_is_won(runtime, state):
    payload = {"category": 7, "section": 0, "quest_id": PREMIUM}
    quest = runtime.masters.lookup("quest_data", "id", PREMIUM)
    payload.update(category=quest["category"], section=quest["chapter"])
    listing = call(runtime, state, "quest/subskill_probabilities", **payload)
    assert listing["table_id"] == "premium_dungeon_1"
    shares = {row["id"]: row["p"] for row in listing["prizes"]}
    assert shares[1049] == "1.639%"
    assert set(shares.values()) == {"1.639%", "16.393%"}
    runtime.config.read()["subskill_prizes"]["limited_weight"] = 10.0
    listing = call(runtime, state, "quest/subskill_probabilities", **payload)
    assert {row["p"] for row in listing["prizes"]} == {"14.286%"}
    state["quest_records"].append(
        {"quest_id": PREMIUM, "subskills": [{"id": 1049, "hit": 1, "miss": 0}]}
    )
    listing = call(runtime, state, "quest/subskill_probabilities", **payload)
    assert listing["table_id"] == "premium_dungeon_1-2"
    assert {row["p"] for row in listing["prizes"]} == {"14.286%"}


def test_quests_without_a_prize_table_list_nothing(runtime, state):
    empty = {"table_id": "", "prizes": []}
    quest = runtime.masters.lookup("quest_data", "id", QUEST)
    payload = {"category": quest["category"], "section": quest["chapter"]}
    endpoint = "quest/subskill_probabilities"
    assert call(runtime, state, endpoint, quest_id=QUEST, **payload) == empty
    assert call(runtime, state, endpoint, quest_id=424242, **payload) == empty
    with pytest.raises(ValueError):
        call(runtime, state, endpoint, quest_id=DUNGEON, **payload)


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("quest/masters_team", {"quest_id": QUEST}),
        ("quest/character/ranking", {"quest_id": QUEST}),
        (
            "quest/subskill_probabilities",
            {"category": 6, "section": 0, "quest_id": DUNGEON},
        ),
    ],
)
def test_response_field_names_match_client_dto(runtime, state, endpoint, payload):
    clear(runtime, state)
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(field for field in schema[model]["fields"] if field["name"] == "data")
    listing = call(runtime, state, endpoint, **payload)
    assert set(listing) == {field["name"] for field in schema[data["type"]]["fields"]}
    rows = next(value for value in listing.values() if isinstance(value, list))
    element = next(
        field["type"].removesuffix("[]")
        for field in schema[data["type"]]["fields"]
        if field["type"].endswith("[]")
    )
    assert set(rows[0]) == {field["name"] for field in schema[element]["fields"]}
