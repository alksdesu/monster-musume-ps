from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.errors import ApiNotFound
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.store import write_json
from monmusu_ps.features.battle_log import ROUTES
from monmusu_ps.features.player import home
from monmusu_ps.features.reminders import checkoff_revision
from monmusu_ps.runtime import Runtime
from test_battle import closing, connect, opening
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
DAY = 86400
FOREVER = 253_402_300_799
CODE = "BLOGGER1"
OTHER = "BLOGGER2"
VERSION = 37
LOG_ENDPOINTS = set(ROUTES)


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
    return create_player("blog", CODE, runtime.masters, runtime.config.read(), NOW)


def context(runtime, state, now=NOW, **payload):
    session = runtime.sessions._profiles.get(CODE) or runtime.sessions.create(CODE)
    return Context(runtime, payload, state, session, now)


def call(runtime, state, endpoint, now=NOW, **payload):
    return ROUTES[endpoint](context(runtime, state, now, **payload))


def replay(quest_id=10000, version=VERSION, **extra):
    return json.dumps(
        {
            "version": "1.1.0",
            "battleVersion": version,
            "questId": quest_id,
            "questMode": 0,
            "seed": 7,
            "tMenuRank": 0,
            "tMenus": [],
            "team": {"units": [], "boms": [], "rented": False},
            "action": [{"type": 1, "frame": 3, "param": ["x"]}],
            **extra,
        },
        separators=(",", ":"),
    )


def upload(quest_id=10000, result="COMPLETE", rating=3, key=None, text=None):
    return {
        "quest_id": quest_id,
        "result": result,
        "rating": rating,
        "defeat": -1,
        "defeat_max": -1,
        "wild_hunt_score": 0,
        "log_data": replay(quest_id) if text is None else text,
        "team_log_key": key,
    }


def battle(runtime, state, quest_id=10000, rating=3, now=NOW, **statement):
    started = runtime.battles.start(state, opening(quest_id, **statement), now)
    result = "COMPLETE" if rating else "FAILURE"
    finished = runtime.battles.finish(
        state, closing(started.data["txid"], result=result, rating=rating), now
    )
    return finished.data["team_log_key"]


def record(runtime, state, quest_id=10000, rating=3, now=NOW, **statement):
    key = battle(runtime, state, quest_id, rating, now, **statement)
    result = "COMPLETE" if rating else "FAILURE"
    return call(
        runtime,
        state,
        "battle_log/put",
        now=now,
        **upload(quest_id, result, rating, key),
    ).data["log_file_name"]


def seed(runtime, code, name):
    profile = f"seed:{code}"
    state = create_player(profile, code, runtime.masters, runtime.config.read(), NOW)
    state["player_text"]["name"] = name
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


def shelf(runtime):
    return runtime.battle_logs.shelf.rows()


def follow(runtime, source, target):
    runtime.battle_logs.graph.link(source, target, NOW)


def test_uploads_keep_the_replay_and_describe_the_battle(runtime, state):
    name = record(runtime, state)
    assert len(name) == 32 and name.isalnum()
    row = shelf(runtime)[0]
    assert (row["player_code"], row["quest_id"], row["result"], row["rating"]) == (
        CODE,
        10000,
        "COMPLETE",
        3,
    )
    assert (row["version"], row["fmt_ver"], row["created_at"]) == (37, "1.1.0", NOW)
    assert (row["share"], row["likes"]) == (None, [])
    assert runtime.battle_logs.download(name) == replay()
    assert (runtime.settings.saves / "battle_logs" / f"{name}.json").exists()
    assert (runtime.settings.saves / "battle_logs" / "index.json").exists()


def test_team_snapshots_hold_the_unit_rows_as_json_text(runtime, state):
    fielded = deepcopy(state["units"][:2])
    record(runtime, state)
    team = shelf(runtime)[0]["team"]
    assert team["format_ver"] == 1 and team["rental"] is None
    assert [json.loads(slot["unit"]) for slot in team["units"]] == fielded
    assert [slot["slot"] for slot in team["units"]] == [0, 1]
    assert [json.loads(slot["bom"]) for slot in team["boms"]] == state["boms"]
    assert team["boms"][0]["slot"] == 0


