from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_DIR = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "Tempo Storm"
    / "The Bazaar"
    / "cache"
)
DATA_DIR = BASE_DIR / "data"

TYPE_KEY = "$type"
ITEM_TYPE = "TCardItem"
SKILL_TYPE = "TCardSkill"
ENCOUNTER_PREFIX = "TCardEncounter"
RARITY_ORDER = ["Bronze", "Silver", "Gold", "Diamond", "Legendary"]


def load_cache_list(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        list_values = [value for value in payload.values() if isinstance(value, list)]
        if len(list_values) == 1:
            return list_values[0]

    raise ValueError(f"Unsupported cache JSON shape: {path}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def text_from_localization(localization: dict[str, Any] | None, key: str) -> str | None:
    if not localization:
        return None

    value = localization.get(key)
    if isinstance(value, dict):
        return value.get("Text")

    return None


def title_for_card(card: dict[str, Any]) -> str:
    localization = card.get("Localization")
    title = text_from_localization(localization, "Title")
    return title or card.get("InternalName") or card["Id"]


def description_for_card(card: dict[str, Any]) -> str:
    localization = card.get("Localization") or {}
    description = text_from_localization(localization, "Description")
    if description:
        return description

    tooltips = localization.get("Tooltips") or []
    tooltip_texts = []
    for tooltip in tooltips:
        content = tooltip.get("Content") if isinstance(tooltip, dict) else None
        if isinstance(content, dict) and content.get("Text"):
            tooltip_texts.append(content["Text"])

    return "\n".join(tooltip_texts)


def normalize_tag(value: str) -> str:
    return value.strip().lower()


def unique_normalized(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = normalize_tag(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)

    return result


def tier_names(card: dict[str, Any]) -> list[str]:
    tiers = card.get("Tiers") or {}
    if not isinstance(tiers, dict):
        return []

    return [
        rarity
        for rarity in RARITY_ORDER
        if rarity in tiers
    ]


def buy_prices(card: dict[str, Any]) -> dict[str, int]:
    prices: dict[str, int] = {}

    for rarity, tier_data in (card.get("Tiers") or {}).items():
        attributes = tier_data.get("Attributes") or {}
        buy_price = attributes.get("BuyPrice")
        if buy_price is not None:
            prices[rarity] = buy_price

    return prices


def convert_item(card: dict[str, Any]) -> dict[str, Any]:
    tiers = tier_names(card)
    tags = unique_normalized((card.get("Tags") or []) + (card.get("HiddenTags") or []))
    heroes = card.get("Heroes") or []

    return {
        "id": card["Id"],
        "source_id": card["Id"],
        "internal_name": card.get("InternalName"),
        "name": title_for_card(card),
        "hero": heroes[0] if len(heroes) == 1 else None,
        "heroes": heroes,
        "type": "Item",
        "size": card.get("Size"),
        "min_rarity": tiers[0] if tiers else card.get("StartingTier"),
        "max_rarity": tiers[-1] if tiers else card.get("StartingTier"),
        "tiers": tiers,
        "tags": tags,
        "visible_tags": unique_normalized(card.get("Tags") or []),
        "hidden_tags": unique_normalized(card.get("HiddenTags") or []),
        "buy_prices": buy_prices(card),
        "description": description_for_card(card),
        "card_pack_id": card.get("CardPackId"),
    }


def convert_skill(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": card["Id"],
        "internal_name": card.get("InternalName"),
        "name": title_for_card(card),
        "type": "Skill",
        "heroes": card.get("Heroes") or [],
        "tags": unique_normalized((card.get("Tags") or []) + (card.get("HiddenTags") or [])),
        "description": description_for_card(card),
    }


def convert_encounter(card: dict[str, Any]) -> dict[str, Any]:
    converted = {
        "id": card["Id"],
        "internal_name": card.get("InternalName"),
        "name": title_for_card(card),
        "cache_type": card.get(TYPE_KEY),
        "type": card.get("Type"),
        "heroes": card.get("Heroes") or [],
        "tags": unique_normalized((card.get("Tags") or []) + (card.get("HiddenTags") or [])),
        "description": description_for_card(card),
    }

    for key in [
        "SelectionContext",
        "SelectionCriteria",
        "SelectionRequirements",
        "IsReselectable",
    ]:
        if key in card:
            converted[key[0].lower() + key[1:]] = card.get(key)

    return converted


def convert_card_pack(pack: dict[str, Any]) -> dict[str, Any]:
    localization = pack.get("Localization") or {}
    return {
        "id": pack.get("Id"),
        "internal_name": pack.get("InternalName"),
        "name": text_from_localization(localization, "Name") or pack.get("InternalName"),
        "description": (
            text_from_localization(localization, "Description")
            or pack.get("InternalDescription")
        ),
        "hero": pack.get("Hero"),
        "cards": pack.get("Cards") or [],
    }


def convert_challenge(challenge: dict[str, Any]) -> dict[str, Any]:
    localization = challenge.get("Localization") or {}
    return {
        "id": challenge.get("Id"),
        "duration": challenge.get("Duration"),
        "version": challenge.get("Version"),
        "xp_reward": challenge.get("XpReward"),
        "completion_requirement": challenge.get("CompletionRequirement"),
        "name": text_from_localization(localization, "Title"),
        "description": text_from_localization(localization, "Description"),
    }


def index_by_unique_name(records: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    indexed: dict[str, Any] = {}
    duplicates: list[str] = []

    for record in records:
        name = record["name"]
        key = name

        if key in indexed:
            suffix = str(record.get("id", ""))[:8] or str(len(duplicates) + 1)
            key = f"{name} <{suffix}>"
            record = {**record, "duplicate_name": name}
            duplicates.append(name)

        indexed[key] = record

    return indexed, duplicates


def build_outputs(cache_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    cards = load_cache_list(cache_dir / "cards.json")
    cardpacks = load_cache_list(cache_dir / "cardpacks.json")
    challenges = load_cache_list(cache_dir / "challenges.json")

    type_counts = Counter(card.get(TYPE_KEY) for card in cards)

    items, duplicate_item_names = index_by_unique_name(
        [
            convert_item(card)
            for card in cards
            if card.get(TYPE_KEY) == ITEM_TYPE
        ]
    )
    skills, duplicate_skill_names = index_by_unique_name(
        [
            convert_skill(card)
            for card in cards
            if card.get(TYPE_KEY) == SKILL_TYPE
        ]
    )
    encounters, duplicate_encounter_names = index_by_unique_name(
        [
            convert_encounter(card)
            for card in cards
            if str(card.get(TYPE_KEY, "")).startswith(ENCOUNTER_PREFIX)
        ]
    )
    packs = {
        converted["id"]: converted
        for pack in cardpacks
        for converted in [convert_card_pack(pack)]
        if converted["id"]
    }
    normalized_challenges = [
        convert_challenge(challenge)
        for challenge in challenges
    ]

    metadata = {
        "cache_dir": str(cache_dir),
        "raw_card_count": len(cards),
        "type_counts": dict(type_counts),
        "item_count": len(items),
        "skill_count": len(skills),
        "encounter_count": len(encounters),
        "card_pack_count": len(packs),
        "challenge_count": len(normalized_challenges),
        "duplicate_item_names": sorted(set(duplicate_item_names)),
        "duplicate_skill_names": sorted(set(duplicate_skill_names)),
        "duplicate_encounter_names": sorted(set(duplicate_encounter_names)),
    }

    outputs = {
        "cards_generated.json": items,
        "skills_generated.json": skills,
        "encounters_generated.json": encounters,
        "cardpacks.json": packs,
        "challenges.json": normalized_challenges,
        "cache_import_summary.json": metadata,
    }

    return outputs, metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import The Bazaar official cache JSON.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    outputs, metadata = build_outputs(args.cache_dir)

    print("Cache import summary:")
    for key, value in metadata.items():
        print(f"- {key}: {value}")

    if args.check_only:
        return

    for filename, payload in outputs.items():
        write_json(args.output_dir / filename, payload)
        print(f"Wrote {args.output_dir / filename}")


if __name__ == "__main__":
    main()
