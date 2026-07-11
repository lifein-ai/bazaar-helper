from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from build_events_from_encounters import build_events


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from app_paths import get_runtime_dir  # noqa: E402


RUNTIME_DIR = get_runtime_dir()
DATA_DIR = BASE_DIR / "data"
LIVE_CARDS_PATH = RUNTIME_DIR / "live_cards_raw.json"
CARDS_OUTPUT_PATH = DATA_DIR / "cards_generated.json"
CARDS_BACKUP_PATH = DATA_DIR / "cards_generated.backup.json"
ENCOUNTERS_OUTPUT_PATH = DATA_DIR / "encounters_generated.json"
ENCOUNTERS_BACKUP_PATH = DATA_DIR / "encounters_generated.backup.json"
EVENTS_OUTPUT_PATH = DATA_DIR / "events.json"
EVENTS_BACKUP_PATH = DATA_DIR / "events.backup.json"

CARD_TYPES = {"Item", "Skill"}
ENCOUNTER_CACHE_TYPES = {
    "EncounterStep": "TCardEncounterStep",
    "EventEncounter": "TCardEncounterEvent",
    "CombatEncounter": "TCardEncounterCombat",
    "PedestalEncounter": "TCardEncounterPedestal",
}

RARITY_NAMES = {
    "bronze": "Bronze",
    "silver": "Silver",
    "gold": "Gold",
    "diamond": "Diamond",
    "legendary": "Legendary",
}


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_text(value).lower()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def normalize_heroes(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def normalize_rarity(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""

    mapped = RARITY_NAMES.get(text.lower())
    return mapped or text


def normalize_rarity_lower(value: Any) -> str:
    return normalize_rarity(value).lower()


def normalize_rarity_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        rarity = normalize_rarity(value)
        if not rarity or rarity in seen:
            continue
        seen.add(rarity)
        result.append(rarity)
    return result


def normalize_price_map(values: Any) -> dict[str, Any]:
    if not isinstance(values, dict):
        return {}

    result: dict[str, Any] = {}
    for key, value in values.items():
        rarity = normalize_rarity(key)
        if rarity:
            result[rarity] = value
    return result


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


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def first_present(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def normalize_rarity_filters(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        rarity = normalize_rarity_lower(value)
        if not rarity or rarity in seen:
            continue
        seen.add(rarity)
        result.append(rarity)
    return result


def merchant_profile_from_raw(
    raw: dict[str, Any],
    *,
    source_id: str,
    template_id: str,
    name: str,
) -> dict[str, Any] | None:
    tags = {tag.lower() for tag in normalize_tags(raw.get("tags"))}
    description = normalize_text(raw.get("description")).lower()
    if "merchant" not in tags and not description.startswith("sells "):
        return None

    spawn_filter = first_dict(
        raw.get("spawning_filter"),
        raw.get("SpawningFilter"),
        raw.get("spawningFilter"),
        raw.get("spawn_filter"),
    )
    reroll = first_dict(
        raw.get("reroll"),
        raw.get("Reroll"),
        spawn_filter.get("Reroll"),
        spawn_filter.get("reroll"),
    )
    rerolls = spawn_filter.get("Rerolls") or spawn_filter.get("rerolls")
    if not reroll and isinstance(rerolls, list) and rerolls:
        reroll = first_dict(*rerolls)

    base_refresh_cost = optional_int(
        first_present(
            raw,
            "base_refresh_cost",
            "reroll_cost",
            "RerollCost",
            "refresh_cost",
        )
    )
    if base_refresh_cost is None:
        base_refresh_cost = optional_int(
            first_present(reroll, "RerollCost", "reroll_cost", "Cost", "cost")
        )

    base_refresh_count = optional_int(
        first_present(
            raw,
            "base_refresh_count",
            "number_of_rerolls",
            "NumberOfRerolls",
            "refresh_count",
        )
    )
    if base_refresh_count is None:
        base_refresh_count = optional_int(
            first_present(
                reroll,
                "NumberOfRerolls",
                "number_of_rerolls",
                "Count",
                "count",
            )
        )

    refresh_enabled = optional_bool(
        first_present(raw, "refresh_enabled", "RerollEnabled", "reroll_enabled")
    )
    if refresh_enabled is None:
        refresh_enabled = optional_bool(
            first_present(reroll, "RerollEnabled", "reroll_enabled", "Enabled", "enabled")
        )

    sold_item_tier_filters = normalize_rarity_filters(
        first_present(raw, "ItemTierFilters", "item_tier_filters")
        or first_present(spawn_filter, "ItemTierFilters", "item_tier_filters")
        or []
    )

    shop_tier = normalize_rarity_lower(raw.get("rarity"))
    profile = {
        "id": source_id,
        "source_id": source_id,
        "template_id": template_id,
        "name": name,
        "internal_name": normalize_text(raw.get("internal_name")),
        "source_ids": [source_id] if source_id else [],
        "shop_tier": shop_tier,
        "shop_tier_source": "merchant_card_rarity" if shop_tier else None,
        "base_refresh_cost": base_refresh_cost,
        "base_refresh_count": base_refresh_count,
        "refresh_enabled": refresh_enabled,
        "sold_item_tier_filters": sold_item_tier_filters,
    }
    return {key: value for key, value in profile.items() if value not in (None, "", [])}


def read_live_cards(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)

    if isinstance(payload, list):
        return [card for card in payload if isinstance(card, dict)]

    if isinstance(payload, dict):
        cards = payload.get("cards")
        if isinstance(cards, list):
            return [card for card in cards if isinstance(card, dict)]

    raise ValueError(f"Unsupported live card payload: {path}")


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = normalize_text(value)
        if text:
            return text
    return ""


def merge_visible_tags(tags: list[str], hidden_tags: list[str], visible_tags: Any) -> list[str]:
    if isinstance(visible_tags, list):
        normalized = normalize_tags(visible_tags)
        if normalized:
            return normalized

    hidden = {tag.lower() for tag in hidden_tags}
    return [tag for tag in tags if tag.lower() not in hidden]


def attach_runtime_raw_fields(record: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    for key in ["raw_effects", "raw_effect_fields", "spawning_filter"]:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            record[key] = value
    return record


def convert_card(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = first_nonempty(raw.get("name"), raw.get("internal_name"), raw.get("source_id"), raw.get("template_id"))
    if not name:
        return None

    heroes = normalize_heroes(raw.get("heroes"))
    hero = normalize_text(raw.get("hero"))
    if not hero and len(heroes) == 1:
        hero = heroes[0]

    visible_source_tags = normalize_tags(raw.get("tags"))
    hidden_tags = normalize_tags(raw.get("hidden_tags"))
    tags = normalize_tags([*visible_source_tags, *hidden_tags])
    tiers = normalize_rarity_list(raw.get("tiers"))
    rarity = normalize_rarity(raw.get("rarity")) or (tiers[0] if tiers else "")
    min_rarity = normalize_rarity(raw.get("min_rarity")) or (tiers[0] if tiers else rarity)
    max_rarity = normalize_rarity(raw.get("max_rarity")) or (tiers[-1] if tiers else rarity)
    source_id = first_nonempty(raw.get("source_id"), raw.get("id"))
    template_id = first_nonempty(raw.get("template_id"), source_id)

    return attach_runtime_raw_fields({
        "id": first_nonempty(source_id, template_id, name),
        "source_id": source_id,
        "template_id": template_id,
        "internal_name": normalize_text(raw.get("internal_name")),
        "name": name,
        "hero": hero,
        "heroes": heroes,
        "type": normalize_text(raw.get("type") or raw.get("card_type")),
        "size": normalize_text(raw.get("size")),
        "min_rarity": min_rarity,
        "max_rarity": max_rarity,
        "rarity": rarity,
        "tiers": tiers,
        "tags": tags,
        "visible_tags": merge_visible_tags(
            visible_source_tags,
            hidden_tags,
            raw.get("visible_tags"),
        ),
        "hidden_tags": hidden_tags,
        "buy_prices": normalize_price_map(raw.get("buy_prices")),
        "sell_prices": normalize_price_map(raw.get("sell_prices")),
        "description": normalize_text(raw.get("description")),
        "card_pack_id": normalize_text(raw.get("card_pack_id")),
    }, raw)


def build_cards(raw_cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cards: dict[str, dict[str, Any]] = {}
    for raw in raw_cards:
        card = convert_card(raw)
        if not card or card["type"] not in CARD_TYPES:
            continue

        key = card["name"]
        if key in cards:
            key = f"{key} <{card['id'][:8]}>"
        cards[key] = card
    return dict(sorted(cards.items(), key=lambda item: item[0].lower()))


def build_encounters(raw_cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    encounters: dict[str, dict[str, Any]] = {}
    for raw in raw_cards:
        card_type = normalize_text(raw.get("type") or raw.get("card_type"))
        cache_type = ENCOUNTER_CACHE_TYPES.get(card_type)
        if not cache_type:
            continue

        source_id = first_nonempty(
            raw.get("source_id"),
            raw.get("template_id"),
            raw.get("id"),
        )
        name = first_nonempty(
            raw.get("name"),
            raw.get("internal_name"),
            source_id,
        )
        if not source_id or not name:
            continue

        encounter = attach_runtime_raw_fields({
            "id": source_id,
            "source_id": source_id,
            "template_id": first_nonempty(raw.get("template_id"), source_id),
            "internal_name": normalize_text(raw.get("internal_name")),
            "name": name,
            "cache_type": cache_type,
            "type": card_type,
            "heroes": normalize_heroes(raw.get("heroes")),
            "tags": normalize_tags(raw.get("tags")),
            "rarity": normalize_rarity_lower(raw.get("rarity")),
            "min_rarity": normalize_rarity_lower(raw.get("min_rarity")),
            "max_rarity": normalize_rarity_lower(raw.get("max_rarity")),
            "description": normalize_text(raw.get("description")),
            "isReselectable": bool(raw.get("isReselectable", False)),
        }, raw)
        merchant_profile = merchant_profile_from_raw(
            raw,
            source_id=source_id,
            template_id=encounter["template_id"],
            name=name,
        )
        if merchant_profile:
            encounter["merchant_profile"] = merchant_profile
        key = name
        if key in encounters:
            key = f"{name} <{source_id[:8]}>"
        encounters[key] = encounter

    return dict(sorted(encounters.items(), key=lambda item: item[0].lower()))


def backup_file(source: Path, backup: Path) -> None:
    if not source.exists():
        return
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, backup)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import live Bazaar cards and encounters into generated data files."
    )
    parser.add_argument("--input", type=Path, default=LIVE_CARDS_PATH)
    parser.add_argument("--output", type=Path, default=CARDS_OUTPUT_PATH)
    parser.add_argument("--backup", type=Path, default=CARDS_BACKUP_PATH)
    parser.add_argument("--encounters-output", type=Path, default=ENCOUNTERS_OUTPUT_PATH)
    parser.add_argument("--encounters-backup", type=Path, default=ENCOUNTERS_BACKUP_PATH)
    parser.add_argument("--events-output", type=Path, default=EVENTS_OUTPUT_PATH)
    parser.add_argument("--events-backup", type=Path, default=EVENTS_BACKUP_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise FileNotFoundError(f"Missing live card export: {args.input}")

    raw_cards = read_live_cards(args.input)
    cards = build_cards(raw_cards)
    encounters = build_encounters(raw_cards)
    events = build_events(encounters)

    backup_file(args.output, args.backup)
    backup_file(args.encounters_output, args.encounters_backup)
    backup_file(args.events_output, args.events_backup)

    write_json(args.output, cards)
    write_json(args.encounters_output, encounters)
    write_json(args.events_output, events)
    print(f"Read live objects: {len(raw_cards)}")
    print(f"Wrote cards_generated.json: {len(cards)}")
    print(f"Wrote encounters_generated.json: {len(encounters)}")
    print(
        "Wrote events.json: "
        + str(sum(len(category) for category in events.values()))
    )
    print(
        "Backups: "
        + ", ".join(
            str(path)
            for path in [
                args.backup,
                args.encounters_backup,
                args.events_backup,
            ]
        )
    )


if __name__ == "__main__":
    main()
