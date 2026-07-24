from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import random
import time
from typing import Any

from combat_simulator import PlacedCard, card_label, targetable_cards
from monster_battle_evaluator import (
    DEFAULT_DURATION_SEC,
    MAX_DURATION_SEC,
    SandstormConfig,
    _build_side_input,
    _dedupe,
    _simulate_two_sided_battle,
    evaluate_monster_choices,
)


MAX_MONSTER_SIMULATIONS = 50
DEFAULT_MONSTER_SIMULATIONS = 10
MIN_DUMMY_DURATION_SEC = 0.0
DEFAULT_DUMMY_DURATION_SEC = 10.0
MAX_DUMMY_DURATION_SEC = 120.0
DEFAULT_DUMMY_HEALTH = 1_000_000_000
SIMULATION_CACHE_TTL_SEC = 10.0
SIMULATION_CACHE_MAX_ENTRIES = 32
SIMULATION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


class BattleSimulationInputError(ValueError):
    def __init__(self, code: str, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.field = field

    def to_response(self) -> dict[str, Any]:
        response = {"ok": False, "error": self.code, "message": str(self)}
        if self.field:
            response["field"] = self.field
        return response


@dataclass(frozen=True)
class DummyConfig:
    duration_seconds: float = DEFAULT_DUMMY_DURATION_SEC
    dummy_max_health: float = DEFAULT_DUMMY_HEALTH
    dummy_initial_shield: float = 0.0


def simulate_selected_monster(
    *,
    data: dict[str, Any],
    player_payload: dict[str, Any],
    event_option_id: Any = None,
    monster_id: Any = None,
    simulation_count: Any = DEFAULT_MONSTER_SIMULATIONS,
    duration_sec: Any = DEFAULT_DURATION_SEC,
    seed: Any = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    count = _bounded_int(
        simulation_count,
        default=DEFAULT_MONSTER_SIMULATIONS,
        minimum=1,
        maximum=MAX_MONSTER_SIMULATIONS,
        field="simulation_count",
    )
    horizon = _bounded_float(
        duration_sec,
        default=DEFAULT_DURATION_SEC,
        minimum=1.0,
        maximum=MAX_DURATION_SEC,
        field="duration_sec",
    )
    selected = resolve_selected_monster(
        player_payload.get("monster_choices") or [],
        event_option_id=event_option_id,
        monster_id=monster_id,
    )
    if selected is None:
        return {
            "ok": False,
            "available": False,
            "reason": "monster_data_not_found",
            "event_option_id": event_option_id,
            "monster_id": monster_id,
        }

    cache_key = _cache_key(
        "monster",
        data.get("data_version"),
        _stable_state(player_payload),
        _monster_identity(selected),
        count,
        horizon,
        seed,
    )
    cached = _cache_get(cache_key) if use_cache else None
    if cached is not None:
        return cached

    base_seed = _optional_int(seed)
    result = evaluate_monster_choices(
        data=data,
        player_state=player_payload,
        monster_choices=[selected],
        simulations=count,
        duration_sec=horizon,
        seed=base_seed if base_seed is not None else random.randint(1, 2_000_000_000),
        use_cache=False,
        sandstorm_config=SandstormConfig(),
    )
    monster_result = (result.get("results") or [{}])[0]
    response = {
        "ok": bool(monster_result),
        "available": bool(monster_result),
        "mode": "selected_monster",
        "event_option_id": event_option_id,
        "monster_id": monster_result.get("monster_id") or _monster_identity(selected),
        "monster_name": monster_result.get("monster_name") or selected.get("name"),
        "simulation_count": count,
        "duration_sec": horizon,
        "summary": _monster_summary(monster_result, count),
        "result": monster_result,
        "warnings": _dedupe([*result.get("warnings", []), *monster_result.get("warnings", [])]),
        "cache": {"hit": False, "key": cache_key},
    }
    _cache_put(cache_key, response)
    return response


def simulate_training_dummy(
    *,
    data: dict[str, Any],
    player_payload: dict[str, Any],
    duration_seconds: Any = DEFAULT_DUMMY_DURATION_SEC,
    dummy_max_health: Any = DEFAULT_DUMMY_HEALTH,
    dummy_initial_shield: Any = 0,
    seed: Any = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    config = DummyConfig(
        duration_seconds=_bounded_float(
            duration_seconds,
            default=DEFAULT_DUMMY_DURATION_SEC,
            minimum=MIN_DUMMY_DURATION_SEC,
            maximum=MAX_DUMMY_DURATION_SEC,
            field="duration_seconds",
        ),
        dummy_max_health=_bounded_float(
            dummy_max_health,
            default=DEFAULT_DUMMY_HEALTH,
            minimum=1.0,
            maximum=DEFAULT_DUMMY_HEALTH,
            field="dummy_max_health",
        ),
        dummy_initial_shield=_bounded_float(
            dummy_initial_shield,
            default=0.0,
            minimum=0.0,
            maximum=DEFAULT_DUMMY_HEALTH,
            field="dummy_initial_shield",
        ),
    )
    cache_key = _cache_key(
        "training_dummy",
        data.get("data_version"),
        _stable_state(player_payload),
        config,
        seed,
    )
    cached = _cache_get(cache_key) if use_cache else None
    if cached is not None:
        return cached

    player = _build_side_input(data, player_payload, side="player")
    warnings = list(player.warnings)
    if player.health is None:
        warnings.append("player_health_missing")
    if not targetable_cards(player.placements):
        warnings.append("player_board_missing")

    if player.health is None:
        response = {
            "ok": False,
            "available": False,
            "reason": "player_health_missing",
            "mode": "training_dummy",
            "warnings": _dedupe(warnings),
            "cache": {"hit": False, "key": cache_key},
        }
        _cache_put(cache_key, response)
        return response

    rng_seed = _optional_int(seed)
    rng = random.Random(rng_seed if rng_seed is not None else random.randint(1, 2_000_000_000)).random
    battle = _simulate_two_sided_battle(
        player_cards=player.placements,
        monster_cards=[],
        player_health=float(player.health),
        monster_health=config.dummy_max_health,
        duration_sec=config.duration_seconds,
        rng=rng,
        player_attributes=player.attributes,
        monster_attributes={},
        monster_initial_shield=config.dummy_initial_shield,
        sandstorm_config=SandstormConfig(enabled=False),
        card_catalog=data.get("cards", {}),
    )
    metrics = aggregate_training_dummy_metrics(
        battle.timeline,
        player_cards=player.placements,
        requested_duration=config.duration_seconds,
        actual_duration=battle.duration,
        dummy_max_health=config.dummy_max_health,
        dummy_remaining_health=battle.monster_remaining_health,
        dummy_remaining_shield=battle.monster_remaining_shield,
        player_remaining_shield=battle.player_remaining_shield,
        dummy_killed=battle.winner == "player",
    )
    response = {
        "ok": True,
        "available": True,
        "mode": "training_dummy",
        "config": {
            "duration_seconds": config.duration_seconds,
            "dummy_max_health": config.dummy_max_health,
            "dummy_initial_shield": config.dummy_initial_shield,
        },
        "summary": metrics,
        "battle": {
            "winner": battle.winner,
            "end_reason": battle.end_reason,
            "duration": battle.duration,
            "sample_timeline": battle.timeline[:80],
        },
        "warnings": _dedupe(
            [
                *warnings,
                "training_dummy_has_no_items_or_skills",
                "sandstorm_disabled_for_training_dummy",
            ]
        ),
        "cache": {"hit": False, "key": cache_key},
    }
    _cache_put(cache_key, response)
    return response


def resolve_selected_monster(
    monster_choices: list[Any],
    *,
    event_option_id: Any = None,
    monster_id: Any = None,
) -> dict[str, Any] | None:
    tokens = {
        str(value).strip().lower()
        for value in (event_option_id, monster_id)
        if str(value or "").strip()
    }
    candidates = [item for item in monster_choices if isinstance(item, dict)]
    if not tokens:
        return candidates[0] if len(candidates) == 1 else None
    for monster in candidates:
        if tokens & _monster_tokens(monster):
            return monster
    return None


def aggregate_training_dummy_metrics(
    timeline: list[dict[str, Any]],
    *,
    player_cards: list[PlacedCard],
    requested_duration: float,
    actual_duration: float,
    dummy_max_health: float,
    dummy_remaining_health: float,
    dummy_remaining_shield: float,
    player_remaining_shield: float,
    dummy_killed: bool,
) -> dict[str, Any]:
    card_names = {card.placement_id: card_label(card) for card in player_cards}
    card_metrics: dict[str, dict[str, Any]] = {
        card_label(card): {
            "card_name": card_label(card),
            "uses": 0,
            "damage": 0.0,
            "shield_generated": 0.0,
            "effective_heal": 0.0,
            "overheal": 0.0,
            "burn_applied": 0.0,
            "poison_applied": 0.0,
            "regen_applied": 0.0,
            "slow_duration": 0.0,
            "freeze_duration": 0.0,
            "haste_duration": 0.0,
            "charge_seconds": 0.0,
            "trigger_count": 0,
        }
        for card in player_cards
    }
    total_damage = 0.0
    direct_damage = 0.0
    burn_applied = 0.0
    poison_applied = 0.0
    burn_damage = 0.0
    poison_damage = 0.0
    shield_generated = 0.0
    requested_heal = 0.0
    effective_heal = 0.0
    overheal = 0.0
    regen_applied = 0.0
    slow_duration = 0.0
    freeze_duration = 0.0
    haste_duration = 0.0
    charge_seconds = 0.0
    card_uses = 0
    trigger_count = 0

    for event in timeline:
        if float(event.get("time") or 0.0) > actual_duration + 1e-6:
            continue
        kind = str(event.get("kind") or "")
        source = str(event.get("source") or "")
        value = float(event.get("value") or 0.0)
        source_metrics = card_metrics.setdefault(source, _empty_card_metrics(source)) if source else None
        if event.get("target_side") == "monster" and kind in {"damage", "use", "burn-tick", "poison-tick"}:
            total_damage += value
            if source_metrics is not None:
                source_metrics["damage"] += value
            if kind in {"damage", "use"}:
                direct_damage += value
            elif kind == "burn-tick":
                burn_damage += value
            elif kind == "poison-tick":
                poison_damage += value
        elif event.get("target_side") == "monster" and kind == "burn-apply":
            burn_applied += value
            if source_metrics is not None:
                source_metrics["burn_applied"] += value
        elif event.get("target_side") == "monster" and kind == "poison-apply":
            poison_applied += value
            if source_metrics is not None:
                source_metrics["poison_applied"] += value
        elif event.get("target_side") == "player" and kind == "regen-apply":
            regen_applied += value
            if source_metrics is not None:
                source_metrics["regen_applied"] += value
        elif kind in {"slow", "freeze", "haste"}:
            effective = float(event.get("effective_duration") or value)
            if kind == "slow":
                slow_duration += effective
                if source_metrics is not None:
                    source_metrics["slow_duration"] += effective
            elif kind == "freeze":
                freeze_duration += effective
                if source_metrics is not None:
                    source_metrics["freeze_duration"] += effective
            elif kind == "haste":
                haste_duration += effective
                if source_metrics is not None:
                    source_metrics["haste_duration"] += effective
        elif kind == "charge-resolved":
            charge_seconds += value
            if source_metrics is not None:
                source_metrics["charge_seconds"] += value
        elif event.get("target_side") == "player" and kind == "shield":
            shield_generated += value
            if source_metrics is not None:
                source_metrics["shield_generated"] += value
        elif event.get("target_side") == "player" and kind in {"heal", "regen-heal"}:
            actual = float(event.get("actual_heal") or value)
            effective_heal += actual
            if source_metrics is not None:
                source_metrics["effective_heal"] += actual
        elif event.get("target_side") == "player" and kind == "overheal":
            overheal += value
            if source_metrics is not None:
                source_metrics["overheal"] += value
        elif kind == "item-used" and event.get("side") == "player":
            card_uses += 1
            if source_metrics is not None:
                source_metrics["uses"] += 1
        elif kind.isupper() and event.get("side") == "player":
            trigger_count += 1
            if source_metrics is not None:
                source_metrics["trigger_count"] += 1

    requested_heal = effective_heal + overheal
    runtime = max(0.0, actual_duration)
    return {
        "requested_duration_seconds": requested_duration,
        "actual_duration_seconds": round(actual_duration, 6),
        "total_damage": round(total_damage, 3),
        "damage_per_second": round(total_damage / runtime, 3) if runtime > 0 else 0.0,
        "direct_damage": round(direct_damage, 3),
        "burn_applied": round(burn_applied, 3),
        "poison_applied": round(poison_applied, 3),
        "burn_damage": round(burn_damage, 3),
        "poison_damage": round(poison_damage, 3),
        "shield_generated": round(shield_generated, 3),
        "ending_player_shield": round(player_remaining_shield, 3),
        "requested_heal": round(requested_heal, 3),
        "effective_heal": round(effective_heal, 3),
        "overheal": round(overheal, 3),
        "regen_applied": round(regen_applied, 3),
        "slow_duration": round(slow_duration, 3),
        "freeze_duration": round(freeze_duration, 3),
        "haste_duration": round(haste_duration, 3),
        "charge_seconds": round(charge_seconds, 3),
        "card_uses": card_uses,
        "trigger_count": trigger_count,
        "dummy_remaining_health": round(dummy_remaining_health, 3),
        "dummy_remaining_shield": round(dummy_remaining_shield, 3),
        "dummy_killed": dummy_killed,
        "kill_time_seconds": round(actual_duration, 6) if dummy_killed else None,
        "card_metrics": [
            _round_card_metrics(metrics)
            for _, metrics in sorted(card_metrics.items(), key=lambda item: item[0])
            if metrics["uses"]
            or metrics["damage"]
            or metrics["shield_generated"]
            or metrics["effective_heal"]
            or metrics["overheal"]
            or metrics["burn_applied"]
            or metrics["poison_applied"]
            or metrics["regen_applied"]
            or metrics["slow_duration"]
            or metrics["freeze_duration"]
            or metrics["haste_duration"]
            or metrics["charge_seconds"]
            or metrics["trigger_count"]
            or metrics["card_name"] in card_names.values()
        ],
    }


def _monster_summary(result: dict[str, Any], requested: int) -> dict[str, Any]:
    completed = int(result.get("simulations_completed") or 0)
    wins = int(result.get("wins") or 0)
    unsupported_items = _unsupported_labels(result, card_type="item")
    unsupported_skills = _unsupported_labels(result, card_type="skill")
    unsupported_effects = _unsupported_effect_labels(result)
    warnings = _dedupe(list(result.get("warnings") or []))
    feedback_lines = _monster_feedback_lines(
        result,
        requested=requested,
        completed=completed,
        unsupported_items=unsupported_items,
        unsupported_skills=unsupported_skills,
        unsupported_effects=unsupported_effects,
        warnings=warnings,
    )
    return {
        "simulation_count": requested,
        "simulations_completed": completed,
        "wins": wins,
        "win_rate": round(wins / completed, 4) if completed else None,
        "status": result.get("status"),
        "confidence": result.get("confidence"),
        "monster_name": result.get("monster_name"),
        "monster_id": result.get("monster_id"),
        "average_battle_duration": result.get("average_battle_duration"),
        "average_remaining_health": result.get("average_remaining_health_on_win"),
        "minimum_remaining_health": _minimum_player_health(result.get("battle_log") or []),
        "average_damage_dealt": _average_from_last_log(result.get("battle_log") or [], "monster"),
        "average_damage_taken": _average_from_last_log(result.get("battle_log") or [], "player"),
        "timeouts": int(result.get("draws") or 0),
        "failures": int(result.get("simulations_failed") or 0),
        "unsupported_items": unsupported_items,
        "unsupported_skills": unsupported_skills,
        "unsupported_effects": unsupported_effects,
        "unsupported_item_count": len(unsupported_items),
        "unsupported_skill_count": len(unsupported_skills),
        "unsupported_effect_count": len(unsupported_effects),
        "player_cards": list(result.get("player_cards") or []),
        "monster_cards": list(result.get("monster_cards") or []),
        "player_card_details": list(result.get("player_card_details") or []),
        "monster_card_details": list(result.get("monster_card_details") or []),
        "warnings": warnings,
        "feedback_lines": feedback_lines,
        "feedback_available": True,
    }


def _monster_feedback_lines(
    result: dict[str, Any],
    *,
    requested: int,
    completed: int,
    unsupported_items: list[str],
    unsupported_skills: list[str],
    unsupported_effects: list[str],
    warnings: list[str],
) -> list[str]:
    lines = [
        "BazaarHelper battle simulation feedback",
        f"monster: {result.get('monster_name') or '-'}",
        f"monster_id: {result.get('monster_id') or '-'}",
        f"status: {result.get('status') or '-'}",
        f"confidence: {result.get('confidence') or '-'}",
        f"simulations: {completed}/{requested}",
        f"win_rate: {result.get('estimated_win_rate')}",
    ]
    if result.get("average_battle_duration") is not None:
        lines.append(f"average_duration: {result.get('average_battle_duration')}")
    if result.get("average_remaining_health_on_win") is not None:
        lines.append(f"average_remaining_health_on_win: {result.get('average_remaining_health_on_win')}")
    if result.get("simulations_failed"):
        lines.append(f"simulation_failures: {result.get('simulations_failed')}")
    lines.append("player_cards: " + _join_feedback_values(result.get("player_cards") or []))
    lines.append("monster_cards: " + _join_feedback_values(result.get("monster_cards") or []))
    lines.append("player_card_details: " + _feedback_json(result.get("player_card_details") or []))
    lines.append("monster_card_details: " + _feedback_json(result.get("monster_card_details") or []))
    if unsupported_items:
        lines.append("unsupported_items: " + _join_feedback_values(unsupported_items))
    if unsupported_skills:
        lines.append("unsupported_skills: " + _join_feedback_values(unsupported_skills))
    if unsupported_effects:
        lines.append("unsupported_effects: " + _join_feedback_values(unsupported_effects))
    if warnings:
        lines.append("warnings: " + _join_feedback_values(warnings))
    return lines


def _join_feedback_values(values: list[Any]) -> str:
    text = [str(value) for value in values if value]
    return " | ".join(text) if text else "-"


def _feedback_json(value: Any) -> str:
    if value in (None, "", [], {}):
        return "-"
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unsupported_labels(result: dict[str, Any], *, card_type: str) -> list[str]:
    labels: list[str] = []
    if card_type == "item":
        for entry in result.get("unsupported_cards") or []:
            if isinstance(entry, dict):
                labels.append(_unsupported_entry_name(entry, "card"))
    elif card_type == "skill":
        for entry in result.get("unsupported_skills") or []:
            if isinstance(entry, dict):
                labels.append(_unsupported_entry_name(entry, "skill"))

    for entry in result.get("unsupported_effects") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("card_type") or "").lower() != card_type:
            continue
        labels.append(_unsupported_entry_label(entry))
    return _dedupe(labels)


def _unsupported_effect_labels(result: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            _unsupported_entry_label(entry)
            for entry in result.get("unsupported_effects") or []
            if isinstance(entry, dict)
        ]
    )


def _unsupported_entry_label(entry: dict[str, Any]) -> str:
    name = _unsupported_entry_name(entry, "card")
    reason = str(entry.get("reason") or "unsupported")
    effect = str(entry.get("effect") or "")
    detail = reason if not effect else f"{reason}: {effect}"
    side = str(entry.get("side") or "")
    prefix = f"{side} " if side else ""
    return f"{prefix}{name} ({detail})"


def _unsupported_entry_name(entry: dict[str, Any], key: str) -> str:
    return str(entry.get(key) or entry.get("name") or entry.get("template_id") or entry.get("id") or "unknown")


def _minimum_player_health(timeline: list[dict[str, Any]]) -> float | None:
    values = [
        float(event.get("after_health"))
        for event in timeline
        if event.get("target_side") == "player" and event.get("after_health") is not None
    ]
    return round(min(values), 3) if values else None


def _average_from_last_log(timeline: list[dict[str, Any]], target_side: str) -> float | None:
    total = sum(
        float(event.get("value") or 0.0)
        for event in timeline
        if event.get("target_side") == target_side
        and str(event.get("kind") or "") in {"damage", "use", "burn-tick", "poison-tick"}
    )
    return round(total, 3) if timeline else None


def _empty_card_metrics(name: str) -> dict[str, Any]:
    return {
        "card_name": name,
        "uses": 0,
        "damage": 0.0,
        "shield_generated": 0.0,
        "effective_heal": 0.0,
        "overheal": 0.0,
        "burn_applied": 0.0,
        "poison_applied": 0.0,
        "regen_applied": 0.0,
        "slow_duration": 0.0,
        "freeze_duration": 0.0,
        "haste_duration": 0.0,
        "charge_seconds": 0.0,
        "trigger_count": 0,
    }


def _round_card_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        key: round(value, 3) if isinstance(value, float) else value
        for key, value in metrics.items()
    }


