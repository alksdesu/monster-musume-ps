import asyncio
from copy import deepcopy
import json

import pytest

from monmusu_ps.core.migrations import (
    MIGRATIONS,
    add_battle_state,
    add_quest_rule_state,
    add_team_snapshots,
)
from monmusu_ps.core.store import PlayerStore


async def test_transaction_rolls_back_all_changes(tmp_path):
    store = PlayerStore(tmp_path, lambda profile: {"gold": 500, "units": [11, 22]})
    await store.read("local")
    before = store.path("local").read_bytes()
    with pytest.raises(RuntimeError):
        async with store.transaction("local") as state:
            state["gold"] -= 100
            state["units"].append(23)
            raise RuntimeError("reward calculation failed")
    assert store.path("local").read_bytes() == before


async def test_concurrent_transactions_preserve_every_update(tmp_path):
    store = PlayerStore(tmp_path, lambda profile: {"gold": 0})

    async def increment():
        async with store.transaction("local") as state:
            current = state["gold"]
            await asyncio.sleep(0)
            state["gold"] = current + 1

    await asyncio.gather(*(increment() for _ in range(20)))
    assert (await store.read("local"))["gold"] == 20
    assert json.loads(store.path("local").read_text())["revision"] == 20


async def test_profiles_and_restart(tmp_path):
    store = PlayerStore(tmp_path, lambda profile: {"profile": profile, "gold": 500})
    async with store.transaction("../other/player") as state:
        state["gold"] = 800
    assert store.path("../other/player").is_relative_to(tmp_path)
    reopened = PlayerStore(tmp_path, lambda profile: {"profile": profile, "gold": 500})
    assert (await reopened.read("../other/player"))["gold"] == 800
    assert (await reopened.read("second"))["gold"] == 500


