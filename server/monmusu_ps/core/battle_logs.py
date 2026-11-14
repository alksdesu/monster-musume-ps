from copy import deepcopy
import json
from pathlib import Path
import secrets

from .calendar import FOREVER
from .errors import ApiNotFound
from .journal import BATTLE_LOG_SHARE
from .progression import player_name
from .store import write_json
from .sync import Result, diff_state


DAY = 86400
REMINDER_GROUP, REMINDER_ID = 2, 1
REMINDER_KEY = f"{REMINDER_GROUP}.{REMINDER_ID}"
SCOPES = FOLLOWERS, FRIENDS = "fwr", "fnd"
LIKE, UNLIKE = 1, 2
FULL_RATING, MAX_SIZE, COMMENT_LIMIT, SHARE_LIMIT = 41, 66, 73, 74
LOG_FIELDS = (
    "player_code",
    "log_file_name",
    "quest_id",
    "result",
    "rating",
    "defeat",
    "defeat_max",
    "wild_hunt_score",
    "training_rank",
    "team",
    "version",
    "fmt_ver",
    "created_at",
)


def _integer(value) -> bool:
    return type(value) is int


class Shelf:
    def __init__(self, root: Path):
        self.root = root
        self._rows: list[dict] | None = None

    def rows(self) -> list[dict]:
        if self._rows is None:
            index = self.root / "index.json"
            document = (
                json.loads(index.read_text(encoding="utf-8"))
                if index.exists()
                else {"logs": []}
            )
            self._rows = document["logs"]
        return self._rows

    def commit(self) -> None:
        write_json(self.root / "index.json", {"logs": self.rows()})

    def _file(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def add(self, row: dict, replay: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        # The client replays these bytes as they were uploaded.
        self._file(row["log_file_name"]).write_bytes(replay.encode("utf-8"))
        self.rows().append(row)

    def remove(self, rows: list[dict]) -> None:
        for row in rows:
            self.rows().remove(row)
            self._file(row["log_file_name"]).unlink(missing_ok=True)

    def replay(self, row: dict) -> str:
        return self._file(row["log_file_name"]).read_bytes().decode("utf-8")


class BattleLogs:
    def __init__(self, masters, config, shelf, store, graph, journal, public_url):
        self.masters = masters
        self.config = config
        self.shelf = shelf
        self.store = store
        self.graph = graph
        self.journal = journal
        self.public_url = public_url

    def _settings(self) -> dict:
        return self.config.read()["battle_log"]

    def _limit(self, key: int) -> int:
        return self.masters.property("Int", key)

    def _names(self, state: dict) -> dict[str, str]:
        names = {
            document["state"]["player"]["player_code"]: player_name(document["state"])
            for document in self.store.documents()
        }
        names[state["player"]["player_code"]] = player_name(state)
        return names

    def _sweep(self, now: int) -> None:
        lifetime = self._settings()["days"] * DAY
        stale = [
            row for row in self.shelf.rows() if row["created_at"] + lifetime <= now
        ]
        if stale:
            self.shelf.remove(stale)
            self.shelf.commit()

    def _listable(self, row: dict) -> bool:
        try:
            self.masters.lookup("quest_data", "id", row["quest_id"])
        except KeyError:
            return False
        return True

    def _own(self, code: str) -> list[dict]:
        return [
            row
            for row in self.shelf.rows()
            if row["player_code"] == code and self._listable(row)
        ]

    def _visible(self, viewer: str, row: dict) -> bool:
        owner = row["player_code"]
        if owner == viewer or not self._settings()["scoped_by_follow"]:
            return True
        if self.graph.edge(viewer, owner) is None:
            return False
        friends_only = row["share"]["sharing_scope"] == FRIENDS
        return not friends_only or self.graph.edge(owner, viewer) is not None

    def _shared(self, code: str) -> list[dict]:
        rows = [
            row
            for row in self.shelf.rows()
            if row["player_code"] != code
            and row["share"]
            and self._listable(row)
            and self._visible(code, row)
        ]
        return sorted(rows, key=lambda row: row["share"]["shared_at"], reverse=True)

    @staticmethod
    def _log(row: dict) -> dict:
        return {field: deepcopy(row[field]) for field in LOG_FIELDS}

    @staticmethod
    def _profile(row: dict, name: str) -> dict:
        return {
            "log_file_name": row["log_file_name"],
            "name": name,
            "like_count": len(row["likes"]),
            **row["share"],
        }

    @staticmethod
    def _battle(state: dict, quest_id: int) -> tuple[str, dict] | None:
        played = [
            (key, row)
            for key, row in state["quest_transactions"].items()
            if row.get("quest_id") == quest_id and "team" in row
        ]
        return played[-1] if played else None

    def _replay(self, text: str, quest_id: int) -> dict | None:
        if not text or len(text.encode("utf-8")) > self._limit(MAX_SIZE):
            return None
        try:
            replay = json.loads(text)
        except ValueError:
            return None
        if not isinstance(replay, dict) or replay.get("questId") != quest_id:
            return None
        return replay if _integer(replay.get("battleVersion")) else None

    def _trim(self, code: str) -> None:
        kept = sorted(self._own(code), key=lambda row: row["created_at"], reverse=True)
        self.shelf.remove(kept[self._settings()["keep"] :])

    def put(self, state: dict, payload: dict, now: int) -> Result:
        quest_id, text, key = (
            payload["quest_id"],
            payload["log_data"],
            payload["team_log_key"],
        )
        scores = {
            name: payload[name] for name in ("defeat", "defeat_max", "wild_hunt_score")
        }
        if (
            not _integer(quest_id)
            or not isinstance(text, str)
            or not (key is None or isinstance(key, str))
            or not all(_integer(value) for value in scores.values())
        ):
            raise ValueError("Malformed battle log")
        battle, replay = self._battle(state, quest_id), self._replay(text, quest_id)
        if battle is None or replay is None:
            return Result({"log_file_name": ""})
        self._sweep(now)
        code, (transaction_id, settled) = state["player"]["player_code"], battle
        saved = next(
            (
                row
                for row in self.shelf.rows()
                if (row["player_code"], row["txid"]) == (code, transaction_id)
            ),
            None,
        )
        if saved is not None:
            return Result({"log_file_name": saved["log_file_name"]})
        request = settled["completion_request"]
        rank, version = replay.get("tMenuRank"), replay.get("version")
        row = {
            "log_file_name": secrets.token_hex(16),
            "player_code": code,
            "txid": transaction_id,
            "quest_id": quest_id,
            "result": request["result"],
            "rating": request["statement"]["rating"],
            **scores,
            "training_rank": rank if _integer(rank) else 0,
            "team": deepcopy(settled["team"]),
            "version": replay["battleVersion"],
            "fmt_ver": version if isinstance(version, str) else None,
            "created_at": now,
            "share": None,
            "likes": [],
        }
        self.shelf.add(row, text)
        self._trim(code)
        self.shelf.commit()
        return Result({"log_file_name": row["log_file_name"]})

    def mine(self, state: dict, now: int) -> Result:
        self._sweep(now)
        rows, name = self._own(state["player"]["player_code"]), player_name(state)
        return Result(
            {
                "battle_logs": [self._log(row) for row in rows],
                "shared_log_profs": [
                    self._profile(row, name) for row in rows if row["share"]
                ],
            }
        )

    def _remind(self, state: dict, shared: list[dict]) -> dict | None:
        newest = max((row["share"]["shared_at"] for row in shared), default=0)
        revision = newest if newest > state["reminder_seen"].get(REMINDER_KEY, 0) else 0
        reminders = state["revisioned_reminders"]
        row = next(
            (
                row
                for row in reminders
                if (row["group"], row["id"]) == (REMINDER_GROUP, REMINDER_ID)
            ),
            None,
        )
        if row is None:
            if not revision:
                return None
            row = {
                "group": REMINDER_GROUP,
                "id": REMINDER_ID,
                "rev": 0,
                "expire": FOREVER,
            }
            reminders.append(row)
        if row["rev"] == revision:
            return None
        row["rev"] = revision
        return row

    def remind(self, state: dict, now: int) -> dict | None:
        self._sweep(now)
        return self._remind(state, self._shared(state["player"]["player_code"]))

    def feed(self, state: dict, now: int) -> Result:
        self._sweep(now)
        before = deepcopy(state)
        code = state["player"]["player_code"]
        shared = self._shared(code)
        self._remind(state, shared)
        rows, names = shared[: self._settings()["feed_limit"]], self._names(state)
        return Result(
            {
                "battle_logs": [self._log(row) for row in rows],
                "shared_log_profs": [
                    self._profile(row, names.get(row["player_code"], ""))
                    for row in rows
                ],
                "list_of_likes": [
                    row["log_file_name"] for row in rows if code in row["likes"]
                ],
            },
            diff_state(before, state),
        )

    def _owned(self, state: dict, name) -> dict:
        code = state["player"]["player_code"]
        row = next(
            (row for row in self._own(code) if row["log_file_name"] == name), None
        )
        if row is None:
            raise ApiNotFound(str(name))
        return row

    def locate(self, state: dict, payload: dict, now: int) -> Result:
        self._sweep(now)
        code, name = state["player"]["player_code"], payload["log_file_name"]
        known = {
            row["log_file_name"] for row in (*self._own(code), *self._shared(code))
        }
        if name not in known:
            raise ValueError(f"Unknown battle log: {name}")
        return Result({"log_url": f"{self.public_url}/battle_log/{name}"})

    def shared(self, viewer: str, owner: str, transaction_id: str) -> dict | None:
        return next(
            (
                row
                for row in self.shelf.rows()
                if (row["player_code"], row["txid"]) == (owner, transaction_id)
                and row["share"]
                and self._visible(viewer, row)
            ),
            None,
        )

    def download(self, name: str) -> str | None:
        row = next(
            (row for row in self.shelf.rows() if row["log_file_name"] == name), None
        )
        return None if row is None else self.shelf.replay(row)

    def like(self, state: dict, payload: dict, now: int) -> Result:
        orders = payload["battle_log_like_list"]
        if not isinstance(orders, list) or any(
            order["order"] not in (LIKE, UNLIKE) for order in orders
        ):
            raise ValueError("Likes are ordered with 1 and withdrawn with 2")
        self._sweep(now)
        code = state["player"]["player_code"]
        shared = {row["log_file_name"]: row for row in self._shared(code)}
        changed = False
        for order in orders:
            # A log withdrawn while its window stayed open is no longer an error.
            row = shared.get(order["log_file_name"])
            liked = order["order"] == LIKE
            if row is None or (code in row["likes"]) == liked:
                continue
            if liked:
                row["likes"].append(code)
            else:
                row["likes"].remove(code)
            changed = True
        if changed:
            self.shelf.commit()
        return Result()

    def _comment(self, comment) -> str:
        if comment is None:
            return ""
        if not isinstance(comment, str):
            raise ValueError("comment must be a string")
        comment = comment.replace("\r", "").replace("\n", "")
        if len(comment) > self._limit(COMMENT_LIMIT):
            raise ValueError("comment is too long")
        return comment

    def share(self, state: dict, payload: dict, now: int) -> Result:
        self._sweep(now)
        row = self._owned(state, payload["log_file_name"])
        scope, comment = payload["sharing_scope"], self._comment(payload["comment"])
        if scope not in SCOPES:
            raise ValueError(f"Unknown sharing scope: {scope}")
        before = deepcopy(state)
        mastered = (row["result"], row["rating"]) == (
            "COMPLETE",
            self._limit(FULL_RATING),
        )
        if mastered:
            shared = state["shared_quest_logs"]
            if row["quest_id"] not in shared:
                shared.append(row["quest_id"])
            self.journal.record(
                state,
                f"battlelog-{row['log_file_name']}",
                BATTLE_LOG_SHARE,
                now,
                {"quest_id": row["quest_id"]},
            )
        earlier = sorted(
            (
                other
                for other in self._own(row["player_code"])
                if other["share"] and other is not row
            ),
            key=lambda other: other["share"]["shared_at"],
        )
        # The client drops its oldest shared log at the same limit without asking.
        for other in earlier[: max(0, len(earlier) + 1 - self._limit(SHARE_LIMIT))]:
            other.update(share=None, likes=[])
        row["share"] = {"sharing_scope": scope, "comment": comment, "shared_at": now}
        self.shelf.commit()
        return Result(sync=diff_state(before, state))

    def unshare(self, state: dict, payload: dict, now: int) -> Result:
        self._sweep(now)
        row = self._owned(state, payload["log_file_name"])
        if row["share"]:
            row.update(share=None, likes=[])
            self.shelf.commit()
        return Result()
