from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.app import RUNTIME
from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.store import write_json
from monmusu_ps.features.votes import ROUTES
from monmusu_ps.runtime import Runtime
from test_catalog import Context
from test_server import NativeClient, http


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
DAY = 86400
CODE = "VOTER001"
OTHER = "VOTER002"
ROUND = {"campaign_id": 1, "round_no": 1}
OPENING, CLOSING = 1_647_230_400, 4_102_343_999
WARRIOR, FAIRY = "unit.card.11", "unit.card.22"
GOLD = "wallet.point.gold"
LEGEND = 5_460_818

__all__ = ["http"]


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
    return create_player("voter", CODE, runtime.masters, runtime.config.read(), NOW)


@pytest.fixture
def campaign(runtime):
    campaign = runtime.config.read()["votes"]["campaigns"][0]
    campaign["candidates"] = [11, 22, 57]
    return campaign


def vote(runtime, state, *choices, now=NOW):
    return runtime.votes.post(state, {**ROUND, "choices": list(choices)}, now)


def summary(revision=None):
    return {
        **ROUND,
        "start_at": OPENING,
        "end_at": CLOSING,
        "ranking_rev": revision,
    }


def test_running_rounds_are_summarised_for_the_player(runtime, campaign):
    assert runtime.votes.summaries(NOW) == [summary()]
    assert runtime.votes.summaries(OPENING - 1) is None
    assert runtime.votes.summaries(CLOSING) is None
    campaign["ranking_rev"] = "r1"
    assert runtime.votes.summaries(NOW) == [summary("r1")]
    runtime.config.read()["votes"]["campaigns"] = []
    assert runtime.votes.summaries(NOW) is None


async def test_the_player_read_carries_the_vote_summaries(http):
    client = NativeClient(http)
    await client.login()
    modules = {"player_module_ids": ["stamina"]}
    listed = await client.call("player/get", modules)
    assert listed["data"]["votes"] == [summary()]
    http.app[RUNTIME].config.read()["votes"]["campaigns"].clear()
    assert (await client.call("player/get", modules))["data"]["votes"] is None


def test_the_campaign_lists_its_candidates_and_the_ballot(runtime, state, campaign):
    listed = runtime.votes.campaign(state, ROUND, NOW)
    assert listed == {
        "campaign": {
            "summary": summary(),
            "tickets": 3,
            "candidates": [WARRIOR, FAIRY, "unit.card.57"],
            "message": "",
        },
        "record": {
            **ROUND,
            "vote_id": 1,
            "past_choices": None,
            "choices": None,
            "voted_at": 0,
        },
    }


def test_candidates_default_to_every_card_of_the_listed_rarities(runtime, state):
    candidates = runtime.votes.campaign(state, ROUND, NOW)["campaign"]["candidates"]
    assert len(candidates) == len(set(candidates)) == 324
    assert all(
        runtime.masters.asset("unitcard", rid.rsplit(".", 1)[1])["rarityId"] == LEGEND
        for rid in candidates
    )


def test_a_vote_is_recorded_tallied_and_thanked(runtime, state, campaign):
    held = runtime.resources.balance(state, GOLD)
    result = vote(runtime, state, WARRIOR, FAIRY)
    assert result.data == {
        "record": {
            **ROUND,
            "vote_id": 1,
            "past_choices": None,
            "choices": [WARRIOR, FAIRY],
            "voted_at": NOW,
        },
        "rewards": [{"rid": GOLD, "val": 10000, "inc": 10000, "inbox": 0, "over": 0}],
    }
    assert runtime.resources.balance(state, GOLD) == held + 10000
    assert "wallet" in result.sync
    assert state["votes"]["1.1"]["tally"] == {WARRIOR: 1, FAIRY: 1}
    with pytest.raises(ValueError):
        vote(runtime, state, WARRIOR, now=NOW + 60)
    record = runtime.votes.campaign(state, ROUND, NOW + 60)["record"]
    assert (record["choices"], record["voted_at"]) == ([WARRIOR, FAIRY], NOW)


