from .calendar import FOREVER
from .errors import ApiRejected
from .journal import LOGIN
from .quest_gates import quest_record
from .resources import ResourceCapacityError


LEVEL, EXSKILL, AWAKENING = 1, 2, 3
REMINDER_GROUP = 3
UNIT_PREFIX = "unit.card."
SOURCE = "event.sealed_chara"
INVALID_EVENT, OUT_OF_SCHEDULE = "INVALID_EVENT_ID", "OUT_OF_SCHEDULE"
NOT_ENOUGH_COST, DISPROP_MOD = "NOT_ENOUGH_COST", "DISPROP_MOD"
NOT_QUALIFIED, LOCKED = "NOT_QUALIFIED_EVENT", "NOT_QUALIFIED_TERMS_OF_MOD"


class SealedCharacters:
    def __init__(self, masters, config, resources, boosts, journal):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.boosts = boosts
        journal.subscribe(LOGIN, self._on_login)

    def _event(self, event_id, now: int) -> tuple[dict, dict]:
        if type(event_id) is not int:
            raise ValueError("event_id must be an integer")
        try:
            props = self.masters.lookup(
                "event_sealed_chara_props", "event_id", event_id
            )
            event = self.masters.lookup("event_list_data", "id", event_id)
        except KeyError as error:
            raise ApiRejected(INVALID_EVENT) from error
        term = event["open_time"], event["close_time"]
        scheduled = self.config.read()["sealed_chara"]["follow_master_schedule"]
        if scheduled and not self.masters.in_term(*term, now):
            raise ApiRejected(OUT_OF_SCHEDULE)
        return props, event

    def _steps(self, event_id: int) -> dict[int, dict[int, dict]]:
        steps: dict[int, dict[int, dict]] = {}
        for row in self.masters.table("event_sealed_chara_mods"):
            if row["event_id"] == event_id:
                steps.setdefault(row["mod_type"], {})[row["no"]] = row
        return steps

    def _points(self, state: dict, props: dict, event: dict) -> int:
        earned = {star: props[f"rank{star}_pt"] for star in (1, 2, 3)}
        return sum(
            earned.get(record["record_rating"], 0)
            for quest in self.masters.table("quest_data")
            if quest["chapter"] == event["chapter"]
            and (record := quest_record(state, quest["id"])) is not None
        )

    @staticmethod
    def _cap(state: dict, rows: dict[int, dict]) -> int:
        def released(row: dict) -> bool:
            required = row["release_terms_quest_id"]
            record = quest_record(state, required) if required else None
            return not required or (record is not None and record["wins"] > 0)

        return max((number for number, row in rows.items() if released(row)), default=0)

    def _record(self, state: dict, event: dict, plan: dict, acquired: int) -> dict:
        return {
            "event_id": event["id"],
            "acquired_pt": acquired,
            # The client needs exactly one row for every kind of enhancement.
            "mods": [
                {
                    "type": kind,
                    "activated_no": plan["mods"].get(str(kind), 0),
                    "activation_cap_no": self._cap(state, rows),
                }
                for kind, rows in self._steps(event["id"]).items()
            ],
            "is_entered": True,
        }

    def confirm(self, state: dict, payload: dict, now: int) -> dict:
        props, event = self._event(payload["event_id"], now)
        plan = state["sealed_chara"].setdefault(
            str(event["id"]), {"mods": {}, "confirmed_pt": 0, "delivered": False}
        )
        acquired = self._points(state, props, event)
        income = acquired - plan["confirmed_pt"]
        plan["confirmed_pt"] = acquired
        return {
            "income_pt": income,
            "event_record": self._record(state, event, plan, acquired),
        }

    @staticmethod
    def _chosen(mods, steps: dict[int, dict[int, dict]]) -> dict[int, int]:
        chosen: dict[int, int] = {}
        for mod in mods if isinstance(mods, list) else [None]:
            kind = mod.get("type") if isinstance(mod, dict) else None
            number = mod.get("no") if isinstance(mod, dict) else None
            if (
                type(kind) is not int
                or type(number) is not int
                or kind not in steps
                or kind in chosen
                or (number and number not in steps[kind])
            ):
                raise ApiRejected(DISPROP_MOD)
            chosen[kind] = number
        return chosen

    def modify(self, state: dict, payload: dict, now: int) -> dict:
        props, event = self._event(payload["event_id"], now)
        steps = self._steps(event["id"])
        chosen = self._chosen(payload["mods"], steps)
        plan = state["sealed_chara"].get(str(event["id"]))
        acquired = self._points(state, props, event)
        if plan is None or not acquired:
            raise ApiRejected(NOT_QUALIFIED)
        if plan["delivered"]:
            raise ApiRejected(OUT_OF_SCHEDULE)
        if any(
            number > self._cap(state, steps[kind]) for kind, number in chosen.items()
        ):
            raise ApiRejected(LOCKED)
        mods = {
            **plan["mods"],
            **{str(kind): number for kind, number in chosen.items()},
        }
        spent = sum(
            steps[int(kind)][number]["activate_total_cost"]
            for kind, number in mods.items()
            if number
        )
        if spent > acquired:
            raise ApiRejected(NOT_ENOUGH_COST)
        plan["mods"] = mods
        return {"event_record": self._record(state, event, plan, acquired)}

    def _enhance(self, state: dict, unit: dict, event_id: int, plan: dict, now: int):
        steps = self._steps(event_id)
        for kind, reached in plan["mods"].items():
            taken = [
                row for number, row in steps[int(kind)].items() if number <= reached
            ]
            if not taken:
                continue
            value = max(row["mod_val"] for row in taken)
            if int(kind) == LEVEL:
                # Class changes come first: each one raises the level the unit can hold.
                self.boosts.promote(unit, max(row["mod_val2"] for row in taken))
                self.boosts.level(state, unit, value, now)
            elif int(kind) == EXSKILL:
                self.boosts.exskill(unit, value)
            elif int(kind) == AWAKENING:
                self.boosts.awaken(unit, value)
        unit["updated_at"] = now

    def _deliver(self, state: dict, event_id: int, plan: dict, now: int) -> None:
        props = self.masters.lookup("event_sealed_chara_props", "event_id", event_id)
        card = props["reward_card_id"]
        order = {"rid": f"{UNIT_PREFIX}{card}", "val": 1}
        try:
            self.resources.grant(state, [order], now, source=f"{SOURCE}:{event_id}")
        except ResourceCapacityError:
            return
        unit = next(row for row in state["units"] if row["card_id"] == card)
        self._enhance(state, unit, event_id, plan, now)
        plan["delivered"] = True
        # The client loops forever on this reminder unless the unit is already held.
        reminder = {"group": REMINDER_GROUP, "id": event_id, "expire": FOREVER}
        if reminder not in state["flag_reminders"]:
            state["flag_reminders"].append(reminder)

    def _on_login(self, state: dict, event: dict) -> None:
        now = event["at"]
        for identity, plan in state["sealed_chara"].items():
            closing = self.masters.lookup("event_list_data", "id", int(identity))[
                "close_time"
            ]
            if plan["delivered"] or not closing:
                continue
            if now >= self.masters.timestamp(closing):
                self._deliver(state, int(identity), plan, now)
