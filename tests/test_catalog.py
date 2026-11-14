import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from monmusu_ps.core.context import Context
from monmusu_ps.core.sessions import Sessions
from monmusu_ps.features.catalog import ROUTES


@pytest.fixture
def context():
    state = {
        key: []
        for key in (
            "units",
            "boms",
            "wallets",
            "items",
            "charms",
            "skins",
            "stories",
            "quest_records",
            "quest_secrets",
            "periods",
            "limits",
            "inbox",
            "bonus_passes",
            "gacha_stamp_records",
            "gacha_selection",
            "missions",
            "mission_counters",
            "flag_reminders",
            "vouchers",
        )
    }
    config = {
        "periods": [],
        "limits": [],
    }
    runtime = SimpleNamespace(
        config=SimpleNamespace(read=lambda: config), sessions=Sessions()
    )
    return Context(runtime, {}, state, runtime.sessions.create("catalog-test"), 1000)


def call(ctx, endpoint, **payload):
    ctx.payload = payload
    return ROUTES[endpoint](ctx)


@pytest.mark.parametrize(
    ("endpoint", "state_key", "response_key", "value"),
    [
        ("bom/all", "boms", "boms", [{"instance_id": 1, "card_id": 9001}]),
        (
            "wallet/all",
            "wallets",
            "wallets",
            [{"category": "point", "name": "gold", "val": 250}],
        ),
    ],
)
def test_read_endpoints_return_saved_values(
    context, endpoint, state_key, response_key, value
):
    context.state[state_key] = value
    original = deepcopy(context.state)
    result = call(context, endpoint, _z0=0)
    assert result.data == {response_key: value}
    assert result.sync is None
    assert context.state == original


def test_units_include_charms_and_single_instance_lookup(context):
    context.state["units"] = [
        {"instance_id": 1, "card_id": 11},
        {"instance_id": 7, "card_id": 22},
    ]
    context.state["charms"] = [{"id": 3, "tid": 4, "para": [], "created_at": 1}]
    assert call(context, "unit/all").data == {
        "units": context.state["units"],
        "charms": context.state["charms"],
    }
    assert call(context, "unit/", instance_id=7).data == {
        "unit": context.state["units"][1]
    }
    with pytest.raises(KeyError):
        call(context, "unit/", instance_id=22)


def test_item_pagination_keeps_snapshot_and_consumes_tokens(context):
    context.state["items"] = [
        {"id": f"item.material.{index}", "val": index} for index in range(5)
    ]
    original = deepcopy(context.state["items"])
    first = call(context, "item/list", per_view=2).data
    assert first["items"] == original[:2]
    first_token = first["sequence_token"]
    context.state["items"][2]["val"] = 999
    context.state["items"].append({"id": "item.material.later", "val": 100})
    second = call(context, "item/list/continue", sequence_token=first_token).data
    assert second["items"] == original[2:4]
    with pytest.raises(KeyError):
        call(context, "item/list/continue", sequence_token=first_token)
    final = call(
        context, "item/list/continue", sequence_token=second["sequence_token"]
    ).data
    assert final == {"items": original[4:], "sequence_token": None}


def test_cursor_is_bound_to_session_and_list_family(context):
    context.state["items"] = [{"id": "a", "val": 1}, {"id": "b", "val": 2}]
    token = call(context, "item/list", per_view=1).data["sequence_token"]
    with pytest.raises(ValueError, match="another list"):
        call(context, "quest/record/list/continue", sequence_token=token)
    other = Context(
        context.runtime,
        {},
        context.state,
        context.runtime.sessions.create("other-player"),
        context.now,
    )
    with pytest.raises(KeyError):
        call(other, "item/list/continue", sequence_token=token)
    assert call(context, "item/list/continue", sequence_token=token).data["items"] == [
        {"id": "b", "val": 2}
    ]


@pytest.mark.parametrize("per_view", [0, -1, True, "2"])
def test_invalid_page_size_is_rejected(context, per_view):
    with pytest.raises(ValueError, match="positive integer"):
        call(context, "item/list", per_view=per_view)


