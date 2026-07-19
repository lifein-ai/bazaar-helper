from __future__ import annotations

from typing import Any

from build_strategy import build_applies_to_day, build_phase_relation, get_game_stage_for_day
from dooley_rules import dooley_build_is_blocked_by_missing_core
from shop_pool_cache import (
    get_cached_shop_pool_summary,
    hydrate_cached_shop_cards,
)


RARITY_ORDER = {
    "bronze": 1,
    "silver": 2,
    "gold": 3,
    "diamond": 4,
    "legendary": 5,
}
RARITY_BY_ORDER = {order: rarity for rarity, order in RARITY_ORDER.items()}

ROLE_LABELS = {
    "core": "core",
    "transition": "transition",
    "optional": "optional",
    "unrelated": "unrelated",
}

RECOMMENDATION_RANK = {
    "High Value": 1,
    "Medium Value": 2,
    "Low Value": 3,
}

VISIBLE_OFFER_COUNT = 3
REFRESH_OFFER_COUNT = 3
SHOP_ITEM_TIER_DISTRIBUTION_RULE = "shop_item_tier_distribution_by_day"
SHOP_ENTRY_STATUSES = {
    "strong_candidate",
    "candidate",
    "situational",
    "weak_candidate",
    "not_actionable",
    "unknown",
}

ENCHANTMENT_TAGS = {
    "fiery": ["burn"],
    "flame": ["burn"],
    "burn": ["burn"],
    "toxic": ["poison"],
    "poison": ["poison"],
    "icy": ["freeze"],
    "freeze": ["freeze"],
    "shielded": ["shield"],
    "shield": ["shield"],
    "restorative": ["heal"],
    "heal": ["heal"],
    "turbo": ["haste"],
    "haste": ["haste"],
    "deadly": ["crit"],
    "crit": ["crit"],
    "shiny": ["value"],
    "golden": ["gold"],
    "heavy": ["damage"],
    "obsidian": ["damage"],
}


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().lower()


