from copy import deepcopy
import re

from .sync import Result, diff_state


# The client only submits codes of this shape.
FORMAT = re.compile(r"[0-9a-zA-Z-]+")
MAIL_SOURCE = "serial_code.reward"
INVALID, USED, PENALTY, EXPIRED, LIMIT = (
    "INVALID_CODE",
    "ALREADY_USED",
    "SERIAL_INVALID_PENALTY",
    "OUT_OF_TERM",
    "LIMIT_OVER",
)


class SerialCodes:
    def __init__(self, masters, config, resources, mailbox):
        self.masters = masters
        self.config = config
        self.resources = resources
        self.mailbox = mailbox

    def _campaign(self, campaign_code: str) -> dict | None:
        return next(
            (
                row
                for row in self.masters.table("serial_code_campaign_data")
                if row["campaign_code"] == campaign_code
            ),
            None,
        )

    def _resolve(self, code: str, settings: dict) -> tuple[dict | None, int]:
        custom = settings["codes"].get(code)
        if custom is not None:
            campaign = self._campaign(custom["campaign_code"])
            if campaign is None:
                raise ValueError(f"Serial code {code} names an unknown campaign")
            return campaign, custom["end_at"]
        if FORMAT.fullmatch(code) and settings["accept_campaign_codes"]:
            return self._campaign(code), 0
        return None, 0

    @staticmethod
    def _blocked(record: dict, settings: dict, now: int) -> bool:
        attempts = settings["invalid_attempts"]
        penalised = now < record["failed_at"] + settings["penalty_seconds"]
        return 0 < attempts <= record["failures"] and penalised

    @staticmethod
    def _fail(record: dict, settings: dict, now: int) -> None:
        recent = now < record["failed_at"] + settings["penalty_seconds"]
        record.update(failures=record["failures"] + 1 if recent else 1, failed_at=now)

    def _refusal(
        self, record: dict, code: str, campaign: dict | None, end_at: int, now: int
    ) -> str | None:
        if campaign is None:
            return INVALID
        if 0 < end_at <= now:
            return EXPIRED
        if any(row["code"] == code for row in record["redeemed"]):
            return USED
        redeemed = sum(
            row["campaign_code"] == campaign["campaign_code"]
            for row in record["redeemed"]
        )
        return LIMIT if redeemed >= campaign["count"] else None

    def _redeem(
        self, state: dict, record: dict, code: str, campaign: dict, now: int
    ) -> None:
        campaign_code = campaign["campaign_code"]
        for reward in campaign["reward_table"]["m_rewards"]:
            order = self.resources.reward_order(reward)
            if order is not None:
                self.mailbox.post(state, order, f"{MAIL_SOURCE}:{campaign_code}", now)
        record["redeemed"].append(
            {"code": code, "campaign_code": campaign_code, "at": now}
        )
        record.update(failures=0, failed_at=0)

    def use(self, state: dict, payload: dict, now: int) -> Result:
        code = payload["serial_code"]
        if not isinstance(code, str):
            raise ValueError("serial_code must be a string")
        settings = self.config.read()["serial_code"]
        record = state["serial_codes"]
        if self._blocked(record, settings, now):
            return Result({"successes": None, "rejected_reason": PENALTY})
        before = deepcopy(state)
        campaign, end_at = self._resolve(code, settings)
        reason = self._refusal(record, code, campaign, end_at, now)
        if reason == INVALID:
            self._fail(record, settings, now)
        if reason is not None:
            return Result(
                {"successes": None, "rejected_reason": reason},
                diff_state(before, state),
            )
        self._redeem(state, record, code, campaign, now)
        return Result(
            {
                "successes": {"campaign_code": campaign["campaign_code"]},
                "rejected_reason": None,
            },
            diff_state(before, state),
        )
