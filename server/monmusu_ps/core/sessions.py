from dataclasses import dataclass, field
from datetime import datetime, timezone
import secrets
import time
import uuid


@dataclass
class Session:
    profile: str
    login: str
    token: str
    password: str
    created_at: int
    cursors: dict = field(default_factory=dict)


class Sessions:
    def __init__(self):
        self._tokens: dict[str, Session] = {}
        self._profiles: dict[str, Session] = {}

    @staticmethod
    def _token() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"dat@{stamp}-{uuid.uuid4()}@{uuid.uuid4()}"

    def create(self, profile: str, login: str | None = None) -> Session:
        previous = self._profiles.get(profile)
        if previous is not None:
            self._tokens.pop(previous.token)
        session = Session(
            profile,
            login or profile,
            self._token(),
            secrets.token_urlsafe(32),
            int(time.time()),
        )
        self._tokens[session.token] = session
        self._profiles[profile] = session
        return session

    def get(self, token: str) -> Session:
        return self._tokens[token]

    def active(self) -> list[Session]:
        return list(self._profiles.values())

    def close(self, profile: str) -> None:
        session = self._profiles.pop(profile, None)
        if session is not None:
            del self._tokens[session.token]

    def refresh(self, session: Session) -> str:
        del self._tokens[session.token]
        session.token = self._token()
        self._tokens[session.token] = session
        return session.token
