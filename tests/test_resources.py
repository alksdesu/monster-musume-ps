from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from monmusu_ps.config import RuntimeConfig
from monmusu_ps.core.calendar import Calendar
from monmusu_ps.core.initial import create_player
from monmusu_ps.core.journal import Journal
from monmusu_ps.core.mail import Mailbox
from monmusu_ps.core.masterdata import MasterData
from monmusu_ps.core.progression import Progression
from monmusu_ps.core.resources import (
    InsufficientResource,
    ResourceCapacityError,
    Resources,
    UnknownResource,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = 1_794_340_000
DAY_END = 1_794_409_199
MONTH_END = 1_797_001_199
MEDAL = "wallet.gachamedal.gmedal_common"


@pytest.fixture
def setup(tmp_path):
    masters = MasterData(ROOT / "masterdata")
    settings = json.loads(
        (ROOT / "server/data/settings.json").read_text(encoding="utf-8")
    )
    settings["resources"] = {
        "attachable_capacity": 9999,
        "unit_capacity": 1500,
        "duplicate_unit": {
            "potential_cap": 5,
            "compensation_rid": MEDAL,
            "compensation": {"R": 5, "HR": 20, "SR": 100, "SSR": 500},
        },
    }
    settings["mail"] = {"inbox": "main", "lifetime_seconds": 2592000}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    state = create_player("资源测试", "LOCAL", masters, settings, NOW - 100)
    state["resource_sequences"] = {"unit": 3, "bom": 2, "inbox": 1}
    config = RuntimeConfig(path)
    mailbox = Mailbox(config, Calendar(masters))
    progression = Progression(masters, config, Journal(config))
    resources = Resources(masters, config, mailbox, progression)
    return resources, state, settings, path


def order(rid, amount):
    return {"rid": rid, "val": amount}


def wallet(state, name, category="point"):
    return next(
        row
        for row in state["wallets"]
        if row["category"] == category and row["name"] == name
    )


def potential(state, card_id):
    return next(row for row in state["units"] if row["card_id"] == card_id)["upa"]


def configure(setup):
    _, _, settings, path = setup
    path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")


def test_balance_uses_real_resource_registry(setup):
    resources, state, _, _ = setup
    assert resources.balance(state, "wallet.point.gold") == 500
    assert resources.balance(state, "wallet.point.free_coin") == 300
    assert resources.balance(state, "wallet.point.stone") == 300
    assert resources.balance(state, "item.material.Bom2") == 0
    assert resources.balance(state, "item.equip.9401") == 0
    assert resources.balance(state, "skill.attachable.1175") == 0
    assert resources.balance(state, "unit.card.11") == 1
    assert resources.balance(state, "stamina.quest") == 70


def test_spend_sums_duplicate_orders_and_reports_final_wallet_once(setup):
    resources, state, _, _ = setup
    orders = [order("wallet.point.gold", 200), order("wallet.point.gold", 150)]
    before_orders = deepcopy(orders)
    result = resources.spend(state, orders, NOW)
    assert wallet(state, "gold")["val"] == 150
    assert result.deliveries == []
    assert result.updates == {"wallet": [wallet(state, "gold")]}
    assert wallet(state, "gold")["created_at"] == NOW - 100
    assert orders == before_orders


def test_spend_checks_aggregate_balance_before_any_mutation(setup):
    resources, state, _, _ = setup
    original = deepcopy(state)
    with pytest.raises(InsufficientResource) as caught:
        resources.spend(
            state,
            [order("wallet.point.gold", 300), order("wallet.point.gold", 300)],
            NOW,
        )
    assert (caught.value.rid, caught.value.required, caught.value.available) == (
        "wallet.point.gold",
        600,
        500,
    )
    assert state == original
    with pytest.raises(InsufficientResource):
        resources.spend(
            state, [order("wallet.point.gold", 10), order("item.material.Bom2", 1)], NOW
        )
    assert state == original


def test_stone_spending_uses_free_then_paid_without_third_wallet(setup):
    resources, state, _, _ = setup
    wallet(state, "paid_coin")["val"] = 500
    result = resources.spend(state, [order("wallet.point.stone", 450)], NOW)
    assert wallet(state, "free_coin")["val"] == 0
    assert wallet(state, "paid_coin")["val"] == 350
    assert resources.balance(state, "wallet.point.stone") == 350
    assert {row["name"] for row in result.updates["wallet"]} == {
        "free_coin",
        "paid_coin",
    }
    assert all(row["name"] != "stone" for row in state["wallets"])


def test_stone_and_explicit_coin_costs_reserve_each_balance_once(setup):
    resources, state, _, _ = setup
    wallet(state, "paid_coin")["val"] = 400
    result = resources.spend(
        state,
        [order("wallet.point.stone", 500), order("wallet.point.free_coin", 100)],
        NOW,
    )
    assert wallet(state, "free_coin")["val"] == 0
    assert wallet(state, "paid_coin")["val"] == 100
    assert len(result.updates["wallet"]) == 2
    original = deepcopy(state)
    with pytest.raises(InsufficientResource):
        resources.spend(
            state,
            [order("wallet.point.stone", 100), order("wallet.point.paid_coin", 1)],
            NOW,
        )
    assert state == original


def test_stone_grant_requires_explicit_currency(setup):
    resources, state, _, _ = setup
    original = deepcopy(state)
    with pytest.raises(NotImplementedError, match="free_coin or paid_coin"):
        resources.grant(state, [order("wallet.point.stone", 10)], NOW)
    assert state == original


def test_wallet_grant_uses_template_revision_and_keeps_creation_time(setup):
    resources, state, _, _ = setup
    result = resources.grant(state, [order("wallet.point.duplicate221107", 3)], NOW)
    created = wallet(state, "duplicate221107")
    assert created == {
        "category": "point",
        "name": "duplicate221107",
        "val": 3,
        "revision": 9,
        "created_at": NOW,
    }
    assert result.updates == {"wallet": [created]}
    resources.grant(state, [order("wallet.point.duplicate221107", 2)], NOW + 1)
    assert wallet(state, "duplicate221107")["revision"] == 9
    assert wallet(state, "duplicate221107")["created_at"] == NOW


def test_multiple_grants_preserve_deliveries_and_merge_stock_updates(setup):
    resources, state, _, _ = setup
    result = resources.grant(
        state, [order("item.material.Bom2", 2), order("item.material.Bom2", 3)], NOW
    )
    assert result.deliveries == [
        {"rid": "item.material.Bom2", "val": 2, "inc": 2, "inbox": 0, "over": 0},
        {"rid": "item.material.Bom2", "val": 3, "inc": 3, "inbox": 0, "over": 0},
    ]
    assert result.updates == {"item": [{"id": "item.material.Bom2", "val": 5}]}
    spent = resources.spend(state, [order("item.material.Bom2", 5)], NOW)
    assert spent.updates == {"item": [{"id": "item.material.Bom2", "val": 0}]}


def test_capacity_rejection_is_atomic_across_resources(setup):
    resources, state, _, _ = setup
    wallet(state, "gold")["val"] = 99999998
    original = deepcopy(state)
    with pytest.raises(ResourceCapacityError):
        resources.grant(
            state, [order("item.material.Bom2", 2), order("wallet.point.gold", 2)], NOW
        )
    assert state == original


@pytest.mark.parametrize(
    ("policy", "inbox_count", "over"), [("mail", 3, 0), ("discard", 0, 3)]
)
def test_overflow_policy_records_exact_accepted_and_overflow_amounts(
    setup, policy, inbox_count, over
):
    resources, state, _, _ = setup
    wallet(state, "gold")["val"] = 99999997
    result = resources.grant(state, [order("wallet.point.gold", 5)], NOW, policy=policy)
    assert wallet(state, "gold")["val"] == 99999999
    assert result.deliveries == [
        {
            "rid": "wallet.point.gold",
            "val": 5,
            "inc": 2,
            "inbox": inbox_count,
            "over": over,
        }
    ]
    assert set(result.updates) == {"wallet"}
    if policy == "mail":
        assert state["inbox"] == [
            {
                "inbox": "main",
                "no": 1,
                "message": None,
                "reward": {"rid": "wallet.point.gold", "val": 3},
                "tags": [],
                "receive_at": NOW,
                "expire_at": MONTH_END,
            }
        ]
        assert state["resource_sequences"]["inbox"] == 2
    else:
        assert state["inbox"] == []
        assert state["resource_sequences"]["inbox"] == 1


def test_mail_ids_remain_monotonic_after_claimed_messages_are_removed(setup):
    resources, state, _, _ = setup
    wallet(state, "gold")["val"] = 99999999
    resources.grant(state, [order("wallet.point.gold", 1)], NOW, policy="mail")
    state["inbox"].clear()
    resources.grant(state, [order("wallet.point.gold", 1)], NOW + 1, policy="mail")
    assert state["inbox"][0]["no"] == 2


@pytest.mark.parametrize(
    ("rid", "capacity"), [("item.equip.1000", 9999), ("item.equip.9401", 999)]
)
def test_equipment_uses_its_own_master_capacity(setup, rid, capacity):
    resources, state, _, _ = setup
    result = resources.grant(state, [order(rid, capacity)], NOW)
    assert result.updates == {"item": [{"id": rid, "val": capacity}]}
    with pytest.raises(ResourceCapacityError):
        resources.grant(state, [order(rid, 1)], NOW)


@pytest.mark.parametrize(("skill", "limit"), [(1175, 1), (1218, 2)])
def test_item_limit_converts_only_excess_to_derived_rewards(setup, skill, limit):
    resources, state, _, _ = setup
    rid = f"skill.attachable.{skill}"
    result = resources.grant(state, [order(rid, limit + 2)], NOW)
    assert resources.balance(state, rid) == limit
    assert resources.balance(state, "wallet.gachamedal.gmedal_common") == 700
    assert result.deliveries == [
        {
            "rid": rid,
            "val": limit + 2,
            "inc": limit,
            "inbox": 0,
            "over": 2,
            "derived": [
                {
                    "rid": "wallet.gachamedal.gmedal_common",
                    "val": 700,
                    "inc": 700,
                    "inbox": 0,
                    "over": 0,
                }
            ],
        }
    ]
    assert set(result.updates) == {"item", "wallet"}


def test_skin_conversion_limit_overrides_generic_item_capacity(setup):
    resources, state, _, _ = setup
    result = resources.grant(state, [order("item.unit_skin.46_2", 2)], NOW)
    assert resources.balance(state, "item.unit_skin.46_2") == 1
    assert wallet(state, "gold")["val"] == 100500
    assert result.deliveries[0]["derived"][0]["val"] == 100000


def test_derived_reward_capacity_rejection_rolls_back_source_grant(setup):
    resources, state, _, _ = setup
    wallet(state, "gold")["val"] = 99999999
    original = deepcopy(state)
    with pytest.raises(ResourceCapacityError):
        resources.grant(state, [order("item.unit_skin.46_2", 2)], NOW)
    assert state == original


def test_derived_reward_uses_mail_policy_for_its_own_overflow(setup):
    resources, state, _, _ = setup
    wallet(state, "gold")["val"] = 99999999
    result = resources.grant(
        state, [order("item.unit_skin.46_2", 2)], NOW, policy="mail"
    )
    assert result.deliveries[0]["over"] == 1
    assert result.deliveries[0]["derived"] == [
        {
            "rid": "wallet.point.gold",
            "val": 100000,
            "inc": 0,
            "inbox": 100000,
            "over": 0,
        }
    ]
    assert state["inbox"][0]["reward"] == {"rid": "wallet.point.gold", "val": 100000}


def test_new_unit_uses_native_initial_fields_and_sequence(setup):
    resources, state, _, _ = setup
    result = resources.grant(state, [order("unit.card.6", 1)], NOW)
    acquired = state["units"][-1]
    assert acquired["instance_id"] == 3
    assert acquired["card_id"] == 6
    assert acquired["t_exp"] == 0
    assert acquired["r_unit_lv"] == 1
    assert acquired["exskill_lv"] == 1
    assert acquired["rank"] == 0
    assert acquired["gear_set"] == {"A": 0, "B": 0, "C": 0, "D": 0}
    assert acquired["upa"]["all_pt"] == acquired["upa"]["used_pt"] == 0
    assert acquired["created_at"] == acquired["updated_at"] == NOW
    assert state["resource_sequences"]["unit"] == 4
    assert result.updates == {"unit": [acquired]}
    assert result.deliveries == [
        {"rid": "unit.card.6", "val": 1, "inc": 1, "inbox": 0, "over": 0}
    ]


def test_another_copy_of_an_owned_card_raises_its_potential(setup):
    resources, state, _, _ = setup
    result = resources.grant(
        state, [order("wallet.point.gold", 1), order("unit.card.11", 1)], NOW
    )
    owned = state["units"][0]
    assert (owned["card_id"], owned["upa"], owned["updated_at"]) == (
        11,
        {"all_pt": 1, "used_pt": 0, "skills": []},
        NOW,
    )
    assert [row["card_id"] for row in state["units"]] == [11, 22]
    assert state["resource_sequences"]["unit"] == 3
    assert result.deliveries[1] == {
        "rid": "unit.card.11",
        "val": 1,
        "inc": 0,
        "inbox": 0,
        "over": 0,
    }
    assert result.updates["unit"] == [owned]


def test_copies_beyond_the_first_of_a_new_card_become_potential(setup):
    resources, state, _, _ = setup
    result = resources.grant(state, [order("unit.card.6", 3)], NOW)
    acquired = state["units"][-1]
    assert (acquired["instance_id"], acquired["card_id"]) == (3, 6)
    assert acquired["upa"]["all_pt"] == 2
    assert result.deliveries == [
        {"rid": "unit.card.6", "val": 3, "inc": 1, "inbox": 0, "over": 0}
    ]
    assert result.updates == {"unit": [acquired]}
    resources.grant(state, [order("unit.card.6", 1)], NOW + 1)
    assert potential(state, 6)["all_pt"] == 3
    assert state["resource_sequences"]["unit"] == 4


def test_orders_for_the_same_new_card_share_one_instance(setup):
    resources, state, _, _ = setup
    result = resources.grant(
        state, [order("unit.card.6", 1), order("unit.card.6", 1)], NOW
    )
    assert [row["card_id"] for row in state["units"]] == [11, 22, 6]
    assert potential(state, 6)["all_pt"] == 1
    assert [row["inc"] for row in result.deliveries] == [1, 0]
    assert result.updates == {"unit": [state["units"][-1]]}


@pytest.mark.parametrize(("card", "medals"), [(11, 5), (22, 20)])
def test_copies_past_full_potential_convert_to_medals_by_rarity(setup, card, medals):
    resources, state, _, _ = setup
    potential(state, card)["all_pt"] = 4
    result = resources.grant(state, [order(f"unit.card.{card}", 3)], NOW)
    assert potential(state, card)["all_pt"] == 5
    assert resources.balance(state, MEDAL) == medals * 2
    assert result.deliveries == [
        {
            "rid": f"unit.card.{card}",
            "val": 3,
            "inc": 0,
            "inbox": 0,
            "over": 2,
            "derived": [
                {
                    "rid": MEDAL,
                    "val": medals * 2,
                    "inc": medals * 2,
                    "inbox": 0,
                    "over": 0,
                }
            ],
        }
    ]
    assert set(result.updates) == {"unit", "wallet"}


def test_fully_awakened_cards_only_pay_medals(setup):
    resources, state, _, _ = setup
    potential(state, 11)["all_pt"] = 5
    roster = deepcopy(state["units"])
    result = resources.grant(state, [order("unit.card.11", 1)], NOW)
    assert state["units"] == roster
    assert set(result.updates) == {"wallet"}
    assert (result.deliveries[0]["inc"], result.deliveries[0]["over"]) == (0, 1)
    assert resources.balance(state, MEDAL) == 5


def test_medal_overflow_follows_the_delivery_policy(setup):
    resources, state, _, _ = setup
    potential(state, 11)["all_pt"] = 5
    resources.grant(state, [order(MEDAL, 9_999_999)], NOW)
    original = deepcopy(state)
    with pytest.raises(ResourceCapacityError):
        resources.grant(state, [order("unit.card.11", 1)], NOW)
    assert state == original
    result = resources.grant(state, [order("unit.card.11", 1)], NOW, policy="mail")
    assert result.deliveries[0]["derived"] == [
        {"rid": MEDAL, "val": 5, "inc": 0, "inbox": 5, "over": 0}
    ]
    assert state["inbox"][0]["reward"] == {"rid": MEDAL, "val": 5}


def test_duplicate_rules_follow_the_configuration(setup):
    resources, state, settings, _ = setup
    rules = settings["resources"]["duplicate_unit"]
    rules.update(potential_cap=1, compensation_rid="wallet.point.gold")
    rules["compensation"]["R"] = 7
    configure(setup)
    resources.grant(state, [order("unit.card.11", 3)], NOW)
    assert potential(state, 11)["all_pt"] == 1
    assert wallet(state, "gold")["val"] == 514
    del rules["compensation"]["R"]
    configure(setup)
    original = deepcopy(state)
    with pytest.raises(ValueError, match="compensation for rarity R"):
        resources.grant(state, [order("unit.card.11", 1)], NOW)
    assert state == original
    rules["compensation"]["R"] = 0
    configure(setup)
    result = resources.grant(state, [order("unit.card.11", 1)], NOW)
    assert result.deliveries == [
        {"rid": "unit.card.11", "val": 1, "inc": 0, "inbox": 0, "over": 1}
    ]
    assert result.updates == {}


@pytest.mark.parametrize("amount", [1, 2])
def test_full_unit_capacity_can_send_new_card_to_mail(setup, amount):
    resources, state, settings, _ = setup
    settings["resources"]["unit_capacity"] = 2
    configure(setup)
    result = resources.grant(state, [order("unit.card.6", amount)], NOW, policy="mail")
    assert len(state["units"]) == 2
    assert state["resource_sequences"]["unit"] == 3
    assert state["inbox"][0]["reward"] == {"rid": "unit.card.6", "val": amount}
    assert result.updates == {}
    assert result.deliveries == [
        {"rid": "unit.card.6", "val": amount, "inc": 0, "inbox": amount, "over": 0}
    ]


def test_full_unit_capacity_refuses_or_discards_new_cards(setup):
    resources, state, settings, _ = setup
    settings["resources"]["unit_capacity"] = 2
    configure(setup)
    original = deepcopy(state)
    with pytest.raises(ResourceCapacityError):
        resources.grant(state, [order("unit.card.6", 1)], NOW)
    assert state == original
    result = resources.grant(state, [order("unit.card.6", 1)], NOW, policy="discard")
    assert state == original
    assert result.deliveries == [
        {"rid": "unit.card.6", "val": 1, "inc": 0, "inbox": 0, "over": 1}
    ]


def test_owned_cards_gain_potential_with_a_full_roster(setup):
    resources, state, settings, _ = setup
    settings["resources"]["unit_capacity"] = 2
    configure(setup)
    result = resources.grant(state, [order("unit.card.22", 2)], NOW)
    assert potential(state, 22)["all_pt"] == 2
    assert result.deliveries == [
        {"rid": "unit.card.22", "val": 2, "inc": 0, "inbox": 0, "over": 0}
    ]
    assert state["inbox"] == []


def test_stamina_debit_and_grant_report_module_changes(setup):
    resources, state, _, _ = setup
    result = resources.spend(state, [order("stamina.quest", 8)], NOW)
    stamina = state["modules"]["stamina"][0]
    assert stamina["amount"] == 62
    assert stamina["latest_recovered_at"] == NOW
    assert result.updates == {"module_prop": {"stamina": [stamina]}}
    resources.grant(state, [order("stamina.quest", 100)], NOW + 1)
    assert state["modules"]["stamina"][0]["amount"] == 162


@pytest.mark.parametrize(
    "rid",
    [
        "wallet.point.未知",
        "item.material.未知",
        "item.equip.999999",
        "skill.attachable.999999",
        "unit.card.999999",
        "unit.card.01",
    ],
)
def test_unknown_resources_are_rejected_without_new_state(setup, rid):
    resources, state, _, _ = setup
    original = deepcopy(state)
    with pytest.raises(UnknownResource):
        resources.grant(state, [order("wallet.point.gold", 1), order(rid, 1)], NOW)
    assert state == original


def test_check_validates_orders_without_touching_the_state(setup):
    resources, state, _, _ = setup
    original = deepcopy(state)
    resources.check([order("wallet.point.gold", 5), order("unit.card.6", 1)])
    with pytest.raises(UnknownResource):
        resources.check([order("wallet.point.未知", 1)])
    with pytest.raises(ValueError, match="nonnegative Int32"):
        resources.check([order("wallet.point.gold", -1)])
    assert state == original


@pytest.mark.parametrize("amount", [-1, 1.5, True, "1", 2147483648])
def test_resource_quantities_follow_int32_contract(setup, amount):
    resources, state, _, _ = setup
    original = deepcopy(state)
    with pytest.raises(ValueError, match="Int32"):
        resources.grant(state, [order("wallet.point.gold", amount)], NOW)
    assert state == original


def test_zero_grants_leave_state_unchanged_and_preserve_unicode(setup):
    resources, state, _, _ = setup
    state["player"]["nickname"] = "魔物娘 🎲"
    original = deepcopy(state)
    result = resources.grant(state, [order("wallet.point.gold", 0)], NOW)
    assert result.deliveries == []
    assert result.updates == {}
    assert state == original


@pytest.mark.parametrize(
    ("category", "name", "rid"),
    [
        ("unit", "6", "unit.card.6"),
        ("equip", "9401", "item.equip.9401"),
        ("item", "item.material.Bom2", "item.material.Bom2"),
        ("point", "free_coin", "wallet.point.free_coin"),
        ("unit_exp", "sr_all", "wallet.unit_exp.sr_all"),
        ("attachable", "1175", "skill.attachable.1175"),
    ],
)
def test_reward_table_categories_map_to_exact_registered_rids(
    setup, category, name, rid
):
    resources = setup[0]
    assert resources.reward_order(
        {"m_walletCategory": category, "m_walletName": name, "m_count": 1}
    ) == {"rid": rid, "val": 1}


def test_reward_placeholders_and_unknown_names(setup):
    resources = setup[0]
    assert (
        resources.reward_order(
            {"m_walletCategory": "", "m_walletName": "", "m_count": 1}
        )
        is None
    )
    assert (
        resources.reward_order(
            {"m_walletCategory": "point", "m_walletName": "gold", "m_count": 0}
        )
        is None
    )
    with pytest.raises(UnknownResource):
        resources.reward_order(
            {"m_walletCategory": "item", "m_walletName": "Bom2", "m_count": 1}
        )


def test_every_nonempty_tutorial_reward_resolves_and_grants(setup):
    resources, state, _, _ = setup
    awards = []
    for quest in resources.masters.table("quest_data"):
        if quest["id"] not in range(10000, 10005):
            continue
        for value in quest.values():
            if isinstance(value, dict) and "m_rewards" in value:
                for reward in value["m_rewards"]:
                    converted = resources.reward_order(reward)
                    if converted is not None:
                        awards.append(converted)
    result = resources.grant(state, awards, NOW)
    assert resources.balance(state, "unit.card.6") == 1
    assert resources.balance(state, "unit.card.57") == 1
    assert resources.balance(state, "wallet.point.free_coin") == 400
    assert len(result.deliveries) == len(awards)


def test_mail_policy_reads_updated_configuration_and_preserves_unicode(setup):
    resources, state, settings, path = setup
    wallet(state, "gold")["val"] = 99999999
    resources.grant(state, [order("wallet.point.gold", 1)], NOW, policy="mail")
    settings["mail"]["inbox"] = "贈り物 🎁"
    settings["mail"]["lifetime_seconds"] = 60
    path.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
    resources.grant(state, [order("wallet.point.gold", 1)], NOW + 1, policy="mail")
    assert state["inbox"][-1]["inbox"] == "贈り物 🎁"
    assert state["inbox"][-1]["expire_at"] == DAY_END


@pytest.mark.parametrize(
    ("offset", "retained", "converted"), [(-1, 3, 0), (0, 1, 700), (10, 3, 0)]
)
def test_conversion_window_uses_jst_start_inclusive_end_exclusive(
    setup, offset, retained, converted
):
    resources, state, _, _ = setup
    row = next(
        row
        for row in resources.masters.table("item_limit_table")
        if row["item_rid"] == "skill.attachable.1175"
    )
    tz = timezone(timedelta(hours=9))
    row["start_at"] = datetime.fromtimestamp(NOW, tz).strftime("%Y/%m/%d %H:%M:%S")
    row["end_at"] = datetime.fromtimestamp(NOW + 10, tz).strftime("%Y/%m/%d %H:%M:%S")
    resources.grant(state, [order("skill.attachable.1175", 3)], NOW + offset)
    assert resources.balance(state, "skill.attachable.1175") == retained
    assert resources.balance(state, "wallet.gachamedal.gmedal_common") == converted


def test_conversion_cycle_fails_without_partial_state(setup):
    resources, state, _, _ = setup
    row = next(
        row
        for row in resources.masters.table("item_limit_table")
        if row["item_rid"] == "skill.attachable.1175"
    )
    row["converted_item_rid"] = "skill.attachable.1175"
    original = deepcopy(state)
    with pytest.raises(ValueError, match="conversion cycle"):
        resources.grant(state, [order("skill.attachable.1175", 2)], NOW)
    assert state == original


def test_resource_registry_reloads_changed_master_capacity(setup, tmp_path):
    resources, state, _, _ = setup
    master_root = tmp_path / "masters"
    data = master_root / "assets/resources/mdf_output/data"
    data.mkdir(parents=True)
    for name in (
        "wallet_template",
        "item_template",
        "equipment",
        "attach_ability_data",
        "item_limit_table",
        "bonus_pass_data",
        "bonus_pass_tier_data",
        "season_pass_data",
        "unit_rarity",
        "login_bonus",
        "property_sheet",
    ):
        (data / f"{name}.json").write_text(
            json.dumps(resources.masters.document(name)), encoding="utf-8"
        )
    live = Resources(
        MasterData(master_root),
        resources.config,
        resources.mailbox,
        resources.progression,
    )
    live.grant(state, [order("item.equip.9401", 1)], NOW)
    path = data / "equipment.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    row = next(row for row in document["table"] if row["item_id"] == "item.equip.9401")
    row["val_cap"] = 1
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ResourceCapacityError):
        live.grant(state, [order("item.equip.9401", 1)], NOW)


