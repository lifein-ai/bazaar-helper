from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

TAG_ALIASES = {
    "ammo": "ammo",
    "apparel": "apparel",
    "aquatic": "aquatic",
    "burn": "burn",
    "cooldown": "cooldown",
    "damage": "damage",
    "economic": "gold",
    "economy": "gold",
    "enchanted": None,
    "food": "food",
    "foods": "food",
    "freeze": "freeze",
    "friend": "friend",
    "haste": "haste",
    "heal": "heal",
    "health": "health",
    "monster": None,
    "non-weapon": None,
    "poison": "poison",
    "potion": "potion",
    "potions": "potion",
    "property": "property",
    "reagent": "reagent",
    "regen": "regen",
    "relic": "relic",
    "relics": "relic",
    "shield": "shield",
    "skill": None,
    "skills": None,
    "slow": "slow",
    "sports equipment": "weapon",
    "tech": "tech",
    "tool": "tool",
    "tools": "tool",
    "toy": "toy",
    "toys": "toy",
    "vehicle": "vehicle",
    "vehicles": "vehicle",
    "weapon": "weapon",
    "weapons": "weapon",
}

REWARD_CARD_ALIASES = {
    "gunpowder": ["Gunpowder"],
    "cinders": ["Cinders"],
    "cinder": ["Cinders"],
    "extract": ["Extract"],
    "medkit": ["Med Kit"],
    "med kit": ["Med Kit"],
    "spare change": ["Spare Change"],
    "scrap": ["Scrap"],
    "sharpening stone": ["Sharpening Stone"],
    "chocolate bars": ["Chocolate Bar"],
    "chocolate bar": ["Chocolate Bar"],
    "gumballs": ["Blue Gumball", "Green Gumball", "Red Gumball", "Yellow Gumball"],
    "gumball": ["Blue Gumball", "Green Gumball", "Red Gumball", "Yellow Gumball"],
}

RARITY_ALIASES = {
    "bronze": "bronze",
    "silver": "silver",
    "gold": "gold",
    "diamond": "diamond",
}

HERO_NAMES = {"Vanessa", "Pygmalien", "Dooley", "Mak", "Jules", "Stelle", "Karnok"}
SELECTABLE_CACHE_TYPES = {"TCardEncounterEvent", "TCardEncounterPedestal"}
STEP_CACHE_TYPE = "TCardEncounterStep"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def clean_name(name: str) -> str:
    return re.sub(r"\s+<[0-9a-f]{8}>$", "", name).strip()


def normalized_words(description: str) -> list[str]:
    text = description.lower()
    text = text.replace("non-weapon", "non-weapon")
    text = re.sub(r"[^a-z0-9+\- ]+", " ", text)
    return [word for word in text.split() if word]


def tags_from_description(description: str) -> list[str]:
    text = description.lower()

    if "non-weapon" in text:
        return []

    tags: list[str] = []

    for phrase, tag in TAG_ALIASES.items():
        if tag is None:
            continue
        if phrase in text:
            tags.append(tag)

    return unique(tags)


def sizes_from_description(description: str) -> list[str]:
    text = description.lower()
    sizes = []

    for size in ["small", "medium", "large"]:
        if re.search(rf"\b{size}\b", text):
            sizes.append(size)

    return sizes


def rarity_filter_from_description(description: str) -> dict[str, str] | None:
    text = description.lower()
    if "gold or diamond" in text or "gold-tier or diamond-tier" in text:
        return {"min": "gold", "max": "diamond"}
    if "silver or lower" in text:
        return {"min": "bronze", "max": "silver"}
    for rarity, normalized in RARITY_ALIASES.items():
        if re.search(rf"\b{rarity}[- ]tier\b", text):
            return {"min": normalized, "max": normalized}
    return None


def rarity_word_from_description(description: str) -> str | None:
    text = description.lower()
    for rarity, normalized in RARITY_ALIASES.items():
        if re.search(rf"\b{rarity}(?:[- ]tier)?\b", text):
            return normalized
    return None


def hero_filter_from_event(name: str, description: str) -> str | None:
    if "purchase an item from this hero" in description.lower() and name in HERO_NAMES:
        return name
    if "neutral items" in description.lower():
        return "Common"
    return None


def hero_scope_from_description(description: str, hero_filter: str | None) -> str:
    if hero_filter:
        return "fixed"
    if "from any hero" in description.lower():
        return "any"
    return "current"


def event_heroes_from_event(event: dict[str, Any]) -> list[str]:
    return unique(event.get("heroes") or [])


def excluded_tags_from_description(description: str) -> list[str]:
    excluded = ["legendary"]
    if "non-weapon" in description.lower():
        excluded.append("weapon")
    return excluded


