import pytest

from monmusu_ps.core.errors import ApiRejected
from monmusu_ps.core.quest_gates import quest_record
from test_battle import NOW, closing, opening, runtime, state, sweep


__all__ = ["runtime", "state"]

PREMIUM, KEY_UNIT = 56012, 32
PREMIUM_KEY = "wallet.quest_key.premium"
NORMAL_DUNGEON, BOSS, HUNT, FLOOR, PROLOGUE = 55000, 50101, 65000, 91001, 91000
RAID, RAID_STAMINA = 90000, 9
SKIP_TICKET = "wallet.ticket.quest_skip"
BEST_KILLS, TOTAL_KILLS, LISTED = 50, 51, 29
RAID_MISSION = 33502


def master(runtime, quest_id):
    return runtime.masters.lookup("quest_data", "id", quest_id)


def won(runtime, state, quest_id, rating=3, wins=1):
    row = runtime.battles.new_record(master(runtime, quest_id))
    row.update(wins=wins, tries=wins, record_rating=rating)
    state["quest_records"].append(row)
    return row


def give(runtime, state, rid, amount=1):
    runtime.resources.grant(state, [{"rid": rid, "val": amount}], NOW)


def rejection(action):
    with pytest.raises(ApiRejected) as refused:
        action()
    return refused.value.reason


def start(runtime, state, quest_id, **statement):
    return runtime.battles.start(state, opening(quest_id, **statement), NOW)


def finish(runtime, state, started, kills=0, **fields):
    payload = closing(started.data["txid"], **fields)
    payload["statement"]["ec_score"] = kills
    return runtime.battles.finish(state, payload, NOW)


def reopen(runtime, quest_id):
    master(runtime, quest_id).update(openTime="", closeTime="", needQuestIndex=0)


def test_premium_dungeons_need_their_key_unit(runtime, state):
    won(runtime, state, 10004)
    give(runtime, state, PREMIUM_KEY)
    assert all(unit["card_id"] != KEY_UNIT for unit in state["units"])
    assert rejection(lambda: start(runtime, state, PREMIUM)) == "NOT_QUALIFIED"
    give(runtime, state, f"unit.card.{KEY_UNIT}")
    assert start(runtime, state, PREMIUM).data["postpaid_costs"] == [
        {"rid": PREMIUM_KEY, "val": 1}
    ]


def test_release_conditions_ask_for_read_stories_and_rated_quests(runtime, state):
    master(runtime, 10000)["releaseCondId"] = 1
    assert rejection(lambda: start(runtime, state, 10000)) == "NOT_QUALIFIED"
    state["stories"].append({"id": "story.6.4.8", "is_already_read": False})
    assert rejection(lambda: start(runtime, state, 10000)) == "NOT_QUALIFIED"
    state["stories"][0]["is_already_read"] = True
    start(runtime, state, 10000)
    condition = runtime.masters.table("quest_release_condition")[0]
    condition["releaseConditionData"][0].update(
        check_type=0, check_values=[10001, 0, 0]
    )
    assert rejection(lambda: start(runtime, state, 10000)) == "NOT_QUALIFIED"
    won(runtime, state, 10001, rating=1)
    start(runtime, state, 10000)
    master(runtime, 10000)["releaseCondId"] = 99
    assert rejection(lambda: start(runtime, state, 10000)) == "NOT_QUALIFIED"


@pytest.mark.parametrize(("timing", "runs"), [("game_clear", 1), ("game_try", 1)])
def test_quest_limits_count_lifetime_clears_or_tries(runtime, state, timing, runs):
    master(runtime, 10000).update(questlimit_timing=timing, questlimit_max_times=runs)
    finish(runtime, state, start(runtime, state, 10000))
    assert rejection(lambda: start(runtime, state, 10000)) == (
        "LIMIT_OVER_OF_QUEST_EXEC"
    )


def test_a_sweep_counts_every_run_against_the_limit(runtime, state):
    master(runtime, 10000).update(
        questlimit_timing="game_clear", questlimit_max_times=3
    )
    won(runtime, state, 10000)
    give(runtime, state, SKIP_TICKET, 5)
    refused = rejection(lambda: runtime.battles.cycle(state, sweep(10000, 3), NOW))
    assert refused == "LIMIT_OVER_OF_QUEST_EXEC"
    runtime.battles.cycle(state, sweep(10000, 2), NOW)
    assert quest_record(state, 10000)["wins"] == 3


@pytest.mark.parametrize("quest_id", [BOSS, HUNT, FLOOR, PROLOGUE, RAID])
def test_only_the_categories_the_client_sweeps_are_swept(runtime, state, quest_id):
    reopen(runtime, quest_id)
    won(runtime, state, quest_id)
    give(runtime, state, SKIP_TICKET, 5)
    refused = rejection(lambda: runtime.battles.cycle(state, sweep(quest_id, 1), NOW))
    assert refused == "INVALID_QUEST"