def test_team_snapshots_carry_the_support_unit(runtime, state):
    record(runtime, state, rental_unit="MOMIJI02", rental_unit_iid=1)
    rental = json.loads(shelf(runtime)[0]["team"]["rental"])
    assert (rental["card_id"], rental["r_unit_lv"]) == (1, 8)


def test_defeats_are_uploaded_too(runtime, state):
    name = record(runtime, state, rating=0)
    assert name and shelf(runtime)[0]["result"] == "FAILURE"


@pytest.mark.parametrize(
    "text",
    ["", "{not json", replay(10001), replay(version="37"), "x" * 30001],
)
def test_unusable_replays_are_acknowledged_without_saving(runtime, state, text):
    key = battle(runtime, state)
    result = call(runtime, state, "battle_log/put", **upload(key=key, text=text))
    assert result.data == {"log_file_name": ""}
    assert shelf(runtime) == []


def test_uploads_without_a_matching_battle_are_not_saved(runtime, state):
    result = call(runtime, state, "battle_log/put", **upload())
    assert result.data == {"log_file_name": ""}


@pytest.mark.parametrize(
    "payload",
    [
        {"quest_id": "10000"},
        {"log_data": None},
        {"team_log_key": 5},
        {"defeat": "0"},
    ],
)
def test_malformed_uploads_are_errors(runtime, state, payload):
    with pytest.raises(ValueError):
        call(runtime, state, "battle_log/put", **{**upload(), **payload})


def test_retried_uploads_return_the_same_file(runtime, state):
    key = battle(runtime, state)
    first = call(runtime, state, "battle_log/put", **upload(key=key)).data
    second = call(runtime, state, "battle_log/put", **upload(key=key)).data
    assert first == second and len(shelf(runtime)) == 1


def test_only_the_newest_logs_are_kept(runtime, state):
    runtime.config.read()["battle_log"]["keep"] = 2
    names = [record(runtime, state, now=NOW + index) for index in range(3)]
    assert [row["log_file_name"] for row in shelf(runtime)] == names[1:]
    assert runtime.battle_logs.download(names[0]) is None


def test_old_logs_expire_after_the_retention_days(runtime, state):
    old = record(runtime, state)
    data = call(runtime, state, "battle_log/all", now=NOW + 14 * DAY).data
    assert data == {"battle_logs": [], "shared_log_profs": []}
    assert runtime.battle_logs.download(old) is None


def test_my_list_describes_logs_and_shared_profiles(runtime, state):
    state["player_text"]["name"] = "ブロガー"
    name = record(runtime, state)
    call(
        runtime,
        state,
        "battle_log/share",
        log_file_name=name,
        sharing_scope="fwr",
        comment="楽勝",
    )
    data = call(runtime, state, "battle_log/all").data
    assert set(data["battle_logs"][0]) == {
        "player_code",
        "log_file_name",
        "quest_id",
        "result",
        "rating",
        "defeat",
        "defeat_max",
        "wild_hunt_score",
        "training_rank",
        "team",
        "version",
        "fmt_ver",
        "created_at",
    }
    assert data["shared_log_profs"] == [
        {
            "log_file_name": name,
            "name": "ブロガー",
            "like_count": 0,
            "sharing_scope": "fwr",
            "comment": "楽勝",
            "shared_at": NOW,
        }
    ]


def test_locating_a_log_builds_a_public_download_url(runtime, state):
    name = record(runtime, state)
    data = call(runtime, state, "battle_log/", log_file_name=name).data
    assert data == {"log_url": f"http://127.0.0.1:18621/battle_log/{name}"}
    with pytest.raises(ValueError):
        call(runtime, state, "battle_log/", log_file_name="missing")


def test_sharing_validates_scope_and_comment(runtime, state):
    name = record(runtime, state)
    with pytest.raises(ValueError):
        call(
            runtime,
            state,
            "battle_log/share",
            log_file_name=name,
            sharing_scope="all",
            comment=None,
        )
    with pytest.raises(ValueError):
        call(
            runtime,
            state,
            "battle_log/share",
            log_file_name=name,
            sharing_scope="fnd",
            comment="あ" * 33,
        )
    with pytest.raises(ApiNotFound):
        call(
            runtime,
            state,
            "battle_log/share",
            log_file_name="nope",
            sharing_scope="fnd",
            comment=None,
        )
    call(
        runtime,
        state,
        "battle_log/share",
        log_file_name=name,
        sharing_scope="fnd",
        comment="a\r\nb",
    )
    assert shelf(runtime)[0]["share"] == {
        "sharing_scope": "fnd",
        "comment": "ab",
        "shared_at": NOW,
    }