def unique(values: list[str]) -> list[str]:
    result = []
    seen = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def is_item_merchant(event: dict[str, Any]) -> bool:
    description = event.get("description") or ""
    if not str(event.get("cache_type", "")).startswith("TCardEncounter"):
        return False
    if "merchant" in event.get("tags", []):
        return True
    if description.lower().startswith("visit one of") and "merchant" in description.lower():
        return True
    return description.startswith("Sells ") and "skill" not in description.lower()


def build_shop(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    rarity_filter = rarity_filter_from_description(description)
    hero_filter = hero_filter_from_event(event_name, description)
    hero_scope = hero_scope_from_description(description, hero_filter)
    reward_tags = tags_from_description(description)
    size_filter = sizes_from_description(description)

    if hero_filter:
        reward_tags = []

    shop_pool: dict[str, Any] = {
        "reward_tags": reward_tags,
        "match_mode": "any",
        "rarity_filter": rarity_filter,
        "rarity_rule": None if rarity_filter else "normal_shop_by_day",
        "excluded_tags": excluded_tags_from_description(description),
        "hero_scope": hero_scope,
    }

    if size_filter:
        shop_pool["size_filter"] = size_filter

    if hero_filter:
        shop_pool["hero_filter"] = hero_filter

    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "shop_type": infer_shop_type(description, reward_tags, size_filter, hero_filter, rarity_filter),
        "hero_filter": hero_filter,
        "shop_pool": shop_pool,
        "notes": description,
    }


def infer_shop_type(
    description: str,
    reward_tags: list[str],
    size_filter: list[str],
    hero_filter: str | None,
    rarity_filter: dict[str, str] | None,
) -> str:
    if hero_filter:
        return "hero"
    if rarity_filter:
        return rarity_filter["min"]
    if size_filter:
        return "+".join(size_filter)
    if reward_tags:
        return "+".join(reward_tags)
    return "normal"


def is_improve_item_event(event: dict[str, Any]) -> bool:
    description = event.get("description") or ""
    return (
        str(event.get("cache_type", "")).startswith("TCardEncounter")
        and description.lower().startswith("improve your ")
    )


def is_upgrade_item_event(event: dict[str, Any]) -> bool:
    description = (event.get("description") or "").lower()
    return (
        str(event.get("cache_type", "")).startswith("TCardEncounter")
        and (
            description.startswith("upgrade")
            and (
                "item" in description
                or "weapon" in description
                or "ammo" in description
                or "burn" in description
                or "heal" in description
                or "regen" in description
                or "shield" in description
                or "poison" in description
            )
            or "enchance your offensive items" in description
            or description.startswith("transform your items")
        )
    )


def is_skill_event(event: dict[str, Any]) -> bool:
    description = (event.get("description") or "").lower()
    if not str(event.get("cache_type", "")).startswith("TCardEncounter"):
        return False
    return (
        "sells skills" in description
        or "sells monster skills" in description
        or "sells skill upgrades" in description
        or "teaches" in description
        or "starter skill" in description
        or "choose an skill" in description
        or "get a skill" in description
        or "gain a " in description and " skill" in description
        or "gain an " in description and " skill" in description
    )


def is_item_reward_event(event: dict[str, Any]) -> bool:
    description = (event.get("description") or "").lower()
    if not str(event.get("cache_type", "")).startswith("TCardEncounter"):
        return False
    reward_words = [
        "item",
        "weapon",
        "property",
        "friend",
        "potion",
        "core",
        "loot",
        "aquatic",
        "ammo",
        "burn",
        "chest",
        "medkit",
        "med kit",
        "poison",
        "reagent",
        "shield",
        "spare change",
        "freeze",
        "haste",
        "slow",
        "tool",
        "gunpowder",
        "cinders",
        "extract",
        "scrap",
        "sharpening stone",
        "chocolate",
        "gumball",
    ]
    reward_starters = [
        "get ",
        "gain an item",
        "gain a small",
        "gain a medium",
        "gain a random",
        "gain a weapon",
        "choose ",
        "dig for ",
        "you find an abandoned item",
        "you find an abandoned chest",
        "you found ",
        "you easily take ",
        "take one of their items",
    ]
    has_reward_starter = any(description.startswith(starter) for starter in reward_starters)
    if description.startswith("(if ") and " get " in description:
        has_reward_starter = True

    return has_reward_starter and any(
        word in description for word in reward_words
    )


