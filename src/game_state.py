from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OwnedCard:
    name: str
    rarity: str


@dataclass(frozen=True)
class GameState:
    hero: str
    build: str
    day: int
    event_options: list[str]
    owned_cards: dict[str, str] = field(default_factory=dict)
    owned_card_enchantments: dict[str, list[str]] = field(default_factory=dict)
    visible_cards: list[str] = field(default_factory=list)
    gold: int | None = None
    combat_health: int | None = None
    monster_health: int | None = None
    prestige: int | None = None
    max_prestige: int | None = None
    income: int | None = None
    level: int | None = None
    xp: int | None = None
    owned_items: list[dict[str, Any]] | None = None
    board_items: list[dict[str, Any]] | None = None
    stash_items: list[dict[str, Any]] | None = None
    skills: list[dict[str, Any]] | None = None
    monster_items: list[dict[str, Any]] | None = None
    monster_skills: list[dict[str, Any]] | None = None
    monster_choices: list[dict[str, Any]] | None = None
    current_events: list[dict[str, Any]] | None = None
    current_shop: dict[str, Any] | None = None
    effective_shop: dict[str, Any] | None = None
    current_reward_options: list[dict[str, Any]] | None = None
    inventory_slots_used: int | None = None
    inventory_slots_total: int | None = None
    source: str = "manual"

    @property
    def health(self) -> int | None:
        return self.combat_health

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "GameState":
        owned_cards = payload.get("owned_cards", {})
        owned_card_enchantments: dict[str, list[str]] = {}
        if isinstance(owned_cards, list):
            for item in owned_cards:
                name = item.get("name")
                if not name:
                    continue

                enchantments = item.get("enchantments", [])
                if item.get("enchantment"):
                    enchantments = [item["enchantment"], *enchantments]
                owned_card_enchantments[str(name)] = [
                    str(enchantment)
                    for enchantment in enchantments
                    if enchantment
                ]

            owned_cards = {
                item["name"]: item["rarity"]
                for item in owned_cards
                if item.get("name") and item.get("rarity")
            }

        visible_cards = payload.get("visible_cards", [])
        if isinstance(visible_cards, list):
            visible_cards = [
                item.get("name") if isinstance(item, dict) else item
                for item in visible_cards
            ]

        raw_entries = payload.get("owned_cards")
        entries = (
            [dict(item) for item in raw_entries if isinstance(item, dict)]
            if isinstance(raw_entries, list)
            else None
        )
        current_shop = payload.get("current_shop")
        if not isinstance(current_shop, dict):
            current_shop = None
        effective_shop = payload.get("effective_shop")
        if not isinstance(effective_shop, dict):
            effective_shop = current_shop

        return cls(
            hero=str(payload["hero"]),
            build=str(payload["build"]),
            day=int(payload["day"]),
            event_options=[str(name) for name in payload.get("event_options", [])],
            owned_cards={str(name): str(rarity).lower() for name, rarity in owned_cards.items()},
            owned_card_enchantments=owned_card_enchantments,
            visible_cards=[str(name) for name in visible_cards if name],
            gold=_optional_int(payload.get("gold")),
            combat_health=_optional_int(payload.get("combat_health", payload.get("health"))),
            monster_health=_optional_int(payload.get("monster_health")),
            prestige=_optional_int(payload.get("prestige")),
            max_prestige=_optional_int(payload.get("max_prestige")),
            income=_optional_int(payload.get("income")),
            level=_optional_int(payload.get("level")),
            xp=_optional_int(payload.get("xp")),
            owned_items=_optional_list(payload.get("owned_items", _items(entries))),
            board_items=_optional_list(payload.get("board_items", _by_section(entries, {"hand", "board"}))),
            stash_items=_optional_list(payload.get("stash_items", _by_section(entries, {"stash"}))),
            skills=_optional_list(payload.get("skills", _by_type(entries, "skill"))),
            monster_items=_optional_list(payload.get("monster_items")),
            monster_skills=_optional_list(payload.get("monster_skills")),
            monster_choices=_optional_list(payload.get("monster_choices")),
            current_events=_optional_list(payload.get("current_events")),
            current_shop=dict(current_shop) if current_shop is not None else None,
            effective_shop=dict(effective_shop) if effective_shop is not None else None,
            current_reward_options=_optional_list(payload.get("current_reward_options")),
            inventory_slots_used=_optional_int(payload.get("inventory_slots_used")),
            inventory_slots_total=_optional_int(payload.get("inventory_slots_total")),
            source=str(payload.get("source", "manual")),
        )

    def validate_against(self, data: dict[str, Any]) -> list[str]:
        errors: list[str] = []

        if self.build not in data["builds"]:
            errors.append(f"未知阵容：{self.build}")

        valid_heroes = {
            hero
            for card in data["cards"].values()
            for hero in card.get("heroes", [])
        }
        if self.hero not in valid_heroes:
            errors.append(f"未知英雄：{self.hero}")

        if self.build in data["builds"]:
            build_hero = data["builds"][self.build].get("hero")
            if build_hero and build_hero != self.hero:
                errors.append(
                    f"阵容 {self.build} 属于 {build_hero}，不适用于 {self.hero}。"
                )

        if self.day <= 0:
            errors.append("天数必须是正整数。")

        unknown_events = [
            event_name
            for event_name in self.event_options
            if event_name not in data["events"]
        ]
        if unknown_events:
            errors.append(f"未知事件：{', '.join(unknown_events)}")

        unavailable_events = [
            event_name
            for event_name in self.event_options
            if event_name in data["events"]
            and not _event_available_for_hero(data["events"][event_name], self.hero)
        ]
        if unavailable_events:
            errors.append(
                f"{self.hero} 无法遇到这些事件：{', '.join(unavailable_events)}"
            )

        unknown_owned_cards = [
            card_name
            for card_name in self.owned_cards
            if card_name not in data["cards"]
        ]
        if unknown_owned_cards:
            errors.append(f"未知已拥有卡牌：{', '.join(unknown_owned_cards)}")

        return errors


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_list(value: Any) -> list[dict[str, Any]] | None:
    if value is None or not isinstance(value, list):
        return None
    return [dict(item) for item in value if isinstance(item, dict)]


def _by_section(entries: list[dict[str, Any]] | None, sections: set[str]) -> list[dict[str, Any]] | None:
    if entries is None:
        return None
    return [
        item for item in entries
        if str(item.get("card_type", "item")).lower() != "skill"
        and str(item.get("section", "")).lower() in sections
    ]


def _by_type(entries: list[dict[str, Any]] | None, card_type: str) -> list[dict[str, Any]] | None:
    if entries is None:
        return None
    return [
        item for item in entries
        if str(item.get("card_type", "")).lower() == card_type.lower()
    ]


def _items(entries: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if entries is None:
        return None
    return [
        item for item in entries
        if str(item.get("card_type", "item")).lower() != "skill"
    ]


def _event_available_for_hero(event_data: dict[str, Any], hero: str) -> bool:
    event_heroes = event_data.get("event_heroes", [])
    if not event_heroes:
        return True
    return hero in event_heroes or "Common" in event_heroes
