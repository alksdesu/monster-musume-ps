from collections import Counter
from copy import deepcopy

from . import cards
from .schedule import VOTES
from .sync import Result, diff_state


UNIT_PREFIX = "unit.card."
SOURCE = "votes.reward"


class Votes:
    def __init__(self, masters, config, schedule, calendar, resources, store):
        self.masters = masters
        self.config = config
        self.schedule = schedule
        self.calendar = calendar
        self.resources = resources
        self.store = store

    def _campaigns(self) -> list[dict]:
        return self.schedule.adjust(VOTES, self.config.read()[VOTES]["campaigns"])

    def _summary(self, campaign: dict) -> dict:
        return {
            "campaign_id": campaign["campaign_id"],
            "round_no": campaign["round_no"],
            "start_at": self.masters.timestamp(campaign["start_at"]),
            "end_at": self.masters.timestamp(campaign["end_at"]),
            "ranking_rev": campaign["ranking_rev"],
        }

    @staticmethod
    def _running(summary: dict, now: int) -> bool:
        return summary["start_at"] <= now < summary["end_at"]

    def summaries(self, now: int) -> list[dict] | None:
        running = [
            summary
            for summary in map(self._summary, self._campaigns())
            if self._running(summary, now)
        ]
        return running or None

    def _round(self, payload: dict, now: int, running: bool = True) -> dict:
        key = payload["campaign_id"], payload["round_no"]
        campaign = next(
            (
                row
                for row in self._campaigns()
                if (row["campaign_id"], row["round_no"]) == key
            ),
            None,
        )
        if campaign is None:
            raise ValueError("Unknown vote round")
        if running and not self._running(self._summary(campaign), now):
            raise ValueError("The vote round is closed")
        return campaign

    @staticmethod
    def _key(campaign: dict) -> str:
        return f"{campaign['campaign_id']}.{campaign['round_no']}"

    def _candidates(self, campaign: dict) -> list[str]:
        listed = campaign["candidates"]
        if listed is None:
            rarities = {
                cards.rarity_id(name) for name in campaign["candidate_rarities"]
            }
            listed = [
                card["id"]
                for card in self.masters.assets("unitcard")
                if card["rarityId"] in rarities
            ]
        return [f"{UNIT_PREFIX}{card_id}" for card_id in listed]

    def _ballot(self, state: dict, campaign: dict, now: int) -> dict:
        ballot = state["votes"].setdefault(
            self._key(campaign),
            {
                "vote_id": 1,
                "choices": None,
                "past_choices": None,
                "voted_at": 0,
                "tally": {},
            },
        )
        # The client words the reset as every morning at five, the game day boundary.
        if ballot["voted_at"] and ballot["voted_at"] < self.calendar.day_start(now):
            ballot.update(
                past_choices=ballot["choices"],
                choices=None,
                voted_at=0,
                vote_id=ballot["vote_id"] + 1,
            )
        return ballot

    @staticmethod
    def _record(campaign: dict, ballot: dict) -> dict:
        return {
            "campaign_id": campaign["campaign_id"],
            "round_no": campaign["round_no"],
            "vote_id": ballot["vote_id"],
            "past_choices": ballot["past_choices"],
            "choices": ballot["choices"],
            "voted_at": ballot["voted_at"],
        }

    def campaign(self, state: dict, payload: dict, now: int) -> dict:
        campaign = self._round(payload, now)
        return {
            "campaign": {
                "summary": self._summary(campaign),
                "tickets": campaign["tickets"],
                "candidates": self._candidates(campaign),
                "message": campaign["message"],
            },
            "record": self._record(campaign, self._ballot(state, campaign, now)),
        }

    def post(self, state: dict, payload: dict, now: int) -> Result:
        campaign = self._round(payload, now)
        choices = payload["choices"]
        if (
            not isinstance(choices, list)
            or not 1 <= len(choices) <= campaign["tickets"]
            or not all(isinstance(choice, str) for choice in choices)
            or len(set(choices)) != len(choices)
            or not set(self._candidates(campaign)).issuperset(choices)
        ):
            raise ValueError("Choices are distinct candidates within the daily tickets")
        ballot = self._ballot(state, campaign, now)
        if ballot["voted_at"]:
            raise ValueError("Today's vote is already cast")
        before = deepcopy(state)
        ballot.update(choices=list(choices), voted_at=now)
        for choice in choices:
            ballot["tally"][choice] = ballot["tally"].get(choice, 0) + 1
        gifts = deepcopy(campaign["rewards"])
        rewards = self.resources.grant(
            state, gifts, now, policy="mail", source=SOURCE
        ).deliveries
        return Result(
            {"record": self._record(campaign, ballot), "rewards": rewards},
            diff_state(before, state),
        )

    def ranking(self, state: dict, payload: dict, now: int) -> dict:
        campaign = self._round(payload, now, running=False)
        revision = campaign["ranking_rev"]
        if revision is None:
            return {"ranking_rev": None, "ranks": []}
        totals = Counter(
            {
                f"{UNIT_PREFIX}{card_id}": votes
                for card_id, votes in campaign["base_votes"].items()
            }
        )
        saves = {
            document["state"]["player"]["player_code"]: document["state"]
            for document in self.store.documents()
        }
        saves[state["player"]["player_code"]] = state
        for save in saves.values():
            totals.update(save["votes"].get(self._key(campaign), {}).get("tally", {}))
        cast = sum(totals.values())
        ranked = sorted(
            (item for item in totals.items() if item[1] > 0),
            key=lambda item: (-item[1], item[0]),
        )
        return {
            "ranking_rev": revision,
            "ranks": [
                {
                    "rank": rank,
                    "candidate": candidate,
                    # The client shows this as a percentage with two decimals.
                    "ratio": round(votes * 10000 / cast),
                }
                for rank, (candidate, votes) in enumerate(
                    ranked[: campaign["ranking_size"]], 1
                )
            ],
        }