def test_the_ballot_reopens_at_the_day_boundary(runtime, state, campaign):
    vote(runtime, state, WARRIOR)
    record = runtime.votes.campaign(state, ROUND, NOW + DAY)["record"]
    assert record == {
        **ROUND,
        "vote_id": 2,
        "past_choices": [WARRIOR],
        "choices": None,
        "voted_at": 0,
    }
    vote(runtime, state, WARRIOR, FAIRY, now=NOW + DAY)
    assert state["votes"]["1.1"]["tally"] == {WARRIOR: 2, FAIRY: 1}


@pytest.mark.parametrize(
    "choices",
    [
        [],
        [WARRIOR, FAIRY, "unit.card.57", WARRIOR],
        [WARRIOR, WARRIOR],
        ["unit.card.84"],
        [11],
        [[WARRIOR]],
        WARRIOR,
        None,
    ],
)
def test_choices_are_distinct_candidates_within_the_tickets(
    runtime, state, campaign, choices
):
    with pytest.raises(ValueError):
        runtime.votes.post(state, {**ROUND, "choices": choices}, NOW)
    assert state["votes"] == {}


def test_closed_and_unknown_rounds_take_no_votes(runtime, state, campaign):
    for payload in ({**ROUND, "round_no": 2}, {**ROUND, "campaign_id": 9}):
        with pytest.raises(ValueError):
            runtime.votes.campaign(state, payload, NOW)
        with pytest.raises(ValueError):
            runtime.votes.ranking(state, payload, NOW)
    with pytest.raises(ValueError):
        runtime.votes.campaign(state, ROUND, CLOSING)
    with pytest.raises(ValueError):
        vote(runtime, state, WARRIOR, now=CLOSING)


def test_the_ranking_is_withheld_until_a_revision_is_announced(
    runtime, state, campaign
):
    vote(runtime, state, WARRIOR, FAIRY)
    assert runtime.votes.ranking(state, ROUND, NOW) == {
        "ranking_rev": None,
        "ranks": [],
    }
    other = create_player("other", OTHER, runtime.masters, runtime.config.read(), NOW)
    other["votes"]["1.1"] = {"tally": {FAIRY: 4}}
    write_json(
        runtime.store.path("seed:other"),
        {
            "schema_version": runtime.store.schema_version,
            "profile": "seed:other",
            "revision": 0,
            "state": other,
        },
    )
    campaign.update(ranking_rev="r1", base_votes={"11": 3})
    assert runtime.votes.ranking(state, ROUND, CLOSING) == {
        "ranking_rev": "r1",
        "ranks": [
            {"rank": 1, "candidate": FAIRY, "ratio": 5556},
            {"rank": 2, "candidate": WARRIOR, "ratio": 4444},
        ],
    }
    campaign["ranking_size"] = 1
    assert len(runtime.votes.ranking(state, ROUND, NOW)["ranks"]) == 1


@pytest.mark.parametrize(
    ("endpoint", "payload", "nested"),
    [
        ("votes/campaign", ROUND, ("record", "VoteRecord")),
        ("votes/post", {**ROUND, "choices": [WARRIOR]}, ("record", "VoteRecord")),
        ("votes/ranking", ROUND, None),
    ],
)
def test_response_field_names_match_client_dto(
    runtime, state, campaign, endpoint, payload, nested
):
    campaign["ranking_rev"] = "r1"
    state["votes"]["1.1"] = {
        "vote_id": 3,
        "choices": None,
        "past_choices": None,
        "voted_at": 0,
        "tally": {FAIRY: 2},
    }
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    context = Context(runtime, payload, state, runtime.sessions.create(CODE), NOW)
    listing = ROUTES[endpoint](context).data
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(field for field in schema[model]["fields"] if field["name"] == "data")
    assert set(listing) == {row["name"] for row in schema[data["type"]]["fields"]}
    if nested:
        field, name = nested
        assert set(listing[field]) == {row["name"] for row in schema[name]["fields"]}
    else:
        assert set(listing["ranks"][0]) == {
            row["name"] for row in schema["VoteRankRecord"]["fields"]
        }
