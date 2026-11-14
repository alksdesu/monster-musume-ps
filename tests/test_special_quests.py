from types import SimpleNamespace

import pytest

from monmusu_ps.core.initial import create_player
from monmusu_ps.core.quest_gates import quest_record
from monmusu_ps.core.store import write_json
from test_battle import NOW, closing, opening, runtime, state, sweep
from test_quest_gates import finish, give, master, rejection, start, won


__all__ = ["runtime", "state"]

HOUR = 3600
SECRET, SECRET_LAST, HUNT, EVENT_HUNT, EVENT_CATEGORY = 60000, 60007, 65000, 69000, 6
NORMAL_DUNGEON, PREMIUM, KEY_UNIT = 55000, 56012, 32
TRAINING, EXORCISM = 80130, 58010
POINTS = "wallet.event.pt_skillunlock221024"
PROLOGUE, FLOOR, TOWER_EVENT = 91000, 91001, 135
GOLD, TOWER_POINTS = "wallet.point.gold", "wallet.event.pt_temple01"
SKIP_TICKET = "wallet.ticket.quest_skip"


def scramble(score, key):
    return [
        0 if byte == 0 else (byte ^ key[index % len(key)]) or key[index % len(key)]
        for index, byte in enumerate(score.to_bytes(4, "big"))
    ]


def hunt(runtime, state, score, quest_id=HUNT, rating=2, begin=NOW, end=NOW, log=None):
    started = runtime.battles.start(state, opening(quest_id), begin)
    key = quest_record(state, quest_id)["log_key"]
    payload = closing(
        started.data["txid"],
        rating=rating,
        log=scramble(score, key) if log is None else log,
    )
    payload["statement"]["ec_score"] = score
    return runtime.battles.finish(state, payload, end)


def crossed(runtime, table_id, before, after):
    nodes = runtime.secret_quests.tracks.crossed(table_id, before, after)
    return [order for node in nodes for order in node["rewards"]]


def secret_row(runtime, state, category_id):
    listing = runtime.secret_quests.listing(state)
    return next(
        (summary, rank["rank"])
        for summary, rank in zip(listing["quest_secrets"], listing["wild_hunt_ranks"])
        if summary["category_id"] == category_id == rank["category_id"]
    )


def test_the_secret_listing_names_every_category(runtime, state):
    listing = runtime.secret_quests.listing(state)
    order = [1, 10, 11, 2, 3, 4, 5, 6, 7, 8, 9]
    assert listing == {
        "quest_secrets": [
            {"category_id": number, "sum_star_count": 0, "max_hunt": 0, "total_hunt": 0}
            for number in order
        ],
        "wild_hunt_ranks": [{"category_id": number, "rank": 0} for number in order],
    }


def test_secret_clears_add_up_the_stars_of_their_category(runtime, state):
    won(runtime, state, 10607)
    result = finish(runtime, state, start(runtime, state, SECRET), rating=2)
    summary = {"category_id": 1, "sum_star_count": 2, "max_hunt": 0, "total_hunt": 0}
    assert result.sync["quest_secret"][0]["data"] == [summary]
    assert secret_row(runtime, state, 1) == (summary, 0)


def test_a_hunt_keeps_its_best_and_total_score_and_pays_new_tiers(runtime, state):
    won(runtime, state, SECRET_LAST)
    result = hunt(runtime, state, 250)
    record = quest_record(state, HUNT)
    assert (record["wh_best_score"], record["wh_total_score"]) == (250, 250)
    tiers = crossed(runtime, 1, 0, 250)
    assert len(tiers) == 2 and result.data["report"]["wild_hunt_rewards"] == tiers
    summary = {
        "category_id": 1,
        "sum_star_count": 3,
        "max_hunt": 250,
        "total_hunt": 250,
    }
    assert result.sync["quest_secret"][0]["data"] == [summary]
    assert secret_row(runtime, state, 1) == (summary, 10000)
    again = hunt(runtime, state, 150, rating=3)
    assert (record["wh_best_score"], record["wh_total_score"]) == (250, 400)
    assert "wild_hunt_rewards" not in again.data["report"]
    assert "is_aggregating" not in again.data["report"]
    listed = runtime.clear_teams.teams(state, {"quest_id": HUNT})["team_logs"]
    assert [row["wild_hunt_score"] for row in listed] == [250]


def test_hunt_scores_stop_at_the_native_cap(runtime, state):
    won(runtime, state, SECRET_LAST)
    hunt(runtime, state, 123456)
    record = quest_record(state, HUNT)
    assert (record["wh_best_score"], record["wh_total_score"]) == (99999, 99999)


