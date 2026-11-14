from .calendar import FOREVER, Calendar


COINS = ("wallet.point.free_coin", "wallet.point.paid_coin")


class Mailbox:
    def __init__(self, config, calendar: Calendar):
        self.config = config
        self.calendar = calendar

    @staticmethod
    def currency(rid: str) -> bool:
        return rid in COINS

    def tags(self, source: str, rid: str) -> list[str]:
        kind = rid.split(".", 1)[0]
        return [source, "vcr", kind] if self.currency(rid) else [source, kind]

    @staticmethod
    def expired(row: dict, now: int) -> bool:
        deadline = row["expire_at"]
        return 0 < deadline < now and deadline != FOREVER

    def deadline(self, now: int, lifetime: int) -> int:
        return self.calendar.date_end(now + lifetime)

    def letter(
        self,
        number: int,
        reward: dict,
        tags: list[str],
        now: int,
        message: str | None = None,
    ) -> dict:
        settings = self.config.read()["mail"]
        return {
            "inbox": settings["inbox"],
            "no": number,
            "message": message,
            "reward": reward,
            "tags": tags,
            "receive_at": now,
            "expire_at": self.deadline(now, settings["lifetime_seconds"]),
        }

    def post(
        self,
        state: dict,
        reward: dict,
        source: str,
        now: int,
        message: str | None = None,
    ) -> dict:
        tags = self.tags(source, reward["rid"])
        sequences = state["resource_sequences"]
        row = self.letter(sequences["inbox"], reward, tags, now, message)
        sequences["inbox"] += 1
        state["inbox"].append(row)
        return row
