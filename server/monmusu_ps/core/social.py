from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re

from . import cards
from .calendar import FOREVER
from .errors import ApiNotFound, ApiRejected
from .initial import unit as unit_row
from .journal import QUEST_COMPLETE, SOCIAL_FOLLOW, SOCIAL_LENDING
from .progression import player_name, player_rank
from .store import write_json
from .sync import Result, diff_state


DAY = 86400
CODE = re.compile(r"[A-Z0-9]{8}\Z")
FRIEND_POINT = "item.friend_point.friend_pt1"
GROUP = 1
FOLLOWER, MUTUAL = 1, 2
SLOTS = ("s1", "s2", "s3")
RENTED, RENTED_CAP = 79, 80
GREETING, GREETING_CAP = 81, 82
GREETED, GREETED_CAP = 83, 84
NO_SUPPORT_CATEGORIES = (5, 8)
REACHED_LIMIT, UNFOLLOWABLE = "FOLLOWS_IS_REACHED_LIMIT", "UNFOLLOWABLE_TARGET"
INVALID_STATEMENT = "INVALID_STATEMENT"


def _share() -> dict:
    return {"count": 0, "point": 0}


def _tally(day: int) -> dict:
    return {"day": day, "greeted": _share(), "rented": _share()}


def _summary(point: int, count: int, cap: int) -> dict:
    return {"point": point, "count": count, "is_max": point >= cap}


class SocialGraph:
    def __init__(self, path: Path):
        self.path = path
        self._document: dict | None = None

    def _data(self) -> dict:
        if self._document is None:
            self._document = (
                json.loads(self.path.read_text(encoding="utf-8"))
                if self.path.exists()
                else {"follows": [], "tallies": {}}
            )
        return self._document

    def commit(self) -> None:
        write_json(self.path, self._data())

    def reload(self) -> None:
        self._document = None

    def edges(self) -> list[dict]:
        return self._data()["follows"]

    def edge(self, source: str, target: str) -> dict | None:
        return next(
            (
                row
                for row in self.edges()
                if (row["from"], row["to"]) == (source, target)
            ),
            None,
        )

    def following(self, code: str) -> list[dict]:
        return [row for row in self.edges() if row["from"] == code]

    def followers(self, code: str) -> list[dict]:
        return [row for row in self.edges() if row["to"] == code]

    def link(self, source: str, target: str, now: int) -> dict:
        row = self.edge(source, target)
        if row is None:
            row = {"from": source, "to": target, "linked_at": now, "greeting_at": 0}
            self.edges().append(row)
        return row

    def unlink(self, source: str, target: str) -> bool:
        row = self.edge(source, target)
        if row is None:
            return False
        self.edges().remove(row)
        return True

    def tally(self, code: str, day: int) -> dict:
        rows = self._data()["tallies"].setdefault(code, [])
        row = next((row for row in rows if row["day"] == day), None)
        if row is None:
            row = _tally(day)
            rows.append(row)
        return row

    def settle(self, code: str, before: int) -> list[dict]:
        tallies = self._data()["tallies"]
        rows = tallies.get(code, [])
        settled = [row for row in rows if row["day"] < before]
        remaining = [row for row in rows if row["day"] >= before]
        if remaining:
            tallies[code] = remaining
        else:
            tallies.pop(code, None)
        return settled


@dataclass
class Person:
    code: str
    name: str
    bio: str
    rank: int
    fav_unit_card: int
    fav_unit_skin: int
    login_at: int
    units: dict[int, dict]
    lending: tuple[int, int, int]
    habits: dict

    def lent(self) -> list[dict]:
        return [self.units[iid] for iid in self.lending if iid in self.units]


