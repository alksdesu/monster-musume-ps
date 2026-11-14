import json

from .masterdata import MasterData
from .migrations import MIGRATIONS


def bom(instance_id: int, card_id: int, now: int) -> dict:
    return {
        "instance_id": instance_id,
        "card_id": card_id,
        "exskill_lv": 1,
        "passive_lv": 1,
        "created_at": now,
        "updated_at": now,
    }


def unit(instance_id: int, card_id: int, now: int) -> dict:
    return {
        "instance_id": instance_id,
        "card_id": card_id,
        "t_exp": 0,
        "r_unit_lv": 1,
        "exskill_lv": 1,
        "exskill_lv_2": 1,
        "exskill_lv_3": 1,
        "uqw_lv": 0,
        "primary_exskill": 1,
        "rental_exskill": 1,
        "rank": 0,
        "gear_set": {"A": 0, "B": 0, "C": 0, "D": 0},
        "gear_history": [],
        "skin": 0,
        "upa": {"all_pt": 0, "used_pt": 0, "skills": []},
        "aa": {"s1": 0, "s2": 0, "s3": 0, "s4": 0},
        "charm_set": {"unlocked": 1, "slots": []},
        "is_protected": False,
        "flags": 0,
        "created_at": now,
        "updated_at": now,
    }


def create_player(
    profile: str, player_code: str, masters: MasterData, config: dict, now: int
) -> dict:
    template = masters.table("composition_template")[0]
    rank = masters.table("player_rank_info_table")[0]
    capacity = rank["staminaCap"]
    stamina = (
        capacity
        if config["initial_stamina"] == "capacity"
        else config["initial_stamina"]
    )
    units = [
        unit(index, card_id, now)
        for index, card_id in enumerate(masters.property("IntArray", 1), 1)
    ]
    bom_row = bom(1, masters.property("Int", 5), now)
    slots = [
        json.dumps(
            {
                "t": 1,
                "sn": i,
                "iid": row["instance_id"],
                "sauto": True,
                "skillAuto": 1,
                "classActionAuto": 1,
                "sskills": {"s1": 0, "s2": 0},
                "exskill": 1,
            },
            separators=(",", ":"),
        )
        for i, row in enumerate(units)
    ]
    slots.append(json.dumps({"t": 2, "sn": 0, "iid": 1}, separators=(",", ":")))
    wallets = {"point.paid_coin": 0, **json.loads(masters.property("String", 1))}
    state = {
        "created_at": now,
        "player": {
            "player_code": player_code,
            "nickname": config["initial_nickname"],
            "tutorial": 0,
            "follows": 0,
            "towers": [],
        },
        "player_text": {"name": "", "bio": ""},
        "modules": {
            "stamina": [
                {
                    "section": "quest",
                    "amount": stamina,
                    "capacity": capacity,
                    "max_capacity": config["stamina_max_capacity"],
                    "recover_val": 1,
                    "recover_interval": masters.property("Int", 13),
                    "latest_recovered_at": now,
                }
            ],
            "experience": [
                {
                    "section": "player_rank",
                    "exp": 0,
                    "level": 1,
                    "level_cap": masters.property("Int", 6),
                }
            ],
            "composition": [
                {
                    "section": template["section"],
                    "capacity": template["init_composition_cpacity"],
                    "slot_capacity": template["init_slot_capacity"],
                }
            ],
            "farming": [
                {
                    "section": "basic",
                    "tier": 1,
                    "interval_min": config["farming_interval_min"],
                    "term_min": config["farming_term_min"],
                    "start_at": now,
                }
            ],
        },
        "units": units,
        "boms": [bom_row],
        "wallets": [
            {
                "category": rid.split(".", 1)[0],
                "name": rid.split(".", 1)[1],
                "val": amount,
                "revision": 1,
                "created_at": now,
            }
            for rid, amount in wallets.items()
        ],
        "compositions": [
            {
                "section": template["section"],
                "no": no,
                "label": f"チーム{no}",
                "slots": slots if no == 1 else [],
                "exdata": '{"bom_buf":true}',
            }
            for no in range(1, template["init_composition_cpacity"] + 1)
        ],
        "prefs": {
            "home_favorite": {
                "prefs_name": "home_favorite",
                "owner_domain": "player",
                "owner_id": player_code,
                "payload": json.dumps(
                    {
                        "fav_unit_card": units[0]["card_id"],
                        "rand": False,
                        "my_page_unit_cards": [row["card_id"] for row in units],
                        "home_prefs_ver": 2,
                    },
                    separators=(",", ":"),
                ),
            },
        },
        "rental_units": {"format_ver": 1, "s1": None, "s2": None, "s3": None},
        "home_visited_at": 0,
    }
    for key in (
        "items",
        "charms",
        "skins",
        "stories",
        "quest_records",
        "quest_secrets",
        "wild_hunt_ranks",
        "periods",
        "limits",
        "inbox",
        "follow",
        "follower",
        "bonus_passes",
        "gacha_stamp_records",
        "gacha_selection",
        "missions",
        "mission_counters",
        "flag_reminders",
        "vouchers",
        "sp_favor",
    ):
        state[key] = []
    for migrate in MIGRATIONS:
        migrate(state)
    return state
