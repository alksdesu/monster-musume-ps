from .journal import LOGIN


TICKET = "ticket"
SOURCE = "gacha.tickets"
STOCK = "ticket_stock"


class TicketStock:
    def __init__(self, catalog, config, resources, journal):
        self.catalog = catalog
        self.config = config
        self.resources = resources
        journal.subscribe(LOGIN, self._on_login)

    def stock(self) -> int:
        return int(self.config.read()["gacha"][STOCK])

    def wallets(self, now: int) -> list[str]:
        hidden = set(self.config.read()["gacha"]["hidden_ids"])
        known = set(self.resources.rids())
        rids = {}
        for pool in self.catalog.pools():
            if (
                pool["cost_type"] == TICKET
                and pool["id"] not in hidden
                and self.catalog.started(pool, now)
                and not self.catalog.ended(pool, now)
                and self.catalog.stocked(pool)
            ):
                rid = f"wallet.{TICKET}.{pool['cost_name']}"
                # A ticket the master data never defined as a wallet cannot be held.
                if rid in known:
                    rids[rid] = None
        return list(rids)

    def _on_login(self, state: dict, event: dict) -> None:
        stock = self.stock()
        if stock <= 0:
            return
        now = event["at"]
        orders = []
        for rid in self.wallets(now):
            short = stock - self.resources.balance(state, rid)
            if short > 0:
                orders.append({"rid": rid, "val": short})
        if orders:
            self.resources.grant(state, orders, now, policy="discard", source=SOURCE)
