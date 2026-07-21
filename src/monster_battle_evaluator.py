from __future__ import annotations

from dataclasses import dataclass, field, replace
import heapq
import hashlib
import json
import math
import random
import time
from typing import Any, Callable

from combat_simulator import (
    CHARGE_PORT_ICD_SEC,
    CombatSummary,
    CooldownModifyConfig,
    DEFAULT_BURN_CONFIG,
    DEFAULT_COOLDOWN_MODIFY_CONFIG,
    DEFAULT_HEAL_CLEANSE_CONFIG,
    DEFAULT_OVERHEAL_CONFIG,
    BurnConfig,
    HealCleanseConfig,
    ItemCooldownState,
    ITEM_USE_ICD_SEC,
    ItemTimingConfig,
    OverhealConfig,
    PlacedCard,
    ChargePortState,
    action_performed_key,
    apply_attribute_operation,
    build_current_board_placements,
    card_attr_with_bonus,
    card_label,
    clone_placed_cards,
    compare_number,
    crit_multiplier_for_casts,
    damage_timeline,
    effective_tier,
    effect_rows,
    expand_action_nodes,
    get_card_ammo_max,
    get_card_cooldown_sec,
    get_attr_value_by_tier,
    get_field,
    is_skill_card,
    item_timing_config,
    matches_card,
    normalize_tag,
    normalize_offense_attr,
    pick_x_most,
    read_rules,
    set_attr_value_by_tier,
    simulate_combat,
    status_cleanse_amount,
    targetable_cards,
    type_name,
)


SUPPORTED_RULE_ACTIONS = {
    "TActionAnd",
    "TActionCardCharge",
    "TActionCardHaste",
    "TActionCardSlow",
    "TActionCardForceUse",
    "TActionCardReload",
    "TActionCardFreeze",
    "TActionCardFlyingStart",
    "TActionCardFlyingStop",
    "TActionCardFlyingToggle",
    "TActionCardAddTagsList",
    "TActionCardAddTagsRandom",
    "TActionCardAddTagsBySource",
    "TActionPlayerShieldApply",
    "TActionPlayerHealApply",
    "TActionPlayerHeal",
    "TActionPlayerPoisonApply",
    "TActionPlayerRegenApply",
    "TActionPlayerRageApply",
    "TActionPlayerModifyAttribute",
    "TActionPlayerReviveHeal",
    "TActionCardModifyAttribute",
    "TAuraActionCardModifyAttribute",
    "TAuraActionPlayerModifyAttribute",
}
SUPPORTED_ON_USE_ACTIONS = {
    "TActionPlayerDamage",
    "TActionPlayerBurnApply",
    "TActionPlayerPoisonApply",
    "TActionPlayerHeal",
    "TActionPlayerHealApply",
    "TActionPlayerRegenApply",
}
SUPPORTED_ACTIONS = SUPPORTED_RULE_ACTIONS | SUPPORTED_ON_USE_ACTIONS
TWO_SIDED_RULE_ACTIONS = {
    "TActionAnd",
    "TActionCardCharge",
    "TActionCardHaste",
    "TActionCardSlow",
    "TActionCardForceUse",
    "TActionCardReload",
    "TActionCardFreeze",
    "TActionCardFlyingStart",
    "TActionCardFlyingStop",
    "TActionCardFlyingToggle",
    "TActionCardAddTagsList",
    "TActionCardAddTagsRandom",
    "TActionCardAddTagsBySource",
    "TActionPlayerDamage",
    "TActionPlayerBurnApply",
    "TActionPlayerShieldApply",
    "TActionPlayerHealApply",
    "TActionPlayerHeal",
    "TActionPlayerPoisonApply",
    "TActionPlayerRegenApply",
    "TActionPlayerRageApply",
    "TActionPlayerModifyAttribute",
    "TActionCardModifyAttribute",
}
AMOUNTLESS_RULE_ACTIONS = {
    "TActionCardFlyingStart",
    "TActionCardFlyingStop",
    "TActionCardFlyingToggle",
    "TActionCardAddTagsList",
    "TActionCardAddTagsBySource",
}
RUNTIME_CARD_STATES = {"flying", "heated", "chilled"}
RUNTIME_SIDE_STATES = {"enraged"}
RUNTIME_STATE_ATTRS = RUNTIME_CARD_STATES | RUNTIME_SIDE_STATES
BATTLE_SIDE_CONDITION_ATTRS = {
    "shield",
    "burn",
    "burning",
    "poison",
    "poisoned",
    "health",
    "healthpercent",
    "belowhalfhealth",
    "rage",
}
RUNTIME_AURA_ATTRS = {
    "DamageAmount",
    "BurnApplyAmount",
    "PoisonApplyAmount",
    "ShieldApplyAmount",
    "HealAmount",
    "RegenApplyAmount",
    "RageApplyAmount",
    "CritChance",
    "Lifesteal",
    "Multicast",
    "AmmoMax",
    "ChargeAmount",
    "SlowAmount",
    "FreezeAmount",
    "HasteAmount",
    "FlatCooldownReduction",
    "PercentCooldownReduction",
    "PercentFreezeReduction",
    "PercentSlowReduction",
    "PercentHasteReduction",
}
TIMELINE_AURA_ATTRS = {
    "Multicast",
    "AmmoMax",
    "ChargeAmount",
    "SlowAmount",
    "FreezeAmount",
    "HasteAmount",
}
COOLDOWN_AURA_ATTRS = {"CooldownMax", "Cooldown", "FlatCooldownReduction", "PercentCooldownReduction"}
STATUS_DURATION_REDUCTION_ATTRS = {
    "freeze": "PercentFreezeReduction",
    "slow": "PercentSlowReduction",
    "haste": "PercentHasteReduction",
}
MAX_TRIGGER_DEPTH = 8
SUPPORTED_TRIGGER_TOKENS = {
    "itemused",
    "cardfired",
    "cardstartedflying",
    "cardstoppedflying",
    "playerattributechanged",
    "playerenraged",
    "playerenrageended",
    "performedslow",
    "performedhaste",
    "performedfreeze",
    "performedflying",
    "performedrage",
    "performedburn",
    "performedpoison",
    "performeddamage",
    "performedshield",
    "performedheal",
    "performedregen",
    "performedreload",
    "performeddestruction",
    "cardcritted",
    "playerdied",
    "fightstarted",
}
DEFAULT_SIMULATIONS = 10
DEFAULT_DURATION_SEC = 60.0
MAX_SIMULATIONS = 100
MAX_DURATION_SEC = 180.0
EVENT_PRIORITY = {
    "STATUS_TICK": 10,
    "COOLDOWN_READY": 20,
    "CHARGE_TRIGGERED": 30,
    "CHARGE_RESOLVED": 40,
    "COOLDOWN_MODIFIER_EXPIRED": 45,
    "ITEM_USE_REQUESTED": 50,
    "MULTICAST_REQUESTED": 55,
    "ITEM_USED": 60,
    "HEAL_RESOLVED": 70,
    "BURN_CLEANSED": 71,
    "POISON_CLEANSED": 72,
    "DEATH_CHECK": 90,
}


MonsterEvaluatorCache: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class SideInput:
    health: float | None
    placements: list[PlacedCard]
    skipped_cards: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    warnings: list[str]
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeState:
    state_type: str
    value: float = 1.0
    source_id: str = ""
    start_time: float = 0.0
    expire_time: float = math.inf
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BattleSide:
    name: str
    cards: list[PlacedCard]
    health: float
    max_health: float
    base_max_health: float = 0.0
    shield: float = 0.0
    burn_stack: float = 0.0
    poison_stack: float = 0.0
    regen_stack: float = 0.0
    damage_reduction: list[dict[str, float]] = field(default_factory=list)
    revives: list[dict[str, Any]] = field(default_factory=list)
    timeline: list[dict[str, Any]] = field(default_factory=list)
    uses: dict[str, float] = field(default_factory=dict)
    bonus: dict[str, dict[str, float]] = field(default_factory=dict)
    cooldowns: dict[str, ItemCooldownState] = field(default_factory=dict)
    haste_until: dict[str, float] = field(default_factory=dict)
    slow_until: dict[str, float] = field(default_factory=dict)
    freeze_until: dict[str, float] = field(default_factory=dict)
    ammo: dict[str, dict[str, float]] = field(default_factory=dict)
    active: list[PlacedCard] = field(default_factory=list)
    trigger_counts: dict[Any, int] = field(default_factory=dict)
    condition_state: dict[str, bool] = field(default_factory=dict)
    max_health_modifiers: list[dict[str, Any]] = field(default_factory=list)
    runtime_states: dict[str, list[RuntimeState]] = field(default_factory=dict)
    item_runtime_states: dict[str, dict[str, list[RuntimeState]]] = field(default_factory=dict)
    runtime_aura_bonus: dict[str, dict[str, float]] = field(default_factory=dict)
    runtime_tags_by_source: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    rage: float = 0.0
    rage_max: float = 100.0
    rage_gained_total: float = 0.0
    enraged_duration_sec: float = 5.0
    is_enraged: bool = False


@dataclass(frozen=True)
class BattleCardRef:
    side: BattleSide
    card: PlacedCard


@dataclass(frozen=True)
class TriggerCounterKey:
    owner_id: str
    ability_id: str
    trigger_id: str
    source_item_id: str | None = None
    target_item_id: str | None = None


@dataclass(order=True)
class ScheduledBattleEvent:
    execute_time: float
    event_priority: int
    board_position: int
    sequence_id: int
    kind: str = field(compare=False)
    ref: BattleCardRef | None = field(default=None, compare=False)
    amount: float = field(default=0.0, compare=False)
    source_side: BattleSide | None = field(default=None, compare=False)
    source: PlacedCard | None = field(default=None, compare=False)
    port_id: str = field(default="", compare=False)
    modifier_id: str = field(default="", compare=False)
    requested_time: float = field(default=0.0, compare=False)
    reason: str = field(default="", compare=False)
    cast_index: int = field(default=1, compare=False)
    forced: bool = field(default=False, compare=False)


class BattleEventScheduler:
    def __init__(self, timeline: list[dict[str, Any]]) -> None:
        self.timeline = timeline
        self.events: list[ScheduledBattleEvent] = []
        self.sequence_id = 0
        self.charge_ports: dict[str, ChargePortState] = {}

    def next_time(self) -> float:
        return self.events[0].execute_time if self.events else math.inf

    def push(self, event: ScheduledBattleEvent) -> None:
        heapq.heappush(self.events, event)

    def pop_due(self, now: float, epsilon: float = 1e-6) -> ScheduledBattleEvent | None:
        if not self.events or self.events[0].execute_time > now + epsilon:
            return None
        return heapq.heappop(self.events)

    def request_item_use(
        self,
        ref: BattleCardRef,
        requested_time: float,
        *,
        reason: str,
        cast_index: int = 1,
        forced: bool = False,
    ) -> float:
        state = ref.side.cooldowns.get(ref.card.placement_id)
        if state is None:
            return math.inf
        timing = item_timing_config(ref.card)
        execute_time = max(float(requested_time), state.next_use_available_time)
        delayed_by = max(0.0, execute_time - float(requested_time))
        state.next_use_available_time = execute_time + timing.use_icd
        event = ScheduledBattleEvent(
            execute_time=execute_time,
            event_priority=EVENT_PRIORITY["ITEM_USED"],
            board_position=_event_board_position(ref),
            sequence_id=self._next_sequence(),
            kind="ITEM_USED",
            ref=ref,
            requested_time=float(requested_time),
            reason=reason,
            cast_index=cast_index,
            forced=forced,
        )
        self.push(event)
        self.timeline.append(
            _battle_event(
                requested_time,
                ref.side.name,
                ref.side.name,
                "item-use-requested",
                card_label(ref.card),
                0.0,
                target=card_label(ref.card),
                requested_time=round(float(requested_time), 6),
                execute_time=round(execute_time, 6),
                delayed_by_item_icd=round(delayed_by, 6),
                use_icd=round(timing.use_icd, 6),
                reason=reason,
                cast_index=cast_index,
                forced=forced,
            )
        )
        return execute_time

    def trigger_charge(
        self,
        target: BattleCardRef,
        trigger_time: float,
        *,
        source_side: BattleSide,
        source: PlacedCard,
        port_id: str,
        amount: float,
    ) -> float:
        port = self.charge_ports.setdefault(port_id, ChargePortState(port_id=port_id))
        execute_time = max(float(trigger_time), port.next_available_time)
        delayed_by = max(0.0, execute_time - float(trigger_time))
        port.next_available_time = execute_time + CHARGE_PORT_ICD_SEC
        event = ScheduledBattleEvent(
            execute_time=execute_time,
            event_priority=EVENT_PRIORITY["CHARGE_RESOLVED"],
            board_position=_event_board_position(target),
            sequence_id=self._next_sequence(),
            kind="CHARGE_RESOLVED",
            ref=target,
            amount=amount,
            source_side=source_side,
            source=source,
            port_id=port_id,
            requested_time=float(trigger_time),
        )
        self.push(event)
        self.timeline.append(
            _battle_event(
                trigger_time,
                source_side.name,
                target.side.name,
                "charge-port-triggered",
                card_label(source),
                amount,
                target=card_label(target.card),
                trigger_time=round(float(trigger_time), 6),
                execute_time=round(execute_time, 6),
                delayed_by_charge_icd=round(delayed_by, 6),
                port_id=port_id,
            )
        )
        return execute_time

    def schedule_cooldown_modifier_expiry(
        self,
        target: BattleCardRef,
        execute_time: float,
        *,
        source_side: BattleSide,
        source: PlacedCard,
        modifier_id: str,
    ) -> None:
        self.push(
            ScheduledBattleEvent(
                execute_time=float(execute_time),
                event_priority=EVENT_PRIORITY["COOLDOWN_MODIFIER_EXPIRED"],
                board_position=_event_board_position(target),
                sequence_id=self._next_sequence(),
                kind="COOLDOWN_MODIFIER_EXPIRED",
                ref=target,
                source_side=source_side,
                source=source,
                modifier_id=modifier_id,
            )
        )

    def _next_sequence(self) -> int:
        self.sequence_id += 1
        return self.sequence_id


@dataclass
class TwoSidedBattleOutcome:
    winner: str
    duration: float
    player_remaining_health: float
    monster_remaining_health: float
    player_damage: float
    monster_damage: float
    timeline: list[dict[str, Any]]


def _event_board_position(ref: BattleCardRef) -> int:
    side_offset = 0 if ref.side.name == "player" else 10000
    return side_offset + int(ref.card.start or 0)