def is_resource_event(event: dict[str, Any]) -> bool:
    description = (event.get("description") or "").lower()
    if not str(event.get("cache_type", "")).startswith("TCardEncounter"):
        return False
    resource_words = [
        "blacksmith",
        "coin",
        "customers",
        "experience",
        "financial",
        "gold",
        "health",
        "income",
        "max health",
        "merchant",
        "opportunities",
        "seminar",
        "regen",
        "speed",
        "stats",
        "temporary",
        "toughness",
        "xp",
    ]
    resource_starters = [
        "attend ",
        "boost ",
        "dare to risk",
        "buy experience",
        "eat at ",
        "gain ",
        "get ",
        "make a pitch",
        "look for opportunities",
        "lose ",
        "study ",
        "the blacksmith",
        "the merchant",
        "on your way out",
        "you learned",
        "the wallet was full",
        "trade health",
        "work ",
        "you find an abandoned cache",
    ]
    return any(description.startswith(starter) for starter in resource_starters) and any(
        word in description
        for word in resource_words
    )


def is_enchant_event(event: dict[str, Any]) -> bool:
    description = (event.get("description") or "").lower()
    return str(event.get("cache_type", "")).startswith("TCardEncounter") and "enchant" in description


def is_combat_event(event: dict[str, Any]) -> bool:
    description = (event.get("description") or "").lower()
    return str(event.get("cache_type", "")).startswith("TCardEncounter") and (
        "fight" in description
        or "monster attacks" in description
        or "agents have found you" in description
        or "monster returns" in description
        or "test your build against" in description
        or "try to take your money" in description
    )


def is_utility_event(event: dict[str, Any]) -> bool:
    description = (event.get("description") or "").lower()
    if not str(event.get("cache_type", "")).startswith("TCardEncounter"):
        return False
    utility_phrases = [
        "abandoned ruins",
        "aid a caravan",
        "adventurer you helped",
        "aid the convoys",
        "an echo of the past",
        "choose a starting package",
        "collected here",
        "claim your 3 wishes",
        "enter the thieves guild",
        "explore the exotic wilds",
        "glimpse of what the future holds",
        "journey begins here",
        "journey into the depths",
        "mysterious creature",
        "next, choose",
        "patron",
        "see all that the bazaar has to offer",
        "see if the merchant is alright",
        "spin the wheel",
        "take a chance",
        "thanks for the investment",
        "this is not the end",
        "travel almost anywhere",
        "wants to thank you",
        "you find a strange mushroom",
    ]
    return any(phrase in description for phrase in utility_phrases)


def build_item_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    effect = "improve_items"
    lowered = description.lower()
    if lowered.startswith("upgrade"):
        effect = "upgrade_items"
    elif lowered.startswith("transform"):
        effect = "transform_items"
    elif "enchance your offensive items" in lowered:
        effect = "enhance_offensive_items"

    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "item_event",
        "effect": effect,
        "target_tags": tags_from_description(description),
        "match_mode": "any",
        "rarity_filter": None,
        "rarity_rule": None,
        "resource_rewards": {"gold": 0, "exp": 0, "health": 0},
        "notes": description,
    }


def reward_card_names_from_description(description: str) -> list[str]:
    text = description.lower()
    names: list[str] = []
    for phrase, card_names in REWARD_CARD_ALIASES.items():
        if phrase in text:
            names.extend(card_names)
    return unique(names)


def build_item_reward_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    rarity_filter = rarity_filter_from_description(description)
    size_filter = sizes_from_description(description)
    reward_tags = tags_from_description(description)
    exact_names = reward_card_names_from_description(description)

    card_reward: dict[str, Any] = {
        "enabled": True,
        "exact_names": exact_names,
        "reward_tags": reward_tags,
        "match_mode": "any",
        "rarity_filter": rarity_filter,
        "rarity_rule": None if rarity_filter else "normal_shop_by_day",
        "excluded_tags": excluded_tags_from_description(description),
        "hero_scope": hero_scope_from_description(description, None),
    }
    if size_filter:
        card_reward["size_filter"] = size_filter

    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "item_reward",
        "exact_names": exact_names,
        "reward_tags": reward_tags,
        "card_reward": card_reward,
        "resource_rewards": {"gold": 0, "exp": 0, "health": 0},
        "notes": description,
    }


def resource_types_from_description(description: str) -> list[str]:
    text = description.lower()
    result = []
    if "coin" in text:
        result.append("gold")
    if "gold" in text:
        result.append("gold")
    if "xp" in text or "experience" in text or "seminar" in text:
        result.append("exp")
    if "health" in text or "tasty treats" in text or "temporary boost" in text:
        result.append("health")
    if "income" in text or "financial boost" in text or "opportunities" in text:
        result.append("income")
    if "power" in text:
        result.append("power")
    if "regen" in text:
        result.append("regen")
    if "toughness" in text:
        result.append("toughness")
    if "speed" in text:
        result.append("speed")
    return unique(result)