def experience(state):
    return next(
        row for row in state["modules"]["experience"] if row["section"] == "player_rank"
    )


def test_instant_stamina_rewards_restore_quest_stamina(setup):
    resources, state, _, _ = setup
    next(row for row in state["modules"]["stamina"] if row["section"] == "quest")[
        "amount"
    ] = 10
    changes = resources.grant(state, [order("wallet.instant.stamina", 25)], NOW)
    assert changes.deliveries == [
        {"rid": "wallet.instant.stamina", "val": 25, "inc": 25, "inbox": 0, "over": 0}
    ]
    assert resources.balance(state, "wallet.instant.stamina") == 35
    assert resources.balance(state, "stamina.quest") == 35
    assert [row["amount"] for row in changes.updates["module_prop"]["stamina"]] == [35]


def test_instant_experience_rewards_level_the_player(setup):
    resources, state, _, _ = setup
    changes = resources.grant(state, [order("wallet.instant.pexp", 700)], NOW)
    assert changes.deliveries == [
        {"rid": "wallet.instant.pexp", "val": 700, "inc": 700, "inbox": 0, "over": 0}
    ]
    assert (experience(state)["exp"], experience(state)["level"]) == (700, 2)
    assert resources.balance(state, "wallet.instant.pexp") == 700
    assert set(changes.updates["module_prop"]) == {"experience", "stamina"}


