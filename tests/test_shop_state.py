from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from data_loader import load_all_data
from game_state import GameState
from recommender import (
    analyze_event,
    estimated_avg_shop_item_price,
    shop_item_tier_distribution_for_day,
)
from shop_state import merge_effective_shop
from shop_state import available_merchant_profiles_for_day, merchant_available_on_day


DATA_DIR = PROJECT_ROOT / "data"


def test_shop_item_tier_distribution_normalizes_and_caps_after_day_14() -> None:
    rarity_rules = load_all_data(DATA_DIR)["rarity_rules"]

    day_12 = shop_item_tier_distribution_for_day(rarity_rules, 12)
    assert round(sum(day_12.values()), 6) == 1.0

    assert shop_item_tier_distribution_for_day(
        rarity_rules, 99
    ) == shop_item_tier_distribution_for_day(rarity_rules, 14)


def test_estimated_avg_shop_item_price_uses_distribution_without_filtering_pool() -> None:
    rarity_rules = {
        "shop_item_tier_distribution_by_day": {
            "1": {"bronze": 0.5, "silver": 0.5, "gold": 0.0, "diamond": 0.0}
        }
    }
    cards = {
        "Bronze Item": {
            "type": "Item",
            "buy_prices": {"bronze": 2},
        },
        "Silver Item": {
            "type": "Item",
            "buy_prices": {"silver": 6},
        },
    }
    merchant_pool = [
        {"name": name, "raw": card}
        for name, card in cards.items()
    ]

    assert estimated_avg_shop_item_price(1, merchant_pool, cards, rarity_rules) == 4.0
    assert [card["name"] for card in merchant_pool] == ["Bronze Item", "Silver Item"]


def test_shop_refresh_requires_budget_for_estimated_item_price() -> None:
    cards = {
        "Target": {
            "type": "Item",
            "hero": "Common",
            "heroes": ["Common"],
            "tags": ["tool"],
            "min_rarity": "bronze",
            "max_rarity": "diamond",
            "buy_prices": {
                "bronze": 10,
                "silver": 10,
                "gold": 10,
                "diamond": 10,
            },
        }
    }
    result = analyze_event(
        event_name="Test Merchant",
        event_data={
            "name": "Test Merchant",
            "event_category": "shops",
            "shop_pool": {
                "exact_names": ["Target"],
                "rarity_filter": {"min": "bronze", "max": "diamond"},
                "hero_scope": "any",
            },
        },
        cards=cards,
        build_name="test",
        build_data={"core_cards": ["Target"]},
        current_day=1,
        rarity_rules={
            "shop_item_tier_distribution_by_day": {
                "1": {"bronze": 1.0, "silver": 0.0, "gold": 0.0, "diamond": 0.0}
            }
        },
        current_hero="Vanessa",
        current_shop={"refresh_available": True, "refresh_cost": 1},
        current_gold=5,
    )

    assert result["pool_stats"]["expected_core_in_shop"] == 3.0
    assert result["shop_entry_analysis"]["gold_support"]["current_gold"] == 5
    assert result["shop_entry_analysis"]["gold_support"]["estimated_purchase_price"] == 10.0
    assert result["shop_entry_analysis"]["gold_support"]["supports_entry"] is False
    assert result["shop_entry_analysis"]["status"] == "not_actionable"
    assert result["shop_decision"]["action"] == "defer"


def test_shop_entry_zero_gold_keeps_gold_known_and_insufficient() -> None:
    result = analyze_event(
        event_name="Test Merchant",
        event_data={
            "name": "Test Merchant",
            "event_category": "shops",
            "shop_pool": {
                "exact_names": ["Target"],
                "rarity_filter": {"min": "bronze", "max": "diamond"},
                "hero_scope": "any",
            },
        },
        cards={
            "Target": {
                "type": "Item",
                "hero": "Common",
                "heroes": ["Common"],
                "tags": ["tool"],
                "min_rarity": "bronze",
                "max_rarity": "diamond",
            },
        },
        build_name="test",
        build_data={"core_cards": ["Target"]},
        current_day=1,
        rarity_rules={},
        current_hero="Vanessa",
        current_shop={"refresh_available": True, "refresh_cost": 1},
        current_gold=0,
    )

    assert result["shop_entry_analysis"]["gold_support"]["current_gold"] == 0
    assert result["shop_entry_analysis"]["gold_support"]["gold_known"] is True
    assert result["shop_entry_analysis"]["gold_support"]["supports_entry"] is False
    assert result["shop_entry_analysis"]["gold_support"]["status"] == "insufficient"


def test_effective_shop_uses_merchant_template_but_keeps_item_tiers_separate() -> None:
    data = {
        "merchant_profiles": {
            "Pol": {
                "name": "Pol",
                "source_id": "pol-source",
                "template_id": "pol-template",
                "shop_tier": "silver",
                "base_refresh_cost": 2,
                "base_refresh_count": 3,
                "refresh_enabled": True,
                "sold_item_tier_filters": ["gold", "diamond"],
            }
        },
        "merchant_profile_index": {},
    }
    data["merchant_profile_index"] = {
        "pol": data["merchant_profiles"]["Pol"],
        "pol-template": data["merchant_profiles"]["Pol"],
    }

    effective = merge_effective_shop(
        data,
        {
            "merchant_name": "Pol",
            "visible_items": [{"name": "Some Item", "rarity": "gold"}],
        },
    )

    assert effective is not None
    assert effective["shop_tier"] == "silver"
    assert effective["sold_item_tier_filters"] == ["gold", "diamond"]
    assert effective["refresh_cost"] == 2
    assert effective["refresh_cost_source"] == "template"
    assert effective["refreshes_remaining"] == 3