def test_a_hunt_log_that_disagrees_with_the_score_is_tolerated_or_refused(
    runtime, state
):
    won(runtime, state, SECRET_LAST)
    hunt(runtime, state, 300, log=[0, 0, 0, 0])
    assert quest_record(state, HUNT)["wh_best_score"] == 300
    runtime.config.read()["battle"]["hunt_score_mismatch"] = "reject"
    refused = rejection(lambda: hunt(runtime, state, 300, log=[0, 0, 0, 0]))
    assert refused == "INVALID_STATEMENT"
    hunt(runtime, state, 400)


@pytest.mark.parametrize("log", [[], [0, 0, 0], [0, 0, 0, 256], [0, 0, 0, "1"]])
def test_a_hunt_log_is_four_bytes(runtime, state, log):
    won(runtime, state, SECRET_LAST)
    refused = rejection(lambda: hunt(runtime, state, 10, log=log))
    assert refused == "INVALID_STATEMENT"


def test_hunt_ranks_compare_the_local_saves(runtime, state):
    won(runtime, state, SECRET_LAST)
    hunt(runtime, state, 250)
    other = create_player(
        "other", "OTHER001", runtime.masters, runtime.config.read(), NOW
    )
    rival = won(runtime, other, HUNT)
    document = {
        "schema_version": runtime.store.schema_version,
        "profile": "seed:other",
        "revision": 0,
        "state": other,
    }
    for score, rank in ((500, 10000), (100, 5000)):
        rival["wh_best_score"] = score
        write_json(runtime.store.path("seed:other"), document)
        assert secret_row(runtime, state, 1)[1] == rank


def test_event_hunts_follow_their_category_term_and_the_tally_break(runtime, state):
    won(runtime, state, 10607)
    later = NOW + HOUR
    refused = rejection(
        lambda: runtime.battles.start(state, opening(EVENT_HUNT), later)
    )
    assert refused == "OUT_OF_SCHEDULE"
    category = runtime.masters.lookup(
        "quest_secret_category_table", "id", EVENT_CATEGORY
    )
    category.update(start_at="", end_at="")
    refused = rejection(lambda: runtime.battles.start(state, opening(EVENT_HUNT), NOW))
    assert refused == "AGGREGATING"
    result = hunt(runtime, state, 600, quest_id=EVENT_HUNT, begin=later, end=later)
    best = crossed(runtime, category["hunt_track_rewards_id"], 0, 600)
    total = crossed(runtime, category["hunt_track_total_rewards_id"], 0, 600)
    report = result.data["report"]
    assert (len(best), len(total)) == (6, 1)
    assert report["wild_hunt_best_rewards"] == best
    assert report["wild_hunt_total_rewards"] == total
    assert report["is_aggregating"] is False and "wild_hunt_rewards" not in report
    mailed = [
        letter["reward"]
        for letter in state["inbox"]
        if letter["tags"][0] == f"quest.wild_hunt:{EVENT_CATEGORY}"
    ]
    assert mailed == best + total
    held = hunt(runtime, state, 900, quest_id=EVENT_HUNT, begin=NOW - HOUR, end=NOW)
    assert held.data["report"]["is_aggregating"] is True
    record = quest_record(state, EVENT_HUNT)
    assert (record["wh_best_score"], record["wh_total_score"], record["wins"]) == (
        600,
        600,
        2,
    )
    runtime.config.read()["battle"]["hunt_aggregation"] = False
    hunt(runtime, state, 900, quest_id=EVENT_HUNT)
    assert record["wh_best_score"] == 900


def test_a_dungeon_run_drops_one_sub_skill(runtime, state):
    won(runtime, state, 10004)
    give(runtime, state, "wallet.quest_key.normal", 2)
    started = start(runtime, state, NORMAL_DUNGEON)
    prizes = started.data["sub_skill_list"]
    table = runtime.subskill_prizes.table(None, master(runtime, NORMAL_DUNGEON))
    offered = {f"skill.attachable.{prize['subSkillId']}" for prize in table["prizes"]}
    assert len(prizes) == 1 and prizes[0]["val"] == 1 and prizes[0]["rid"] in offered
    result = finish(runtime, state, started)
    assert result.data["report"]["sub_skill_rewards"] == prizes
    assert runtime.resources.balance(state, prizes[0]["rid"]) == 1
    assert "subskills" not in quest_record(state, NORMAL_DUNGEON)
    lost = finish(
        runtime,
        state,
        start(runtime, state, NORMAL_DUNGEON),
        result="FAILURE",
        rating=0,
    )
    assert "sub_skill_rewards" not in lost.data["report"]
    assert sum(runtime.resources.balance(state, rid) for rid in offered) == 1
    assert start(runtime, state, 10000).data["sub_skill_list"] == []


