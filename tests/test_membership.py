from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.features.membership import ROUTES
from monmusu_ps.runtime import Runtime
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
DAY = 86400
NEW_YEAR = 1_798_761_600
CODE = "MEMBER01"
GOLD = {"rid": "wallet.point.gold", "val": 1000}
COINS = {"rid": "wallet.point.free_coin", "val": 50}


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
    return create_player("member", CODE, runtime.masters, runtime.config.read(), NOW)


def call(runtime, state, endpoint, now=NOW):
    context = Context(runtime, {"_z0": 0}, state, runtime.sessions.create(CODE), now)
    return ROUTES[endpoint](context).data


def letters(state, source):
    return [row for row in state["inbox"] if row["tags"][0].startswith(source)]


def test_vip_rating_comes_from_the_configuration(runtime, state):
    assert call(runtime, state, "vip/info") == {
        "rating_info": {"year": 2026, "month": 11, "point": 0, "rank": 0},
        "last_rating_info": {"year": 2026, "month": 10, "point": 0, "rank": 0},
        "benefits_list": [],
    }
    runtime.config.read()["vip"].update(point=12000, rank=3, benefits=[GOLD])
    listed = call(runtime, state, "vip/info", NEW_YEAR)
    assert listed == {
        "rating_info": {"year": 2027, "month": 1, "point": 12000, "rank": 3},
        "last_rating_info": {"year": 2026, "month": 12, "point": 12000, "rank": 3},
        "benefits_list": [{"year": 2027, "month": 1, "rewards": [GOLD]}],
    }


def test_vip_benefits_are_mailed_once_a_month(runtime, state):
    runtime.config.read()["vip"]["benefits"] = [GOLD, COINS]
    runtime.attendance.check_in(state, NOW)
    runtime.attendance.check_in(state, NOW + DAY)
    mailed = letters(state, "vip.benefits")
    assert [(row["reward"], row["tags"]) for row in mailed] == [
        (GOLD, ["vip.benefits:2026-11", "wallet"]),
        (COINS, ["vip.benefits:2026-11", "vcr", "wallet"]),
    ]
    assert state["monthly_benefits"] == {"vip": 202611, "dmm_premium": 0}
    runtime.attendance.check_in(state, NOW + 30 * DAY)
    assert [row["tags"][0] for row in letters(state, "vip.benefits")[2:]] == [
        "vip.benefits:2026-12",
        "vip.benefits:2026-12",
    ]


def test_dmm_premium_members_get_a_monthly_letter_at_login(runtime, state):
    premium = runtime.config.read()["dmm_premium"]
    premium.update(member=True, rewards=[GOLD])
    waiting = call(runtime, state, "vip/dmm_premium/benefits")
    assert waiting == {
        "is_obtained": False,
        "benefits": {"year": 2026, "month": 11, "rewards": [GOLD]},
    }
    runtime.attendance.check_in(state, NOW)
    runtime.attendance.check_in(state, NOW + 3600)
    mailed = letters(state, "vip.dmm_premium")
    assert [(row["reward"], row["tags"], row["receive_at"]) for row in mailed] == [
        (GOLD, ["vip.dmm_premium:2026-11", "wallet"], NOW)
    ]
    assert call(runtime, state, "vip/dmm_premium/benefits")["is_obtained"] is True
    later = call(runtime, state, "vip/dmm_premium/benefits", NEW_YEAR)
    assert later == {
        "is_obtained": False,
        "benefits": {"year": 2027, "month": 1, "rewards": [GOLD]},
    }
    runtime.attendance.check_in(state, NEW_YEAR)
    assert letters(state, "vip.dmm_premium")[-1]["tags"][0] == "vip.dmm_premium:2027-1"
    assert state["monthly_benefits"]["dmm_premium"] == 202701


def test_players_outside_dmm_premium_receive_nothing(runtime, state):
    runtime.config.read()["dmm_premium"]["rewards"] = [GOLD]
    runtime.attendance.check_in(state, NOW)
    assert state["inbox"] == []
    assert call(runtime, state, "vip/dmm_premium/benefits") == {
        "is_obtained": False,
        "benefits": {"year": 2026, "month": 11, "rewards": []},
    }


def test_unknown_benefit_rewards_are_reported_before_anything_is_sent(runtime, state):
    runtime.config.read()["vip"]["benefits"] = [GOLD, {"rid": "wallet.none", "val": 1}]
    with pytest.raises((LookupError, ValueError)):
        runtime.attendance.check_in(state, NOW)
    assert state["inbox"] == []
    assert state["monthly_benefits"]["vip"] == 0


@pytest.mark.parametrize(
    ("endpoint", "nested"),
    [
        ("vip/info", ("rating_info", "VipRatingInfo")),
        ("vip/dmm_premium/benefits", ("benefits", "DmmPremiumBenefits")),
    ],
)
def test_response_field_names_match_client_dto(runtime, state, endpoint, nested):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(field for field in schema[model]["fields"] if field["name"] == "data")
    listing = call(runtime, state, endpoint)
    assert set(listing) == {field["name"] for field in schema[data["type"]]["fields"]}
    field, name = nested
    assert set(listing[field]) == {row["name"] for row in schema[name]["fields"]}
