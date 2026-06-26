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
    health: int | None = None
    source: str = "manual"

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

        return cls(
            hero=str(payload["hero"]),
            build=str(payload["build"]),
            day=int(payload["day"]),
            event_options=[str(name) for name in payload.get("event_options", [])],
            owned_cards={str(name): str(rarity).lower() for name, rarity in owned_cards.items()},
            owned_card_enchantments=owned_card_enchantments,
            visible_cards=[str(name) for name in visible_cards if name],
            gold=_optional_int(payload.get("gold")),
            health=_optional_int(payload.get("health")),
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


def _event_available_for_hero(event_data: dict[str, Any], hero: str) -> bool:
    event_heroes = event_data.get("event_heroes", [])
    if not event_heroes:
        return True
    return hero in event_heroes or "Common" in event_heroes
