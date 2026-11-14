from datetime import date, timedelta

from .journal import LOGIN


VIP, DMM_PREMIUM = "vip", "dmm_premium"
# The client titles a letter tagged vip.dmm_premium:<year>-<month> by its month.
SOURCES = {VIP: "vip.benefits", DMM_PREMIUM: "vip.dmm_premium"}


def _stamp(month: date) -> int:
    return month.year * 100 + month.month


class Membership:
    def __init__(self, config, calendar, resources, mailbox, journal):
        self.config = config
        self.calendar = calendar
        self.resources = resources
        self.mailbox = mailbox
        journal.subscribe(LOGIN, self._on_login)

    def _month(self, now: int) -> date:
        return self.calendar.day(now).replace(day=1)

    def _benefits(self) -> dict[str, list[dict]]:
        settings = self.config.read()
        premium = settings[DMM_PREMIUM]
        return {
            VIP: settings[VIP]["benefits"],
            DMM_PREMIUM: premium["rewards"] if premium["member"] else [],
        }

    def _on_login(self, state: dict, event: dict) -> None:
        now = event["at"]
        month = self._month(now)
        delivered = state["monthly_benefits"]
        for program, rewards in self._benefits().items():
            if not rewards or delivered[program] == _stamp(month):
                continue
            self.resources.check(rewards)
            source = f"{SOURCES[program]}:{month.year}-{month.month}"
            for reward in rewards:
                self.mailbox.post(state, dict(reward), source, now)
            delivered[program] = _stamp(month)

    def vip(self, now: int) -> dict:
        settings = self.config.read()[VIP]
        month = self._month(now)

        def rating(moment: date) -> dict:
            return {
                "year": moment.year,
                "month": moment.month,
                "point": settings["point"],
                "rank": settings["rank"],
            }

        benefits = settings["benefits"]
        listed = {"year": month.year, "month": month.month, "rewards": benefits}
        return {
            "rating_info": rating(month),
            "last_rating_info": rating(month - timedelta(days=1)),
            "benefits_list": [listed] if benefits else [],
        }

    def dmm_premium(self, state: dict, now: int) -> dict:
        month = self._month(now)
        return {
            "is_obtained": state["monthly_benefits"][DMM_PREMIUM] == _stamp(month),
            "benefits": {
                "year": month.year,
                "month": month.month,
                "rewards": self._benefits()[DMM_PREMIUM],
            },
        }