def test_sharing_a_full_rating_clear_records_the_quest(runtime, state):
    name = record(runtime, state)
    result = call(
        runtime,
        state,
        "battle_log/share",
        log_file_name=name,
        sharing_scope="fnd",
        comment=None,
    )
    assert state["shared_quest_logs"] == [10000]
    assert state["events"][-1]["type"] == "battle_log_share"
    assert result.sync is None
    lesser = record(runtime, state, rating=2, now=NOW + 1)
    call(
        runtime,
        state,
        "battle_log/share",
        log_file_name=lesser,
        sharing_scope="fnd",
        comment=None,
    )
    assert state["shared_quest_logs"] == [10000]


def test_sharing_beyond_the_limit_withdraws_the_oldest_share(runtime, state):
    names = [record(runtime, state, now=NOW + index) for index in range(11)]
    for index, name in enumerate(names):
        call(
            runtime,
            state,
            "battle_log/share",
            now=NOW + index,
            log_file_name=name,
            sharing_scope="fwr",
            comment=None,
        )
    shared = [row["log_file_name"] for row in shelf(runtime) if row["share"]]
    assert shared == names[1:]


def test_unsharing_clears_the_share_and_its_likes(runtime, state):
    name = record(runtime, state)
    call(
        runtime,
        state,
        "battle_log/share",
        log_file_name=name,
        sharing_scope="fwr",
        comment=None,
    )
    shelf(runtime)[0]["likes"].append(OTHER)
    call(runtime, state, "battle_log/unshare", log_file_name=name)
    assert (shelf(runtime)[0]["share"], shelf(runtime)[0]["likes"]) == (None, [])
    call(runtime, state, "battle_log/unshare", log_file_name=name)
    with pytest.raises(ApiNotFound):
        call(runtime, state, "battle_log/unshare", log_file_name="nope")


def test_the_feed_lists_other_players_shared_logs_with_their_names(runtime, state):
    other = seed(runtime, OTHER, "となり")
    follow(runtime, CODE, OTHER)
    name = record(runtime, other)
    mine = record(runtime, state, now=NOW + 1)
    for log, owner, moment in ((name, other, NOW), (mine, state, NOW + 1)):
        call(
            runtime,
            owner,
            "battle_log/share",
            now=moment,
            log_file_name=log,
            sharing_scope="fwr",
            comment="どうぞ",
        )
    feed = call(runtime, state, "battle_log/feed/list").data
    assert [row["log_file_name"] for row in feed["battle_logs"]] == [name]
    assert feed["battle_logs"][0]["player_code"] == OTHER
    assert feed["shared_log_profs"][0]["name"] == "となり"
    assert feed["list_of_likes"] == []
    runtime.config.read()["battle_log"]["feed_limit"] = 0
    assert call(runtime, state, "battle_log/feed/list").data["battle_logs"] == []


def test_likes_toggle_idempotently_and_skip_withdrawn_logs(runtime, state):
    other = seed(runtime, OTHER, "となり")
    follow(runtime, CODE, OTHER)
    name = record(runtime, other)
    call(
        runtime,
        other,
        "battle_log/share",
        log_file_name=name,
        sharing_scope="fwr",
        comment=None,
    )
    orders = [{"player_code": OTHER, "log_file_name": name, "order": 1}]
    call(runtime, state, "battle_log/like", battle_log_like_list=orders * 2)
    assert shelf(runtime)[0]["likes"] == [CODE]
    feed = call(runtime, state, "battle_log/feed/list").data
    assert feed["list_of_likes"] == [name]
    assert feed["shared_log_profs"][0]["like_count"] == 1
    orders[0]["order"] = 2
    call(runtime, state, "battle_log/like", battle_log_like_list=orders)
    assert shelf(runtime)[0]["likes"] == []
    gone = [{"player_code": OTHER, "log_file_name": "gone", "order": 1}]
    assert (
        call(runtime, state, "battle_log/like", battle_log_like_list=gone).data is None
    )
    with pytest.raises(ValueError):
        call(
            runtime,
            state,
            "battle_log/like",
            battle_log_like_list=[{**orders[0], "order": 3}],
        )


