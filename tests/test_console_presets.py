import pytest

from monmusu_ps.console.presets import FREE, OPENERS
from test_console import PROFILE, get, panel, post, runtime_of


__all__ = ["panel"]

GOLD = "wallet.point.gold"


@pytest.fixture(autouse=True)
def unscheduled():
    yield


def listed(actions, key, group=None):
    return next(
        row
        for row in actions
        if row["key"] == key and (group is None or row["group"] == group)
    )


async def test_every_group_offers_its_openers_switches_and_links(panel):
    actions = (await get(panel, "presets"))["actions"]
    assert {row["key"] for row in actions if row["action"] == "open"} == {
        entry.key for entry in OPENERS
    }
    gacha = listed(actions, "open_gacha")
    assert gacha["total"] > 1000 and gacha["running"] < gacha["total"]
    assert gacha["opened"] is False and gacha["picked"] == []
    assert listed(actions, FREE, "gacha")["on"] is False
    assert listed(actions, "stories_all", "story")["page"] == "shortcuts"


async def test_opening_all_of_a_kind_keeps_everything_running(panel):
    opened = await post(panel, "presets/run", {"key": "open_bingo"})
    assert opened["opened"] > 1
    actions = (await get(panel, "presets"))["actions"]
    bingo = listed(actions, "open_bingo")
    assert bingo["opened"] and bingo["running"] == bingo["total"]
    entry = runtime_of(panel).schedule.config.read()["activities"][0]
    assert entry["kind"] == "bingo" and entry["end"].startswith("2999/")
    closed = await post(panel, "presets/run", {"key": "open_bingo", "on": False})
    assert closed["dropped"] == opened["opened"]
    assert (
        listed((await get(panel, "presets"))["actions"], "open_bingo")["opened"]
        is False
    )
    await post(panel, "presets/run", {"key": "open_nothing"}, expect=404)


async def test_entrances_are_picked_one_by_one(panel):
    ended = await get(panel, "presets/items", key="open_event", status="ended", limit=3)
    assert ended["total"] > 3 and all(not row["picked"] for row in ended["rows"])
    ids = [row["id"] for row in ended["rows"][:2]]
    picked = await post(
        panel, "presets/pick", {"key": "open_event", "ids": ids, "on": True}
    )
    assert picked == {"picked": 2, "changed": 2}
    again = await get(
        panel, "presets/items", key="open_event", status="running", q="", limit=50
    )
    assert {row["id"] for row in again["rows"] if row["picked"]} == set(ids)
    assert listed((await get(panel, "presets"))["actions"], "open_event")[
        "picked"
    ] == sorted(ids, key=str)
    dropped = await post(
        panel, "presets/pick", {"key": "open_event", "ids": ids[:1], "on": False}
    )
    assert dropped == {"picked": 1, "changed": 1}
    await post(
        panel,
        "presets/pick",
        {"key": "open_event", "ids": [-1], "on": True},
        expect=400,
    )


async def test_the_free_switch_waives_every_price(panel):
    runtime = runtime_of(panel)
    state = await runtime.store.read(PROFILE)
    before = runtime.resources.balance(state, GOLD)
    assert (await post(panel, "presets/run", {"key": FREE, "on": True}))["on"] is True
    assert runtime.config.read()["resources"]["free_costs"] is True
    async with runtime.store.transaction(PROFILE) as state:
        runtime.resources.charge(
            state, [{"rid": GOLD, "val": before + 1}], 0, "NOT_ENOUGH"
        )
    assert runtime.resources.balance(await runtime.store.read(PROFILE), GOLD) == before
    assert (await post(panel, "presets/run", {"key": FREE, "on": False}))["on"] is False
    await post(panel, "presets/run", {"key": FREE, "on": "yes"}, expect=400)


async def test_knobs_turn_the_settings_of_their_group(panel):
    actions = (await get(panel, "presets"))["actions"]
    ssr = listed(actions, "rate_ssr", "gacha")
    assert ssr["action"] == "knob" and ssr["value"] == 3.0
    turned = await post(panel, "presets/run", {"key": "rate_ssr", "value": 50})
    assert turned == {"value": 50}
    assert runtime_of(panel).config.read()["gacha"]["rates"]["SSR"] == 50
    flag = listed(actions, "sugoroku_tickets", "event")
    assert flag["kind"] == "flag" and flag["value"] is True
    assert (
        await post(panel, "presets/run", {"key": "sugoroku_tickets", "value": False})
    )["value"] is False
    await post(panel, "presets/run", {"key": "rate_ssr", "value": "many"}, expect=400)
    await post(panel, "presets/run", {"key": "rate_ssr", "value": -1}, expect=400)
    await post(
        panel, "presets/run", {"key": "sugoroku_tickets", "value": 1}, expect=400
    )