def test_experience_past_the_level_cap_is_refused_or_discarded(setup):
    resources, state, _, _ = setup
    experience(state)["level_cap"] = 2
    original = deepcopy(state)
    with pytest.raises(ResourceCapacityError):
        resources.grant(state, [order("wallet.instant.pexp", 700)], NOW)
    assert state == original
    changes = resources.grant(
        state, [order("wallet.instant.pexp", 700)], NOW, policy="discard"
    )
    assert changes.deliveries == [
        {"rid": "wallet.instant.pexp", "val": 700, "inc": 600, "inbox": 0, "over": 100}
    ]
    assert (experience(state)["exp"], experience(state)["level"]) == (600, 2)


def test_bonus_pass_points_progress_the_running_season_pass(setup):
    resources, state, _, _ = setup
    changes = resources.grant(state, [order("bonus_pass.point.9101", 10)], NOW)
    assert changes.deliveries == [
        {"rid": "bonus_pass.point.9101", "val": 10, "inc": 10, "inbox": 0, "over": 0}
    ]
    assert state["bonus_passes"] == [
        {
            "pass_id": 9105,
            "tier": 1,
            "progression": 10,
            "free_track": {"is_locked": False, "received_tier": 0, "remind_tier": 0},
            "premium_track": {"is_locked": True, "received_tier": 0, "remind_tier": 0},
            "tier_progressed_at": NOW,
        }
    ]
    assert changes.updates["bonuspass_progression"] == state["bonus_passes"]
    resources.grant(state, [order("bonus_pass.point.9101", 5)], NOW + 5)
    row = state["bonus_passes"][0]
    assert (row["tier"], row["progression"], row["tier_progressed_at"]) == (1, 15, NOW)
    resources.grant(state, [order("bonus_pass.point.9101", 5)], NOW + 9)
    row = state["bonus_passes"][0]
    assert (row["tier"], row["progression"], row["tier_progressed_at"]) == (
        2,
        20,
        NOW + 9,
    )


