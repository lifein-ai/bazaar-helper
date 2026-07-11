from __future__ import annotations

import re
from typing import Any


RARITY_ORDER = ["bronze", "silver", "gold", "diamond", "legendary"]


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_key(value: Any) -> str:
    return normalize_text(value).lower()


def normalize_rarity(value: Any) -> str | None:
    text = normalize_key(value)
    if not text:
        return None
    text = text.replace("_", " ").replace("-", " ")
    for rarity in RARITY_ORDER:
        if text == rarity or text == f"{rarity} tier":
            return rarity
    return None


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return None


def rarity_range(min_rarity: str | None, max_rarity: str | None) -> list[str]:
    if min_rarity not in RARITY_ORDER or max_rarity not in RARITY_ORDER:
        return []
    start = RARITY_ORDER.index(min_rarity)
    end = RARITY_ORDER.index(max_rarity)
    if start > end:
        return []
    return RARITY_ORDER[start : end + 1]


def tier_from_text(*values: Any) -> str | None:
    for value in values:
        text = normalize_key(value)
        if not text:
            continue
        for rarity in RARITY_ORDER:
            if re.search(rf"\b{rarity}(?:[- ]tier)?\b", text):
                return rarity
    return None


def is_merchant_like(entry: dict[str, Any]) -> bool:
    tags = {normalize_key(tag) for tag in entry.get("tags", []) if tag}
    description = normalize_key(entry.get("description"))
    category = normalize_key(entry.get("event_category"))
    return (
        "merchant" in tags
        or category in {"shops", "skill_shops"}
        or isinstance(entry.get("shop_pool"), dict)
        or ("merchant" in description and "sell" in description)
        or description.startswith("sells ")
    )


