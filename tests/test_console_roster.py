import json
import pytest

from monmusu_ps.console.shortcuts import CATALOG, ORDER, SUB_SKILL
from test_console import PROFILE, get, panel, post, runtime_of


__all__ = ["panel"]

CARD = 1
ANOTHER = 7


@pytest.fixture(autouse=True)
def unscheduled():
    yield


async def state_of(panel):
    return await runtime_of(panel).store.read(PROFILE)


async def test_units_are_described_with_their_caps_and_choices(panel):
    cards = (await get(panel, "roster/cards", profile=PROFILE))["cards"]
    assert cards and any(card["instance_id"] is None for card in cards)
    assert all(
        card["instance_id"] is None
        for card in cards
        if card["card_id"] in (CARD, ANOTHER)
    )
    assert {card["rarity"] for card in cards} <= {"R", "HR", "SR", "SSR"}
    outcome = await post(
        panel, "roster/add", {"profile": PROFILE, "card_ids": [CARD, CARD, ANOTHER]}
    )
    assert outcome["added"] == 2 and outcome["synced"]
    units = (await get(panel, "roster", profile=PROFILE))["units"]
    unit = next(row for row in units if row["card_id"] == CARD)
    assert unit["level"] == 1 and unit["level_cap"] == 30
    assert unit["rank"] == 0 and unit["rank_cap"] >= 1
    assert unit["exskills"][0]["cap"] >= 2 and unit["exskills"][0]["open"]
    assert unit["potential"]["cap"] == 5 and unit["skins"][0]["id"] == 0
    assert (await post(panel, "roster/add", {"profile": PROFILE, "card_ids": [CARD]}))[
        "added"
    ] == 0


async def test_a_unit_is_set_within_its_caps(panel):
    await post(panel, "roster/add", {"profile": PROFILE, "card_ids": [CARD]})
    unit = next(
        row
        for row in (await get(panel, "roster", profile=PROFILE))["units"]
        if row["card_id"] == CARD
    )
    instance = unit["instance_id"]
    body = {"profile": PROFILE, "instance_id": instance, "fields": {"level": 31}}
    await post(panel, "roster/unit", body, expect=400)
    body["fields"] = {
        "rank": 1,
        "level": 45,
        "exskills": {"2": 2},
        "potential_points": 5,
        "potential_skills": [1, 2],
    }
    changed = (await post(panel, "roster/unit", body))["unit"]
    assert (changed["rank"], changed["level"], changed["level_cap"]) == (1, 45, 60)
    assert changed["exskills"][1]["level"] == 2 and changed["exskills"][1]["open"]
    assert changed["potential"]["points"] == 5 and changed["potential"]["skills"] == [
        1,
        2,
    ]
    state = await state_of(panel)
    stored = next(row for row in state["units"] if row["card_id"] == CARD)
    assert stored["t_exp"] == runtime_of(panel).progression.level_exp(stored, 45)
    assert stored["flags"] & 1
    # Dropping the rank pulls the level back under the new cap.
    body["fields"] = {"rank": 0}
    assert (await post(panel, "roster/unit", body))["unit"]["level"] == 30
    skin = next(row["id"] for row in changed["skins"] if row["id"])
    body["fields"] = {"skin": skin}
    assert (await post(panel, "roster/unit", body))["unit"]["skin"] == skin
    state = await state_of(panel)
    assert (
        runtime_of(panel).resources.balance(state, f"item.unit_skin.{CARD}_{skin}") == 1
    )


async def test_every_unit_is_filled_to_the_top(panel):
    await post(panel, "roster/add", {"profile": PROFILE, "card_ids": [CARD, ANOTHER]})
    outcome = await post(
        panel,
        "roster/fill",
        {
            "profile": PROFILE,
            "aspects": ["level", "rank", "exskill", "weapon", "potential"],
        },
    )
    assert len(outcome["filled"]) >= 2
    for unit in (await get(panel, "roster", profile=PROFILE))["units"]:
        assert unit["level"] == unit["level_cap"] and unit["rank"] == unit["rank_cap"]
        assert all(
            skill["level"] == skill["cap"] and skill["open"]
            for skill in unit["exskills"]
        )
        assert unit["weapon"]["level"] == unit["weapon"]["cap"]
        assert unit["potential"]["points"] == unit["potential"]["cap"]
    again = await post(panel, "roster/fill", {"profile": PROFILE, "aspects": ["level"]})
    assert again["filled"] == {}


async def test_shortcuts_are_listed_with_what_they_would_change(panel):
    listed = await get(panel, "shortcuts", profile=PROFILE)
    assert [entry["key"] for entry in listed["catalog"]] == [
        entry.key for entry in CATALOG
    ]
    status = listed["status"]
    assert 0 < status["units"]["owned"] < 5 and status["units"]["total"] > 400
    assert status["stories"]["total"] > 2000 and status["quests"]["total"] > 1000
    assert status["subskills"]["held"] == 0 and status["subskills"]["total"] > 300
    assert status["tutorial"] is False