def test_merchant_meta_day_ranges_are_loaded_without_polluting_current_shop() -> None:
    data = load_all_data(DATA_DIR)

    assert data["merchant_meta_warnings"] == []
    assert data["merchant_profiles"]["Aimbot"]["available_day_range"] == [6, None]
    assert data["merchant_profiles"]["Curio"]["available_day_range"] == [2, 5]
    assert merchant_available_on_day(data["merchant_profiles"]["Aimbot"], 1) is False
    assert merchant_available_on_day(data["merchant_profiles"]["Aimbot"], 6) is True
    assert merchant_available_on_day(data["merchant_profiles"]["Curio"], 10) is False

    day_one_merchants = available_merchant_profiles_for_day(
        data["merchant_profiles"],
        1,
    )
    day_six_merchants = available_merchant_profiles_for_day(
        data["merchant_profiles"],
        6,
    )
    day_ten_merchants = available_merchant_profiles_for_day(
        data["merchant_profiles"],
        10,
    )

    assert "Aimbot" not in day_one_merchants
    assert "Aimbot" in day_six_merchants
    assert "Curio" not in day_ten_merchants

    effective = merge_effective_shop(data, {"merchant_name": "Aimbot"})
    assert effective is not None
    assert "available_day_range" not in effective
    assert "available_days" not in effective
    assert effective["merchant_profile"]["available_day_range"] == [6, None]


def test_effective_shop_runtime_refresh_values_override_template() -> None:
    data = {
        "merchant_profiles": {
            "Pol": {
                "name": "Pol",
                "source_id": "pol-source",
                "shop_tier": "silver",
                "base_refresh_cost": 2,
                "base_refresh_count": 3,
                "refresh_enabled": True,
            }
        },
        "merchant_profile_index": {},
    }
    data["merchant_profile_index"] = {
        "pol-source": data["merchant_profiles"]["Pol"]
    }

    effective = merge_effective_shop(
        data,
        {
            "merchant_id": "pol-source",
            "refresh_cost": 1,
            "refresh_available": False,
            "refreshes_remaining": 1,
        },
    )

    assert effective is not None
    assert effective["refresh_cost"] == 1
    assert effective["refresh_cost_source"] == "runtime"
    assert effective["refresh_available"] is False
    assert effective["refreshes_used"] == 2


def test_state_keeps_combat_health_separate_from_prestige() -> None:
    state = GameState.from_dict(
        {
            "hero": "Vanessa",
            "build": "VanessaAquaticAmmo",
            "day": 2,
            "health": 550,
            "prestige": 18,
        }
    )

    assert state.combat_health == 550
    assert state.health == 550
    assert state.prestige == 18
    assert state.max_prestige is None


def test_shop_prefers_visible_items_and_does_not_refresh_past_target() -> None:
    data = load_all_data(DATA_DIR)
    event = data["events"]["Colt"]
    result = analyze_event(
        event_name="Colt",
        event_data=event,
        cards=data["cards"],
        build_name="huokai",
        build_data=data["builds"]["huokai"],
        current_day=6,
        rarity_rules=data["rarity_rules"],
        current_hero="Vanessa",
        current_shop={
            "visible_items": [{"name": "Burnacuda"}],
            "refresh_available": True,
            "refresh_cost": 1,
            "refreshes_remaining": 2,
        },
        current_gold=8,
    )

    assert [card["name"] for card in result["possible_cards"]] == ["Burnacuda"]
    assert result["shop_decision"]["action"] == "buy"
    assert result["shop_inside_analysis"]["phase"] == "shop_inside"
    assert result["shop_decision"]["visible_offer_count"] == 1
    assert result["shop_inside_analysis"]["refreshes_remaining"] == 2
    assert result["shop_inside_analysis"]["refresh_scope"] == "current_shop_only"
    assert result["shop_inside_analysis"]["refresh_carries_over"] is False
    assert result["shop_inside_analysis"]["worth_buying"][0]["name"] == "Burnacuda"


def test_unknown_refresh_cost_never_pushes_refresh() -> None:
    data = load_all_data(DATA_DIR)
    event = data["events"]["Colt"]
    result = analyze_event(
        event_name="Colt",
        event_data=event,
        cards=data["cards"],
        build_name="huokai",
        build_data=data["builds"]["huokai"],
        current_day=6,
        rarity_rules=data["rarity_rules"],
        current_hero="Vanessa",
        current_shop={
            "visible_items": [{"name": "Unknown Visible Item"}],
            "refresh_available": True,
            "refresh_cost": None,
        },
        current_gold=20,
    )

    assert result["shop_decision"]["action"] == "skip"
    assert result["shop_decision"]["reason"] == "refresh_cost_unknown"


def test_empty_visible_items_is_shop_entry_not_inside_shop() -> None:
    data = load_all_data(DATA_DIR)
    event = data["events"]["Colt"]
    result = analyze_event(
        event_name="Colt",
        event_data=event,
        cards=data["cards"],
        build_name="huokai",
        build_data=data["builds"]["huokai"],
        current_day=6,
        rarity_rules=data["rarity_rules"],
        current_hero="Vanessa",
        current_shop={
            "visible_items": [],
            "refresh_available": True,
            "refresh_cost": 1,
        },
        current_gold=8,
        all_builds=data["builds"],
        data_context=data,
    )

    assert result["shop_entry_analysis"]["phase"] == "shop_entry"
    assert result["shop_inside_analysis"] is None
    assert result["shop_decision"]["using_visible_items"] is False
    assert result["pool_stats"]["total_pool_count"] == result["shop_entry_analysis"]["pool_count"]
