from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.context import Context
from monmusu_ps.core.initial import create_player
from monmusu_ps.features.inbox import ROUTES
from monmusu_ps.runtime import Runtime
from test_battle import connect


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_332_574
DAY = 86400
MAIL_EXPIRY = 1_797_001_199
SKIP = "wallet.ticket.quest_skip"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: NOW, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    settings["comeback"]["with_login_bonus"] = False
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def state(runtime):
    return create_player(
        "inbox", "INBOX001", runtime.masters, runtime.config.read(), NOW
    )


@pytest.fixture
def context(runtime, state):
    return Context(runtime, {}, state, runtime.sessions.create("inbox-test"), NOW)


def call(ctx, endpoint, **payload):
    ctx.payload = payload
    return ROUTES[endpoint](ctx)


def mail(runtime, state, rid=SKIP, amount=10, now=NOW, message=None):
    reward = {"rid": rid, "val": amount}
    return runtime.mailbox.post(state, reward, "test.source", now, message)


def delivered(rid, amount):
    return {"rid": rid, "val": amount, "inc": amount, "inbox": 0, "over": 0}


def balance(runtime, state, rid=SKIP):
    return runtime.resources.balance(state, rid)


def single(number):
    return {"inbox_name": "main", "message_no": number}


def several(*numbers):
    return {"inbox_name": "main", "message_no_list": list(numbers)}


def listing(order=2):
    return {"inbox_name": "main", "per_view": 100, "order": order}


def archived(order=2, kind=0, box="history"):
    return {"inbox_name": box, "log_filter": kind, "per_view": 100, "order": order}


def refused(number, reason, box="main"):
    return {"inbox": box, "no": number, "reason": reason}


def test_opening_a_letter_matches_the_official_sample(runtime, state):
    letter = mail(runtime, state)
    result = runtime.inbox.open(state, single(letter["no"]), NOW + 60)
    assert result.data == {"success": {"no": 1, "reward": delivered(SKIP, 10)}}
    assert set(result.sync) == {"wallet"}
    assert [
        (row["category"], row["name"], row["val"])
        for row in result.sync["wallet"][0]["data"]
    ] == [("ticket", "quest_skip", 10)]
    assert state["inbox"] == []


def test_opened_letters_move_to_the_history(runtime, state):
    letter = deepcopy(mail(runtime, state, message="補填"))
    runtime.inbox.open(state, single(1), NOW + 60)
    assert state["inbox_archive"] == [
        {
            "log_type": 1,
            "item": letter,
            "log_at": NOW + 60,
            "expire_at": MAIL_EXPIRY,
        }
    ]


def test_opening_several_letters_matches_the_official_shape(runtime, state):
    mail(runtime, state, "wallet.point.gold", 20000)
    mail(runtime, state, "wallet.point.free_coin", 500)
    mail(runtime, state)
    result = runtime.inbox.open_many(state, several(3, 2, 1), NOW)
    assert result.data == {
        "successes": [
            {"no": 3, "reward": delivered(SKIP, 10)},
            {"no": 2, "reward": delivered("wallet.point.free_coin", 500)},
            {"no": 1, "reward": delivered("wallet.point.gold", 20000)},
        ],
        "rejected_reasons": [],
    }
    assert set(result.sync) == {"wallet"}
    assert state["inbox"] == []
    assert [row["item"]["no"] for row in state["inbox_archive"]] == [3, 2, 1]
    assert balance(runtime, state, "wallet.point.gold") == 20500


def test_an_empty_bulk_request_changes_nothing(runtime, state):
    mail(runtime, state)
    before = deepcopy(state)
    result = runtime.inbox.open_many(state, several(), NOW)
    assert result.data == {"successes": [], "rejected_reasons": []}
    assert result.sync is None
    assert state == before


@pytest.mark.parametrize("number", [2, 0, -1, True, "1", None])
def test_unknown_letters_are_refused_in_the_response(runtime, state, number):
    mail(runtime, state)
    before = deepcopy(state)
    result = runtime.inbox.open(state, single(number), NOW)
    assert result.data == {"rejected_reason": refused(number, "NOT_FOUND_MESSAGE")}
    assert result.sync is None
    assert state == before


def test_letters_of_another_box_are_not_found(runtime, state):
    mail(runtime, state)
    payload = {"inbox_name": "other", "message_no": 1}
    result = runtime.inbox.open(state, payload, NOW)
    assert result.data == {
        "rejected_reason": refused(1, "NOT_FOUND_MESSAGE", box="other")
    }
    assert len(state["inbox"]) == 1


