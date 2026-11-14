import json
from pathlib import Path

import pytest

from monmusu_ps.core.calendar import FOREVER
from monmusu_ps.features.event import ROUTES
from test_battle import CODE, NOW, runtime, state
from test_catalog import Context
from test_quest_gates import rejection, won


__all__ = ["runtime", "state"]

ROOT = Path(__file__).resolve().parents[1]
SEALED, SEALED_STAGES, SEALED_CARD = 44, (58000, 58001, 58002), 105
RAID, RAID_STAGES, GROWTH = 119, (90040, 90041), 34
RAID_TAG = f"event.sd_raid_prg_reward:{RAID}"


def entered(runtime, state):
    for stage, rating in zip(SEALED_STAGES, (3, 2, 1)):
        won(runtime, state, stage, rating=rating)
    return runtime.sealed_characters.confirm(state, {"event_id": SEALED}, NOW)


def modify(runtime, state, *mods):
    payload = {
        "event_id": SEALED,
        "mods": [{"type": kind, "no": number} for kind, number in mods],
    }
    return runtime.sealed_characters.modify(state, payload, NOW)


def levels(record):
    return {mod["type"]: mod["activated_no"] for mod in record["mods"]}


def test_sealed_points_come_from_the_best_rating_of_each_stage(runtime, state):
    first = runtime.sealed_characters.confirm(state, {"event_id": SEALED}, NOW)
    assert first == {
        "income_pt": 0,
        "event_record": {
            "event_id": SEALED,
            "acquired_pt": 0,
            "mods": [
                {"type": 1, "activated_no": 0, "activation_cap_no": 12},
                {"type": 2, "activated_no": 0, "activation_cap_no": 4},
                {"type": 3, "activated_no": 0, "activation_cap_no": 5},
            ],
            "is_entered": True,
        },
    }
    earned = entered(runtime, state)
    assert (earned["income_pt"], earned["event_record"]["acquired_pt"]) == (13, 13)
    again = runtime.sealed_characters.confirm(state, {"event_id": SEALED}, NOW)
    assert (again["income_pt"], again["event_record"]["acquired_pt"]) == (0, 13)


def test_the_enhancement_plan_is_bought_with_the_earned_points(runtime, state):
    assert rejection(lambda: modify(runtime, state, (1, 1))) == "NOT_QUALIFIED_EVENT"
    runtime.sealed_characters.confirm(state, {"event_id": SEALED}, NOW)
    assert rejection(lambda: modify(runtime, state, (1, 1))) == "NOT_QUALIFIED_EVENT"
    entered(runtime, state)
    assert levels(modify(runtime, state, (1, 5))["event_record"]) == {1: 5, 2: 0, 3: 0}
    assert levels(modify(runtime, state, (2, 2))["event_record"]) == {1: 5, 2: 2, 3: 0}
    assert rejection(lambda: modify(runtime, state, (3, 2))) == "NOT_ENOUGH_COST"
    lowered = modify(runtime, state, (1, 0), (3, 2))["event_record"]
    assert levels(lowered) == {1: 0, 2: 2, 3: 2}
    assert state["sealed_chara"][str(SEALED)]["mods"] == {"1": 0, "2": 2, "3": 2}


@pytest.mark.parametrize(
    "mods",
    [
        [{"type": 9, "no": 1}],
        [{"type": 1, "no": 13}],
        [{"type": 1, "no": 1}, {"type": 1, "no": 2}],
        [{"type": "1", "no": 1}],
        [{"type": 1}],
        [1],
        None,
    ],
)
def test_unknown_enhancements_are_refused(runtime, state, mods):
    entered(runtime, state)
    payload = {"event_id": SEALED, "mods": mods}
    refused = rejection(lambda: runtime.sealed_characters.modify(state, payload, NOW))
    assert refused == "DISPROP_MOD"


def test_locked_enhancements_wait_for_their_stage(runtime, state):
    entered(runtime, state)
    for row in runtime.masters.table("event_sealed_chara_mods"):
        if (row["mod_type"], row["no"]) in ((2, 3), (2, 4)):
            row["release_terms_quest_id"] = 58005
    record = runtime.sealed_characters.confirm(state, {"event_id": SEALED}, NOW)
    assert record["event_record"]["mods"][1]["activation_cap_no"] == 2
    refused = rejection(lambda: modify(runtime, state, (2, 3)))
    assert refused == "NOT_QUALIFIED_TERMS_OF_MOD"
    won(runtime, state, 58005, rating=1)
    assert levels(modify(runtime, state, (2, 3))["event_record"])[2] == 3


def test_sealed_events_follow_their_term_when_asked_to(runtime, state):
    runtime.config.read()["sealed_chara"]["follow_master_schedule"] = True
    sealed = runtime.sealed_characters
    confirm = {"event_id": SEALED}
    assert rejection(lambda: sealed.confirm(state, confirm, NOW)) == "OUT_OF_SCHEDULE"
    assert rejection(lambda: modify(runtime, state, (1, 1))) == "OUT_OF_SCHEDULE"
    for unknown in (GROWTH, 999999):
        refused = rejection(lambda: sealed.confirm(state, {"event_id": unknown}, NOW))
        assert refused == "INVALID_EVENT_ID"
    with pytest.raises(ValueError):
        sealed.confirm(state, {"event_id": "44"}, NOW)


