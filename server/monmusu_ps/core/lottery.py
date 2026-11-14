from collections import defaultdict


UNIT = "unit"
SOURCES = ("gacha_prize_data", "unit_rarity")


def percent(share: float) -> str:
    return f"{share * 100:.3f}%"


class Lottery:
    def __init__(self, masters, config, rng):
        self.masters = masters
        self.config = config
        self.rng = rng
        self._documents = ()
        self._tables: dict[str, list[dict]] = {}
        self._rarities: dict[int, str] = {}

    def _refresh(self) -> None:
        documents = tuple(self.masters.document(name) for name in SOURCES)
        if len(documents) == len(self._documents) and all(
            current is previous for current, previous in zip(documents, self._documents)
        ):
            return
        tables, rarities = (document["table"] for document in documents)
        self._tables = {table["id"]: table["prizes"] for table in tables}
        self._rarities = {row["id"]: row["idName"] for row in rarities}
        self._documents = documents

    def table(self, identity: str) -> list[dict]:
        self._refresh()
        prizes = self._tables.get(identity)
        if not prizes:
            raise ValueError(f"Unknown gacha prize table {identity}")
        return prizes

    def bucket(self, prize: dict) -> str:
        if prize["prize_type"] != UNIT:
            return prize["prize_type"]
        self._refresh()
        card = self.masters.asset("unitcard", int(prize["prize_name"]))
        return self._rarities[card["rarityId"]]

    def shares(self, prizes: list[dict]) -> list[tuple[dict, float]]:
        if not prizes:
            raise ValueError("A gacha lottery needs at least one prize")
        settings = self.config.read()["gacha"]
        buckets: dict[str, list[dict]] = defaultdict(list)
        for prize in prizes:
            buckets[self.bucket(prize)].append(prize)
        rates = {
            name: settings["rates"].get(name, settings["other_rate"])
            for name in buckets
        }
        total = sum(rates.values())
        if total <= 0:
            raise ValueError("Gacha rates must be positive for " + ", ".join(buckets))
        shares: dict[int, float] = {}
        for name, members in buckets.items():
            weights = [
                settings["pickup_weight"] if prize["pickup"] else 1.0
                for prize in members
            ]
            scale = rates[name] / total / sum(weights)
            for prize, weight in zip(members, weights):
                shares[prize["no"]] = weight * scale
        return [(prize, shares[prize["no"]]) for prize in prizes]

    def by_rarity(self, shares: list[tuple[dict, float]]) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for prize, share in shares:
            if prize["prize_type"] == UNIT:
                totals[self.bucket(prize)] += share
        return dict(totals)

    def pick(self, shares: list[tuple[dict, float]]) -> dict:
        point = self.rng.random()
        for prize, share in shares:
            point -= share
            if point < 0:
                return prize
        return shares[-1][0]