def test_rewards_over_the_holding_cap_stay_in_the_box(runtime, state):
    mail(runtime, state, "wallet.point.gold", 99_999_999)
    mail(runtime, state)
    before = deepcopy(state["inbox"])
    result = runtime.inbox.open_many(state, several(2, 1, 2), NOW)
    assert result.data == {
        "successes": [{"no": 2, "reward": delivered(SKIP, 10)}],
        "rejected_reasons": [
            refused(1, "ASSET_CAPACITY_OVERFLOW"),
            refused(2, "NOT_FOUND_MESSAGE"),
        ],
    }
    assert state["inbox"] == before[:1]
    assert balance(runtime, state, "wallet.point.gold") == 500
    alone = runtime.inbox.open(state, single(1), NOW)
    assert alone.data == {"rejected_reason": refused(1, "ASSET_CAPACITY_OVERFLOW")}
    assert alone.sync is None


def test_mailed_units_join_the_roster(runtime, state):
    mail(runtime, state, "unit.card.6", 1)
    result = runtime.inbox.open(state, single(1), NOW)
    assert result.data == {"success": {"no": 1, "reward": delivered("unit.card.6", 1)}}
    assert set(result.sync) == {"unit"}
    assert [row["card_id"] for row in state["units"]] == [11, 22, 6]


def test_a_full_roster_keeps_the_mailed_unit(runtime, state):
    runtime.config.read()["resources"]["unit_capacity"] = 2
    mail(runtime, state, "unit.card.6", 1)
    result = runtime.inbox.open(state, single(1), NOW)
    assert result.data == {"rejected_reason": refused(1, "ASSET_CAPACITY_OVERFLOW")}
    assert len(state["inbox"]) == 1


def test_letters_can_be_opened_through_their_last_second(runtime, state):
    mail(runtime, state)
    result = runtime.inbox.open(state, single(1), MAIL_EXPIRY)
    assert result.data["success"]["no"] == 1


def test_expired_letters_leave_the_box_for_the_history(runtime, state):
    old = deepcopy(mail(runtime, state))
    fresh = mail(runtime, state, now=NOW + DAY)
    result = runtime.inbox.open(state, single(1), MAIL_EXPIRY + 1)
    assert result.data == {"rejected_reason": refused(1, "NOT_FOUND_MESSAGE")}
    assert balance(runtime, state) == 0
    assert state["inbox"] == [fresh]
    assert state["inbox_archive"] == [
        {
            "log_type": 2,
            "item": old,
            "log_at": MAIL_EXPIRY,
            "expire_at": runtime.mailbox.deadline(MAIL_EXPIRY, 30 * DAY),
        }
    ]


@pytest.mark.parametrize(("order", "expected"), [(1, [1, 2, 4]), (2, [4, 2, 1])])
def test_list_filters_box_and_expiry_then_orders_by_number(
    context, runtime, order, expected
):
    state = context.state
    mail(runtime, state)
    mail(runtime, state)
    mail(runtime, state, now=NOW - 40 * DAY)
    mail(runtime, state)
    state["inbox"].append({**state["inbox"][0], "inbox": "other", "no": 9})
    state["inbox"][1]["expire_at"] = 0
    state["inbox"].reverse()
    first = call(
        context, "inbox/list/seq_start", inbox_name="main", per_view=2, order=order
    ).data
    state["inbox"].clear()
    second = call(
        context, "inbox/list/seq_continue", sequence_token=first["sequence_token"]
    ).data
    assert [row["no"] for row in first["items"] + second["items"]] == expected
    assert second["sequence_token"] is None


@pytest.mark.parametrize("order", [0, 3, None, "2"])
def test_unknown_orders_are_rejected(runtime, state, order):
    with pytest.raises(ValueError, match="order must be 1 or 2"):
        runtime.inbox.letters(state, listing(order), NOW)
    with pytest.raises(ValueError, match="order must be 1 or 2"):
        runtime.inbox.history(state, archived(order), NOW)


def test_letters_without_a_deadline_never_expire(runtime, state):
    for deadline in (0, -1, 253_402_300_799):
        mail(runtime, state)["expire_at"] = deadline
    rows = runtime.inbox.letters(state, listing(1), NOW + 10_000 * DAY)
    assert [row["no"] for row in rows] == [1, 2, 3]
    assert state["inbox_archive"] == []