def evaluate_monster_choices(
    *,
    data: dict[str, Any],
    player_state: Any,
    monster_choices: list[dict[str, Any]],
    simulations: int = DEFAULT_SIMULATIONS,
    duration_sec: float = DEFAULT_DURATION_SEC,
    seed: int | None = 0,
    use_cache: bool = True,
    simulate_fn: Callable[..., CombatSummary] = simulate_combat,
) -> dict[str, Any]:
    """Evaluate monster choices with the two-sided battle simulator.

    The default path runs both sides on one shared timeline. A custom
    ``simulate_fn`` still uses the older damage-race path for test injection
    and backwards-compatible diagnostics.
    """

    simulation_count = max(1, min(MAX_SIMULATIONS, int(simulations or DEFAULT_SIMULATIONS)))
    horizon = max(1.0, min(MAX_DURATION_SEC, float(duration_sec or DEFAULT_DURATION_SEC)))
    cache_key = _evaluation_cache_key(
        data=data,
        player_state=player_state,
        monster_choices=monster_choices,
        simulations=simulation_count,
        duration_sec=horizon,
        seed=seed,
    )
    if use_cache and cache_key in MonsterEvaluatorCache:
        cached = json.loads(json.dumps(MonsterEvaluatorCache[cache_key], ensure_ascii=False))
        cached["cache"] = {"hit": True, "key": cache_key}
        return cached

    started = time.perf_counter()
    player = _build_side_input(data, player_state, side="player")
    results = [
        _evaluate_single_monster(
            data=data,
            player=player,
            monster_choice=monster,
            simulations=simulation_count,
            duration_sec=horizon,
            seed=seed,
            simulate_fn=simulate_fn,
        )
        for monster in monster_choices
        if isinstance(monster, dict)
    ]
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    response = {
        "status": "ok" if results else "unsupported",
        "scheme": "B",
        "simulator_model": "two_sided_timeline" if simulate_fn is simulate_combat else "one_sided_damage_race",
        "simulations_requested": simulation_count,
        "duration_sec": horizon,
        "results": results,
        "performance": {
            "elapsed_ms": round(elapsed_ms, 3),
            "average_ms_per_choice": round(elapsed_ms / len(results), 3) if results else None,
        },
        "cache": {"hit": False, "key": cache_key},
        "warnings": _dedupe(
            [
                (
                    "current_simulator_is_bounded_two_sided_timeline"
                    if simulate_fn is simulate_combat
                    else "current_simulator_is_damage_race_approximation"
                ),
                *player.warnings,
            ]
        ),
    }
    if use_cache:
        MonsterEvaluatorCache[cache_key] = json.loads(
            json.dumps({**response, "cache": {"hit": False, "key": cache_key}}, ensure_ascii=False)
        )
    return response


def clear_monster_evaluation_cache() -> None:
    MonsterEvaluatorCache.clear()


def _simulate_two_sided_battle(
    *,
    player_cards: list[PlacedCard],
    monster_cards: list[PlacedCard],
    player_health: float,
    monster_health: float,
    duration_sec: float,
    rng: Callable[[], float] | None,
    player_attributes: dict[str, Any] | None = None,
    monster_attributes: dict[str, Any] | None = None,
) -> TwoSidedBattleOutcome:
    player_cards = clone_placed_cards(player_cards)
    monster_cards = clone_placed_cards(monster_cards)
    _apply_two_sided_static_card_auras(player_cards, monster_cards, rng)
    player = _make_battle_side("player", player_cards, player_health, duration_sec, attributes=player_attributes)
    monster = _make_battle_side("monster", monster_cards, monster_health, duration_sec, attributes=monster_attributes)
    sides = [player, monster]
    rules_by_source = {
        card.placement_id: read_rules(card, TWO_SIDED_RULE_ACTIONS)
        for side in sides
        for card in side.cards
    }
    timeline: list[dict[str, Any]] = []
    scheduler = BattleEventScheduler(timeline)
    _initialize_runtime_state_auras(player, monster, timeline, rng, duration_sec)
    _refresh_runtime_state_auras(player, monster, scheduler, 0.0, timeline, rng)
    _run_fight_started_actions(player, monster, rules_by_source, scheduler, timeline, rng, duration_sec)
    _refresh_runtime_state_auras(player, monster, scheduler, 0.0, timeline, rng)
    if _battle_winner(player, monster):
        return _battle_outcome(player, monster, 0.0, timeline)

    now = 0.0
    next_burn_tick = 0.5
    next_second_tick = 1.0
    guard = 0
    epsilon = 1e-6
    while now < duration_sec and guard < 2400:
        guard += 1
        card_time = _next_card_ready_time(sides, now)
        event_time = scheduler.next_time()
        tick_time = min(next_burn_tick, next_second_tick)
        state_time = _next_runtime_state_expiry(sides, now)
        next_time = min(card_time, event_time, tick_time, state_time)
        if not math.isfinite(next_time) or next_time > duration_sec + epsilon:
            break
        elapsed = max(0.0, next_time - now)
        if elapsed > 0:
            _advance_cooldowns(sides, now, elapsed)
        now = next_time
        for transition in _expire_runtime_states_for_sides(player, monster, now, timeline):
            event_card = transition.get("card")
            if isinstance(event_card, PlacedCard):
                _run_state_triggered_rules(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    event_side=transition["side"],
                    event_card=event_card,
                    performed=transition["performed"],
                    trigger_depth=0,
                )
        _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)

        if now + epsilon >= next_burn_tick:
            _process_burn_tick(player, monster, now, timeline)
            next_burn_tick += 0.5
        if now + epsilon >= next_second_tick:
            _process_second_tick(player, monster, now, timeline)
            next_second_tick += 1.0
        _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
        if _battle_winner(player, monster):
            break

        for ref in sorted(_ready_cards(sides, epsilon), key=_event_board_position):
            _activate_ready_card(ref, now, scheduler, timeline, reason="cooldown")

        event_guard = 0
        while event_guard < 720 and not _battle_winner(player, monster):
            event_guard += 1
            event = scheduler.pop_due(now, epsilon)
            if event is None:
                break
            if event.kind == "CHARGE_RESOLVED":
                _resolve_charge_event(event, scheduler, now, timeline)
            elif event.kind == "COOLDOWN_MODIFIER_EXPIRED":
                _expire_cooldown_modifier(event, scheduler, now, timeline)
            elif event.kind == "ITEM_USED" and event.ref is not None:
                _fire_battle_card(
                    event.ref,
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    forced=event.forced,
                    cast_index=event.cast_index,
                    requested_time=event.requested_time,
                )
            _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
    winner_time = min(now, duration_sec)
    return _battle_outcome(player, monster, winner_time, timeline)


def _make_battle_side(
    name: str,
    cards: list[PlacedCard],
    health: float,
    duration_sec: float,
    *,
    attributes: dict[str, Any] | None = None,
) -> BattleSide:
    active = [card for card in targetable_cards(cards) if get_card_cooldown_sec(card, cards) > 0]
    attrs = attributes or {}
    side = BattleSide(
        name=name,
        cards=cards,
        health=health,
        max_health=health,
        base_max_health=health,
        active=active,
        rage_max=max(1.0, _numeric_attr(attrs, "RageMax", default=100.0)),
        enraged_duration_sec=_duration_attr_sec(attrs, "EnragedDurationMax", default=5.0),
        uses={card.placement_id: 0.0 for card in active},
        cooldowns={
            card.placement_id: ItemCooldownState(
                base_cooldown=get_card_cooldown_sec(card, cards),
                remaining_cooldown=get_card_cooldown_sec(card, cards),
            )
            for card in active
        },
        haste_until={card.placement_id: 0.0 for card in active},
        slow_until={card.placement_id: 0.0 for card in active},
        freeze_until={card.placement_id: 0.0 for card in active},
        ammo={
            card.placement_id: {
                "base_max": float(get_card_ammo_max(card)),
                "max": float(get_card_ammo_max(card)),
                "current": float(get_card_ammo_max(card)),
                "empty": False,
            }
            for card in active
            if get_card_ammo_max(card) > 0
        },
        bonus={
            attr: {card.placement_id: 0.0 for card in active}
            for attr in (
                "DamageAmount",
                "BurnApplyAmount",
                "PoisonApplyAmount",
                "ShieldApplyAmount",
                "HealAmount",
                "RegenApplyAmount",
                "RageApplyAmount",
                "CritChance",
                "Lifesteal",
                "Multicast",
                "AmmoMax",
                "ChargeAmount",
                "SlowAmount",
                "FreezeAmount",
                "HasteAmount",
            )
        },
        runtime_aura_bonus={
            attr: {card.placement_id: 0.0 for card in active}
            for attr in RUNTIME_AURA_ATTRS
        },
    )
    side.damage_reduction.extend(_initial_damage_reduction(cards, duration_sec))
    side.revives.extend(_initial_revives(cards))
    side.condition_state = _condition_snapshot(side)
    return side


def _apply_two_sided_static_card_auras(
    player_cards: list[PlacedCard],
    monster_cards: list[PlacedCard],
    rng: Callable[[], float] | None,
) -> None:
    player = BattleSide("player", player_cards, 1.0, 1.0)
    monster = BattleSide("monster", monster_cards, 1.0, 1.0)
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            for rule in read_rules(source, {"TAuraActionCardModifyAttribute"}):
                if not rule.attribute_type:
                    continue
                if _runtime_state_attribute(rule.attribute_type) or _condition_uses_runtime_state(rule.target_condition):
                    continue
                for target in _resolve_battle_targets(source_side, other_side, source, source, rule, rng):
                    if is_skill_card(target.card):
                        continue
                    tier = effective_tier(target.card)
                    current = get_attr_value_by_tier(target.card.card, rule.attribute_type, tier)
                    updated = apply_attribute_operation(current, rule.amount, rule.operation)
                    set_attr_value_by_tier(target.card.card, rule.attribute_type, tier, updated)


def _numeric_attr(attributes: dict[str, Any], key: str, *, default: float = 0.0) -> float:
    lower_key = key.lower()
    for raw_key, value in attributes.items():
        if str(raw_key).lower() != lower_key:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    return default


def _duration_attr_sec(attributes: dict[str, Any], key: str, *, default: float) -> float:
    value = _numeric_attr(attributes, key, default=default)
    if value > 100.0:
        return max(0.0, value / 1000.0)
    return max(0.0, value)


def _runtime_state_key(value: str) -> str:
    lower = str(value or "").strip().lower()
    if lower.endswith("reference"):
        lower = lower[: -len("reference")]
    return lower


def _runtime_state_label(state_key: str) -> str:
    return {
        "flying": "Flying",
        "heated": "Heated",
        "chilled": "Chilled",
        "enraged": "Enraged",
    }.get(state_key, state_key)


def _runtime_state_attribute(attribute_type: str) -> bool:
    return _runtime_state_key(attribute_type) in RUNTIME_STATE_ATTRS


def _runtime_condition_attribute(attribute_type: str) -> bool:
    return _runtime_state_attribute(attribute_type) or _runtime_state_key(attribute_type) in BATTLE_SIDE_CONDITION_ATTRS


def _normalize_battle_attribute(attribute_type: str) -> str:
    text = str(attribute_type or "").strip()
    lower = text.lower()
    aliases = {
        "cooldown": "CooldownMax",
        "cooldownmax": "CooldownMax",
        "cooldownamount": "CooldownMax",
        "cooldownduration": "CooldownMax",
        "flatcooldownreduction": "FlatCooldownReduction",
        "cooldownreduction": "FlatCooldownReduction",
        "percentcooldownreduction": "PercentCooldownReduction",
        "cooldownpercent": "PercentCooldownReduction",
        "multicast": "Multicast",
        "ammomax": "AmmoMax",
        "chargeamount": "ChargeAmount",
        "slowamount": "SlowAmount",
        "slowduration": "SlowAmount",
        "freezeamount": "FreezeAmount",
        "freezeduration": "FreezeAmount",
        "hasteamount": "HasteAmount",
        "hasteduration": "HasteAmount",
        "percentfreezereduction": "PercentFreezeReduction",
        "freezereduction": "PercentFreezeReduction",
        "percentslowreduction": "PercentSlowReduction",
        "slowreduction": "PercentSlowReduction",
        "percenthastereduction": "PercentHasteReduction",
        "hastereduction": "PercentHasteReduction",
    }
    return aliases.get(lower, normalize_offense_attr(text) or text)


def _condition_uses_runtime_state(condition: Any) -> bool:
    return any(_runtime_condition_attribute(str(item.get("attribute") or "")) for item in getattr(condition, "attr_conditions", []))


def _condition_runtime_attr_conditions(condition: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in getattr(condition, "attr_conditions", [])
        if _runtime_condition_attribute(str(item.get("attribute") or ""))
    ]


def _condition_without_runtime_attrs(condition: Any) -> Any:
    runtime_conditions = _condition_runtime_attr_conditions(condition)
    if not runtime_conditions:
        return condition
    return replace(
        condition,
        attr_conditions=[
            item
            for item in getattr(condition, "attr_conditions", [])
            if not _runtime_condition_attribute(str(item.get("attribute") or ""))
        ],
    )


def _battle_card_matches(card_ref: BattleCardRef, condition: Any) -> bool:
    runtime_conditions = _condition_runtime_attr_conditions(condition)
    static_condition = _condition_without_runtime_attrs(condition)
    static_pass = matches_card(
        card_ref.card,
        static_condition,
        card_ref.side.cards,
        _runtime_aura_tags(card_ref.side),
    )
    if not runtime_conditions:
        return static_pass
    runtime_checks = [
        compare_number(
            _runtime_state_attr_value(card_ref, str(item.get("attribute") or "")),
            str(item.get("operator") or ""),
            float(item.get("value", 0) or 0),
        )
        for item in runtime_conditions
    ]
    runtime_pass = all(runtime_checks) if getattr(condition, "mode", "and") != "or" else any(runtime_checks)
    if getattr(condition, "mode", "and") == "or":
        return static_pass or runtime_pass
    return static_pass and runtime_pass


def _runtime_aura_tags(side: BattleSide) -> dict[str, set[str]]:
    tags_by_card: dict[str, set[str]] = {}
    for placement_id, sources in side.runtime_tags_by_source.items():
        tags: set[str] = set()
        for source_tags in sources.values():
            tags.update(source_tags)
        if tags:
            tags_by_card[placement_id] = tags
    return tags_by_card


def _runtime_tags_for_action(
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    amount: float,
    rng: Callable[[], float] | None,
) -> set[str]:
    action_type = str(rule.action_type or "")
    raw_tags = [normalize_tag(tag) for tag in (get_field(rule.raw_action, "Tags", default=[]) or []) if normalize_tag(tag)]
    if action_type == "TActionCardAddTagsList":
        return set(raw_tags)
    if action_type == "TActionCardAddTagsRandom":
        if not raw_tags:
            return set()
        count = min(len(raw_tags), max(1, int(round(amount or rule.amount or 1))))
        pool = list(raw_tags)
        picked: set[str] = set()
        for _ in range(count):
            index = 0 if rng is None else max(0, min(len(pool) - 1, int(rng() * len(pool))))
            picked.add(pool.pop(index))
            if not pool:
                break
        return picked
    if action_type == "TActionCardAddTagsBySource":
        tag_map = _runtime_aura_tags(source_side)
        static_tags = set()
        for key in ("tags", "hidden_tags", "visible_tags"):
            static_tags.update(normalize_tag(tag) for tag in source.card.get(key, []) or [] if normalize_tag(tag))
        return static_tags | tag_map.get(source.placement_id, set())
    return set()