class Social:
    def __init__(
        self, masters, config, roster, calendar, resources, store, journal, graph
    ):
        self.masters = masters
        self.config = config
        self.roster = roster
        self.calendar = calendar
        self.resources = resources
        self.store = store
        self.journal = journal
        self.graph = graph
        journal.subscribe(QUEST_COMPLETE, self._on_quest_complete)

    def _settings(self) -> dict:
        return self.config.read()["social"]

    def _limit(self, key: int) -> int:
        return self.masters.property("Int", key)

    @staticmethod
    def _code(state: dict) -> str:
        return state["player"]["player_code"]

    @staticmethod
    def _favorite(state: dict) -> int:
        payload = json.loads(state["prefs"]["home_favorite"]["payload"])
        return payload["fav_unit_card"]

    def _saved(self, state: dict) -> Person:
        return Person(
            self._code(state),
            player_name(state),
            state["player_text"]["bio"],
            player_rank(state),
            self._favorite(state),
            0,
            state["last_login_at"],
            {row["instance_id"]: row for row in state["units"]},
            tuple(state["lending"][slot] for slot in SLOTS),
            {},
        )

    def _rank_for(self, level: int) -> int:
        base = self.masters.lookup("unit_rarity", "idName", "R")["maxLevel"]
        for row in self.masters.table("unit_rank_info_table"):
            if base + row["addMaxLevel"] >= level:
                return row["rank"]
        raise ValueError(f"Roster unit level is beyond every rank: {level}")

    def _roster_unit(self, index: int, spec: dict, now: int) -> dict:
        card = self.masters.asset("unitcard", spec["card_id"])
        thresholds = next(
            row["table"]
            for row in self.masters.document("unit_level_exp_table")["tables"]
            if row["rarityId"] == card["rarityId"]
        )
        level = spec["level"]
        row = unit_row(index, spec["card_id"], now)
        row.update(
            t_exp=thresholds[level - 1],
            r_unit_lv=level,
            rank=spec.get("rank", self._rank_for(level)),
        )
        return row

    def _listed(self, spec: dict, now: int) -> Person:
        login_at = now - spec["login_minutes_ago"] * 60
        units = {
            index: self._roster_unit(index, unit, login_at)
            for index, unit in enumerate(spec["units"][: len(SLOTS)], 1)
        }
        lending = tuple(
            units.get(index, {}).get("instance_id", 0) for index in (1, 2, 3)
        )
        return Person(
            spec["player_code"],
            spec["name"],
            spec["bio"],
            spec["rank"],
            spec["fav_unit_card"],
            spec.get("fav_unit_skin", 0),
            login_at,
            units,
            lending,
            {key: spec.get(key, False) for key in ("follow_back", "greets", "rents")},
        )

    def _people(self, state: dict, now: int) -> dict[str, Person]:
        people = {}
        for spec in self.roster.read()["players"]:
            if not CODE.fullmatch(spec["player_code"]):
                raise ValueError(
                    f"Roster code is not 8 capitals: {spec['player_code']}"
                )
            people[spec["player_code"]] = self._listed(spec, now)
        for document in self.store.documents():
            person = self._saved(document["state"])
            people[person.code] = person
        me = self._saved(state)
        people[me.code] = me
        return people

    def _person(self, state: dict, code, now: int) -> Person | None:
        if not isinstance(code, str) or not CODE.fullmatch(code):
            return None
        return self._people(state, now).get(code)

    @staticmethod
    def _profile(person: Person, followers: int) -> dict:
        return {
            "format_ver": 1,
            "player_code": person.code,
            "rank": person.rank,
            "fav_unit_card": person.fav_unit_card,
            "fav_unit_skin": person.fav_unit_skin,
            "followers": followers,
            "login_at": person.login_at,
        }

    @staticmethod
    def _lending(person: Person) -> dict:
        return {
            "format_ver": 1,
            **{
                slot: cards.text(person.units[iid]) if iid in person.units else None
                for slot, iid in zip(SLOTS, person.lending)
            },
        }

    def _info(self, viewer: str, person: Person) -> dict:
        forward = self.graph.edge(viewer, person.code)
        backward = self.graph.edge(person.code, viewer)
        linked = [row["linked_at"] for row in (forward, backward) if row is not None]
        return {
            "text": {"name": person.name, "bio": person.bio},
            "profile": self._profile(person, len(self.graph.followers(person.code))),
            "rental_units": self._lending(person),
            "is_follower": backward is not None,
            "is_following": forward is not None,
            "linked_at": max(linked, default=0),
            "greeting_at": forward["greeting_at"] if forward else 0,
        }

    def _infos(self, state: dict, codes: list[str], now: int) -> list[dict]:
        people, me = self._people(state, now), self._code(state)
        return [self._info(me, people[code]) for code in codes if code in people]

    def _strangers(self, state: dict, now: int) -> list[Person]:
        me = self._code(state)
        people = [
            person
            for person in self._people(state, now).values()
            if person.code != me and self.graph.edge(me, person.code) is None
        ]
        return sorted(people, key=lambda person: person.login_at, reverse=True)

    def following(self, state: dict, now: int) -> list[dict]:
        rows = self.graph.following(self._code(state))
        return self._infos(state, [row["to"] for row in rows], now)

    def followers(self, state: dict, now: int) -> list[dict]:
        rows = self.graph.followers(self._code(state))
        return self._infos(state, [row["from"] for row in rows], now)

    def follower_count(self, state: dict) -> int:
        return len(self.graph.followers(self._code(state)))

    def friend_point(self, state: dict) -> int:
        return self.resources.balance(state, FRIEND_POINT)

    def recommended(self, state: dict, now: int) -> list[dict]:
        me, limit = self._code(state), self._settings()["recommend_limit"]
        return [
            self._info(me, person) for person in self._strangers(state, now)[:limit]
        ]

    def search(self, state: dict, code, now: int) -> dict | None:
        person = self._person(state, code, now)
        if person is None or person.code == self._code(state):
            return None
        return self._info(self._code(state), person)

    def _follow_cap(self, state: dict) -> int:
        ranks = self.masters.table("player_rank_info_table")
        return ranks[player_rank(state) - 1]["followCap"]

    def _reminder(self, state: dict, identity: int, times: list[int]) -> dict:
        seen = state["reminder_seen"].get(f"{GROUP}.{identity}", 0)
        rows = state["revisioned_reminders"]
        row = next(
            (row for row in rows if (row["group"], row["id"]) == (GROUP, identity)),
            None,
        )
        if row is None:
            row = {"group": GROUP, "id": identity, "rev": 0, "expire": FOREVER}
            rows.append(row)
        # The client lights the badge while rev <= the newest unseen link time.
        row["rev"] = min((moment for moment in times if moment > seen), default=0)
        return row

    def remind(self, state: dict, now: int) -> list[dict]:
        me = self._code(state)
        followers = self.graph.followers(me)
        mutual = [
            max(row["linked_at"], forward["linked_at"])
            for row in followers
            if (forward := self.graph.edge(me, row["from"])) is not None
        ]
        return [
            self._reminder(state, FOLLOWER, [row["linked_at"] for row in followers]),
            self._reminder(state, MUTUAL, mutual),
        ]

    def follow(self, state: dict, code, now: int) -> Result:
        me = self._code(state)
        if code == me:
            raise ApiRejected(UNFOLLOWABLE)
        person = self._person(state, code, now)
        if person is None:
            raise ApiNotFound(f"player.{code}")
        before = deepcopy(state)
        if self.graph.edge(me, code) is None:
            if len(self.graph.following(me)) >= self._follow_cap(state):
                raise ApiRejected(REACHED_LIMIT)
            self.graph.link(me, code, now)
            if person.habits.get("follow_back"):
                self.graph.link(code, me, now)
            self.graph.commit()
            state["player"]["follows"] = len(self.graph.following(me))
            self.journal.record(
                state, f"follow-{code}-{now}", SOCIAL_FOLLOW, now, {"player_code": code}
            )
        self.remind(state, now)
        return Result(
            {"latest_data": self._info(me, person)}, diff_state(before, state)
        )

    def unfollow(self, state: dict, code, now: int) -> Result:
        me = self._code(state)
        before = deepcopy(state)
        if self.graph.unlink(me, code):
            self.graph.commit()
            state["player"]["follows"] = len(self.graph.following(me))
        self.remind(state, now)
        return Result(sync=diff_state(before, state))

    def _ledger(self, state: dict, now: int) -> dict:
        ledger, today = state["friend_points"], self.calendar.day_start(now)
        if ledger["day"] != today:
            ledger.update(day=today, greeted=0)
        return ledger

    def _credit(self, share: dict, per: int, cap: int) -> None:
        share["count"] += 1
        share["point"] = min(cap, share["point"] + per)

    def greet(self, state: dict, codes, now: int) -> Result:
        if not isinstance(codes, list) or not all(isinstance(c, str) for c in codes):
            raise ValueError("player_codes must be a list of player codes")
        before = deepcopy(state)
        me, today = self._code(state), self.calendar.day_start(now)
        ledger = self._ledger(state, now)
        per, cap = self._limit(GREETING), self._limit(GREETING_CAP)
        items, earned = [], 0
        for code in dict.fromkeys(codes):
            edge = self.graph.edge(me, code)
            if edge is None or (
                edge["greeting_at"]
                and self.calendar.day(edge["greeting_at"]) == self.calendar.day(now)
            ):
                continue
            edge["greeting_at"] = now
            items.append({"player_code": code, "greeting_at": now})
            gain = min(per, cap - ledger["greeted"])
            ledger["greeted"] += gain
            earned += gain
            self._credit(
                self.graph.tally(code, today)["greeted"],
                self._limit(GREETED),
                self._limit(GREETED_CAP),
            )
        if items:
            self.graph.commit()
        if earned:
            self.resources.grant(
                state, [{"rid": FRIEND_POINT, "val": earned}], now, policy="discard"
            )
        return Result(
            {
                "items": items,
                "follow": _summary(earned, len(items), cap)
                | {"is_max": ledger["greeted"] >= cap},
                "friend_point": self.friend_point(state),
            },
            diff_state(before, state),
        )

    def lending(self, state: dict) -> dict:
        return self._lending(self._saved(state))

    def set_lending(self, state: dict, payload: dict, now: int) -> Result:
        slots = payload["rental_units"]
        values = [slots[slot] for slot in SLOTS]
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("Lending slots must be unit instance ids or zero")
        chosen = [value for value in values if value]
        owned = {row["instance_id"] for row in state["units"]}
        if len(chosen) != len(set(chosen)) or not set(chosen) <= owned:
            raise ValueError("Lending slots must hold distinct owned units")
        before = deepcopy(state)
        state["lending"] = dict(zip(SLOTS, values))
        if chosen:
            self.journal.record(
                state, f"lending-{now}", SOCIAL_LENDING, now, {"instance_ids": chosen}
            )
        return Result(sync=diff_state(before, state))

    def selects(self, state: dict, now: int) -> dict:
        owners = [person for person in self._strangers(state, now) if person.lent()]
        owners = owners[: self._settings()["selects_limit"]]
        return {
            "rental_units": [
                {"player_code": person.code, "format_ver": 1, "s": cards.text(row)}
                for person in owners
                for row in person.lent()
            ],
            "rental_owners": [
                self._profile(person, len(self.graph.followers(person.code)))
                for person in owners
            ],
            "owner_texts": [
                {"name": person.name, "bio": person.bio} for person in owners
            ],
        }

    def supporter(self, state: dict, quest: dict, statement: dict, now: int):
        owner, iid = statement["rental_unit"], statement["rental_unit_iid"]
        if owner is None:
            if iid:
                raise ApiRejected(INVALID_STATEMENT)
            return None
        if quest["category"] in NO_SUPPORT_CATEGORIES or type(iid) is not int:
            raise ApiRejected(INVALID_STATEMENT)
        person = self._person(state, owner, now)
        if (
            person is None
            or person.code == self._code(state)
            or iid not in person.lending
            or iid not in person.units
        ):
            raise ApiRejected(INVALID_STATEMENT)
        return {"owner": owner, "instance_id": iid, "unit": deepcopy(person.units[iid])}

    def _on_quest_complete(self, state: dict, event: dict) -> None:
        owner = event["data"].get("support_owner")
        if event["data"]["result"] != "COMPLETE" or not owner:
            return
        day = self.calendar.day_start(event["at"])
        self._credit(
            self.graph.tally(owner, day)["rented"],
            self._limit(RENTED),
            self._limit(RENTED_CAP),
        )
        self.graph.commit()

    def _habits(self, state: dict, day: int, now: int) -> dict:
        me, tally = self._code(state), _tally(day)
        lending = bool(self._saved(state).lent())
        for spec in self.roster.read()["players"]:
            edge = self.graph.edge(spec["player_code"], me)
            if edge is None or edge["linked_at"] >= day + DAY:
                continue
            if spec.get("greets"):
                self._credit(
                    tally["greeted"], self._limit(GREETED), self._limit(GREETED_CAP)
                )
            if spec.get("rents") and lending:
                self._credit(
                    tally["rented"], self._limit(RENTED), self._limit(RENTED_CAP)
                )
        return tally

    def collect(self, state: dict, now: int) -> dict:
        me, today = self._code(state), self.calendar.day_start(now)
        ledger = state["friend_points"]
        greeted, rented = _share(), _share()
        caps = {"greeted": self._limit(GREETED_CAP), "rented": self._limit(RENTED_CAP)}
        maxed = {"greeted": False, "rented": False}
        if ledger["collected_day"] != today:
            ledger["collected_day"] = today
            yesterday = today - DAY
            settled = self.graph.settle(me, today)
            habits = self._habits(state, yesterday, now)
            days = {row["day"]: row for row in settled}
            if habits["greeted"]["count"] or habits["rented"]["count"]:
                row = days.setdefault(yesterday, _tally(yesterday))
                for kind in ("greeted", "rented"):
                    row[kind]["count"] += habits[kind]["count"]
                    row[kind]["point"] = min(
                        caps[kind], row[kind]["point"] + habits[kind]["point"]
                    )
            for row in days.values():
                for kind, share in (("greeted", greeted), ("rented", rented)):
                    share["count"] += row[kind]["count"]
                    share["point"] += row[kind]["point"]
                    maxed[kind] |= row[kind]["point"] >= caps[kind]
            if settled:
                self.graph.commit()
            total = greeted["point"] + rented["point"]
            if total:
                self.resources.grant(
                    state, [{"rid": FRIEND_POINT, "val": total}], now, policy="discard"
                )
        return {
            "friend_point": self.friend_point(state),
            "rental": {**rented, "is_max": maxed["rented"]},
            "follower": {**greeted, "is_max": maxed["greeted"]},
        }