def test_bonus_pass_points_without_a_running_pass_are_lost(setup):
    resources, state, _, _ = setup
    december = resources.masters.timestamp("2026/12/05 12:00:00")
    changes = resources.grant(state, [order("bonus_pass.point.9101", 10)], december)
    assert changes.deliveries == [
        {"rid": "bonus_pass.point.9101", "val": 10, "inc": 0, "inbox": 0, "over": 10}
    ]
    assert state["bonus_passes"] == []
    assert changes.updates == {}


def test_bonus_pass_rewards_resolve_but_have_no_balance(setup):
    resources, state, _, _ = setup
    row = {
        "m_walletCategory": "bonus_pass",
        "m_walletName": "point.9101",
        "m_count": 10,
    }
    assert resources.reward_order(row) == order("bonus_pass.point.9101", 10)
    with pytest.raises(UnknownResource):
        resources.reward_order({**row, "m_walletName": "point.404"})
    with pytest.raises(ValueError, match="not a balance"):
        resources.balance(state, "bonus_pass.point.9101")


def test_bom_cards_join_the_roster_once(setup):
    resources, state, _, _ = setup
    first = resources.grant(state, [{"rid": "bom.card.9009", "val": 1}], NOW)
    assert first.deliveries == [
        {"rid": "bom.card.9009", "val": 1, "inc": 1, "inbox": 0, "over": 0}
    ]
    added = state["boms"][-1]
    assert added["card_id"] == 9009
    assert added["instance_id"] == 2
    assert first.updates["bom"] == [added]
    with pytest.raises(ResourceCapacityError):
        resources.grant(state, [{"rid": "bom.card.9009", "val": 1}], NOW)
    mailed = resources.grant(
        state, [{"rid": "bom.card.9009", "val": 2}], NOW, policy="mail"
    )
    assert mailed.deliveries[0]["inbox"] == 2
    assert state["inbox"][-1]["reward"] == {"rid": "bom.card.9009", "val": 2}
    with pytest.raises(UnknownResource):
        resources.check([{"rid": "bom.card.1", "val": 1}])
    assert resources.reward_order(
        {"m_walletCategory": "bom", "m_walletName": "10000", "m_count": 1}
    ) == {"rid": "bom.card.10000", "val": 1}