def _add_runtime_tags(
    target: BattleCardRef,
    tags: set[str],
    source_id: str,
    source_side: BattleSide,
    source: PlacedCard,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    if not tags:
        return False
    sources = target.side.runtime_tags_by_source.setdefault(target.card.placement_id, {})
    before = set().union(*sources.values()) if sources else set()
    sources[source_id] = set(tags)
    after = set().union(*sources.values()) if sources else set()
    added = sorted(after - before)
    if not added:
        return False
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target.side.name,
            "runtime-tags-added",
            card_label(source),
            float(len(added)),
            target=card_label(target.card),
            tags=added,
            source_id=source_id,
        )
    )
    return True


def _remove_runtime_tags(
    side: BattleSide,
    card: PlacedCard,
    source_id: str,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    sources = side.runtime_tags_by_source.get(card.placement_id)
    if not sources or source_id not in sources:
        return False
    before = set().union(*sources.values()) if sources else set()
    sources.pop(source_id, None)
    after = set().union(*sources.values()) if sources else set()
    removed = sorted(before - after)
    if not removed:
        return False
    timeline.append(
        _battle_event(
            now,
            side.name,
            side.name,
            "runtime-tags-removed",
            card_label(card),
            float(len(removed)),
            target=card_label(card),
            tags=removed,
            source_id=source_id,
        )
    )
    return True


def _runtime_state_attr_value(card_ref: BattleCardRef, attribute_type: str) -> float:
    state_key = _runtime_state_key(attribute_type)
    if state_key in RUNTIME_SIDE_STATES:
        return 1.0 if _has_runtime_state(card_ref.side, None, state_key) else 0.0
    if state_key in {"shield"}:
        return max(0.0, card_ref.side.shield)
    if state_key in {"burn", "burning"}:
        return max(0.0, card_ref.side.burn_stack)
    if state_key in {"poison", "poisoned"}:
        return max(0.0, card_ref.side.poison_stack)
    if state_key == "health":
        return max(0.0, card_ref.side.health)
    if state_key == "healthpercent":
        return max(0.0, card_ref.side.health) / max(1.0, card_ref.side.max_health) * 100.0
    if state_key == "belowhalfhealth":
        return 1.0 if card_ref.side.max_health > 0 and card_ref.side.health <= card_ref.side.max_health / 2.0 else 0.0
    if state_key == "rage":
        return max(0.0, card_ref.side.rage)
    return 1.0 if _has_runtime_state(card_ref.side, card_ref.card, state_key) else 0.0


def _state_bucket(side: BattleSide, card: PlacedCard | None, state_type: str) -> list[RuntimeState]:
    state_key = _runtime_state_key(state_type)
    if state_key in RUNTIME_SIDE_STATES or card is None:
        return side.runtime_states.setdefault(state_key, [])
    item_states = side.item_runtime_states.setdefault(card.placement_id, {})
    return item_states.setdefault(state_key, [])


def _state_bucket_read(side: BattleSide, card: PlacedCard | None, state_type: str) -> list[RuntimeState]:
    state_key = _runtime_state_key(state_type)
    if state_key in RUNTIME_SIDE_STATES or card is None:
        return side.runtime_states.get(state_key, [])
    return side.item_runtime_states.get(card.placement_id, {}).get(state_key, [])


def _state_is_active(state: RuntimeState, now: float | None = None) -> bool:
    return now is None or not math.isfinite(state.expire_time) or state.expire_time > now + 1e-6


def _has_runtime_state(side: BattleSide, card: PlacedCard | None, state_type: str, now: float | None = None) -> bool:
    return any(_state_is_active(state, now) for state in _state_bucket_read(side, card, state_type))


def _add_runtime_state(
    side: BattleSide,
    card: PlacedCard | None,
    state_type: str,
    *,
    value: float,
    source_id: str,
    source_label: str,
    source_side: BattleSide,
    now: float,
    duration_sec: float,
    timeline: list[dict[str, Any]],
) -> bool:
    state_key = _runtime_state_key(state_type)
    states = _state_bucket(side, card, state_key)
    was_active = any(_state_is_active(state, now) for state in states)
    expire_time = now + duration_sec if math.isfinite(duration_sec) and duration_sec > 0 else math.inf
    existing = next((state for state in states if state.source_id == source_id), None)
    if existing is None:
        states.append(
            RuntimeState(
                state_type=_runtime_state_label(state_key),
                value=value,
                source_id=source_id,
                start_time=now,
                expire_time=expire_time,
            )
        )
    else:
        existing.value = value
        existing.start_time = now
        existing.expire_time = expire_time
    became_active = not was_active
    if became_active:
        _emit_runtime_state_transition(side, card, state_key, True, now, timeline, source_label, source_side.name)
    return became_active


def _remove_runtime_state(
    side: BattleSide,
    card: PlacedCard | None,
    state_type: str,
    *,
    source_id: str = "",
    source_label: str,
    source_side: BattleSide,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    state_key = _runtime_state_key(state_type)
    states = _state_bucket(side, card, state_key)
    was_active = any(_state_is_active(state, now) for state in states)
    if source_id:
        states[:] = [state for state in states if state.source_id != source_id]
    else:
        states.clear()
    is_active = any(_state_is_active(state, now) for state in states)
    became_inactive = was_active and not is_active
    if became_inactive:
        _emit_runtime_state_transition(side, card, state_key, False, now, timeline, source_label, source_side.name)
    return became_inactive


def _emit_runtime_state_transition(
    side: BattleSide,
    card: PlacedCard | None,
    state_key: str,
    entered: bool,
    now: float,
    timeline: list[dict[str, Any]],
    source_label: str,
    source_side_name: str,
) -> None:
    label = _runtime_state_label(state_key)
    target = card_label(card) if card is not None else side.name
    timeline.append(
        _battle_event(
            now,
            source_side_name,
            side.name,
            "runtime-state-entered" if entered else "runtime-state-exited",
            source_label,
            1.0 if entered else 0.0,
            target=target,
            state=label,
        )
    )
    specific_kind = {
        ("flying", True): "item-started-flying",
        ("flying", False): "item-stopped-flying",
        ("heated", True): "item-heated",
        ("heated", False): "item-heat-ended",
        ("chilled", True): "item-chilled",
        ("chilled", False): "item-chill-ended",
        ("enraged", True): "player-enraged",
        ("enraged", False): "player-enrage-ended",
    }.get((state_key, entered))
    if specific_kind:
        timeline.append(
            _battle_event(
                now,
                source_side_name,
                side.name,
                specific_kind,
                source_label,
                1.0 if entered else 0.0,
                target=target,
                state=label,
            )
        )


def _initialize_runtime_state_auras(
    player: BattleSide,
    monster: BattleSide,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    duration_sec: float,
) -> None:
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            for rule in read_rules(source, {"TAuraActionCardModifyAttribute"}):
                state_key = _runtime_state_key(rule.attribute_type)
                if state_key not in RUNTIME_CARD_STATES or rule.amount <= 0:
                    continue
                duration = rule.duration_sec if math.isfinite(rule.duration_sec) and rule.duration_sec > 0 else duration_sec
                for target in _resolve_battle_targets(source_side, other_side, source, source, rule, rng):
                    if is_skill_card(target.card):
                        continue
                    _add_runtime_state(
                        target.side,
                        target.card,
                        state_key,
                        value=rule.amount,
                        source_id=rule.effect_id,
                        source_label=card_label(source),
                        source_side=source_side,
                        now=0.0,
                        duration_sec=duration,
                        timeline=timeline,
                    )


def _next_runtime_state_expiry(sides: list[BattleSide], now: float) -> float:
    next_time = math.inf
    for side in sides:
        for states in side.runtime_states.values():
            for state in states:
                if math.isfinite(state.expire_time) and state.expire_time > now + 1e-6:
                    next_time = min(next_time, state.expire_time)
        for item_states in side.item_runtime_states.values():
            for states in item_states.values():
                for state in states:
                    if math.isfinite(state.expire_time) and state.expire_time > now + 1e-6:
                        next_time = min(next_time, state.expire_time)
    return next_time


def _expire_runtime_states_for_sides(
    player: BattleSide,
    monster: BattleSide,
    now: float,
    timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    for side in (player, monster):
        for state_key, states in list(side.runtime_states.items()):
            expired = [state for state in states if math.isfinite(state.expire_time) and state.expire_time <= now + 1e-6]
            if not expired:
                continue
            states[:] = [state for state in states if state not in expired]
            if states:
                continue
            if state_key == "enraged":
                side.is_enraged = False
            _emit_runtime_state_transition(side, None, state_key, False, now, timeline, _runtime_state_label(state_key), side.name)
            if state_key == "enraged":
                transitions.append({"side": side, "card": _first_event_card(side), "performed": {"enrage_ended": 1}})
        for placement_id, item_states in list(side.item_runtime_states.items()):
            card = next((item for item in side.cards if item.placement_id == placement_id), None)
            if card is None:
                continue
            for state_key, states in list(item_states.items()):
                expired = [state for state in states if math.isfinite(state.expire_time) and state.expire_time <= now + 1e-6]
                if not expired:
                    continue
                states[:] = [state for state in states if state not in expired]
                if states:
                    continue
                _emit_runtime_state_transition(side, card, state_key, False, now, timeline, _runtime_state_label(state_key), side.name)
                performed = {}
                if state_key == "flying":
                    performed["stopped_flying"] = 1
                transitions.append({"side": side, "card": card, "performed": performed})
    return transitions


def _first_event_card(side: BattleSide) -> PlacedCard | None:
    return side.active[0] if side.active else (side.cards[0] if side.cards else None)


def _refresh_runtime_state_auras(
    player: BattleSide,
    monster: BattleSide,
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
) -> None:
    for side in (player, monster):
        side.runtime_aura_bonus = {
            attr: {card.placement_id: 0.0 for card in side.active}
            for attr in RUNTIME_AURA_ATTRS
        }
    active_cooldown_modifier_ids: set[str] = set()
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            for rule in read_rules(source, {"TAuraActionCardModifyAttribute"}):
                if not _condition_uses_runtime_state(rule.target_condition):
                    continue
                mapped = _normalize_battle_attribute(rule.attribute_type)
                if mapped not in RUNTIME_AURA_ATTRS and mapped not in COOLDOWN_AURA_ATTRS:
                    continue
                for target in _resolve_battle_targets(source_side, other_side, source, source, rule, rng):
                    if mapped in COOLDOWN_AURA_ATTRS:
                        modifier_id = _runtime_aura_modifier_id(source_side, source, rule, target, mapped)
                        active_cooldown_modifier_ids.add(modifier_id)
                        _upsert_runtime_cooldown_modifier(
                            target,
                            source_side,
                            source,
                            rule,
                            mapped,
                            modifier_id,
                            now,
                            scheduler,
                            timeline,
                        )
                        continue
                    if target.card.placement_id not in target.side.runtime_aura_bonus.get(mapped, {}):
                        continue
                    current = target.side.runtime_aura_bonus[mapped][target.card.placement_id]
                    target.side.runtime_aura_bonus[mapped][target.card.placement_id] = apply_attribute_operation(
                        current,
                        rule.amount,
                        rule.operation,
                    )
    _remove_stale_runtime_cooldown_modifiers((player, monster), active_cooldown_modifier_ids, now, scheduler, timeline)
    _refresh_ammo_max_for_sides((player, monster), now, timeline)


def _runtime_aura_modifier_id(
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    target: BattleCardRef,
    attribute: str,
) -> str:
    return f"runtime-aura:{source_side.name}:{source.placement_id}:{rule.effect_id}:{target.card.placement_id}:{attribute}"


def _upsert_runtime_cooldown_modifier(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    attribute: str,
    modifier_id: str,
    now: float,
    scheduler: BattleEventScheduler,
    timeline: list[dict[str, Any]],
    config: CooldownModifyConfig = DEFAULT_COOLDOWN_MODIFY_CONFIG,
) -> None:
    state = _cooldown_state(target.side, target.card)
    if state is None:
        return
    new_modifier = {
        "id": modifier_id,
        "kind": "runtime_aura",
        "attribute": attribute,
        "amount": rule.amount,
        "operation": rule.operation,
        "source": card_label(source),
        "start": now,
        "expires_at": math.inf,
    }
    existing = next((modifier for modifier in state.modifiers if modifier.get("id") == modifier_id), None)
    if existing is not None:
        if (
            float(existing.get("amount") or 0.0) == float(rule.amount)
            and str(existing.get("operation") or "") == str(rule.operation or "")
            and str(existing.get("attribute") or "") == attribute
        ):
            return
        existing.update(new_modifier)
    else:
        state.modifiers.append(new_modifier)
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "runtime-aura-added",
                card_label(source),
                rule.amount,
                target=card_label(target.card),
                attribute=attribute,
                modifier_id=modifier_id,
            )
        )
    _recalculate_effective_cooldown(state, target, source_side, source, now, timeline, config)
    if state.remaining_cooldown <= 1e-6:
        _activate_ready_card(target, now, scheduler, timeline, reason="runtime-aura")


def _remove_stale_runtime_cooldown_modifiers(
    sides: tuple[BattleSide, BattleSide],
    active_modifier_ids: set[str],
    now: float,
    scheduler: BattleEventScheduler,
    timeline: list[dict[str, Any]],
    config: CooldownModifyConfig = DEFAULT_COOLDOWN_MODIFY_CONFIG,
) -> None:
    for side in sides:
        for card in side.active:
            state = _cooldown_state(side, card)
            if state is None:
                continue
            stale = [
                modifier
                for modifier in state.modifiers
                if modifier.get("kind") == "runtime_aura" and str(modifier.get("id")) not in active_modifier_ids
            ]
            if not stale:
                continue
            stale_ids = {str(modifier.get("id")) for modifier in stale}
            state.modifiers = [modifier for modifier in state.modifiers if str(modifier.get("id")) not in stale_ids]
            for modifier in stale:
                timeline.append(
                    _battle_event(
                        now,
                        side.name,
                        side.name,
                        "runtime-aura-removed",
                        str(modifier.get("source") or "runtime-aura"),
                        float(modifier.get("amount") or 0.0),
                        target=card_label(card),
                        attribute=str(modifier.get("attribute") or ""),
                        modifier_id=str(modifier.get("id") or ""),
                    )
                )
            _recalculate_effective_cooldown(state, BattleCardRef(side, card), side, card, now, timeline, config)
            if state.remaining_cooldown <= 1e-6:
                _activate_ready_card(BattleCardRef(side, card), now, scheduler, timeline, reason="runtime-aura-removed")


def _refresh_ammo_max_for_sides(
    sides: tuple[BattleSide, BattleSide],
    now: float,
    timeline: list[dict[str, Any]],
) -> None:
    for side in sides:
        for card in side.active:
            ammo = side.ammo.get(card.placement_id)
            if not ammo:
                continue
            old_max = float(ammo.get("max") or 0.0)
            base_max = float(ammo.get("base_max") if ammo.get("base_max") is not None else old_max)
            new_max = max(0.0, base_max + _bonus_value(side, "AmmoMax", card.placement_id))
            if abs(new_max - old_max) <= 1e-6:
                continue
            old_current = float(ammo.get("current") or 0.0)
            ammo["max"] = new_max
            ammo["current"] = min(old_current, new_max)
            ammo["empty"] = ammo["current"] <= 0
            timeline.append(
                _battle_event(
                    now,
                    side.name,
                    side.name,
                    "ammo-max-modified",
                    card_label(card),
                    new_max - old_max,
                    target=card_label(card),
                    old_max=old_max,
                    new_max=new_max,
                    old_current=old_current,
                    new_current=ammo["current"],
                )
            )
            if ammo["current"] < old_current:
                timeline.append(
                    _battle_event(
                        now,
                        side.name,
                        side.name,
                        "ammo-clipped",
                        card_label(card),
                        old_current - ammo["current"],
                        target=card_label(card),
                        old_current=old_current,
                        new_current=ammo["current"],
                    )
                )


def _merged_bonus(side: BattleSide) -> dict[str, dict[str, float]]:
    merged: dict[str, dict[str, float]] = {}
    for attr in set(side.bonus) | set(side.runtime_aura_bonus):
        placement_ids = set(side.bonus.get(attr, {})) | set(side.runtime_aura_bonus.get(attr, {}))
        merged[attr] = {
            placement_id: side.bonus.get(attr, {}).get(placement_id, 0.0)
            + side.runtime_aura_bonus.get(attr, {}).get(placement_id, 0.0)
            for placement_id in placement_ids
        }
    return merged


def _bonus_value(side: BattleSide, attr: str, placement_id: str) -> float:
    normalized = _normalize_battle_attribute(attr)
    return side.bonus.get(normalized, {}).get(placement_id, 0.0) + side.runtime_aura_bonus.get(normalized, {}).get(placement_id, 0.0)


def _effective_multicast(ref: BattleCardRef) -> int:
    base = get_attr_value_by_tier(ref.card.card, "Multicast", effective_tier(ref.card)) or 1.0
    value = base + _bonus_value(ref.side, "Multicast", ref.card.placement_id)
    return max(1, int(round(value)))


def _effective_rule_amount(
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    amount: float,
    casts: int,
) -> float:
    attr = _normalize_battle_attribute(_action_bonus_attribute(rule.action_type))
    if not attr:
        return amount
    return max(0.0, amount + _bonus_value(source_side, attr, source.placement_id) * max(1, casts))


def _action_bonus_attribute(action_type: str) -> str:
    return {
        "TActionPlayerDamage": "DamageAmount",
        "TActionPlayerBurnApply": "BurnApplyAmount",
        "TActionPlayerPoisonApply": "PoisonApplyAmount",
        "TActionPlayerShieldApply": "ShieldApplyAmount",
        "TActionPlayerHealApply": "HealAmount",
        "TActionPlayerHeal": "HealAmount",
        "TActionPlayerRegenApply": "RegenApplyAmount",
        "TActionPlayerRageApply": "RageApplyAmount",
        "TActionCardCharge": "ChargeAmount",
        "TActionCardHaste": "HasteAmount",
        "TActionCardSlow": "SlowAmount",
        "TActionCardFreeze": "FreezeAmount",
    }.get(action_type, "")


def _effective_status_duration(target: BattleCardRef, status_name: str, duration_sec: float) -> float:
    status = _runtime_state_key(status_name)
    effective = max(0.0, duration_sec)
    reduction_attr = STATUS_DURATION_REDUCTION_ATTRS.get(status)
    if reduction_attr:
        reduction = max(0.0, _bonus_value(target.side, reduction_attr, target.card.placement_id))
        effective *= max(0.0, 1.0 - reduction / 100.0)
    if status in {"slow", "freeze"} and _has_runtime_state(target.side, target.card, "flying"):
        effective *= 0.5
    return max(0.0, effective)


def _initial_damage_reduction(cards: list[PlacedCard], duration_sec: float) -> list[dict[str, float]]:
    reductions: list[dict[str, float]] = []
    for source in cards:
        for rule in read_rules(source, {"TAuraActionPlayerModifyAttribute"}):
            if rule.attribute_type != "PercentDamageReduction" or rule.amount <= 0:
                continue
            reductions.append(
                {
                    "start": 0.0,
                    "duration": rule.duration_sec if rule.duration_sec > 0 else duration_sec,
                    "value": rule.amount,
                }
            )
    return reductions


def _initial_revives(cards: list[PlacedCard]) -> list[dict[str, Any]]:
    revives: list[dict[str, Any]] = []
    for source in cards:
        for row in effect_rows(source.card):
            action = get_field(row, "Action", default={}) or {}
            if type_name(action) != "TActionPlayerReviveHeal":
                continue
            amount = max(0.0, _action_amount_for_battle(action, source, "HealAmount"))
            revives.append(
                {
                    "source": card_label(source),
                    "value": amount if amount > 0 else 0.5,
                    "mode": "health" if amount > 0 else "health_fraction",
                }
            )
    return revives


def _action_amount_for_battle(action: dict[str, Any], source: PlacedCard, default_attr: str) -> float:
    from combat_simulator import action_amount

    return action_amount(action, source, default_attr)


def _cooldown_state(side: BattleSide, card: PlacedCard) -> ItemCooldownState | None:
    return side.cooldowns.get(card.placement_id)


def _cooldown_speed_multiplier(side: BattleSide, card: PlacedCard, now: float) -> float:
    placement_id = card.placement_id
    if now < side.freeze_until.get(placement_id, 0.0):
        return 0.0
    speed = 1.0
    if now < side.haste_until.get(placement_id, 0.0):
        speed *= 2.0
    if now < side.slow_until.get(placement_id, 0.0):
        speed *= 0.5
    return speed


def _next_speed_change_time(side: BattleSide, card: PlacedCard, now: float) -> float:
    placement_id = card.placement_id
    changes = [
        value
        for value in (
            side.haste_until.get(placement_id, 0.0),
            side.slow_until.get(placement_id, 0.0),
            side.freeze_until.get(placement_id, 0.0),
        )
        if value > now + 1e-6
    ]
    return min(changes) if changes else math.inf


def _next_card_ready_time(sides: list[BattleSide], now: float) -> float:
    next_time = math.inf
    for side in sides:
        if side.health <= 0:
            continue
        for card in side.active:
            ammo = side.ammo.get(card.placement_id)
            if ammo and ammo.get("empty"):
                continue
            state = _cooldown_state(side, card)
            if state is None:
                continue
            cooldown = max(0.0, state.remaining_cooldown)
            speed = _cooldown_speed_multiplier(side, card, now)
            if cooldown <= 1e-6:
                next_time = min(next_time, now)
            elif speed > 0:
                next_time = min(next_time, now + cooldown / speed)
            next_time = min(next_time, _next_speed_change_time(side, card, now))
    return next_time


def _advance_cooldowns(sides: list[BattleSide], now: float, elapsed: float) -> None:
    if elapsed <= 0:
        return
    for side in sides:
        if side.health <= 0:
            continue
        for card in side.active:
            ammo = side.ammo.get(card.placement_id)
            if ammo and ammo.get("empty"):
                continue
            state = _cooldown_state(side, card)
            if state is None:
                continue
            speed = _cooldown_speed_multiplier(side, card, now)
            progress = elapsed * speed
            state.cooldown_elapsed = min(float(state.effective_cooldown or state.base_cooldown), state.cooldown_elapsed + progress)
            state.remaining_cooldown = max(0.0, state.remaining_cooldown - progress)


def _ready_cards(sides: list[BattleSide], epsilon: float) -> list[BattleCardRef]:
    ready: list[BattleCardRef] = []
    for side in sides:
        if side.health <= 0:
            continue
        for card in side.active:
            ammo = side.ammo.get(card.placement_id)
            if ammo and ammo.get("empty"):
                continue
            state = _cooldown_state(side, card)
            if state is not None and state.remaining_cooldown <= epsilon:
                ready.append(BattleCardRef(side, card))
    return ready


def _activate_ready_card(
    ref: BattleCardRef,
    now: float,
    scheduler: BattleEventScheduler,
    timeline: list[dict[str, Any]],
    *,
    reason: str,
) -> None:
    state = _cooldown_state(ref.side, ref.card)
    if state is None or ref.side.health <= 0:
        return
    ammo = ref.side.ammo.get(ref.card.placement_id)
    if ammo and ammo.get("empty"):
        return
    state.cooldown_elapsed = 0.0
    state.remaining_cooldown = max(0.0, float(state.effective_cooldown or state.base_cooldown))
    casts = _effective_multicast(ref)
    timing = item_timing_config(ref.card)
    multicast_interval = timing.multicast_interval if timing.multicast_interval is not None else timing.use_icd
    timeline.append(
        _battle_event(
            now,
            ref.side.name,
            ref.side.name,
            "cooldown-ready",
            card_label(ref.card),
            0.0,
            target=card_label(ref.card),
            base_cooldown=round(state.base_cooldown, 6),
            effective_cooldown=round(float(state.effective_cooldown or state.base_cooldown), 6),
            remaining_cooldown=round(state.remaining_cooldown, 6),
            multicast_snapshot=casts,
            reason=reason,
        )
    )
    if casts > 1:
        timeline.append(
            _battle_event(
                now,
                ref.side.name,
                ref.side.name,
                "multicast-requested",
                card_label(ref.card),
                casts,
                target=card_label(ref.card),
                reason=reason,
            )
        )
    for cast_index in range(1, casts + 1):
        requested_time = now + (cast_index - 1) * multicast_interval
        scheduler.request_item_use(ref, requested_time, reason=reason if cast_index == 1 else "multicast", cast_index=cast_index)


def _resolve_charge_event(
    event: ScheduledBattleEvent,
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
) -> None:
    target = event.ref
    source_side = event.source_side
    source = event.source
    if target is None or source_side is None or source is None or target.side.health <= 0:
        return
    state = _cooldown_state(target.side, target.card)
    if state is None:
        return
    before = max(0.0, state.remaining_cooldown)
    amount = max(0.0, event.amount)
    state.cooldown_elapsed = min(float(state.effective_cooldown or state.base_cooldown), state.cooldown_elapsed + amount)
    state.remaining_cooldown = max(0.0, before - amount)
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target.side.name,
            "charge-resolved",
            card_label(source),
            event.amount,
            target=card_label(target.card),
            port_id=event.port_id,
            trigger_time=round(event.requested_time, 6),
            old_remaining=round(before, 6),
            new_remaining=round(state.remaining_cooldown, 6),
        )
    )
    if state.remaining_cooldown <= 1e-6:
        _activate_ready_card(target, now, scheduler, timeline, reason="charge")


