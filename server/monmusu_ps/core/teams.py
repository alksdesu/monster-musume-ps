from collections import Counter
import json

from . import cards
from .progression import player_name


def team_log_key(quest_id: int, player_code: str) -> str:
    return f"quest.{quest_id}#{player_code}"


def _fielded(unit: dict, slot: dict | None) -> dict:
    if slot is None:
        return unit
    # In battle the deck slot's skills stand in for the unit's own.
    skills = slot.get("sskills") or {}
    worn = {"s1": skills.get("s1", 0), "s2": skills.get("s2", 0), "s3": 0, "s4": 0}
    return {
        **unit,
        "primary_exskill": slot.get("exskill", unit["primary_exskill"]),
        "aa": worn,
    }


def snapshot(state: dict, party: list[int], deck: dict, support: dict | None) -> dict:
    units = {row["instance_id"]: row for row in state["units"]}
    boms = {row["instance_id"]: row for row in state["boms"]}
    slots = [
        json.loads(raw)
        for row in state["compositions"]
        if (row["section"], row["no"]) == (deck["section"], deck["no"])
        for raw in row["slots"]
    ]
    fielded = {slot["iid"]: slot for slot in slots if slot["t"] == 1}
    members = [
        (units[identity], fielded.get(identity))
        for identity in party
        if identity in units
    ]
    return {
        "format_ver": 1,
        "units": [
            {"slot": index, "unit": cards.text(_fielded(unit, slot))}
            for index, (unit, slot) in enumerate(members)
        ],
        "boms": [
            {"slot": slot["sn"], "bom": cards.text(boms[slot["iid"]])}
            for slot in slots
            if slot["t"] == 2 and slot["iid"] in boms
        ],
        "rental": cards.text(support["unit"]) if support else None,
    }


class ClearTeams:
    def __init__(self, masters, store, battle_logs):
        self.masters = masters
        self.store = store
        self.battle_logs = battle_logs

    def _entries(self, state: dict, quest_id) -> list[tuple[dict, dict]]:
        if type(quest_id) is not int:
            raise ValueError("quest_id must be an integer")
        saves = {
            document["state"]["player"]["player_code"]: document["state"]
            for document in self.store.documents()
        }
        saves[state["player"]["player_code"]] = state
        full, hunt = self.masters.property("Int", 41), self._hunt(quest_id)
        cleared = [
            (owner, entry)
            for code, owner in saves.items()
            if (entry := owner["team_logs"].get(team_log_key(quest_id, code)))
            # A hunt always ends in victory, so its teams rank by score instead.
            and (hunt or entry["rating"] == full)
        ]
        return sorted(
            cleared,
            key=lambda pair: (pair[1]["wild_hunt_score"], pair[1]["created_at"]),
            reverse=True,
        )

    def _hunt(self, quest_id: int) -> bool:
        try:
            quest = self.masters.lookup("quest_data", "id", quest_id)
        except KeyError:
            return False
        return bool(quest["isWildHunt"])

    def teams(self, state: dict, payload: dict) -> dict:
        rows, viewer = [], state["player"]["player_code"]
        for number, (owner, entry) in enumerate(
            self._entries(state, payload["quest_id"]), 1
        ):
            code = owner["player"]["player_code"]
            shared = self.battle_logs.shared(viewer, code, entry["txid"])
            rows.append(
                {
                    "no": number,
                    "name": player_name(owner),
                    "player_code": code,
                    "log_file_name": shared["log_file_name"] if shared else "",
                    "log_sharing_scope": shared["share"]["sharing_scope"]
                    if shared
                    else None,
                    "team": entry["team"],
                    "wild_hunt_score": entry["wild_hunt_score"],
                    "created_at": entry["created_at"],
                }
            )
        return {"team_logs": rows}

    def usage(self, state: dict, payload: dict) -> dict:
        entries = self._entries(state, payload["quest_id"])
        fielded: Counter = Counter()
        for _, entry in entries:
            fielded.update(
                {json.loads(slot["unit"])["card_id"] for slot in entry["team"]["units"]}
            )
        ranked = sorted(fielded.items(), key=lambda item: (-item[1], item[0]))
        return {
            "ranks": [
                {
                    "rank": rank,
                    "unit_card": f"unit.card.{card_id}",
                    # The client shows this as a percentage with two decimals.
                    "ratio": round(teams * 10000 / len(entries)),
                }
                for rank, (card_id, teams) in enumerate(ranked, 1)
            ]
        }
