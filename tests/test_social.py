from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.store import write_json
from monmusu_ps.features.player import home
from monmusu_ps.features.reminders import checkoff_revision
from monmusu_ps.features.social import ROUTES
from monmusu_ps.runtime import Runtime
from test_battle import closing, connect, opening
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
DAY = 86400
TODAY = 1_794_254_400
FOREVER = 253_402_300_799
CODE = "SOCIAL01"
OTHER = "SOCIAL02"
NEWCOMER, VETERAN, LONER = "HANA0001", "MOMIJI02", "RINNE006"
FRIEND_POINT = "item.friend_point.friend_pt1"
SOCIAL_ENDPOINTS = set(ROUTES)


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
    return create_player("social", CODE, runtime.masters, runtime.config.read(), NOW)


def seed(runtime, code, lending=(), name="", login_at=NOW - 3600):
    profile = f"seed:{code}"
    state = create_player(profile, code, runtime.masters, runtime.config.read(), NOW)
    state["player_text"]["name"] = name
    state["last_login_at"] = login_at
    for slot, instance_id in zip(("s1", "s2", "s3"), lending):
        state["lending"][slot] = instance_id
    write_json(
        runtime.store.path(profile),
        {
            "schema_version": runtime.store.schema_version,
            "profile": profile,
            "revision": 0,
            "state": state,
        },
    )
    return state


def context(runtime, state, now=NOW, **payload):
    session = runtime.sessions._profiles.get(CODE) or runtime.sessions.create(CODE)
    return Context(runtime, payload, state, session, now)


def call(runtime, state, endpoint, now=NOW, **payload):
    return ROUTES[endpoint](context(runtime, state, now, **payload))


def reminder(state, identity):
    return next(
        row
        for row in state["revisioned_reminders"]
        if (row["group"], row["id"]) == (1, identity)
    )


def counter(state, name):
    return next(
        (row["val"] for row in state["mission_counters"] if row["name"] == name), 0
    )


def test_search_describes_a_roster_player_in_the_client_shape(runtime, state):
    found = call(runtime, state, "social/player/search", player_code=NEWCOMER).data
    info = found["found_data"]
    assert info["text"] == {
        "name": "はなまる",
        "bio": "始めたばかりです。よろしくお願いします！",
    }
    assert info["profile"] == {
        "format_ver": 1,
        "player_code": NEWCOMER,
        "rank": 2,
        "fav_unit_card": 4,
        "fav_unit_skin": 0,
        "followers": 0,
        "login_at": NOW - 8 * 60,
    }
    assert (info["is_follower"], info["is_following"]) == (False, False)
    assert (info["linked_at"], info["greeting_at"]) == (0, 0)
    lent = [json.loads(info["rental_units"][slot]) for slot in ("s1", "s2", "s3")]
    assert [(row["instance_id"], row["card_id"], row["r_unit_lv"]) for row in lent] == [
        (1, 4, 3),
        (2, 11, 4),
        (3, 57, 5),
    ]
    assert lent[0]["gear_set"] == {"A": 0, "B": 0, "C": 0, "D": 0}
    assert lent[0]["t_exp"] == 90


def test_roster_units_carry_the_awakening_rank_their_level_needs(runtime, state):
    found = call(runtime, state, "social/player/search", player_code="SHION010").data
    lent = [
        json.loads(found["found_data"]["rental_units"][slot])
        for slot in ("s1", "s2", "s3")
    ]
    assert [(row["r_unit_lv"], row["rank"]) for row in lent] == [
        (120, 3),
        (130, 4),
        (150, 4),
    ]


@pytest.mark.parametrize("code", ["NOBODY01", "short", CODE, 12345678, None])
def test_search_misses_answer_with_a_null_entry(runtime, state, code):
    assert call(runtime, state, "social/player/search", player_code=code).data == {
        "found_data": None
    }


def test_search_reaches_other_local_saves(runtime, state):
    seed(runtime, OTHER, lending=(1,), name="となり")
    found = call(runtime, state, "social/player/search", player_code=OTHER).data
    info = found["found_data"]
    assert info["text"]["name"] == "となり"
    assert info["profile"]["login_at"] == NOW - 3600
    assert json.loads(info["rental_units"]["s1"])["card_id"] == 11
    assert info["rental_units"]["s2"] is None