def test_history_lists_the_newest_log_first_and_filters_by_type(runtime, state):
    mail(runtime, state, now=NOW - 40 * DAY)
    mail(runtime, state)
    mail(runtime, state)
    runtime.inbox.open(state, single(2), NOW)
    runtime.inbox.open(state, single(3), NOW + 60)

    def logs(**query):
        rows = runtime.inbox.history(state, archived(**query), NOW + 60)
        return [(row["log_type"], row["item"]["no"]) for row in rows]

    assert logs() == [(1, 3), (1, 2), (2, 1)]
    assert logs(order=1) == [(2, 1), (1, 2), (1, 3)]
    assert logs(kind=1) == [(1, 3), (1, 2)]
    assert logs(kind=2) == [(2, 1)]
    assert logs(kind=3) == []
    assert logs(box="main") == []


def test_history_forgets_logs_past_their_own_deadline(runtime, state):
    mail(runtime, state)
    runtime.inbox.open(state, single(1), NOW)
    assert len(runtime.inbox.history(state, archived(), MAIL_EXPIRY)) == 1
    assert runtime.inbox.history(state, archived(), MAIL_EXPIRY + 1) == []
    assert state["inbox_archive"] == []


def test_history_keeps_only_the_newest_logs_within_its_capacity(runtime, state):
    runtime.config.read()["mail"]["archive"]["capacity"] = 2
    for _ in range(3):
        mail(runtime, state)
    runtime.inbox.open_many(state, several(1, 2, 3), NOW)
    runtime.inbox.sweep(state, NOW)
    assert [row["item"]["no"] for row in state["inbox_archive"]] == [2, 3]
    runtime.config.read()["mail"]["archive"]["capacity"] = 0
    runtime.inbox.sweep(state, NOW)
    assert state["inbox_archive"] == []


def test_login_bonus_letters_can_be_collected_in_bulk(runtime, state):
    state["player"]["tutorial"] = 900
    runtime.login_bonus.stamp(state, NOW)
    numbers = [row["no"] for row in runtime.inbox.letters(state, listing(), NOW)]
    assert numbers == list(range(15, 0, -1))
    result = runtime.inbox.open_many(state, several(*numbers), NOW)
    assert [row["no"] for row in result.data["successes"]] == numbers
    assert result.data["rejected_reasons"] == []
    assert state["inbox"] == []
    assert balance(runtime, state, "wallet.point.free_coin") == 10_300
    assert balance(runtime, state) == 40


def test_debug_post_is_off_until_enabled(runtime):
    with pytest.raises(PermissionError, match="disabled"):
        runtime.inbox.authorize_debug()
    runtime.config.read()["mail"]["debug_post"] = True
    runtime.inbox.authorize_debug()


def test_debug_post_mails_the_reward_with_its_message(runtime, state):
    payload = {
        "player_code": "INBOX001",
        "inbox_name": "main",
        "message": "テスト配布",
        "reward": {"rid": "wallet.point.free_coin", "val": 300},
    }
    row = runtime.inbox.post(state, payload, NOW)
    assert state["inbox"] == [row]
    assert row == {
        "inbox": "main",
        "no": 1,
        "message": "テスト配布",
        "reward": {"rid": "wallet.point.free_coin", "val": 300},
        "tags": ["debug.post", "vcr", "wallet"],
        "receive_at": NOW,
        "expire_at": MAIL_EXPIRY,
    }
    assert runtime.inbox.post(state, {**payload, "message": ""}, NOW)["message"] is None
    assert runtime.inbox.post(state, {**payload, "message": None}, NOW)["no"] == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"inbox_name": "other"},
        {"message": 5},
        {"reward": None},
        {"reward": {"rid": "wallet.point.missing", "val": 1}},
        {"reward": {"rid": "wallet.point.gold", "val": 0}},
        {"reward": {"rid": "wallet.point.gold", "val": -1}},
        {"reward": {"rid": "wallet.point.gold", "val": "1"}},
    ],
)
def test_debug_post_rejects_unusable_letters(runtime, state, changes):
    payload = {
        "player_code": "INBOX001",
        "inbox_name": "main",
        "message": "",
        "reward": {"rid": "wallet.point.gold", "val": 1},
        **changes,
    }
    before = deepcopy(state)
    with pytest.raises(ValueError):
        runtime.inbox.post(state, payload, NOW)
    assert state == before