def normalize_text_list(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values or []:
        normalized = normalize_text(value)
        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


def tags_from_enchantments(enchantments: list[str] | None) -> list[str]:
    tags: list[str] = []
    for enchantment in normalize_text_list(enchantments):
        tags.extend(ENCHANTMENT_TAGS.get(enchantment, []))
    return normalize_text_list(tags)


def tag_family(tag: str) -> str:
    normalized = normalize_text(tag)
    if normalized.endswith("reference"):
        return normalized.removesuffix("reference")
    return normalized


def tags_overlap_build_wants(card_tags: list[str], build_data: dict[str, Any]) -> bool:
    card_tag_families = {tag_family(tag) for tag in normalize_text_list(card_tags)}
    wanted_tag_families = {
        tag_family(tag)
        for tag in normalize_text_list(build_data.get("wanted_tags", []))
    }
    return bool(card_tag_families & wanted_tag_families)


def effective_card_tags(
    card_data: dict[str, Any],
    enchantments: list[str] | None = None,
) -> list[str]:
    return normalize_text_list(
        card_data.get("tags", []) + tags_from_enchantments(enchantments)
    )


def rarity_range_intersects(
    card_min: str,
    card_max: str,
    event_min: str,
    event_max: str,
) -> bool:
    card_min = normalize_text(card_min)
    card_max = normalize_text(card_max)
    event_min = normalize_text(event_min)
    event_max = normalize_text(event_max)

    for label, rarity in {
        "card min rarity": card_min,
        "card max rarity": card_max,
        "event min rarity": event_min,
        "event max rarity": event_max,
    }.items():
        if rarity not in RARITY_ORDER:
            raise ValueError(f"Unknown {label}: {rarity}")

    return (
        RARITY_ORDER[card_min] <= RARITY_ORDER[event_max]
        and RARITY_ORDER[event_min] <= RARITY_ORDER[card_max]
    )


def tags_match(card_tags: list[str], reward_tags: list[str], match_mode: str) -> bool:
    reward_tags = normalize_text_list(reward_tags)
    if not reward_tags:
        return True

    card_tag_set = set(normalize_text_list(card_tags))
    reward_tag_set = set(reward_tags)

    if match_mode == "any":
        return bool(card_tag_set & reward_tag_set)

    if match_mode == "all":
        return reward_tag_set.issubset(card_tag_set)

    raise ValueError(f"Unknown match_mode: {match_mode}")


def resolve_event_rarity_filter(
    pool_rule: dict[str, Any],
    current_day: int,
    rarity_rules: dict[str, Any],
) -> dict[str, str] | None:
    fixed_filter = pool_rule.get("rarity_filter")
    if fixed_filter:
        return {
            "min": normalize_text(fixed_filter["min"]),
            "max": normalize_text(fixed_filter["max"]),
        }

    rule_name = pool_rule.get("rarity_rule")
    if not rule_name:
        return None

    if rule_name not in rarity_rules:
        raise ValueError(f"Rarity rule not found: {rule_name}")

    for item in rarity_rules[rule_name]:
        from_day = item["from_day"]
        to_day = item["to_day"]

        if current_day >= from_day and (to_day is None or current_day <= to_day):
            return {
                "min": normalize_text(item["min"]),
                "max": normalize_text(item["max"]),
            }

    raise ValueError(f"Rarity rule {rule_name} does not cover Day {current_day}")


def get_event_card_pool_rule(event_data: dict[str, Any]) -> dict[str, Any] | None:
    event_category = event_data.get("event_category")

    if event_category == "skill_shops":
        shop_pool = event_data.get("shop_pool")
        if shop_pool is not None:
            return shop_pool

        # Older generated event files stored skill filters at the event level.
        return {
            "reward_tags": event_data.get("skill_tags", []),
            "match_mode": "any",
            "rarity_filter": event_data.get("rarity_filter"),
            "rarity_rule": event_data.get("rarity_rule") or "normal_shop_by_day",
            "excluded_tags": ["legendary"],
            "hero_scope": "current",
        }

    if event_category == "shops":
        return event_data.get("shop_pool")

    if event_category == "resource_events":
        card_reward = event_data.get("card_reward", {})
        return card_reward if card_reward.get("enabled") else None

    if event_category == "item_rewards":
        card_reward = event_data.get("card_reward")
        if card_reward:
            return card_reward

    return None


def infer_possible_cards_for_event(
    event_data: dict[str, Any],
    cards: dict[str, Any],
    current_day: int,
    rarity_rules: dict[str, Any],
    current_hero: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    pool_rule = get_event_card_pool_rule(event_data)
    if pool_rule is None:
        return [], None

    reward_tags = normalize_text_list(pool_rule.get("reward_tags", []))
    exact_names = set(pool_rule.get("exact_names", []))
    match_mode = pool_rule.get("match_mode", "any")
    excluded_tags = normalize_text_list(pool_rule.get("excluded_tags", []))
    size_filter = normalize_text_list(pool_rule.get("size_filter", []))
    hero_filter = normalize_text(
        pool_rule.get("hero_filter") or event_data.get("hero_filter")
    )
    hero_scope = normalize_text(pool_rule.get("hero_scope") or "current")
    current_hero = normalize_text(current_hero)
    event_category = normalize_text(event_data.get("event_category"))
    event_name = normalize_text(event_data.get("name"))
    allows_packages = event_name in {"farai", "法莱"}
    allows_loot = bool(pool_rule.get("allow_loot"))
    allows_package = bool(pool_rule.get("allow_package"))
    allows_quest = bool(pool_rule.get("allow_quest"))
    enchantment_required = bool(
        pool_rule.get("enchantment_required") or pool_rule.get("enchanted_shop")
    )
    expected_card_type = "skill" if event_has_skill_reward(event_data) else "item"

    rarity_filter = resolve_event_rarity_filter(pool_rule, current_day, rarity_rules)
    if rarity_filter is None:
        rarity_filter = {"min": "bronze", "max": "diamond"}

    possible_cards: list[dict[str, Any]] = []

    for card_name, card_data in cards.items():
        card_type = normalize_text(
            card_data.get("type") or card_data.get("card_type")
        )
        if card_type != expected_card_type:
            continue

        card_tags = normalize_text_list(card_data.get("tags", []))
        exact_match = card_name in exact_names
        if event_category != "shops" and "package" in card_tags and not allows_packages:
            continue
        if event_category == "shops":
            card_name_text = normalize_text(card_name)
            internal_name_text = normalize_text(card_data.get("internal_name"))
            if "loot" in card_tags and not allows_loot:
                continue
            if "package" in card_tags and not allows_package:
                continue
            if "quest" in card_tags and not allows_quest:
                continue
            if not exact_match and (
                any(tag in card_tags for tag in {"legendary", "debug", "template"})
                or "debug" in card_name_text
                or "debug" in internal_name_text
                or "template" in card_name_text
                or "template" in internal_name_text
            ):
                continue
        card_min = normalize_text(card_data.get("min_rarity"))
        card_max = normalize_text(card_data.get("max_rarity"))

        if not card_min or not card_max:
            continue

        card_hero = normalize_text(card_data.get("hero"))
        card_heroes = {normalize_text(hero) for hero in card_data.get("heroes", [])}

        card_hero_pool = {card_hero} | card_heroes
        if hero_scope == "fixed":
            if not hero_filter or hero_filter not in card_hero_pool:
                continue
        elif hero_scope == "current":
            if not current_hero or current_hero not in card_hero_pool:
                continue
        elif hero_scope == "other":
            if not current_hero or current_hero in card_hero_pool:
                continue
        elif hero_scope != "any":
            continue

        if size_filter and normalize_text(card_data.get("size")) not in size_filter:
            continue

        if any(tag in card_tags for tag in excluded_tags) and not (
            event_category == "shops" and exact_match
        ):
            continue

        if exact_names:
            if card_name not in exact_names:
                continue
        elif not tags_match(card_tags, reward_tags, match_mode):
            continue

        if not rarity_range_intersects(
            card_min,
            card_max,
            rarity_filter["min"],
            rarity_filter["max"],
        ):
            continue

        possible_cards.append(
            {
                "name": card_name,
                "tier": card_data.get("tier", "Unknown"),
                "tags": card_tags,
                "min_rarity": card_min,
                "max_rarity": card_max,
                "enchantment_required": enchantment_required,
                "raw": card_data,
            }
        )

    return possible_cards, rarity_filter


def get_card_role_for_build(
    card_name: str,
    card_data: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
) -> str:
    """
    鍒ゆ柇涓€寮犲崱鍦ㄥ綋鍓?build 閲岀殑瀹氫綅銆?

    浼樺厛绾э細
    1. community_builds.json 閲岀殑 core_cards / transition_cards / optional_cards
    2. card_ratings.json 閲岀殑 build_roles
    3. 榛樿 unrelated

    绀惧尯闃靛鏂囦欢鏄?Build 瀹氫綅鐨勫敮涓€浜嬪疄鏉ユ簮锛?    card_ratings.json 鍙彁渚涘叏灞€鍗＄墝璇勭骇鍜屽彲閫夌殑瀹氫綅琛ュ厖銆?    """

    if card_name in build_data.get("core_cards", []):
        return "core"
    if card_name in build_data.get("transition_cards", []):
        return "transition"
    if card_name in build_data.get("optional_cards", []):
        return "optional"

    build_roles = card_data.get("build_roles", {})
    if build_name in build_roles:
        role = build_roles[build_name]
        return "unrelated" if role == "trap" else role

    return "unrelated"


def get_alt_core_build_hits(
    card_name: str,
    *,
    current_build_name: str,
    current_hero: str | None,
    current_day: int,
    all_builds: dict[str, Any] | None,
    owned_cards: dict[str, str] | None = None,
    cards: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    if not all_builds or not current_hero:
        return []

    hits: list[dict[str, str]] = []
    current_stage = get_game_stage_for_day(current_day)
    for build_name, candidate in all_builds.items():
        if build_name == current_build_name or candidate.get("hero") != current_hero:
            continue
        if build_phase_relation(candidate, current_stage) == "past_build":
            continue
        if dooley_build_is_blocked_by_missing_core(
            hero=current_hero,
            current_day=current_day,
            build_data=candidate,
            owned_cards=owned_cards,
            cards=cards,
        ):
            continue
        if card_name not in candidate.get("core_cards", []):
            continue
        hits.append(
            {
                "build_name": build_name,
                "display_name": candidate.get("display_name") or build_name,
            }
        )

    return hits


def probability_at_least_one(hit_ratio: float, draws: int = VISIBLE_OFFER_COUNT) -> float:
    if hit_ratio <= 0:
        return 0.0
    if hit_ratio >= 1:
        return 1.0
    return 1 - (1 - hit_ratio) ** draws


def rarity_names_in_range(min_rarity: str, max_rarity: str) -> list[str]:
    min_order = RARITY_ORDER[normalize_text(min_rarity)]
    max_order = RARITY_ORDER[normalize_text(max_rarity)]
    return [
        RARITY_BY_ORDER[order]
        for order in range(min_order, max_order + 1)
        if order in RARITY_BY_ORDER
    ]


def expected_card_sell_gold(
    card_data: dict[str, Any],
    rarity_filter: dict[str, str] | None,
) -> float:
    buy_prices = {
        normalize_text(rarity): price
        for rarity, price in (card_data.get("buy_prices") or {}).items()
        if isinstance(price, (int, float))
    }
    if not buy_prices:
        return 0.0

    card_min = normalize_text(card_data.get("min_rarity"))
    card_max = normalize_text(card_data.get("max_rarity"))
    if not card_min or not card_max:
        return 0.0

    event_min = normalize_text(rarity_filter.get("min") if rarity_filter else card_min)
    event_max = normalize_text(rarity_filter.get("max") if rarity_filter else card_max)
    min_order = max(RARITY_ORDER[card_min], RARITY_ORDER[event_min])
    max_order = min(RARITY_ORDER[card_max], RARITY_ORDER[event_max])
    if min_order > max_order:
        return 0.0

    sell_values = [
        buy_prices[rarity] / 2
        for rarity in rarity_names_in_range(
            RARITY_BY_ORDER[min_order],
            RARITY_BY_ORDER[max_order],
        )
        if rarity in buy_prices
    ]
    if not sell_values:
        return 0.0

    return sum(sell_values) / len(sell_values)


def expected_unrelated_sell_gold(
    analyzed_cards: list[dict[str, Any]],
    event_data: dict[str, Any],
    draw_count: int,
) -> float:
    if event_data.get("event_category") != "item_rewards":
        return 0.0
    if not analyzed_cards:
        return 0.0

    total_sell_value = sum(
        card.get("sell_gold", 0.0)
        for card in analyzed_cards
        if card.get("role") == "unrelated"
    )
    return draw_count * total_sell_value / len(analyzed_cards)


def shop_item_tier_distribution_for_day(
    rarity_rules: dict[str, Any],
    current_day: int,
) -> dict[str, float]:
    distributions = rarity_rules.get(SHOP_ITEM_TIER_DISTRIBUTION_RULE)
    if not isinstance(distributions, dict):
        return {}

    try:
        day = max(1, int(current_day))
    except (TypeError, ValueError):
        return {}

    key = str(min(day, 14))
    raw_distribution = distributions.get(key)
    if not isinstance(raw_distribution, dict):
        return {}

    distribution: dict[str, float] = {}
    for rarity in ("bronze", "silver", "gold", "diamond"):
        value = raw_distribution.get(rarity)
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        distribution[rarity] = float(value)

    total = sum(distribution.values())
    if total <= 0:
        return {}
    return {rarity: value / total for rarity, value in distribution.items()}


def average_price_by_rarity(cards: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, list[float]] = {}
    for card in cards:
        raw = card.get("raw") if isinstance(card, dict) else None
        if not isinstance(raw, dict):
            continue
        buy_prices = raw.get("buy_prices")
        if not isinstance(buy_prices, dict):
            continue
        for rarity, price in buy_prices.items():
            normalized = normalize_text(str(rarity))
            if normalized not in RARITY_ORDER or not isinstance(price, (int, float)):
                continue
            values.setdefault(normalized, []).append(float(price))

    return {
        rarity: sum(prices) / len(prices)
        for rarity, prices in values.items()
        if prices
    }


def estimated_avg_shop_item_price(
    current_day: int,
    merchant_pool: list[dict[str, Any]],
    all_cards: dict[str, Any],
    rarity_rules: dict[str, Any],
) -> float | None:
    distribution = shop_item_tier_distribution_for_day(rarity_rules, current_day)
    if not distribution:
        return None

    pool_prices = average_price_by_rarity(merchant_pool)
    global_prices = average_price_by_rarity(
        [
            {"raw": card_data}
            for card_data in all_cards.values()
            if isinstance(card_data, dict)
            and normalize_text(card_data.get("type") or card_data.get("card_type"))
            == "item"
        ]
    )

    weighted_total = 0.0
    usable_probability = 0.0
    for rarity, probability in distribution.items():
        price = pool_prices.get(rarity, global_prices.get(rarity))
        if price is None:
            continue
        weighted_total += probability * price
        usable_probability += probability

    if usable_probability <= 0:
        return None
    return weighted_total / usable_probability


def _visible_entry_is_skill(item: dict[str, Any]) -> bool:
    raw = item.get("raw") if isinstance(item.get("raw"), dict) else {}
    card_type = item.get("card_type") or item.get("type") or raw.get("card_type") or raw.get("type")
    return normalize_text(str(card_type)) == "skill"


def max_known_visible_item_price(
    visible_items: Any,
    *,
    ignore_skill_prices: bool = False,
) -> int | None:
    if not isinstance(visible_items, list):
        return None
    prices: list[int] = []
    for item in visible_items:
        if not isinstance(item, dict):
            continue
        if ignore_skill_prices and _visible_entry_is_skill(item):
            continue
        price = item.get("price")
        if isinstance(price, (int, float)) and price >= 0:
            prices.append(int(price))
    return max(prices) if prices else None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _merchant_available_on_day(summary: dict[str, Any], current_day: int) -> bool | None:
    available = summary.get("available_on_day")
    if isinstance(available, bool):
        return available

    day_range = summary.get("available_day_range")
    if not isinstance(day_range, list) or len(day_range) != 2:
        return None
    try:
        start = int(day_range[0])
        day = int(current_day)
    except (TypeError, ValueError):
        return None
    raw_end = day_range[1]
    if raw_end in (None, ""):
        return day >= start
    try:
        end = int(raw_end)
    except (TypeError, ValueError):
        return None
    return start <= day <= end


def _shop_is_baseline_candidate(event_data: dict[str, Any]) -> bool:
    return event_data.get("event_category") in {"shops", "skill_shops"}


def _card_strategy_bucket(
    card_name: str,
    card_data: dict[str, Any],
    *,
    build_name: str,
    build_data: dict[str, Any],
    current_day: int,
    current_hero: str | None,
    owned_cards: dict[str, str],
    all_builds: dict[str, Any] | None,
) -> str:
    role = get_card_role_for_build(card_name, card_data, build_name, build_data)
    if role == "core":
        return "missing_current_core" if card_name not in owned_cards else "current_core"
    if role == "transition":
        return "current_transition"
    if role == "optional":
        return "current_optional"

    if not all_builds or not current_hero:
        return "unrelated"

    current_stage = get_game_stage_for_day(current_day)
    has_past_core = False
    for candidate_name, candidate in all_builds.items():
        if candidate_name == build_name or candidate.get("hero") != current_hero:
            continue
        if card_name not in candidate.get("core_cards", []):
            continue
        if dooley_build_is_blocked_by_missing_core(
            hero=current_hero,
            current_day=current_day,
            build_data=candidate,
            owned_cards=owned_cards,
        ):
            continue
        relation = build_phase_relation(candidate, current_stage)
        if relation == "past_build":
            has_past_core = True
            continue
        return "future_core"

    return "expired_core" if has_past_core else "unrelated"


def _shop_target_profile(
    cards_in_pool: list[dict[str, Any]],
    *,
    build_name: str,
    build_data: dict[str, Any],
    current_day: int,
    current_hero: str | None,
    owned_cards: dict[str, str],
    all_builds: dict[str, Any] | None,
) -> dict[str, Any]:
    counts = {
        "missing_current_core": 0,
        "current_core": 0,
        "current_transition": 0,
        "current_optional": 0,
        "future_core": 0,
        "expired_core": 0,
        "unrelated": 0,
    }
    sample_hits: dict[str, list[str]] = {key: [] for key in counts}
    weights = {
        "missing_current_core": 6.0,
        "current_core": 5.0,
        "current_transition": 3.5,
        "current_optional": 2.5,
        "future_core": 1.0,
        "expired_core": 0.0,
        "unrelated": 0.0,
    }
    weighted_score = 0.0

    for card in cards_in_pool:
        card_name = str(card.get("name") or "")
        raw = card.get("raw") if isinstance(card.get("raw"), dict) else {}
        bucket = _card_strategy_bucket(
            card_name,
            raw,
            build_name=build_name,
            build_data=build_data,
            current_day=current_day,
            current_hero=current_hero,
            owned_cards=owned_cards,
            all_builds=all_builds,
        )
        counts[bucket] = counts.get(bucket, 0) + 1
        weighted_score += weights.get(bucket, 0.0)
        if card_name and len(sample_hits.setdefault(bucket, [])) < 5:
            sample_hits[bucket].append(card_name)

    pool_count = len(cards_in_pool)
    actionable_count = (
        counts["missing_current_core"]
        + counts["current_core"]
        + counts["current_transition"]
        + counts["current_optional"]
    )
    soft_target_count = actionable_count + counts["future_core"]
    return {
        "pool_count": pool_count,
        "counts": counts,
        "sample_hits": {key: value for key, value in sample_hits.items() if value},
        "actionable_target_count": actionable_count,
        "soft_target_count": soft_target_count,
        "weighted_score": weighted_score,
        "weighted_density": weighted_score / pool_count if pool_count else 0.0,
        "actionable_density": actionable_count / pool_count if pool_count else 0.0,
        "soft_target_density": soft_target_count / pool_count if pool_count else 0.0,
        "theoretical_only": actionable_count == 0 and counts["future_core"] > 0,
    }


def _density_band_from_baseline(current: float, baseline: list[float]) -> tuple[str, float | None]:
    values = sorted(value for value in baseline if isinstance(value, (int, float)))
    if not values:
        return "unknown", None
    below_or_equal = sum(1 for value in values if value <= current)
    percentile = below_or_equal / len(values)
    if percentile >= 2 / 3:
        return "high", percentile
    if percentile <= 1 / 3:
        return "low", percentile
    return "medium", percentile


def _entry_gold_support(
    *,
    current_gold: int | None,
    summary: dict[str, Any] | None,
    estimated_avg_item_price: float | None,
    free_purchase: bool = False,
) -> dict[str, Any]:
    if current_gold is None:
        return {
            "status": "unknown",
            "gold_known": False,
            "price_known": False,
            "current_gold": None,
            "supports_entry": None,
            "supports_refresh_then_buy": None,
            "reason": "current_gold_unknown",
        }
    if free_purchase:
        refresh_cost = (
            _optional_nonnegative_int(summary.get("base_refresh_cost"))
            if isinstance(summary, dict)
            else None
        )
        return {
            "status": "free_purchase",
            "gold_known": True,
            "price_known": True,
            "current_gold": current_gold,
            "supports_entry": True,
            "supports_refresh_then_buy": (
                current_gold >= refresh_cost if refresh_cost is not None else None
            ),
            "estimated_purchase_price": 0,
            "estimated_purchase_price_source": "skill_shop_free",
            "base_refresh_cost": refresh_cost,
        }

    price = None
    source = "unknown"
    if isinstance(summary, dict):
        price = _float_or_none(summary.get("median_price"))
        source = "pool_median_price" if price is not None else source
        if price is None:
            price = _float_or_none(summary.get("avg_price"))
            source = "pool_avg_price" if price is not None else source
    if price is None and estimated_avg_item_price is not None:
        price = float(estimated_avg_item_price)
        source = "day_weighted_avg_price"

    refresh_cost = (
        _optional_nonnegative_int(summary.get("base_refresh_cost"))
        if isinstance(summary, dict)
        else None
    )
    if price is None:
        return {
            "status": "unknown",
            "gold_known": True,
            "price_known": False,
            "current_gold": current_gold,
            "supports_entry": current_gold > 0,
            "supports_refresh_then_buy": None,
            "estimated_purchase_price": None,
            "estimated_purchase_price_source": source,
            "base_refresh_cost": refresh_cost,
            "reason": "price_unknown",
        }

    supports_entry = current_gold >= max(1.0, price * 0.8)
    supports_refresh = (
        current_gold >= refresh_cost + max(1.0, price * 0.8)
        if refresh_cost is not None
        else None
    )
    if supports_refresh:
        status = "refresh_supported"
    elif supports_entry:
        status = "buy_supported"
    else:
        status = "insufficient"

    return {
        "status": status,
        "gold_known": True,
        "price_known": True,
        "current_gold": current_gold,
        "supports_entry": supports_entry,
        "supports_refresh_then_buy": supports_refresh,
        "estimated_purchase_price": round(price, 2),
        "estimated_purchase_price_source": source,
        "base_refresh_cost": refresh_cost,
    }


def build_shop_entry_analysis(
    *,
    event_name: str,
    event_data: dict[str, Any],
    cards: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
    current_day: int,
    current_hero: str | None,
    owned_cards: dict[str, str],
    all_builds: dict[str, Any] | None,
    rarity_rules: dict[str, Any],
    data_context: dict[str, Any] | None,
    shop_pool_summary: dict[str, Any] | None,
    merchant_pool: list[dict[str, Any]],
    estimated_avg_item_price: float | None,
    current_gold: int | None,
) -> dict[str, Any]:
    if not isinstance(shop_pool_summary, dict):
        return {
            "phase": "shop_entry",
            "status": "unknown",
            "reasons": ["missing_shop_pool_summary"],
            "debug": {"day": current_day, "merchant": event_name},
        }

    target_profile = _shop_target_profile(
        merchant_pool,
        build_name=build_name,
        build_data=build_data,
        current_day=current_day,
        current_hero=current_hero,
        owned_cards=owned_cards,
        all_builds=all_builds,
    )
    available_on_day = _merchant_available_on_day(shop_pool_summary, current_day)

    day_merchant_names: list[str] = []
    baseline_densities: list[float] = []
    baseline_debug: list[dict[str, Any]] = []
    events = data_context.get("events", {}) if isinstance(data_context, dict) else {}
    if isinstance(events, dict):
        for baseline_name, baseline_event in events.items():
            if not isinstance(baseline_event, dict) or not _shop_is_baseline_candidate(baseline_event):
                continue
            try:
                summary = get_cached_shop_pool_summary(
                    data=data_context,
                    event_name=baseline_name,
                    event_data=baseline_event,
                    cards=cards,
                    current_day=current_day,
                    current_hero=current_hero,
                    rarity_rules=rarity_rules,
                    resolver=infer_possible_cards_for_event,
                )
            except Exception:
                continue
            if _merchant_available_on_day(summary, current_day) is not True:
                continue
            day_merchant_names.append(baseline_name)
            baseline_pool = hydrate_cached_shop_cards(summary, cards)
            profile = _shop_target_profile(
                baseline_pool,
                build_name=build_name,
                build_data=build_data,
                current_day=current_day,
                current_hero=current_hero,
                owned_cards=owned_cards,
                all_builds=all_builds,
            )
            baseline_densities.append(float(profile["weighted_density"]))
            baseline_debug.append(
                {
                    "merchant": baseline_name,
                    "weighted_density": round(float(profile["weighted_density"]), 4),
                    "pool_count": profile["pool_count"],
                }
            )

    density_band, density_percentile = _density_band_from_baseline(
        float(target_profile["weighted_density"]),
        baseline_densities,
    )
    current_weighted_density = float(target_profile["weighted_density"])
    ranked_baseline = sorted(
        [
            {
                **item,
                "weighted_density": float(item.get("weighted_density") or 0.0),
            }
            for item in baseline_debug
        ],
        key=lambda item: (-float(item.get("weighted_density") or 0.0), str(item.get("merchant") or "")),
    )
    density_rank = None
    if ranked_baseline:
        density_rank = 1 + sum(
            1
            for item in ranked_baseline
            if float(item.get("weighted_density") or 0.0) > current_weighted_density
        )
    gold_support = _entry_gold_support(
        current_gold=current_gold,
        summary=shop_pool_summary,
        estimated_avg_item_price=estimated_avg_item_price,
        free_purchase=event_data.get("event_category") == "skill_shops",
    )

    counts = target_profile["counts"]
    has_current_targets = (
        counts["missing_current_core"]
        + counts["current_core"]
        + counts["current_transition"]
        + counts["current_optional"]
    ) > 0
    has_high_priority = counts["missing_current_core"] > 0 or counts["current_core"] > 0
    theoretical_only = bool(target_profile["theoretical_only"])
    supports_entry = gold_support.get("supports_entry")

    if available_on_day is False:
        status = "unknown"
    elif supports_entry is False and (has_current_targets or counts["future_core"]):
        status = "not_actionable"
    elif not has_current_targets and counts["future_core"] <= 0:
        status = "weak_candidate"
    elif theoretical_only:
        status = "situational" if gold_support.get("status") != "insufficient" else "not_actionable"
    elif density_band == "high" and has_high_priority and supports_entry is not False:
        status = "strong_candidate"
    elif density_band in {"high", "medium"} and has_current_targets and supports_entry is not False:
        status = "candidate"
    elif has_current_targets:
        status = "situational"
    else:
        status = "weak_candidate"

    reasons: list[str] = []
    if available_on_day is False:
        reasons.append("merchant_not_available_on_current_day")
    if has_high_priority:
        reasons.append("pool_contains_current_core_targets")
    elif counts["current_transition"] or counts["current_optional"]:
        reasons.append("pool_contains_current_tempo_or_optional_targets")
    elif counts["future_core"]:
        reasons.append("only_future_core_targets_or_stash_value")
    else:
        reasons.append("no_current_build_targets_in_pool")
    if density_band != "unknown":
        reasons.append(f"target_density_{density_band}_vs_current_day_merchants")
    if gold_support.get("status") == "insufficient":
        reasons.append("gold_does_not_support_estimated_purchase")
    if theoretical_only:
        reasons.append("theoretical_pool_hit_without_current_actionable_target")

    return {
        "phase": "shop_entry",
        "status": status if status in SHOP_ENTRY_STATUSES else "unknown",
        "merchant": event_name,
        "day": current_day,
        "hero": current_hero,
        "shop_tier": shop_pool_summary.get("shop_tier"),
        "available_on_day": available_on_day,
        "day_available_merchant_count": len(day_merchant_names),
        "day_available_merchants": day_merchant_names,
        "pool_count": target_profile["pool_count"],
        "target_counts": counts,
        "target_samples": target_profile["sample_hits"],
        "weighted_density": round(float(target_profile["weighted_density"]), 4),
        "actionable_density": round(float(target_profile["actionable_density"]), 4),
        "target_density_band": density_band,
        "target_density_percentile": (
            round(float(density_percentile), 4)
            if density_percentile is not None
            else None
        ),
        "target_density_rank": density_rank,
        "top_day_merchants_by_density": ranked_baseline[:5],
        "gold_support": gold_support,
        "theoretical_only": theoretical_only,
        "worth_spending_choice": status in {"strong_candidate", "candidate"},
        "reasons": reasons,
        "debug": {
            "day": current_day,
            "merchant_available_on_day": available_on_day,
            "day_available_merchant_count": len(day_merchant_names),
            "merchant_pool_size": target_profile["pool_count"],
            "current_core_hits": counts["missing_current_core"] + counts["current_core"],
            "current_tempo_hits": counts["current_transition"],
            "current_optional_hits": counts["current_optional"],
            "future_core_hits": counts["future_core"],
            "expired_core_hits": counts["expired_core"],
            "density_band": density_band,
            "gold_support_status": gold_support.get("status"),
            "current_gold": current_gold,
            "theoretical_only": theoretical_only,
            "baseline_merchants": baseline_debug,
        },
    }


def build_shop_inside_analysis(
    *,
    analyzed_cards: list[dict[str, Any]],
    visible_items: Any,
    current_shop: dict[str, Any] | None,
    current_gold: int | None,
    shop_entry_analysis: dict[str, Any] | None,
    estimated_avg_item_price: float | None,
    refresh_pool_ratio: float,
    free_skill_prices: bool = False,
) -> dict[str, Any]:
    refresh_available = current_shop.get("refresh_available") if current_shop else None
    refresh_cost = _optional_nonnegative_int(current_shop.get("refresh_cost")) if current_shop else None
    refreshes_remaining = (
        _optional_nonnegative_int(current_shop.get("refreshes_remaining"))
        if current_shop
        else None
    )
    visible_known_price = max_known_visible_item_price(
        visible_items,
        ignore_skill_prices=free_skill_prices,
    )
    purchase_budget_price = (
        float(visible_known_price)
        if visible_known_price is not None
        else estimated_avg_item_price
    )
    purchase_budget_price_source = (
        "runtime_visible_price"
        if visible_known_price is not None
        else "estimated_avg_shop_item_price"
        if estimated_avg_item_price is not None
        else "unknown"
    )
    gold_sufficient_for_refresh = (
        current_gold - refresh_cost >= purchase_budget_price
        if current_gold is not None
        and refresh_cost is not None
        and purchase_budget_price is not None
        else current_gold > refresh_cost
        if current_gold is not None and refresh_cost is not None
        else None
    )
    visible_by_name: dict[str, dict[str, Any]] = {}
    if isinstance(visible_items, list):
        for item in visible_items:
            if isinstance(item, dict) and item.get("name"):
                visible_by_name[str(item["name"])] = item

    worth_buying: list[dict[str, Any]] = []
    unaffordable: list[dict[str, Any]] = []
    visible_targets = [
        card
        for card in analyzed_cards
        if card.get("role") in {"core", "transition", "optional"}
    ]
    for card in visible_targets:
        visible = visible_by_name.get(str(card.get("name")), {})
        price = (
            None
            if free_skill_prices and _visible_entry_is_skill(visible)
            else _optional_nonnegative_int(visible.get("price"))
        )
        affordable = (
            current_gold >= price
            if current_gold is not None and price is not None
            else None
        )
        item = {
            "name": card.get("name"),
            "role": card.get("role"),
            "tier": card.get("tier"),
            "price": price,
            "affordable": affordable,
        }
        if affordable is False:
            unaffordable.append(item)
        else:
            worth_buying.append(item)

    pool_quality_high = (
        isinstance(shop_entry_analysis, dict)
        and shop_entry_analysis.get("target_density_band") == "high"
    )
    if visible_targets:
        action, rationale = "buy", "visible_target_before_refresh"
    elif refresh_available is False:
        action, rationale = "skip", "refresh_not_available"
    elif refresh_cost is None:
        action, rationale = "skip", "refresh_cost_unknown"
    elif current_gold is None:
        action, rationale = "skip", "current_gold_unknown"
    elif current_gold <= refresh_cost:
        action, rationale = "skip", "not_enough_gold_after_refresh"
    elif gold_sufficient_for_refresh is False:
        action, rationale = "skip", "refresh_leaves_insufficient_purchase_budget"
    elif pool_quality_high or refresh_pool_ratio > 0:
        action, rationale = "refresh", "no_visible_target_but_shop_pool_is_actionable"
    else:
        action, rationale = "skip", "no_visible_target_and_pool_quality_low"

    return {
        "phase": "shop_inside",
        "action": action,
        "reason": rationale,
        "visible_offer_count": len(visible_items) if isinstance(visible_items, list) else 0,
        "worth_buying": worth_buying,
        "unaffordable_targets": unaffordable,
        "visible_target_count": len(visible_targets),
        "refresh_available": refresh_available,
        "refresh_cost": refresh_cost,
        "refreshes_remaining": refreshes_remaining,
        "refresh_scope": "current_shop_only",
        "refresh_carries_over": False,
        "estimated_avg_item_price": estimated_avg_item_price,
        "purchase_budget_price": purchase_budget_price,
        "purchase_budget_price_source": purchase_budget_price_source,
        "gold_sufficient_for_refresh": gold_sufficient_for_refresh,
        "refresh_pool_valuable_ratio": refresh_pool_ratio,
        "pool_quality_band": (
            shop_entry_analysis.get("target_density_band")
            if isinstance(shop_entry_analysis, dict)
            else None
        ),
        "debug": {
            "uses_visible_items": True,
            "current_gold": current_gold,
            "has_visible_target": bool(visible_targets),
            "refreshes_remaining": refreshes_remaining,
            "refresh_pool_valuable_ratio": round(float(refresh_pool_ratio), 4),
        },
    }


def get_event_draw_count(event_data: dict[str, Any]) -> int:
    """
    杩斿洖杩欎釜浜嬩欢涓€娆¤兘鐪嬪埌/鑾峰緱澶氬皯涓墿鍝併€?

    瑙勫垯锛?
    - shops / skill_shops锛氶粯璁ょ湅 6 寮?
    - item_rewards / 甯?card_reward 鐨?resource_events锛氶粯璁よ幏寰?1 涓?
    - card_reward.count 瀛樺湪鏃讹紝鐢?count
    - count 缂哄け銆佷负绌恒€佸啓閿欐椂锛屽畨鍏ㄥ洖閫€涓?1
    """
    event_category = event_data.get("event_category")

    if event_category in {"shops", "skill_shops"}:
        return VISIBLE_OFFER_COUNT

    card_reward = event_data.get("card_reward")
    if isinstance(card_reward, dict):
        raw_count = card_reward.get(
            "offer_count",
            card_reward.get("count", event_data.get("count", 1)),
        )
    else:
        raw_count = event_data.get("count", 1)

    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        count = 1

    return max(count, 1)


def get_event_selection_count(event_data: dict[str, Any]) -> int:
    card_reward = event_data.get("card_reward")
    if not isinstance(card_reward, dict):
        return get_event_draw_count(event_data)

    raw_count = card_reward.get(
        "choose_count",
        card_reward.get("count", event_data.get("count", 1)),
    )
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        count = 1

    return max(count, 1)


def event_has_skill_reward(event_data: dict[str, Any]) -> bool:
    """Return whether an event includes a skill reward."""
    if not isinstance(event_data, dict):
        return False

    event_category = normalize_text(event_data.get("event_category"))
    event_type = normalize_text(event_data.get("event_type"))
    effect = normalize_text(event_data.get("effect"))

    if event_category == "skill_shops":
        return True

    if event_type in {"skill_shop", "skill_event", "skill_reward"}:
        return True

    if effect in {"gain_skill", "choose_skill", "skill_reward"}:
        return True

    qualitative_rewards = event_data.get("qualitative_rewards", [])
    if isinstance(qualitative_rewards, list):
        for reward in qualitative_rewards:
            if "skill" in normalize_text(str(reward)):
                return True

    text_fields = [
        event_data.get("name", ""),
        event_data.get("notes", ""),
        event_data.get("description", ""),
    ]
    text = " ".join(str(value).lower() for value in text_fields if value)

    skill_keywords = [
        "skill",
        "skills",
        "choose 1 of 2 skills",
        "choose 1 of 3 skills",
        "choose a skill",
        "gain a skill",
    ]

    return any(keyword in text for keyword in skill_keywords)


def analyze_event(
    event_name: str,
    event_data: dict[str, Any],
    cards: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
    current_day: int,
    rarity_rules: dict[str, Any],
    current_hero: str | None = None,
    owned_cards: dict[str, str] | None = None,
    owned_card_enchantments: dict[str, list[str]] | None = None,
    include_followups: bool = True,
    all_builds: dict[str, Any] | None = None,
    current_shop: dict[str, Any] | None = None,
    current_gold: int | None = None,
    data_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    owned_cards = owned_cards or {}
    owned_card_enchantments = owned_card_enchantments or {}
    is_shop = event_data.get("event_category") in {"shops", "skill_shops"}
    shop_pool_summary: dict[str, Any] | None = None
    if is_shop:
        try:
            shop_pool_summary = get_cached_shop_pool_summary(
                data=data_context,
                event_name=event_name,
                event_data=event_data,
                cards=cards,
                current_day=current_day,
                current_hero=current_hero,
                rarity_rules=rarity_rules,
                resolver=infer_possible_cards_for_event,
            )
        except Exception as exc:
            shop_pool_summary = {
                "cache_status": "fallback",
                "reason": str(exc),
                "merchant_name": event_name,
            }

    if is_shop and shop_pool_summary and shop_pool_summary.get("cache_status") == "hit":
        possible_cards = hydrate_cached_shop_cards(shop_pool_summary, cards)
        resolved_rarity_filter = shop_pool_summary.get("resolved_rarity_filter")
    else:
        possible_cards, resolved_rarity_filter = infer_possible_cards_for_event(
            event_data,
            cards,
            current_day,
            rarity_rules,
            current_hero,
        )
    refresh_pool_valuable_count = sum(
        get_card_role_for_build(card["name"], card["raw"], build_name, build_data)
        in {"core", "transition", "optional"}
        for card in possible_cards
    )
    refresh_pool_ratio = (
        refresh_pool_valuable_count / len(possible_cards)
        if possible_cards
        else 0.0
    )
    merchant_pool = list(possible_cards)
    estimated_avg_item_price = (
        estimated_avg_shop_item_price(
            current_day,
            merchant_pool,
            cards,
            rarity_rules,
        )
        if is_shop and event_data.get("event_category") != "skill_shops"
        else None
    )
    shop_entry_analysis = (
        build_shop_entry_analysis(
            event_name=event_name,
            event_data=event_data,
            cards=cards,
            build_name=build_name,
            build_data=build_data,
            current_day=current_day,
            current_hero=current_hero,
            owned_cards=owned_cards,
            all_builds=all_builds,
            rarity_rules=rarity_rules,
            data_context=data_context,
            shop_pool_summary=shop_pool_summary,
            merchant_pool=merchant_pool,
            estimated_avg_item_price=estimated_avg_item_price,
            current_gold=current_gold,
        )
        if is_shop
        else None
    )
    visible_items = current_shop.get("visible_items") if is_shop and current_shop else None
    using_visible_items = (
        isinstance(visible_items, list)
        and any(isinstance(item, dict) and item.get("name") for item in visible_items)
    )
    if using_visible_items:
        visible_names = {
            str(item.get("name") if isinstance(item, dict) else item)
            for item in visible_items
        }
        possible_cards = [card for card in possible_cards if card.get("name") in visible_names]

    analyzed_cards: list[dict[str, Any]] = []
    role_counts = {role: 0 for role in ROLE_LABELS}
    high_tier_count = 0
    upgrade_hits: list[str] = []
    owned_target_hits: list[dict[str, Any]] = []

    for card in possible_cards:
        card_name = card["name"]
        card_data = card["raw"]
        tier = card.get("tier", "Unknown")
        role = get_card_role_for_build(card_name, card_data, build_name, build_data)
        alt_core_build_hits = (
            []
            if role == "core"
            else get_alt_core_build_hits(
                card_name,
                current_build_name=build_name,
                current_hero=current_hero,
                current_day=current_day,
                all_builds=all_builds,
                owned_cards=owned_cards,
                cards=cards,
            )
        )
        if (
            role == "unrelated"
            and event_data.get("event_category") == "item_rewards"
            and event_data.get("card_reward", {}).get("exact_names")
            and tags_overlap_build_wants(card.get("tags", []), build_data)
        ):
            role = "optional"
        role_counts[role] = role_counts.get(role, 0) + 1

        if tier in {"S", "A"}:
            high_tier_count += 1

        owned_rarity = owned_cards.get(card_name)
        can_upgrade = bool(
            owned_rarity
            and normalize_text(owned_rarity) != normalize_text(card_data.get("max_rarity"))
        )

        if can_upgrade:
            upgrade_hits.append(card_name)

        analyzed_cards.append(
            {
                "name": card_name,
                "tier": tier,
                "role": role,
                "role_label": ROLE_LABELS.get(role, role.title()),
                "can_upgrade": can_upgrade,
                "owned_rarity": owned_rarity,
                "tags": card.get("tags", []),
                "sell_gold": expected_card_sell_gold(card_data, resolved_rarity_filter),
                "alt_core_build_hits": alt_core_build_hits,
            }
        )


    if event_data.get("event_category") in {"item_events", "enchant_events"}:
        owned_target_hits = analyze_owned_target_hits(
            event_data=event_data,
            cards=cards,
            build_name=build_name,
            build_data=build_data,
            owned_cards=owned_cards,
            owned_card_enchantments=owned_card_enchantments,
        )

    resource_rewards = event_data.get("resource_rewards", {})
    has_resource_reward = any(value > 0 for value in resource_rewards.values())

    total_pool_count = len(analyzed_cards)
    valuable_count = (
        role_counts.get("core", 0)
        + role_counts.get("transition", 0)
        + role_counts.get("optional", 0)
    )
    core_count = role_counts.get("core", 0)
    alt_core_cards = [
        card for card in analyzed_cards if card.get("alt_core_build_hits")
    ]
    alt_core_card_count = len(alt_core_cards)

    valuable_ratio = valuable_count / total_pool_count if total_pool_count else 0.0
    core_ratio = core_count / total_pool_count if total_pool_count else 0.0
    high_tier_ratio = high_tier_count / total_pool_count if total_pool_count else 0.0

    draw_count = len(visible_items) if using_visible_items else get_event_draw_count(event_data)
    selection_count = get_event_selection_count(event_data)

    expected_sell_gold = expected_unrelated_sell_gold(
        analyzed_cards,
        event_data,
        selection_count,
    )

    pool_stats = {
        "draw_count": draw_count,
        "selection_count": selection_count,
        "total_pool_count": total_pool_count,
        "valuable_count": valuable_count,
        "valuable_ratio": valuable_ratio,
        "core_ratio": core_ratio,
        "high_tier_ratio": high_tier_ratio,
        "expected_valuable_in_shop": draw_count * valuable_ratio,
        "expected_core_in_shop": draw_count * core_ratio,
        "expected_high_tier_in_shop": draw_count * high_tier_ratio,
        "prob_valuable_in_shop": probability_at_least_one(valuable_ratio, draw_count),
        "prob_core_in_shop": probability_at_least_one(core_ratio, draw_count),
        "prob_high_tier_in_shop": probability_at_least_one(high_tier_ratio, draw_count),
        "expected_sell_gold": expected_sell_gold,
    }
    if shop_pool_summary:
        pool_stats["shop_pool_cache_status"] = shop_pool_summary.get("cache_status")

    recommendation, reasons = decide_recommendation(
        analyzed_cards,
        role_counts,
        high_tier_count,
        upgrade_hits,
        has_resource_reward,
        resource_rewards,
        pool_stats,
        owned_target_hits,
        event_data,
    )
    shop_decision = None
    shop_inside_analysis = None
    if is_shop:
        if using_visible_items:
            shop_inside_analysis = build_shop_inside_analysis(
                analyzed_cards=analyzed_cards,
                visible_items=visible_items,
                current_shop=current_shop,
                current_gold=current_gold,
                shop_entry_analysis=shop_entry_analysis,
                estimated_avg_item_price=estimated_avg_item_price,
                refresh_pool_ratio=refresh_pool_ratio,
                free_skill_prices=event_data.get("event_category") == "skill_shops",
            )
            shop_decision = shop_inside_analysis
            reasons.insert(0, str(shop_inside_analysis.get("reason") or "shop_inside_analysis"))
        elif isinstance(shop_entry_analysis, dict):
            entry_status = shop_entry_analysis.get("status")
            if entry_status == "strong_candidate":
                recommendation = "High Value"
            elif entry_status in {"candidate", "situational"}:
                recommendation = "Medium Value"
            elif entry_status in {"weak_candidate", "not_actionable"}:
                recommendation = "Low Value"
            shop_decision = {
                "action": "enter" if entry_status in {"strong_candidate", "candidate"} else "defer",
                "reason": ",".join(shop_entry_analysis.get("reasons", [])[:4]),
                "using_visible_items": False,
                "entry_status": entry_status,
                "target_density_band": shop_entry_analysis.get("target_density_band"),
                "gold_support": shop_entry_analysis.get("gold_support"),
                "refresh_pool_valuable_ratio": refresh_pool_ratio,
            }
            reasons.insert(0, f"shop_entry_status={entry_status}")
    for card in alt_core_cards:
        build_labels = ", ".join(
            str(hit.get("display_name") or hit.get("build_name") or "")
            for hit in card["alt_core_build_hits"]
            if hit
        )
        reasons.append(f"{card['name']}: core target for alternate build(s): {build_labels}.")
    if alt_core_card_count >= 2:
        reasons.append(
            f"Pool contains {alt_core_card_count} alternate-build core cards."
        )
        if recommendation == "Low Value":
            recommendation = "Medium Value"
    followup_results: list[dict[str, Any]] = []
    followup_value_summary: dict[str, Any] | None = None
    if include_followups:
        for option in event_data.get("followup_options", []):
            followup_results.append(
                analyze_event(
                    event_name=option.get("name", "Follow-up option"),
                    event_data=option,
                    cards=cards,
                    build_name=build_name,
                    build_data=build_data,
                    current_day=current_day,
                    rarity_rules=rarity_rules,
                    current_hero=current_hero,
                    owned_cards=owned_cards,
                    owned_card_enchantments=owned_card_enchantments,
                    include_followups=False,
                    all_builds=all_builds,
                    data_context=data_context,
                )
            )

        recommendation, reasons, followup_value_summary = apply_followup_value(
            recommendation,
            reasons,
            followup_results,
        )

        if followup_value_summary:
            reasons = [reason for reason in reasons if reason]

    if followup_value_summary:
        followup_pool_stats = followup_value_summary.get("pool_stats", {})
        if (
            not pool_stats.get("total_pool_count")
            and followup_pool_stats.get("total_pool_count")
        ):
            pool_stats = dict(followup_pool_stats)

        followup_resources = followup_value_summary.get("resource_rewards", {})
        if not has_resource_reward and any(
            isinstance(value, (int, float)) and value > 0
            for value in followup_resources.values()
        ):
            resource_rewards = dict(followup_resources)

    return {
        "event_name": event_name,
        "event_type": event_data.get("event_category", "unknown"),
        "notes": event_data.get("notes", ""),
        "current_day": current_day,
        "resolved_rarity_filter": resolved_rarity_filter,
        "possible_cards": analyzed_cards,
        "alt_core_build_hits": [
            {
                "card_name": card["name"],
                "builds": card["alt_core_build_hits"],
            }
            for card in alt_core_cards
        ],
        "alt_core_card_count": alt_core_card_count,
        "role_counts": role_counts,
        "high_tier_count": high_tier_count,
        "upgrade_hits": upgrade_hits,
        "owned_target_hits": owned_target_hits,
        "resource_rewards": resource_rewards,
        "followup_options": summarize_followup_results(followup_results),
        "best_followup": followup_value_summary.get("best_followup") if followup_value_summary else None,
        "followup_recommendation_level": followup_value_summary.get("followup_recommendation_level") if followup_value_summary else None,
        "followup_expected_value": followup_value_summary.get("followup_expected_value") if followup_value_summary else 0.0,
        "followup_hit_chance": followup_value_summary.get("followup_hit_chance") if followup_value_summary else 0.0,
        "followup_value_summary": followup_value_summary,
        "pool_stats": pool_stats,
        "shop_pool_summary": shop_pool_summary,
        "shop_entry_analysis": shop_entry_analysis,
        "shop_inside_analysis": shop_inside_analysis,
        "recommendation": recommendation,
        "reasons": reasons,
        "shop_decision": shop_decision,
    }


def _optional_nonnegative_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def select_best_followup_result(followup_results: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not followup_results:
        return None

    ranked = sorted(
        followup_results,
        key=lambda result: (
            RECOMMENDATION_RANK.get(result.get("recommendation"), 99),
            -float(result.get("pool_stats", {}).get("expected_valuable_in_shop", 0.0)),
            -float(result.get("pool_stats", {}).get("expected_sell_gold", 0.0)),
            result.get("event_name", ""),
        ),
    )
    return ranked[0]


def summarize_best_followup_value(best: dict[str, Any] | None) -> dict[str, Any] | None:
    if not best:
        return None

    pool_stats = best.get("pool_stats", {})
    if not isinstance(pool_stats, dict):
        pool_stats = {}

    resource_rewards = best.get("resource_rewards", {})
    if not isinstance(resource_rewards, dict):
        resource_rewards = {}

    return {
        "best_followup": best.get("event_name"),
        "followup_recommendation_level": best.get("recommendation"),
        "followup_expected_value": float(pool_stats.get("expected_valuable_in_shop", 0.0)),
        "followup_hit_chance": float(pool_stats.get("prob_valuable_in_shop", 0.0)),
        "pool_stats": dict(pool_stats),
        "resource_rewards": dict(resource_rewards),
    }


def apply_followup_value(
    recommendation: str,
    reasons: list[str],
    followup_results: list[dict[str, Any]],
) -> tuple[str, list[str], dict[str, Any] | None]:
    if not followup_results:
        return recommendation, reasons, None

    best = select_best_followup_result(followup_results)
    if not best:
        return recommendation, reasons, None

    best_recommendation = best.get("recommendation", "Low Value")
    followup_summary = summarize_best_followup_value(best)

    pool_stats = best.get("pool_stats", {})
    if not isinstance(pool_stats, dict):
        pool_stats = {}

    resource_rewards = best.get("resource_rewards", {})
    if not isinstance(resource_rewards, dict):
        resource_rewards = {}

    if any(value > 0 for value in resource_rewards.values() if isinstance(value, (int, float))):
        reasons.append(
            f"Best follow-up can provide {format_resource_rewards(resource_rewards)}."
        )

    total_pool_count = int(pool_stats.get("total_pool_count", 0))
    if total_pool_count > 0:
        reasons.append(
            f"Best follow-up {best.get('event_name', 'option')} has "
            f"{float(pool_stats.get('prob_valuable_in_shop', 0.0)):.0%} useful hit chance, "
            f"{float(pool_stats.get('prob_core_in_shop', 0.0)):.0%} core hit chance, "
            f"expected useful cards {float(pool_stats.get('expected_valuable_in_shop', 0.0)):.1f}."
        )
    elif not any(value > 0 for value in resource_rewards.values() if isinstance(value, (int, float))):
        reasons.append("Follow-up options detected, but current estimated value is limited.")

    current_rank = RECOMMENDATION_RANK.get(recommendation, 99)
    best_rank = RECOMMENDATION_RANK.get(best_recommendation, 99)
    if best_rank < current_rank:
        recommendation = best_recommendation

    return recommendation, reasons, followup_summary


def summarize_followup_results(followup_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for result in followup_results:
        pool_stats = result.get("pool_stats", {})
        summaries.append(
            {
                "name": result.get("event_name"),
                "recommendation": result.get("recommendation"),
                "event_type": result.get("event_type"),
                "notes": result.get("notes", ""),
                "resource_rewards": result.get("resource_rewards", {}),
                "valuable_count": int(pool_stats.get("valuable_count", 0)),
                "total_pool_count": int(pool_stats.get("total_pool_count", 0)),
                "expected_sell_gold": float(pool_stats.get("expected_sell_gold", 0.0)),
                "priority_cards": [
                    {
                        "name": card.get("name"),
                        "tier": card.get("tier"),
                        "role": card.get("role"),
                    }
                    for card in result.get("possible_cards", [])
                    if card.get("role") in {"core", "transition", "optional"}
                ][:5],
            }
        )
    return summaries


def analyze_owned_target_hits(
    event_data: dict[str, Any],
    cards: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
    owned_cards: dict[str, str],
    owned_card_enchantments: dict[str, list[str]],
) -> list[dict[str, Any]]:
    target_tags = event_data.get("target_tags", [])
    if not target_tags and event_data.get("event_category") == "enchant_events":
        target_tags = event_data.get("enchantment_tags", [])

    target_tags = normalize_text_list(target_tags)
    effect = event_data.get("effect")
    matches_any_owned_item = effect in {
        "upgrade_items",
        "transform_items",
        "enhance_offensive_items",
    }
    if effect == "enhance_offensive_items" and not target_tags:
        target_tags = ["weapon", "damage"]

    if not target_tags and not matches_any_owned_item:
        return []

    hits: list[dict[str, Any]] = []
    for card_name, rarity in owned_cards.items():
        card_data = cards.get(card_name)
        if not card_data:
            continue

        enchantments = owned_card_enchantments.get(card_name, [])
        card_tags = effective_card_tags(card_data, enchantments)
        if target_tags and not tags_match(card_tags, target_tags, event_data.get("match_mode", "any")):
            continue

        role = get_card_role_for_build(card_name, card_data, build_name, build_data)
        can_upgrade = normalize_text(rarity) != normalize_text(card_data.get("max_rarity"))
        hits.append(
            {
                "name": card_name,
                "rarity": rarity,
                "tier": card_data.get("tier", "Unknown"),
                "role": role,
                "role_label": ROLE_LABELS.get(role, role.title()),
                "can_upgrade": can_upgrade,
                "tags": card_tags,
                "enchantments": enchantments,
            }
        )

    return hits


def decide_recommendation(
    analyzed_cards: list[dict[str, Any]],
    role_counts: dict[str, int],
    high_tier_count: int,
    upgrade_hits: list[str],
    has_resource_reward: bool,
    resource_rewards: dict[str, int],
    pool_stats: dict[str, float],
    owned_target_hits: list[dict[str, Any]],
    event_data: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    total_pool_count = int(pool_stats.get("total_pool_count", 0))
    valuable_count = int(pool_stats.get("valuable_count", 0))
    valuable_ratio = pool_stats.get("valuable_ratio", 0.0)
    expected_valuable = pool_stats.get("expected_valuable_in_shop", 0.0)
    expected_core = pool_stats.get("expected_core_in_shop", 0.0)
    expected_sell_gold = pool_stats.get("expected_sell_gold", 0.0)
    prob_valuable = pool_stats.get("prob_valuable_in_shop", 0.0)
    prob_core = pool_stats.get("prob_core_in_shop", 0.0)
    draw_count = int(pool_stats.get("draw_count", VISIBLE_OFFER_COUNT))
    selection_count = int(pool_stats.get("selection_count", draw_count))

    has_skill_reward = event_has_skill_reward(event_data)

    core_count = role_counts.get("core", 0)
    transition_count = role_counts.get("transition", 0)
    owned_valuable_hits = [
        card
        for card in owned_target_hits
        if card.get("role") in {"core", "transition", "optional"}
    ]
    upgradeable_valuable_hits = [
        card
        for card in owned_valuable_hits
        if card.get("can_upgrade")
    ]

    high_tier_core_cards = [
        card
        for card in analyzed_cards
        if card["role"] == "core" and card["tier"] in {"S", "A"}
    ]

    if high_tier_core_cards:
        names = ", ".join(card["name"] for card in high_tier_core_cards)
        reasons.append(f"Can hit high-tier core cards: {names}.")

    if core_count >= 2:
        reasons.append(f"Pool contains {core_count} current-build core cards.")

    if upgrade_hits:
        reasons.append(f"Can upgrade owned cards: {', '.join(upgrade_hits)}.")

    if owned_target_hits:
        names = ", ".join(card["name"] for card in owned_target_hits[:5])
        reasons.append(f"Affects owned matching items: {names}.")

    if transition_count > 0:
        reasons.append(f"Pool contains {transition_count} transition cards.")

    if high_tier_count > 0:
        reasons.append(f"Pool contains {high_tier_count} S/A tier cards.")

    if has_resource_reward:
        reasons.append(f"Provides resources: {format_resource_rewards(resource_rewards)}.")

    if has_skill_reward:
        reasons.append("Includes a skill reward.")

    if expected_sell_gold > 0:
        reasons.append(
            f"Unrelated items still have estimated sell value around {expected_sell_gold:.1f} gold."
        )

    if event_data.get("event_category") == "enchant_events":
        enchantment_tags = event_data.get("enchantment_tags", [])
        if enchantment_tags:
            reasons.append(f"Can provide enchantment direction: {', '.join(enchantment_tags)}.")
        else:
            reasons.append("Can enchant an item, but exact enchantment value is unclear.")

    if total_pool_count > 0:
        reasons.append(
            f"Pool has {total_pool_count} cards; {valuable_count} are build-relevant "
            f"({valuable_ratio:.0%})."
        )
        if selection_count < draw_count:
            reasons.append(
                f"Shows {draw_count} cards and lets you choose {selection_count}; "
                f"expected relevant cards {expected_valuable:.1f}, "
                f"chance to see at least one relevant card {prob_valuable:.0%}."
            )
        elif event_data.get("event_category") in {"shops", "skill_shops"}:
            reasons.append(
                f"Shop view expects {expected_valuable:.1f} relevant cards; "
                f"chance to see at least one relevant card {prob_valuable:.0%}."
            )
        else:
            reasons.append(
                f"Reward gives {draw_count} items; expected relevant cards {expected_valuable:.1f}, "
                f"useful hit chance {prob_valuable:.0%}."
            )
        reasons.append(f"Chance to hit at least one core card is {prob_core:.0%}.")

    if (
        not analyzed_cards
        and not has_resource_reward
        and not has_skill_reward
        and not owned_target_hits
        and event_data.get("event_category") != "enchant_events"
    ):
        reasons.append("No clear card or resource value identified.")

    if event_data.get("effect") == "upgrade_items" and upgradeable_valuable_hits:
        return "High Value", reasons
    if event_data.get("effect") == "upgrade_items" and owned_target_hits:
        return "Medium Value", reasons
    if owned_valuable_hits and event_data.get("effect") != "transform_items":
        return "High Value", reasons
    if owned_target_hits:
        return "Medium Value", reasons
    if has_skill_reward:
        return "Medium Value", reasons

    if has_skill_reward:
        return "Medium Value", reasons
    if event_data.get("event_category") in {"skill_shops", "enchant_events"}:
        return "Medium Value", reasons
    if high_tier_core_cards and expected_core >= 0.4:
        return "High Value", reasons
    if core_count >= 2 and expected_core >= 0.3:
        return "High Value", reasons
    if upgrade_hits and expected_valuable >= 0.3:
        return "High Value", reasons
    if expected_valuable >= 0.6:
        return "High Value", reasons
    if expected_valuable >= 0.25:
        return "Medium Value", reasons
    if has_resource_reward:
        return "Medium Value", reasons
    if expected_sell_gold >= 1:
        return "Medium Value", reasons

    if analyzed_cards:
        reasons.append("Some usable cards exist, but hit quality is low.")

    return "Low Value", reasons


def format_resource_rewards(resource_rewards: dict[str, int]) -> str:
    labels = {
        "exp": "exp",
        "gold": "gold",
        "health": "health",
        "income": "income",
        "regen": "regen",
        "speed": "speed",
        "toughness": "toughness",
    }
    parts = [
        f"{labels.get(name, name)} +{value}"
        for name, value in sorted(resource_rewards.items())
        if value > 0
    ]

    return ", ".join(parts) if parts else "none"


def format_rarity_filter(rarity_filter: dict[str, str] | None) -> str:
    if rarity_filter is None:
        return "no rarity filter"
    return f"{rarity_filter['min']} - {rarity_filter['max']}"


def print_event_analysis(result: dict[str, Any]) -> None:
    print("=" * 72)
    print(f"Event: {result['event_name']}")
    print(f"Recommendation: {result['recommendation']}")
    print(f"Day: {result['current_day']}")
    print(f"Resolved rarity range: {format_rarity_filter(result['resolved_rarity_filter'])}")

    if result["notes"]:
        print(f"Notes: {result['notes']}")

    print("\nReasons:")
    for reason in result["reasons"]:
        print(f"- {reason}")

    pool_stats = result.get("pool_stats", {})
    if pool_stats:
        print("\nPool stats:")
        print(f"- Candidate cards: {int(pool_stats['total_pool_count'])}")
        print(
            f"- Build-relevant cards: {int(pool_stats['valuable_count'])} "
            f"({pool_stats['valuable_ratio']:.0%})"
        )
        print(
            f"- Expected build-relevant cards in shop: "
            f"{pool_stats['expected_valuable_in_shop']:.1f}"
        )
        print(f"- Probability of at least one useful card: {pool_stats['prob_valuable_in_shop']:.0%}")
        print(f"- Probability of at least one core card: {pool_stats['prob_core_in_shop']:.0%}")

    print("\nPriority cards:")
    priority_cards = [
        card
        for card in result["possible_cards"]
        if card["role"] in {"core", "transition"}
    ]

    if not priority_cards:
        print("- No core or transition cards in this pool.")
    else:
        for card in priority_cards:
            upgrade_text = ""
            if card["can_upgrade"]:
                upgrade_text = f" | owned {card['owned_rarity']}, upgrade possible"

            print(f"- {card['name']} | {card['tier']} | {card['role_label']}{upgrade_text}")

    owned_target_hits = result.get("owned_target_hits", [])
    if owned_target_hits:
        print("\nAffected owned cards:")
        for card in owned_target_hits:
            enchantment_text = ""
            if card.get("enchantments"):
                enchantment_text = f" | enchantments: {', '.join(card['enchantments'])}"
            upgrade_text = " | upgradeable" if card.get("can_upgrade") else ""
            print(
                f"- {card['name']} | {card['tier']} | {card['role_label']}"
                f" | owned {card['rarity']}{upgrade_text}{enchantment_text}"
            )

    print(f"\nResources: {format_resource_rewards(result['resource_rewards'])}")
