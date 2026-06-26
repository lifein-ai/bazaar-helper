from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


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


def build_index(data: dict[str, Any]) -> dict[int, str]:
    return {index + 1: name for index, name in enumerate(data.keys())}


def load_all_data(data_dir: str | Path) -> dict[str, Any]:
    data_dir = Path(data_dir)

    official_cards = load_json(data_dir / "cards_generated.json")
    card_ratings = load_json(data_dir / "card_ratings.json")
    cards = merge_cards_with_ratings(official_cards, card_ratings)

    raw_events = load_json(data_dir / "events.json")
    events = flatten_events_list(raw_events)

    builds = load_json(data_dir / "builds.json")
    rarity_rules = load_json(data_dir / "rarity_rules.json")
    translations_path = data_dir / "translations_zh_cn.json"
    translations = load_json(translations_path) if translations_path.exists() else {}

    return {
        "cards": cards,
        "events": events,
        "event_index": build_index(events),
        "builds": builds,
        "build_index": build_index(builds),
        "rarity_rules": rarity_rules,
        "translations": translations,
    }