def profile_aliases(profile: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in (
        "id",
        "source_id",
        "template_id",
        "name",
        "internal_name",
        "merchant_id",
        "merchant_template_id",
        "merchant_name",
    ):
        value = profile.get(key)
        if value:
            aliases.append(str(value))
    aliases.extend(str(value) for value in profile.get("source_ids", []) if value)
    return list(dict.fromkeys(aliases))


def merchant_profile_from_entry(
    name: str,
    entry: dict[str, Any],
) -> dict[str, Any] | None:
    if not is_merchant_like(entry) and not isinstance(entry.get("merchant_profile"), dict):
        return None

    raw_profile = (
        dict(entry.get("merchant_profile"))
        if isinstance(entry.get("merchant_profile"), dict)
        else {}
    )

    shop_tier = normalize_rarity(
        raw_profile.get("shop_tier")
        or entry.get("shop_tier")
        or entry.get("rarity")
        or entry.get("min_rarity")
    ) or tier_from_text(entry.get("name") or name, entry.get("description"), entry.get("notes"))

    source_ids = list(
        dict.fromkeys(
            str(value)
            for value in (
                raw_profile.get("source_ids")
                or entry.get("source_ids")
                or [entry.get("source_id") or entry.get("id")]
            )
            if value
        )
    )
    source_id = (
        normalize_text(raw_profile.get("source_id"))
        or normalize_text(entry.get("source_id"))
        or normalize_text(entry.get("id"))
    )
    template_id = normalize_text(raw_profile.get("template_id") or entry.get("template_id"))

    shop_pool = entry.get("shop_pool") if isinstance(entry.get("shop_pool"), dict) else {}
    sold_item_tier_filters = [
        rarity
        for rarity in raw_profile.get("sold_item_tier_filters", [])
        if normalize_rarity(rarity)
    ]
    sold_item_tier_filters = [
        normalize_rarity(rarity) or str(rarity).lower()
        for rarity in sold_item_tier_filters
    ]
    if not sold_item_tier_filters:
        rarity_filter = shop_pool.get("rarity_filter")
        if isinstance(rarity_filter, dict):
            sold_item_tier_filters = rarity_range(
                normalize_rarity(rarity_filter.get("min")),
                normalize_rarity(rarity_filter.get("max")),
            )

    base_refresh_cost = optional_int(
        raw_profile.get("base_refresh_cost")
        if "base_refresh_cost" in raw_profile
        else raw_profile.get("reroll_cost")
    )
    base_refresh_count = optional_int(
        raw_profile.get("base_refresh_count")
        if "base_refresh_count" in raw_profile
        else raw_profile.get("number_of_rerolls")
    )
    refresh_enabled = optional_bool(raw_profile.get("refresh_enabled"))
    if refresh_enabled is None and (
        base_refresh_cost is not None or base_refresh_count is not None
    ):
        refresh_enabled = True

    profile = {
        "name": normalize_text(raw_profile.get("name") or entry.get("name") or name),
        "internal_name": normalize_text(
            raw_profile.get("internal_name") or entry.get("internal_name")
        ),
        "id": source_id,
        "source_id": source_id,
        "template_id": template_id or source_id,
        "source_ids": source_ids,
        "shop_tier": shop_tier,
        "shop_tier_source": (
            raw_profile.get("shop_tier_source")
            or ("merchant_card_rarity" if shop_tier else None)
        ),
        "base_refresh_cost": base_refresh_cost,
        "base_refresh_count": base_refresh_count,
        "refresh_enabled": refresh_enabled,
        "sold_item_tier_filters": sold_item_tier_filters,
    }
    return {key: value for key, value in profile.items() if value not in (None, "", [])}


def build_merchant_profiles(
    encounters: dict[str, Any],
    events: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}

    for name, entry in encounters.items():
        if isinstance(entry, dict):
            profile = merchant_profile_from_entry(str(name), entry)
            if profile:
                profiles[profile["name"] or str(name)] = profile

    for name, entry in events.items():
        if isinstance(entry, dict):
            profile = merchant_profile_from_entry(str(name), entry)
            if profile:
                existing = profiles.get(profile["name"] or str(name), {})
                profiles[profile["name"] or str(name)] = {
                    **profile,
                    **{key: value for key, value in existing.items() if value not in (None, "", [])},
                    **{key: value for key, value in profile.items() if value not in (None, "", [])},
                }

    return dict(sorted(profiles.items(), key=lambda item: item[0].lower()))


def merchant_profile_index(
    profiles: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for name, profile in profiles.items():
        for alias in [name, *profile_aliases(profile)]:
            key = normalize_key(alias)
            if key:
                index[key] = profile
    return index


def resolve_merchant_profile(
    data: dict[str, Any],
    current_shop: dict[str, Any] | None,
    event_options: list[str] | None = None,
) -> dict[str, Any] | None:
    profiles = data.get("merchant_profiles")
    if not isinstance(profiles, dict) or not profiles:
        return None

    index = data.get("merchant_profile_index")
    if not isinstance(index, dict):
        index = merchant_profile_index(profiles)

    candidates: list[Any] = []
    if isinstance(current_shop, dict):
        candidates.extend(
            current_shop.get(key)
            for key in (
                "merchant_id",
                "merchant_template_id",
                "merchant_name",
                "source_id",
                "template_id",
                "name",
            )
        )
    candidates.extend(event_options or [])

    for candidate in candidates:
        profile = index.get(normalize_key(candidate))
        if profile:
            return profile
    return None


def merge_effective_shop(
    data: dict[str, Any],
    current_shop: dict[str, Any] | None,
    event_options: list[str] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(current_shop, dict):
        return None

    profile = resolve_merchant_profile(data, current_shop, event_options)
    effective = dict(current_shop)
    if profile:
        effective.setdefault("merchant_name", profile.get("name"))
        effective.setdefault("merchant_id", profile.get("source_id") or profile.get("id"))
        effective.setdefault("merchant_template_id", profile.get("template_id"))
        effective["merchant_profile"] = profile
        effective["shop_tier"] = profile.get("shop_tier")
        effective["shop_tier_source"] = profile.get("shop_tier_source")
        effective["sold_item_tier_filters"] = profile.get("sold_item_tier_filters", [])
        effective["template_refresh_cost"] = profile.get("base_refresh_cost")
        effective["template_refreshes_total"] = profile.get("base_refresh_count")
        effective["template_refresh_enabled"] = profile.get("refresh_enabled")

    runtime_refresh_cost = optional_int(current_shop.get("refresh_cost"))
    template_refresh_cost = optional_int(effective.get("template_refresh_cost"))
    effective["refresh_cost"] = (
        runtime_refresh_cost
        if runtime_refresh_cost is not None
        else template_refresh_cost
    )
    effective["refresh_cost_source"] = (
        "runtime"
        if runtime_refresh_cost is not None
        else "template"
        if template_refresh_cost is not None
        else "unknown"
    )

    runtime_available = optional_bool(current_shop.get("refresh_available"))
    template_available = optional_bool(effective.get("template_refresh_enabled"))
    effective["refresh_available"] = (
        runtime_available
        if runtime_available is not None
        else template_available
    )
    effective["refresh_available_source"] = (
        "runtime"
        if runtime_available is not None
        else "template"
        if template_available is not None
        else "unknown"
    )

    runtime_remaining = optional_int(current_shop.get("refreshes_remaining"))
    template_total = optional_int(effective.get("template_refreshes_total"))
    effective["refreshes_remaining"] = (
        runtime_remaining
        if runtime_remaining is not None
        else template_total
    )
    effective["refreshes_remaining_source"] = (
        "runtime"
        if runtime_remaining is not None
        else "template"
        if template_total is not None
        else "unknown"
    )

    runtime_used = optional_int(current_shop.get("refreshes_used"))
    if runtime_used is None and template_total is not None and runtime_remaining is not None:
        runtime_used = max(0, template_total - runtime_remaining)
    effective["refreshes_used"] = runtime_used
    effective["refreshes_total"] = template_total

    return effective