def _cooldown_attribute_supported(attribute_type: str) -> bool:
    return _normalize_battle_attribute(attribute_type) in COOLDOWN_AURA_ATTRS


def _add_cooldown_modifier(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    amount: float,
    now: float,
    scheduler: BattleEventScheduler,
    timeline: list[dict[str, Any]],
    duration_sec: float,
    config: CooldownModifyConfig = DEFAULT_COOLDOWN_MODIFY_CONFIG,
) -> None:
    state = _cooldown_state(target.side, target.card)
    if state is None:
        return
    modifier_id = f"{rule.effect_id}:{target.card.placement_id}:{len(state.modifiers) + 1}:{now:.6f}"
    state.modifiers.append(
        {
            "id": modifier_id,
            "amount": amount,
            "operation": rule.operation,
            "attribute": _normalize_battle_attribute(rule.attribute_type),
            "source": card_label(source),
            "start": now,
            "expires_at": now + rule.duration_sec if math.isfinite(rule.duration_sec) and rule.duration_sec > 0 else math.inf,
        }
    )
    _recalculate_effective_cooldown(state, target, source_side, source, now, timeline, config)
    if math.isfinite(rule.duration_sec) and rule.duration_sec > 0:
        scheduler.schedule_cooldown_modifier_expiry(
            target,
            now + rule.duration_sec,
            source_side=source_side,
            source=source,
            modifier_id=modifier_id,
        )
    if state.remaining_cooldown <= 1e-6:
        _activate_ready_card(target, now, scheduler, timeline, reason="cooldown-modified")


def _expire_cooldown_modifier(
    event: ScheduledBattleEvent,
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    config: CooldownModifyConfig = DEFAULT_COOLDOWN_MODIFY_CONFIG,
) -> None:
    target = event.ref
    if target is None:
        return
    state = _cooldown_state(target.side, target.card)
    if state is None:
        return
    before_count = len(state.modifiers)
    state.modifiers = [modifier for modifier in state.modifiers if modifier.get("id") != event.modifier_id]
    if len(state.modifiers) == before_count:
        return
    source_side = event.source_side or target.side
    source = event.source or target.card
    _recalculate_effective_cooldown(state, target, source_side, source, now, timeline, config, expired_modifier_id=event.modifier_id)
    if state.remaining_cooldown <= 1e-6:
        _activate_ready_card(target, now, scheduler, timeline, reason="cooldown-modifier-expired")


def _recalculate_effective_cooldown(
    state: ItemCooldownState,
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    now: float,
    timeline: list[dict[str, Any]],
    config: CooldownModifyConfig,
    *,
    expired_modifier_id: str = "",
) -> None:
    old_effective = max(0.0, float(state.effective_cooldown or state.base_cooldown))
    old_remaining = max(0.0, state.remaining_cooldown)
    old_elapsed = max(0.0, state.cooldown_elapsed)
    new_effective = max(0.0, state.base_cooldown)
    for modifier in state.modifiers:
        attr = _normalize_battle_attribute(str(modifier.get("attribute") or "CooldownMax"))
        value = float(modifier.get("amount") or 0.0)
        if attr == "FlatCooldownReduction":
            new_effective = max(0.0, new_effective - value)
        elif attr == "PercentCooldownReduction":
            new_effective = max(0.0, new_effective * max(0.0, 1.0 - value / 100.0))
        else:
            new_effective = max(
                0.0,
                apply_attribute_operation(new_effective, value, str(modifier.get("operation") or "Add")),
            )
    mode = str(config.progress_mode or "preserve_progress_ratio").lower()
    if old_effective > 0 and mode == "preserve_progress_ratio":
        progress_ratio = max(0.0, min(1.0, old_elapsed / old_effective))
        state.cooldown_elapsed = min(new_effective, new_effective * progress_ratio)
    else:
        state.cooldown_elapsed = min(new_effective, old_elapsed)
    state.effective_cooldown = new_effective
    state.remaining_cooldown = max(0.0, new_effective - state.cooldown_elapsed)
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target.side.name,
            "cooldown-modified",
            card_label(source),
            new_effective - old_effective,
            target=card_label(target.card),
            old_effective_cooldown=round(old_effective, 6),
            new_effective_cooldown=round(new_effective, 6),
            old_remaining=round(old_remaining, 6),
            new_remaining=round(state.remaining_cooldown, 6),
            progress_mode=config.progress_mode,
            expired_modifier_id=expired_modifier_id,
        )
    )


