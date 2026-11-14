from copy import deepcopy
import json
from pathlib import Path
import random

import pytest

from monmusu_ps.config import Settings
from monmusu_ps.core.calendar import FOREVER
from monmusu_ps.features.news import ROUTES
from monmusu_ps.runtime import Runtime
from test_catalog import Context


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
DAY = 86400
CODE = "READER01"
OPENING = 1_647_230_400


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    runtime = Runtime(
        Settings.load(ROOT, tmp_path), clock=lambda: NOW, rng=random.Random(0)
    )
    settings = deepcopy(runtime.config.read())
    monkeypatch.setattr(runtime.config, "read", lambda: settings)
    yield runtime
    runtime.registry.close()


@pytest.fixture
def session(runtime):
    return runtime.sessions.create(CODE)


def notice(identity, **changes):
    return {
        "id": identity,
        "category": 1,
        "priority": 0,
        "title": "メンテナンス",
        "article": "本文",
        "thumbnail": "",
        "image": "",
        "start_at": NOW - DAY,
        "end_at": NOW + DAY,
        "exdata": "",
        **changes,
    }


def call(runtime, session, endpoint, **payload):
    return ROUTES[endpoint](Context(runtime, payload, {}, session, NOW)).data


def test_master_news_is_published_in_the_client_shape(runtime):
    rows, version = runtime.news.listing(NOW)
    assert [row["id"] for row in rows] == ["1", "2", "3", "4", "5", "6", "7"]
    assert rows[0] == {
        "id": "1",
        "category": 7,
        "priority": 0,
        "title": "アーリーアクセス開始のお知らせ",
        "article": rows[0]["article"],
        "thumbnail": "",
        "image": "",
        "start_at": OPENING,
        "end_at": FOREVER,
        "exdata": "",
    }
    assert rows[0]["article"].startswith("「モンスター娘ＴＤ」運営チームです。")
    assert (len(version), runtime.news.listing(NOW)[1]) == (16, version)
    assert runtime.news.listing(OPENING - 1)[0] == []


def test_configured_notices_join_while_they_run(runtime):
    _, plain = runtime.news.listing(NOW)
    runtime.config.read()["news"]["items"] = [
        notice("local-urgent", priority=5),
        notice("local-new", start_at=NOW),
        notice("local-coming", start_at=NOW + 1),
        notice("local-over", end_at=NOW),
    ]
    rows, version = runtime.news.listing(NOW)
    assert [row["id"] for row in rows[:3]] == ["local-urgent", "local-new", "1"]
    assert len(rows) == 9 and version != plain


def test_news_pages_carry_the_edition(runtime, session):
    first = call(runtime, session, "news/list", per_view=3, news_version="")
    assert [row["id"] for row in first["items"]] == ["1", "2", "3"]
    version = first["news_version"]
    second = call(
        runtime, session, "news/list/continue", sequence_token=first["sequence_token"]
    )
    last = call(
        runtime, session, "news/list/continue", sequence_token=second["sequence_token"]
    )
    assert [row["id"] for row in second["items"]] == ["4", "5", "6"]
    assert ([row["id"] for row in last["items"]], last["sequence_token"]) == (
        ["7"],
        None,
    )
    assert second["news_version"] == last["news_version"] == version
    with pytest.raises(KeyError):
        call(
            runtime,
            session,
            "news/list/continue",
            sequence_token=first["sequence_token"],
        )


def test_readers_holding_the_edition_are_sent_nothing(runtime, session):
    version = runtime.news.listing(NOW)[1]
    held = call(runtime, session, "news/list", per_view=10, news_version=version)
    assert held == {"items": [], "sequence_token": None, "news_version": version}
    stale = call(runtime, session, "news/list", per_view=10, news_version="older")
    assert len(stale["items"]) == 7
    with pytest.raises(ValueError):
        call(runtime, session, "news/list", per_view=0, news_version="")


def test_response_field_names_match_client_dto(runtime, session):
    schema = json.loads((ROOT / "protocol/api_schema.json").read_text("utf-8"))[
        "models"
    ]
    listing = call(runtime, session, "news/list", per_view=10, news_version="")
    for endpoint in ROUTES:
        model = runtime.contracts[endpoint]["response_models"]["200"]
        data = next(row for row in schema[model]["fields"] if row["name"] == "data")
        assert set(listing) == {row["name"] for row in schema[data["type"]]["fields"]}
    assert set(listing["items"][0]) == {row["name"] for row in schema["News"]["fields"]}
