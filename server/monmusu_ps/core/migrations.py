from .calendar import FOREVER
from .teams import snapshot


def add_battle_state(state: dict) -> None:
    state["quest_transactions"] = {}
    state["team_logs"] = {}
    state["events"] = []
    state["resource_sequences"] = {
        "unit": max((row["instance_id"] for row in state["units"]), default=0) + 1,
        "bom": max((row["instance_id"] for row in state["boms"]), default=0) + 1,
        "inbox": max((row["no"] for row in state["inbox"]), default=0) + 1,
    }
    state["boxgacha_records"] = []
    state["revisioned_reminders"] = []


def add_login_bonus_state(state: dict) -> None:
    state["login_bonus"] = []


def add_inbox_archive(state: dict) -> None:
    state["inbox_archive"] = []


def add_login_day(state: dict) -> None:
    state["last_login_day"] = 0


def add_gacha_history(state: dict) -> None:
    state["gacha_history"] = []


def drop_special_favors(state: dict) -> None:
    state.pop("sp_favor", None)


def add_serial_codes(state: dict) -> None:
    state["serial_codes"] = {"redeemed": [], "failures": 0, "failed_at": 0}


def add_battle_log_state(state: dict) -> None:
    state["shared_quest_logs"] = []
    state["reminder_seen"] = {}


def add_social_state(state: dict) -> None:
    for key in ("rental_units", "follow", "follower"):
        state.pop(key, None)
    state["lending"] = {"s1": 0, "s2": 0, "s3": 0}
    state["friend_points"] = {"day": 0, "greeted": 0, "collected_day": 0}
    state["last_login_at"] = 0
    reminders = state.setdefault("revisioned_reminders", [])
    for identity in (1, 2):
        if not any((row["group"], row["id"]) == (1, identity) for row in reminders):
            reminders.append({"group": 1, "id": identity, "rev": 0, "expire": FOREVER})


def add_team_snapshots(state: dict) -> None:
    state.pop("skins", None)
    ratings = {
        row["quest_id"]: row["record_rating"] for row in state.get("quest_records", ())
    }
    for entry in state.get("team_logs", {}).values():
        deck = entry.pop("statement")["use_deck"]
        entry.update(
            rating=ratings.get(entry["quest_id"], 0),
            txid="",
            team=snapshot(state, entry.pop("party"), deck, None),
        )
    for transaction in state.get("quest_transactions", {}).values():
        if transaction["status"] == "active":
            transaction.setdefault("support", None)
        else:
            for key in ("party", "support", "deck"):
                transaction.pop(key, None)


def add_monthly_benefits(state: dict) -> None:
    state["monthly_benefits"] = {"vip": 0, "dmm_premium": 0}


def add_campaign_records(state: dict) -> None:
    state.update(bingo={}, votes={}, mileage={})


def add_event_growth(state: dict) -> None:
    state["event_growth"] = {}


def add_quest_rule_state(state: dict) -> None:
    state.pop("wild_hunt_ranks", None)
    records = {row["quest_id"]: row for row in state.get("quest_records", [])}
    for transaction in state.get("quest_transactions", {}).values():
        if transaction["status"] != "active":
            continue
        record = records.get(transaction.get("quest", {}).get("id"), {})
        transaction.update(
            sub_skills=[],
            training=None,
            mode=0,
            raid=False,
            log_key=list(record.get("log_key", [])),
        )
    for entry in state.get("team_logs", {}).values():
        entry["wild_hunt_score"] = 0


def add_event_plans(state: dict) -> None:
    state.update(sealed_chara={}, subdue_raid={})


def add_sugoroku(state: dict) -> None:
    state["sugoroku"] = {"cleared": [], "session": None, "last_result": None}


MIGRATIONS = (
    add_battle_state,
    add_login_bonus_state,
    add_inbox_archive,
    add_login_day,
    add_gacha_history,
    drop_special_favors,
    add_serial_codes,
    add_battle_log_state,
    add_social_state,
    add_team_snapshots,
    add_monthly_benefits,
    add_campaign_records,
    add_event_growth,
    add_quest_rule_state,
    add_event_plans,
    add_sugoroku,
)
