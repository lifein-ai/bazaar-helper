from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = BASE_DIR / "raw_data" / "events.csv"
DEFAULT_OUTPUT = BASE_DIR / "data" / "events.json"
DEFAULT_CARDS = BASE_DIR / "data" / "cards.json"
DEFAULT_RARITY_RULES = BASE_DIR / "data" / "rarity_rules.json"

VALID_CATEGORIES = {"shop", "skill_shop", "resource_event", "item_event", "event"}
VALID_MATCH_MODES = {"any", "all"}
VALID_RARITIES = {"bronze", "silver", "gold", "diamond", "legendary"}


def split_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def normalize(value: str | None) -> str:
    return (value or "").strip()


def normalize_lower(value: str | None) -> str:
    return normalize(value).lower()


def parse_int(value: str | None) -> int:
    value = normalize(value)
    return int(value) if value else 0


def read_csv(path: Path) -> list[dict[str, str]]:
    encodings = ["utf-8-sig", "utf-8", "gb18030", "gbk"]
    last_error: UnicodeDecodeError | None = None

    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                return list(csv.DictReader(file))
        except UnicodeDecodeError as error:
            last_error = error

    raise RuntimeError(f"Cannot read CSV encoding for {path}: {last_error}")


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def build_rarity_filter(row: dict[str, str]) -> dict[str, str] | None:
    rarity_min = normalize_lower(row.get("rarity_min"))
    rarity_max = normalize_lower(row.get("rarity_max"))

    if not rarity_min and not rarity_max:
        return None

    if not rarity_min or not rarity_max:
        raise ValueError("rarity_min and rarity_max must be filled together")

    return {"min": rarity_min, "max": rarity_max}


def row_category(row: dict[str, str]) -> str:
    category = normalize_lower(row.get("category") or row.get("event_type"))
    if category == "event":
        return "item_event"
    return category


def build_shop(row: dict[str, str]) -> dict[str, Any]:
    rarity_filter = build_rarity_filter(row)

    return {
        "name": normalize(row.get("name")),
        "shop_type": normalize_lower(row.get("shop_type")) or "tag",
        "hero_filter": normalize(row.get("hero_filter")) or None,
        "shop_pool": {
            "reward_tags": split_list(row.get("reward_tags") or row.get("target_tags")),
            "match_mode": normalize_lower(row.get("match_mode")) or "any",
            "rarity_filter": rarity_filter,
            "rarity_rule": normalize(row.get("rarity_rule")) or None,
            "excluded_tags": split_list(row.get("excluded_tags")) or ["legendary"],
        },
        "notes": normalize(row.get("notes")),
    }


def build_item_event(row: dict[str, str]) -> dict[str, Any]:
    return {
        "name": normalize(row.get("name")),
        "event_type": "item_event",
        "effect": normalize_lower(row.get("effect")) or "improve_items",
        "target_tags": split_list(row.get("target_tags") or row.get("reward_tags")),
        "match_mode": normalize_lower(row.get("match_mode")) or "any",
        "rarity_filter": build_rarity_filter(row),
        "rarity_rule": normalize(row.get("rarity_rule")) or None,
        "resource_rewards": {
            "gold": parse_int(row.get("gold")),
            "exp": parse_int(row.get("exp")),
            "health": parse_int(row.get("health")),
        },
        "notes": normalize(row.get("notes")),
    }


def build_resource_event(row: dict[str, str]) -> dict[str, Any]:
    card_reward_enabled = bool(
        split_list(row.get("reward_tags") or row.get("target_tags"))
        or normalize(row.get("rarity_rule"))
        or normalize(row.get("rarity_min"))
    )

    return {
        "name": normalize(row.get("name")),
        "event_type": "resource_event",
        "resource_rewards": {
            "gold": parse_int(row.get("gold")),
            "exp": parse_int(row.get("exp")),
            "health": parse_int(row.get("health")),
        },
        "card_reward": {
            "enabled": card_reward_enabled,
            "reward_tags": split_list(row.get("reward_tags") or row.get("target_tags")),
            "match_mode": normalize_lower(row.get("match_mode")) or "any",
            "rarity_filter": build_rarity_filter(row),
            "rarity_rule": normalize(row.get("rarity_rule")) or None,
            "excluded_tags": split_list(row.get("excluded_tags")) or ["legendary"],
        },
        "notes": normalize(row.get("notes")),
    }