@pytest.mark.parametrize(
    ("endpoint", "model", "payload"),
    [
        ("inbox/list/seq_start", "GetInboxListSeqStartResponse200Data", listing()),
        (
            "inbox_archive/list/seq_start",
            "GetInboxArchiveListSeqStartResponse200Data",
            archived(),
        ),
        (
            "inbox/message/multi_open",
            "OpenInboxMultiMessageResponse200Data",
            several(),
        ),
    ],
)
def test_response_field_names_match_the_client_models(
    context, endpoint, model, payload
):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text(encoding="utf-8"))
    fields = {field["name"] for field in schema["models"][model]["fields"]}
    assert set(call(context, endpoint, **payload).data) == fields


def test_stored_rows_match_the_client_models(runtime, state):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text(encoding="utf-8"))

    def fields(model):
        return {field["name"] for field in schema["models"][model]["fields"]}

    mail(runtime, state)
    assert set(state["inbox"][0]) == fields("MessageItem")
    opened = runtime.inbox.open(state, single(1), NOW).data["success"]
    assert set(opened) == fields("MessageOpenedResult")
    assert set(state["inbox_archive"][0]) == fields("MessageArchive")
    refusal = runtime.inbox.open(state, single(1), NOW).data["rejected_reason"]
    assert set(refusal) == fields("MessageRejectedReason")


async def test_native_inbox_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        assert set(ROUTES) <= set(runtime.registry.handlers)
        async with runtime.store.transaction(profile) as state:
            state["player"]["tutorial"] = 900
        runtime.config.read()["comeback"]["with_login_bonus"] = False
        await client.call("loginbonus/get", {"_z0": 0})
        badge = await client.call(
            "inbox/list/seq_start", {"inbox_name": "main", "per_view": 1, "order": 2}
        )
        assert [row["no"] for row in badge["data"]["items"]] == [15]
        assert badge["data"]["sequence_token"]
        listed = await client.call("inbox/list/seq_start", listing())
        assert [row["no"] for row in listed["data"]["items"]] == list(range(15, 0, -1))
        assert listed["data"]["sequence_token"] is None
        opened = await client.call("inbox/message/open", single(15))
        assert opened["data"] == {"success": {"no": 15, "reward": delivered(SKIP, 10)}}
        assert set(opened["sync"]) == {"wallet"}
        rest = await client.call("inbox/message/multi_open", several(*range(14, 0, -1)))
        assert len(rest["data"]["successes"]) == 14
        assert rest["data"]["rejected_reasons"] == []
        again = await client.call("inbox/message/open", single(15))
        assert again["data"] == {"rejected_reason": refused(15, "NOT_FOUND_MESSAGE")}
        assert again["sync"] is None
        empty = await client.call("inbox/list/seq_start", listing())
        assert empty["data"] == {"items": [], "sequence_token": None}
        history = await client.call("inbox_archive/list/seq_start", archived())
        logs = history["data"]["items"]
        assert [row["item"]["no"] for row in logs] == list(range(1, 16))
        assert {row["log_type"] for row in logs} == {1}
        saved = await runtime.store.read(profile)
        assert saved["inbox"] == []
        assert len(saved["inbox_archive"]) == 15
    finally:
        await http.close()


async def test_native_debug_post_reaches_the_named_player(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        saved = await runtime.store.read(profile)
        payload = {
            "player_code": saved["player"]["player_code"],
            "inbox_name": "main",
            "message": "テスト配布",
            "reward": {"rid": SKIP, "val": 3},
        }
        headers = {"x-ag-protocol": "1"}
        closed = await http.post(
            "/api/debug/inbox/message/post", json=payload, headers=headers
        )
        assert closed.status == 401
        assert (await runtime.store.read(profile))["inbox"] == []
        settings = deepcopy(runtime.config.read())
        settings["mail"]["debug_post"] = True
        runtime.config.read = lambda: settings
        posted = await http.post(
            "/api/debug/inbox/message/post", json=payload, headers=headers
        )
        assert posted.status == 200
        body = await posted.json()
        assert body["status"] == {"code": 1, "message": "succeeded"}
        assert "data" not in body
        stranger = await http.post(
            "/api/debug/inbox/message/post",
            json={**payload, "player_code": "NOBODY00"},
            headers=headers,
        )
        assert stranger.status == 400
        listed = await client.call("inbox/list/seq_start", listing())
        assert [
            (row["no"], row["message"], row["reward"])
            for row in listed["data"]["items"]
        ] == [(1, "テスト配布", {"rid": SKIP, "val": 3})]
    finally:
        await http.close()
