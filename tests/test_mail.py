from pathlib import Path

import pytest

from monmusu_ps.core.calendar import FOREVER, Calendar
from monmusu_ps.core.mail import Mailbox
from monmusu_ps.core.masterdata import MasterData


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_332_574
MONTH_END = 1_797_001_199
SOURCE = "loginbonus.stamp_reward:153:1"


class Config:
    def __init__(self):
        self.mail = {"inbox": "main", "lifetime_seconds": 2592000}

    def read(self):
        return {"mail": self.mail}


@pytest.fixture
def mailbox():
    return Mailbox(Config(), Calendar(MasterData(ROOT / "masterdata")))


@pytest.fixture
def state():
    return {"inbox": [], "resource_sequences": {"inbox": 10}}


def test_letter_has_the_official_shape(mailbox):
    reward = {"rid": "wallet.point.gold", "val": 3}
    assert mailbox.letter(7, reward, ["@manage:1"], NOW) == {
        "inbox": "main",
        "no": 7,
        "message": None,
        "reward": reward,
        "tags": ["@manage:1"],
        "receive_at": NOW,
        "expire_at": MONTH_END,
    }


@pytest.mark.parametrize(
    ("moment", "lifetime", "expiry"),
    [
        ("2026/11/11 00:00:00", 2592000, "2026/12/11 23:59:59"),
        ("2026/11/11 23:59:59", 2592000, "2026/12/11 23:59:59"),
        ("2026/11/11 02:42:54", 0, "2026/11/11 23:59:59"),
        ("2026/11/11 02:42:54", 180 * 86400, "2027/05/10 23:59:59"),
    ],
)
def test_letters_expire_at_the_end_of_their_last_day(mailbox, moment, lifetime, expiry):
    masters = mailbox.calendar.masters
    mailbox.config.mail["lifetime_seconds"] = lifetime
    row = mailbox.letter(
        1, {"rid": "wallet.point.gold", "val": 1}, [], masters.timestamp(moment)
    )
    assert row["expire_at"] == masters.timestamp(expiry)


def test_letters_can_carry_a_message(mailbox, state):
    reward = {"rid": "wallet.point.gold", "val": 3}
    assert mailbox.letter(7, reward, [], NOW, "お詫び")["message"] == "お詫び"
    assert mailbox.post(state, reward, SOURCE, NOW, "補填")["message"] == "補填"
    assert mailbox.post(state, reward, SOURCE, NOW)["message"] is None


@pytest.mark.parametrize(
    ("deadline", "now", "expired"),
    [
        (1000, 999, False),
        (1000, 1000, False),
        (1000, 1001, True),
        (0, 1001, False),
        (-1, 1001, False),
        (FOREVER, FOREVER + 1, False),
    ],
)
def test_letters_expire_after_their_last_second(mailbox, deadline, now, expired):
    assert mailbox.expired({"expire_at": deadline}, now) is expired


def test_deadline_is_the_end_of_the_day_after_the_lifetime(mailbox):
    assert mailbox.deadline(NOW, 2592000) == MONTH_END
    assert mailbox.deadline(NOW, 0) == mailbox.calendar.date_end(NOW)


@pytest.mark.parametrize(
    ("rid", "tags"),
    [
        ("wallet.point.free_coin", [SOURCE, "vcr", "wallet"]),
        ("wallet.point.paid_coin", [SOURCE, "vcr", "wallet"]),
        ("wallet.point.gold", [SOURCE, "wallet"]),
        ("wallet.ticket.quest_skip", [SOURCE, "wallet"]),
        ("item.material.Bom2", [SOURCE, "item"]),
        ("unit.card.23", [SOURCE, "unit"]),
        ("skill.attachable.1", [SOURCE, "skill"]),
    ],
)
def test_posted_letters_are_tagged_by_source_and_resource(mailbox, state, rid, tags):
    row = mailbox.post(state, {"rid": rid, "val": 1}, SOURCE, NOW)
    assert row["tags"] == tags
    assert state["inbox"] == [row]


def test_posting_takes_the_next_number_of_the_save(mailbox, state):
    first = mailbox.post(state, {"rid": "wallet.point.gold", "val": 1}, SOURCE, NOW)
    state["inbox"].clear()
    second = mailbox.post(state, {"rid": "wallet.point.gold", "val": 2}, SOURCE, NOW)
    assert (first["no"], second["no"]) == (10, 11)
    assert state["resource_sequences"]["inbox"] == 12
    assert [row["reward"]["val"] for row in state["inbox"]] == [2]


def test_inbox_name_follows_the_configuration(mailbox, state):
    mailbox.config.mail["inbox"] = "贈り物"
    row = mailbox.post(state, {"rid": "wallet.point.gold", "val": 1}, SOURCE, NOW)
    assert row["inbox"] == "贈り物"