def test_the_feed_reminder_follows_the_newest_share_until_checked_off(runtime, state):
    other = seed(runtime, OTHER, "となり")
    follow(runtime, CODE, OTHER)
    name = record(runtime, other)
    call(
        runtime,
        other,
        "battle_log/share",
        now=NOW + 5,
        log_file_name=name,
        sharing_scope="fwr",
        comment=None,
    )
    feed = call(runtime, state, "battle_log/feed/list", now=NOW + 6)
    assert feed.sync["rev_reminder"][0]["data"] == [
        {"group": 2, "id": 1, "rev": NOW + 5, "expire": FOREVER}
    ]
    checkoff_revision(context(runtime, state, group=2, id=1, rev=NOW + 5))
    assert call(runtime, state, "battle_log/feed/list", now=NOW + 7).sync is None
    assert "rev_reminder" not in home(context(runtime, state, now=NOW + 8)).sync
    second = record(runtime, other, now=NOW + 9)
    call(
        runtime,
        other,
        "battle_log/share",
        now=NOW + 9,
        log_file_name=second,
        sharing_scope="fwr",
        comment=None,
    )
    rows = home(context(runtime, state, now=NOW + 10)).sync["rev_reminder"][0]["data"]
    assert rows == [{"group": 2, "id": 1, "rev": NOW + 9, "expire": FOREVER}]
    call(runtime, other, "battle_log/unshare", log_file_name=second)
    rows = home(context(runtime, state, now=NOW + 11)).sync["rev_reminder"][0]["data"]
    assert rows == [{"group": 2, "id": 1, "rev": 0, "expire": FOREVER}]


async def test_replays_download_as_plain_text_without_the_envelope(tmp_path):
    http, _, runtime, _ = await connect(tmp_path)
    try:
        assert LOG_ENDPOINTS <= set(runtime.registry.handlers)
        state = create_player("x", CODE, runtime.masters, runtime.config.read(), NOW)
        name = record(runtime, state)
        response = await http.get(f"/battle_log/{name}")
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/plain; charset=utf-8"
        assert await response.text() == replay()
        assert (await http.get("/battle_log/missing")).status == 404
    finally:
        await http.close()


def offered(runtime, scope):
    other = seed(runtime, OTHER, "となり")
    name = record(runtime, other)
    call(
        runtime,
        other,
        "battle_log/share",
        log_file_name=name,
        sharing_scope=scope,
        comment=None,
    )
    return name


def listed(runtime, state):
    feed = call(runtime, state, "battle_log/feed/list").data
    return [row["log_file_name"] for row in feed["battle_logs"]]


def test_logs_shared_with_followers_reach_only_those_who_follow(runtime, state):
    name = offered(runtime, "fwr")
    assert listed(runtime, state) == []
    assert "rev_reminder" not in (home(context(runtime, state)).sync or {})
    with pytest.raises(ValueError):
        call(runtime, state, "battle_log/", log_file_name=name)
    like = [{"player_code": OTHER, "log_file_name": name, "order": 1}]
    call(runtime, state, "battle_log/like", battle_log_like_list=like)
    assert shelf(runtime)[0]["likes"] == []
    follow(runtime, OTHER, CODE)
    assert listed(runtime, state) == []
    follow(runtime, CODE, OTHER)
    assert listed(runtime, state) == [name]
    located = call(runtime, state, "battle_log/", log_file_name=name).data
    assert located["log_url"].endswith(f"/battle_log/{name}")


def test_logs_shared_with_friends_need_a_mutual_follow(runtime, state):
    name = offered(runtime, "fnd")
    follow(runtime, CODE, OTHER)
    assert listed(runtime, state) == []
    follow(runtime, OTHER, CODE)
    assert listed(runtime, state) == [name]


def test_follow_scoping_can_be_switched_off(runtime, state):
    name = offered(runtime, "fnd")
    runtime.config.read()["battle_log"]["scoped_by_follow"] = False
    assert listed(runtime, state) == [name]
