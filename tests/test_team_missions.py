import pytest

from test_battle import NOW, runtime, state, sweep
from test_quest_gates import finish, give, reopen, start


__all__ = ["runtime", "state"]

KEY, SKIP_TICKET = "wallet.quest_key.ancient", "wallet.ticket.quest_skip"
SCOUTS, EARTH, RARE_OR_LOWER = (3, 5), (11, 12), (1, 3)
WARRIOR_GUARD, EPIC = 1, 5
CLASS, RARITY, ELEMENT, SIZE = 100001, 100005, 100000, 100030
FLOORS = {CLASS: 57001, RARITY: 57005, ELEMENT: 57000, SIZE: 57030}


def field(state, *cards):
    for unit, card in zip(state["units"], cards):
        unit["card_id"] = card


def clear(runtime, state, mission):
    quest_id = FLOORS[mission]
    reopen(runtime, quest_id)
    started = start(runtime, state, quest_id)
    for cost in started.data["postpaid_costs"]:
        give(runtime, state, cost["rid"], cost["val"])
    finish(runtime, state, started)


def achieved(state):
    return {record["id"] for record in state["missions"]}


@pytest.fixture(autouse=True)
def keys(runtime, state):
    give(runtime, state, KEY, 10)


@pytest.mark.parametrize(
    ("mission", "fitting", "violating"),
    [
        (CLASS, SCOUTS, WARRIOR_GUARD),
        (RARITY, RARE_OR_LOWER, EPIC),
        (ELEMENT, EARTH, WARRIOR_GUARD),
    ],
)
def test_formation_missions_need_every_unit_to_fit(
    runtime, state, mission, fitting, violating
):
    field(state, fitting[0], violating)
    clear(runtime, state, mission)
    assert mission not in achieved(state)
    field(state, *fitting)
    clear(runtime, state, mission)
    assert mission in achieved(state)
    assert state["events"][-1]["data"]["support_card"] is None


def test_a_formation_mission_belongs_to_its_own_floor(runtime, state):
    field(state, *SCOUTS)
    clear(runtime, state, ELEMENT)
    assert CLASS not in achieved(state)


def test_small_team_missions_count_the_fielded_units(runtime, state):
    mission = runtime.masters.lookup("mission_data", "id", SIZE)
    mission["count"] = 1
    clear(runtime, state, SIZE)
    assert SIZE not in achieved(state)
    mission["count"] = 2
    clear(runtime, state, SIZE)
    assert SIZE in achieved(state)


def test_a_borrowed_unit_is_part_of_the_team(runtime, state):
    field(state, *SCOUTS)

    def announce(support):
        data = {
            "quest_id": FLOORS[CLASS],
            "category": 4,
            "result": "COMPLETE",
            "kills": 0,
            "unit_ids": [1, 2],
            "support_card": support,
        }
        runtime.missions._on_quest_complete(state, {"at": NOW, "data": data})

    announce(WARRIOR_GUARD)
    assert CLASS not in achieved(state)
    announce(SCOUTS[0])
    assert CLASS in achieved(state)


def test_a_sweep_fields_no_team(runtime, state):
    field(state, SCOUTS[0], WARRIOR_GUARD)
    clear(runtime, state, CLASS)
    field(state, *SCOUTS)
    give(runtime, state, SKIP_TICKET, 1)
    runtime.battles.cycle(state, sweep(FLOORS[CLASS], 1), NOW)
    assert CLASS not in achieved(state)


def test_unreadable_formation_rules_are_never_met(runtime, state):
    mission = runtime.masters.lookup("mission_data", "id", CLASS)
    mission["text"] = "未知のみの編成で地下２階をクリア"
    field(state, *SCOUTS)
    clear(runtime, state, CLASS)
    assert CLASS not in achieved(state)
