from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal

from combat_simulator import (
    CombatSummary,
    build_current_board_placements,
    damage_timeline,
    first_time_to_damage,
    simulate_combat,
)


ChangeOperation = Literal["add", "remove", "replace"]


@dataclass(frozen=True)
class CandidateChange:
    operation: ChangeOperation
    card: dict[str, Any] | None = None
    match: dict[str, Any] | None = None


@dataclass(frozen=True)
class EnemyState:
    health: float | None = None


@dataclass
class BuildEvaluation:
    baseline: dict[str, Any]
    changed: dict[str, Any]
    delta: dict[str, Any]
    changes: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    statistics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "changed": self.changed,
            "delta": self.delta,
            "changes": self.changes,
            "warnings": self.warnings,
            "statistics": self.statistics,
        }


def evaluate_build(
    data: dict[str, Any],
    current_items: list[dict[str, Any]],
    current_skills: list[dict[str, Any]] | None = None,
    candidate_changes: list[CandidateChange | dict[str, Any]] | None = None,
    enemy_state: EnemyState | dict[str, Any] | None = None,
    simulation_count: int = 1,
    duration_sec: float = 20.0,
) -> dict[str, Any]:
    """Compare current board output with the board after candidate changes."""

    warnings: list[str] = []
    changes = [normalize_change(change) for change in candidate_changes or []]
    enemy = normalize_enemy(enemy_state)
    baseline_entries = normalize_item_entries(current_items)
    changed_entries = apply_candidate_changes(baseline_entries, changes, warnings)

    baseline_summary, baseline_skipped = simulate_entries(
        data,
        baseline_entries,
        duration_sec=duration_sec,
        simulation_count=simulation_count,
    )
    changed_summary, changed_skipped = simulate_entries(
        data,
        changed_entries,
        duration_sec=duration_sec,
        simulation_count=simulation_count,
    )

    if current_skills:
        warnings.append("skills_are_not_simulated_yet")
    if baseline_skipped:
        warnings.append("baseline_cards_skipped")
    if changed_skipped:
        warnings.append("changed_cards_skipped")

    baseline_metrics = summarize_combat(
        baseline_summary,
        enemy,
        duration_sec=duration_sec,
        skipped_cards=baseline_skipped,
    )
    changed_metrics = summarize_combat(
        changed_summary,
        enemy,
        duration_sec=duration_sec,
        skipped_cards=changed_skipped,
    )
    return BuildEvaluation(
        baseline=baseline_metrics,
        changed=changed_metrics,
        delta=delta_metrics(baseline_metrics, changed_metrics),
        changes=[change_to_dict(change) for change in changes],
        warnings=warnings,
        statistics={
            "simulation_count": max(1, int(simulation_count or 1)),
            "duration_sec": duration_sec,
            "enemy_state": enemy_to_dict(enemy),
        },
    ).to_dict()


def simulate_entries(
    data: dict[str, Any],
    entries: list[dict[str, Any]],
    *,
    duration_sec: float,
    simulation_count: int,
) -> tuple[CombatSummary, list[dict[str, Any]]]:
    placed, skipped = build_current_board_placements(data, {"board_items": entries})
    summary = simulate_combat(
        placed,
        duration_sec=duration_sec,
        random_trials=max(1, int(simulation_count or 1)),
    )
    return summary, skipped


def apply_candidate_changes(
    current_items: list[dict[str, Any]],
    changes: list[CandidateChange],
    warnings: list[str],
) -> list[dict[str, Any]]:
    result = [deepcopy(item) for item in current_items]
    for change in changes:
        if change.operation == "add":
            if not change.card:
                warnings.append("add_change_missing_card")
                continue
            result.append(normalize_added_card(change.card, len(result)))
        elif change.operation == "remove":
            before = len(result)
            result = [
                item
                for item in result
                if not entry_matches(item, change.match or change.card or {})
            ]
            if len(result) == before:
                warnings.append("remove_change_matched_no_cards")
        elif change.operation == "replace":
            if not change.card:
                warnings.append("replace_change_missing_card")
                continue
            if not change.match:
                warnings.append("replace_change_missing_match")
                continue

            match_indexes = [
                index
                for index, item in enumerate(result)
                if entry_matches(item, change.match)
            ]
            if not match_indexes:
                warnings.append("replace_change_matched_no_cards")
                continue
            if len(match_indexes) > 1:
                warnings.append("replace_change_matched_multiple_cards")

            replace_index = match_indexes[0]
            result[replace_index] = normalize_replacement_card(
                change.card,
                result[replace_index],
                replace_index,
            )
    return result