def test_dungeon_sweeps_stop_at_ten_runs(runtime, state):
    won(runtime, state, 10004)
    won(runtime, state, NORMAL_DUNGEON)
    give(runtime, state, SKIP_TICKET, 20)
    give(runtime, state, "wallet.quest_key.normal", 20)
    refused = rejection(
        lambda: runtime.battles.cycle(state, sweep(NORMAL_DUNGEON, 11), NOW)
    )
    assert refused == "TOO_LARGE_NUM_OF_CYCLE_EXEC"
    result = runtime.battles.cycle(state, sweep(NORMAL_DUNGEON, 10), NOW)
    assert sum(row["val"] for row in result.data["report"]["sub_skill_rewards"]) == 10


def test_a_story_quest_is_cleared_by_opening_it(runtime, state):
    won(runtime, state, 10607)
    assert rejection(lambda: start(runtime, state, FLOOR)) == "NOT_QUALIFIED"
    watched = start(runtime, state, PROLOGUE)
    assert set(watched.data) == {
        "txid",
        "prepaid_costs",
        "postpaid_costs",
        "drop_list",
        "sub_skill_list",
    }
    record = quest_record(state, PROLOGUE)
    assert (record["tries"], record["wins"], record["record_rating"]) == (1, 1, 0)
    assert state["quest_transactions"] == {}
    event = state["events"][-1]
    assert (event["id"], event["type"]) == (watched.data["txid"], "quest_complete")
    assert (event["data"]["result"], event["data"]["rating"]) == ("COMPLETE", 0)
    assert watched.sync["quest_record"][0]["data"] == [record]
    start(runtime, state, FLOOR)


def test_endless_quests_record_capped_kills(runtime, state):
    reopen(runtime, BOSS)
    first = finish(runtime, state, start(runtime, state, BOSS), kills=1005)
    assert first.data["result"] == "COMPLETE"
    record = quest_record(state, BOSS)
    assert (record["ec_best_score"], record["ec_total_score"]) == (999, 999)
    assert state["events"][-1]["data"]["kills"] == 999
    finish(runtime, state, start(runtime, state, BOSS), kills=40)
    assert (record["ec_best_score"], record["ec_total_score"]) == (999, 1039)
    assert first.sync["quest_record"][0]["data"][0]["ec_best_score"] == 999


@pytest.mark.parametrize("kills", [-1, "3", None, 2.5])
def test_kill_counts_are_non_negative_integers(runtime, state, kills):
    started = start(runtime, state, 10000)
    refused = rejection(lambda: finish(runtime, state, started, kills=kills))
    assert refused == "INVALID_STATEMENT"


def test_ordinary_quests_keep_no_kill_scores(runtime, state):
    finish(runtime, state, start(runtime, state, 10000), kills=7)
    record = quest_record(state, 10000)
    assert (record["ec_best_score"], record["ec_total_score"]) == (0, 0)
    assert state["events"][-1]["data"]["kills"] == 0


@pytest.mark.parametrize("mode", [1, 2, -1, "1", None, True])
def test_the_triple_sortie_belongs_to_everyones_subjugation(runtime, state, mode):
    refused = rejection(lambda: start(runtime, state, 10000, r_mode=mode))
    assert refused == "INVALID_STATEMENT"


def test_a_triple_sortie_triples_the_stamina_and_the_running_total(runtime, state):
    reopen(runtime, RAID)
    started = start(runtime, state, RAID, r_mode=1)
    assert started.data["postpaid_costs"][0]["val"] == RAID_STAMINA * 3
    finish(runtime, state, started, kills=50)
    record = quest_record(state, RAID)
    assert (record["ec_best_score"], record["ec_total_score"]) == (50, 150)
    finish(runtime, state, start(runtime, state, RAID), kills=20)
    assert (record["ec_best_score"], record["ec_total_score"]) == (50, 170)
    stamina = state["modules"]["stamina"][0]
    assert stamina["amount"] == stamina["capacity"] - RAID_STAMINA * 4


def test_kill_missions_follow_the_best_and_the_running_total(runtime, state):
    reopen(runtime, BOSS)
    rows = [
        row
        for row in runtime.masters.table("mission_data")
        if row["target"] == str(BOSS)
    ]
    for row in rows:
        row.update(startDate="", endDate="")
    finish(runtime, state, start(runtime, state, BOSS), kills=5)
    achieved = {record["id"] for record in state["missions"]}
    for kind in (BEST_KILLS, TOTAL_KILLS):
        reached = {
            row["id"] for row in rows if row["condition"] == kind and row["count"] <= 5
        }
        missed = {
            row["id"] for row in rows if row["condition"] == kind and row["count"] > 5
        }
        assert reached and reached <= achieved and not missed & achieved
    counter = next(
        row for row in state["mission_counters"] if row["name"] == "4.mugen220920"
    )
    assert counter["val"] == 5


def test_listed_quest_missions_count_clears_of_any_listed_quest(runtime, state):
    reopen(runtime, RAID)
    mission = runtime.masters.lookup("mission_data", "id", RAID_MISSION)
    assert mission["condition"] == LISTED
    mission.update(startDate="", endDate="")
    finish(runtime, state, start(runtime, state, 10000))
    assert RAID_MISSION not in {record["id"] for record in state["missions"]}
    finish(runtime, state, start(runtime, state, RAID), kills=3)
    assert RAID_MISSION in {record["id"] for record in state["missions"]}
