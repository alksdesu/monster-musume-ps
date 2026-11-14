import base64
from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import random
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.context import Context
from monmusu_ps.core.errors import ApiNotFound, ApiRejected
from monmusu_ps.core.initial import create_player
from monmusu_ps.features.gacha import ROUTES
from monmusu_ps.runtime import Runtime
from test_battle import connect, post


ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "captures/session_20261111_012410.jsonl"
NOW = 1_794_340_000
RESET = 1_794_340_800
TUTORIAL = "gacha.normal.tutorial_L"
LOCAL = "gacha.normal.local"
MEDAL = "wallet.gachamedal.gmedal_common"
UNIT_PRIZE = {"rid": "unit.card.23", "val": 1, "inc": 1, "inbox": 0, "over": 0}
COMMON, RARE, EPIC = 57, 6, 23


class Dice:
    def __init__(self, *points):
        self.points = list(points)

    def random(self):
        return self.points.pop(0)


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
    state = create_player(
        "gacha", "GACHA001", runtime.masters, runtime.config.read(), NOW
    )
    state["player"]["tutorial"] = 500
    return state


@pytest.fixture
def pool(runtime):
    return runtime.masters.lookup("gacha_data", "id", TUTORIAL)["enclosure"][0]


@pytest.fixture
def prizes(runtime):
    return runtime.masters.lookup("gacha_prize_data", "id", "Tutorial")["prizes"]


def official(endpoint):
    waiting, samples = deque(), []
    for line in CAPTURE.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["kind"] == "req" and record["data"]["proto"] != 1:
            path = urlsplit(record["data"]["url"]).path.removeprefix("/api/")
            payload = record["data"]["payload"]
            waiting.append((path, json.loads(payload) if payload else None))
        elif record["kind"] == "resp-b64" and waiting:
            path, request = waiting.popleft()
            if path == endpoint:
                response = json.loads(base64.b64decode(record["data"]["b64"]))
                samples.append((request, response))
    return samples[0]


def order(gacha_id=TUTORIAL, count=1):
    return {"gacha_id": gacha_id, "exec_num": count, "interruption": False}


def wallet(state, name, category="point"):
    return next(
        row
        for row in state["wallets"]
        if row["category"] == category and row["name"] == name
    )


def fund(runtime, state, rid, amount):
    runtime.resources.grant(state, [{"rid": rid, "val": amount}], NOW)


def synced(result):
    return {key: rows[0]["data"] for key, rows in result.sync.items()}


def rejection(runtime, state, payload, now=NOW):
    before = deepcopy(state)
    with pytest.raises(ApiRejected) as caught:
        runtime.gacha.draw(state, payload, now)
    assert state == before
    return caught.value.reason


def pay_gold(pool, prizes):
    pool.update(execlimit_max_times=10, max_batch=5)
    prizes[0].update(prize_type="point", prize_name="gold", prize_num=100)


def card(number, card_id, **flags):
    return {
        "table_id": "local",
        "no": number,
        "prize_num": 1,
        "prize_type": "unit",
        "prize_name": str(card_id),
        "pickup": 0,
        "status": 0,
        "is_select": 0,
        **flags,
    }


def table(runtime, identity, *rows):
    runtime.masters.table("gacha_prize_data").append(
        {"id": identity, "prizes": list(rows)}
    )


def bonus(runtime, source, identity, *rows):
    prizes = [
        {
            "group_id": identity,
            "no": number,
            "prize_num": amount,
            "prize_type": kind,
            "prize_name": name,
            "status": 0,
        }
        for number, (kind, name, amount) in enumerate(rows, 1)
    ]
    runtime.masters.table(source).append({"id": identity, "prizes": prizes})


def forge(runtime, identity=LOCAL, **changes):
    group = deepcopy(runtime.masters.lookup("gacha_data", "id", TUTORIAL))
    pool = group["enclosure"][0]
    pool.update(
        id=identity,
        execlimit_counter_name=identity,
        start_at="",
        end_at="",
        cost_num=0,
        execlimit_max_times=999999,
        prize_table_id="local",
    )
    pool.update(changes)
    group["id"] = identity
    runtime.masters.table("gacha_data").append(group)
    return pool


def legend(runtime):
    table = runtime.masters.lookup("gacha_prize_data", "id", "limited_legendget10")
    return int(table["prizes"][0]["prize_name"])