def test_recommendations_skip_the_player_and_people_already_followed(runtime, state):
    seed(runtime, OTHER)
    call(runtime, state, "social/follow/entry", player_code=NEWCOMER)
    players = call(runtime, state, "social/player/selects/recommended").data["players"]
    codes = [row["profile"]["player_code"] for row in players]
    assert NEWCOMER not in codes and CODE not in codes and OTHER in codes
    assert codes[0] == VETERAN
    assert len(codes) == 10
    runtime.config.read()["social"]["recommend_limit"] = 3
    players = call(runtime, state, "social/player/selects/recommended").data["players"]
    assert len(players) == 3


def test_following_links_both_ways_when_the_roster_player_follows_back(runtime, state):
    result = call(runtime, state, "social/follow/entry", player_code=NEWCOMER)
    latest = result.data["latest_data"]
    assert (latest["is_following"], latest["is_follower"]) == (True, True)
    assert latest["linked_at"] == NOW
    assert latest["profile"]["followers"] == 1
    assert state["player"]["follows"] == 1
    assert result.sync["player"][0]["data"][0]["follows"] == 1
    assert counter(state, "5.social_follow") == 1
    assert 26208 in {row["id"] for row in state["missions"]}
    saved = json.loads((runtime.settings.saves / "social.json").read_text("utf-8"))
    assert saved["follows"] == [
        {"from": CODE, "to": NEWCOMER, "linked_at": NOW, "greeting_at": 0},
        {"from": NEWCOMER, "to": CODE, "linked_at": NOW, "greeting_at": 0},
    ]


def test_following_a_loner_stays_one_sided_and_is_idempotent(runtime, state):
    first = call(runtime, state, "social/follow/entry", player_code=LONER)
    again = call(runtime, state, "social/follow/entry", player_code=LONER, now=NOW + 5)
    assert first.data["latest_data"]["is_follower"] is False
    assert again.data["latest_data"]["linked_at"] == NOW
    assert state["player"]["follows"] == 1
    assert counter(state, "5.social_follow") == 1


def test_following_refuses_yourself_and_strangers(runtime, state):
    with pytest.raises(ApiRejected) as refused:
        call(runtime, state, "social/follow/entry", player_code=CODE)
    assert refused.value.reason == "UNFOLLOWABLE_TARGET"
    with pytest.raises(ApiNotFound):
        call(runtime, state, "social/follow/entry", player_code="NOBODY01")


def test_following_stops_at_the_rank_table_cap(runtime, state):
    for index in range(15):
        runtime.social.graph.link(CODE, f"FAKE{index:04d}", NOW)
    with pytest.raises(ApiRejected) as refused:
        call(runtime, state, "social/follow/entry", player_code=NEWCOMER)
    assert refused.value.reason == "FOLLOWS_IS_REACHED_LIMIT"
    state["modules"]["experience"][0]["level"] = 10
    call(runtime, state, "social/follow/entry", player_code=NEWCOMER)
    assert state["player"]["follows"] == 16


def test_unfollowing_drops_only_the_outgoing_edge(runtime, state):
    call(runtime, state, "social/follow/entry", player_code=NEWCOMER)
    result = call(runtime, state, "social/follow/delete", player_code=NEWCOMER)
    assert state["player"]["follows"] == 0
    assert result.sync["player"][0]["data"][0]["follows"] == 0
    info = call(runtime, state, "social/player/search", player_code=NEWCOMER).data
    assert (info["found_data"]["is_following"], info["found_data"]["is_follower"]) == (
        False,
        True,
    )
    assert (
        call(runtime, state, "social/follow/delete", player_code=NEWCOMER).sync is None
    )


def test_follow_lists_page_with_the_friend_point_balance(runtime, state):
    for code in (NEWCOMER, VETERAN, LONER):
        call(runtime, state, "social/follow/entry", player_code=code)
    runtime.resources.grant(state, [{"rid": FRIEND_POINT, "val": 12}], NOW)
    first = call(runtime, state, "social/follow/list", per_view=2).data
    second = call(
        runtime,
        state,
        "social/follow/list/continue",
        sequence_token=first["sequence_token"],
    ).data
    assert first["friend_point"] == second["friend_point"] == 12
    rows = first["items"] + second["items"]
    assert [row["profile"]["player_code"] for row in rows] == [
        NEWCOMER,
        VETERAN,
        LONER,
    ]
    assert all(row["is_following"] and row["rental_units"]["s1"] for row in rows)
    assert second["sequence_token"] is None