def premium(runtime, state):
    won(runtime, state, 10004)
    give(runtime, state, f"unit.card.{KEY_UNIT}")
    give(runtime, state, "wallet.quest_key.premium", 5)
    quest = master(runtime, PREMIUM)
    table = runtime.subskill_prizes.table(None, quest)
    unique = next(
        prize["subSkillId"] for prize in table["prizes"] if prize["limitation"] > 0
    )
    common = next(
        prize["subSkillId"] for prize in table["prizes"] if prize["limitation"] == 0
    )
    wanted = [common]
    runtime.subskill_prizes.rng = SimpleNamespace(
        choices=lambda population, weights: [wanted[0]]
    )
    return quest, table, unique, wanted


def test_missing_the_unique_prize_raises_its_odds_until_it_is_won(runtime, state):
    quest, table, unique, wanted = premium(runtime, state)
    prizes = runtime.subskill_prizes
    finish(runtime, state, start(runtime, state, PREMIUM))
    record = quest_record(state, PREMIUM)
    assert record["subskills"] == [{"id": unique, "hit": 0, "miss": 1}]
    assert prizes.shares(table, record)[unique] > prizes.shares(table)[unique]
    listed = prizes.listing(state, {"quest_id": PREMIUM, "category": 7, "section": 0})
    assert listed["table_id"] == table["id"]
    wanted[0] = unique
    result = finish(runtime, state, start(runtime, state, PREMIUM))
    assert record["subskills"] == [{"id": unique, "hit": 1, "miss": 1}]
    assert result.sync["quest_record"][0]["data"][0]["subskills"] == record["subskills"]
    assert prizes.table(record, quest)["id"] == f"{table['id']}-2"
    finish(runtime, state, start(runtime, state, PREMIUM))
    assert record["subskills"] == [{"id": unique, "hit": 1, "miss": 1}]
    assert runtime.resources.balance(state, f"skill.attachable.{unique}") == 2


def test_later_draws_of_a_run_move_on_once_the_unique_prize_falls(runtime, state):
    quest, table, unique, wanted = premium(runtime, state)
    runtime.config.read()["subskill_prizes"]["drops_per_clear"] = 2
    wanted[0] = unique
    started = start(runtime, state, PREMIUM)
    order = {"rid": f"skill.attachable.{unique}", "val": 1}
    assert started.data["sub_skill_list"] == [order, order]
    finish(runtime, state, started)
    assert quest_record(state, PREMIUM)["subskills"] == [
        {"id": unique, "hit": 1, "miss": 0}
    ]


def points(report):
    return next(row["val"] for row in report["clear_rewards"] if row["rid"] == POINTS)


def test_training_menus_raise_the_event_points_and_the_best_rank(runtime, state):
    won(runtime, state, 10006)
    started = start(runtime, state, TRAINING, t_menus=[1031, 1028])
    result = finish(runtime, state, started, rating=1)
    record = quest_record(state, TRAINING)
    assert (record["t_best_rank"], record["t_cleared_menus"]) == (2, [1028, 1031])
    assert points(result.data["report"]) == 172
    plain = finish(runtime, state, start(runtime, state, TRAINING), rating=1)
    assert points(plain.data["report"]) == 160
    assert (record["t_best_rank"], record["t_cleared_menus"]) == (2, [1028, 1031])


def test_a_training_sweep_pays_at_the_best_rank_after_any_clear(runtime, state):
    won(runtime, state, 10006)
    give(runtime, state, SKIP_TICKET, 2)
    refused = rejection(lambda: runtime.battles.cycle(state, sweep(TRAINING, 1), NOW))
    assert refused == "NOT_QUALIFIED"
    started = start(runtime, state, TRAINING, t_menus=[1028, 1031])
    finish(runtime, state, started, rating=1)
    result = runtime.battles.cycle(state, sweep(TRAINING, 2), NOW)
    assert points(result.data["report"]) == 172 * 2
    record = quest_record(state, TRAINING)
    assert (record["wins"], record["t_best_rank"]) == (3, 2)
    assert record["t_cleared_menus"] == [1028, 1031]


@pytest.mark.parametrize(
    ("quest_id", "menus"),
    [
        (TRAINING, [1028, 1029]),
        (TRAINING, [20]),
        (TRAINING, [1028, 1028]),
        (TRAINING, ["1028"]),
        (TRAINING, "1028"),
        (TRAINING, None),
        (10000, [1028]),
    ],
)
def test_unselectable_menus_are_refused(runtime, state, quest_id, menus):
    won(runtime, state, 10006)
    refused = rejection(lambda: start(runtime, state, quest_id, t_menus=menus))
    assert refused == "INVALID_STATEMENT"


