from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from monmusu_ps.core.context import Context
from monmusu_ps.core.errors import ApiNotFound
from monmusu_ps.features.reminders import ROUTES


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
FOREVER = 253_402_300_799


@pytest.fixture
def context():
    state = {
        "player": {"player_code": "REMIND01"},
        "modules": {},
        "reminder_seen": {},
        "revisioned_reminders": [
            {"group": 1, "id": 1, "rev": 1_794_300_000, "expire": FOREVER},
            {"group": 1, "id": 2, "rev": 0, "expire": FOREVER},
            {"group": 2, "id": 1, "rev": 1_794_330_000, "expire": FOREVER},
        ],
        "flag_reminders": [
            {"group": 3, "id": 10001, "expire": FOREVER},
            {"group": 3, "id": 10002, "expire": FOREVER},
        ],
    }
    for key in (
        "units",
        "boms",
        "compositions",
        "wallets",
        "items",
        "charms",
        "quest_records",
        "quest_secrets",
        "bonus_passes",
        "boxgacha_records",
        "gacha_stamp_records",
        "missions",
        "mission_counters",
        "periods",
        "limits",
        "stories",
    ):
        state[key] = []
    return Context(SimpleNamespace(), {}, state, None, NOW)


def call(ctx, endpoint, **payload):
    ctx.payload = payload
    return ROUTES[endpoint](ctx)


def test_flag_list_returns_the_saved_flags(context):
    result = call(context, "reminder/flag/list")
    assert result.data == {"flag_reminders": context.state["flag_reminders"]}


def test_checking_off_a_revision_clears_it_and_syncs_the_row(context):
    result = call(context, "reminder/rev/checkoff", group=1, id=1, rev=1_794_300_000)
    assert result.include_data is False
    assert result.sync == {
        "rev_reminder": [
            {
                "scope": "player@REMIND01",
                "data": [{"group": 1, "id": 1, "rev": 0, "expire": FOREVER}],
            }
        ]
    }
    assert context.state["revisioned_reminders"][0]["rev"] == 0


def test_a_newer_revision_survives_an_older_checkoff(context):
    before = deepcopy(context.state)
    result = call(context, "reminder/rev/checkoff", group=2, id=1, rev=1_794_320_000)
    assert result.sync is None
    before["reminder_seen"] = {"2.1": 1_794_320_000}
    assert context.state == before


def test_checking_off_an_unknown_reminder_is_not_found(context):
    with pytest.raises(ApiNotFound) as caught:
        call(context, "reminder/rev/checkoff", group=1, id=9, rev=5)
    assert caught.value.resources == ["reminder.rev.1.9"]
    with pytest.raises(ApiNotFound) as caught:
        call(context, "reminder/flag/checkoff", group=3, id=10009)
    assert caught.value.resources == ["reminder.flag.3.10009"]


@pytest.mark.parametrize(
    "payload",
    [
        {"group": "1", "id": 1, "rev": 1},
        {"group": 1, "id": None, "rev": 1},
        {"group": 1, "id": 1, "rev": "1"},
        {"group": True, "id": 1, "rev": 1},
    ],
)
def test_reminder_keys_must_be_integers(context, payload):
    with pytest.raises(ValueError):
        call(context, "reminder/rev/checkoff", **payload)


def test_checking_off_a_flag_removes_it_and_replaces_the_table(context):
    result = call(context, "reminder/flag/checkoff", group=3, id=10001)
    assert result.include_data is False
    assert result.sync == {
        "flag_reminder": [
            {
                "scope": "player@REMIND01",
                "data": [{"group": 3, "id": 10002, "expire": FOREVER}],
            }
        ]
    }
    assert call(context, "reminder/flag/list").data == {
        "flag_reminders": [{"group": 3, "id": 10002, "expire": FOREVER}]
    }


def test_response_field_names_match_the_client_models(context):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text(encoding="utf-8"))

    def fields(model):
        return {field["name"] for field in schema["models"][model]["fields"]}

    rows = call(context, "reminder/flag/list").data["flag_reminders"]
    assert all(set(row) == fields("FlaggedReminder") for row in rows)
    assert all(
        set(row) == fields("RevisionedReminder")
        for row in context.state["revisioned_reminders"]
    )