def test_follower_lists_carry_the_friend_reminders_in_sync(runtime, state):
    seed(runtime, OTHER)
    runtime.social.graph.link(OTHER, CODE, NOW - 100)
    result = call(runtime, state, "social/follower/list", per_view=100)
    assert [row["profile"]["player_code"] for row in result.data["items"]] == [OTHER]
    assert result.data["items"][0]["is_follower"] is True
    assert result.sync == {
        "rev_reminder": [
            {
                "scope": f"player@{CODE}",
                "data": [
                    {"group": 1, "id": 1, "rev": NOW - 100, "expire": FOREVER},
                    {"group": 1, "id": 2, "rev": 0, "expire": FOREVER},
                ],
            }
        ]
    }
    assert call(runtime, state, "social/follower/count").data == {"followers": 1}


def test_friend_reminders_track_unseen_links_and_clear_on_checkoff(runtime, state):
    seed(runtime, OTHER)
    runtime.social.graph.link(OTHER, CODE, NOW - 300)
    runtime.social.graph.link("HANA0001", CODE, NOW - 200)
    runtime.social.graph.link(CODE, "HANA0001", NOW - 100)
    runtime.social.remind(state, NOW)
    assert (reminder(state, 1)["rev"], reminder(state, 2)["rev"]) == (
        NOW - 300,
        NOW - 100,
    )
    checkoff_revision(context(runtime, state, group=1, id=1, rev=NOW - 200))
    checkoff_revision(context(runtime, state, group=1, id=2, rev=NOW - 100))
    assert (reminder(state, 1)["rev"], reminder(state, 2)["rev"]) == (0, 0)
    runtime.social.graph.link("MOMIJI02", CODE, NOW)
    runtime.social.remind(state, NOW)
    assert (reminder(state, 1)["rev"], reminder(state, 2)["rev"]) == (NOW, 0)


def test_the_home_screen_refreshes_the_friend_reminders(runtime, state):
    runtime.social.graph.link(OTHER, CODE, NOW - 50)
    result = home(context(runtime, state))
    rows = result.sync["rev_reminder"][0]["data"]
    assert rows == [{"group": 1, "id": 1, "rev": NOW - 50, "expire": FOREVER}]


def test_greetings_pay_once_per_game_day_and_mark_the_edge(runtime, state):
    for code in (NEWCOMER, VETERAN):
        call(runtime, state, "social/follow/entry", player_code=code)
    result = call(
        runtime,
        state,
        "social/follow/greeting",
        player_codes=[NEWCOMER, VETERAN, LONER, NEWCOMER],
    )
    assert result.data == {
        "items": [
            {"player_code": NEWCOMER, "greeting_at": NOW},
            {"player_code": VETERAN, "greeting_at": NOW},
        ],
        "follow": {"point": 20, "count": 2, "is_max": False},
        "friend_point": 20,
    }
    assert result.sync["item"][0]["data"][0]["id"] == FRIEND_POINT
    assert result.sync["item"][0]["data"][0]["val"] == 20
    later = call(
        runtime,
        state,
        "social/follow/greeting",
        player_codes=[NEWCOMER],
        now=NOW + 600,
    )
    assert later.data == {
        "items": [],
        "follow": {"point": 0, "count": 0, "is_max": False},
        "friend_point": 20,
    }
    assert later.sync is None
    listed = call(runtime, state, "social/follow/list", per_view=10).data["items"]
    assert [row["greeting_at"] for row in listed] == [NOW, NOW]
    tomorrow = call(
        runtime,
        state,
        "social/follow/greeting",
        player_codes=[NEWCOMER],
        now=TODAY + DAY,
    )
    assert tomorrow.data["follow"] == {"point": 10, "count": 1, "is_max": False}


def test_greetings_cap_the_daily_friend_points(runtime, state):
    for index in range(21):
        runtime.social.graph.link(CODE, f"FAKE{index:04d}", NOW)
    codes = [f"FAKE{index:04d}" for index in range(21)]
    result = call(runtime, state, "social/follow/greeting", player_codes=codes)
    assert result.data["follow"] == {"point": 200, "count": 21, "is_max": True}
    assert result.data["friend_point"] == 200


def test_greetings_are_rejected_when_malformed(runtime, state):
    with pytest.raises(ValueError):
        call(runtime, state, "social/follow/greeting", player_codes=[1])


def test_lending_slots_are_saved_and_read_back_as_unit_text(runtime, state):
    runtime.periods.open(state, "sdpanel_mission.2", NOW, NOW + DAY, NOW)
    first, second = (row["instance_id"] for row in state["units"][:2])
    result = call(
        runtime,
        state,
        "social/rental_units/update",
        rental_units={"s1": second, "s2": 0, "s3": first},
    )
    assert state["lending"] == {"s1": second, "s2": 0, "s3": first}
    assert counter(state, "5.social_rental") == 2
    assert counter(state, "7.70011") == 2
    assert {26209, 70011} <= {row["id"] for row in state["missions"]}
    assert result.sync["mission_counter"][0]["data"]
    read = call(runtime, state, "social/rental_units").data["rental_units"]
    assert read["format_ver"] == 1 and read["s2"] is None
    assert json.loads(read["s1"])["instance_id"] == second
    assert json.loads(read["s3"]) == state["units"][0]


