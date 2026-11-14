from monmusu_ps.core.gacha_tickets import SOURCE, STOCK, TICKET
from test_gacha import NOW, runtime, state


__all__ = ["runtime", "state"]

UNDEFINED = "wallet.ticket.gacha_common260803lundead"


def ticket_pools(runtime):
    return [
        pool for pool in runtime.gacha.catalog.pools() if pool["cost_type"] == TICKET
    ]


def test_every_open_ticket_pool_gets_its_tickets_at_login(runtime, state):
    pools = ticket_pools(runtime)
    assert len(pools) > 500
    wallets = runtime.tickets.wallets(NOW)
    catalog = runtime.gacha.catalog
    # One ticket has no wallet row, one pool has no prize table left; neither gets tickets.
    drawable = {pool["cost_name"] for pool in pools if catalog.stocked(pool)}
    assert len({pool["cost_name"] for pool in pools}) - len(drawable) == 1
    assert len(wallets) == len(drawable) - 1
    assert UNDEFINED not in wallets and UNDEFINED not in runtime.resources.rids()
    assert all(runtime.resources.balance(state, rid) == 0 for rid in wallets)
    runtime.attendance.check_in(state, NOW)
    assert all(runtime.resources.balance(state, rid) == 999 for rid in wallets)
    # Nothing overflowed, so no letter carries the stock's tag.
    assert not any(row["tags"][0].startswith(SOURCE) for row in state["inbox"])


def test_the_stock_is_only_topped_up(runtime, state):
    rid = runtime.tickets.wallets(NOW)[0]
    runtime.attendance.check_in(state, NOW)
    runtime.resources.spend(state, [{"rid": rid, "val": 10}], NOW)
    runtime.resources.grant(state, [{"rid": runtime.tickets.wallets(NOW)[1], "val": 1}], NOW)
    runtime.attendance.check_in(state, NOW + 86400)
    assert runtime.resources.balance(state, rid) == 999
    assert runtime.resources.balance(state, runtime.tickets.wallets(NOW)[1]) == 1000


def test_hidden_pools_and_a_zero_stock_grant_nothing(runtime, state):
    settings = runtime.config.read()["gacha"]
    first = ticket_pools(runtime)[0]
    settings["hidden_ids"] = [first["id"]]
    rid = f"wallet.ticket.{first['cost_name']}"
    assert rid not in runtime.tickets.wallets(NOW)
    settings[STOCK] = 0
    runtime.attendance.check_in(state, NOW)
    assert all(
        runtime.resources.balance(state, rid) == 0
        for rid in runtime.tickets.wallets(NOW)
    )