def build_resource_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    resource_types = resource_types_from_description(description)
    resource_rewards = {resource_type: 1 for resource_type in resource_types}
    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "resource_event",
        "resource_types": resource_types,
        "resource_rewards": resource_rewards,
        "notes": description,
    }


def enchantment_tags_from_description(description: str) -> list[str]:
    text = description.lower()
    mapping = {
        "burn": "burn",
        "fiery": "burn",
        "flame": "burn",
        "poison": "poison",
        "toxic": "poison",
        "freeze": "freeze",
        "icy": "freeze",
        "shield": "shield",
        "shielded": "shield",
        "heal": "heal",
        "restorative": "heal",
        "haste": "haste",
        "turbo": "haste",
        "slow": "slow",
        "crit": "crit",
        "deadly": "crit",
        "damage": "damage",
        "shiny": "value",
        "golden": "gold",
    }
    return unique([tag for phrase, tag in mapping.items() if phrase in text])


def build_enchant_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "enchant_event",
        "effect": "enchant_items",
        "target_tags": tags_from_description(description),
        "enchantment_tags": enchantment_tags_from_description(description),
        "match_mode": "any",
        "resource_rewards": {"gold": 0, "exp": 0, "health": 0},
        "notes": description,
    }


def build_skill_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    skill_tags = tags_from_description(description)
    rarity_filter = rarity_filter_from_description(description)
    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "skill_event",
        "skill_tags": skill_tags,
        "shop_pool": {
            "reward_tags": skill_tags,
            "match_mode": "any",
            "rarity_filter": rarity_filter,
            "rarity_rule": None if rarity_filter else "normal_shop_by_day",
            "excluded_tags": ["legendary"],
            "hero_scope": "current",
        },
        "resource_rewards": {"gold": 0, "exp": 0, "health": 0},
        "notes": description,
    }


def build_combat_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "combat_event",
        "resource_rewards": {"gold": 0, "exp": 0, "health": 0},
        "notes": description,
    }


def build_utility_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "utility_event",
        "resource_rewards": {},
        "notes": description,
    }


def build_unknown_event(event_name: str, event: dict[str, Any]) -> dict[str, Any]:
    description = event.get("description") or ""
    return {
        "name": event_name,
        "source_id": event.get("id"),
        "source_ids": [event.get("id")] if event.get("id") else [],
        "event_heroes": event_heroes_from_event(event),
        "event_type": "unknown_event",
        "resource_rewards": {"gold": 0, "exp": 0, "health": 0},
        "notes": description,
    }


