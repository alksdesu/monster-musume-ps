import hashlib
import hmac
import json
from pathlib import Path
import re

from .errors import ApiRejected
from .progression import player_name, player_rank
from .sessions import Session
from .store import write_json


PASSWORD = re.compile(r"[0-9a-zA-Z]{8,12}\Z")
DMM_PREFIX = "dmm:"
INVALID, OWN, EXPIRED = "INVALID_INPUT_PARAMS", "TARGET_OWN", "PASSWORD_EXPIRED"
LINKED = "ALREADY_LINKED_DMM"


def _digest(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


class DataLinks:
    def __init__(self, path: Path, config, store):
        self.path = path
        self.config = config
        self.store = store

    def _read(self) -> dict:
        if not self.path.exists():
            return {"passwords": {}, "bindings": {}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def resolve(self, login: str) -> str:
        binding = self._read()["bindings"].get(login)
        return binding["profile"] if binding else login

    def bindings(self) -> dict[str, str]:
        ledger = self._read()["bindings"]
        return {login: binding["profile"] for login, binding in ledger.items()}

    @staticmethod
    def _rebind(ledger: dict, login: str, target: str, previous: str, now: int) -> None:
        if target == login:
            ledger["bindings"].pop(login, None)
        else:
            ledger["bindings"][login] = {
                "profile": target,
                "previous": previous,
                "linked_at": now,
            }

    def bind(self, login: str, profile: str, now: int) -> None:
        ledger = self._read()
        self._rebind(ledger, login, profile, self.resolve(login), now)
        write_json(self.path, ledger)

    def issue(self, state: dict, payload: dict, now: int) -> None:
        password = payload["password"]
        if not isinstance(password, str) or not PASSWORD.match(password):
            raise ValueError("A link password is 8 to 12 ASCII letters and digits")
        ledger = self._read()
        ledger["passwords"][state["player"]["player_code"]] = {
            "hash": _digest(password),
            "issued_at": now,
        }
        write_json(self.path, ledger)

    @staticmethod
    def _dmm_bound(ledger: dict, profile: str) -> bool:
        bindings = ledger["bindings"]
        return (profile.startswith(DMM_PREFIX) and profile not in bindings) or any(
            login.startswith(DMM_PREFIX) and binding["profile"] == profile
            for login, binding in bindings.items()
        )

    def _target(self, ledger: dict, session: Session, payload: dict, now: int) -> dict:
        code, password = payload["player_code"], payload["password"]
        issued = ledger["passwords"].get(code) if isinstance(code, str) else None
        document = next(
            (
                document
                for document in self.store.documents()
                if document["state"]["player"]["player_code"] == code
            ),
            None,
        )
        if (
            issued is None
            or document is None
            or not isinstance(password, str)
            or not hmac.compare_digest(issued["hash"], _digest(password))
        ):
            raise ApiRejected(INVALID)
        if document["profile"] == session.profile:
            raise ApiRejected(OWN)
        settings = self.config.read()["data_link"]
        if now > issued["issued_at"] + settings["password_ttl_seconds"]:
            raise ApiRejected(EXPIRED)
        if not settings["allow_linked_target"] and self._dmm_bound(
            ledger, document["profile"]
        ):
            raise ApiRejected(LINKED)
        return document

    def confirm(self, session: Session, payload: dict, now: int) -> dict:
        try:
            target = self._target(self._read(), session, payload, now)["state"]
        except ApiRejected as error:
            # The client words the other refusals itself when they come with a 200.
            if error.reason == LINKED:
                raise
            return {"rejected_reason": error.reason, "success": None}
        return {
            "rejected_reason": None,
            "success": {
                "player_code": target["player"]["player_code"],
                "nickname": player_name(target),
                "rank": player_rank(target),
                "login_at": target["last_login_at"],
            },
        }

    def execute(self, session: Session, payload: dict, now: int) -> None:
        ledger = self._read()
        target = self._target(ledger, session, payload, now)["profile"]
        self._rebind(ledger, session.login, target, session.profile, now)
        if self.config.read()["data_link"]["consume_password_on_exec"]:
            del ledger["passwords"][payload["player_code"]]
        write_json(self.path, ledger)
