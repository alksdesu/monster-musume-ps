from .gacha import PERIOD_PREFIX
from .journal import LOGIN
from .loginbonus import COMEBACK, holds, restart
from .shop import counter
from .tutorial import Tutorial


CONDITION = "カムバックCP"
GACHA_PERIOD = "cb_gacha"


class Comeback:
    def __init__(self, masters, config, catalog, periods, limits, journal):
        self.masters = masters
        self.config = config
        self.catalog = catalog
        self.periods = periods
        self.limits = limits
        journal.subscribe(LOGIN, self._on_login)

    def _sheets(self, now: int) -> list[dict]:
        return [
            sheet
            for sheet in self.masters.table("login_bonus")
            if sheet["category"] == COMEBACK
            and self.masters.in_term(sheet["start_dt"], sheet["end_dt"], now)
        ]

    def _goods(self, sheets: list[dict]) -> list[dict]:
        parameters = ["", *(str(sheet["id"]) for sheet in sheets)]
        return [
            row
            for parameter in parameters
            for row in self.periods.conditional_sales(CONDITION, parameter)
        ]

    def _counters(self, goods: list[dict]) -> list[str]:
        pools = [
            pool["execlimit_counter_name"]
            for pool in self.catalog.pools()
            if pool["period_name"] == GACHA_PERIOD
        ]
        return pools + [counter(row) for row in goods]

    def _begin(self, state: dict, sheets: list[dict], now: int) -> None:
        if not sheets:
            return
        goods = self._goods(sheets)
        for sheet in sheets:
            restart(state["login_bonus"], sheet["id"])
        closing = now + self.config.read()["comeback"]["gacha_seconds"]
        self.periods.open(state, f"{PERIOD_PREFIX}{GACHA_PERIOD}", now, closing, now)
        self.periods.open_sales(state, goods, now)
        for name in self._counters(goods):
            self.limits.restart(state, name, now)

    def _on_login(self, state: dict, event: dict) -> None:
        threshold = self.config.read()["comeback"]["away_days"]
        returned = 0 < threshold <= event["data"]["away_days"]
        if returned and Tutorial.finished(state):
            self._begin(state, self._sheets(event["at"]), event["at"])

    def welcome(self, state: dict, now: int) -> None:
        if not self.config.read()["comeback"]["with_login_bonus"]:
            return
        fresh = [
            sheet
            for sheet in self._sheets(now)
            if not holds(state["login_bonus"], sheet["id"])
        ]
        self._begin(state, fresh, now)