def summarize_combat(
    summary: CombatSummary,
    enemy: EnemyState,
    *,
    duration_sec: float,
    skipped_cards: list[dict[str, Any]],
) -> dict[str, Any]:
    timeline = damage_timeline(summary)
    battle_time = (
        first_time_to_damage(timeline, enemy.health)
        if enemy.health is not None and enemy.health > 0
        else None
    )
    win = (
        summary.total_damage >= enemy.health
        if enemy.health is not None and enemy.health > 0
        else None
    )
    return {
        "win_rate": 1.0 if win is True else 0.0 if win is False else None,
        "battle_time_sec": battle_time,
        "total_damage": summary.total_damage,
        "average_damage": summary.total_damage,
        "total_uses": summary.total_uses,
        "total_shield": summary.total_shield,
        "total_heal": summary.total_heal,
        "total_burn_applied": summary.total_burn_applied,
        "total_poison_applied": summary.total_poison_applied,
        "total_burn_tick_damage": summary.total_burn_tick_damage,
        "total_poison_tick_damage": summary.total_poison_tick_damage,
        "damage_per_second": summary.total_damage / duration_sec if duration_sec > 0 else 0.0,
        "by_card_uses": summary.by_card,
        "by_card_damage": summary.by_card_damage,
        "cumulative_damage_by_second": summary.cumulative_damage_by_second,
        "timeline": timeline[:80],
        "skipped_cards": skipped_cards,
    }


def delta_metrics(baseline: dict[str, Any], changed: dict[str, Any]) -> dict[str, Any]:
    base_time = baseline.get("battle_time_sec")
    changed_time = changed.get("battle_time_sec")
    return {
        "total_damage": numeric_delta(baseline, changed, "total_damage"),
        "average_damage": numeric_delta(baseline, changed, "average_damage"),
        "damage_per_second": numeric_delta(baseline, changed, "damage_per_second"),
        "total_uses": numeric_delta(baseline, changed, "total_uses"),
        "total_shield": numeric_delta(baseline, changed, "total_shield"),
        "total_heal": numeric_delta(baseline, changed, "total_heal"),
        "total_burn_applied": numeric_delta(baseline, changed, "total_burn_applied"),
        "total_poison_applied": numeric_delta(baseline, changed, "total_poison_applied"),
        "battle_time_sec": (
            float(changed_time) - float(base_time)
            if base_time is not None and changed_time is not None
            else None
        ),
        "win_rate": numeric_delta(baseline, changed, "win_rate"),
        "by_card_damage": map_delta(
            baseline.get("by_card_damage", {}),
            changed.get("by_card_damage", {}),
        ),
        "by_card_uses": map_delta(
            baseline.get("by_card_uses", {}),
            changed.get("by_card_uses", {}),
        ),
    }


def normalize_item_entries(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in items or [] if isinstance(item, dict)]


def normalize_change(change: CandidateChange | dict[str, Any]) -> CandidateChange:
    if isinstance(change, CandidateChange):
        return change
    operation = str(change.get("operation") or change.get("op") or "").lower()
    if operation not in {"add", "remove", "replace"}:
        operation = "add"
    raw_card = change.get("card")
    raw_match = change.get("match")
    if operation == "add" and raw_card is None:
        raw_card = change
    if operation == "remove" and raw_match is None:
        raw_match = change
    if operation == "replace" and raw_match is None:
        raw_match = change.get("replace") or change.get("remove") or change
    return CandidateChange(
        operation=operation,  # type: ignore[arg-type]
        card=dict(raw_card) if isinstance(raw_card, dict) else None,
        match=dict(raw_match) if isinstance(raw_match, dict) else None,
    )


def normalize_enemy(enemy_state: EnemyState | dict[str, Any] | None) -> EnemyState:
    if isinstance(enemy_state, EnemyState):
        return enemy_state
    if not isinstance(enemy_state, dict):
        return EnemyState()
    health = enemy_state.get("health")
    try:
        return EnemyState(health=float(health) if health is not None else None)
    except (TypeError, ValueError):
        return EnemyState()


def normalize_added_card(card: dict[str, Any], index: int) -> dict[str, Any]:
    entry = dict(card)
    entry.setdefault("id", f"candidate_{index}")
    entry.setdefault("section", "Hand")
    entry.setdefault("card_type", "Item")
    entry.setdefault("rarity", entry.get("tier") or "Bronze")
    return entry


def normalize_replacement_card(
    card: dict[str, Any],
    replaced_item: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    entry = normalize_added_card(card, index)
    for key in (
        "section",
        "slot",
        "position",
        "x",
        "y",
        "row",
        "column",
        "board_index",
        "index",
        "location",
        "container",
    ):
        if key in replaced_item and key not in entry:
            entry[key] = replaced_item[key]
    return entry


def entry_matches(entry: dict[str, Any], match: dict[str, Any]) -> bool:
    if not match:
        return False
    for key in ("id", "template_id", "source_id", "name", "internal_name"):
        expected = match.get(key)
        if expected is not None and str(entry.get(key, "")).lower() == str(expected).lower():
            return True
    return False


def numeric_delta(
    baseline: dict[str, Any],
    changed: dict[str, Any],
    key: str,
) -> float | None:
    base = baseline.get(key)
    new = changed.get(key)
    if base is None or new is None:
        return None
    return float(new) - float(base)


def map_delta(
    baseline: dict[str, Any],
    changed: dict[str, Any],
) -> dict[str, float]:
    keys = set(baseline) | set(changed)
    return {
        key: float(changed.get(key, 0.0) or 0.0)
        - float(baseline.get(key, 0.0) or 0.0)
        for key in sorted(keys)
    }


def change_to_dict(change: CandidateChange) -> dict[str, Any]:
    return {
        "operation": change.operation,
        "card": change.card,
        "match": change.match,
    }


def enemy_to_dict(enemy: EnemyState) -> dict[str, Any]:
    return {"health": enemy.health}