def roll(runtime, *points):
    runtime.gacha.lottery.rng = Dice(*points)


def cards(result):
    return [
        int(row["prize"]["rid"].removeprefix("unit.card."))
        for row in result.data["main_prizes"]
    ]


def rates(runtime, state, gacha_id=LOCAL, rarity=True, alt=False, now=NOW):
    payload = {
        "gacha_id": gacha_id,
        "needs_rarity_table": rarity,
        "needs_alt_table": alt,
    }
    return runtime.gacha.probabilities(state, payload, now)


def test_tutorial_draw_matches_the_official_response(runtime, state):
    result = runtime.gacha.draw(state, order(), NOW)
    assert result.data == {
        "main_prizes": [{"no": 1, "prize": UNIT_PRIZE}],
        "extra_prizes": None,
        "rnd_extra_prizes": None,
        "actual_exec_num": 1,
        "stamp_prizes": None,
    }
    sync = synced(result)
    assert set(sync) == {"wallet", "limit", "unit", "player"}
    assert [(row["name"], row["val"]) for row in sync["wallet"]] == [("free_coin", 0)]
    assert sync["limit"] == [{"id": TUTORIAL, "times": 1, "counted_at": NOW}]
    assert [(row["instance_id"], row["card_id"]) for row in sync["unit"]] == [(3, 23)]
    assert sync["player"] == [state["player"]]
    assert state["player"]["tutorial"] == 600
    assert state["inbox"] == []


@pytest.mark.parametrize("progress", [0, 350, 600, 9999])
def test_draw_outside_the_gacha_step_keeps_the_progress(runtime, state, progress):
    state["player"]["tutorial"] = progress
    result = runtime.gacha.draw(state, order(), NOW)
    assert "player" not in result.sync
    assert state["player"]["tutorial"] == progress


def test_other_pools_leave_the_tutorial_alone(runtime, state, monkeypatch):
    monkeypatch.setattr(runtime.tutorial, "gacha", lambda: "gacha.normal.other")
    result = runtime.gacha.draw(state, order(), NOW)
    assert "player" not in result.sync
    assert state["player"]["tutorial"] == 500


def test_draw_is_recorded_for_later_consumers(runtime, state):
    runtime.gacha.draw(state, order(), NOW)
    event = state["events"][-1]
    uuid.UUID(event["id"].removeprefix("gachaexec-"))
    assert event == {
        "id": event["id"],
        "type": "gacha_draw",
        "at": NOW,
        "data": {
            "gacha_id": TUTORIAL,
            "draws": 1,
            "costs": [{"rid": "wallet.point.free_coin", "val": 300}],
            "rewards": [{"rid": "unit.card.23", "val": 1}],
        },
    }


@pytest.mark.parametrize("identity", ["gacha.normal.missing", "", 7, None, ["x"]])
def test_unknown_pools_are_not_found(runtime, state, identity):
    before = deepcopy(state)
    with pytest.raises(ApiNotFound) as caught:
        runtime.gacha.draw(state, order(identity), NOW)
    assert caught.value.resources == [str(identity)]
    assert state == before


def test_listing_matches_the_official_sample(runtime):
    _, response = official("gacha/list")
    listed = runtime.gacha.listed(response["timesync"])
    catalog = runtime.gacha.catalog
    official_ids = response["data"]["gacha_ids"]
    # The official server kept one test pool whose prize table the client's master data lacks.
    unstocked = [pool for pool in official_ids if not catalog.stocked(catalog.pool(pool))]
    assert unstocked == ["gacha_ticket.normal.ticket_Default230806test_youkai"]
    assert listed == [pool for pool in official_ids if pool not in unstocked]
    assert len(listed) == 598


def test_listing_keeps_upcoming_pools_and_drops_finished_ones(runtime, pool):
    pool.update(start_at="2026/11/11 05:00", end_at="2026/11/12 05:00")
    assert TUTORIAL in runtime.gacha.listed(NOW)
    assert TUTORIAL in runtime.gacha.listed(RESET + 86399)
    assert TUTORIAL not in runtime.gacha.listed(RESET + 86400)
    assert "pt_event.box.Box20260914" not in runtime.gacha.listed(NOW)


def test_hidden_pools_are_neither_listed_nor_drawn(runtime, state):
    runtime.config.read()["gacha"]["hidden_ids"] = [TUTORIAL]
    assert TUTORIAL not in runtime.gacha.listed(NOW)
    assert rejection(runtime, state, order()) == "OUT_OF_SCHEDULE"


