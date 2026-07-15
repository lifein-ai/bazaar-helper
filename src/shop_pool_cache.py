from __future__ import annotations

import json
from statistics import median
from typing import Any, Callable


SHOP_STAGE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "bronze_only",
        "label": "只有青铜",
        "representative_day": 1,
        "rarity_filter": {"min": "bronze", "max": "bronze"},
    },
    {
        "key": "silver_unlocked",
        "label": "出现白银",
        "representative_day": 2,
        "rarity_filter": {"min": "bronze", "max": "silver"},
    },
    {
        "key": "gold_unlocked",
        "label": "出现黄金",
        "representative_day": 5,
        "rarity_filter": {"min": "bronze", "max": "gold"},
    },
    {
        "key": "diamond_unlocked",
        "label": "出现钻石",
        "representative_day": 9,
        "rarity_filter": {"min": "silver", "max": "diamond"},
    },
)

_STAGE_BY_KEY = {stage["key"]: stage for stage in SHOP_STAGE_DEFINITIONS}
_MEMORY_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def shop_stage_for_day(current_day: int) -> dict[str, Any]:
    try:
        day = int(current_day)
    except (TypeError, ValueError):
        day = 1

    if day <= 1:
        return dict(_STAGE_BY_KEY["bronze_only"])
    if day <= 4:
        return dict(_STAGE_BY_KEY["silver_unlocked"])
    if day <= 8:
        return dict(_STAGE_BY_KEY["gold_unlocked"])
    return dict(_STAGE_BY_KEY["diamond_unlocked"])


def data_version_for_cache(data: dict[str, Any] | None) -> Any:
    if isinstance(data, dict) and data.get("data_version"):
        version = data["data_version"]
        if isinstance(version, dict):
            return tuple(
                sorted(
                    (
                        name,
                        item.get("mtime_ns") if isinstance(item, dict) else None,
                        item.get("size") if isinstance(item, dict) else None,
                    )
                    for name, item in version.items()
                )
            )
        return version
    if not isinstance(data, dict):
        return "unknown"
    return (
        id(data.get("cards")),
        id(data.get("events")),
        id(data.get("rarity_rules")),
        len(data.get("cards", {}) or {}),
        len(data.get("events", {}) or {}),
    )


def clear_shop_pool_cache() -> None:
    _MEMORY_CACHE.clear()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_key(value: Any) -> str:
    return _normalize_text(value).lower()


def _event_identity(event_name: str, event_data: dict[str, Any]) -> tuple[Any, ...]:
    rule_signature = {
        "category": event_data.get("event_category"),
        "event_heroes": event_data.get("event_heroes"),
        "hero_filter": event_data.get("hero_filter"),
        "shop_pool": event_data.get("shop_pool"),
    }
    return (
        event_name,
        event_data.get("source_id"),
        tuple(event_data.get("source_ids", []) or []),
        event_data.get("_override_source"),
        bool(event_data.get("_has_manual_override")),
        json.dumps(rule_signature, ensure_ascii=False, sort_keys=True, default=str),
    )


def _price_values_for_card(card_data: dict[str, Any], rarity_filter: dict[str, str] | None) -> list[float]:
    buy_prices = card_data.get("buy_prices")
    if not isinstance(buy_prices, dict):
        return []

    allowed = None
    if isinstance(rarity_filter, dict):
        order = {"bronze": 1, "silver": 2, "gold": 3, "diamond": 4, "legendary": 5}
        min_rarity = _normalize_key(rarity_filter.get("min"))
        max_rarity = _normalize_key(rarity_filter.get("max"))
        if min_rarity in order and max_rarity in order:
            allowed = {
                rarity
                for rarity, rarity_order in order.items()
                if order[min_rarity] <= rarity_order <= order[max_rarity]
            }

    values: list[float] = []
    for rarity, price in buy_prices.items():
        if not isinstance(price, (int, float)):
            continue
        if allowed is not None and _normalize_key(rarity) not in allowed:
            continue
        values.append(float(price))
    return values


def _pool_focus(pool_rule: dict[str, Any] | None, pool_count: int) -> str:
    if not isinstance(pool_rule, dict):
        return "unknown"
    exact_names = pool_rule.get("exact_names") or []
    reward_tags = pool_rule.get("reward_tags") or []
    size_filter = pool_rule.get("size_filter") or []
    hero_scope = _normalize_key(pool_rule.get("hero_scope") or "current")

    if exact_names or pool_count <= 8:
        return "precise"
    if reward_tags or size_filter or hero_scope in {"fixed", "other"}:
        return "focused"
    return "broad"


def _candidate_summary(card: dict[str, Any]) -> dict[str, Any]:
    raw = card.get("raw") if isinstance(card.get("raw"), dict) else {}
    return {
        "card_name": card.get("name"),
        "card_id": raw.get("id") or raw.get("source_id") or raw.get("template_id"),
    }