def _fire_battle_card(
    ref: BattleCardRef,
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    forced: bool,
    cast_index: int = 1,
    requested_time: float | None = None,
) -> None:
    source_side = ref.side
    target_side = _opponent(player, monster, source_side)
    fired = ref.card
    if source_side.health <= 0:
        return
    ammo = source_side.ammo.get(fired.placement_id)
    if ammo and ammo.get("current", 0.0) <= 0:
        ammo["empty"] = True
        state = _cooldown_state(source_side, fired)
        if state is not None:
            state.remaining_cooldown = 0.0
        return

    casts = 1
    if ammo:
        ammo["current"] = max(0.0, float(ammo.get("current", 0.0)) - 1.0)
        ammo["empty"] = False
    source_side.uses[fired.placement_id] = source_side.uses.get(fired.placement_id, 0.0) + 1.0

    merged_bonus = _merged_bonus(source_side)
    crit_multiplier, crits = crit_multiplier_for_casts(fired, source_side.cards, merged_bonus, casts, rng)
    damage = _battle_amount(fired, source_side, "TActionPlayerDamage", "DamageAmount", casts, crit_multiplier, opponent_only=True)
    burn = _battle_amount(fired, source_side, "TActionPlayerBurnApply", "BurnApplyAmount", casts, crit_multiplier, opponent_only=True)
    poison = _battle_amount(fired, source_side, "TActionPlayerPoisonApply", "PoisonApplyAmount", casts, crit_multiplier, opponent_only=True)
    shield = _battle_amount(fired, source_side, "TActionPlayerShieldApply", "ShieldApplyAmount", casts, crit_multiplier)
    heal = (
        _battle_amount(fired, source_side, "TActionPlayerHealApply", "HealAmount", casts, crit_multiplier)
        + _battle_amount(fired, source_side, "TActionPlayerHeal", "HealAmount", casts, crit_multiplier)
    )
    regen = _battle_amount(fired, source_side, "TActionPlayerRegenApply", "RegenApplyAmount", casts, crit_multiplier)
    rage = _battle_amount(fired, source_side, "TActionPlayerRageApply", "RageApplyAmount", casts, 1.0)
    performed = {
        "damage": casts if damage > 0 else 0,
        "burn": casts if burn > 0 else 0,
        "poison": casts if poison > 0 else 0,
        "crit": crits,
        "slow": 0,
        "haste": 0,
        "freeze": 0,
        "reload": 0,
        "destruction": 0,
        "shield": casts if shield > 0 else 0,
        "heal": 0,
        "regen": casts if regen > 0 else 0,
        "rage": casts if rage > 0 else 0,
        "enraged": 0,
    }

    if damage:
        actual = _apply_damage(source_side, target_side, damage, now, card_label(fired), "use", timeline)
        lifesteal = max(0.0, card_attr_with_bonus(fired, source_side.cards, "Lifesteal", merged_bonus)) / 100.0
        if lifesteal > 0 and actual > 0:
            actual_lifesteal = _apply_heal(source_side, actual * lifesteal, now, card_label(fired), timeline, reason="lifesteal")
            if actual_lifesteal > 0:
                performed["heal"] += 1
    if burn:
        target_side.burn_stack += burn
        timeline.append(_battle_event(now, source_side.name, target_side.name, "burn-apply", card_label(fired), burn))
    if poison:
        target_side.poison_stack += poison
        timeline.append(_battle_event(now, source_side.name, target_side.name, "poison-apply", card_label(fired), poison))
    if shield:
        source_side.shield += shield
        timeline.append(_battle_event(now, source_side.name, source_side.name, "shield", card_label(fired), shield))
    if heal:
        actual_heal = _apply_heal(source_side, heal, now, card_label(fired), timeline)
        if actual_heal > 0:
            performed["heal"] += 1
    if regen:
        source_side.regen_stack += regen
        timeline.append(_battle_event(now, source_side.name, source_side.name, "regen-apply", card_label(fired), regen))
    if rage:
        if _apply_rage(source_side, rage, now, card_label(fired), timeline):
            performed["enraged"] = 1
            _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
    if crits:
        timeline.append(_battle_event(now, source_side.name, target_side.name, "crit", card_label(fired), crits))

    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target_side.name,
            "item-used",
            card_label(fired),
            damage,
            requested_time=round(float(requested_time if requested_time is not None else now), 6),
            forced=forced,
            cast_index=cast_index,
        )
    )
    _run_triggered_rules(
        player,
        monster,
        rules_by_source,
        scheduler,
        now,
        timeline,
        rng,
        fired_ref=ref,
        performed=performed,
        casts=casts,
    )


def _battle_amount(
    card: PlacedCard,
    side: BattleSide,
    action_type: str,
    attr_type: str,
    casts: int,
    crit_multiplier: float,
    *,
    opponent_only: bool = False,
) -> float:
    from combat_simulator import base_on_use_amount

    base = base_on_use_amount(card, action_type, opponent_only=opponent_only)
    return max(0.0, base + _bonus_value(side, attr_type, card.placement_id)) * casts * crit_multiplier


def _run_fight_started_actions(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    duration_sec: float,
) -> None:
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            for rule in rules_by_source.get(source.placement_id, []):
                if "fightstarted" not in rule.trigger_type.lower():
                    continue
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    source,
                    rule,
                    max(0.0, rule.amount),
                    1,
                    scheduler,
                    0.0,
                    timeline,
                    rng,
                    rules_by_source,
                    duration_sec,
                    {"damage": 0},
                )


def _run_triggered_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    fired_ref: BattleCardRef,
    performed: dict[str, float],
    casts: int,
    trigger_depth: int = 0,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(_battle_event(now, fired_ref.side.name, fired_ref.side.name, "trigger-depth-limited", card_label(fired_ref.card), trigger_depth))
        return
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            for rule in rules_by_source.get(source.placement_id, []):
                if "fightstarted" in rule.trigger_type.lower() or "playerdied" in rule.trigger_type.lower():
                    continue
                if _skip_self_on_fire_rule(source, rule, fired_ref):
                    continue
                if not _battle_trigger_matches(source_side, fired_ref.side, source, fired_ref.card, rule, performed):
                    continue
                if not _consume_trigger_count(source_side, source, rule, fired_ref, now, timeline):
                    continue
                amount = max(0.0, rule.amount) * casts
                preview_amount = _effective_rule_amount(source_side, source, rule, amount, casts)
                if preview_amount <= 0 and rule.action_type not in AMOUNTLESS_RULE_ACTIONS:
                    continue
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    fired_ref.card,
                    rule,
                    amount,
                    casts,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    rules_by_source,
                    math.inf,
                    performed,
                    trigger_depth=trigger_depth + 1,
                )


def _run_state_triggered_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    event_side: BattleSide,
    event_card: PlacedCard,
    performed: dict[str, float],
    casts: int = 1,
    trigger_depth: int = 0,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(_battle_event(now, event_side.name, event_side.name, "trigger-depth-limited", card_label(event_card), trigger_depth))
        return
    fired_ref = BattleCardRef(event_side, event_card)
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            for rule in rules_by_source.get(source.placement_id, []):
                if "fightstarted" in rule.trigger_type.lower() or "playerdied" in rule.trigger_type.lower():
                    continue
                if _state_event_should_skip_rule(rule):
                    continue
                if not _battle_trigger_matches(source_side, event_side, source, event_card, rule, performed):
                    continue
                if not _consume_trigger_count(source_side, source, rule, fired_ref, now, timeline):
                    continue
                amount = max(0.0, rule.amount) * casts
                preview_amount = _effective_rule_amount(source_side, source, rule, amount, casts)
                if preview_amount <= 0 and rule.action_type not in AMOUNTLESS_RULE_ACTIONS:
                    continue
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    event_card,
                    rule,
                    amount,
                    casts,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    rules_by_source,
                    math.inf,
                    performed,
                    trigger_depth=trigger_depth + 1,
                )


def _state_event_should_skip_rule(rule: Any) -> bool:
    lower = str(rule.trigger_type or "").lower()
    return "cardfired" in lower or "itemused" in lower


def _consume_trigger_count(
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    fired_ref: BattleCardRef,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    limit = getattr(rule, "max_triggers_per_combat", None)
    if limit is None or limit <= 0:
        return True
    scope = str(getattr(rule, "trigger_limit_scope", "") or "combat").lower()
    source_item_id = fired_ref.card.placement_id if scope in {"source", "source_item", "item", "per_source", "per_item"} else None
    key = TriggerCounterKey(
        owner_id=f"{source_side.name}:{source.placement_id}",
        ability_id=str(getattr(rule, "effect_id", "") or source.placement_id),
        trigger_id=str(getattr(rule, "trigger_type", "")),
        source_item_id=source_item_id,
        target_item_id=None,
    )
    current = source_side.trigger_counts.get(key, 0)
    if current >= limit:
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                source_side.name,
                "trigger-blocked",
                card_label(source),
                current,
                trigger_id=key.trigger_id,
                ability_id=key.ability_id,
                limit=limit,
                source_item_id=key.source_item_id,
            )
        )
        return False
    source_side.trigger_counts[key] = current + 1
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            source_side.name,
            "trigger-count-consumed",
            card_label(source),
            current + 1,
            trigger_id=key.trigger_id,
            ability_id=key.ability_id,
            limit=limit,
            source_item_id=key.source_item_id,
        )
    )
    return True


def _skip_self_on_fire_rule(source: PlacedCard, rule: Any, fired_ref: BattleCardRef) -> bool:
    return (
        rule.action_type
        in {
            "TActionPlayerDamage",
            "TActionPlayerBurnApply",
            "TActionPlayerPoisonApply",
            "TActionPlayerShieldApply",
            "TActionPlayerHealApply",
            "TActionPlayerHeal",
            "TActionPlayerRegenApply",
            "TActionPlayerRageApply",
        }
        and rule.trigger_type == "TTriggerOnCardFired"
        and source.placement_id == fired_ref.card.placement_id
        and "." not in str(getattr(rule, "action_path", "0"))
    )


def _apply_battle_rule(
    player: BattleSide,
    monster: BattleSide,
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
    trigger_card: PlacedCard,
    rule: Any,
    amount: float,
    casts: int,
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    rules_by_source: dict[str, list[Any]],
    duration_sec: float,
    performed: dict[str, float],
    trigger_depth: int = 0,
) -> None:
    targets = _resolve_battle_targets(source_side, other_side, source, trigger_card, rule, rng)
    amount = _effective_rule_amount(source_side, source, rule, amount, casts)
    if rule.action_type == "TActionCardCharge":
        for target in targets:
            if target.card.placement_id not in target.side.cooldowns:
                continue
            scheduler.trigger_charge(
                target,
                now,
                source_side=source_side,
                source=source,
                port_id=rule.effect_id or f"{source.placement_id}:charge",
                amount=amount,
            )
        performed["charge"] = performed.get("charge", 0) + len(targets)
    elif rule.action_type == "TActionCardHaste":
        for target in targets:
            if target.card.placement_id in target.side.haste_until:
                effective = _effective_status_duration(target, "haste", amount)
                target.side.haste_until[target.card.placement_id] = max(target.side.haste_until[target.card.placement_id], now + effective)
                timeline.append(
                    _battle_event(
                        now,
                        source_side.name,
                        target.side.name,
                        "haste",
                        card_label(source),
                        effective,
                        target=card_label(target.card),
                        base_duration=amount,
                        effective_duration=effective,
                    )
                )
        performed["haste"] = performed.get("haste", 0) + len(targets)
    elif rule.action_type == "TActionCardSlow":
        for target in targets:
            if target.card.placement_id in target.side.slow_until:
                effective = _effective_status_duration(target, "slow", amount)
                target.side.slow_until[target.card.placement_id] = max(target.side.slow_until[target.card.placement_id], now + effective)
                timeline.append(
                    _battle_event(
                        now,
                        source_side.name,
                        target.side.name,
                        "slow",
                        card_label(source),
                        effective,
                        target=card_label(target.card),
                        base_duration=amount,
                        effective_duration=effective,
                        duration_modifier="flying" if effective != amount else "",
                    )
                )
        performed["slow"] = performed.get("slow", 0) + len(targets)
    elif rule.action_type == "TActionCardFreeze":
        for target in targets:
            if target.card.placement_id in target.side.freeze_until:
                effective = _effective_status_duration(target, "freeze", amount)
                target.side.freeze_until[target.card.placement_id] = max(target.side.freeze_until[target.card.placement_id], now + effective)
                timeline.append(
                    _battle_event(
                        now,
                        source_side.name,
                        target.side.name,
                        "freeze",
                        card_label(source),
                        effective,
                        target=card_label(target.card),
                        base_duration=amount,
                        effective_duration=effective,
                        duration_modifier="flying" if effective != amount else "",
                    )
                )
        performed["freeze"] = performed.get("freeze", 0) + len(targets)
    elif rule.action_type in {"TActionCardFlyingStart", "TActionCardFlyingStop", "TActionCardFlyingToggle"}:
        for target in targets:
            transition = _apply_flying_action(
                target,
                source_side,
                source,
                rule,
                now,
                timeline,
                duration_sec,
            )
            if not transition:
                continue
            performed[transition] = performed.get(transition, 0) + 1
            _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
            _run_state_triggered_rules(
                player,
                monster,
                rules_by_source,
                scheduler,
                now,
                timeline,
                rng,
                event_side=target.side,
                event_card=target.card,
                performed={transition: 1},
                casts=casts,
                trigger_depth=trigger_depth + 1,
            )
    elif rule.action_type == "TActionCardForceUse":
        for target in targets:
            if target.card.placement_id in target.side.cooldowns:
                scheduler.request_item_use(target, now, reason="forceuse", forced=True)
        performed["forceuse"] = performed.get("forceuse", 0) + len(targets)
    elif rule.action_type == "TActionCardReload":
        for target in targets:
            ammo = target.side.ammo.get(target.card.placement_id)
            if not ammo:
                continue
            ammo["current"] = min(float(ammo.get("max", 0.0)), float(ammo.get("current", 0.0)) + amount)
            ammo["empty"] = ammo["current"] <= 0
            timeline.append(_battle_event(now, source_side.name, target.side.name, "reload", card_label(source), amount, target=card_label(target.card)))
        performed["reload"] = performed.get("reload", 0) + len(targets)
    elif rule.action_type in {"TActionCardAddTagsList", "TActionCardAddTagsRandom", "TActionCardAddTagsBySource"}:
        added = 0
        for target in targets:
            tags = _runtime_tags_for_action(source_side, source, rule, amount, rng)
            if not tags:
                timeline.append(
                    _battle_event(
                        now,
                        source_side.name,
                        target.side.name,
                        "unsupported-runtime-tag-operation",
                        card_label(source),
                        0.0,
                        target=card_label(target.card),
                        action=rule.action_type,
                    )
                )
                continue
            if _add_runtime_tags(target, tags, rule.effect_id or source.placement_id, source_side, source, now, timeline):
                added += 1
        performed["tag"] = performed.get("tag", 0) + added
    elif rule.action_type == "TActionCardModifyAttribute":
        state_key = _runtime_state_key(rule.attribute_type)
        if state_key in RUNTIME_CARD_STATES:
            for target in targets:
                transition = _apply_runtime_card_state_attribute(
                    target,
                    source_side,
                    source,
                    rule,
                    amount,
                    now,
                    timeline,
                    duration_sec,
                )
                if transition:
                    performed[transition] = performed.get(transition, 0) + 1
                    _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
                    if transition in {"started_flying", "stopped_flying"}:
                        _run_state_triggered_rules(
                            player,
                            monster,
                            rules_by_source,
                            scheduler,
                            now,
                            timeline,
                            rng,
                            event_side=target.side,
                            event_card=target.card,
                            performed={transition: 1},
                            casts=casts,
                            trigger_depth=trigger_depth + 1,
                        )
            return
        if _cooldown_attribute_supported(rule.attribute_type):
            for target in targets:
                _add_cooldown_modifier(
                    target,
                    source_side,
                    source,
                    rule,
                    amount,
                    now,
                    scheduler,
                    timeline,
                    duration_sec,
                )
            return
        mapped = _normalize_battle_attribute(rule.attribute_type)
        if not mapped:
            return
        for target in targets:
            if target.card.placement_id in target.side.bonus.get(mapped, {}):
                current = target.side.bonus[mapped][target.card.placement_id]
                target.side.bonus[mapped][target.card.placement_id] = apply_attribute_operation(current, amount, rule.operation)
                timeline.append(_battle_event(now, source_side.name, target.side.name, "modify-attribute", card_label(source), amount, target=card_label(target.card), attribute=mapped))
    else:
        target_side = _player_target_side(player, monster, source_side, rule)
        if rule.action_type == "TActionPlayerRageApply":
            performed["rage"] = performed.get("rage", 0) + max(1, casts)
            if _apply_rage(target_side, amount, now, card_label(source), timeline):
                performed["enraged"] = performed.get("enraged", 0) + 1
                _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
                event_card = _first_event_card(target_side)
                if event_card is not None:
                    _run_state_triggered_rules(
                        player,
                        monster,
                        rules_by_source,
                        scheduler,
                        now,
                        timeline,
                        rng,
                        event_side=target_side,
                        event_card=event_card,
                        performed={"enraged": 1},
                        casts=casts,
                        trigger_depth=trigger_depth + 1,
                    )
            return
        _apply_player_action(source_side, target_side, source, rule, amount, now, timeline, duration_sec)
        key = action_performed_key(rule.action_type)
        if key:
            performed[key] = performed.get(key, 0) + max(1, casts)


