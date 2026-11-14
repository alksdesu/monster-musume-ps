from .errors import ApiNotFound, ApiRejected


BOX = "box"
UNFULFILLED = "UNFULFILLED_BOX_RESET_CONDITION"


class BoxGacha:
    def __init__(self, config, masters, resources, rng):
        self.config = config
        self.masters = masters
        self.resources = resources
        self.rng = rng

    def catalog(self, pool: dict) -> list[dict]:
        boxes = self.config.read().get(pool["id"])
        if not boxes:
            raise ApiNotFound(pool["id"])
        return boxes

    def _box(self, pool: dict, number: int) -> dict | None:
        return next((box for box in self.catalog(pool) if box["no"] == number), None)

    @staticmethod
    def _capacity(box: dict | None) -> int:
        return sum(prize["load_num"] for prize in box["list"]) if box else 0

    def record(self, state: dict, pool: dict) -> dict:
        row = next(
            (row for row in state["boxgacha_records"] if row["id"] == pool["id"]), None
        )
        if row is None:
            row = {
                "id": pool["id"],
                "box_capaciity": self._capacity(self._box(pool, 1)),
                "reseted_box": 0,
                "drawn_list": [],
                "resettable": False,
            }
            state["boxgacha_records"].append(row)
        return row

    def _remaining(self, pool: dict, record: dict) -> list[tuple[dict, int]]:
        box = self._box(pool, record["reseted_box"] + 1)
        if box is None:
            raise ApiRejected("OUT_OF_SCHEDULE")
        drawn = record["drawn_list"]
        stock = [
            (prize, prize["load_num"] - drawn.count(prize["no"]))
            for prize in box["list"]
        ]
        return [(prize, left) for prize, left in stock if left > 0]

    def pick(
        self, state: dict, pool: dict, batches: int, interruption: bool
    ) -> tuple[list[dict], list[int]]:
        record = self.record(state, pool)
        remaining = self._remaining(pool, record)
        total = sum(left for _, left in remaining)
        if total <= 0:
            raise ApiRejected("LIMIT_OVER_OF_GACHA_TRIES")
        if batches > total:
            raise ApiRejected("TOO_LARGE_NUM_OF_TRIALS")
        orders, numbers = [], []
        for _ in range(batches):
            point = self.rng.random() * total
            for index, (prize, left) in enumerate(remaining):
                point -= left
                if point < 0:
                    break
            orders.append({"rid": prize["rid"], "val": prize["val"]})
            numbers.append(prize["no"])
            remaining[index] = (prize, left - 1)
            total -= 1
            if interruption and prize["pickup"]:
                break
        self.resources.check(orders)
        return orders, numbers

    def mark(self, state: dict, pool: dict, numbers: list[int]) -> None:
        record = self.record(state, pool)
        record["drawn_list"] = record["drawn_list"] + numbers
        box = self._box(pool, record["reseted_box"] + 1)
        featured = {prize["no"] for prize in box["list"] if prize["pickup"]}
        if featured & set(numbers):
            record["resettable"] = True

    def reset(self, state: dict, pool: dict, now: int) -> None:
        record = self.record(state, pool)
        following = self._box(pool, record["reseted_box"] + 2)
        if following is None:
            raise ApiRejected(UNFULFILLED)
        published = following["publish_at"]
        if published and self.masters.timestamp(published) > now:
            raise ApiRejected(UNFULFILLED)
        stock = sum(left for _, left in self._remaining(pool, record))
        if stock > 0 and not record["resettable"]:
            raise ApiRejected(UNFULFILLED)
        record.update(
            box_capaciity=self._capacity(following),
            reseted_box=record["reseted_box"] + 1,
            drawn_list=[],
            resettable=False,
        )

    def records(self, state: dict, pools: list[dict]) -> list[dict]:
        return [self.record(state, pool) for pool in pools]
