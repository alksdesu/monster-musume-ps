import json
from pathlib import Path

import pytest

from monmusu_ps.core.lottery import Lottery
from monmusu_ps.core.masterdata import MasterData


ROOT = Path(__file__).resolve().parents[1]
COMMON, RARE, EPIC = 11, 22, 23


class Config:
    def __init__(self, value):
        self.value = value

    def read(self):
        return self.value


class Dice:
    def __init__(self, *points):
        self.points = list(points)

    def random(self):
        return self.points.pop(0)


@pytest.fixture
def settings():
    document = json.loads(
        (ROOT / "server/data/settings.json").read_text(encoding="utf-8")
    )
    return {"gacha": document["gacha"]}


@pytest.fixture
def masters():
    return MasterData(ROOT / "masterdata")


@pytest.fixture
def lottery(masters, settings):
    return Lottery(masters, Config(settings), Dice())


@pytest.fixture
def legend(lottery):
    return int(lottery.table("limited_legendget10")[0]["prize_name"])


def unit(number, card, pickup=0):
    return {
        "table_id": "local",
        "no": number,
        "prize_num": 1,
        "prize_type": "unit",
        "prize_name": str(card),
        "pickup": pickup,
        "status": 0,
        "is_select": 0,
    }


def other(number, kind, name):
    return {**unit(number, 0), "prize_type": kind, "prize_name": name}


def portions(lottery, prizes):
    return [share for _, share in lottery.shares(prizes)]


def test_rarity_rates_split_the_table(lottery, legend):
    prizes = [unit(1, COMMON), unit(2, RARE), unit(3, EPIC), unit(4, legend)]
    shares = lottery.shares(prizes)
    assert [prize for prize, _ in shares] == prizes
    assert portions(lottery, prizes) == pytest.approx([0.55, 0.30, 0.12, 0.03])
    assert lottery.by_rarity(shares) == pytest.approx(
        {"R": 0.55, "HR": 0.30, "SR": 0.12, "SSR": 0.03}
    )


def test_pickups_weigh_more_inside_their_rarity(lottery, legend):
    prizes = [
        unit(1, legend, pickup=1),
        unit(2, legend),
        unit(3, legend),
        unit(4, COMMON),
        unit(5, COMMON),
    ]
    legendary, common = 3 / 58, 55 / 58
    assert portions(lottery, prizes) == pytest.approx(
        [
            legendary * 40 / 42,
            legendary / 42,
            legendary / 42,
            common / 2,
            common / 2,
        ]
    )


def test_absent_rarities_leave_their_share_to_the_rest(lottery, legend):
    prizes = [unit(1, EPIC), unit(2, legend)]
    assert portions(lottery, prizes) == pytest.approx([0.8, 0.2])
    assert portions(lottery, [unit(1, legend), unit(2, legend)]) == [0.5, 0.5]


def test_other_prizes_use_the_fallback_rate(lottery):
    prizes = [other(1, "point", "gold"), unit(2, COMMON), other(3, "item", "x")]
    shares = lottery.shares(prizes)
    assert portions(lottery, prizes) == pytest.approx([10 / 75, 55 / 75, 10 / 75])
    assert lottery.by_rarity(shares) == pytest.approx({"R": 55 / 75})


def test_rates_follow_the_configuration(lottery, settings, legend):
    settings["gacha"]["rates"] = {"SSR": 1.0, "R": 3.0}
    settings["gacha"]["pickup_weight"] = 1.0
    prizes = [unit(1, COMMON), unit(2, legend, pickup=1), unit(3, legend)]
    assert portions(lottery, prizes) == pytest.approx([0.75, 0.125, 0.125])
    settings["gacha"]["other_rate"] = 4.0
    assert portions(lottery, [*prizes, unit(4, EPIC)]) == pytest.approx(
        [0.375, 0.0625, 0.0625, 0.5]
    )


def test_rates_must_leave_something_to_draw(lottery, settings):
    settings["gacha"]["rates"] = {"R": 0.0}
    with pytest.raises(ValueError, match="rates must be positive for R"):
        lottery.shares([unit(1, COMMON)])
    with pytest.raises(ValueError, match="at least one prize"):
        lottery.shares([])


def test_unknown_tables_are_rejected(lottery):
    assert [prize["prize_name"] for prize in lottery.table("Tutorial")] == ["23"]
    with pytest.raises(ValueError, match="Unknown gacha prize table missing"):
        lottery.table("missing")


@pytest.mark.parametrize(
    ("point", "number"),
    [(0.0, 1), (0.5499, 1), (0.55, 2), (0.84, 2), (0.86, 3), (0.98, 4), (0.9999, 4)],
)
def test_pick_walks_the_cumulative_shares(masters, settings, legend, point, number):
    lottery = Lottery(masters, Config(settings), Dice(point))
    prizes = [unit(1, COMMON), unit(2, RARE), unit(3, EPIC), unit(4, legend)]
    assert lottery.pick(lottery.shares(prizes))["no"] == number


def test_rounding_tail_falls_to_the_last_prize(masters, settings):
    lottery = Lottery(masters, Config(settings), Dice(0.99995))
    shares = [(unit(1, COMMON), 0.5), (unit(2, RARE), 0.4999)]
    assert lottery.pick(shares)["no"] == 2