@pytest.mark.parametrize(
    ("start", "end", "now", "opened"),
    [
        ("2026/11/11 05:00", "", RESET - 1, False),
        ("2026/11/11 05:00", "", RESET, True),
        ("", "2026/11/11 5:00:00", RESET - 1, True),
        ("", "2026/11/11 5:00:00", RESET, False),
        ("1022/10/24 12:00", "2026/11/11", NOW, False),
        ("2026/11/10", "2026/11/12", NOW, True),
    ],
)
def test_master_schedule_bounds_the_pool(runtime, state, pool, start, end, now, opened):
    pool.update(start_at=start, end_at=end)
    if opened:
        assert runtime.gacha.draw(state, order(), now).data["actual_exec_num"] == 1
    else:
        assert rejection(runtime, state, order(), now) == "OUT_OF_SCHEDULE"


def test_period_pools_need_an_open_window(runtime, state, pool):
    pool["period_name"] = "local"
    assert rejection(runtime, state, order()) == "OUT_OF_SCHEDULE"
    window = {"id": "gacha.local", "period": {"start_at": NOW, "end_at": NOW + 60}}
    state["periods"].append({**window, "counted_at": NOW})
    assert rejection(runtime, state, order(), NOW - 1) == "OUT_OF_SCHEDULE"
    assert rejection(runtime, state, order(), NOW + 60) == "OUT_OF_SCHEDULE"
    assert runtime.gacha.draw(state, order(), NOW + 59).data["actual_exec_num"] == 1


def test_configured_windows_open_period_pools_for_everyone(runtime, state, pool):
    pool["period_name"] = "local"
    window = {"id": "gacha.local", "period": {"start_at": NOW, "end_at": NOW + 60}}
    runtime.config.read()["periods"] = [{**window, "counted_at": NOW}]
    assert runtime.gacha.draw(state, order(), NOW).data["actual_exec_num"] == 1


def test_batch_size_is_bounded_by_the_pool(runtime, state):
    assert rejection(runtime, state, order(count=2)) == "TOO_LARGE_NUM_OF_TRIALS"


@pytest.mark.parametrize("count", [0, -1, True, "1", 1.0, None])
def test_batch_size_must_be_a_positive_integer(runtime, state, count):
    before = deepcopy(state)
    with pytest.raises(ValueError, match="exec_num"):
        runtime.gacha.draw(state, order(count=count), NOW)
    assert state == before


def test_exhausted_limit_rejects_the_draw(runtime, state):
    state["limits"].append({"id": TUTORIAL, "times": 1, "counted_at": NOW - 1})
    assert rejection(runtime, state, order()) == "LIMIT_OVER_OF_GACHA_TRIES"


def test_pools_without_a_limit_never_run_out(runtime, state, pool, prizes):
    pay_gold(pool, prizes)
    pool.update(execlimit_max_times=0, cost_num=0)
    for elapsed in range(3):
        runtime.gacha.draw(state, order(count=5), NOW + elapsed)
    assert state["limits"] == [{"id": TUTORIAL, "times": 15, "counted_at": NOW + 2}]


def test_missing_currency_rejects_the_draw(runtime, state):
    wallet(state, "free_coin")["val"] = 299
    assert rejection(runtime, state, order()) == "NOT_ENOUGH_COST"


def test_batches_multiply_cost_draws_and_limit_use(runtime, state, pool, prizes):
    pay_gold(pool, prizes)
    pool["draw"] = 2
    wallet(state, "free_coin")["val"] = 1000
    result = runtime.gacha.draw(state, order(count=3), NOW)
    gold = {"rid": "wallet.point.gold", "val": 100, "inc": 100, "inbox": 0, "over": 0}
    assert result.data["main_prizes"] == [
        {"no": number, "prize": gold} for number in range(1, 7)
    ]
    assert result.data["actual_exec_num"] == 3
    assert wallet(state, "free_coin")["val"] == 100
    assert wallet(state, "gold")["val"] == 1100
    assert state["limits"] == [{"id": TUTORIAL, "times": 3, "counted_at": NOW}]
    assert state["events"][-1]["data"]["draws"] == 6