def test_the_sealed_character_arrives_enhanced_once_the_event_is_over(runtime, state):
    runtime.attendance.check_in(state, NOW)
    assert all(unit["card_id"] != SEALED_CARD for unit in state["units"])
    entered(runtime, state)
    modify(runtime, state, (1, 6), (2, 2), (3, 1))
    runtime.attendance.check_in(state, NOW + 60)
    unit = next(row for row in state["units"] if row["card_id"] == SEALED_CARD)
    assert unit["rank"] == 2
    assert unit["r_unit_lv"] == min(60, runtime.progression.level_cap(unit))
    assert (unit["exskill_lv"], unit["upa"]["all_pt"]) == (3, 1)
    reminder = {"group": 3, "id": SEALED, "expire": FOREVER}
    assert state["flag_reminders"].count(reminder) == 1
    assert state["sealed_chara"][str(SEALED)]["delivered"] is True
    assert rejection(lambda: modify(runtime, state, (1, 7))) == "OUT_OF_SCHEDULE"
    runtime.attendance.check_in(state, NOW + 120)
    assert sum(row["card_id"] == SEALED_CARD for row in state["units"]) == 1
    assert state["flag_reminders"].count(reminder) == 1


def kills(runtime, state):
    for stage, best, total in zip(RAID_STAGES, (120, 60), (600, 500)):
        won(runtime, state, stage).update(ec_best_score=best, ec_total_score=total)


def raid(runtime, state, scene="quest", event_id=RAID):
    payload = {"scene": scene, "event_id": event_id}
    return runtime.subdue_raids.confirm(state, payload, NOW)


def test_raid_tiers_are_mailed_once_the_kills_reach_them(runtime, state):
    idle = raid(runtime, state, "login")
    assert idle.data == {
        "progress_of_self": 0,
        "progress_of_best": 0,
        "progress_of_everyone": 0,
        "rewards_of_self": [],
        "rewards_of_best": [],
        "rewards_of_everyone": [],
    }
    kills(runtime, state)
    result = raid(runtime, state).data
    assert (result["progress_of_self"], result["progress_of_best"]) == (1100, 120)
    assert result["progress_of_everyone"] == 11_000_000
    assert [row["progress_num"] for row in result["rewards_of_self"]] == [
        100,
        500,
        1000,
    ]
    assert [row["progress_num"] for row in result["rewards_of_best"]] == [50, 100]
    assert result["rewards_of_everyone"] == []
    assert result["rewards_of_self"][0]["rewards"] == [
        {"rid": "unit.card.254", "val": 1, "inc": 0, "inbox": 1, "over": 0}
    ]
    letters = [row for row in state["inbox"] if row["tags"][0] == RAID_TAG]
    table_id = runtime.masters.lookup("event_list_data", "id", RAID)[
        "event_dataTable_id"
    ]
    title = runtime.masters.lookup("everyones_killevent_data", "dataId", table_id)
    assert len(letters) == 5
    assert {row["message"] for row in letters} == {title["box_message"]}
    assert state["subdue_raid"] == {str(RAID): [409, 410, 411, 416, 417]}
    again = raid(runtime, state).data
    assert again["rewards_of_self"] == again["rewards_of_best"] == []
    assert again["progress_of_self"] == 1100


def test_the_shared_raid_count_is_scaled_from_the_local_saves(runtime, state):
    kills(runtime, state)
    runtime.config.read()["subdue_raid"]["everyone"] = {
        "base": 20_000_000,
        "multiplier": 5000,
    }
    result = raid(runtime, state).data
    assert result["progress_of_everyone"] == 25_500_000
    assert [row["progress_num"] for row in result["rewards_of_everyone"]] == [
        25_000_000
    ]


def test_raid_confirmations_name_a_raid_event_and_a_known_scene(runtime, state):
    for unknown in (SEALED, 999999):
        refused = rejection(lambda: raid(runtime, state, event_id=unknown))
        assert refused == "INVALID_EVENT_ID"
    for scene in ("home", None, 1):
        with pytest.raises(ValueError):
            raid(runtime, state, scene)
    with pytest.raises(ValueError):
        raid(runtime, state, event_id="119")
    runtime.config.read()["subdue_raid"]["follow_master_schedule"] = True
    assert rejection(lambda: raid(runtime, state)) == "OUT_OF_SCHEDULE"


@pytest.mark.parametrize(
    ("endpoint", "payload", "nested"),
    [
        ("event/sealed_chara/confirm", {"event_id": SEALED}, "event_record"),
        (
            "event/sealed_chara/modify",
            {"event_id": SEALED, "mods": [{"type": 1, "no": 1}]},
            "event_record",
        ),
        ("event/subdue_raid/confirm", {"scene": "quest", "event_id": RAID}, None),
    ],
)
def test_response_field_names_match_client_dto(
    runtime, state, endpoint, payload, nested
):
    entered(runtime, state)
    kills(runtime, state)
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]

    def names(model):
        return {row["name"] for row in schema[model]["fields"]}

    context = Context(runtime, payload, state, runtime.sessions.create(CODE), NOW)
    listing = ROUTES[endpoint](context).data
    model = runtime.contracts[endpoint]["response_models"]["200"]
    data = next(field for field in schema[model]["fields"] if field["name"] == "data")
    assert set(listing) == names(data["type"])
    if nested:
        assert set(listing[nested]) == names("SealedCharaEventRecord")
        assert set(listing[nested]["mods"][0]) == names("AppyedSealedCharaMod")
    else:
        assert set(listing["rewards_of_self"][0]) == names("ProgressRewardResult")