def _monster_identity(monster: dict[str, Any]) -> str:
    return str(
        monster.get("monster_id")
        or monster.get("id")
        or monster.get("source_id")
        or monster.get("template_id")
        or monster.get("name")
        or ""
    )


def _monster_tokens(monster: dict[str, Any]) -> set[str]:
    values: list[Any] = [
        monster.get("monster_id"),
        monster.get("id"),
        monster.get("source_id"),
        monster.get("template_id"),
        monster.get("internal_name"),
        monster.get("name"),
        monster.get("monster_name"),
    ]
    values.extend(monster.get("encounter_ids") or [])
    values.extend(monster.get("encounter_names") or [])
    for encounter in monster.get("encounters") or []:
        if isinstance(encounter, dict):
            values.extend(
                [
                    encounter.get("id"),
                    encounter.get("source_id"),
                    encounter.get("template_id"),
                    encounter.get("internal_name"),
                    encounter.get("name"),
                ]
            )
    return {str(value).strip().lower() for value in values if str(value or "").strip()}


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, field: str) -> int:
    number = _optional_int(value)
    if number is None:
        number = default
    if number < minimum or number > maximum:
        raise BattleSimulationInputError(
            "invalid_parameter",
            f"{field} must be between {minimum} and {maximum}.",
            field=field,
        )
    return number


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float, field: str) -> float:
    number = _optional_float(value)
    if number is None:
        number = default
    if number < minimum or number > maximum:
        raise BattleSimulationInputError(
            "invalid_parameter",
            f"{field} must be between {minimum:g} and {maximum:g}.",
            field=field,
        )
    return number


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BattleSimulationInputError(
            "invalid_parameter",
            "Expected an integer value.",
        ) from None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise BattleSimulationInputError(
            "invalid_parameter",
            "Expected a numeric value.",
        ) from None