def test_exorcism_menus_open_with_a_clear_and_with_the_best_rank(runtime, state):
    won(runtime, state, 10607)
    basic, advanced = list(range(100020, 100030)), [100030]
    refused = rejection(lambda: start(runtime, state, EXORCISM, t_menus=basic[:1]))
    assert refused == "INVALID_STATEMENT"
    finish(runtime, state, start(runtime, state, EXORCISM))
    refused = rejection(lambda: start(runtime, state, EXORCISM, t_menus=advanced))
    assert refused == "INVALID_STATEMENT"
    finish(runtime, state, start(runtime, state, EXORCISM, t_menus=basic))
    assert quest_record(state, EXORCISM)["t_best_rank"] == 10
    start(runtime, state, EXORCISM, t_menus=advanced)
    event = runtime.training_menus.event(master(runtime, EXORCISM))
    grown = runtime.event_growth.confirm(state, {"event_id": event["id"]}, NOW)
    assert grown.data["sum_star_count"] == 10


def climb(runtime, state, rating, tower):
    started = start(runtime, state, FLOOR)
    payload = closing(started.data["txid"], rating=rating)
    payload["statement"]["tower"] = tower
    return runtime.battles.finish(state, payload, NOW)


def test_a_tower_floor_is_scored_and_pays_its_score_tiers(runtime, state):
    won(runtime, state, 10607)
    start(runtime, state, PROLOGUE)
    gold = runtime.resources.balance(state, GOLD)
    result = climb(runtime, state, 3, {"deployed": 2, "tokens": 0})
    scored = result.data["report"]["event_result"]["tower"]
    assert scored == {
        "final_score": 54450,
        "record_score": 54450,
        "prev_record_score": 0,
        "bonus_details": [
            {"category": "星獲得数", "org": 3, "factor": "300"},
            {"category": "編成人数", "org": 2, "factor": "11"},
            {"category": "出撃人数", "org": 2, "factor": "11"},
            {"category": "出撃トークン", "org": 0, "factor": "1"},
            {"category": "レンタル", "org": 0, "factor": "1.5"},
        ],
        "floor_reward_ids": list(range(1, 22)),
    }
    assert runtime.resources.balance(state, GOLD) == gold + 50000 * 11
    record = quest_record(state, FLOOR)
    assert record["event_payloads"] == {"tower": {"score": 54450, "progress": 54450}}
    progression = {
        "id": 1,
        "total_score": 54450,
        "stars": 3,
        "daily_pt_1": {"rid": TOWER_POINTS, "val": 50},
        "daily_pt_2": None,
        "daily_pt_3": None,
    }
    assert state["player"]["towers"] == [progression]
    assert result.sync["player"][0]["data"][0]["towers"] == [progression]
    assert (
        result.sync["quest_record"][0]["data"][0]["event_payloads"]
        == (record["event_payloads"])
    )
    lower = climb(runtime, state, 1, {"deployed": 2, "tokens": 9})
    scored = lower.data["report"]["event_result"]["tower"]
    assert (scored["final_score"], scored["record_score"]) == (12705, 54450)
    assert (scored["prev_record_score"], scored["floor_reward_ids"]) == (54450, [])
    assert scored["bonus_details"][3] == {
        "category": "出撃トークン",
        "org": 9,
        "factor": "0.7",
    }
    assert state["player"]["towers"] == [progression]
    grown = runtime.event_growth.confirm(state, {"event_id": TOWER_EVENT}, NOW)
    assert grown.data["achieved_list"] == [61000, 61001, 61002]


@pytest.mark.parametrize(
    "tower", [None, {"deployed": -1, "tokens": 0}, {"deployed": 1}, [1, 0], "1"]
)
def test_a_tower_report_names_its_deployments(runtime, state, tower):
    won(runtime, state, PROLOGUE)
    refused = rejection(lambda: climb(runtime, state, 3, tower))
    assert refused == "INVALID_STATEMENT"


def test_the_tower_mails_its_daily_points_at_the_first_login_of_a_day(runtime, state):
    won(runtime, state, PROLOGUE)
    runtime.attendance.check_in(state, NOW)

    def mailed():
        return [
            letter["reward"]
            for letter in state["inbox"]
            if letter["tags"][0] == "tower.daily_points:1"
        ]

    assert mailed() == []
    climb(runtime, state, 3, {"deployed": 2, "tokens": 0})
    runtime.attendance.check_in(state, NOW + 60)
    assert mailed() == []
    runtime.attendance.check_in(state, NOW + 24 * HOUR)
    assert mailed() == [{"rid": TOWER_POINTS, "val": 50}]
    runtime.config.read()["tower"]["daily_points"]["multiplier"] = 2
    runtime.attendance.check_in(state, NOW + 48 * HOUR)
    assert mailed()[-1] == {"rid": TOWER_POINTS, "val": 100}
    runtime.config.read()["tower"]["daily_points"]["enabled"] = False
    runtime.attendance.check_in(state, NOW + 72 * HOUR)
    assert len(mailed()) == 2
