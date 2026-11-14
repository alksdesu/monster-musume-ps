import hmac
import json
from pathlib import Path
import secrets

from .store import write_json


class PlatformError(PermissionError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class LocalPlatform:
    def __init__(self, path: Path, config):
        self.path = path
        self.config = config

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _account(self, accounts: dict, viewer_id: str) -> dict:
        if viewer_id not in accounts:
            raise PlatformError(102, "Local platform login is required")
        return accounts[viewer_id]

    def _access(self, account: dict, token: str) -> None:
        if not isinstance(token, str) or not hmac.compare_digest(
            account["access_token"], token
        ):
            raise PlatformError(110, "Invalid platform access token")

    def _ticket(self, account: dict, token: str) -> None:
        if not isinstance(token, str) or not hmac.compare_digest(
            account["onetime_token"], token
        ):
            raise PlatformError(204, "Invalid platform one-time token")

    def _application(self, application: str) -> None:
        if application != self.config.read()["platform_app_id"]:
            raise PlatformError(203, "Invalid application identifier")

    @staticmethod
    def save_profile(viewer_id: str) -> str:
        return f"dmm:{viewer_id}"

    def _result(self, account: dict) -> dict:
        return {
            "result_code": 0,
            "access_token": account["access_token"],
            "onetime_token": account["onetime_token"],
            "nickname": account["nickname"],
            "install_status": True,
            "can_use_point": self.config.read()["platform_point_balance"],
            "can_use_chip": 0,
            "payment_id": None,
        }

    def check_login(
        self, viewer_id: str, access_token: str, onetime_token: str
    ) -> dict:
        if not isinstance(viewer_id, str) or not viewer_id:
            raise PlatformError(200, "viewer_id is required")
        accounts = self._read()
        if access_token == "local" and onetime_token == "local":
            account = accounts.setdefault(
                viewer_id, {"nickname": self.config.read()["initial_nickname"]}
            )
            account.update(
                access_token=access_token,
                onetime_token=onetime_token,
            )
            write_json(self.path, accounts)
        else:
            account = self._account(accounts, viewer_id)
            self._access(account, access_token)
            self._ticket(account, onetime_token)
        return self._result(account)

    def profile(self, viewer_id: str, access_token: str) -> dict:
        account = self._account(self._read(), viewer_id)
        self._access(account, access_token)
        return self._result(account)

    def refresh(self, viewer_id: str, access_token: str) -> dict:
        accounts = self._read()
        account = self._account(accounts, viewer_id)
        self._access(account, access_token)
        account.update(
            access_token=secrets.token_urlsafe(32),
            onetime_token=secrets.token_urlsafe(32),
        )
        write_json(self.path, accounts)
        return self._result(account)

    def authenticate(self, viewer_id: str, onetime_token: str, application: str) -> str:
        self._application(application)
        accounts = self._read()
        account = self._account(accounts, viewer_id)
        self._ticket(account, onetime_token)
        return self.save_profile(viewer_id)

    def set_ticket(self, viewer_id: str, onetime_token: str, application: str) -> None:
        self._application(application)
        account = self._account(self._read(), viewer_id)
        self._ticket(account, onetime_token)

    def update_ticket(
        self, viewer_id: str, onetime_token: str, application: str
    ) -> str:
        self._application(application)
        accounts = self._read()
        account = self._account(accounts, viewer_id)
        self._ticket(account, onetime_token)
        account.update(onetime_token=secrets.token_urlsafe(32))
        write_json(self.path, accounts)
        return account["onetime_token"]
