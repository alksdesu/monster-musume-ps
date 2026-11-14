from .editing import Editing, SYNCED


SOURCE = "console.gift"
DIRECT, INBOX = "direct", "inbox"
UNIT_PREFIX = "unit.card."


class Players:
    def __init__(self, runtime):
        self.runtime = runtime
        self.editing = Editing(runtime)

    def _known(self, profile: str) -> str:
        return self.editing.known(profile)

    def _edit(self, profile: str):
        return self.editing.edit(profile)

    async def summary(self, profile: str) -> dict:
        state = await self.runtime.store.read(self._known(profile))
        sections = [
            {
                "name": name,
                "kind": type(value).__name__,
                "size": len(value) if isinstance(value, list | dict) else None,
                "synced": name in SYNCED,
            }
            for name, value in state.items()
        ]
        return {
            "player": state["player"],
            "wallets": state["wallets"],
            "sections": sections,
        }

    async def section(self, profile: str, name: str):
        state = await self.runtime.store.read(self._known(profile))
        return state[name]

    async def replace(self, profile: str, name: str, value) -> dict:
        async with self._edit(profile) as (state, outcome):
            if type(value) is not type(state[name]):
                raise ValueError(f"{name} has to stay a {type(state[name]).__name__}")
            state[name] = value
        return outcome

    async def rename(self, profile: str, nickname) -> dict:
        if type(nickname) is not str or not nickname.strip():
            raise ValueError("A nickname has to be some text")
        async with self._edit(profile) as (state, outcome):
            state["player"]["nickname"] = nickname.strip()
        return outcome

    async def set_balance(self, profile: str, rid: str, value: int) -> dict:
        if type(value) is not int or value < 0:
            raise ValueError("A balance is a whole number from 0 up")
        resources = self.runtime.resources
        async with self._edit(profile) as (state, outcome):
            now = int(self.runtime.clock())
            delta = value - resources.balance(state, rid)
            if delta > 0:
                resources.grant(state, [{"rid": rid, "val": delta}], now)
            elif delta < 0:
                resources.spend(state, [{"rid": rid, "val": -delta}], now)
        return outcome

    async def grant(
        self, profiles: list[str], orders: list[dict], delivery: str, message: str
    ) -> dict:
        runtime = self.runtime
        orders = [{"rid": order["rid"], "val": order["val"]} for order in orders]
        if delivery not in (DIRECT, INBOX) or not orders:
            raise ValueError("A gift needs rewards and a known way of delivery")
        if any(type(order["val"]) is not int or order["val"] < 1 for order in orders):
            raise ValueError("A reward amount is a whole number from 1 up")
        runtime.resources.check(orders)
        # Every save is checked first so that a bad name leaves none of them half served.
        for profile in profiles:
            self._known(profile)
        outcomes = {}
        for profile in profiles:
            async with self._edit(profile) as (state, outcome):
                now = int(runtime.clock())
                if delivery == INBOX:
                    for order in orders:
                        runtime.mailbox.post(state, order, SOURCE, now, message or None)
                else:
                    granted = runtime.resources.grant(
                        state, orders, now, policy="mail", source=SOURCE
                    )
                    outcome["deliveries"] = granted.deliveries
            outcomes[profile] = outcome
        return outcomes

    def catalog(self) -> list[dict]:
        masters = self.runtime.masters
        names = {
            f"wallet.{row['category']}.{row['name']}": row["wallet_name"]
            for row in masters.table("wallet_template")
        }
        rows = [
            {"rid": rid, "name": names.get(rid, "")}
            for rid in self.runtime.resources.rids()
        ]
        rows += [
            {
                "rid": f"{UNIT_PREFIX}{card['id']}",
                "name": f"{card['nickname']} {card['charaName']}",
            }
            for card in masters.assets("unitcard")
        ]
        return rows