def _apply_flying_action(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    now: float,
    timeline: list[dict[str, Any]],
    duration_sec: float,
) -> str:
    action_type = str(rule.action_type or "")
    duration = rule.duration_sec if math.isfinite(rule.duration_sec) and rule.duration_sec > 0 else duration_sec
    if action_type == "TActionCardFlyingStart":
        started = _add_runtime_state(
            target.side,
            target.card,
            "flying",
            value=1.0,
            source_id=rule.effect_id or source.placement_id,
            source_label=card_label(source),
            source_side=source_side,
            now=now,
            duration_sec=duration,
            timeline=timeline,
        )
        return "started_flying" if started else ""
    if action_type == "TActionCardFlyingStop":
        stopped = _remove_runtime_state(
            target.side,
            target.card,
            "flying",
            source_label=card_label(source),
            source_side=source_side,
            now=now,
            timeline=timeline,
        )
        return "stopped_flying" if stopped else ""
    if _has_runtime_state(target.side, target.card, "flying", now):
        stopped = _remove_runtime_state(
            target.side,
            target.card,
            "flying",
            source_label=card_label(source),
            source_side=source_side,
            now=now,
            timeline=timeline,
        )
        return "stopped_flying" if stopped else ""
    started = _add_runtime_state(
        target.side,
        target.card,
        "flying",
        value=1.0,
        source_id=rule.effect_id or source.placement_id,
        source_label=card_label(source),
        source_side=source_side,
        now=now,
        duration_sec=duration,
        timeline=timeline,
    )
    return "started_flying" if started else ""


def _apply_runtime_card_state_attribute(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    amount: float,
    now: float,
    timeline: list[dict[str, Any]],
    duration_sec: float,
) -> str:
    state_key = _runtime_state_key(rule.attribute_type)
    duration = rule.duration_sec if math.isfinite(rule.duration_sec) and rule.duration_sec > 0 else duration_sec
    operation = str(rule.operation or "Add").lower()
    if amount > 0 and operation not in {"subtract", "remove"}:
        entered = _add_runtime_state(
            target.side,
            target.card,
            state_key,
            value=amount,
            source_id=rule.effect_id or source.placement_id,
            source_label=card_label(source),
            source_side=source_side,
            now=now,
            duration_sec=duration,
            timeline=timeline,
        )
        if state_key == "flying":
            return "started_flying" if entered else ""
        return state_key if entered else ""
    exited = _remove_runtime_state(
        target.side,
        target.card,
        state_key,
        source_label=card_label(source),
        source_side=source_side,
        now=now,
        timeline=timeline,
    )
    if state_key == "flying":
        return "stopped_flying" if exited else ""
    return f"{state_key}_ended" if exited else ""


def _apply_rage(
    side: BattleSide,
    amount: float,
    now: float,
    source: str,
    timeline: list[dict[str, Any]],
) -> bool:
    if amount <= 0 or side.health <= 0:
        return False
    condition_before = _condition_snapshot(side)
    before = side.rage
    side.rage_gained_total += amount
    if not side.is_enraged:
        side.rage = min(side.rage_max, side.rage + amount)
    timeline.append(
        _battle_event(
            now,
            side.name,
            side.name,
            "rage-gained",
            source,
            amount,
            old_rage=before,
            new_rage=side.rage,
            rage_max=side.rage_max,
            rage_gained_total=side.rage_gained_total,
        )
    )
    if side.is_enraged or before + amount < side.rage_max:
        _emit_condition_edges(side, condition_before, now, timeline)
        return False
    side.rage = 0.0
    side.is_enraged = True
    _add_runtime_state(
        side,
        None,
        "enraged",
        value=1.0,
        source_id=f"{side.name}:enraged:{now:.6f}",
        source_label=source,
        source_side=side,
        now=now,
        duration_sec=side.enraged_duration_sec,
        timeline=timeline,
    )
    _emit_condition_edges(side, condition_before, now, timeline)
    return True


def _apply_player_action(
    source_side: BattleSide,
    target_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    amount: float,
    now: float,
    timeline: list[dict[str, Any]],
    duration_sec: float,
) -> None:
    if rule.action_type == "TActionPlayerDamage":
        _apply_damage(source_side, target_side, amount, now, card_label(source), "damage", timeline)
    elif rule.action_type == "TActionPlayerBurnApply":
        target_side.burn_stack += amount
        timeline.append(_battle_event(now, source_side.name, target_side.name, "burn-apply", card_label(source), amount))
    elif rule.action_type == "TActionPlayerPoisonApply":
        target_side.poison_stack += amount
        timeline.append(_battle_event(now, source_side.name, target_side.name, "poison-apply", card_label(source), amount))
    elif rule.action_type == "TActionPlayerShieldApply":
        target_side.shield += amount
        timeline.append(_battle_event(now, source_side.name, target_side.name, "shield", card_label(source), amount))
    elif rule.action_type in {"TActionPlayerHealApply", "TActionPlayerHeal"}:
        _apply_heal(target_side, amount, now, card_label(source), timeline)
    elif rule.action_type == "TActionPlayerRegenApply":
        target_side.regen_stack += amount
        timeline.append(_battle_event(now, source_side.name, target_side.name, "regen-apply", card_label(source), amount))
    elif rule.action_type == "TActionPlayerRageApply":
        _apply_rage(target_side, amount, now, card_label(source), timeline)
    elif rule.action_type == "TActionPlayerModifyAttribute" and rule.attribute_type == "PercentDamageReduction":
        target_side.damage_reduction.append(
            {
                "start": now,
                "duration": rule.duration_sec if rule.duration_sec > 0 else max(0.0, duration_sec - now),
                "value": amount,
            }
        )
        timeline.append(_battle_event(now, source_side.name, target_side.name, "damage-reduction", card_label(source), amount))
    elif rule.action_type == "TActionPlayerModifyAttribute" and _max_health_attribute_supported(rule.attribute_type):
        _apply_max_health_modifier(source_side, target_side, source, rule, amount, now, timeline)


def _max_health_attribute_supported(attribute_type: str) -> bool:
    return str(attribute_type or "").lower() in {"maxhealth", "healthmax", "maximumhealth"}


def _apply_max_health_modifier(
    source_side: BattleSide,
    target_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    amount: float,
    now: float,
    timeline: list[dict[str, Any]],
) -> None:
    condition_before = _condition_snapshot(target_side)
    before_max = target_side.max_health
    before_health = target_side.health
    target_side.max_health = max(1.0, apply_attribute_operation(target_side.max_health, amount, rule.operation))
    target_side.max_health_modifiers.append(
        {
            "source": card_label(source),
            "amount": amount,
            "operation": rule.operation,
            "start": now,
            "duration": rule.duration_sec,
        }
    )
    if target_side.health > target_side.max_health:
        target_side.health = target_side.max_health
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target_side.name,
            "max-health-changed",
            card_label(source),
            target_side.max_health - before_max,
            old_max_health=before_max,
            new_max_health=target_side.max_health,
        )
    )
    if target_side.health != before_health:
        timeline.append(
            _battle_event(
                now,
                target_side.name,
                target_side.name,
                "health-changed",
                card_label(source),
                target_side.health - before_health,
                before_health=before_health,
                after_health=target_side.health,
                reason="max-health-clamp",
            )
        )
    _emit_condition_edges(target_side, condition_before, now, timeline)


def _resolve_battle_targets(
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
    trigger_card: PlacedCard,
    rule: Any,
    rng: Callable[[], float] | None,
) -> list[BattleCardRef]:
    target_side = _card_target_side(source_side, other_side, rule)
    cards = target_side.cards
    pool = targetable_cards(cards)

    def match(card: PlacedCard) -> bool:
        if rule.target_exclude_self and target_side is source_side and card.placement_id == source.placement_id:
            return False
        if rule.target_condition.not_trigger_source and card.placement_id == trigger_card.placement_id:
            return False
        return _battle_card_matches(BattleCardRef(target_side, card), rule.target_condition)

    if rule.target_type == "TTargetCardSelf":
        return [BattleCardRef(source_side, source)] if match(source) else []
    if rule.target_type == "TTargetCardSection":
        return [BattleCardRef(target_side, card) for card in pool if match(card)]
    if rule.target_type == "TTargetCardXMost":
        chosen = pick_x_most([card for card in pool if match(card)], rule.target_mode or "RightMostCard")
        return [BattleCardRef(target_side, chosen)] if chosen else []
    if rule.target_type == "TTargetCardRandom":
        candidates = [card for card in pool if match(card)]
        count = min(len(candidates), max(1, int(rule.target_count or 1)))
        out: list[BattleCardRef] = []
        for _ in range(count):
            if not candidates:
                break
            index = 0 if rng is None else max(0, min(len(candidates) - 1, int(rng() * len(candidates))))
            out.append(BattleCardRef(target_side, candidates.pop(index)))
        return out
    if target_side is not source_side:
        candidates = [card for card in pool if match(card)]
        return [BattleCardRef(target_side, candidates[0])] if candidates else []

    left = next((card for card in pool if card.start + (card.width or 1) == source.start), None)
    right = next((card for card in pool if card.start == source.start + (source.width or 1)), None)
    if rule.target_mode == "LeftCard":
        return [BattleCardRef(source_side, left)] if left and match(left) else []
    if rule.target_mode == "RightCard":
        return [BattleCardRef(source_side, right)] if right and match(right) else []
    if rule.target_mode == "Neighbor":
        return [BattleCardRef(source_side, card) for card in (left, right) if card and match(card)]
    if rule.target_mode == "AllRightCards":
        return [
            BattleCardRef(source_side, card)
            for card in sorted(pool, key=lambda item: item.start)
            if card.start >= source.start + (source.width or 1) and match(card)
        ]
    return [BattleCardRef(source_side, source)] if rule.target_include_origin and match(source) else []


def _card_target_side(source_side: BattleSide, other_side: BattleSide, rule: Any) -> BattleSide:
    target_text = f"{rule.target_section} {rule.target_mode}".lower()
    return other_side if "opponent" in target_text else source_side


def _player_target_side(
    player: BattleSide,
    monster: BattleSide,
    source_side: BattleSide,
    rule: Any,
) -> BattleSide:
    return _opponent(player, monster, source_side) if str(rule.target_mode).lower() == "opponent" else source_side


def _battle_trigger_matches(
    source_side: BattleSide,
    fired_side: BattleSide,
    source: PlacedCard,
    fired: PlacedCard,
    rule: Any,
    performed: dict[str, float],
) -> bool:
    lower = str(rule.trigger_type or "").lower()
    if not lower or rule.trigger_type == "TTriggerOnCardFired":
        if not rule.trigger_subject_type and not rule.trigger_subject_mode and not rule.trigger_condition.include_tags:
            return source_side is fired_side and source.placement_id == fired.placement_id
    if "itemused" in lower or rule.trigger_type == "TTriggerOnCardFired":
        return _trigger_side_matches(source_side, fired_side, rule) and _battle_card_matches(BattleCardRef(fired_side, fired), rule.trigger_condition)
    if "cardstartedflying" in lower:
        return (
            performed.get("started_flying", 0) > 0
            and _trigger_side_matches(source_side, fired_side, rule)
            and _battle_card_matches(BattleCardRef(fired_side, fired), rule.trigger_condition)
        )
    if "cardstoppedflying" in lower:
        return (
            performed.get("stopped_flying", 0) > 0
            and _trigger_side_matches(source_side, fired_side, rule)
            and _battle_card_matches(BattleCardRef(fired_side, fired), rule.trigger_condition)
        )
    if "playerenraged" in lower:
        return performed.get("enraged", 0) > 0 and _trigger_side_matches(source_side, fired_side, rule)
    if "playerenrageended" in lower:
        return performed.get("enrage_ended", 0) > 0 and _trigger_side_matches(source_side, fired_side, rule)
    if "playerattributechanged" in lower:
        attribute = str(rule.trigger_attribute_changed or "").lower()
        change = str(rule.trigger_change_type or "").lower()
        if attribute == "rage" and performed.get("rage", 0) > 0 and (not change or change == "gain"):
            return _trigger_side_matches(source_side, fired_side, rule)
    performed_map = {
        "performedslow": "slow",
        "performedhaste": "haste",
        "performedfreeze": "freeze",
        "performedflying": "started_flying",
        "performedrage": "rage",
        "performedburn": "burn",
        "performedpoison": "poison",
        "performeddamage": "damage",
        "performedshield": "shield",
        "performedheal": "heal",
        "performedregen": "regen",
        "performedreload": "reload",
        "cardcritted": "crit",
        "critted": "crit",
    }
    for token, key in performed_map.items():
        if token in lower:
            return (
                performed.get(key, 0) > 0
                and _trigger_side_matches(source_side, fired_side, rule)
                and _battle_card_matches(BattleCardRef(fired_side, fired), rule.trigger_condition)
            )
    return False