def build_shop_pool_summary(
    *,
    event_name: str,
    event_data: dict[str, Any],
    cards: dict[str, Any],
    current_day: int,
    current_hero: str,
    rarity_rules: dict[str, Any],
    merchant_profile: dict[str, Any] | None = None,
    resolver: Callable[..., tuple[list[dict[str, Any]], dict[str, str] | None]],
) -> dict[str, Any]:
    stage = shop_stage_for_day(current_day)
    possible_cards, resolved_rarity_filter = resolver(
        event_data,
        cards,
        int(stage["representative_day"]),
        rarity_rules,
        current_hero,
    )

    candidates = [
        _candidate_summary(card)
        for card in possible_cards
        if card.get("name")
    ]
    price_values: list[float] = []
    for candidate in candidates:
        card_data = cards.get(candidate["card_name"])
        if isinstance(card_data, dict):
            price_values.extend(_price_values_for_card(card_data, resolved_rarity_filter))

    pool_rule = (
        event_data.get("shop_pool")
        if isinstance(event_data.get("shop_pool"), dict)
        else {}
    )
    pool_count = len(candidates)
    availability = _merchant_availability_summary(
        merchant_profile or _merchant_profile_for_event(event_name, event_data),
        current_day,
    )
    summary = {
        "cache_status": "hit",
        "merchant_name": event_name,
        "current_hero": current_hero,
        "shop_tier": (merchant_profile or {}).get("shop_tier"),
        "base_refresh_cost": (merchant_profile or {}).get("base_refresh_cost"),
        "base_refresh_count": (merchant_profile or {}).get("base_refresh_count"),
        "refresh_enabled": (merchant_profile or {}).get("refresh_enabled"),
        "stage": stage["key"],
        "stage_label": stage["label"],
        "representative_day": stage["representative_day"],
        "resolved_rarity_filter": resolved_rarity_filter,
        "pool_count": pool_count,
        "card_names": [candidate["card_name"] for candidate in candidates],
        "card_ids": [
            candidate["card_id"]
            for candidate in candidates
            if candidate.get("card_id")
        ],
        "avg_price": (sum(price_values) / len(price_values)) if price_values else None,
        "median_price": median(price_values) if price_values else None,
        "price_sample_count": len(price_values),
        "pool_focus": _pool_focus(pool_rule, pool_count),
        "enchantment_required": bool(
            pool_rule.get("enchantment_required") or pool_rule.get("enchanted_shop")
        ),
        **availability,
    }
    return summary


def _merchant_profile_for_event(event_name: str, event_data: dict[str, Any]) -> dict[str, Any]:
    profile = event_data.get("merchant_profile")
    if isinstance(profile, dict):
        return profile
    return {"name": event_name}


def _merchant_availability_summary(
    profile: dict[str, Any],
    current_day: int,
) -> dict[str, Any]:
    day_range = profile.get("available_day_range")
    if not isinstance(day_range, list) or len(day_range) != 2:
        return {}
    try:
        start = int(day_range[0])
        day = int(current_day)
    except (TypeError, ValueError):
        return {}

    raw_end = day_range[1]
    if raw_end in (None, ""):
        end = None
    else:
        try:
            end = int(raw_end)
        except (TypeError, ValueError):
            return {}

    return {
        "available_day_range": [start, end],
        "available_days": profile.get("available_days"),
        "available_on_day": day >= start and (end is None or day <= end),
    }


def get_cached_shop_pool_summary(
    *,
    data: dict[str, Any] | None,
    event_name: str,
    event_data: dict[str, Any],
    cards: dict[str, Any],
    current_day: int,
    current_hero: str | None,
    rarity_rules: dict[str, Any],
    resolver: Callable[..., tuple[list[dict[str, Any]], dict[str, str] | None]],
) -> dict[str, Any]:
    if not _normalize_text(current_hero):
        return {
            "cache_status": "unknown",
            "reason": "missing_current_hero",
            "merchant_name": event_name,
            "current_hero": None,
            "stage": shop_stage_for_day(current_day)["key"],
        }

    stage = shop_stage_for_day(current_day)
    merchant_profile = _resolve_merchant_profile(data, event_name, event_data)
    key = (
        data_version_for_cache(data),
        _normalize_key(current_hero),
        stage["key"],
        *_event_identity(event_name, event_data),
    )
    cached = _MEMORY_CACHE.get(key)
    if cached is not None:
        return dict(cached)

    summary = build_shop_pool_summary(
        event_name=event_name,
        event_data=event_data,
        cards=cards,
        current_day=current_day,
        current_hero=str(current_hero),
        rarity_rules=rarity_rules,
        merchant_profile=merchant_profile,
        resolver=resolver,
    )
    _MEMORY_CACHE[key] = dict(summary)
    return summary


def _resolve_merchant_profile(
    data: dict[str, Any] | None,
    event_name: str,
    event_data: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    index = data.get("merchant_profile_index")
    if not isinstance(index, dict):
        return None
    candidates = [
        event_name,
        event_data.get("name"),
        event_data.get("source_id"),
        event_data.get("id"),
    ]
    raw_profile = event_data.get("merchant_profile")
    if isinstance(raw_profile, dict):
        candidates.extend(
            raw_profile.get(key)
            for key in ("name", "source_id", "id", "template_id", "internal_name")
        )
    for candidate in candidates:
        profile = index.get(_normalize_key(candidate))
        if isinstance(profile, dict):
            return profile
    return None


def hydrate_cached_shop_cards(
    summary: dict[str, Any],
    cards: dict[str, Any],
) -> list[dict[str, Any]]:
    names = summary.get("card_names")
    if not isinstance(names, list):
        return []

    result: list[dict[str, Any]] = []
    for name in names:
        card_data = cards.get(name)
        if not isinstance(card_data, dict):
            continue
        result.append(
            {
                "name": name,
                "tier": card_data.get("tier", "Unknown"),
                "tags": card_data.get("tags", []),
                "min_rarity": card_data.get("min_rarity"),
                "max_rarity": card_data.get("max_rarity"),
                "enchantment_required": bool(summary.get("enchantment_required")),
                "raw": card_data,
            }
        )
    return result