def test_limit_counts_batches_until_it_is_full(runtime, state, pool, prizes):
    pay_gold(pool, prizes)
    pool["execlimit_max_times"] = 4
    wallet(state, "free_coin")["val"] = 9000
    runtime.gacha.draw(state, order(count=3), NOW)
    assert rejection(runtime, state, order(count=2)) == "LIMIT_OVER_OF_GACHA_TRIES"
    runtime.gacha.draw(state, order(count=1), NOW + 1)
    assert state["limits"] == [{"id": TUTORIAL, "times": 4, "counted_at": NOW + 1}]


def test_daily_limit_reopens_after_the_reset(runtime, state, pool, prizes):
    pay_gold(pool, prizes)
    pool.update(execlimit_max_times=1, execlimit_cycle_mode="daily")
    wallet(state, "free_coin")["val"] = 600
    runtime.gacha.draw(state, order(), NOW)
    assert rejection(runtime, state, order(), RESET - 1) == "LIMIT_OVER_OF_GACHA_TRIES"
    runtime.gacha.draw(state, order(), RESET)
    assert state["limits"] == [{"id": TUTORIAL, "times": 1, "counted_at": RESET}]
    assert wallet(state, "gold")["val"] == 700


def test_free_pools_spend_nothing(runtime, state, pool):
    pool["cost_num"] = 0
    result = runtime.gacha.draw(state, order(), NOW)
    assert "wallet" not in result.sync
    assert wallet(state, "free_coin")["val"] == 300
    assert state["events"][-1]["data"]["costs"] == []


def test_ticket_pools_spend_their_ticket(runtime, state):
    table(runtime, "local", card(1, EPIC))
    forge(runtime, cost_type="ticket", cost_name="gacha_common261109l", cost_num=1)
    assert rejection(runtime, state, order(LOCAL)) == "NOT_ENOUGH_COST"
    fund(runtime, state, "wallet.ticket.gacha_common261109l", 2)
    runtime.gacha.draw(state, order(LOCAL), NOW)
    assert wallet(state, "gacha_common261109l", "ticket")["val"] == 1


def test_full_roster_sends_the_unit_to_the_inbox(runtime, state):
    runtime.config.read()["resources"]["unit_capacity"] = 2
    result = runtime.gacha.draw(state, order(), NOW)
    assert result.data["main_prizes"] == [
        {"no": 1, "prize": {**UNIT_PRIZE, "inc": 0, "inbox": 1}}
    ]
    assert "unit" not in result.sync
    assert [row["card_id"] for row in state["units"]] == [11, 22]
    assert [(row["no"], row["reward"], row["tags"]) for row in state["inbox"]] == [
        (1, {"rid": "unit.card.23", "val": 1}, [])
    ]


@pytest.mark.parametrize(
    ("point", "drawn"), [(0.0, COMMON), (0.56, COMMON), (0.57, RARE), (0.9, EPIC)]
)
def test_prizes_are_drawn_by_rarity_rates(runtime, state, point, drawn):
    table(runtime, "local", card(1, COMMON), card(2, RARE), card(3, EPIC))
    forge(runtime)
    roll(runtime, point)
    assert cards(runtime.gacha.draw(state, order(LOCAL), NOW)) == [drawn]


def test_every_draw_of_a_batch_is_rolled_on_its_own(runtime, state):
    table(runtime, "local", card(1, COMMON), card(2, RARE), card(3, EPIC))
    forge(runtime, draw=3, max_batch=2)
    roll(runtime, 0.9, 0.0, 0.6, 0.6, 0.9, 0.0)
    result = runtime.gacha.draw(state, order(LOCAL, 2), NOW)
    assert cards(result) == [EPIC, COMMON, RARE, RARE, EPIC, COMMON]
    assert [row["no"] for row in result.data["main_prizes"]] == [1, 2, 3, 4, 5, 6]
    assert [row["card_id"] for row in state["units"]] == [11, 22, EPIC, COMMON, RARE]


def test_closing_draws_use_the_guaranteed_table(runtime, state):
    table(runtime, "local", card(1, COMMON))
    table(runtime, "local_closing", card(1, EPIC), card(2, legend(runtime)))
    forge(runtime, draw=3, max_batch=2, alt_draw=1, alt_prize_table_id="local_closing")
    roll(runtime, 0.5, 0.5, 0.0, 0.5, 0.5, 0.99)
    result = runtime.gacha.draw(state, order(LOCAL, 2), NOW)
    assert cards(result) == [COMMON, COMMON, EPIC, COMMON, COMMON, legend(runtime)]