@pytest.mark.parametrize(
    "slots",
    [
        {"s1": 1, "s2": 1, "s3": 0},
        {"s1": 99, "s2": 0, "s3": 0},
        {"s1": -1, "s2": 0, "s3": 0},
        {"s1": "1", "s2": 0, "s3": 0},
        {"s1": 1, "s2": 0},
    ],
)
def test_lending_slots_must_be_distinct_owned_units(runtime, state, slots):
    with pytest.raises((ValueError, KeyError)):
        call(runtime, state, "social/rental_units/update", rental_units=slots)
    assert state["lending"] == {"s1": 0, "s2": 0, "s3": 0}


def test_support_candidates_come_from_strangers_who_lend(runtime, state):
    seed(runtime, OTHER, lending=(2,), name="となり", login_at=NOW - 10)
    seed(runtime, "SOCIAL03", lending=(), name="けち")
    call(runtime, state, "social/follow/entry", player_code=NEWCOMER)
    data = call(runtime, state, "social/rental_units/selects").data
    owners = [row["player_code"] for row in data["rental_owners"]]
    assert owners[0] == OTHER and NEWCOMER not in owners and "SOCIAL03" not in owners
    assert len(owners) == 10
    assert data["owner_texts"][0] == {"name": "となり", "bio": ""}
    units = [row for row in data["rental_units"] if row["player_code"] == OTHER]
    assert len(units) == 1 and units[0]["format_ver"] == 1
    assert json.loads(units[0]["s"])["instance_id"] == 2
    assert sum(row["player_code"] == VETERAN for row in data["rental_units"]) == 3
    runtime.config.read()["social"]["selects_limit"] = 2
    data = call(runtime, state, "social/rental_units/selects").data
    assert len(data["rental_owners"]) == len(data["owner_texts"]) == 2


def test_a_support_unit_joins_the_battle_and_credits_its_owner(runtime, state):
    started = runtime.battles.start(
        state, opening(10000, rental_unit=VETERAN, rental_unit_iid=2), NOW
    )
    transaction = state["quest_transactions"][started.data["txid"]]
    assert transaction["support"]["owner"] == VETERAN
    assert transaction["support"]["unit"]["card_id"] == 6
    finished = runtime.battles.finish(state, closing(started.data["txid"]), NOW)
    assert finished.data["result"] == "COMPLETE"
    record = next(row for row in state["quest_records"] if row["quest_id"] == 10000)
    assert record["flags"] == 1
    assert state["events"][-1]["data"]["support_owner"] == VETERAN
    assert state["events"][-1]["data"]["support_card"] == 6
    tallies = runtime.social.graph._data()["tallies"]
    assert tallies[VETERAN] == [
        {
            "day": TODAY,
            "greeted": {"count": 0, "point": 0},
            "rented": {"count": 1, "point": 20},
        }
    ]


def test_clearing_alone_keeps_the_self_clear_flag(runtime, state):
    started = runtime.battles.start(state, opening(10000), NOW)
    runtime.battles.finish(state, closing(started.data["txid"]), NOW)
    record = next(row for row in state["quest_records"] if row["quest_id"] == 10000)
    assert record["flags"] == 3


@pytest.mark.parametrize(
    "statement",
    [
        {"rental_unit": NEWCOMER, "rental_unit_iid": 2},
        {"rental_unit": VETERAN, "rental_unit_iid": 9},
        {"rental_unit": "NOBODY01", "rental_unit_iid": 1},
        {"rental_unit": CODE, "rental_unit_iid": 1},
        {"rental_unit": None, "rental_unit_iid": 1},
        {"rental_unit": VETERAN, "rental_unit_iid": "2"},
    ],
)
def test_invalid_support_statements_are_rejected(runtime, state, statement):
    with pytest.raises(ApiRejected) as refused:
        runtime.battles.start(state, opening(10000, **statement), NOW)
    assert refused.value.reason == "INVALID_STATEMENT"


@pytest.mark.parametrize("category", [5, 8])
def test_challenge_quests_refuse_support_units(runtime, state, category):
    statement = {"rental_unit": VETERAN, "rental_unit_iid": 1}
    with pytest.raises(ApiRejected):
        runtime.social.supporter(state, {"category": category}, statement, NOW)