def _trigger_side_matches(source_side: BattleSide, event_side: BattleSide, rule: Any) -> bool:
    mode = str(rule.trigger_subject_mode or "").lower()
    if "opponent" in mode:
        return source_side is not event_side
    return source_side is event_side


def _process_burn_tick(
    player: BattleSide,
    monster: BattleSide,
    now: float,
    timeline: list[dict[str, Any]],
    config: BurnConfig = DEFAULT_BURN_CONFIG,
) -> None:
    for side in (player, monster):
        if side.health <= 0 or side.burn_stack <= 0:
            continue
        attacker = _opponent(player, monster, side)
        damage = burn_tick_damage_amount(side.burn_stack, side.shield, config)
        _apply_damage(attacker, side, damage, now, "burn", "burn-tick", timeline, raw_amount=side.burn_stack)
        old_stack = side.burn_stack
        side.burn_stack = max(0.0, side.burn_stack - max(0, int(config.decay_per_tick)))
        timeline.append(
            _battle_event(
                now,
                side.name,
                side.name,
                "burn-decayed",
                "burn",
                old_stack - side.burn_stack,
                old_stack=old_stack,
                new_stack=side.burn_stack,
            )
        )


def burn_tick_damage_amount(stack: float, shield: float, config: BurnConfig = DEFAULT_BURN_CONFIG) -> float:
    if stack <= 0:
        return 0.0
    damage = stack * config.shield_damage_multiplier if shield > 0 else stack
    rounding = str(config.rounding or "ceil").lower()
    if rounding == "floor":
        return float(math.floor(damage))
    if rounding == "round":
        return float(round(damage))
    return float(math.ceil(damage))


def _process_second_tick(
    player: BattleSide,
    monster: BattleSide,
    now: float,
    timeline: list[dict[str, Any]],
) -> None:
    for side in (player, monster):
        if side.health <= 0:
            continue
        poison = max(0.0, side.poison_stack)
        regen = max(0.0, side.regen_stack)
        if poison <= 0 and regen <= 0:
            continue
        net = poison - regen
        timeline.append(
            _battle_event(
                now,
                side.name,
                side.name,
                "periodic-health-resolution",
                "poison-regen",
                net,
                poison=poison,
                regen=regen,
                net_damage=max(0.0, net),
                net_heal=max(0.0, -net),
            )
        )
        if net > 0:
            _apply_damage(
                _opponent(player, monster, side),
                side,
                net,
                now,
                "poison",
                "poison-tick",
                timeline,
                bypass_shield=True,
                raw_amount=poison,
            )
        elif net < 0:
            _apply_heal(
                side,
                -net,
                now,
                "regen",
                timeline,
                reason="regen",
                event_kind="regen-heal",
                allow_cleanse=False,
            )


def _apply_damage(
    source_side: BattleSide,
    target_side: BattleSide,
    amount: float,
    now: float,
    source: str,
    kind: str,
    timeline: list[dict[str, Any]],
    *,
    bypass_shield: bool = False,
    raw_amount: float | None = None,
) -> float:
    if amount <= 0 or target_side.health <= 0:
        return 0.0
    condition_before = _condition_snapshot(target_side)
    reduced = amount * (1.0 - _damage_reduction_percent(target_side, now) / 100.0)
    absorbed = 0.0 if bypass_shield else min(target_side.shield, reduced)
    if not bypass_shield:
        target_side.shield -= absorbed
    actual = max(0.0, reduced - absorbed)
    target_side.health -= actual
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target_side.name,
            kind,
            source,
            actual,
            raw=amount if raw_amount is None else raw_amount,
            reduced=reduced,
            absorbed=absorbed,
            bypass_shield=bypass_shield,
            before_health=target_side.health + actual,
            after_health=max(0.0, target_side.health),
        )
    )
    if target_side.health <= 0:
        timeline.append(_battle_event(now, source_side.name, target_side.name, "would-die", source, actual))
        revived = _consume_battle_revive(target_side)
        if revived is not None:
            target_side.health = min(target_side.max_health, revived)
            target_side.shield = 0.0
            timeline.append(_battle_event(now, target_side.name, target_side.name, "revive", "revive", target_side.health, revive_consumed=True))
        else:
            timeline.append(_battle_event(now, target_side.name, target_side.name, "player-died", source, 0.0))
    _emit_condition_edges(target_side, condition_before, now, timeline)
    return actual


def _apply_heal(
    side: BattleSide,
    amount: float,
    now: float,
    source: str,
    timeline: list[dict[str, Any]],
    *,
    reason: str = "",
    cleanse_config: HealCleanseConfig = DEFAULT_HEAL_CLEANSE_CONFIG,
    overheal_config: OverhealConfig = DEFAULT_OVERHEAL_CONFIG,
    event_kind: str = "heal",
    allow_cleanse: bool = True,
) -> float:
    if amount <= 0 or side.health <= 0:
        return 0.0
    condition_before = _condition_snapshot(side)
    before = side.health
    actual_heal = min(amount, max(0.0, side.max_health - before))
    overheal = max(0.0, amount - actual_heal)
    side.health = min(side.max_health, side.health + actual_heal)
    if actual_heal > 0:
        event = _battle_event(
            now,
            side.name,
            side.name,
            event_kind,
            source,
            actual_heal,
            requested_heal=amount,
            actual_heal=actual_heal,
            overheal_amount=overheal,
            before_health=before,
            after_health=side.health,
        )
        if reason:
            event["reason"] = reason
        timeline.append(event)
    if overheal > 0 and _overheal_allowed(reason, overheal_config):
        event = _battle_event(
            now,
            side.name,
            side.name,
            "overheal",
            source,
            overheal,
            requested_heal=amount,
            actual_heal=actual_heal,
            overheal_amount=overheal,
            before_health=before,
            after_health=side.health,
        )
        if reason:
            event["reason"] = reason
        timeline.append(event)
    if allow_cleanse:
        _apply_heal_cleanse(side, now, source, actual_heal, timeline, reason=reason, config=cleanse_config)
    _emit_condition_edges(side, condition_before, now, timeline)
    return actual_heal


def _overheal_allowed(reason: str, config: OverhealConfig) -> bool:
    if reason == "lifesteal":
        return config.lifesteal_triggers_overheal
    if reason == "regen":
        return config.regen_triggers_overheal
    return config.normal_heal_triggers_overheal


def _apply_heal_cleanse(
    side: BattleSide,
    now: float,
    source: str,
    actual_heal: float,
    timeline: list[dict[str, Any]],
    *,
    reason: str = "",
    config: HealCleanseConfig = DEFAULT_HEAL_CLEANSE_CONFIG,
) -> None:
    if config.require_actual_heal and actual_heal <= 0:
        return
    if reason == "lifesteal" and not config.lifesteal_triggers_cleanse:
        return
    burn_before = side.burn_stack
    burn_cleanse = status_cleanse_amount(burn_before, config, actual_heal=actual_heal)
    if burn_cleanse > 0:
        side.burn_stack = max(0.0, burn_before - burn_cleanse)
        timeline.append(
            _battle_event(
                now,
                side.name,
                side.name,
                "burn-cleansed",
                source,
                burn_cleanse,
                old_stack=burn_before,
                new_stack=side.burn_stack,
            )
        )
    poison_before = side.poison_stack
    poison_cleanse = status_cleanse_amount(poison_before, config, actual_heal=actual_heal)
    if poison_cleanse > 0:
        side.poison_stack = max(0.0, poison_before - poison_cleanse)
        timeline.append(
            _battle_event(
                now,
                side.name,
                side.name,
                "poison-cleansed",
                source,
                poison_cleanse,
                old_stack=poison_before,
                new_stack=side.poison_stack,
            )
        )


def _damage_reduction_percent(side: BattleSide, now: float) -> float:
    total = 0.0
    for reduction in side.damage_reduction:
        start = float(reduction.get("start") or 0.0)
        duration = float(reduction.get("duration") or 0.0)
        if now + 1e-6 < start:
            continue
        if math.isfinite(duration) and now > start + duration + 1e-6:
            continue
        total += max(0.0, float(reduction.get("value") or 0.0))
    return max(0.0, min(100.0, total))


def _condition_snapshot(side: BattleSide) -> dict[str, bool]:
    return {
        "below_half_health": side.max_health > 0 and side.health <= side.max_health / 2.0,
        "has_shield": side.shield > 0,
        "is_burning": side.burn_stack > 0,
        "is_poisoned": side.poison_stack > 0,
        "is_enraged": side.is_enraged,
    }


def _emit_condition_edges(
    side: BattleSide,
    before: dict[str, bool],
    now: float,
    timeline: list[dict[str, Any]],
) -> None:
    after = _condition_snapshot(side)
    for name in sorted(set(before) | set(after)):
        was_active = bool(before.get(name, False))
        is_active = bool(after.get(name, False))
        if was_active == is_active:
            continue
        kind = "condition-entered" if is_active else "condition-exited"
        timeline.append(
            _battle_event(
                now,
                side.name,
                side.name,
                kind,
                name,
                1.0 if is_active else 0.0,
                condition=name,
                before=was_active,
                after=is_active,
            )
        )
    side.condition_state = after


def _consume_battle_revive(side: BattleSide) -> float | None:
    if not side.revives:
        return None
    event = side.revives.pop(0)
    value = max(0.0, float(event.get("value") or 0.0))
    if str(event.get("mode") or "") == "health_fraction":
        return side.max_health * value
    return value


def _battle_winner(player: BattleSide, monster: BattleSide) -> str:
    if player.health <= 0 and monster.health <= 0:
        return "draw"
    if monster.health <= 0:
        return "player"
    if player.health <= 0:
        return "monster"
    return ""


def _battle_outcome(
    player: BattleSide,
    monster: BattleSide,
    duration: float,
    timeline: list[dict[str, Any]],
) -> TwoSidedBattleOutcome:
    winner = _battle_winner(player, monster) or "draw"
    ordered_timeline = sorted(
        timeline,
        key=lambda event: (
            float(event.get("time") or 0.0),
            _timeline_priority(str(event.get("kind") or "")),
        ),
    )
    return TwoSidedBattleOutcome(
        winner=winner,
        duration=duration,
        player_remaining_health=max(0.0, player.health),
        monster_remaining_health=max(0.0, monster.health),
        player_damage=max(0.0, monster.max_health - max(0.0, monster.health)),
        monster_damage=max(0.0, player.max_health - max(0.0, player.health)),
        timeline=ordered_timeline,
    )


def _opponent(player: BattleSide, monster: BattleSide, side: BattleSide) -> BattleSide:
    return monster if side is player else player


def _battle_event(
    time_value: float,
    source_side: str,
    target_side: str,
    kind: str,
    source: str,
    value: float,
    **extra: Any,
) -> dict[str, Any]:
    event = {
        "time": round(float(time_value), 6),
        "side": source_side,
        "target_side": target_side,
        "kind": kind,
        "source": source,
        "value": value,
    }
    event.update(extra)
    return event


def _timeline_priority(kind: str) -> int:
    return {
        "burn-tick": EVENT_PRIORITY["STATUS_TICK"],
        "poison-tick": EVENT_PRIORITY["STATUS_TICK"],
        "heal": EVENT_PRIORITY["HEAL_RESOLVED"],
        "overheal": EVENT_PRIORITY["HEAL_RESOLVED"],
        "burn-cleansed": EVENT_PRIORITY["BURN_CLEANSED"],
        "poison-cleansed": EVENT_PRIORITY["POISON_CLEANSED"],
        "burn-decayed": EVENT_PRIORITY["STATUS_TICK"],
        "cooldown-ready": EVENT_PRIORITY["COOLDOWN_READY"],
        "cooldown-modified": EVENT_PRIORITY["COOLDOWN_MODIFIER_EXPIRED"],
        "charge-port-triggered": EVENT_PRIORITY["CHARGE_TRIGGERED"],
        "charge-resolved": EVENT_PRIORITY["CHARGE_RESOLVED"],
        "item-use-requested": EVENT_PRIORITY["ITEM_USE_REQUESTED"],
        "multicast-requested": EVENT_PRIORITY["MULTICAST_REQUESTED"],
        "item-used": EVENT_PRIORITY["ITEM_USED"],
        "would-die": EVENT_PRIORITY["DEATH_CHECK"],
        "revive": EVENT_PRIORITY["DEATH_CHECK"],
        "player-died": EVENT_PRIORITY["DEATH_CHECK"],
        "trigger-count-consumed": 25,
        "trigger-blocked": 25,
        "condition-entered": 35,
        "condition-exited": 35,
        "max-health-changed": 36,
        "health-changed": 37,
    }.get(kind, 80)