def test_guaranteed_table_waits_for_its_draw_count(runtime, state):
    table(runtime, "local", card(1, COMMON))
    table(runtime, "local_closing", card(1, EPIC))
    forge(runtime, draw=2, alt_draw=0, alt_prize_table_id="local_closing")
    roll(runtime, 0.5, 0.5)
    assert cards(runtime.gacha.draw(state, order(LOCAL), NOW)) == [COMMON, COMMON]


def test_pools_without_a_prize_table_are_closed(runtime, state):
    forge(runtime, prize_table_id="absent")
    before = deepcopy(state)
    assert LOCAL not in runtime.gacha.listed(NOW)
    with pytest.raises(ApiRejected, match="OUT_OF_SCHEDULE"):
        runtime.gacha.draw(state, order(LOCAL), NOW)
    assert state == before
    with pytest.raises(ValueError, match="Unknown gacha prize table absent"):
        runtime.gacha.lottery.table("absent")


def test_fixed_extras_are_paid_once_per_batch(runtime, state):
    table(runtime, "local", card(1, COMMON))
    bonus(
        runtime,
        "gacha_extra_prize_data",
        "local_extra",
        ("gachamedal", "gmedal_common", 10),
        ("point", "gold", 50),
        ("gachamedal", "gmedal_common", 5),
    )
    forge(runtime, max_batch=3, ex_prize_group_id="local_extra")
    roll(runtime, 0.5, 0.5, 0.5)
    result = runtime.gacha.draw(state, order(LOCAL, 3), NOW)
    assert result.data["extra_prizes"] == [
        {"rid": MEDAL, "val": 45, "inc": 45, "inbox": 0, "over": 0},
        {"rid": "wallet.point.gold", "val": 150, "inc": 150, "inbox": 0, "over": 0},
    ]
    assert result.data["rnd_extra_prizes"] is None
    assert wallet(state, "gmedal_common", "gachamedal")["val"] == 45


def test_random_extras_follow_paid_batches(runtime, state):
    table(runtime, "local", card(1, COMMON))
    bonus(
        runtime,
        "gacha_rnd_extra_prize_data",
        "local_random",
        ("point", "gold", 100),
        ("point", "gold", 700),
    )
    pool = forge(runtime, max_batch=2, cost_num=1, rnd_ex_prize_group_id="local_random")
    runtime.gacha.rng = SimpleNamespace(choice=lambda rows: rows[-1])
    roll(runtime, 0.5, 0.5, 0.5)
    result = runtime.gacha.draw(state, order(LOCAL, 2), NOW)
    assert result.data["rnd_extra_prizes"] == [
        {"rid": "wallet.point.gold", "val": 1400, "inc": 1400, "inbox": 0, "over": 0}
    ]
    pool["cost_num"] = 0
    assert runtime.gacha.draw(state, order(LOCAL), NOW).data["rnd_extra_prizes"] is None


def test_unknown_extra_groups_are_reported(runtime, state):
    table(runtime, "local", card(1, COMMON))
    forge(runtime, ex_prize_group_id="absent")
    with pytest.raises(ValueError, match="Unknown gacha extra prize group absent"):
        runtime.gacha.draw(state, order(LOCAL), NOW)


def test_another_copy_is_annotated_with_the_owned_unit(runtime, state):
    table(runtime, "local", card(1, 11))
    forge(runtime)
    state["units"][0]["r_unit_lv"] = 7
    roll(runtime, 0.5)
    result = runtime.gacha.draw(state, order(LOCAL), NOW)
    assert result.data["main_prizes"] == [
        {
            "no": 1,
            "prize": {"rid": "unit.card.11", "val": 1, "inc": 0, "inbox": 0, "over": 0},
            "prize_unit_ant": {"lv": 7, "is_dup": True, "dup_items": []},
        }
    ]
    assert state["units"][0]["upa"]["all_pt"] == 1
    assert [row["card_id"] for row in synced(result)["unit"]] == [11]