@pytest.mark.parametrize(
    ("collection", "family"), [("periods", "period"), ("limits", "limit")]
)
def test_ranges_merge_config_with_player_values_and_snapshot(
    context, collection, family
):
    global_rows = [
        {"id": "gacha"},
        {"id": "gacha.normal.pool", "value": 1},
        {"id": "gachax.pool"},
        {"id": "sales.local"},
    ]
    context.runtime.config.read()[collection] = global_rows
    context.state[collection] = [
        {"id": "gacha.normal.pool", "value": 5},
        {"id": "gacha.ticket.pool", "value": 2},
    ]
    first = call(
        context, f"{family}/list/seq_start", range_names=["gacha"], per_view=2
    ).data
    assert first["items"] == [{"id": "gacha"}, {"id": "gacha.normal.pool", "value": 5}]
    context.state[collection][1]["value"] = 90
    context.runtime.config.read()[collection].append({"id": "gacha.new"})
    second = call(
        context, f"{family}/list/seq_continue", sequence_token=first["sequence_token"]
    ).data
    assert second == {
        "items": [{"id": "gacha.ticket.pool", "value": 2}],
        "sequence_token": None,
    }
    assert (
        len(
            call(context, f"{family}/list/seq_start", range_names=[], per_view=10).data[
                "items"
            ]
        )
        == 6
    )


def test_quest_record_pagination_and_secret_records(context):
    rows = [
        {"quest_id": 10000, "category": 1, "section": 1},
        {"quest_id": 10001, "category": 1, "section": 1},
    ]
    context.state["quest_records"] = rows
    first = call(context, "quest/record/list", range_names=[], per_view=1).data
    second = call(
        context, "quest/record/list/continue", sequence_token=first["sequence_token"]
    ).data
    assert first["items"] + second["items"] == rows
    listing = {
        "quest_secrets": [
            {"category_id": 1, "sum_star_count": 3, "max_hunt": 0, "total_hunt": 0}
        ],
        "wild_hunt_ranks": [{"category_id": 1, "rank": 1}],
    }
    context.runtime.secret_quests = SimpleNamespace(listing=lambda state: listing)
    assert call(context, "quest/record/secret").data == listing


def test_quest_record_ranges_narrow_by_category_then_chapter(context):
    context.state["quest_records"] = [
        {"category": 1, "section": 1, "quest_id": 10000},
        {"category": 1, "section": 2, "quest_id": 10100},
        {"category": 8, "section": 1001, "quest_id": 56000},
    ]

    def listed(*ranges):
        data = call(context, "quest/record/list", range_names=list(ranges), per_view=10)
        return [row["quest_id"] for row in data.data["items"]]

    assert listed() == [10000, 10100, 56000]
    assert listed("1") == [10000, 10100]
    assert listed("1", "2") == [10100]
    assert listed("8", "1001") == [56000]
    assert listed("3") == []


@pytest.mark.parametrize(
    "ranges", [["unknown-range"], ["1", "2", "3"], [1], ["-1"], ["１"], "1", None]
)
def test_malformed_quest_record_ranges_are_errors(context, ranges):
    with pytest.raises(ValueError, match="range_names"):
        call(context, "quest/record/list", range_names=ranges, per_view=1)


def test_paid_coin_batches_are_not_tracked(context):
    data = call(context, "purchase/vcr/record/list", per_view=100).data
    assert data == {"items": [], "sequence_token": None}


@pytest.mark.parametrize(
    ("endpoint", "model", "payload"),
    [
        ("unit/all", "GetUnitAllResponse200Data", {}),
        ("bom/all", "GetBomAllResponse200Data", {}),
        ("wallet/all", "GetWalletAllResponse200Data", {}),
        ("item/list", "GetItemListResponse200Data", {"per_view": 10}),
        (
            "period/list/seq_start",
            "GetPeriodInfoListSeqStartResponse200Data",
            {"range_names": [], "per_view": 10},
        ),
        (
            "limit/list/seq_start",
            "GetLimitCounterListSeqStartResponse200Data",
            {"range_names": [], "per_view": 10},
        ),
        (
            "purchase/vcr/record/list",
            "GetVcrRecordListResponse200Data",
            {"per_view": 10},
        ),
    ],
)
def test_response_field_names_match_client_dto(context, endpoint, model, payload):
    schema = json.loads(
        (Path(__file__).parents[1] / "protocol/api_schema.json").read_text(
            encoding="utf-8"
        )
    )
    expected = {field["name"] for field in schema["models"][model]["fields"]}
    assert set(call(context, endpoint, **payload).data) == expected
