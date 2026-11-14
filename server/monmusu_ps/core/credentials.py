import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
import uuid

from .store import write_json


class Credentials:
    def __init__(self, path: Path):
        self.path = path
        self._lock = asyncio.Lock()
        self._challenges: dict[str, float] = {}

    def handshake(self) -> str:
        now = time.time()
        self._challenges = {
            token: expires
            for token, expires in self._challenges.items()
            if expires > now
        }
        token = secrets.token_urlsafe(32)
        self._challenges[token] = now + 300
        return token

    def verify_challenge(self, token: str) -> None:
        if self._challenges.get(token, 0) <= time.time():
            raise PermissionError("Handshake token is missing or expired")

    async def issue(self, provider: str, token: str) -> dict:
        self.verify_challenge(token)
        identity, password = uuid.uuid4().hex, secrets.token_urlsafe(32)
        async with self._lock:
            data = self._read()
            data[identity] = {
                "provider": provider,
                "password_hash": hashlib.sha256(password.encode()).hexdigest(),
            }
            write_json(self.path, data)
        return {"id": identity, "psw": password}

    async def authenticate(
        self, provider: str, identity: str, password: str, token: str
    ) -> str:
        self.verify_challenge(token)
        async with self._lock:
            record = self._read().get(identity)
            digest = hashlib.sha256(password.encode()).hexdigest()
            if (
                record is None
                or record["provider"] != provider
                or not hmac.compare_digest(record["password_hash"], digest)
            ):
                raise PermissionError("Invalid client credential")
        del self._challenges[token]
        return f"credential:{provider}:{identity}"

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))
