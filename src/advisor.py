from __future__ import annotations

from dataclasses import dataclass

from game_state import GameState
from recommender import RECOMMENDATION_RANK, analyze_event


@dataclass(frozen=True)
class AdvisorResult:
    state: GameState
    recommendations: list[dict]
    warnings: list[str]

    @property
    def best(self) -> dict | None:
        return self.recommendations[0] if self.recommendations else None


def analyze_game_state(data: dict, state: GameState, top: int | None = None) -> AdvisorResult:
    warnings = state.validate_against(data)
    unknown_events = [
        event_name
        for event_name in state.event_options
        if event_name not in data["events"]
    ]
    blocking_warnings = [
        warning
        for warning in warnings
        if not warning.startswith("未知事件：")
    ]
    if blocking_warnings:
        return AdvisorResult(state=state, recommendations=[], warnings=warnings)

    recommendations = []
    for event_name in state.event_options:
        event_data = data["events"].get(event_name) or build_missing_event(event_name)
        recommendations.append(
            analyze_event(
                event_name=event_name,
                event_data=event_data,
                cards=data["cards"],
                build_name=state.build,
                build_data=data["builds"][state.build],
                current_day=state.day,
                rarity_rules=data["rarity_rules"],
                current_hero=state.hero,
                owned_cards=state.owned_cards,
                owned_card_enchantments=state.owned_card_enchantments,
                all_builds=data["builds"],
                current_shop=state.effective_shop,
                current_gold=state.gold,
            )
        )

    recommendations.sort(
        key=lambda result: (
            RECOMMENDATION_RANK.get(result["recommendation"], 99),
            -result["pool_stats"]["expected_valuable_in_shop"],
            result["event_name"],
        )
    )

    return AdvisorResult(
        state=state,
        recommendations=recommendations[:top] if top else recommendations,
        warnings=unknown_event_warnings(unknown_events),
    )


def build_missing_event(event_name: str) -> dict:
    return {
        "name": event_name,
        "event_category": "missing_events",
        "event_type": "missing_event",
        "resource_rewards": {"gold": 0, "exp": 0, "health": 0},
        "notes": "这个事件还没有补充到事件数据中。",
    }


def unknown_event_warnings(event_names: list[str]) -> list[str]:
    if not event_names:
        return []
    return [
        "事件需要补充数据：" + ", ".join(event_names),
    ]