def classify_event(event_name: str, event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if is_item_merchant(event):
        return "shops", build_shop(event_name, event)

    if is_improve_item_event(event):
        return "item_events", build_item_event(event_name, event)

    if is_upgrade_item_event(event):
        return "item_events", build_item_event(event_name, event)

    if is_item_reward_event(event):
        return "item_rewards", build_item_reward_event(event_name, event)

    if is_enchant_event(event):
        return "enchant_events", build_enchant_event(event_name, event)

    if is_skill_event(event):
        return "skill_shops", build_skill_event(event_name, event)

    if is_resource_event(event):
        return "resource_events", build_resource_event(event_name, event)

    if is_combat_event(event):
        return "combat_events", build_combat_event(event_name, event)

    if is_utility_event(event):
        return "utility_events", build_utility_event(event_name, event)

    if event.get("description"):
        return "unknown_events", build_unknown_event(event_name, event)

    return None


def option_separator_matches(parent: str, child: str) -> bool:
    if not parent or len(parent) < 4:
        return False

    return (
        child.startswith(f"{parent} - ")
        or child.startswith(f"{parent} (")
    )


def step_matches_event(step: dict[str, Any], event_name: str, event: dict[str, Any]) -> bool:
    step_internal = step.get("internal_name") or ""
    step_name = step.get("name") or ""
    event_internal = event.get("internal_name") or ""
    candidates = unique([event_internal, event_name])

    for candidate in candidates:
        if option_separator_matches(candidate, step_internal):
            return True
        if option_separator_matches(candidate, step_name):
            return True

    return False


def build_followup_options(
    event_name: str,
    event: dict[str, Any],
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for step in steps:
        if not step_matches_event(step, event_name, event):
            continue

        option_name = clean_name(step.get("name") or step.get("internal_name") or "")
        if not option_name:
            continue

        classified = classify_event(option_name, step)
        if not classified:
            continue

        category_name, option = classified
        option_id = option.get("source_id") or step.get("id") or option_name
        if option_id in seen_ids:
            continue
        seen_ids.add(option_id)
        options.append(
            {
                **option,
                "event_category": category_name,
                "parent_event": event_name,
                "source_internal_name": step.get("internal_name"),
            }
        )

    return sorted(options, key=lambda item: item["name"])


def attach_followup_options(
    categories: dict[str, list[dict[str, Any]]],
    followups_by_name: dict[str, list[dict[str, Any]]],
) -> None:
    for category_events in categories.values():
        for event in category_events:
            options = followups_by_name.get(event["name"], [])
            if options:
                event["followup_options"] = options


def merge_event(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    source_ids = unique(existing.get("source_ids", []) + incoming.get("source_ids", []))
    event_heroes = unique(existing.get("event_heroes", []) + incoming.get("event_heroes", []))

    merged = {**existing}
    merged["source_ids"] = source_ids
    merged["event_heroes"] = event_heroes

    return merged


def build_events(encounters: dict[str, Any]) -> dict[str, Any]:
    shops_by_name: dict[str, dict[str, Any]] = {}
    skill_events_by_name: dict[str, dict[str, Any]] = {}
    item_rewards_by_name: dict[str, dict[str, Any]] = {}
    item_events_by_name: dict[str, dict[str, Any]] = {}
    resource_events_by_name: dict[str, dict[str, Any]] = {}
    enchant_events_by_name: dict[str, dict[str, Any]] = {}
    combat_events_by_name: dict[str, dict[str, Any]] = {}
    utility_events_by_name: dict[str, dict[str, Any]] = {}
    unknown_events_by_name: dict[str, dict[str, Any]] = {}
    steps = [
        event
        for event in encounters.values()
        if event.get("cache_type") == STEP_CACHE_TYPE
    ]
    followups_by_name: dict[str, list[dict[str, Any]]] = {}

    def add_event(target: dict[str, dict[str, Any]], name: str, incoming: dict[str, Any]) -> None:
        if name in target:
            target[name] = merge_event(target[name], incoming)
        else:
            target[name] = incoming

    for raw_name, event in encounters.items():
        if event.get("cache_type") not in SELECTABLE_CACHE_TYPES:
            continue

        name = clean_name(raw_name)
        if name.startswith("[") and name.endswith("]"):
            continue

        classified = classify_event(name, event)
        if classified:
            category_name, event_data = classified
            targets = {
                "shops": shops_by_name,
                "skill_shops": skill_events_by_name,
                "item_rewards": item_rewards_by_name,
                "item_events": item_events_by_name,
                "resource_events": resource_events_by_name,
                "enchant_events": enchant_events_by_name,
                "combat_events": combat_events_by_name,
                "utility_events": utility_events_by_name,
                "unknown_events": unknown_events_by_name,
            }
            add_event(targets[category_name], name, event_data)

        followup_options = build_followup_options(name, event, steps)
        if followup_options:
            followups_by_name[name] = followup_options

    categories = {
        "shops": sorted(shops_by_name.values(), key=lambda item: item["name"]),
        "skill_shops": sorted(skill_events_by_name.values(), key=lambda item: item["name"]),
        "item_rewards": sorted(item_rewards_by_name.values(), key=lambda item: item["name"]),
        "item_events": sorted(item_events_by_name.values(), key=lambda item: item["name"]),
        "resource_events": sorted(resource_events_by_name.values(), key=lambda item: item["name"]),
        "enchant_events": sorted(enchant_events_by_name.values(), key=lambda item: item["name"]),
        "combat_events": sorted(combat_events_by_name.values(), key=lambda item: item["name"]),
        "utility_events": sorted(utility_events_by_name.values(), key=lambda item: item["name"]),
        "unknown_events": sorted(unknown_events_by_name.values(), key=lambda item: item["name"]),
    }
    attach_followup_options(categories, followups_by_name)
    return categories


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build data/events.json from official encounter data.")
    parser.add_argument("--encounters", type=Path, default=DATA_DIR / "encounters_generated.json")
    parser.add_argument("--output", type=Path, default=DATA_DIR / "events.json")
    parser.add_argument("--check-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    encounters = load_json(args.encounters)
    events = build_events(encounters)

    print("Event build summary:")
    for category_name, category_events in events.items():
        print(f"- {category_name}: {len(category_events)}")
    print(f"- output: {args.output}")

    if args.check_only:
        for category_name, category_events in events.items():
            print(f"\n{category_name}:")
            for event in category_events[:20]:
                print(f"  {event['name']} -> {event['notes']}")
        return

    write_json(args.output, events)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
