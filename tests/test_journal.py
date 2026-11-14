from types import SimpleNamespace

import pytest

from monmusu_ps.core.journal import Journal


def journal(retention=10):
    settings = {"retention": {"events": retention}}
    return Journal(SimpleNamespace(read=lambda: settings))


def test_listeners_hear_only_their_kind_in_subscription_order():
    book, heard = journal(), []
    book.subscribe("arrival", lambda state, event: heard.append(("first", event)))
    book.subscribe("arrival", lambda state, event: heard.append(("second", event)))
    book.subscribe("departure", lambda state, event: heard.append(("other", event)))
    state = {"events": []}
    book.record(state, "one", "arrival", 5, {"gate": 1})
    stored = {"id": "one", "type": "arrival", "at": 5, "data": {"gate": 1}}
    assert state["events"] == [stored]
    assert heard == [("first", stored), ("second", stored)]


def test_listeners_see_the_stored_event_and_may_change_the_state():
    book = journal()
    state = {"events": [], "visits": 0}

    def count(state, event):
        assert state["events"][-1] is event
        state["visits"] += event["data"]["guests"]

    book.subscribe("arrival", count)
    book.record(state, "one", "arrival", 5, {"guests": 2})
    book.record(state, "two", "arrival", 6, {"guests": 3})
    assert state["visits"] == 5


def test_events_recorded_by_listeners_follow_their_cause():
    book = journal()
    book.subscribe(
        "arrival",
        lambda state, event: book.record(state, "echo", "greeting", event["at"], {}),
    )
    state = {"events": []}
    book.record(state, "one", "arrival", 5, {})
    assert [event["id"] for event in state["events"]] == ["one", "echo"]


def test_listener_failures_reach_the_caller():
    book = journal()

    def refuse(state, event):
        raise ValueError("closed")

    book.subscribe("arrival", refuse)
    with pytest.raises(ValueError, match="closed"):
        book.record({"events": []}, "one", "arrival", 5, {})


def test_listeners_still_hear_events_beyond_the_retention():
    book, heard = journal(retention=1), []
    book.subscribe("arrival", lambda state, event: heard.append(event["id"]))
    state = {"events": []}
    for identity in ("one", "two", "three"):
        book.record(state, identity, "arrival", 5, {})
    assert [event["id"] for event in state["events"]] == ["three"]
    assert heard == ["one", "two", "three"]