def build_events(rows: list[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = {
        "shops": [],
        "skill_shops": [],
        "resource_events": [],
        "item_events": [],
    }

    for row in rows:
        name = normalize(row.get("name"))
        if not name:
            continue

        category = row_category(row)
        if category == "shop":
            events["shops"].append(build_shop(row))
        elif category == "skill_shop":
            events["skill_shops"].append(build_shop(row))
        elif category == "resource_event":
            events["resource_events"].append(build_resource_event(row))
        elif category == "item_event":
            events["item_events"].append(build_item_event(row))
        else:
            raise ValueError(f"Unknown event category for {name}: {category}")

    return events


def known_tags(cards_path: Path) -> set[str]:
    cards = load_json(cards_path)
    tags: set[str] = set()

    for card in cards.values():
        for tag in card.get("tags", []):
            tags.add(normalize_lower(tag))

    return tags


def validate_events(
    events: dict[str, list[dict[str, Any]]],
    card_tags: set[str],
    rarity_rules: dict[str, Any],
) -> list[str]:
    warnings: list[str] = []
    names: set[str] = set()

    def check_name(name: str) -> None:
        if name in names:
            warnings.append(f"Duplicate event name: {name}")
        names.add(name)

    def check_pool(name: str, pool: dict[str, Any]) -> None:
        match_mode = pool.get("match_mode", "any")
        if match_mode not in VALID_MATCH_MODES:
            warnings.append(f"{name}: unknown match_mode '{match_mode}'")

        for tag in pool.get("reward_tags", []) + pool.get("excluded_tags", []):
            if tag and tag not in card_tags and tag != "legendary":
                warnings.append(f"{name}: tag not found in card data: {tag}")

        rarity_filter = pool.get("rarity_filter")
        if rarity_filter:
            for key in ["min", "max"]:
                rarity = rarity_filter.get(key)
                if rarity not in VALID_RARITIES:
                    warnings.append(f"{name}: unknown rarity {key}='{rarity}'")

        rarity_rule = pool.get("rarity_rule")
        if rarity_rule and rarity_rule not in rarity_rules:
            warnings.append(f"{name}: rarity_rule not found: {rarity_rule}")

    for shop in events["shops"] + events["skill_shops"]:
        check_name(shop["name"])
        check_pool(shop["name"], shop["shop_pool"])

    for event in events["item_events"]:
        check_name(event["name"])
        check_pool(
            event["name"],
            {
                "reward_tags": event.get("target_tags", []),
                "match_mode": event.get("match_mode", "any"),
                "rarity_filter": event.get("rarity_filter"),
                "rarity_rule": event.get("rarity_rule"),
                "excluded_tags": [],
            },
        )

    for event in events["resource_events"]:
        check_name(event["name"])
        if event.get("card_reward", {}).get("enabled"):
            check_pool(event["name"], event["card_reward"])

    return warnings


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import event CSV into data/events.json.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cards", type=Path, default=DEFAULT_CARDS)
    parser.add_argument("--rarity-rules", type=Path, default=DEFAULT_RARITY_RULES)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = read_csv(args.input)
    events = build_events(rows)
    warnings = validate_events(events, known_tags(args.cards), load_json(args.rarity_rules))

    if warnings:
        print("Validation warnings:")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("Validation passed.")

    if args.check_only:
        return

    write_json(args.output, events)
    print(f"Wrote {args.output}")
    print(
        "Counts: "
        + ", ".join(f"{category}={len(items)}" for category, items in events.items())
    )


if __name__ == "__main__":
    main()
