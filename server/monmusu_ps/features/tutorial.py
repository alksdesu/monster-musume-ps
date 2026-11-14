from copy import deepcopy
import json

from ..core.context import Context
from ..core.sync import Result, diff_state


def _main_deck(state: dict) -> dict:
    return next(
        row
        for row in state["compositions"]
        if row["section"] == "unit_deck" and row["no"] == 1
    )


def _place_unit(state: dict, card_id: int, number: int | None = None) -> None:
    unit = next((row for row in state["units"] if row["card_id"] == card_id), None)
    if unit is None:
        raise ValueError(f"Unit card {card_id} has not joined the roster")
    deck = _main_deck(state)
    slots = [json.loads(value) for value in deck["slots"]]
    if number is None:
        if any(slot["t"] == 1 and slot["iid"] == unit["instance_id"] for slot in slots):
            return
        number = max((slot["sn"] for slot in slots if slot["t"] == 1), default=-1) + 1
    slots = [
        slot
        for slot in slots
        if slot["t"] != 1
        or (slot["sn"] != number and slot["iid"] != unit["instance_id"])
    ]
    slots.append(
        {
            "t": 1,
            "sn": number,
            "iid": unit["instance_id"],
            "sauto": True,
            "skillAuto": 1,
            "classActionAuto": 1,
            "sskills": {"s1": 0, "s2": 0},
            "exskill": unit["primary_exskill"],
        }
    )
    slots.sort(key=lambda slot: (slot["t"], slot["sn"]))
    deck["slots"] = [json.dumps(slot, separators=(",", ":")) for slot in slots]


def skip(ctx: Context) -> Result:
    tutorial = ctx.runtime.tutorial
    if tutorial.finished(ctx.state):
        return Result(include_data=False)
    if tutorial.started(ctx.state):
        raise ValueError("Tutorial can only be skipped before it starts")
    before = deepcopy(ctx.state)
    policy = ctx.runtime.config.read()["tutorial_skip"]
    masters = ctx.runtime.masters
    for index, quest_id in enumerate(tutorial.quests()):
        started = ctx.runtime.battles.start(
            ctx.state,
            {
                "quest_id": quest_id,
                "statement": {
                    "use_deck": {"section": "unit_deck", "no": 1},
                    "rental_unit": None,
                    "rental_unit_iid": 0,
                    "t_menus": [],
                    "r_mode": 0,
                },
            },
            ctx.now,
            costs_enabled=policy["consume_stamina"],
            random_rewards=policy["random_rewards"],
        )
        ctx.runtime.battles.finish(
            ctx.state,
            {
                "txid": started.data["txid"],
                "result": "COMPLETE",
                "statement": {
                    "rating": policy["rating"],
                    "ec_score": 0,
                    "tower": None,
                    "loot_list": [drop["slot"] for drop in started.data["drop_list"]],
                    "log_data": [],
                },
            },
            ctx.now,
        )
        quest = masters.lookup("quest_data", "id", quest_id)
        for reward in quest["beginningRewardtable"]["m_rewards"]:
            if reward["m_walletCategory"] == "unit" and reward["m_count"] > 0:
                _place_unit(ctx.state, int(reward["m_walletName"]))
        if index == 3:
            ctx.runtime.gacha.draw(
                ctx.state,
                {"gacha_id": tutorial.gacha(), "exec_num": 1, "interruption": False},
                ctx.now,
            )
            _place_unit(
                ctx.state, masters.property("Int", 55), masters.property("Int", 56)
            )
    tutorial.finish(ctx.state, ctx.now)
    return Result(sync=diff_state(before, ctx.state), include_data=False)


ROUTES = {"tutorial/skip": skip}