async def test_each_shortcut_does_what_it_says(panel):
    runtime = runtime_of(panel)
    for key in ORDER:
        outcome = await post(panel, "shortcuts/run", {"profile": PROFILE, "key": key})
        assert outcome["backup"].startswith("console-"), key
    status = (await get(panel, "shortcuts", profile=PROFILE))["status"]
    assert status["units"]["owned"] == status["units"]["total"]
    assert status["units_filled"]["done"] == status["units_filled"]["total"]
    assert status["boms"]["owned"] == status["boms"]["total"]
    assert status["skins"]["held"] == status["skins"]["total"]
    assert status["subskills"]["held"] == status["subskills"]["total"]
    assert (
        status["stories"]["open"]
        == status["stories"]["read"]
        == status["stories"]["total"]
    )
    assert status["quests"]["cleared"] == status["quests"]["total"]
    assert status["missions"]["done"] == status["missions"]["total"] > 0
    assert status["wallets"]["full"] == status["wallets"]["total"]
    assert status["stamina"]["amount"] == status["stamina"]["max"]
    assert status["player"]["rank"] == status["player"]["max"]
    assert status["tutorial"] is True
    state = await state_of(panel)
    assert runtime.resources.balance(state, "wallet.point.gold") == 99999999
    assert all(row["wins"] > 0 and row["flags"] & 3 for row in state["quest_records"])
    assert any(event["type"] == "quest_complete" for event in state["events"])
    # Running everything again changes nothing more.
    outcome = await post(
        panel, "shortcuts/run", {"profile": PROFILE, "key": "everything"}
    )
    assert outcome["result"]["units_all"]["added"] == 0
    assert outcome["result"]["quests_all"]["cleared"] == 0
    await post(
        panel, "shortcuts/run", {"profile": PROFILE, "key": "no_such"}, expect=404
    )


async def test_every_sub_skill_is_handed_out_once(panel):
    kept = {"rid": f"{SUB_SKILL}1001", "val": 5}
    gift = {"profiles": [PROFILE], "orders": [kept], "delivery": "direct"}
    await post(panel, "player/grant", {**gift, "message": ""})
    body = {"profile": PROFILE, "key": "subskills_all"}
    outcome = await post(panel, "shortcuts/run", body)
    total = (await get(panel, "shortcuts", profile=PROFILE))["status"]["subskills"]
    assert outcome["synced"] and "subskills-all" in outcome["backup"]
    assert outcome["result"] == {"given": total["total"] - 1}
    assert total["held"] == total["total"]
    held = {
        row["id"]: row["val"]
        for row in (await state_of(panel))["items"]
        if row["id"].startswith(SUB_SKILL)
    }
    assert len(held) == total["total"] and held.pop(kept["rid"]) == 5
    assert set(held.values()) == {1}
    assert (await post(panel, "shortcuts/run", body))["result"] == {"given": 0}


async def test_stories_open_raises_units_to_what_the_scenes_need(panel):
    outcome = await post(
        panel, "shortcuts/run", {"profile": PROFILE, "key": "stories_all"}
    )
    result = outcome["result"]
    assert result["added"] > 400 and result["opened"] > 2000 and result["raised"] > 0
    units = (await get(panel, "roster", profile=PROFILE))["units"]
    needed = {}
    for row in runtime_of(panel).masters.table("story_data_unit"):
        needed[int(row["story_id"])] = max(
            needed.get(int(row["story_id"]), 0), row["unlockLevel"]
        )
    assert all(
        unit["level"] >= min(needed.get(unit["card_id"], 0), unit["level_cap"])
        for unit in units
    )


async def test_the_language_is_switched_from_the_console(panel, tmp_path, monkeypatch):
    from monmusu_ps.core import localization

    workspace = tmp_path / "localization"
    workspace.mkdir()
    pairs = [("はい", "好"), ("セレン", "赛莲"), ("行くよ", "走吧"), ("古い", "旧")]
    rows = [json.dumps({"source": a, "target": b}, ensure_ascii=False) for a, b in pairs]
    (workspace / "zh-Hans.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    scene = {
        "origin": "scenario:a/b#s1",
        "title": "t",
        "lines": [
            {"speaker": "セレン", "text": "行くよ", "choice": False},
            {"speaker": "セレン", "text": "まだだ", "choice": False},
        ],
    }
    (workspace / "scenes.jsonl").write_text(json.dumps(scene, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setattr(localization, "WORKSPACE", workspace.name)
    runtime_of(panel).localization.root = tmp_path
    listed = await get(panel, "language")
    assert listed["choices"] == [{"code": "zh-Hans", "name": "简体中文"}]
    counted = listed["libraries"]["zh-Hans"]
    assert (counted["texts"], counted["lines"], counted["all_lines"], counted["scenes"]) == (3, 1, 2, 1)
    assert counted["updated_at"] > 0
    chosen = await post(panel, "language", {"code": "zh-Hans"})
    assert chosen["current"] == "zh-Hans"
    assert runtime_of(panel).config.read()["localization"]["language"] == "zh-Hans"
    assert (await post(panel, "language", {"code": None}))["current"] is None
    await post(panel, "language", {"code": "ko"}, expect=400)


async def test_the_player_is_renamed_from_the_console(panel):
    outcome = await post(
        panel, "player/rename", {"profile": PROFILE, "nickname": " 咪啪 "}
    )
    assert outcome["synced"]
    assert (await get(panel, "player", profile=PROFILE))["player"]["nickname"] == "咪啪"
    await post(
        panel, "player/rename", {"profile": PROFILE, "nickname": "  "}, expect=400
    )
