from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from shop_state import build_merchant_profiles, merchant_profile_index


def load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def load_json_if_exists(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Data file must be a JSON object: {path}")

    return data


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
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


def merge_cards_with_ratings(
    official_cards: dict[str, Any],
    card_ratings: dict[str, Any],
) -> dict[str, Any]:
    merged_cards: dict[str, Any] = {}

    for card_name, official_data in official_cards.items():
        rating_data = card_ratings.get(card_name, {})
        tiers = normalize_text_list(official_data.get("tiers", []))
        min_rarity = normalize_text(
            official_data.get("min_rarity") or official_data.get("starting_tier")
        )
        max_rarity = normalize_text(
            official_data.get("max_rarity") or (tiers[-1] if tiers else min_rarity)
        )

        merged_cards[card_name] = {
            **official_data,
            "min_rarity": min_rarity,
            "max_rarity": max_rarity,
            "tiers": tiers,
            "tags": normalize_text_list(official_data.get("tags", [])),
            "tier": rating_data.get("tier", "Unknown"),
            "build_roles": rating_data.get("build_roles", {}),
        }

    return merged_cards


def flatten_events_list(raw_events: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    for category_name, category_list in raw_events.items():
        if not isinstance(category_list, list):
            continue

        for event_data in category_list:
            name = event_data.get("name")
            if not name:
                continue

            incoming = {
                **event_data,
                "event_category": category_name,
                "reward_keywords": event_data.get("reward_keywords", []),
                "hero_filter": event_data.get("hero_filter"),
            }
            if name in flattened:
                flattened[name] = merge_duplicate_event(flattened[name], incoming)
            else:
                flattened[name] = incoming

    return flattened


def merge_duplicate_event(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    low_priority_categories = {"unknown_events", "utility_events"}
    existing_category = existing.get("event_category")
    incoming_category = incoming.get("event_category")
    if existing_category not in low_priority_categories and incoming_category in low_priority_categories:
        preferred = dict(existing)
    elif existing_category in low_priority_categories and incoming_category not in low_priority_categories:
        preferred = dict(incoming)
    else:
        preferred = dict(incoming)

    preferred["source_ids"] = normalize_unique_values(
        existing.get("source_ids", []) + incoming.get("source_ids", [])
    )
    preferred["event_heroes"] = normalize_unique_values(
        existing.get("event_heroes", []) + incoming.get("event_heroes", [])
    )
    return preferred


def normalize_unique_values(values: list[Any]) -> list[Any]:
    result = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def deep_merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    深度合并字典。

    规则：
    - dict：递归合并
    - list：直接替换，不做追加
    - 普通字段：override 覆盖 base
    """
    result = deepcopy(base)

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = deepcopy(value)

    return result


def apply_event_overrides(
    events: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """
    应用人工事件修正层。

    event_overrides.json 的 key 使用 flatten 后的事件名。
    例如：
    {
      "Midsworth": {
        "_override_reason": "人工修正：不出售中型物品。",
        "shop_pool": {
          "size_filter": ["small", "large"]
        }
      }
    }
    """
    result = deepcopy(events)

    for event_name, override_data in overrides.items():
        if not isinstance(override_data, dict):
            continue

        base_event = result.get(event_name, {})
        if not isinstance(base_event, dict):
            base_event = {}

        merged = deep_merge_dict(base_event, override_data)
        merged["_has_manual_override"] = True
        merged["_override_source"] = "data/event_overrides.json"

        if "_override_reason" not in merged:
            merged["_override_reason"] = "该事件已应用人工修正规则。"

        result[event_name] = merged

    return result


def build_index(data: dict[str, Any]) -> dict[int, str]:
    return {index + 1: name for index, name in enumerate(data.keys())}


def load_all_data(data_dir: str | Path) -> dict[str, Any]:
    data_dir = Path(data_dir)

    official_cards = load_json(data_dir / "cards_generated.json")
    card_ratings = load_json(data_dir / "card_ratings.json")
    cards = merge_cards_with_ratings(official_cards, card_ratings)

    encounters = load_json_if_exists(data_dir / "encounters_generated.json")
    raw_events = load_json(data_dir / "events.json")
    events = flatten_events_list(raw_events)

    event_overrides = load_json_if_exists(data_dir / "event_overrides.json")
    events = apply_event_overrides(events, event_overrides)
    merchant_profiles = build_merchant_profiles(encounters, events)

    builds = load_json(data_dir / "community_builds.json")
    rarity_rules = load_json(data_dir / "rarity_rules.json")
    translations_path = data_dir / "translations_zh_cn.json"
    translations = load_json(translations_path) if translations_path.exists() else {}

    return {
        "cards": cards,
        "events": events,
        "encounters": encounters,
        "merchant_profiles": merchant_profiles,
        "merchant_profile_index": merchant_profile_index(merchant_profiles),
        "event_index": build_index(events),
        "builds": builds,
        "build_index": build_index(builds),
        "rarity_rules": rarity_rules,
        "translations": translations,
    }
