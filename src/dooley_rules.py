from __future__ import annotations

from typing import Any, Iterable


DOOLEY_SHOP_CORE_CARDS = {"C.O.R.A", "Combat Core"}
DOOLEY_EVENT_ONLY_CORE_CARDS = {
    "Armored Core",
    "Critical Core",
    "Launcher Core",
    "The Core",
    "Weaponized Core",
}
DOOLEY_CORE_EVENT_EXPIRES_ON_DAY = 3


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _card_has_core_tag(card_data: dict[str, Any] | None) -> bool:
    if not isinstance(card_data, dict):
        return False
    return "core" in {_normalize(tag) for tag in card_data.get("tags", [])}


def _owned_names(owned_cards: Iterable[str] | dict[str, Any] | None) -> set[str]:
    if isinstance(owned_cards, dict):
        return {str(name) for name in owned_cards if name}
    return {str(name) for name in owned_cards or [] if name}


def dooley_missing_unobtainable_core_cards(
    *,
    hero: str | None,
    current_day: int,
    core_cards: Iterable[str],
    owned_cards: Iterable[str] | dict[str, Any] | None,
    cards: dict[str, Any] | None = None,
) -> list[str]:
    if _normalize(hero) != "dooley" or current_day < DOOLEY_CORE_EVENT_EXPIRES_ON_DAY:
        return []

    owned = _owned_names(owned_cards)
    missing: list[str] = []
    for card_name in core_cards:
        name = str(card_name or "")
        if not name or name in owned or name in DOOLEY_SHOP_CORE_CARDS:
            continue
        card_data = cards.get(name) if isinstance(cards, dict) else None
        if name in DOOLEY_EVENT_ONLY_CORE_CARDS or _card_has_core_tag(card_data):
            missing.append(name)

    return missing


def dooley_build_is_blocked_by_missing_core(
    *,
    hero: str | None,
    current_day: int,
    build_data: dict[str, Any],
    owned_cards: Iterable[str] | dict[str, Any] | None,
    cards: dict[str, Any] | None = None,
) -> bool:
    return bool(
        dooley_missing_unobtainable_core_cards(
            hero=hero,
            current_day=current_day,
            core_cards=build_data.get("core_cards", []),
            owned_cards=owned_cards,
            cards=cards,
        )
    )