def test_copies_past_full_potential_report_their_medals(runtime, state):
    table(runtime, "local", card(1, 11))
    forge(runtime)
    state["units"][0]["upa"]["all_pt"] = 5
    roll(runtime, 0.5)
    result = runtime.gacha.draw(state, order(LOCAL), NOW)
    medals = {"rid": MEDAL, "val": 5, "inc": 5, "inbox": 0, "over": 0}
    assert result.data["main_prizes"] == [
        {
            "no": 1,
            "prize": {"rid": "unit.card.11", "val": 1, "inc": 0, "inbox": 0, "over": 1},
            "prize_unit_ant": {"lv": 1, "is_dup": True, "dup_items": [medals]},
        }
    ]
    assert wallet(state, "gmedal_common", "gachamedal")["val"] == 5
    assert set(synced(result)) == {"wallet", "limit"}


def test_second_copy_in_one_batch_counts_as_a_duplicate(runtime, state):
    table(runtime, "local", card(1, EPIC))
    forge(runtime, draw=2)
    roll(runtime, 0.5, 0.5)
    result = runtime.gacha.draw(state, order(LOCAL), NOW)
    first, second = result.data["main_prizes"]
    assert first == {"no": 1, "prize": UNIT_PRIZE}
    assert second["prize_unit_ant"] == {"lv": 1, "is_dup": True, "dup_items": []}
    assert state["units"][-1]["upa"]["all_pt"] == 1


def test_rates_list_every_prize_and_rarity(runtime, state):
    table(
        runtime,
        "local",
        card(1, COMMON),
        card(2, legend(runtime), pickup=1),
        card(3, legend(runtime)),
        {**card(4, 0), "prize_type": "point", "prize_name": "gold", "prize_num": 500},
    )
    forge(runtime)
    report = rates(runtime, state)
    assert report == {
        "prizes": [
            {"no": 1, "rid": f"unit.card.{COMMON}", "val": 1, "p": "80.882%"},
            {"no": 2, "rid": f"unit.card.{legend(runtime)}", "val": 1, "p": "4.304%"},
            {"no": 3, "rid": f"unit.card.{legend(runtime)}", "val": 1, "p": "0.108%"},
            {"no": 4, "rid": "wallet.point.gold", "val": 500, "p": "14.706%"},
        ],
        "rarities": [{"rid": "R", "p": "80.882%"}, {"rid": "SSR", "p": "4.412%"}],
    }
    assert rates(runtime, state, rarity=False)["rarities"] == []


def test_rates_can_show_the_guaranteed_table(runtime, state, pool):
    table(runtime, "local", card(1, COMMON))
    table(runtime, "local_closing", card(1, EPIC))
    forge(runtime, alt_prize_table_id="local_closing")
    assert rates(runtime, state, alt=True)["rarities"] == [
        {"rid": "SR", "p": "100.000%"}
    ]
    assert rates(runtime, state)["rarities"] == [{"rid": "R", "p": "100.000%"}]
    tutorial = rates(runtime, state, TUTORIAL, alt=True)
    assert tutorial["prizes"] == [
        {"no": 1, "rid": "unit.card.23", "val": 1, "p": "100.000%"}
    ]


def test_rates_of_live_pools_add_up(runtime, state):
    for identity in ("gacha.normal.vivre_PL", "gacha.normal.vivre_PR"):
        for alt in (False, True):
            report = rates(runtime, state, identity, alt=alt)
            total = sum(float(row["p"].rstrip("%")) for row in report["prizes"])
            assert total == pytest.approx(100, abs=0.2)
            by_rarity = sum(float(row["p"].rstrip("%")) for row in report["rarities"])
            assert by_rarity == pytest.approx(100, abs=0.01)


def test_rates_of_unknown_pools_are_not_found(runtime, state):
    with pytest.raises(ApiNotFound):
        rates(runtime, state, "gacha.normal.missing")


def test_history_keeps_the_latest_results_first(runtime, state, pool, prizes):
    pay_gold(pool, prizes)
    pool["cost_num"] = 0
    runtime.config.read()["gacha"]["history"] = 2
    results = [
        runtime.gacha.draw(state, order(count=count), NOW + count).data
        for count in (1, 2, 3)
    ]
    assert runtime.gacha.history(state) == [
        {"log_at": NOW + 3, "gacha_id": TUTORIAL, "result": results[2]},
        {"log_at": NOW + 2, "gacha_id": TUTORIAL, "result": results[1]},
    ]


def test_history_is_a_copy_of_the_response(runtime, state):
    result = runtime.gacha.draw(state, order(), NOW)
    result.data["main_prizes"].clear()
    assert len(state["gacha_history"][0]["result"]["main_prizes"]) == 1