def _stable_state(value: Any) -> Any:
    volatile = {
        "updated_at_utc",
        "captured_at_utc",
        "timestamp",
        "last_updated",
        "frame",
        "frame_count",
        "_runtime_state_age_seconds",
    }
    if isinstance(value, dict):
        return {
            str(key): _stable_state(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in volatile
        }
    if isinstance(value, list):
        return [_stable_state(item) for item in value]
    if isinstance(value, DummyConfig):
        return {
            "duration_seconds": value.duration_seconds,
            "dummy_max_health": value.dummy_max_health,
            "dummy_initial_shield": value.dummy_initial_shield,
        }
    return value


def _cache_key(*parts: Any) -> str:
    encoded = json.dumps(_stable_state(list(parts)), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _cache_get(key: str) -> dict[str, Any] | None:
    item = SIMULATION_CACHE.get(key)
    if item is None:
        return None
    created_at, payload = item
    if time.time() - created_at > SIMULATION_CACHE_TTL_SEC:
        SIMULATION_CACHE.pop(key, None)
        return None
    cached = json.loads(json.dumps(payload, ensure_ascii=False))
    cached["cache"] = {"hit": True, "key": key}
    return cached


def _cache_put(key: str, payload: dict[str, Any]) -> None:
    if len(SIMULATION_CACHE) >= SIMULATION_CACHE_MAX_ENTRIES:
        oldest = min(SIMULATION_CACHE.items(), key=lambda item: item[1][0])[0]
        SIMULATION_CACHE.pop(oldest, None)
    SIMULATION_CACHE[key] = (time.time(), json.loads(json.dumps(payload, ensure_ascii=False)))