def _evaluate_single_monster(
    *,
    data: dict[str, Any],
    player: SideInput,
    monster_choice: dict[str, Any],
    simulations: int,
    duration_sec: float,
    seed: int | None,
    simulate_fn: Callable[..., CombatSummary],
) -> dict[str, Any]:
    use_two_sided = simulate_fn is simulate_combat
    monster = _build_side_input(data, monster_choice, side="monster")
    unsupported_cards = [
        *_skipped_to_unsupported(player.skipped_cards, "player"),
        *_skipped_to_unsupported(monster.skipped_cards, "monster"),
    ]
    unsupported_skills = [
        *_skills_to_unsupported(player.skills, "player"),
        *_skills_to_unsupported(monster.skills, "monster"),
    ]
    unsupported_effects = [
        *_unsupported_effects_for_cards(player.placements, "player"),
        *_unsupported_effects_for_cards(monster.placements, "monster"),
    ]
    warnings = _dedupe(
        [
            *player.warnings,
            *monster.warnings,
            "player_health_missing" if player.health is None else "",
            "monster_health_missing" if monster.health is None else "",
            "player_board_missing" if not targetable_cards(player.placements) else "",
            "monster_board_missing" if not targetable_cards(monster.placements) else "",
            (
                "current_simulator_is_bounded_two_sided_timeline"
                if use_two_sided
                else "current_simulator_is_damage_race_approximation"
            ),
            *_approximation_warnings_for_cards(player.placements, "player"),
            *_approximation_warnings_for_cards(monster.placements, "monster"),
        ]
    )
    input_complete = (
        player.health is not None
        and monster.health is not None
        and bool(targetable_cards(player.placements))
        and bool(targetable_cards(monster.placements))
    )
    support = _support_coverage(
        player.placements,
        monster.placements,
        unsupported_cards=unsupported_cards,
        unsupported_skills=unsupported_skills,
        unsupported_effects=unsupported_effects,
    )
    key_fields = {
        "monster_id": monster_choice.get("monster_id")
        or monster_choice.get("id")
        or monster_choice.get("source_id")
        or monster_choice.get("template_id")
        or monster_choice.get("name"),
        "monster_name": monster_choice.get("monster_name") or monster_choice.get("name"),
        "support_coverage": support,
        "unsupported_cards": unsupported_cards,
        "unsupported_skills": unsupported_skills,
        "unsupported_effects": unsupported_effects,
        "warnings": warnings,
    }
    if not input_complete:
        return {
            **key_fields,
            "status": "unsupported",
            "confidence": "low",
            "estimated_win_rate": None,
            "win_rate_range": None,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "simulations_completed": 0,
            "average_battle_duration": None,
            "average_remaining_health_on_win": None,
        }

    wins = losses = draws = failures = 0
    durations: list[float] = []
    remaining_on_win: list[float] = []
    last_timeline: list[dict[str, Any]] = []
    for index in range(simulations):
        rng = random.Random((seed if seed is not None else 0) + index).random
        try:
            if use_two_sided:
                battle = _simulate_two_sided_battle(
                    player_cards=player.placements,
                    monster_cards=monster.placements,
                    player_health=float(player.health or 0),
                    monster_health=float(monster.health or 0),
                    duration_sec=duration_sec,
                    rng=rng,
                    player_attributes=player.attributes,
                    monster_attributes=monster.attributes,
                )
                outcome = {
                    "winner": battle.winner,
                    "duration": battle.duration,
                    "monster_damage": battle.monster_damage,
                    "player_remaining_health": battle.player_remaining_health,
                    "monster_remaining_health": battle.monster_remaining_health,
                }
                last_timeline = battle.timeline
            else:
                player_summary = simulate_fn(
                    player.placements,
                    duration_sec=duration_sec,
                    random_trials=1,
                    rng=rng,
                )
                monster_summary = simulate_fn(
                    monster.placements,
                    duration_sec=duration_sec,
                    random_trials=1,
                    rng=rng,
                )
                outcome = _race_outcome(
                    player_summary=player_summary,
                    monster_summary=monster_summary,
                    player_health=float(player.health or 0),
                    monster_health=float(monster.health or 0),
                    duration_sec=duration_sec,
                )
        except Exception as exc:  # pragma: no cover - exact exception type is simulator-dependent
            failures += 1
            warnings.append(f"simulation_failed:{type(exc).__name__}")
            continue
        if outcome["winner"] == "player":
            wins += 1
            remaining_on_win.append(float(outcome.get("player_remaining_health") or 0.0))
        elif outcome["winner"] == "monster":
            losses += 1
        else:
            draws += 1
        durations.append(float(outcome["duration"]))

    completed = wins + losses + draws
    confidence = _confidence(
        input_complete=input_complete,
        support_coverage=support,
        unsupported_skills=unsupported_skills,
        unsupported_effects=unsupported_effects,
        failures=failures,
        requested=simulations,
    )
    status = (
        "error"
        if completed == 0
        else "ok"
        if confidence == "high"
        else "partial"
        if confidence == "medium"
        else "unsupported"
    )
    win_rate = wins / completed if completed else None
    precise_allowed = confidence in {"high", "medium"} and completed > 0
    interval = _wilson_interval(wins, completed) if precise_allowed else None
    return {
        **key_fields,
        "status": status,
        "estimated_win_rate": round(win_rate, 4) if precise_allowed and win_rate is not None else None,
        "win_rate_range": interval,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "simulations_completed": completed,
        "simulations_failed": failures,
        "average_battle_duration": (
            round(sum(durations) / len(durations), 3) if durations else None
        ),
        "average_remaining_health_on_win": (
            round(sum(remaining_on_win) / len(remaining_on_win), 3)
            if remaining_on_win
            else None
        ),
        "battle_model": "two_sided_timeline" if use_two_sided else "one_sided_damage_race",
        "sample_timeline": last_timeline[:40],
        "battle_log": last_timeline,
        "confidence": confidence,
        "warnings": _dedupe(warnings),
    }


def _build_side_input(data: dict[str, Any], state: Any, *, side: str) -> SideInput:
    state_dict = state if isinstance(state, dict) else getattr(state, "__dict__", {})
    attributes = dict(_state_value(state, "attributes") or {})
    for key in ("RageMax", "EnragedDurationMax"):
        value = _state_value(state, key)
        if value is not None:
            attributes[key] = value
    health = _optional_float(
        _state_value(state, "combat_health")
        or _state_value(state, "health")
        or _state_value(state, "max_health")
    )
    entries = _state_value(state, "board_items")
    if not isinstance(entries, list) or not entries:
        entries = _state_value(state, "items")
    if not isinstance(entries, list) or not entries:
        entries = _state_value(state, "owned_items")
    skills = _state_value(state, "skills")
    if not isinstance(skills, list):
        skills = []
    state_for_placement = {"board_items": entries or [], "skills": skills}
    placements, skipped = build_current_board_placements(data, state_for_placement, include_skills=True)
    warnings = []
    if health is None:
        warnings.append(f"{side}_health_missing")
    if not targetable_cards(placements):
        warnings.append(f"{side}_board_missing")
    if state_dict and entries is None:
        warnings.append(f"{side}_items_missing")
    skipped_skills = [
        item
        for item in skipped
        if str(item.get("card_type") or item.get("type") or "").lower() == "skill"
    ]
    skipped_cards = [item for item in skipped if item not in skipped_skills]
    return SideInput(
        health=health,
        placements=placements,
        skipped_cards=skipped_cards,
        skills=skipped_skills,
        warnings=warnings,
        attributes=attributes,
    )


def _race_outcome(
    *,
    player_summary: CombatSummary,
    monster_summary: CombatSummary,
    player_health: float,
    monster_health: float,
    duration_sec: float,
) -> dict[str, Any]:
    player_time = _survival_time_against(
        incoming_summary=player_summary,
        defender_summary=monster_summary,
        defender_health=monster_health,
    )
    monster_time = _survival_time_against(
        incoming_summary=monster_summary,
        defender_summary=player_summary,
        defender_health=player_health,
    )
    if player_time is not None and (monster_time is None or player_time < monster_time):
        winner = "player"
        duration = player_time
    elif monster_time is not None and (player_time is None or monster_time < player_time):
        winner = "monster"
        duration = monster_time
    else:
        winner = "draw"
        duration = player_time if player_time is not None else duration_sec
    return {
        "winner": winner,
        "duration": duration,
        "monster_damage": _damage_done_by_time(monster_summary, float(duration)),
        "player_remaining_health": _remaining_health_against(
            incoming_summary=monster_summary,
            defender_summary=player_summary,
            defender_health=player_health,
            at_time=float(duration),
        ),
    }


def _damage_done_by_time(summary: CombatSummary, at_time: float) -> float:
    return sum(
        float(event.get("amount") or 0)
        for event in damage_timeline(summary)
        if float(event.get("time") or 0) <= at_time + 1e-6
    )


def _survival_time_against(
    *,
    incoming_summary: CombatSummary,
    defender_summary: CombatSummary,
    defender_health: float,
) -> float | None:
    health = defender_health
    shield = 0.0
    revives = _revive_pool(defender_summary)
    for event in _survival_events(incoming_summary, defender_summary):
        value = float(event.get("value") or event.get("amount") or 0)
        if value <= 0:
            continue
        kind = str(event.get("kind") or "")
        if kind == "shield":
            shield += value
        elif kind == "heal":
            health = min(defender_health, health + value)
        elif kind == "damage":
            value = _reduced_damage(value, defender_summary, float(event.get("time") or 0))
            absorbed = min(shield, value)
            shield -= absorbed
            health -= value - absorbed
            if health <= 0:
                revived_health = _consume_revive(revives, defender_health)
                if revived_health is not None:
                    health = min(defender_health, revived_health)
                    shield = 0.0
                    continue
                return float(event.get("time") or 0)
    return None


def _remaining_health_against(
    *,
    incoming_summary: CombatSummary,
    defender_summary: CombatSummary,
    defender_health: float,
    at_time: float,
) -> float:
    health = defender_health
    shield = 0.0
    revives = _revive_pool(defender_summary)
    for event in _survival_events(incoming_summary, defender_summary):
        if float(event.get("time") or 0) > at_time + 1e-6:
            break
        value = float(event.get("value") or event.get("amount") or 0)
        if value <= 0:
            continue
        kind = str(event.get("kind") or "")
        if kind == "shield":
            shield += value
        elif kind == "heal":
            health = min(defender_health, health + value)
        elif kind == "damage":
            value = _reduced_damage(value, defender_summary, float(event.get("time") or 0))
            absorbed = min(shield, value)
            shield -= absorbed
            health -= value - absorbed
            if health <= 0:
                revived_health = _consume_revive(revives, defender_health)
                if revived_health is None:
                    return 0.0
                health = min(defender_health, revived_health)
                shield = 0.0
    return max(0.0, health)


def _reduced_damage(amount: float, defender_summary: CombatSummary, at_time: float) -> float:
    reduction = 0.0
    for event in defender_summary.debug_timeline:
        if str(event.get("kind") or "") != "damage-reduction":
            continue
        start = float(event.get("time") or 0)
        duration = float(event.get("duration") or 0)
        if at_time + 1e-6 < start:
            continue
        if math.isfinite(duration) and at_time > start + duration + 1e-6:
            continue
        reduction += max(0.0, float(event.get("value") or 0))
    reduction = max(0.0, min(100.0, reduction))
    return amount * (1.0 - reduction / 100.0)


def _revive_pool(defender_summary: CombatSummary) -> list[dict[str, Any]]:
    return [
        dict(event)
        for event in sorted(defender_summary.debug_timeline, key=lambda item: float(item.get("time") or 0))
        if str(event.get("kind") or "") == "revive"
    ]


def _consume_revive(revives: list[dict[str, Any]], defender_health: float) -> float | None:
    if not revives:
        return None
    event = revives.pop(0)
    value = max(0.0, float(event.get("value") or 0))
    if str(event.get("mode") or "") == "health_fraction":
        return defender_health * value
    return value


def _survival_events(
    incoming_summary: CombatSummary,
    defender_summary: CombatSummary,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for event in damage_timeline(incoming_summary):
        events.append(
            {
                "time": float(event.get("time") or 0),
                "kind": "damage",
                "value": float(event.get("amount") or 0),
            }
        )
    for event in defender_summary.debug_timeline:
        kind = str(event.get("kind") or "")
        if kind in {"shield", "heal"}:
            events.append(
                {
                    "time": float(event.get("time") or 0),
                    "kind": kind,
                    "value": float(event.get("value") or 0),
                }
            )
    priority = {"shield": 0, "heal": 1, "damage": 2}
    return sorted(events, key=lambda item: (float(item.get("time") or 0), priority.get(str(item.get("kind")), 9)))


def _unsupported_effects_for_cards(cards: list[PlacedCard], side: str) -> list[dict[str, Any]]:
    unsupported: list[dict[str, Any]] = []
    for card in cards:
        for row in effect_rows(card.card):
            action = get_field(row, "Action", default={}) or {}
            trigger = get_field(row, "Trigger", default={}) or {}
            action_nodes = expand_action_nodes(action)
            trigger_type = type_name(trigger)
            for action_path, action_node in action_nodes:
                action_type = type_name(action_node)
                if action_type and action_type not in SUPPORTED_ACTIONS:
                    unsupported.append(
                        {
                            "side": side,
                            "card": _card_name(card),
                            "effect": action_type,
                            "reason": "unsupported_action",
                            "action_path": action_path,
                        }
                    )
            if trigger_type and not _trigger_supported(trigger_type):
                unsupported.append(
                    {
                        "side": side,
                        "card": _card_name(card),
                        "effect": trigger_type,
                        "reason": "unsupported_trigger",
                    }
                )
    return unsupported


def _approximation_warnings_for_cards(cards: list[PlacedCard], side: str) -> list[str]:
    warnings: list[str] = []
    for card in cards:
        for row in effect_rows(card.card):
            action = get_field(row, "Action", default={}) or {}
            trigger = get_field(row, "Trigger", default={}) or {}
            action_type = type_name(action)
            trigger_type = type_name(trigger)
            if action_type == "TActionPlayerReviveHeal":
                warnings.append(f"{side}_revive_is_approximated")
            if (
                action_type in {"TActionPlayerModifyAttribute", "TAuraActionPlayerModifyAttribute"}
                and str(get_field(action, "AttributeType", default="")) == "PercentDamageReduction"
            ):
                warnings.append(f"{side}_damage_reduction_is_approximated")
            if "CardCritted" in trigger_type:
                warnings.append(f"{side}_crit_triggers_are_approximated")
    return _dedupe(warnings)


def _trigger_supported(trigger_type: str) -> bool:
    lower = trigger_type.lower()
    return "triggeror" in lower or any(token in lower for token in SUPPORTED_TRIGGER_TOKENS)


def _support_coverage(
    player_cards: list[PlacedCard],
    monster_cards: list[PlacedCard],
    *,
    unsupported_cards: list[dict[str, Any]],
    unsupported_skills: list[dict[str, Any]],
    unsupported_effects: list[dict[str, Any]],
) -> float:
    card_count = len(player_cards) + len(monster_cards)
    rule_count = sum(len(effect_rows(card.card)) for card in [*player_cards, *monster_cards])
    total_units = max(1, card_count + rule_count + len(unsupported_skills))
    unsupported_units = len(unsupported_cards) + len(unsupported_effects) + len(unsupported_skills)
    return round(max(0.0, 1.0 - unsupported_units / total_units), 4)


def _confidence(
    *,
    input_complete: bool,
    support_coverage: float,
    unsupported_skills: list[dict[str, Any]],
    unsupported_effects: list[dict[str, Any]],
    failures: int,
    requested: int,
) -> str:
    failure_rate = failures / max(1, requested)
    critical_effect = any(
        any(token in str(item.get("effect") or "").lower() for token in ("lifestealapply", "critdamage"))
        for item in unsupported_effects
    )
    if not input_complete or critical_effect or unsupported_skills or support_coverage < 0.8 or failure_rate > 0.34:
        return "low"
    if support_coverage < 1.0 or failure_rate > 0:
        return "medium"
    return "high"


def _wilson_interval(wins: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.96
    p = wins / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * ((p * (1 - p) + z * z / (4 * total)) / total) ** 0.5 / denom
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def _skipped_to_unsupported(skipped: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    return [
        {
            "side": side,
            "card": item.get("name") or item.get("template_id") or item.get("id"),
            "reason": item.get("reason") or "card_skipped",
        }
        for item in skipped
    ]


def _skills_to_unsupported(skills: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    return [
        {
            "side": side,
            "skill": item.get("name") or item.get("template_id") or item.get("id"),
            "reason": "skills_not_simulated_yet",
        }
        for item in skills
    ]


def _state_value(state: Any, name: str) -> Any:
    if isinstance(state, dict):
        return state.get(name)
    return getattr(state, name, None)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _card_name(card: PlacedCard) -> str:
    return str(card.card.get("name") or card.card.get("internal_name") or card.placement_id)


def _dedupe(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        key = json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, dict) else str(value)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _evaluation_cache_key(
    *,
    data: dict[str, Any],
    player_state: Any,
    monster_choices: list[dict[str, Any]],
    simulations: int,
    duration_sec: float,
    seed: int | None,
) -> str:
    payload = {
        "data_version": data.get("data_version"),
        "player": _jsonable_state(player_state),
        "monsters": monster_choices,
        "simulations": simulations,
        "duration_sec": duration_sec,
        "seed": seed,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


def _jsonable_state(state: Any) -> Any:
    if isinstance(state, dict):
        return state
    if hasattr(state, "__dict__"):
        return {
            key: value
            for key, value in vars(state).items()
            if not key.startswith("_")
        }
    return str(state)