async def test_corrupt_save_is_preserved(tmp_path):
    store = PlayerStore(tmp_path, lambda profile: {"gold": 500})
    path = store.path("local")
    path.parent.mkdir(parents=True)
    path.write_text('{"broken":', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        await store.read("local")
    assert path.read_text() == '{"broken":'


async def test_saved_players_gain_the_battle_state_on_first_load(tmp_path):
    legacy = {
        "gold": 500,
        "units": [{"instance_id": 1}, {"instance_id": 4}],
        "boms": [{"instance_id": 2}],
        "inbox": [{"no": 7}],
    }
    original = PlayerStore(tmp_path, lambda profile: deepcopy(legacy))
    await original.read("local")
    upgraded = PlayerStore(tmp_path, lambda profile: {}, (add_battle_state,))
    assert await upgraded.read("local") == {
        **legacy,
        "quest_transactions": {},
        "team_logs": {},
        "events": [],
        "resource_sequences": {"unit": 5, "bom": 3, "inbox": 8},
        "boxgacha_records": [],
        "revisioned_reminders": [],
    }
    document = json.loads(upgraded.path("local").read_text())
    assert (document["schema_version"], document["revision"]) == (2, 2)
    await upgraded.read("local")
    assert json.loads(upgraded.path("local").read_text())["revision"] == 2
    with pytest.raises(ValueError, match="Unsupported save schema"):
        await original.read("local")


async def test_battle_era_saves_gain_the_later_state(tmp_path):
    legacy = {"gold": 500, "units": [], "boms": [], "inbox": []}
    original = PlayerStore(tmp_path, lambda profile: {}, (add_battle_state,))
    async with original.transaction("local") as state:
        state.update(legacy)
    assert json.loads(original.path("local").read_text())["schema_version"] == 2
    upgraded = PlayerStore(tmp_path, lambda profile: {}, MIGRATIONS)
    assert await upgraded.read("local") == {
        **legacy,
        "login_bonus": [],
        "inbox_archive": [],
        "last_login_day": 0,
        "gacha_history": [],
        "serial_codes": {"redeemed": [], "failures": 0, "failed_at": 0},
        "shared_quest_logs": [],
        "reminder_seen": {},
        "lending": {"s1": 0, "s2": 0, "s3": 0},
        "friend_points": {"day": 0, "greeted": 0, "collected_day": 0},
        "last_login_at": 0,
        "monthly_benefits": {"vip": 0, "dmm_premium": 0},
        "bingo": {},
        "votes": {},
        "mileage": {},
        "event_growth": {},
        "sealed_chara": {},
        "subdue_raid": {},
        "sugoroku": {"cleared": [], "session": None, "last_result": None},
        "revisioned_reminders": [
            {"group": 1, "id": 1, "rev": 0, "expire": 253402300799},
            {"group": 1, "id": 2, "rev": 0, "expire": 253402300799},
        ],
    }
    document = json.loads(upgraded.path("local").read_text())
    assert document["schema_version"] == len(MIGRATIONS) + 1 == 17
    with pytest.raises(ValueError, match="Unsupported save schema"):
        await original.read("local")


async def test_first_era_saves_run_the_whole_migration_chain(tmp_path):
    legacy = {"units": [{"instance_id": 2}], "boms": [], "inbox": []}
    original = PlayerStore(tmp_path, lambda profile: deepcopy(legacy))
    await original.read("local")
    upgraded = await PlayerStore(tmp_path, lambda profile: {}, MIGRATIONS).read("local")
    assert upgraded["resource_sequences"] == {"unit": 3, "bom": 1, "inbox": 1}
    assert upgraded["login_bonus"] == []
    assert upgraded["inbox_archive"] == []
    assert upgraded["last_login_day"] == 0
    assert upgraded["gacha_history"] == []


async def test_documents_list_every_saved_profile(tmp_path):
    store = PlayerStore(tmp_path, lambda profile: {"owner": profile})
    assert list(store.documents()) == []
    for profile in ("first", "second"):
        await store.read(profile)
    (tmp_path / "platform.json").write_text("{}", encoding="utf-8")
    documents = list(store.documents())
    assert sorted(row["profile"] for row in documents) == ["first", "second"]
    assert all(row["state"] == {"owner": row["profile"]} for row in documents)


async def test_new_players_start_on_the_current_schema(tmp_path):
    store = PlayerStore(tmp_path, lambda profile: {"gold": 500}, (add_battle_state,))
    await store.read("local")
    document = json.loads(store.path("local").read_text())
    assert (document["schema_version"], document["state"]) == (2, {"gold": 500})


def test_legacy_team_logs_become_team_snapshots():
    unit = {
        "instance_id": 1,
        "card_id": 11,
        "primary_exskill": 1,
        "aa": {"s1": 5, "s2": 6, "s3": 0, "s4": 0},
    }
    slot = {"t": 1, "sn": 0, "iid": 1, "sskills": {"s1": 7, "s2": 0}, "exskill": 2}
    deck = {"section": "unit_deck", "no": 1}
    state = {
        "skins": [],
        "units": [unit],
        "boms": [],
        "compositions": [{**deck, "slots": [json.dumps(slot)]}],
        "quest_records": [{"quest_id": 10000, "record_rating": 3}],
        "team_logs": {
            "quest.10000#LEGACY01": {
                "quest_id": 10000,
                "created_at": 5,
                "party": [1, 9],
                "statement": {"use_deck": deck},
                "log_data": [1],
            },
            "quest.10001#LEGACY01": {
                "quest_id": 10001,
                "created_at": 6,
                "party": [],
                "statement": {"use_deck": {"section": "unit_deck", "no": 9}},
                "log_data": [],
            },
        },
        "quest_transactions": {
            "open": {"status": "active"},
            "done": {
                "status": "completed",
                "party": [1],
                "support": None,
                "deck": deck,
            },
        },
    }
    add_team_snapshots(state)
    assert "skins" not in state
    kept = state["team_logs"]["quest.10000#LEGACY01"]
    team = kept.pop("team")
    assert kept == {
        "quest_id": 10000,
        "created_at": 5,
        "rating": 3,
        "txid": "",
        "log_data": [1],
    }
    assert team["format_ver"] == 1 and team["boms"] == [] and team["rental"] is None
    assert [(row["slot"], json.loads(row["unit"])) for row in team["units"]] == [
        (
            0,
            {
                **unit,
                "primary_exskill": 2,
                "aa": {"s1": 7, "s2": 0, "s3": 0, "s4": 0},
            },
        )
    ]
    unrated = state["team_logs"]["quest.10001#LEGACY01"]
    assert (unrated["rating"], unrated["team"]["units"]) == (0, [])
    assert state["quest_transactions"] == {
        "open": {"status": "active", "support": None},
        "done": {"status": "completed"},
    }


def test_open_battles_and_team_logs_gain_the_quest_rule_fields():
    state = {
        "wild_hunt_ranks": [],
        "quest_records": [{"quest_id": 10000, "log_key": [9, 8]}],
        "quest_transactions": {
            "open": {"status": "active", "quest": {"id": 10000}},
            "fresh": {"status": "active", "quest": {"id": 10001}},
            "done": {"status": "completed"},
        },
        "team_logs": {"quest.10000#LEGACY01": {"rating": 3}},
    }
    add_quest_rule_state(state)
    assert "wild_hunt_ranks" not in state
    defaults = {"sub_skills": [], "training": None, "mode": 0, "raid": False}
    assert state["quest_transactions"] == {
        "open": {
            "status": "active",
            "quest": {"id": 10000},
            **defaults,
            "log_key": [9, 8],
        },
        "fresh": {
            "status": "active",
            "quest": {"id": 10001},
            **defaults,
            "log_key": [],
        },
        "done": {"status": "completed"},
    }
    assert state["team_logs"] == {
        "quest.10000#LEGACY01": {"rating": 3, "wild_hunt_score": 0}
    }
    empty = {}
    add_quest_rule_state(empty)
    assert empty == {}