async def test_native_draw_follows_the_recorded_exchange(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        assert "gacha/draw" in runtime.registry.handlers
        async with runtime.store.transaction(profile) as state:
            state["player"]["tutorial"] = 500
        drawn = await client.call("gacha/draw", order())
        assert drawn["data"]["main_prizes"] == [{"no": 1, "prize": UNIT_PRIZE}]
        assert set(drawn["sync"]) == {"wallet", "limit", "unit", "player"}
        assert drawn["sync"]["player"][0]["data"][0]["tutorial"] == 600
        saved = await runtime.store.read(profile)
        assert [row["card_id"] for row in saved["units"]] == [11, 22, 23]
        limits = await client.call(
            "limit/list/seq_start", {"range_names": ["gacha"], "per_view": 100}
        )
        assert limits["data"]["items"] == [
            {"id": TUTORIAL, "times": 1, "counted_at": NOW}
        ]
        history = await client.call("gacha/history", {"_z0": 0})
        assert history["data"]["history"] == [
            {"log_at": NOW, "gacha_id": TUTORIAL, "result": drawn["data"]}
        ]
    finally:
        await http.close()


async def test_native_rejections_keep_the_save(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        await client.call("gacha/draw", order())
        before = await runtime.store.read(profile)
        again = await post(http, client, "gacha/draw", order())
        assert again.status == 422
        assert await again.json() == {
            "status": {"code": 90000006, "message": "LIMIT_OVER_OF_GACHA_TRIES"},
            "data": {"rejected_reason": "LIMIT_OVER_OF_GACHA_TRIES"},
        }
        missing = await post(http, client, "gacha/draw", order("gacha.normal.none"))
        assert missing.status == 404
        assert missing.headers["x-ag-protocol"] == "1"
        assert await missing.json() == {
            "status": {
                "code": 90000004,
                "message": "Unknown resources: gacha.normal.none",
            },
            "data": {"resources": ["gacha.normal.none"]},
        }
        assert await runtime.store.read(profile) == before
        await client.call("unit/all", {"_z0": 0})
    finally:
        await http.close()


async def test_native_catalog_follows_the_official_samples(tmp_path):
    http, client, runtime, _ = await connect(tmp_path)
    try:
        listed = await client.call("gacha/list", {"_z0": 0})
        assert listed["data"]["gacha_ids"] == runtime.gacha.listed(NOW)
        _, sample = official("gacha/select/get")
        chosen = await client.call("gacha/select/get", {"_z0": 0})
        assert chosen["data"] == sample["data"]
        request, sample = official("gacha/stamp_record/list")
        stamps = await client.call("gacha/stamp_record/list", request)
        assert stamps["data"] == sample["data"]
        report = await client.call(
            "gacha/probabilities",
            {
                "gacha_id": TUTORIAL,
                "needs_rarity_table": True,
                "needs_alt_table": False,
            },
        )
        assert report["data"] == {
            "prizes": [{"no": 1, "rid": "unit.card.23", "val": 1, "p": "100.000%"}],
            "rarities": [{"rid": "SR", "p": "100.000%"}],
        }
    finally:
        await http.close()


@pytest.mark.parametrize(
    ("endpoint", "model"),
    [
        ("gacha/list", "GetGachaListResponse200Data"),
        ("gacha/select/get", "GetSelectGachaResponse200Data"),
        ("gacha/stamp_record/list", "GetGachaStampRecordListResponse200Data"),
        ("gacha/probabilities", "GetGachaProbabilitiesResponse200Data"),
        ("gacha/history", "GetGachaHistoryResponse200Data"),
        ("gacha/draw", "DrawGachaResponse200Data"),
    ],
)
def test_response_field_names_match_the_client_models(runtime, state, endpoint, model):
    payloads = {
        "gacha/stamp_record/list": {"stamp_ids": [91]},
        "gacha/probabilities": {
            "gacha_id": TUTORIAL,
            "needs_rarity_table": True,
            "needs_alt_table": False,
        },
        "gacha/draw": order(),
    }
    context = Context(runtime, payloads.get(endpoint, {"_z0": 0}), state, None, NOW)
    fields = {field["name"] for field in runtime.models[model]["fields"]}
    assert set(ROUTES[endpoint](context).data) == fields
