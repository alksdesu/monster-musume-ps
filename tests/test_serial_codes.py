from copy import deepcopy
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.initial import create_player
from monmusu_ps.features.serial_code import ROUTES
from monmusu_ps.runtime import Runtime
from test_battle import connect


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
HOUR = 3600
NOVEL, PILLOW, GOODS = "NV001", "CT001", "CT002"
TICKET = "wallet.ticket.gacha_common221230l"
SUCCESS_FIELDS = {"successes", "rejected_reason"}


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
    return create_player(
        "serial", "SERIAL01", runtime.masters, runtime.config.read(), NOW
    )


def use(runtime, state, code, now=NOW):
    result = runtime.serial_codes.use(state, {"serial_code": code}, now)
    assert set(result.data) == SUCCESS_FIELDS
    return result.data


def letters(state):
    return [(row["reward"], row["tags"]) for row in state["inbox"]]


def custom(runtime, code, campaign_code, end_at=0):
    codes = runtime.config.read()["serial_code"]["codes"]
    codes[code] = {"campaign_code": campaign_code, "end_at": end_at}


def test_campaign_codes_mail_the_campaign_rewards(runtime, state):
    assert use(runtime, state, PILLOW) == {
        "successes": {"campaign_code": PILLOW},
        "rejected_reason": None,
    }
    assert letters(state) == [
        ({"rid": TICKET, "val": 1}, ["serial_code.reward:CT001", "wallet"]),
        (
            {"rid": "item.unit_skin.24_2", "val": 1},
            ["serial_code.reward:CT001", "item"],
        ),
    ]
    assert state["serial_codes"]["redeemed"] == [
        {"code": PILLOW, "campaign_code": PILLOW, "at": NOW}
    ]


def test_every_campaign_can_be_redeemed(runtime, state):
    campaigns = runtime.masters.table("serial_code_campaign_data")
    for row in campaigns:
        data = use(runtime, state, row["campaign_code"])
        assert data["successes"] == {"campaign_code": row["campaign_code"]}
    assert len(state["serial_codes"]["redeemed"]) == len(campaigns) == 36


def test_a_code_works_once(runtime, state):
    use(runtime, state, NOVEL)
    before = deepcopy(state)
    assert use(runtime, state, NOVEL) == {
        "successes": None,
        "rejected_reason": "ALREADY_USED",
    }
    assert state == before


def test_unknown_codes_are_invalid(runtime, state):
    for code in ("NV999", "nv001", "", "NV001 ", "ＮＶ００１"):
        assert use(runtime, state, code)["rejected_reason"] == "INVALID_CODE"
    assert state["inbox"] == []


def test_campaign_codes_can_be_switched_off(runtime, state):
    runtime.config.read()["serial_code"]["accept_campaign_codes"] = False
    assert use(runtime, state, NOVEL)["rejected_reason"] == "INVALID_CODE"
    custom(runtime, "MONMUSU-2026", NOVEL)
    assert use(runtime, state, "MONMUSU-2026")["successes"] == {"campaign_code": NOVEL}


def test_configured_codes_share_the_campaign_limit(runtime, state):
    custom(runtime, "GIFT-A", GOODS)
    custom(runtime, "GIFT-B", GOODS)
    assert use(runtime, state, "GIFT-A")["successes"] == {"campaign_code": GOODS}
    assert use(runtime, state, "GIFT-B")["rejected_reason"] == "LIMIT_OVER"
    assert use(runtime, state, "GIFT-A")["rejected_reason"] == "ALREADY_USED"
    assert len(state["inbox"]) == 1


def test_configured_codes_expire(runtime, state):
    custom(runtime, "LATE-1", GOODS, end_at=NOW)
    custom(runtime, "SOON-1", GOODS, end_at=NOW + 1)
    assert use(runtime, state, "LATE-1")["rejected_reason"] == "OUT_OF_TERM"
    assert use(runtime, state, "SOON-1")["successes"] == {"campaign_code": GOODS}


def test_configured_codes_must_name_a_campaign(runtime, state):
    custom(runtime, "BROKEN", "XX000")
    with pytest.raises(ValueError, match="unknown campaign"):
        use(runtime, state, "BROKEN")


def test_five_wrong_codes_lock_the_input_for_an_hour(runtime, state):
    for attempt in range(5):
        assert use(runtime, state, "WRONG", NOW + attempt)["rejected_reason"] == (
            "INVALID_CODE"
        )
    locked = NOW + 4
    for moment in (locked + 1, locked + HOUR - 1):
        assert use(runtime, state, NOVEL, moment)["rejected_reason"] == (
            "SERIAL_INVALID_PENALTY"
        )
    assert state["inbox"] == []
    assert use(runtime, state, NOVEL, locked + HOUR)["successes"] == {
        "campaign_code": NOVEL
    }
    assert state["serial_codes"]["failures"] == 0


def test_a_right_code_clears_the_wrong_attempts(runtime, state):
    for attempt in range(4):
        use(runtime, state, "WRONG", NOW + attempt)
    use(runtime, state, NOVEL, NOW + 10)
    for attempt in range(4):
        assert use(runtime, state, "WRONG", NOW + 20 + attempt)["rejected_reason"] == (
            "INVALID_CODE"
        )


def test_old_wrong_attempts_are_forgotten(runtime, state):
    for attempt in range(4):
        use(runtime, state, "WRONG", NOW + attempt)
    assert use(runtime, state, "WRONG", NOW + 2 * HOUR)["rejected_reason"] == (
        "INVALID_CODE"
    )
    assert state["serial_codes"]["failures"] == 1


def test_the_lock_can_be_switched_off(runtime, state):
    runtime.config.read()["serial_code"]["invalid_attempts"] = 0
    for attempt in range(8):
        assert use(runtime, state, "WRONG", NOW + attempt)["rejected_reason"] == (
            "INVALID_CODE"
        )


def test_codes_must_be_text(runtime, state):
    with pytest.raises(ValueError):
        runtime.serial_codes.use(state, {"serial_code": 1}, NOW)


async def test_native_serial_code_round_trip(tmp_path):
    http, client, runtime, profile = await connect(tmp_path)
    try:
        assert set(ROUTES) <= set(runtime.registry.handlers)
        used = await client.call("serial_code/use", {"serial_code": GOODS})
        assert used["data"] == {
            "successes": {"campaign_code": GOODS},
            "rejected_reason": None,
        }
        again = await client.call("serial_code/use", {"serial_code": GOODS})
        assert again["data"]["rejected_reason"] == "ALREADY_USED"
        wrong = await client.call("serial_code/use", {"serial_code": "NOPE"})
        assert wrong["data"] == {"successes": None, "rejected_reason": "INVALID_CODE"}
        async with runtime.store.transaction(profile) as state:
            assert [row["code"] for row in state["serial_codes"]["redeemed"]] == [GOODS]
            assert state["serial_codes"]["failures"] == 1
            assert [row["reward"]["rid"] for row in state["inbox"]] == [TICKET]
    finally:
        await http.close()