def test_sign_in_pays_yesterdays_greetings_and_rentals(runtime, state):
    yesterday = TODAY - DAY
    seed(runtime, OTHER)
    runtime.social.graph.link(OTHER, CODE, yesterday)
    greeted = runtime.social.graph.tally(CODE, yesterday)["greeted"]
    greeted.update(count=3, point=15)
    rented = runtime.social.graph.tally(CODE, yesterday - DAY)["rented"]
    rented.update(count=10, point=200)
    runtime.social.graph.commit()
    points = runtime.social.collect(state, NOW)
    assert points == {
        "friend_point": 215,
        "rental": {"point": 200, "count": 10, "is_max": True},
        "follower": {"point": 15, "count": 3, "is_max": False},
    }
    assert runtime.resources.balance(state, FRIEND_POINT) == 215
    assert runtime.social.collect(state, NOW + 60) == {
        "friend_point": 215,
        "rental": {"point": 0, "count": 0, "is_max": False},
        "follower": {"point": 0, "count": 0, "is_max": False},
    }
    assert runtime.social.graph._data()["tallies"] == {}


def test_roster_players_greet_and_rent_every_day_once_linked(runtime, state):
    call(runtime, state, "social/follow/entry", player_code=NEWCOMER, now=NOW - 2 * DAY)
    call(runtime, state, "social/follow/entry", player_code=LONER, now=NOW - 2 * DAY)
    call(
        runtime, state, "social/follow/entry", player_code="SORA0003", now=NOW - 2 * DAY
    )
    assert runtime.social.collect(state, NOW)["follower"]["point"] == 10
    state["friend_points"]["collected_day"] = 0
    call(
        runtime,
        state,
        "social/rental_units/update",
        rental_units={"s1": 1, "s2": 0, "s3": 0},
    )
    points = runtime.social.collect(state, NOW)
    assert points["follower"] == {"point": 10, "count": 2, "is_max": False}
    assert points["rental"] == {"point": 20, "count": 1, "is_max": False}
    assert points["friend_point"] == 40


def test_sign_in_response_carries_the_friend_points(runtime, state):
    call(runtime, state, "social/follow/entry", player_code=NEWCOMER, now=NOW - DAY)
    result = runtime.login_bonus.stamp(state, NOW)
    assert result.data["get_friend_points"] == {
        "friend_point": 5,
        "rental": {"point": 0, "count": 0, "is_max": False},
        "follower": {"point": 5, "count": 1, "is_max": False},
    }
    assert result.sync["item"][0]["data"][0]["val"] == 5


def test_a_newcomer_following_today_is_not_credited_yet(runtime, state):
    call(runtime, state, "social/follow/entry", player_code=NEWCOMER)
    assert runtime.social.collect(state, NOW)["follower"]["count"] == 0


def test_other_saves_see_the_player_through_their_own_transaction(runtime, state):
    seed(runtime, OTHER, name="となり")
    runtime.social.graph.link(OTHER, CODE, NOW - 10)
    runtime.social.graph.commit()
    write_json(
        runtime.store.path("social"),
        {
            "schema_version": runtime.store.schema_version,
            "profile": "social",
            "revision": 0,
            "state": state,
        },
    )
    other = json.loads(runtime.store.path(f"seed:{OTHER}").read_text("utf-8"))["state"]
    rows = runtime.social.following(other, NOW)
    assert [row["profile"]["player_code"] for row in rows] == [CODE]
    assert rows[0]["is_follower"] is False and rows[0]["is_following"] is True


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("social/follow/list", {"per_view": 10}),
        ("social/follower/list", {"per_view": 10}),
        ("social/follower/count", {}),
        ("social/player/selects/recommended", {"_z0": 0}),
        ("social/player/search", {"player_code": NEWCOMER}),
        ("social/follow/entry", {"player_code": NEWCOMER}),
        ("social/follow/greeting", {"player_codes": [NEWCOMER]}),
        ("social/rental_units", {"_z0": 0}),
        ("social/rental_units/selects", {"_z0": 0}),
    ],
)
def test_response_field_names_match_client_dto(runtime, state, endpoint, payload):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text(encoding="utf-8"))
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(
        field for field in schema["models"][model]["fields"] if field["name"] == "data"
    )
    expected = {field["name"] for field in schema["models"][data["type"]]["fields"]}
    assert set(call(runtime, state, endpoint, **payload).data) == expected


async def test_social_endpoints_are_registered(tmp_path):
    http, _, runtime, _ = await connect(tmp_path)
    try:
        assert SOCIAL_ENDPOINTS <= set(runtime.registry.handlers)
    finally:
        await http.close()
