from __future__ import annotations

from copy import deepcopy
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
    action_path_sort_key,
    action_performed_key,
    apply_attribute_operation,
    build_current_board_placements,
    card_attr_with_bonus,
    card_enchantment,
    card_width,
    card_label,
    card_tags,
    clone_placed_cards,
    compare_number,
    CRIT_BONUS_ATTR_BY_AMOUNT_ATTR,
    crit_multiplier_for_casts,
    crit_multiplier_from_crits,
    burn_decay_amount,
    damage_timeline,
    effective_tier,
    effect_rows,
    expand_action_nodes,
    extract_condition_meta,
    get_card_ammo_max,
    get_card_cooldown_sec,
    get_attr_value_by_tier,
    has_runtime_snapshot_attribute,
    get_field,
    is_skill_card,
    item_timing_config,
    matches_card,
    normalize_enchantment,
    normalize_tag,
    normalize_offense_attr,
    pick_x_most,
    read_rules,
    set_attr_value_by_tier,
    simulate_combat,
    status_cleanse_amount,
    targetable_cards,
    template_index,
    type_name,
)


SUPPORTED_RULE_ACTIONS = {
    "TActionAnd",
    "TActionCardCharge",
    "TActionCardHaste",
    "TActionCardSlow",
    "TActionCardForceUse",
    "TActionCardReload",
    "TActionCardRepair",
    "TActionCardFreeze",
    "TActionCardDestroy",
    "TActionCardDisable",
    "TActionCardTransform",
    "TActionCardTransformDestroyed",
    "TActionCardEnchant",
    "TActionCardEnchantRandom",
    "TActionCardFlyingStart",
    "TActionCardFlyingStop",
    "TActionCardFlyingToggle",
    "TActionCardAddTagsList",
    "TActionCardAddTagsRandom",
    "TActionCardAddTagsBySource",
    "TActionCardBeginSandstorm",
    "TActionPlayerShieldApply",
    "TActionPlayerHealApply",
    "TActionPlayerHeal",
    "TActionPlayerPoisonApply",
    "TActionPlayerBurnRemove",
    "TActionPlayerPoisonRemove",
    "TActionPlayerRegenApply",
    "TActionPlayerRageApply",
    "TActionPlayerModifyAttribute",
    "TActionPlayerReviveHeal",
    "TActionCardModifyAttribute",
    "TAuraActionCardAddTagsList",
    "TAuraActionCardAddTagsBySource",
    "TAuraActionCardModifyAttribute",
    "TAuraActionPlayerModifyAttribute",
}
SUPPORTED_ON_USE_ACTIONS = {
    "TActionPlayerDamage",
    "TActionPlayerBurnApply",
    "TActionPlayerPoisonApply",
    "TActionPlayerBurnRemove",
    "TActionPlayerPoisonRemove",
    "TActionPlayerHeal",
    "TActionPlayerHealApply",
    "TActionPlayerRegenApply",
}
SELF_ON_USE_PLAYER_ACTIONS = SUPPORTED_ON_USE_ACTIONS | {
    "TActionPlayerShieldApply",
    "TActionPlayerRageApply",
}
SUPPORTED_ACTIONS = SUPPORTED_RULE_ACTIONS | SUPPORTED_ON_USE_ACTIONS
TWO_SIDED_RULE_ACTIONS = {
    "TActionAnd",
    "TActionCardCharge",
    "TActionCardHaste",
    "TActionCardSlow",
    "TActionCardForceUse",
    "TActionCardReload",
    "TActionCardRepair",
    "TActionCardFreeze",
    "TActionCardDestroy",
    "TActionCardDisable",
    "TActionCardTransform",
    "TActionCardTransformDestroyed",
    "TActionCardEnchant",
    "TActionCardEnchantRandom",
    "TActionCardFlyingStart",
    "TActionCardFlyingStop",
    "TActionCardFlyingToggle",
    "TActionCardAddTagsList",
    "TActionCardAddTagsRandom",
    "TActionCardAddTagsBySource",
    "TAuraActionCardAddTagsList",
    "TAuraActionCardAddTagsBySource",
    "TActionCardBeginSandstorm",
    "TActionPlayerDamage",
    "TActionPlayerBurnApply",
    "TActionPlayerShieldApply",
    "TActionPlayerHealApply",
    "TActionPlayerHeal",
    "TActionPlayerPoisonApply",
    "TActionPlayerBurnRemove",
    "TActionPlayerPoisonRemove",
    "TActionPlayerRegenApply",
    "TActionPlayerRageApply",
    "TActionPlayerModifyAttribute",
    "TActionPlayerReviveHeal",
    "TActionCardModifyAttribute",
}
AMOUNTLESS_RULE_ACTIONS = {
    "TActionCardFlyingStart",
    "TActionCardFlyingStop",
    "TActionCardFlyingToggle",
    "TActionCardAddTagsList",
    "TActionCardAddTagsBySource",
    "TActionCardBeginSandstorm",
    "TActionCardDestroy",
    "TActionCardDisable",
    "TActionCardTransform",
    "TActionCardRepair",
    "TActionCardTransformDestroyed",
    "TActionCardEnchant",
    "TActionCardEnchantRandom",
    "TActionPlayerReviveHeal",
}
RUNTIME_CARD_STATES = {"flying", "heated", "chilled"}
RUNTIME_SIDE_STATES = {"enraged"}
RUNTIME_STATE_ATTRS = RUNTIME_CARD_STATES | RUNTIME_SIDE_STATES
CARD_STATUS_ATTRIBUTES = {"Freeze", "Slow", "Haste"}
TARGET_COUNT_ATTR_BY_ACTION = {
    "TActionCardCharge": "ChargeTargets",
    "TActionCardFreeze": "FreezeTargets",
    "TActionCardHaste": "HasteTargets",
    "TActionCardReload": "ReloadTargets",
    "TActionCardSlow": "SlowTargets",
}
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
    "BurnRemoveAmount",
    "PoisonRemoveAmount",
    "ShieldApplyAmount",
    "HealAmount",
    "RegenApplyAmount",
    "RageApplyAmount",
    "CritChance",
    "DamageCrit",
    "BurnCrit",
    "PoisonCrit",
    "ShieldCrit",
    "HealCrit",
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
    "ChargeTargets",
    "FreezeTargets",
    "HasteTargets",
    "ReloadTargets",
    "SlowTargets",
    *(f"Custom_{index}" for index in range(16)),
}
PLAYER_ATTRIBUTE_AURA_ATTRS = {
    "HealthRegen",
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
NONCOMBAT_UNSUPPORTED_TRIGGERS = {
    "TTriggerOnCardPurchased",
    "TTriggerOnCardSold",
    "TTriggerOnCardSelected",
    "TTriggerOnDayStarted",
    "TTriggerOnEncounterCardsDealt",
    "TTriggerOnEncounterSelected",
    "TTriggerOnFightEnded",
    "TTriggerOnHourStarted",
}
NONCOMBAT_UNSUPPORTED_ACTIONS = {"TActionGameSpawnCards", "TActionCardUpgrade"}
NONCOMBAT_UNSUPPORTED_ATTRIBUTES = {"BuyPrice", "SellPrice"}
MAX_TRIGGER_DEPTH = 8
TAG_AURA_SOURCE_PREFIX = "tag-aura:"
TAG_AURA_ACTIONS = {"TAuraActionCardAddTagsList", "TAuraActionCardAddTagsBySource"}
MAX_TAG_AURA_REFRESH_ITERATIONS = 8
SUPPORTED_TRIGGER_TOKENS = {
    "itemused",
    "cardfired",
    "cardstartedflying",
    "cardstoppedflying",
    "playerattributechanged",
    "cardattributechanged",
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
    "performedoverheal",
    "performedregen",
    "performedreload",
    "performeddestruction",
    "cardcritted",
    "playerdied",
    "beforecarddestroyed",
    "carddisabled",
    "cardtransformed",
    "fightstarted",
    "sandstorm",
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
    destroyed: set[str] = field(default_factory=set)
    destroy_pending: set[str] = field(default_factory=set)
    destroyed_by: dict[str, str] = field(default_factory=dict)
    destroy_count: dict[str, int] = field(default_factory=dict)
    repair_count: dict[str, int] = field(default_factory=dict)
    transform_pending: set[str] = field(default_factory=set)
    transform_count: dict[str, int] = field(default_factory=dict)
    transform_sequence: int = 0
    trigger_counts: dict[Any, int] = field(default_factory=dict)
    condition_state: dict[str, bool] = field(default_factory=dict)
    max_health_modifiers: list[dict[str, Any]] = field(default_factory=list)
    runtime_states: dict[str, list[RuntimeState]] = field(default_factory=dict)
    item_runtime_states: dict[str, dict[str, list[RuntimeState]]] = field(default_factory=dict)
    runtime_aura_bonus: dict[str, dict[str, float]] = field(default_factory=dict)
    player_attribute_bonus: dict[str, float] = field(default_factory=dict)
    runtime_tags_by_source: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
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
class HealResult:
    requested_amount: float
    effective_amount: float
    overheal_amount: float
    health_before: float
    health_after: float
    max_health: float
    overheal_event_emitted: bool = False


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
    def __init__(
        self,
        timeline: list[dict[str, Any]],
        *,
        sandstorm_state: SandstormState | None = None,
        sandstorm_config: SandstormConfig | None = None,
    ) -> None:
        self.timeline = timeline
        self.events: list[ScheduledBattleEvent] = []
        self.sequence_id = 0
        self.charge_ports: dict[str, ChargePortState] = {}
        self.sandstorm_state = sandstorm_state
        self.sandstorm_config = sandstorm_config or DEFAULT_SANDSTORM_CONFIG

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
    player_remaining_shield: float = 0.0
    monster_remaining_shield: float = 0.0
    end_reason: str = ""
    sandstorm: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SandstormConfig:
    enabled: bool = True
    start_ms: int = 30_000
    tick_interval_ms: int = 200
    initial_damage: int = 1
    damage_increment: int = 2
    max_tick_damage: int = 1171

    @property
    def start_sec(self) -> float:
        return max(0.0, self.start_ms / 1000.0)

    @property
    def tick_interval_sec(self) -> float:
        return max(0.001, self.tick_interval_ms / 1000.0)


DEFAULT_SANDSTORM_CONFIG = SandstormConfig()


@dataclass
class SandstormState:
    active: bool = False
    started_at: float | None = None
    next_tick_time: float = math.inf
    tick_index: int = 0
    trigger_source: str = ""
    trigger_mode: str = ""
    duplicate_starts: int = 0
    last_tick_damage: float = 0.0


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
    sandstorm_config: SandstormConfig = DEFAULT_SANDSTORM_CONFIG,
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
        sandstorm_config=sandstorm_config,
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
            sandstorm_config=sandstorm_config,
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
        "sandstorm_config": _jsonable_state(sandstorm_config),
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
    player_initial_shield: float = 0.0,
    monster_initial_shield: float = 0.0,
    sandstorm_config: SandstormConfig = DEFAULT_SANDSTORM_CONFIG,
    card_catalog: dict[str, Any] | None = None,
) -> TwoSidedBattleOutcome:
    player_cards = clone_placed_cards(player_cards)
    monster_cards = clone_placed_cards(monster_cards)
    _apply_two_sided_static_card_auras(player_cards, monster_cards, rng)
    player = _make_battle_side("player", player_cards, player_health, duration_sec, attributes=player_attributes)
    monster = _make_battle_side("monster", monster_cards, monster_health, duration_sec, attributes=monster_attributes)
    player.shield = max(0.0, float(player_initial_shield or 0.0))
    monster.shield = max(0.0, float(monster_initial_shield or 0.0))
    sides = [player, monster]
    rules_by_source = {
        card.placement_id: read_rules(card, TWO_SIDED_RULE_ACTIONS)
        for side in sides
        for card in side.cards
    }
    card_index = template_index(card_catalog or {})
    timeline: list[dict[str, Any]] = []
    sandstorm = SandstormState()
    scheduler = BattleEventScheduler(
        timeline,
        sandstorm_state=sandstorm,
        sandstorm_config=sandstorm_config,
    )
    _initialize_runtime_state_auras(player, monster, timeline, rng, duration_sec)
    _refresh_runtime_state_auras(player, monster, scheduler, 0.0, timeline, rng)
    _run_fight_started_actions(player, monster, rules_by_source, scheduler, timeline, rng, duration_sec, card_index)
    _run_player_attribute_changed_rules_for_events(
        player,
        monster,
        rules_by_source,
        scheduler,
        0,
        0.0,
        timeline,
        rng,
        duration_sec=duration_sec,
        card_index=card_index,
    )
    _refresh_runtime_state_auras(player, monster, scheduler, 0.0, timeline, rng)
    if _battle_winner(player, monster):
        return _battle_outcome(player, monster, 0.0, timeline)

    now = 0.0
    next_burn_tick = 0.5
    next_second_tick = 1.0
    end_reason = ""
    guard = 0
    epsilon = 1e-6
    while now < duration_sec and guard < 2400:
        guard += 1
        card_time = _next_card_ready_time(sides, now)
        event_time = scheduler.next_time()
        tick_time = min(next_burn_tick, next_second_tick)
        state_time = _next_runtime_state_expiry(sides, now)
        sandstorm_time = _next_sandstorm_time(sandstorm, sandstorm_config, now)
        next_time = min(card_time, event_time, tick_time, state_time, sandstorm_time)
        if not math.isfinite(next_time) or next_time > duration_sec + epsilon:
            break
        elapsed = max(0.0, next_time - now)
        if elapsed > 0:
            _advance_cooldowns(sides, now, elapsed)
        now = next_time
        for transition in _expire_runtime_states_for_sides(player, monster, now, timeline):
            event_card = transition.get("card")
            if isinstance(event_card, PlacedCard):
                performed_transition = transition.get("performed") or {}
                if performed_transition.get("stopped_flying", 0) > 0:
                    emit_card_attribute_change(
                        player,
                        monster,
                        rules_by_source,
                        scheduler,
                        now,
                        timeline,
                        rng,
                        changed_ref=BattleCardRef(transition["side"], event_card),
                        attribute="Flying",
                        old_value=1.0,
                        new_value=0.0,
                        source_side=transition["side"],
                        source=event_card,
                        change_kind="runtime",
                        duration_sec=duration_sec,
                        card_index=card_index,
                    )
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
                    performed=performed_transition,
                    trigger_depth=0,
                    card_index=card_index,
                )
        _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)

        if now + epsilon >= next_burn_tick:
            event_cursor = len(timeline)
            _process_burn_tick(player, monster, now, timeline)
            _run_player_attribute_changed_rules_for_events(
                player,
                monster,
                rules_by_source,
                scheduler,
                event_cursor,
                now,
                timeline,
                rng,
                duration_sec=duration_sec,
                card_index=card_index,
            )
            next_burn_tick += 0.5
        if now + epsilon >= next_second_tick:
            event_cursor = len(timeline)
            _process_second_tick(player, monster, now, timeline)
            _run_player_attribute_changed_rules_for_events(
                player,
                monster,
                rules_by_source,
                scheduler,
                event_cursor,
                now,
                timeline,
                rng,
                duration_sec=duration_sec,
                card_index=card_index,
            )
            next_second_tick += 1.0
        _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
        if _battle_winner(player, monster):
            break

        if now + epsilon >= sandstorm_time:
            event_cursor = len(timeline)
            if _process_sandstorm_time(
                player,
                monster,
                rules_by_source,
                scheduler,
                sandstorm,
                sandstorm_config,
                now,
                timeline,
                rng,
                duration_sec,
                card_index=card_index,
            ):
                end_reason = "sandstorm"
            _run_player_attribute_changed_rules_for_events(
                player,
                monster,
                rules_by_source,
                scheduler,
                event_cursor,
                now,
                timeline,
                rng,
                duration_sec=duration_sec,
                card_index=card_index,
            )
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
                event_cursor = len(timeline)
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
                    card_index=card_index,
                )
                _run_player_attribute_changed_rules_for_events(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    event_cursor,
                    now,
                    timeline,
                    rng,
                    duration_sec=duration_sec,
                    card_index=card_index,
                )
            _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
    winner_time = min(now, duration_sec)
    if not end_reason and winner_time >= duration_sec and not _battle_winner(player, monster):
        end_reason = "timeout"
    return _battle_outcome(
        player,
        monster,
        winner_time,
        timeline,
        end_reason=end_reason,
        sandstorm=sandstorm,
    )


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
        attributes=dict(attrs),
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
            for attr in RUNTIME_AURA_ATTRS
        },
        runtime_aura_bonus={
            attr: {card.placement_id: 0.0 for card in targetable_cards(cards)}
            for attr in RUNTIME_AURA_ATTRS
        },
        player_attribute_bonus={attr: 0.0 for attr in PLAYER_ATTRIBUTE_AURA_ATTRS},
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
                mapped = _normalize_battle_attribute(rule.attribute_type)
                uses_battle_value = _value_uses_battle_context(get_field(rule.raw_action, "Value", "ReferenceValue"))
                if (
                    _runtime_state_attribute(rule.attribute_type)
                    or _condition_uses_runtime_state(rule.target_condition)
                    or (_condition_uses_tags(rule.target_condition) and (mapped in RUNTIME_AURA_ATTRS or mapped in COOLDOWN_AURA_ATTRS))
                    or uses_battle_value
                ):
                    continue
                for target in _resolve_battle_targets(source_side, other_side, source, source, rule, rng):
                    if is_skill_card(target.card):
                        continue
                    if has_runtime_snapshot_attribute(target.card.card, rule.attribute_type):
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
    if lower.startswith("custom_") and lower[7:].isdigit():
        return f"Custom_{int(lower[7:])}"
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
        "chargetargets": "ChargeTargets",
        "freezetargets": "FreezeTargets",
        "hastetargets": "HasteTargets",
        "reloadtargets": "ReloadTargets",
        "slowtargets": "SlowTargets",
        "burnremove": "BurnRemoveAmount",
        "burnremoveamount": "BurnRemoveAmount",
        "poisonremove": "PoisonRemoveAmount",
        "poisonremoveamount": "PoisonRemoveAmount",
        "healthregen": "HealthRegen",
        "damagecrit": "DamageCrit",
        "burncrit": "BurnCrit",
        "poisoncrit": "PoisonCrit",
        "shieldcrit": "ShieldCrit",
        "healcrit": "HealCrit",
        "freeze": "Freeze",
        "slow": "Slow",
        "haste": "Haste",
    }
    return aliases.get(lower, normalize_offense_attr(text) or text)


def _player_attribute_value(side: BattleSide, attribute_type: str) -> float:
    lower = str(attribute_type or "").strip().lower()
    if lower in {"burn", "burning"}:
        return max(0.0, side.burn_stack)
    if lower in {"poison", "poisoned"}:
        return max(0.0, side.poison_stack)
    if lower == "health":
        return max(0.0, side.health)
    if lower in {"healthmax", "maxhealth", "maximumhealth"}:
        return max(0.0, side.max_health)
    if lower == "rage":
        return max(0.0, side.rage)
    if lower == "healthregen":
        base = _numeric_attr(side.attributes, "HealthRegen", default=0.0)
        return max(0.0, base + side.player_attribute_bonus.get("HealthRegen", 0.0))
    return 0.0


def _value_uses_battle_player_attribute(value_node: Any) -> bool:
    if isinstance(value_node, dict):
        if "ReferenceValuePlayerAttribute" in type_name(value_node):
            return True
        return any(_value_uses_battle_player_attribute(value) for value in value_node.values())
    if isinstance(value_node, list):
        return any(_value_uses_battle_player_attribute(value) for value in value_node)
    return False


def _value_uses_battle_context(value_node: Any) -> bool:
    if isinstance(value_node, dict):
        node_type = type_name(value_node)
        if (
            "ReferenceValuePlayerAttribute" in node_type
            or "ReferenceValueCardCount" in node_type
            or "ReferenceValueCardTagCount" in node_type
        ):
            return True
        return any(_value_uses_battle_context(value) for value in value_node.values())
    if isinstance(value_node, list):
        return any(_value_uses_battle_context(value) for value in value_node)
    return False


def _battle_value(
    value_node: Any,
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
    trigger_context: dict[str, float] | None = None,
) -> float:
    from combat_simulator import normalize_seconds, resolve_value

    if not isinstance(value_node, dict):
        return resolve_value(value_node, source)
    node_type = type_name(value_node)
    if "ReferenceValueAttributeChange" in node_type:
        base = float((trigger_context or {}).get("attribute_delta", 0.0) or 0.0)
        return _apply_battle_value_modifier(base, value_node, source_side, other_side, source, trigger_context)
    if "ReferenceValueCardCount" in node_type:
        base = float(len(_reference_value_target_cards(value_node, source_side, other_side, source)))
        return _apply_battle_value_modifier(base, value_node, source_side, other_side, source, trigger_context)
    if "ReferenceValueCardTagCount" in node_type:
        base = float(
            sum(
                len(card_tags(ref.card, _runtime_aura_tags(ref.side)).difference({""}))
                for ref in _reference_value_target_cards(value_node, source_side, other_side, source)
            )
        )
        return _apply_battle_value_modifier(base, value_node, source_side, other_side, source, trigger_context)
    if "ReferenceValueCardAttribute" in node_type:
        attr = _normalize_battle_attribute(str(get_field(value_node, "AttributeType", default="")))
        base = get_attr_value_by_tier(source.card, attr, effective_tier(source))
        base += _bonus_value(source_side, attr, source.placement_id)
        modifier = get_field(value_node, "Modifier")
        if isinstance(modifier, dict):
            mode = str(get_field(modifier, "ModifyMode", default=""))
            mv = _battle_value(get_field(modifier, "Value"), source_side, other_side, source, trigger_context)
            if mode == "Multiply":
                base *= mv
            elif mode == "Add":
                base += mv
            elif mode == "Subtract":
                base -= mv
            if bool(get_field(modifier, "ShouldRound", default=False)):
                base = round(base)
        return normalize_seconds(float(base), attr)
    if "ReferenceValuePlayerAttribute" not in node_type:
        return resolve_value(value_node, source)
    target_node = get_field(value_node, "Target", default={}) or {}
    target_mode = str(get_field(target_node, "TargetMode", default="Self") or "Self")
    target_side = other_side if target_mode.lower() == "opponent" else source_side
    base = _player_attribute_value(target_side, str(get_field(value_node, "AttributeType", default="")))
    modifier = get_field(value_node, "Modifier")
    if isinstance(modifier, dict):
        mode = str(get_field(modifier, "ModifyMode", default=""))
        mv = _battle_value(get_field(modifier, "Value"), source_side, other_side, source, trigger_context)
        if mode == "Multiply":
            base *= mv
        elif mode == "Add":
            base += mv
        elif mode == "Subtract":
            base -= mv
        if bool(get_field(modifier, "ShouldRound", default=False)):
            base = round(base)
    return normalize_seconds(float(base), str(get_field(value_node, "AttributeType", default="")))


def _reference_value_target_cards(
    value_node: dict[str, Any],
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
) -> list[BattleCardRef]:
    target_node = get_field(value_node, "Target", default={}) or {}
    target_type = type_name(target_node)
    target_mode = str(get_field(target_node, "TargetMode", default="") or "")
    target_section = str(get_field(target_node, "TargetSection", default="") or "")
    target_text = f"{target_mode} {target_section}".lower()
    target_side = other_side if "opponent" in target_text or "enemy" in target_text else source_side
    condition = extract_condition_meta(get_field(target_node, "Conditions"))
    exclude_self = bool(get_field(target_node, "ExcludeSelf", default=False))
    include_origin = bool(get_field(target_node, "IncludeOrigin", default=False))

    def match(card: PlacedCard) -> bool:
        if exclude_self and target_side is source_side and card.placement_id == source.placement_id:
            return False
        return _battle_card_matches(BattleCardRef(target_side, card), condition)

    if target_type == "TTargetCardSelf":
        return [BattleCardRef(source_side, source)] if match(source) else []

    pool = [card for card in targetable_cards(target_side.cards) if not is_card_destroyed(target_side, card)]
    if target_type == "TTargetCardXMost":
        chosen = pick_x_most([card for card in pool if match(card)], target_mode or "RightMostCard")
        return [BattleCardRef(target_side, chosen)] if chosen else []
    if target_type == "TTargetCardRandom":
        count = int(get_field(target_node, "TargetCount", default=1) or 1)
        return [BattleCardRef(target_side, card) for card in pool if match(card)][: max(1, count)]
    if target_type == "TTargetCardSection":
        return [BattleCardRef(target_side, card) for card in pool if match(card)]

    left = next((card for card in pool if card.start + (card.width or 1) == source.start), None)
    right = next((card for card in pool if card.start == source.start + (source.width or 1)), None)
    if target_mode == "LeftCard":
        return [BattleCardRef(source_side, left)] if left and match(left) else []
    if target_mode == "RightCard":
        return [BattleCardRef(source_side, right)] if right and match(right) else []
    if target_mode == "Neighbor":
        return [BattleCardRef(source_side, card) for card in (left, right) if card and match(card)]
    if include_origin:
        return [BattleCardRef(source_side, source)] if match(source) else []
    return [BattleCardRef(target_side, card) for card in pool if match(card)]


def _apply_battle_value_modifier(
    base: float,
    value_node: dict[str, Any],
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
    trigger_context: dict[str, float] | None = None,
) -> float:
    modifier = get_field(value_node, "Modifier")
    if isinstance(modifier, dict):
        mode = str(get_field(modifier, "ModifyMode", default=""))
        mv = _battle_value(get_field(modifier, "Value"), source_side, other_side, source, trigger_context)
        if mode == "Multiply":
            base *= mv
        elif mode == "Add":
            base += mv
        elif mode == "Subtract":
            base -= mv
        if bool(get_field(modifier, "ShouldRound", default=False)):
            base = round(base)
    return float(base)


def _runtime_aura_rule_amount(
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
    rule: Any,
) -> float:
    from combat_simulator import normalize_seconds

    value_node = get_field(rule.raw_action, "Value", "ReferenceValue")
    if value_node is None:
        return rule.amount
    value = _battle_value(value_node, source_side, other_side, source)
    return normalize_seconds(value, str(get_field(rule.raw_action, "AttributeType", default=rule.attribute_type)))


def _battle_rule_amount(
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    trigger_context: dict[str, float] | None = None,
) -> float:
    from combat_simulator import normalize_seconds

    value_node = get_field(rule.raw_action, "Value", "ReferenceValue")
    if value_node is None:
        return rule.amount
    value = _battle_value(value_node, source_side, other_side, source, trigger_context)
    return normalize_seconds(value, str(get_field(rule.raw_action, "AttributeType", default=rule.attribute_type)))


def _condition_uses_runtime_state(condition: Any) -> bool:
    return any(_runtime_condition_attribute(str(item.get("attribute") or "")) for item in getattr(condition, "attr_conditions", []))


def _condition_uses_tags(condition: Any) -> bool:
    return bool(getattr(condition, "include_tags", None) or getattr(condition, "exclude_tags", None))


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


def _effective_card_tags_from_snapshot(
    side: BattleSide,
    card: PlacedCard,
    runtime_tag_snapshot: dict[int, dict[str, set[str]]],
) -> set[str]:
    return card_tags(card, runtime_tag_snapshot.get(id(side), {}))


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
    return _set_runtime_tags_from_source(target, source_id, tags, source_side, source, now, timeline)


def _pick_enchantment_for_rule(rule: Any, rng: Callable[[], float] | None) -> str:
    if rule.action_type == "TActionCardEnchant":
        return normalize_enchantment(get_field(rule.raw_action, "Enchantment", default=""))
    entries = get_field(rule.raw_action, "Enchantments", default=[]) or []
    weighted: list[tuple[str, float]] = []
    for entry in entries:
        enchantment = normalize_enchantment(get_field(entry, "Enchantment", default=""))
        if not enchantment:
            continue
        try:
            weight = float(get_field(entry, "Weight", default=1) or 1)
        except (TypeError, ValueError):
            weight = 1.0
        if weight > 0:
            weighted.append((enchantment, weight))
    if not weighted:
        return ""
    total = sum(weight for _, weight in weighted)
    roll = 0.0 if rng is None else max(0.0, min(total, rng() * total))
    running = 0.0
    for enchantment, weight in weighted:
        running += weight
        if roll <= running:
            return enchantment
    return weighted[-1][0]


def _apply_card_enchantment(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    enchantment: str,
    rule: Any,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    if not enchantment:
        return False
    existing = card_enchantment(target.card)
    if existing and bool(get_field(rule.raw_action, "PreventOverride", default=False)):
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "enchant-ignored",
                card_label(source),
                0.0,
                target=card_label(target.card),
                enchantment=enchantment,
                existing_enchantment=existing,
            )
        )
        return False
    target.card.card["enchantment"] = enchantment
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target.side.name,
            "enchant",
            card_label(source),
            1.0,
            target=card_label(target.card),
            enchantment=enchantment,
            previous_enchantment=existing or None,
        )
    )
    return True


def _set_runtime_tags_from_source(
    target: BattleCardRef,
    source_id: str,
    tags: set[str],
    source_side: BattleSide,
    source: PlacedCard,
    now: float,
    timeline: list[dict[str, Any]],
    *,
    action_type: str = "",
) -> bool:
    normalized = {normalize_tag(tag) for tag in tags if normalize_tag(tag)}
    sources = target.side.runtime_tags_by_source.setdefault(target.card.placement_id, {})
    previous_source_tags = sources.get(source_id)
    if previous_source_tags == normalized:
        return False
    before = set().union(*sources.values()) if sources else set()
    if normalized:
        sources[source_id] = set(normalized)
    else:
        sources.pop(source_id, None)
    if not sources:
        target.side.runtime_tags_by_source.pop(target.card.placement_id, None)
        after = set()
    else:
        after = set().union(*sources.values())
    added = sorted(after - before)
    removed = sorted(before - after)
    if added:
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
                action_type=action_type or None,
                effective_tags=sorted(after),
            )
        )
    if removed:
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "runtime-tags-removed",
                card_label(source),
                float(len(removed)),
                target=card_label(target.card),
                tags=removed,
                source_id=source_id,
                action_type=action_type or None,
                effective_tags=sorted(after),
            )
        )
    return bool(added or removed or previous_source_tags != normalized)


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


def _tag_aura_source_key(source_side: BattleSide, source: PlacedCard, rule: Any) -> str:
    return f"{TAG_AURA_SOURCE_PREFIX}{source_side.name}:{source.placement_id}:{rule.effect_id}"


def _rule_for_selector(rule: Any, selector: Any) -> Any:
    count = get_field(selector, "TargetCount", default=get_field(rule.raw_action, "TargetCount"))
    return replace(
        rule,
        target_type=type_name(selector),
        target_mode=str(get_field(selector, "TargetMode", default="")),
        target_section=str(get_field(selector, "TargetSection", default="")),
        target_count=int(count) if isinstance(count, (int, float)) and count > 0 else None,
        target_exclude_self=bool(get_field(selector, "ExcludeSelf", default=False)),
        target_include_origin=bool(get_field(selector, "IncludeOrigin", default=False)),
        target_condition=extract_condition_meta(get_field(selector, "Conditions")),
    )


def _tag_aura_tags_for_rule(
    source_side: BattleSide,
    other_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    rng: Callable[[], float] | None,
    runtime_tag_snapshot: dict[int, dict[str, set[str]]],
) -> set[str]:
    if rule.action_type == "TAuraActionCardAddTagsList":
        return {
            normalize_tag(tag)
            for tag in (get_field(rule.raw_action, "Tags", default=[]) or [])
            if normalize_tag(tag)
        }
    if rule.action_type != "TAuraActionCardAddTagsBySource":
        return set()
    selector = get_field(rule.raw_action, "Source", default={}) or {}
    if not isinstance(selector, dict):
        return set()
    selector_rule = _rule_for_selector(rule, selector)
    tags: set[str] = set()
    for selected in _resolve_battle_targets(source_side, other_side, source, source, selector_rule, rng):
        tags.update(_effective_card_tags_from_snapshot(selected.side, selected.card, runtime_tag_snapshot))
    return tags


def _expected_runtime_tag_auras(
    player: BattleSide,
    monster: BattleSide,
    rng: Callable[[], float] | None,
    runtime_tag_snapshot: dict[int, dict[str, set[str]]],
) -> dict[int, dict[str, dict[str, set[str]]]]:
    expected: dict[int, dict[str, dict[str, set[str]]]] = {id(player): {}, id(monster): {}}
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source):
                continue
            for rule in read_rules(source, TAG_AURA_ACTIONS):
                tags = _tag_aura_tags_for_rule(source_side, other_side, source, rule, rng, runtime_tag_snapshot)
                if not tags:
                    continue
                source_key = _tag_aura_source_key(source_side, source, rule)
                for target in _resolve_battle_targets(source_side, other_side, source, source, rule, rng):
                    expected.setdefault(id(target.side), {}).setdefault(target.card.placement_id, {})[source_key] = set(tags)
    return expected


def _apply_runtime_tag_aura_expectation(
    sides: tuple[BattleSide, BattleSide],
    expected: dict[int, dict[str, dict[str, set[str]]]],
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    changed = False
    source_lookup = {
        f"{TAG_AURA_SOURCE_PREFIX}{side.name}:{card.placement_id}:": (side, card)
        for side in sides
        for card in side.cards
    }
    for side in sides:
        side_expected = expected.get(id(side), {})
        target_ids = set(side.runtime_tags_by_source) | set(side_expected)
        for placement_id in list(target_ids):
            card = next((item for item in side.cards if item.placement_id == placement_id), None)
            if card is None:
                continue
            current_sources = side.runtime_tags_by_source.get(placement_id, {})
            source_ids = {
                source_id
                for source_id in set(current_sources) | set(side_expected.get(placement_id, {}))
                if source_id.startswith(TAG_AURA_SOURCE_PREFIX)
            }
            for source_id in sorted(source_ids):
                source_side, source = next(
                    (value for prefix, value in source_lookup.items() if source_id.startswith(prefix)),
                    (side, card),
                )
                target = BattleCardRef(side, card)
                next_tags = side_expected.get(placement_id, {}).get(source_id, set())
                if _set_runtime_tags_from_source(
                    target,
                    source_id,
                    next_tags,
                    source_side,
                    source,
                    now,
                    timeline,
                    action_type="runtime-tag-aura",
                ):
                    changed = True
    return changed


def _refresh_runtime_tag_auras(
    player: BattleSide,
    monster: BattleSide,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
) -> None:
    sides = (player, monster)
    for iteration in range(MAX_TAG_AURA_REFRESH_ITERATIONS):
        snapshot = {id(side): _runtime_aura_tags(side) for side in sides}
        expected = _expected_runtime_tag_auras(player, monster, rng, snapshot)
        changed = _apply_runtime_tag_aura_expectation(sides, expected, now, timeline)
        if not changed:
            return
    timeline.append(
        _battle_event(
            now,
            "system",
            "all",
            "runtime-tag-aura-refresh-limited",
            "runtime-tag-aura",
            float(MAX_TAG_AURA_REFRESH_ITERATIONS),
        )
    )


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
    return next((card for card in side.active if not is_card_destroyed(side, card)), None) or next(
        (card for card in side.cards if not is_card_destroyed(side, card)),
        None,
    )


def _refresh_runtime_state_auras(
    player: BattleSide,
    monster: BattleSide,
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
) -> None:
    _refresh_runtime_tag_auras(player, monster, now, timeline, rng)
    for side in (player, monster):
        side.runtime_aura_bonus = {
            attr: {card.placement_id: 0.0 for card in targetable_cards(side.cards)}
            for attr in RUNTIME_AURA_ATTRS
        }
        side.player_attribute_bonus = {attr: 0.0 for attr in PLAYER_ATTRIBUTE_AURA_ATTRS}
    active_cooldown_modifier_ids: set[str] = set()
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source):
                continue
            for rule in read_rules(source, {"TAuraActionCardModifyAttribute"}):
                uses_battle_value = _value_uses_battle_context(get_field(rule.raw_action, "Value", "ReferenceValue"))
                if not (_condition_uses_runtime_state(rule.target_condition) or _condition_uses_tags(rule.target_condition) or uses_battle_value):
                    continue
                mapped = _normalize_battle_attribute(rule.attribute_type)
                if mapped in PLAYER_ATTRIBUTE_AURA_ATTRS:
                    aura_amount = _runtime_aura_rule_amount(source_side, other_side, source, rule)
                    target_side = _player_target_side(player, monster, source_side, rule)
                    current = target_side.player_attribute_bonus.get(mapped, 0.0)
                    target_side.player_attribute_bonus[mapped] = apply_attribute_operation(
                        current,
                        aura_amount,
                        rule.operation,
                    )
                    continue
                if mapped not in RUNTIME_AURA_ATTRS and mapped not in COOLDOWN_AURA_ATTRS:
                    continue
                aura_amount = _runtime_aura_rule_amount(source_side, other_side, source, rule)
                for target in _resolve_battle_targets(source_side, other_side, source, source, rule, rng):
                    if mapped in COOLDOWN_AURA_ATTRS:
                        dynamic_rule = replace(rule, amount=aura_amount)
                        modifier_id = _runtime_aura_modifier_id(source_side, source, rule, target, mapped)
                        active_cooldown_modifier_ids.add(modifier_id)
                        _upsert_runtime_cooldown_modifier(
                            target,
                            source_side,
                            source,
                            dynamic_rule,
                            mapped,
                            modifier_id,
                            now,
                            scheduler,
                            timeline,
                        )
                        continue
                    if target.card.placement_id not in target.side.runtime_aura_bonus.get(mapped, {}):
                        continue
                    if has_runtime_snapshot_attribute(target.card.card, mapped):
                        continue
                    current = target.side.runtime_aura_bonus[mapped][target.card.placement_id]
                    target.side.runtime_aura_bonus[mapped][target.card.placement_id] = apply_attribute_operation(
                        current,
                        aura_amount,
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
        "TActionPlayerBurnRemove": "BurnRemoveAmount",
        "TActionPlayerPoisonRemove": "PoisonRemoveAmount",
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


def _canonical_card_attribute(attribute_type: str) -> str:
    text = str(attribute_type or "").strip()
    lower = text.lower()
    aliases = {
        "ammo": "Ammo",
        "maxammo": "AmmoMax",
        "ammomax": "AmmoMax",
        "damage": "DamageAmount",
        "damageamount": "DamageAmount",
        "burn": "BurnApplyAmount",
        "burnapplyamount": "BurnApplyAmount",
        "burnremove": "BurnRemoveAmount",
        "burnremoveamount": "BurnRemoveAmount",
        "poison": "PoisonApplyAmount",
        "poisonapplyamount": "PoisonApplyAmount",
        "poisonremove": "PoisonRemoveAmount",
        "poisonremoveamount": "PoisonRemoveAmount",
        "shield": "ShieldApplyAmount",
        "shieldapplyamount": "ShieldApplyAmount",
        "heal": "HealAmount",
        "healamount": "HealAmount",
        "regen": "RegenApplyAmount",
        "regenapplyamount": "RegenApplyAmount",
        "critchance": "CritChance",
        "multicast": "Multicast",
        "cooldown": "CooldownMax",
        "cooldownmax": "CooldownMax",
        "flying": "Flying",
        "freeze": "Freeze",
        "slow": "Slow",
        "haste": "Haste",
    }
    return aliases.get(lower, _normalize_battle_attribute(text))


def _card_attribute_value(ref: BattleCardRef, attribute_type: str, now: float | None = None) -> float | None:
    attr = _canonical_card_attribute(attribute_type)
    if not attr:
        return None
    lower = attr.lower()
    if lower == "ammo":
        ammo = ref.side.ammo.get(ref.card.placement_id)
        return float(ammo.get("current", 0.0)) if ammo else None
    if lower in {"flying", "freeze", "slow", "haste"}:
        threshold = float(now if now is not None else 0.0)
        if lower == "freeze":
            return 1.0 if ref.side.freeze_until.get(ref.card.placement_id, 0.0) > threshold else 0.0
        if lower == "slow":
            return 1.0 if ref.side.slow_until.get(ref.card.placement_id, 0.0) > threshold else 0.0
        if lower == "haste":
            return 1.0 if ref.side.haste_until.get(ref.card.placement_id, 0.0) > threshold else 0.0
        return 1.0 if _has_runtime_state(ref.side, ref.card, "flying", now) else 0.0
    if attr in COOLDOWN_AURA_ATTRS:
        state = _cooldown_state(ref.side, ref.card)
        if state is not None:
            return float(state.effective_cooldown or state.base_cooldown)
    if attr not in RUNTIME_AURA_ATTRS and attr not in COOLDOWN_AURA_ATTRS:
        return None
    return (
        get_attr_value_by_tier(ref.card.card, attr, effective_tier(ref.card))
        + _bonus_value(ref.side, attr, ref.card.placement_id)
    )


def emit_card_attribute_change(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    changed_ref: BattleCardRef,
    attribute: str,
    old_value: float | None,
    new_value: float | None,
    source_side: BattleSide,
    source: PlacedCard,
    change_kind: str,
    duration_sec: float,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> bool:
    if old_value is None or new_value is None:
        return False
    if not _is_current_card_instance(changed_ref.side, changed_ref.card) or is_card_destroyed(changed_ref.side, changed_ref.card):
        return False
    delta = float(new_value) - float(old_value)
    if abs(delta) <= 1e-9:
        return False
    canonical = _canonical_card_attribute(attribute)
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            changed_ref.side.name,
            "CARD_ATTRIBUTE_CHANGED",
            card_label(source),
            delta,
            target=card_label(changed_ref.card),
            attribute=canonical,
            old_value=old_value,
            new_value=new_value,
            delta=delta,
            change_kind=change_kind,
            changed_placement_id=changed_ref.card.placement_id,
        )
    )
    _run_card_attribute_changed_rules(
        player,
        monster,
        rules_by_source,
        scheduler,
        now,
        timeline,
        rng,
        changed_ref=changed_ref,
        attribute=canonical,
        old_value=float(old_value),
        new_value=float(new_value),
        delta=delta,
        duration_sec=duration_sec,
        trigger_depth=trigger_depth + 1,
        card_index=card_index,
    )
    return True


def _run_card_attribute_changed_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    changed_ref: BattleCardRef,
    attribute: str,
    old_value: float,
    new_value: float,
    delta: float,
    duration_sec: float,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(
            _battle_event(
                now,
                changed_ref.side.name,
                changed_ref.side.name,
                "trigger-depth-limited",
                card_label(changed_ref.card),
                trigger_depth,
                trigger="TTriggerOnCardAttributeChanged",
            )
        )
        return
    performed = {"card_attribute_changed": 1, "attribute_delta": delta}
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source) or not _is_current_card_instance(source_side, source):
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                if "cardattributechanged" not in str(rule.trigger_type or "").lower():
                    continue
                if _canonical_card_attribute(rule.trigger_attribute_changed) != attribute:
                    continue
                if not _attribute_change_type_matches(rule.trigger_change_type, delta):
                    continue
                if not _attribute_trigger_subject_matches(source_side, source, changed_ref, rule):
                    continue
                if not _consume_trigger_count(source_side, source, rule, changed_ref, now, timeline):
                    continue
                amount = max(0.0, _battle_rule_amount(source_side, other_side, source, rule, performed))
                preview_amount = _effective_rule_amount(source_side, source, rule, amount, 1)
                if preview_amount <= 0 and rule.action_type not in AMOUNTLESS_RULE_ACTIONS:
                    continue
                timeline.append(
                    _battle_event(
                        now,
                        source_side.name,
                        changed_ref.side.name,
                        "card-attribute-triggered",
                        card_label(source),
                        delta,
                        target=card_label(changed_ref.card),
                        attribute=attribute,
                        old_value=old_value,
                        new_value=new_value,
                        trigger_change_type=str(rule.trigger_change_type or ""),
                    )
                )
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    changed_ref.card,
                    rule,
                    amount,
                    1,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    rules_by_source,
                    duration_sec,
                    dict(performed),
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )


def _run_player_attribute_changed_rules_for_events(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    start_index: int,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    duration_sec: float,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    cursor = max(0, int(start_index))
    guard = 0
    while cursor < len(timeline) and guard < 240:
        guard += 1
        event = timeline[cursor]
        cursor += 1
        if str(event.get("kind") or "") == "player-died":
            died_side = _side_by_name(player, monster, str(event.get("target_side") or event.get("side") or ""))
            if died_side is None:
                continue
            trigger_card = _first_event_card(died_side)
            if trigger_card is None:
                continue
            _run_player_died_triggered_rules(
                player,
                monster,
                rules_by_source,
                scheduler,
                now,
                timeline,
                rng,
                died_side=died_side,
                trigger_card=trigger_card,
                duration_sec=duration_sec,
                trigger_depth=trigger_depth + 1,
                card_index=card_index,
            )
            continue
        before = event.get("before_health")
        after = event.get("after_health")
        attribute = "Health"
        if before is None or after is None:
            kind = str(event.get("kind") or "")
            if kind not in {"burn-apply", "poison-apply", "burn-cleansed", "poison-cleansed"}:
                continue
            try:
                value = float(event.get("value") or 0.0)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            attribute = "Burn" if "burn" in kind else "Poison"
            if "cleansed" in kind:
                old_value = value
                new_value = 0.0
            else:
                old_value = 0.0
                new_value = value
        else:
            try:
                old_value = float(before)
                new_value = float(after)
            except (TypeError, ValueError):
                continue
        if abs(new_value - old_value) <= 1e-9:
            continue
        changed_side = _side_by_name(player, monster, str(event.get("target_side") or event.get("side") or ""))
        if changed_side is None:
            continue
        trigger_card = _first_event_card(changed_side)
        if trigger_card is None:
            continue
        _run_player_attribute_changed_rules(
            player,
            monster,
            rules_by_source,
            scheduler,
            now,
            timeline,
            rng,
            changed_side=changed_side,
            trigger_card=trigger_card,
            attribute=attribute,
            old_value=old_value,
            new_value=new_value,
            duration_sec=duration_sec,
            trigger_depth=trigger_depth + 1,
            card_index=card_index,
        )


def _run_player_attribute_changed_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    changed_side: BattleSide,
    trigger_card: PlacedCard,
    attribute: str,
    old_value: float,
    new_value: float,
    duration_sec: float,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(
            _battle_event(
                now,
                changed_side.name,
                changed_side.name,
                "trigger-depth-limited",
                attribute,
                trigger_depth,
                trigger="TTriggerOnPlayerAttributeChanged",
            )
        )
        return
    delta = float(new_value) - float(old_value)
    if abs(delta) <= 1e-9:
        return
    performed = {
        "player_attribute_changed": 1,
        "attribute_delta": delta,
        "damage": 1 if delta < 0 else 0,
        "heal": 1 if delta > 0 else 0,
    }
    fired_ref = BattleCardRef(changed_side, trigger_card)
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source) or not _is_current_card_instance(source_side, source):
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                if "playerattributechanged" not in str(rule.trigger_type or "").lower():
                    continue
                if str(rule.trigger_attribute_changed or "").lower() != str(attribute or "").lower():
                    continue
                if not _attribute_change_type_matches(rule.trigger_change_type, delta):
                    continue
                if not _trigger_side_matches(source_side, changed_side, rule):
                    continue
                if not _consume_trigger_count(source_side, source, rule, fired_ref, now, timeline):
                    continue
                amount = max(0.0, _battle_rule_amount(source_side, other_side, source, rule, performed))
                preview_amount = _effective_rule_amount(source_side, source, rule, amount, 1)
                if preview_amount <= 0 and rule.action_type not in AMOUNTLESS_RULE_ACTIONS:
                    continue
                timeline.append(
                    _battle_event(
                        now,
                        source_side.name,
                        changed_side.name,
                        "player-attribute-triggered",
                        card_label(source),
                        delta,
                        attribute=attribute,
                        old_value=old_value,
                        new_value=new_value,
                        trigger_change_type=str(rule.trigger_change_type or ""),
                    )
                )
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    trigger_card,
                    rule,
                    amount,
                    1,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    rules_by_source,
                    duration_sec,
                    dict(performed),
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )


def _run_player_died_triggered_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    died_side: BattleSide,
    trigger_card: PlacedCard,
    duration_sec: float,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(
            _battle_event(
                now,
                died_side.name,
                died_side.name,
                "trigger-depth-limited",
                "player-died",
                trigger_depth,
                trigger="TTriggerOnPlayerDied",
            )
        )
        return
    performed = {"player_died": 1}
    fired_ref = BattleCardRef(died_side, trigger_card)
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source) or not _is_current_card_instance(source_side, source):
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                if "playerdied" not in str(rule.trigger_type or "").lower():
                    continue
                if not _trigger_side_matches(source_side, died_side, rule):
                    continue
                if not _consume_trigger_count(source_side, source, rule, fired_ref, now, timeline):
                    continue
                amount = max(0.0, _battle_rule_amount(source_side, other_side, source, rule, performed))
                preview_amount = _effective_rule_amount(source_side, source, rule, amount, 1)
                if preview_amount <= 0 and rule.action_type not in AMOUNTLESS_RULE_ACTIONS:
                    continue
                timeline.append(
                    _battle_event(
                        now,
                        source_side.name,
                        died_side.name,
                        "player-died-triggered",
                        card_label(source),
                        amount,
                    )
                )
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    trigger_card,
                    rule,
                    amount,
                    1,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    rules_by_source,
                    duration_sec,
                    dict(performed),
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )


def _side_by_name(player: BattleSide, monster: BattleSide, name: str) -> BattleSide | None:
    if name == player.name:
        return player
    if name == monster.name:
        return monster
    return None


def _run_overheal_triggered_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    healing_ref: BattleCardRef,
    target_side: BattleSide,
    heal_result: HealResult,
    duration_sec: float,
    source_skill: str = "",
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if not heal_result.overheal_event_emitted or heal_result.overheal_amount <= 0:
        return
    timeline.append(
        _battle_event(
            now,
            healing_ref.side.name,
            target_side.name,
            "CARD_PERFORMED_OVERHEAL",
            card_label(healing_ref.card),
            heal_result.overheal_amount,
            source_card=card_label(healing_ref.card),
            source_placement_id=healing_ref.card.placement_id,
            source_skill=source_skill,
            healing_card=card_label(healing_ref.card),
            target_player=target_side.name,
            requested_heal=heal_result.requested_amount,
            effective_heal=heal_result.effective_amount,
            actual_heal=heal_result.effective_amount,
            overheal=heal_result.overheal_amount,
            overheal_amount=heal_result.overheal_amount,
            health_before=heal_result.health_before,
            health_after=heal_result.health_after,
            max_health=heal_result.max_health,
            combat_time_ms=round(now * 1000.0, 3),
        )
    )
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(
            _battle_event(
                now,
                healing_ref.side.name,
                target_side.name,
                "trigger-depth-limited",
                card_label(healing_ref.card),
                trigger_depth,
                trigger="TTriggerOnCardPerformedOverHeal",
            )
        )
        return
    performed = {"overheal": 1, "overheal_amount": heal_result.overheal_amount}
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source) or not _is_current_card_instance(source_side, source):
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                if "performedoverheal" not in str(rule.trigger_type or "").lower():
                    continue
                if not _overheal_trigger_subject_matches(source_side, source, healing_ref, rule):
                    continue
                if not _consume_trigger_count(source_side, source, rule, healing_ref, now, timeline):
                    continue
                amount = max(0.0, rule.amount)
                preview_amount = _effective_rule_amount(source_side, source, rule, amount, 1)
                if preview_amount <= 0 and rule.action_type not in AMOUNTLESS_RULE_ACTIONS:
                    continue
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    healing_ref.card,
                    rule,
                    amount,
                    1,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    rules_by_source,
                    duration_sec,
                    dict(performed),
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )


def _overheal_trigger_subject_matches(
    source_side: BattleSide,
    source: PlacedCard,
    healing_ref: BattleCardRef,
    rule: Any,
) -> bool:
    return _attribute_trigger_subject_matches(source_side, source, healing_ref, rule)


def _attribute_change_type_matches(change_type: str, delta: float) -> bool:
    lower = str(change_type or "").strip().lower()
    if lower == "gain":
        return delta > 0
    if lower == "loss":
        return delta < 0
    return True


def _attribute_trigger_subject_matches(
    source_side: BattleSide,
    source: PlacedCard,
    changed_ref: BattleCardRef,
    rule: Any,
) -> bool:
    if rule.trigger_exclude_self and source_side is changed_ref.side and source.placement_id == changed_ref.card.placement_id:
        return False
    subject_type = str(rule.trigger_subject_type or "")
    section = str(getattr(rule, "trigger_subject_section", "") or "")
    if subject_type == "TTargetCardSelf":
        if source_side is not changed_ref.side or source.placement_id != changed_ref.card.placement_id:
            return False
    elif subject_type == "TTargetCardSection":
        lower = section.lower()
        if "self" in lower and source_side is not changed_ref.side:
            return False
        if "opponent" in lower and source_side is changed_ref.side:
            return False
    elif subject_type == "TTargetCardPositional":
        if source_side is not changed_ref.side:
            return False
        mode = str(rule.trigger_subject_mode or "").lower()
        left = changed_ref.card.start + (changed_ref.card.width or 1) == source.start
        right = changed_ref.card.start == source.start + (source.width or 1)
        if "neighbor" in mode and not (left or right):
            return False
    elif subject_type:
        return False
    return _battle_card_matches(changed_ref, rule.trigger_condition)


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


def is_card_destroyed(side: BattleSide, card: PlacedCard) -> bool:
    return card.placement_id in side.destroyed


def _is_current_card_instance(side: BattleSide, card: PlacedCard) -> bool:
    return any(current is card for current in side.cards)


def is_card_active(side: BattleSide, card: PlacedCard) -> bool:
    return side.health > 0 and not is_card_destroyed(side, card)


def _cooldown_speed_multiplier(side: BattleSide, card: PlacedCard, now: float) -> float:
    if is_card_destroyed(side, card):
        return 0.0
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
            if is_card_destroyed(side, card):
                continue
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
            if is_card_destroyed(side, card):
                continue
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
            if is_card_destroyed(side, card):
                continue
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
    if state is None or not is_card_active(ref.side, ref.card):
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
    if not _is_current_card_instance(target.side, target.card):
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
    if not _is_current_card_instance(target.side, target.card):
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
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    source_side = ref.side
    target_side = _opponent(player, monster, source_side)
    fired = ref.card
    if not _is_current_card_instance(source_side, fired):
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                source_side.name,
                "item-use-cancelled",
                card_label(fired),
                0.0,
                target=card_label(fired),
                reason="replaced",
                requested_time=round(float(requested_time if requested_time is not None else now), 6),
                forced=forced,
                cast_index=cast_index,
            )
        )
        return
    if not is_card_active(source_side, fired):
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                source_side.name,
                "item-use-cancelled",
                card_label(fired),
                0.0,
                target=card_label(fired),
                reason="destroyed",
                requested_time=round(float(requested_time if requested_time is not None else now), 6),
                forced=forced,
                cast_index=cast_index,
            )
        )
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
        old_ammo = float(ammo.get("current", 0.0))
        ammo["current"] = max(0.0, float(ammo.get("current", 0.0)) - 1.0)
        ammo["empty"] = False
        emit_card_attribute_change(
            player,
            monster,
            rules_by_source,
            scheduler,
            now,
            timeline,
            rng,
            changed_ref=ref,
            attribute="Ammo",
            old_value=old_ammo,
            new_value=float(ammo.get("current", 0.0)),
            source_side=source_side,
            source=fired,
            change_kind="ammo",
            duration_sec=math.inf,
            card_index=card_index,
        )
    source_side.uses[fired.placement_id] = source_side.uses.get(fired.placement_id, 0.0) + 1.0

    performed = {
        "damage": 0,
        "burn": 0,
        "poison": 0,
        "burn_cleanse": 0,
        "poison_cleanse": 0,
        "crit": 0,
        "slow": 0,
        "haste": 0,
        "freeze": 0,
        "reload": 0,
        "destruction": 0,
        "shield": 0,
        "heal": 0,
        "regen": 0,
        "rage": 0,
        "attribute_delta": 0,
        "enraged": 0,
    }
    pre_applied_rule_ids = _run_pre_damage_self_on_fire_rules(
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
        card_index=card_index,
    )

    merged_bonus = _merged_bonus(source_side)
    crit_multiplier, crits = crit_multiplier_for_casts(fired, source_side.cards, merged_bonus, casts, rng)
    damage = _battle_amount(
        fired,
        source_side,
        "TActionPlayerDamage",
        "DamageAmount",
        casts,
        _battle_crit_multiplier(fired, source_side, casts, crits, "DamageAmount"),
        opponent_only=True,
    )
    burn = _battle_amount(
        fired,
        source_side,
        "TActionPlayerBurnApply",
        "BurnApplyAmount",
        casts,
        _battle_crit_multiplier(fired, source_side, casts, crits, "BurnApplyAmount"),
        opponent_only=True,
    )
    poison = _battle_amount(
        fired,
        source_side,
        "TActionPlayerPoisonApply",
        "PoisonApplyAmount",
        casts,
        _battle_crit_multiplier(fired, source_side, casts, crits, "PoisonApplyAmount"),
        opponent_only=True,
    )
    burn_cleanse = _battle_amount(fired, source_side, "TActionPlayerBurnRemove", "BurnRemoveAmount", casts, 1.0)
    poison_cleanse = _battle_amount(fired, source_side, "TActionPlayerPoisonRemove", "PoisonRemoveAmount", casts, 1.0)
    shield = _battle_amount(
        fired,
        source_side,
        "TActionPlayerShieldApply",
        "ShieldApplyAmount",
        casts,
        _battle_crit_multiplier(fired, source_side, casts, crits, "ShieldApplyAmount"),
    )
    heal = (
        _battle_amount(
            fired,
            source_side,
            "TActionPlayerHealApply",
            "HealAmount",
            casts,
            _battle_crit_multiplier(fired, source_side, casts, crits, "HealAmount"),
        )
        + _battle_amount(
            fired,
            source_side,
            "TActionPlayerHeal",
            "HealAmount",
            casts,
            _battle_crit_multiplier(fired, source_side, casts, crits, "HealAmount"),
        )
    )
    regen = _battle_amount(fired, source_side, "TActionPlayerRegenApply", "RegenApplyAmount", casts, crit_multiplier)
    rage = _battle_amount(fired, source_side, "TActionPlayerRageApply", "RageApplyAmount", casts, 1.0)
    performed["damage"] = casts if damage > 0 else 0
    performed["burn"] = casts if burn > 0 else 0
    performed["poison"] = casts if poison > 0 else 0
    performed["crit"] = crits
    performed["shield"] = casts if shield > 0 else 0
    performed["regen"] = casts if regen > 0 else 0
    performed["rage"] = casts if rage > 0 else 0
    performed["attribute_delta"] = rage

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
        heal_result = _apply_heal(source_side, heal, now, card_label(fired), timeline, return_result=True)
        assert isinstance(heal_result, HealResult)
        if heal_result.effective_amount > 0:
            performed["heal"] += 1
        _run_overheal_triggered_rules(
            player,
            monster,
            rules_by_source,
            scheduler,
            now,
            timeline,
            rng,
            healing_ref=ref,
            target_side=source_side,
            heal_result=heal_result,
            duration_sec=math.inf,
            source_skill=f"{fired.placement_id}:on-use",
            card_index=card_index,
        )
    if burn_cleanse:
        removed = cleanse_combat_status(source_side, "burn", burn_cleanse, now, card_label(fired), timeline, source_side=source_side)
        if removed > 0:
            performed["burn_cleanse"] = max(1, casts)
    if poison_cleanse:
        removed = cleanse_combat_status(source_side, "poison", poison_cleanse, now, card_label(fired), timeline, source_side=source_side)
        if removed > 0:
            performed["poison_cleanse"] = max(1, casts)
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
        skip_rule_ids=pre_applied_rule_ids,
        card_index=card_index,
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


def _battle_crit_multiplier(
    card: PlacedCard,
    side: BattleSide,
    casts: int,
    crits: float,
    amount_attr: str,
) -> float:
    return crit_multiplier_from_crits(
        card,
        side.cards,
        _merged_bonus(side),
        casts,
        crits,
        CRIT_BONUS_ATTR_BY_AMOUNT_ATTR.get(amount_attr, ""),
    )


def _run_fight_started_actions(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    duration_sec: float,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source):
                continue
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
                    card_index=card_index,
                )


def _rule_execution_sort_key(rule: Any) -> tuple[int, int, tuple[int, ...], str]:
    return (
        int(getattr(rule, "priority_rank", 30) or 30),
        int(getattr(rule, "source_order", 0) or 0),
        action_path_sort_key(getattr(rule, "action_path", "0")),
        str(getattr(rule, "action_type", "")),
    )


def _matches_direct_self_on_fire_rule(source_side: BattleSide, source: PlacedCard, rule: Any, fired_ref: BattleCardRef, performed: dict[str, float]) -> bool:
    return (
        source_side is fired_ref.side
        and source.placement_id == fired_ref.card.placement_id
        and "." not in str(getattr(rule, "action_path", "0"))
        and _battle_trigger_matches(source_side, fired_ref.side, source, fired_ref.card, rule, performed)
    )


def _run_pre_damage_self_on_fire_rules(
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
    card_index: dict[str, dict[str, Any]] | None = None,
) -> set[str]:
    source_side = fired_ref.side
    other_side = _opponent(player, monster, source_side)
    source = fired_ref.card
    matching_rules = [
        rule
        for rule in rules_by_source.get(source.placement_id, [])
        if _matches_direct_self_on_fire_rule(source_side, source, rule, fired_ref, performed)
    ]
    first_player_action_key = min(
        (_rule_execution_sort_key(rule) for rule in matching_rules if rule.action_type in SELF_ON_USE_PLAYER_ACTIONS),
        default=None,
    )
    if first_player_action_key is None:
        return set()

    applied_rule_ids: set[str] = set()
    for rule in matching_rules:
        rule_id = str(getattr(rule, "effect_id", "") or "")
        if rule.action_type in SELF_ON_USE_PLAYER_ACTIONS or _rule_execution_sort_key(rule) >= first_player_action_key:
            continue
        if not _consume_trigger_count(source_side, source, rule, fired_ref, now, timeline):
            continue
        amount = max(0.0, _battle_rule_amount(source_side, other_side, source, rule, performed)) * casts
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
            trigger_depth=1,
            card_index=card_index,
        )
        if rule_id:
            applied_rule_ids.add(rule_id)
    return applied_rule_ids


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
    skip_rule_ids: set[str] | None = None,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(_battle_event(now, fired_ref.side.name, fired_ref.side.name, "trigger-depth-limited", card_label(fired_ref.card), trigger_depth))
        return
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source):
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                if skip_rule_ids and str(getattr(rule, "effect_id", "") or "") in skip_rule_ids:
                    continue
                lower_trigger = rule.trigger_type.lower()
                if (
                    "fightstarted" in lower_trigger
                    or "playerdied" in lower_trigger
                    or "beforecarddestroyed" in lower_trigger
                    or "carddisabled" in lower_trigger
                    or "cardtransformed" in lower_trigger
                    or "performedoverheal" in lower_trigger
                    or "cardattributechanged" in lower_trigger
                ):
                    continue
                if _skip_self_on_fire_rule(source, rule, fired_ref):
                    continue
                if not _battle_trigger_matches(source_side, fired_ref.side, source, fired_ref.card, rule, performed):
                    continue
                if not _consume_trigger_count(source_side, source, rule, fired_ref, now, timeline):
                    continue
                amount = max(0.0, _battle_rule_amount(source_side, other_side, source, rule, performed)) * casts
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
                    card_index=card_index,
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
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(_battle_event(now, event_side.name, event_side.name, "trigger-depth-limited", card_label(event_card), trigger_depth))
        return
    fired_ref = BattleCardRef(event_side, event_card)
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source):
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                lower_trigger = rule.trigger_type.lower()
                if (
                    "fightstarted" in lower_trigger
                    or "playerdied" in lower_trigger
                    or "beforecarddestroyed" in lower_trigger
                    or "carddisabled" in lower_trigger
                    or "cardtransformed" in lower_trigger
                    or "performedoverheal" in lower_trigger
                    or "cardattributechanged" in lower_trigger
                ):
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
                    card_index=card_index,
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
        rule.action_type in SELF_ON_USE_PLAYER_ACTIONS
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
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if not _is_current_card_instance(source_side, source):
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                source_side.name,
                "battle-action-cancelled",
                card_label(source),
                0.0,
                reason="source-replaced",
                action_type=rule.action_type,
            )
        )
        return
    targets = _resolve_battle_targets(source_side, other_side, source, trigger_card, rule, rng)
    amount = _battle_rule_amount(source_side, other_side, source, rule, performed) * max(1, casts)
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
                old_value = _card_attribute_value(target, "Haste", now)
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
                emit_card_attribute_change(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    changed_ref=target,
                    attribute="Haste",
                    old_value=old_value,
                    new_value=_card_attribute_value(target, "Haste", now),
                    source_side=source_side,
                    source=source,
                    change_kind="runtime",
                    duration_sec=duration_sec,
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )
        performed["haste"] = performed.get("haste", 0) + len(targets)
    elif rule.action_type == "TActionCardSlow":
        for target in targets:
            if target.card.placement_id in target.side.slow_until:
                old_value = _card_attribute_value(target, "Slow", now)
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
                emit_card_attribute_change(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    changed_ref=target,
                    attribute="Slow",
                    old_value=old_value,
                    new_value=_card_attribute_value(target, "Slow", now),
                    source_side=source_side,
                    source=source,
                    change_kind="runtime",
                    duration_sec=duration_sec,
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )
        performed["slow"] = performed.get("slow", 0) + len(targets)
    elif rule.action_type == "TActionCardFreeze":
        for target in targets:
            if target.card.placement_id in target.side.freeze_until:
                old_value = _card_attribute_value(target, "Freeze", now)
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
                emit_card_attribute_change(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    changed_ref=target,
                    attribute="Freeze",
                    old_value=old_value,
                    new_value=_card_attribute_value(target, "Freeze", now),
                    source_side=source_side,
                    source=source,
                    change_kind="runtime",
                    duration_sec=duration_sec,
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )
        performed["freeze"] = performed.get("freeze", 0) + len(targets)
    elif rule.action_type in {"TActionCardFlyingStart", "TActionCardFlyingStop", "TActionCardFlyingToggle"}:
        for target in targets:
            old_value = _card_attribute_value(target, "Flying", now)
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
            emit_card_attribute_change(
                player,
                monster,
                rules_by_source,
                scheduler,
                now,
                timeline,
                rng,
                changed_ref=target,
                attribute="Flying",
                old_value=old_value,
                new_value=_card_attribute_value(target, "Flying", now),
                source_side=source_side,
                source=source,
                change_kind="runtime",
                duration_sec=duration_sec,
                trigger_depth=trigger_depth + 1,
                card_index=card_index,
            )
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
                card_index=card_index,
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
            old_ammo = float(ammo.get("current", 0.0))
            ammo["current"] = min(float(ammo.get("max", 0.0)), float(ammo.get("current", 0.0)) + amount)
            ammo["empty"] = ammo["current"] <= 0
            timeline.append(_battle_event(now, source_side.name, target.side.name, "reload", card_label(source), amount, target=card_label(target.card)))
            emit_card_attribute_change(
                player,
                monster,
                rules_by_source,
                scheduler,
                now,
                timeline,
                rng,
                changed_ref=target,
                attribute="Ammo",
                old_value=old_ammo,
                new_value=float(ammo.get("current", 0.0)),
                source_side=source_side,
                source=source,
                change_kind="ammo",
                duration_sec=duration_sec,
                trigger_depth=trigger_depth + 1,
                card_index=card_index,
            )
        performed["reload"] = performed.get("reload", 0) + len(targets)
    elif rule.action_type in {"TActionCardDisable", "TActionCardDestroy"}:
        destroyed = 0
        for target in targets:
            if destroy_card(
                player,
                monster,
                target,
                source_side,
                source,
                rules_by_source,
                scheduler,
                now,
                timeline,
                rng,
                duration_sec,
                trigger_depth=trigger_depth + 1,
                card_index=card_index,
            ):
                destroyed += 1
        performed["destruction"] = performed.get("destruction", 0) + destroyed
    elif rule.action_type == "TActionCardRepair":
        repaired = 0
        for target in targets:
            if repair_card(target, source_side, source, now, timeline):
                repaired += 1
        performed["repair"] = performed.get("repair", 0) + repaired
    elif rule.action_type == "TActionCardTransform":
        transformed_cards: list[BattleCardRef] = []
        seen_targets: set[str] = set()
        for target in targets:
            if target.card.placement_id in seen_targets:
                continue
            seen_targets.add(target.card.placement_id)
            new_card = transform_card_instance(
                target,
                source_side,
                source,
                rule,
                card_index or {},
                rules_by_source,
                now,
                timeline,
                rng,
                player=player,
                monster=monster,
                trigger_card=trigger_card,
                transform_type="normal",
                action_type="TActionCardTransform",
            )
            if new_card is not None:
                transformed_cards.append(BattleCardRef(target.side, new_card))
        transformed = len(transformed_cards)
        performed["transform"] = performed.get("transform", 0) + transformed
        if transformed:
            _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
            for transformed_ref in transformed_cards:
                _run_transformed_triggered_rules(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    transformed_ref=transformed_ref,
                    performed={"transform": 1},
                    casts=casts,
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )
    elif rule.action_type == "TActionCardTransformDestroyed":
        transformed_cards: list[BattleCardRef] = []
        for target in targets:
            new_card = transform_destroyed_card(
                target,
                source_side,
                source,
                rule,
                card_index or {},
                rules_by_source,
                now,
                timeline,
                rng,
            )
            if new_card is not None:
                transformed_cards.append(BattleCardRef(target.side, new_card))
        transformed = len(transformed_cards)
        performed["transform"] = performed.get("transform", 0) + transformed
        if transformed:
            _refresh_runtime_state_auras(player, monster, scheduler, now, timeline, rng)
            for transformed_ref in transformed_cards:
                _run_transformed_triggered_rules(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    transformed_ref=transformed_ref,
                    performed={"transform": 1},
                    casts=casts,
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )
    elif rule.action_type in {"TActionCardEnchant", "TActionCardEnchantRandom"}:
        enchanted = 0
        for target in targets:
            enchantment = _pick_enchantment_for_rule(rule, rng)
            if _apply_card_enchantment(target, source_side, source, enchantment, rule, now, timeline):
                enchanted += 1
        performed["enchant"] = performed.get("enchant", 0) + enchanted
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
    elif rule.action_type == "TActionCardBeginSandstorm":
        started = _start_sandstorm(
            scheduler.sandstorm_state,
            scheduler.sandstorm_config,
            now,
            timeline,
            trigger_source=card_label(source),
            trigger_mode="active",
        )
        if started:
            performed["sandstorm"] = performed.get("sandstorm", 0) + 1
            _run_sandstorm_triggered_rules(
                player,
                monster,
                rules_by_source,
                scheduler,
                now,
                timeline,
                rng,
                duration_sec,
                trigger_depth=trigger_depth + 1,
                card_index=card_index,
            )
    elif rule.action_type == "TActionCardModifyAttribute":
        state_key = _runtime_state_key(rule.attribute_type)
        if state_key in RUNTIME_CARD_STATES:
            for target in targets:
                old_value = _card_attribute_value(target, rule.attribute_type, now)
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
                    emit_card_attribute_change(
                        player,
                        monster,
                        rules_by_source,
                        scheduler,
                        now,
                        timeline,
                        rng,
                        changed_ref=target,
                        attribute=rule.attribute_type,
                        old_value=old_value,
                        new_value=_card_attribute_value(target, rule.attribute_type, now),
                        source_side=source_side,
                        source=source,
                        change_kind="runtime",
                        duration_sec=duration_sec,
                        trigger_depth=trigger_depth + 1,
                        card_index=card_index,
                    )
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
                            card_index=card_index,
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
        if mapped in CARD_STATUS_ATTRIBUTES:
            for target in targets:
                old_value = _card_attribute_value(target, mapped, now)
                if _modify_card_status_attribute(target, source_side, source, mapped, amount, rule.operation, now, timeline):
                    emit_card_attribute_change(
                        player,
                        monster,
                        rules_by_source,
                        scheduler,
                        now,
                        timeline,
                        rng,
                        changed_ref=target,
                        attribute=mapped,
                        old_value=old_value,
                        new_value=_card_attribute_value(target, mapped, now),
                        source_side=source_side,
                        source=source,
                        change_kind="runtime",
                        duration_sec=duration_sec,
                        trigger_depth=trigger_depth + 1,
                        card_index=card_index,
                    )
            return
        if not mapped:
            return
        for target in targets:
            if target.card.placement_id in target.side.bonus.get(mapped, {}):
                old_value = _card_attribute_value(target, mapped, now)
                current = target.side.bonus[mapped][target.card.placement_id]
                target.side.bonus[mapped][target.card.placement_id] = apply_attribute_operation(current, amount, rule.operation)
                timeline.append(_battle_event(now, source_side.name, target.side.name, "modify-attribute", card_label(source), amount, target=card_label(target.card), attribute=mapped))
                emit_card_attribute_change(
                    player,
                    monster,
                    rules_by_source,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    changed_ref=target,
                    attribute=mapped,
                    old_value=old_value,
                    new_value=_card_attribute_value(target, mapped, now),
                    source_side=source_side,
                    source=source,
                    change_kind="runtime",
                    duration_sec=duration_sec,
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )
    else:
        target_side = _player_target_side(player, monster, source_side, rule)
        if rule.action_type == "TActionPlayerRageApply":
            performed["rage"] = performed.get("rage", 0) + max(1, casts)
            performed["attribute_delta"] = amount
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
        _apply_player_action(
            player,
            monster,
            rules_by_source,
            scheduler,
            source_side,
            target_side,
            source,
            rule,
            amount,
            now,
            timeline,
            duration_sec,
            rng,
            trigger_depth=trigger_depth,
            card_index=card_index,
        )
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
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    source_side: BattleSide,
    target_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    amount: float,
    now: float,
    timeline: list[dict[str, Any]],
    duration_sec: float,
    rng: Callable[[], float] | None,
    *,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if rule.action_type == "TActionPlayerDamage":
        _apply_damage(source_side, target_side, amount, now, card_label(source), "damage", timeline)
    elif rule.action_type == "TActionPlayerBurnApply":
        target_side.burn_stack += amount
        timeline.append(_battle_event(now, source_side.name, target_side.name, "burn-apply", card_label(source), amount))
    elif rule.action_type == "TActionPlayerPoisonApply":
        target_side.poison_stack += amount
        timeline.append(_battle_event(now, source_side.name, target_side.name, "poison-apply", card_label(source), amount))
    elif rule.action_type == "TActionPlayerBurnRemove":
        cleanse_combat_status(target_side, "burn", amount, now, card_label(source), timeline, source_side=source_side)
    elif rule.action_type == "TActionPlayerPoisonRemove":
        cleanse_combat_status(target_side, "poison", amount, now, card_label(source), timeline, source_side=source_side)
    elif rule.action_type == "TActionPlayerShieldApply":
        target_side.shield += amount
        timeline.append(_battle_event(now, source_side.name, target_side.name, "shield", card_label(source), amount))
    elif rule.action_type in {"TActionPlayerHealApply", "TActionPlayerHeal"}:
        heal_result = _apply_heal(target_side, amount, now, card_label(source), timeline, return_result=True)
        assert isinstance(heal_result, HealResult)
        _run_overheal_triggered_rules(
            player,
            monster,
            rules_by_source,
            scheduler,
            now,
            timeline,
            rng,
            healing_ref=BattleCardRef(source_side, source),
            target_side=target_side,
            heal_result=heal_result,
            duration_sec=duration_sec,
            source_skill=getattr(rule, "effect_id", ""),
            trigger_depth=trigger_depth + 1,
            card_index=card_index,
        )
    elif rule.action_type == "TActionPlayerReviveHeal":
        if target_side.health <= 0:
            revived = amount if amount > 0 else target_side.max_health * 0.5
            target_side.health = min(target_side.max_health, max(1.0, revived))
            target_side.shield = 0.0
            timeline.append(
                _battle_event(
                    now,
                    source_side.name,
                    target_side.name,
                    "revive",
                    card_label(source),
                    target_side.health,
                    revive_consumed=True,
                )
            )
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
    if rule.target_type == "TTargetCardTriggerTarget":
        trigger_side = source_side if any(card is trigger_card for card in source_side.cards) else other_side
        trigger_pool = targetable_cards(trigger_side.cards)
        if rule.action_type in {"TActionCardRepair", "TActionCardTransformDestroyed"}:
            trigger_pool = [card for card in trigger_pool if is_card_destroyed(trigger_side, card)]
        else:
            trigger_pool = [card for card in trigger_pool if not is_card_destroyed(trigger_side, card)]
        if not any(card is trigger_card for card in trigger_pool):
            return []
        if rule.target_exclude_self and trigger_side is source_side and trigger_card.placement_id == source.placement_id:
            return []
        if rule.target_condition.not_trigger_source:
            return []
        if not _battle_card_matches(BattleCardRef(trigger_side, trigger_card), rule.target_condition):
            return []
        return [BattleCardRef(trigger_side, trigger_card)]

    cards = target_side.cards
    if rule.action_type in {"TActionCardRepair", "TActionCardTransformDestroyed"}:
        pool = [card for card in targetable_cards(cards) if is_card_destroyed(target_side, card)]
    else:
        pool = [card for card in targetable_cards(cards) if not is_card_destroyed(target_side, card)]

    def match(card: PlacedCard) -> bool:
        if rule.target_exclude_self and target_side is source_side and card.placement_id == source.placement_id:
            return False
        if rule.target_condition.not_trigger_source and card.placement_id == trigger_card.placement_id:
            return False
        return _battle_card_matches(BattleCardRef(target_side, card), rule.target_condition)

    if rule.target_type == "TTargetCardSelf":
        if rule.action_type in {"TActionCardRepair", "TActionCardTransformDestroyed"} and not is_card_destroyed(source_side, source):
            return []
        if rule.action_type not in {"TActionCardRepair", "TActionCardTransformDestroyed"} and is_card_destroyed(source_side, source):
            return []
        return [BattleCardRef(source_side, source)] if match(source) else []
    if rule.target_type == "TTargetCardTriggerSource":
        trigger_side = source_side if any(card is trigger_card for card in source_side.cards) else other_side
        if not any(card is trigger_card for card in targetable_cards(trigger_side.cards)):
            return []
        if is_card_destroyed(trigger_side, trigger_card):
            return []
        return [BattleCardRef(trigger_side, trigger_card)] if _battle_card_matches(BattleCardRef(trigger_side, trigger_card), rule.target_condition) else []
    if rule.target_type == "TTargetCardSection":
        return [BattleCardRef(target_side, card) for card in pool if match(card)]
    if rule.target_type == "TTargetCardXMost":
        chosen = pick_x_most([card for card in pool if match(card)], rule.target_mode or "RightMostCard")
        return [BattleCardRef(target_side, chosen)] if chosen else []
    if rule.target_type == "TTargetCardRandom":
        candidates = [card for card in pool if match(card)]
        count = min(len(candidates), _effective_target_count(source_side, source, rule))
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


def _effective_target_count(source_side: BattleSide, source: PlacedCard, rule: Any) -> int:
    if rule.target_count:
        return max(1, int(rule.target_count))
    attr = TARGET_COUNT_ATTR_BY_ACTION.get(str(rule.action_type or ""))
    if not attr:
        return 1
    value = get_attr_value_by_tier(source.card, attr, effective_tier(source))
    value += _bonus_value(source_side, attr, source.placement_id)
    return max(1, int(round(value or 1)))


def _modify_card_status_attribute(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    attribute: str,
    amount: float,
    operation: str,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    from combat_simulator import normalize_seconds

    status_key = attribute.lower()
    status_maps = {
        "freeze": target.side.freeze_until,
        "slow": target.side.slow_until,
        "haste": target.side.haste_until,
    }
    status_until = status_maps.get(status_key)
    if status_until is None or target.card.placement_id not in status_until:
        return False
    old_until = float(status_until.get(target.card.placement_id, 0.0) or 0.0)
    normalized_amount = normalize_seconds(float(amount or 0.0), f"{attribute}Amount")
    if operation == "Subtract":
        new_until = max(now, old_until - normalized_amount)
    elif operation == "Set":
        new_until = now + normalized_amount
    else:
        new_until = max(old_until, now + normalized_amount)
    if abs(new_until - old_until) <= 1e-9:
        return False
    status_until[target.card.placement_id] = new_until
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target.side.name,
            f"{status_key}-modified",
            card_label(source),
            new_until - old_until,
            target=card_label(target.card),
            old_until=old_until,
            new_until=new_until,
            attribute=attribute,
            operation=operation,
        )
    )
    return True


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
        "performedoverheal": "overheal",
        "performedregen": "regen",
        "performedreload": "reload",
        "performeddestruction": "destruction",
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
    if "sandstorm" in lower:
        return performed.get("sandstorm", 0) > 0 and _trigger_side_matches(source_side, fired_side, rule)
    if "beforecarddestroyed" in lower:
        return performed.get("before_destroyed", 0) > 0 and _trigger_side_matches(source_side, fired_side, rule)
    if "carddisabled" in lower:
        return performed.get("destroyed", 0) > 0 and _trigger_side_matches(source_side, fired_side, rule)
    if "cardtransformed" in lower:
        return (
            performed.get("transform", 0) > 0
            and _trigger_side_matches(source_side, fired_side, rule)
            and _battle_card_matches(BattleCardRef(fired_side, fired), rule.trigger_condition)
        )
    if "performedoverheal" in lower:
        return (
            performed.get("overheal", 0) > 0
            and _trigger_side_matches(source_side, fired_side, rule)
            and _battle_card_matches(BattleCardRef(fired_side, fired), rule.trigger_condition)
        )
    return False


def _trigger_side_matches(source_side: BattleSide, event_side: BattleSide, rule: Any) -> bool:
    mode = str(rule.trigger_subject_mode or "").lower()
    if "opponent" in mode:
        return source_side is not event_side
    return source_side is event_side


def destroy_card(
    player: BattleSide,
    monster: BattleSide,
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    duration_sec: float,
    *,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> bool:
    if is_card_destroyed(target.side, target.card):
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-destroy-ignored",
                card_label(source),
                0.0,
                target=card_label(target.card),
                reason="already-destroyed",
                action_type="TActionCardDisable",
            )
        )
        return False
    if target.card.placement_id in target.side.destroy_pending:
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-destroy-ignored",
                card_label(source),
                0.0,
                target=card_label(target.card),
                reason="destroy-pending",
                action_type="TActionCardDisable",
            )
        )
        return False

    target.side.destroy_pending.add(target.card.placement_id)
    try:
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-destroy-requested",
                card_label(source),
                0.0,
                target=card_label(target.card),
                action_type="TActionCardDisable",
            )
        )
        _run_destroy_triggered_rules(
            player,
            monster,
            rules_by_source,
            scheduler,
            now,
            timeline,
            rng,
            target=target,
            performed={"before_destroyed": 1},
            duration_sec=duration_sec,
            trigger_depth=trigger_depth + 1,
            card_index=card_index,
        )
        if is_card_destroyed(target.side, target.card):
            return False
        target.side.destroyed.add(target.card.placement_id)
        target.side.destroyed_by[target.card.placement_id] = card_label(source)
        source_side.destroy_count[source.placement_id] = source_side.destroy_count.get(source.placement_id, 0) + 1
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-destroyed",
                card_label(source),
                1.0,
                target=card_label(target.card),
                action_type="TActionCardDisable",
                target_destroyed=True,
            )
        )
        _run_destroy_triggered_rules(
            player,
            monster,
            rules_by_source,
            scheduler,
            now,
            timeline,
            rng,
            target=target,
            performed={"destroyed": 1},
            duration_sec=duration_sec,
            trigger_depth=trigger_depth + 1,
            card_index=card_index,
        )
        _run_state_triggered_rules(
            player,
            monster,
            rules_by_source,
            scheduler,
            now,
            timeline,
            rng,
            event_side=source_side,
            event_card=source,
            performed={"destruction": 1},
            trigger_depth=trigger_depth + 1,
            card_index=card_index,
        )
        return True
    finally:
        target.side.destroy_pending.discard(target.card.placement_id)


def repair_card(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    if not is_card_destroyed(target.side, target.card):
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-repair-ignored",
                card_label(source),
                0.0,
                target=card_label(target.card),
                reason="not-destroyed",
            )
        )
        return False
    target.side.destroyed.discard(target.card.placement_id)
    target.side.destroyed_by.pop(target.card.placement_id, None)
    source_side.repair_count[source.placement_id] = source_side.repair_count.get(source.placement_id, 0) + 1
    timeline.append(
        _battle_event(
            now,
            source_side.name,
            target.side.name,
            "card-repaired",
            card_label(source),
            1.0,
            target=card_label(target.card),
            preserves_instance=True,
        )
    )
    return True


def transform_destroyed_card(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    card_index: dict[str, dict[str, Any]],
    rules_by_source: dict[str, list[Any]],
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
) -> PlacedCard | None:
    return transform_card_instance(
        target,
        source_side,
        source,
        rule,
        card_index,
        rules_by_source,
        now,
        timeline,
        rng,
        transform_type="destroyed_card",
        action_type="TActionCardTransformDestroyed",
    )


def transform_card_instance(
    target: BattleCardRef,
    source_side: BattleSide,
    source: PlacedCard,
    rule: Any,
    card_index: dict[str, dict[str, Any]],
    rules_by_source: dict[str, list[Any]],
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    player: BattleSide | None = None,
    monster: BattleSide | None = None,
    trigger_card: PlacedCard | None = None,
    transform_type: str = "normal",
    action_type: str = "TActionCardTransform",
) -> PlacedCard | None:
    old_card = target.card
    if not _is_current_card_instance(target.side, old_card):
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-transform-ignored",
                card_label(source),
                0.0,
                target=card_label(old_card),
                reason="target-replaced",
                action_type=action_type,
            )
        )
        return None
    target_destroyed = is_card_destroyed(target.side, old_card)
    if transform_type == "destroyed_card" and not target_destroyed:
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-transform-ignored",
                card_label(source),
                0.0,
                target=card_label(old_card),
                reason="target-not-destroyed",
                action_type=action_type,
            )
        )
        return None
    if transform_type != "destroyed_card" and target_destroyed:
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-transform-ignored",
                card_label(source),
                0.0,
                target=card_label(old_card),
                reason="target-destroyed",
                action_type=action_type,
            )
        )
        return None
    if old_card.placement_id in target.side.transform_pending:
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-transform-ignored",
                card_label(source),
                0.0,
                target=card_label(old_card),
                reason="transform-pending",
                action_type=action_type,
            )
        )
        return None

    target.side.transform_pending.add(old_card.placement_id)
    try:
        resolved = _resolve_transform_definition(
            rule.raw_action,
            card_index,
            old_card,
            rng,
            player=player,
            monster=monster,
            source_side=source_side,
            source=source,
            trigger_card=trigger_card,
            rule=rule,
        )
        if resolved is None:
            timeline.append(
                _battle_event(
                    now,
                    source_side.name,
                    target.side.name,
                    "unsupported-card-transform",
                    card_label(source),
                    0.0,
                    target=card_label(old_card),
                    reason="spawn-context-unresolved",
                    action_type=action_type,
                )
            )
            return None
        template, tier, resolution = resolved
        position = _card_position(target.side, old_card)
        if position is None:
            timeline.append(
                _battle_event(
                    now,
                    source_side.name,
                    target.side.name,
                    "card-transform-ignored",
                    card_label(source),
                    0.0,
                    target=card_label(old_card),
                    reason="target-missing-from-board",
                    action_type=action_type,
                )
            )
            return None

        old_id = old_card.placement_id
        target.side.transform_sequence += 1
        new_id = f"{old_id}:transformed:{target.side.transform_sequence}"
        new_definition = deepcopy(template)
        old_width = old_card.width or card_width(old_card.card)
        new_card = PlacedCard(
            placement_id=new_id,
            card=new_definition,
            start=old_card.start,
            width=old_width,
            tier=tier,
            cooldown_override_sec=None,
            shield_enchanted=False,
        )

        new_width = card_width(new_definition)
        _clear_card_runtime_state(target.side, old_card)
        target.side.cards[position] = new_card
        _replace_active_card(target.side, old_card, new_card)
        _initialize_card_runtime(target.side, new_card)
        target.side.destroyed.discard(old_id)
        target.side.destroy_pending.discard(old_id)
        target.side.destroyed_by.pop(old_id, None)
        rules_by_source.pop(old_id, None)
        rules_by_source[new_id] = read_rules(new_card, TWO_SIDED_RULE_ACTIONS)
        source_side.transform_count[source.placement_id] = source_side.transform_count.get(source.placement_id, 0) + 1

        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "card-transformed",
                card_label(source),
                1.0,
                target=card_label(new_card),
                old_card=card_label(old_card),
                new_card=card_label(new_card),
                old_placement_id=old_id,
                new_placement_id=new_id,
                position=position,
                transform_type=transform_type,
                action_type=action_type,
                spawn_resolution=resolution,
                width_policy="preserve_old_width",
                old_width=old_width,
                new_definition_width=new_width,
                size_mismatch=old_width != new_width,
            )
        )
        timeline.append(
            _battle_event(
                now,
                source_side.name,
                target.side.name,
                "CARD_TRANSFORMED",
                card_label(source),
                1.0,
                target=card_label(new_card),
                old_card=card_label(old_card),
                new_card=card_label(new_card),
                old_placement_id=old_id,
                new_placement_id=new_id,
                position=position,
                transform_type=transform_type,
                source_card=card_label(source),
                source_skill=getattr(rule, "effect_id", ""),
                owner=target.side.name,
                combat_time_ms=round(now * 1000.0, 3),
            )
        )
        return new_card
    finally:
        target.side.transform_pending.discard(old_card.placement_id)


def _card_position(side: BattleSide, card: PlacedCard) -> int | None:
    for index, current in enumerate(side.cards):
        if current is card:
            return index
    return None


def _replace_active_card(side: BattleSide, old_card: PlacedCard, new_card: PlacedCard) -> None:
    for index, current in enumerate(side.active):
        if current is old_card:
            if get_card_cooldown_sec(new_card, side.cards) > 0:
                side.active[index] = new_card
            else:
                side.active.pop(index)
            return
    if get_card_cooldown_sec(new_card, side.cards) > 0:
        side.active.append(new_card)
        side.active.sort(key=lambda item: item.start)


def _clear_card_runtime_state(side: BattleSide, card: PlacedCard) -> None:
    placement_id = card.placement_id
    for mapping in (
        side.cooldowns,
        side.haste_until,
        side.slow_until,
        side.freeze_until,
        side.ammo,
        side.uses,
        side.destroyed_by,
    ):
        mapping.pop(placement_id, None)
    side.destroyed.discard(placement_id)
    side.destroy_pending.discard(placement_id)
    side.item_runtime_states.pop(placement_id, None)
    side.runtime_tags_by_source.pop(placement_id, None)
    for values in side.bonus.values():
        values.pop(placement_id, None)
    for values in side.runtime_aura_bonus.values():
        values.pop(placement_id, None)


def _initialize_card_runtime(side: BattleSide, card: PlacedCard) -> None:
    cooldown = get_card_cooldown_sec(card, side.cards)
    if cooldown <= 0:
        return
    if not any(current is card for current in side.active):
        side.active.append(card)
        side.active.sort(key=lambda item: item.start)
    # Approximation: transformed destroyed cards restart from their full base cooldown.
    side.cooldowns[card.placement_id] = ItemCooldownState(base_cooldown=cooldown, remaining_cooldown=cooldown)
    side.uses[card.placement_id] = 0.0
    side.haste_until[card.placement_id] = 0.0
    side.slow_until[card.placement_id] = 0.0
    side.freeze_until[card.placement_id] = 0.0
    ammo_max = get_card_ammo_max(card)
    if ammo_max > 0:
        side.ammo[card.placement_id] = {
            "base_max": float(ammo_max),
            "max": float(ammo_max),
            "current": float(ammo_max),
            "empty": False,
        }
    for attr in (
        "DamageAmount",
        "BurnApplyAmount",
        "PoisonApplyAmount",
        "BurnRemoveAmount",
        "PoisonRemoveAmount",
        "ShieldApplyAmount",
        "HealAmount",
        "RegenApplyAmount",
        "RageApplyAmount",
        "CritChance",
        "DamageCrit",
        "BurnCrit",
        "PoisonCrit",
        "ShieldCrit",
        "HealCrit",
        "Lifesteal",
        "Multicast",
        "AmmoMax",
        "ChargeAmount",
        "SlowAmount",
        "FreezeAmount",
        "HasteAmount",
    ):
        side.bonus.setdefault(attr, {})[card.placement_id] = 0.0
    for attr in RUNTIME_AURA_ATTRS:
        side.runtime_aura_bonus.setdefault(attr, {})[card.placement_id] = 0.0


def _resolve_transform_definition(
    raw_action: dict[str, Any],
    card_index: dict[str, dict[str, Any]],
    target: PlacedCard,
    rng: Callable[[], float] | None,
    *,
    player: BattleSide | None = None,
    monster: BattleSide | None = None,
    source_side: BattleSide | None = None,
    source: PlacedCard | None = None,
    trigger_card: PlacedCard | None = None,
    rule: Any | None = None,
) -> tuple[dict[str, Any], str | None, str] | None:
    spawn_context = get_field(raw_action, "SpawnContext", default={}) or {}
    if not isinstance(spawn_context, dict):
        return None
    groups = get_field(spawn_context, "Groups", default=[]) or []
    candidates: list[tuple[dict[str, Any], str | None, str]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_tier = _spawn_tier_from_group(group)
        for filter_node in get_field(group, "Filters", default=[]) or []:
            filter_type = type_name(filter_node)
            if filter_type == "TSpawnFilterIdList":
                for raw_id in get_field(filter_node, "Ids", default=[]) or []:
                    template = card_index.get(str(raw_id).lower())
                    if template is not None:
                        candidates.append((template, group_tier, "id_list"))
            elif filter_type == "TSpawnFilterQuery":
                candidates.extend(
                    (template, group_tier, "query")
                    for template in _spawn_query_candidates(filter_node, card_index)
                )
            elif filter_type == "TSpawnFilterTarget":
                target_filter_cards = _spawn_target_filter_candidates(
                    filter_node,
                    player,
                    monster,
                    source_side,
                    source,
                    trigger_card,
                    rule,
                    rng,
                )
                candidates.extend((card.card, group_tier, "target") for card in target_filter_cards)
    if not candidates:
        return None
    inherit_tier = _spawn_behavior_enabled(spawn_context, "TSpawnBehaviorInheritTier", "Inherits")
    chosen = _pick_spawn_candidate(candidates, rng)
    template, group_tier, resolution = chosen
    target_tier = target.tier or effective_tier(target)
    tier = target_tier if inherit_tier else (group_tier or target_tier)
    return template, tier, resolution


def _spawn_target_filter_candidates(
    filter_node: dict[str, Any],
    player: BattleSide | None,
    monster: BattleSide | None,
    source_side: BattleSide | None,
    source: PlacedCard | None,
    trigger_card: PlacedCard | None,
    rule: Any | None,
    rng: Callable[[], float] | None,
) -> list[PlacedCard]:
    if player is None or monster is None or source_side is None or source is None or rule is None:
        return []
    target_node = get_field(filter_node, "Target", default={}) or {}
    if not isinstance(target_node, dict):
        return []
    other_side = _opponent(player, monster, source_side)
    target_rule = _rule_with_target_node(rule, target_node)
    return [
        ref.card
        for ref in _resolve_battle_targets(source_side, other_side, source, trigger_card or source, target_rule, rng)
        if _is_current_card_instance(ref.side, ref.card) and not is_card_destroyed(ref.side, ref.card)
    ]


def _rule_with_target_node(rule: Any, target_node: dict[str, Any]) -> Any:
    count = get_field(target_node, "TargetCount", default=getattr(rule, "target_count", None))
    return replace(
        rule,
        target_type=type_name(target_node),
        target_mode=str(get_field(target_node, "TargetMode", default="")),
        target_section=str(get_field(target_node, "TargetSection", default="")),
        target_count=int(count) if isinstance(count, (int, float)) and count > 0 else None,
        target_exclude_self=bool(get_field(target_node, "ExcludeSelf", default=False)),
        target_include_origin=bool(get_field(target_node, "IncludeOrigin", default=False)),
        target_condition=extract_condition_meta(get_field(target_node, "Conditions")),
    )


def _spawn_query_candidates(filter_node: dict[str, Any], card_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    unique_cards = _unique_card_templates(card_index)
    constraints = _flatten_spawn_constraints(get_field(filter_node, "Constraints", default={}) or {})
    return sorted(
        [card for card in unique_cards if _card_matches_spawn_constraints(card, constraints)],
        key=lambda item: str(item.get("name") or item.get("internal_name") or item.get("template_id") or item.get("id") or ""),
    )


def _unique_card_templates(card_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    cards: list[dict[str, Any]] = []
    for card in card_index.values():
        marker = id(card)
        if marker in seen:
            continue
        seen.add(marker)
        cards.append(card)
    return cards


def _flatten_spawn_constraints(node: Any) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    node_type = type_name(node)
    if node_type in {"ConstraintAnd", "ConstraintOr"}:
        out: list[dict[str, Any]] = []
        for child in get_field(node, "Constraints", default=[]) or []:
            out.extend(_flatten_spawn_constraints(child))
        return out
    return [node]


def _card_matches_spawn_constraints(card: dict[str, Any], constraints: list[dict[str, Any]]) -> bool:
    for constraint in constraints:
        constraint_type = type_name(constraint)
        if constraint_type == "ConstraintCardType":
            allowed = {str(item).lower() for item in get_field(constraint, "Types", default=[]) or []}
            card_type = str(card.get("type") or card.get("card_type") or "").lower()
            if allowed and card_type not in allowed:
                return False
        elif constraint_type == "ConstraintTag":
            required = {normalize_tag(item) for item in get_field(constraint, "Tags", default=[]) or [] if normalize_tag(item)}
            tags = {
                normalize_tag(item)
                for key in ("tags", "hidden_tags", "visible_tags")
                for item in (card.get(key) or [])
                if normalize_tag(item)
            }
            if required and not required.issubset(tags):
                return False
        elif constraint_type == "ConstraintSize":
            allowed_sizes = {str(item).lower() for item in get_field(constraint, "Sizes", default=[]) or []}
            if allowed_sizes and str(card.get("size") or "").lower() not in allowed_sizes:
                return False
        elif constraint_type == "ConstraintTier":
            continue
        else:
            return False
    return True


def _spawn_tier_from_group(group: dict[str, Any]) -> str | None:
    for filter_node in get_field(group, "Filters", default=[]) or []:
        for constraint in _flatten_spawn_constraints(get_field(filter_node, "Constraints", default={}) or {}):
            if type_name(constraint) != "ConstraintTier":
                continue
            tiers = [str(item) for item in get_field(constraint, "Tiers", default=[]) or [] if item]
            if tiers:
                return tiers[0]
    return None


def _spawn_behavior_enabled(spawn_context: dict[str, Any], behavior_type: str, field_name: str) -> bool:
    for behavior in get_field(spawn_context, "Behaviors", default=[]) or []:
        if type_name(behavior) != behavior_type:
            continue
        value = get_field(behavior, field_name)
        if value is not None:
            return bool(value)
        raw_value = str(get_field(behavior, "$value", default="")).lower()
        return "true" in raw_value
    return False


def _pick_spawn_candidate(
    candidates: list[tuple[dict[str, Any], str | None, str]],
    rng: Callable[[], float] | None,
) -> tuple[dict[str, Any], str | None, str]:
    if len(candidates) == 1:
        return candidates[0]
    ordered = sorted(
        candidates,
        key=lambda item: str(item[0].get("name") or item[0].get("internal_name") or item[0].get("template_id") or item[0].get("id") or ""),
    )
    index = 0 if rng is None else max(0, min(len(ordered) - 1, int(rng() * len(ordered))))
    return ordered[index]


def _run_transformed_triggered_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    transformed_ref: BattleCardRef,
    performed: dict[str, float],
    casts: int = 1,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(_battle_event(now, transformed_ref.side.name, transformed_ref.side.name, "trigger-depth-limited", card_label(transformed_ref.card), trigger_depth))
        return
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            if is_card_destroyed(source_side, source) or not _is_current_card_instance(source_side, source):
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                lower_trigger = rule.trigger_type.lower()
                if "cardtransformed" not in lower_trigger:
                    continue
                if not _battle_trigger_matches(source_side, transformed_ref.side, source, transformed_ref.card, rule, performed):
                    continue
                if not _consume_trigger_count(source_side, source, rule, transformed_ref, now, timeline):
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
                    transformed_ref.card,
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
                    card_index=card_index,
                )


def _run_destroy_triggered_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    *,
    target: BattleCardRef,
    performed: dict[str, float],
    duration_sec: float,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(_battle_event(now, target.side.name, target.side.name, "trigger-depth-limited", card_label(target.card), trigger_depth))
        return
    for source_side, other_side in ((player, monster), (monster, player)):
        for source in source_side.cards:
            allow_destroyed_source = performed.get("destroyed", 0) > 0 and source is target.card
            if is_card_destroyed(source_side, source) and not allow_destroyed_source:
                continue
            for rule in rules_by_source.get(source.placement_id, []):
                lower = str(rule.trigger_type or "").lower()
                if performed.get("before_destroyed", 0) > 0:
                    if "beforecarddestroyed" not in lower:
                        continue
                elif performed.get("destroyed", 0) > 0:
                    if "carddisabled" not in lower:
                        continue
                else:
                    continue
                if not _battle_trigger_matches(source_side, target.side, source, target.card, rule, performed):
                    continue
                if not _consume_trigger_count(source_side, source, rule, target, now, timeline):
                    continue
                amount = max(0.0, rule.amount)
                preview_amount = _effective_rule_amount(source_side, source, rule, amount, 1)
                if preview_amount <= 0 and rule.action_type not in AMOUNTLESS_RULE_ACTIONS:
                    continue
                _apply_battle_rule(
                    player,
                    monster,
                    source_side,
                    other_side,
                    source,
                    target.card,
                    rule,
                    amount,
                    1,
                    scheduler,
                    now,
                    timeline,
                    rng,
                    rules_by_source,
                    duration_sec,
                    dict(performed),
                    trigger_depth=trigger_depth + 1,
                    card_index=card_index,
                )


def _next_sandstorm_time(
    state: SandstormState,
    config: SandstormConfig,
    now: float,
) -> float:
    if not config.enabled:
        return math.inf
    if state.active:
        return state.next_tick_time
    return config.start_sec if config.start_sec >= now - 1e-6 else now


def _process_sandstorm_time(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    state: SandstormState,
    config: SandstormConfig,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    duration_sec: float,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> bool:
    if not config.enabled:
        return False
    if not state.active:
        started = _start_sandstorm(
            state,
            config,
            now,
            timeline,
            trigger_source="natural",
            trigger_mode="natural",
        )
        if started:
            _run_sandstorm_triggered_rules(
                player,
                monster,
                rules_by_source,
                scheduler,
                now,
                timeline,
                rng,
                duration_sec,
                card_index=card_index,
            )
        return False
    if now + 1e-6 < state.next_tick_time:
        return False
    return _process_sandstorm_tick(player, monster, state, config, now, timeline)


def _start_sandstorm(
    state: SandstormState | None,
    config: SandstormConfig,
    now: float,
    timeline: list[dict[str, Any]],
    *,
    trigger_source: str,
    trigger_mode: str,
) -> bool:
    if state is None or not config.enabled:
        return False
    if state.active:
        state.duplicate_starts += 1
        timeline.append(
            _battle_event(
                now,
                "environment",
                "all",
                "sandstorm-start-ignored",
                trigger_source,
                0.0,
                existing_started_at=state.started_at,
                duplicate_starts=state.duplicate_starts,
            )
        )
        return False
    state.active = True
    state.started_at = now
    state.next_tick_time = now + config.tick_interval_sec
    state.tick_index = 0
    state.trigger_source = trigger_source
    state.trigger_mode = trigger_mode
    timeline.append(
        _battle_event(
            now,
            "environment",
            "all",
            "sandstorm-start",
            trigger_source,
            0.0,
            trigger_mode=trigger_mode,
            next_tick_time=round(state.next_tick_time, 6),
        )
    )
    return True


def _run_sandstorm_triggered_rules(
    player: BattleSide,
    monster: BattleSide,
    rules_by_source: dict[str, list[Any]],
    scheduler: BattleEventScheduler,
    now: float,
    timeline: list[dict[str, Any]],
    rng: Callable[[], float] | None,
    duration_sec: float,
    *,
    trigger_depth: int = 0,
    card_index: dict[str, dict[str, Any]] | None = None,
) -> None:
    if trigger_depth >= MAX_TRIGGER_DEPTH:
        timeline.append(_battle_event(now, "environment", "all", "trigger-depth-limited", "sandstorm", trigger_depth))
        return
    for event_side in (player, monster):
        event_card = _first_event_card(event_side)
        if event_card is None:
            continue
        fired_ref = BattleCardRef(event_side, event_card)
        for source_side, other_side in ((player, monster), (monster, player)):
            for source in source_side.cards:
                if is_card_destroyed(source_side, source):
                    continue
                for rule in rules_by_source.get(source.placement_id, []):
                    if "sandstorm" not in str(rule.trigger_type or "").lower():
                        continue
                    if not _battle_trigger_matches(source_side, event_side, source, event_card, rule, {"sandstorm": 1}):
                        continue
                    if not _consume_trigger_count(source_side, source, rule, fired_ref, now, timeline):
                        continue
                    amount = max(0.0, rule.amount)
                    preview_amount = _effective_rule_amount(source_side, source, rule, amount, 1)
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
                        1,
                        scheduler,
                        now,
                        timeline,
                        rng,
                        rules_by_source,
                        duration_sec,
                        {"sandstorm": 1},
                        trigger_depth=trigger_depth + 1,
                        card_index=card_index,
                    )


def _process_sandstorm_tick(
    player: BattleSide,
    monster: BattleSide,
    state: SandstormState,
    config: SandstormConfig,
    now: float,
    timeline: list[dict[str, Any]],
) -> bool:
    damage = _sandstorm_tick_damage(state.tick_index, config)
    state.last_tick_damage = damage
    before_player_health = player.health
    before_monster_health = monster.health
    before_player_shield = player.shield
    before_monster_shield = monster.shield
    _apply_damage(None, player, damage, now, "sandstorm", "sandstorm-tick", timeline)
    _apply_damage(None, monster, damage, now, "sandstorm", "sandstorm-tick", timeline)
    timeline.append(
        _battle_event(
            now,
            "environment",
            "all",
            "sandstorm-tick-summary",
            "sandstorm",
            damage,
            tick_index=state.tick_index,
            raw_damage=damage,
            player_health_loss=max(0.0, before_player_health - player.health),
            monster_health_loss=max(0.0, before_monster_health - monster.health),
            player_shield_loss=max(0.0, before_player_shield - player.shield),
            monster_shield_loss=max(0.0, before_monster_shield - monster.shield),
        )
    )
    state.tick_index += 1
    state.next_tick_time = now + config.tick_interval_sec
    return _battle_winner(player, monster) != ""


def _sandstorm_tick_damage(tick_index: int, config: SandstormConfig = DEFAULT_SANDSTORM_CONFIG) -> float:
    damage = config.initial_damage + max(0, int(tick_index)) * config.damage_increment
    return float(min(damage, config.max_tick_damage))


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
        side.burn_stack = max(0.0, side.burn_stack - burn_decay_amount(side.burn_stack, config))
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
        stack_regen = max(0.0, side.regen_stack)
        health_regen = _player_attribute_value(side, "HealthRegen")
        regen = stack_regen + health_regen
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
                stack_regen=stack_regen,
                health_regen=health_regen,
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
    source_side: BattleSide | None,
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
    source_side_name = source_side.name if source_side is not None else "environment"
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
            source_side_name,
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
        timeline.append(_battle_event(now, source_side_name, target_side.name, "would-die", source, actual))
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
    return_result: bool = False,
) -> float | HealResult:
    if amount <= 0 or side.health <= 0:
        result = HealResult(
            requested_amount=max(0.0, float(amount or 0.0)),
            effective_amount=0.0,
            overheal_amount=0.0,
            health_before=side.health,
            health_after=side.health,
            max_health=side.max_health,
            overheal_event_emitted=False,
        )
        return result if return_result else 0.0
    condition_before = _condition_snapshot(side)
    before = side.health
    actual_heal = min(amount, max(0.0, side.max_health - before))
    overheal = max(0.0, amount - actual_heal)
    side.health = min(side.max_health, side.health + actual_heal)
    overheal_event_emitted = False
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
        overheal_event_emitted = True
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
    result = HealResult(
        requested_amount=amount,
        effective_amount=actual_heal,
        overheal_amount=overheal if overheal_event_emitted else 0.0,
        health_before=before,
        health_after=side.health,
        max_health=side.max_health,
        overheal_event_emitted=overheal_event_emitted,
    )
    return result if return_result else actual_heal


def cleanse_combat_status(
    target_side: BattleSide,
    status_type: str,
    amount: float | None,
    now: float,
    source: str,
    timeline: list[dict[str, Any]],
    *,
    source_side: BattleSide | None = None,
) -> float:
    status = str(status_type or "").strip().lower()
    if status not in {"burn", "poison"}:
        return 0.0
    try:
        requested = float(amount or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(requested) or requested <= 0:
        return 0.0
    old_value = max(0.0, target_side.burn_stack if status == "burn" else target_side.poison_stack)
    if old_value <= 0:
        return 0.0
    removed = min(old_value, requested)
    new_value = max(0.0, old_value - removed)
    if status == "burn":
        target_side.burn_stack = new_value
    else:
        target_side.poison_stack = new_value
    timeline.append(
        _battle_event(
            now,
            source_side.name if source_side is not None else target_side.name,
            target_side.name,
            f"{status}-cleansed",
            source,
            removed,
            old_stack=old_value,
            new_stack=new_value,
            requested_amount=requested,
            removed_amount=removed,
            status=status,
        )
    )
    return removed


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
    *,
    end_reason: str = "",
    sandstorm: SandstormState | None = None,
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
        player_remaining_shield=max(0.0, player.shield),
        monster_remaining_shield=max(0.0, monster.shield),
        end_reason=end_reason or ("combat" if winner != "draw" else "timeout"),
        sandstorm=_sandstorm_summary(sandstorm),
    )


def _sandstorm_summary(state: SandstormState | None) -> dict[str, Any]:
    if state is None:
        return {
            "started": False,
            "start_time_sec": None,
            "trigger_source": "",
            "trigger_mode": "",
            "ticks": 0,
            "final_tick_damage": 0.0,
            "duplicate_starts": 0,
        }
    return {
        "started": state.active,
        "start_time_sec": round(state.started_at, 6) if state.started_at is not None else None,
        "trigger_source": state.trigger_source,
        "trigger_mode": state.trigger_mode,
        "ticks": state.tick_index,
        "final_tick_damage": state.last_tick_damage,
        "duplicate_starts": state.duplicate_starts,
    }


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
        "sandstorm-start": EVENT_PRIORITY["STATUS_TICK"],
        "sandstorm-start-ignored": EVENT_PRIORITY["STATUS_TICK"],
        "sandstorm-tick": EVENT_PRIORITY["STATUS_TICK"],
        "sandstorm-tick-summary": EVENT_PRIORITY["STATUS_TICK"],
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
    sandstorm_config: SandstormConfig,
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
        "player_cards": _feedback_card_labels(player.placements),
        "monster_cards": _feedback_card_labels(monster.placements),
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
    last_end_reason: str | None = None
    last_sandstorm: dict[str, Any] | None = None
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
                    sandstorm_config=sandstorm_config,
                    card_catalog=data.get("cards", {}),
                )
                outcome = {
                    "winner": battle.winner,
                    "duration": battle.duration,
                    "monster_damage": battle.monster_damage,
                    "player_remaining_health": battle.player_remaining_health,
                    "monster_remaining_health": battle.monster_remaining_health,
                    "end_reason": battle.end_reason,
                    "sandstorm": battle.sandstorm,
                }
                last_timeline = battle.timeline
                last_end_reason = battle.end_reason
                last_sandstorm = battle.sandstorm
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
        "end_reason": last_end_reason,
        "sandstorm": last_sandstorm,
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
            if _noncombat_unsupported_trigger(trigger_type):
                continue
            for action_path, action_node in action_nodes:
                action_type = type_name(action_node)
                if action_type in NONCOMBAT_UNSUPPORTED_ACTIONS:
                    continue
                if action_type and action_type not in SUPPORTED_ACTIONS:
                    unsupported.append(
                        {
                            "side": side,
                            "card": _card_name(card),
                            "card_type": _simulation_card_type(card),
                            "effect": action_type,
                            "reason": "unsupported_action",
                            "action_path": action_path,
                        }
                    )
                unsupported_attr = _unsupported_modified_attribute(action_node)
                if unsupported_attr and not _noncombat_unsupported_attribute(unsupported_attr):
                    unsupported.append(
                        {
                            "side": side,
                            "card": _card_name(card),
                            "card_type": _simulation_card_type(card),
                            "effect": unsupported_attr,
                            "reason": "unsupported_attribute",
                            "action_path": action_path,
                        }
                    )
                for value_type in _unsupported_value_reference_types(action_node):
                    unsupported.append(
                        {
                            "side": side,
                            "card": _card_name(card),
                            "card_type": _simulation_card_type(card),
                            "effect": value_type,
                            "reason": "unsupported_value",
                            "action_path": action_path,
                        }
                    )
            if trigger_type and not _trigger_supported(trigger_type):
                unsupported.append(
                    {
                        "side": side,
                        "card": _card_name(card),
                        "card_type": _simulation_card_type(card),
                        "effect": trigger_type,
                        "reason": "unsupported_trigger",
                    }
                )
    return _dedupe(unsupported)


def _simulation_card_type(card: PlacedCard) -> str:
    return "skill" if is_skill_card(card) else "item"


def _noncombat_unsupported_trigger(trigger_type: str) -> bool:
    return str(trigger_type or "") in NONCOMBAT_UNSUPPORTED_TRIGGERS


def _noncombat_unsupported_attribute(attribute_type: str) -> bool:
    normalized = _normalize_battle_attribute(attribute_type)
    return normalized in NONCOMBAT_UNSUPPORTED_ATTRIBUTES or str(attribute_type or "") in NONCOMBAT_UNSUPPORTED_ATTRIBUTES


def _unsupported_modified_attribute(action_node: dict[str, Any]) -> str:
    if type_name(action_node) not in {"TAuraActionCardModifyAttribute", "TActionCardModifyAttribute"}:
        return ""
    attr = str(get_field(action_node, "AttributeType", default="") or "")
    if not attr:
        return ""
    normalized = _normalize_battle_attribute(attr)
    supported = RUNTIME_AURA_ATTRS | COOLDOWN_AURA_ATTRS | PLAYER_ATTRIBUTE_AURA_ATTRS
    if normalized in supported or normalized in CARD_STATUS_ATTRIBUTES or _runtime_state_attribute(attr):
        return ""
    return attr


def _unsupported_value_reference_types(node: Any) -> list[str]:
    unsupported: list[str] = []
    if isinstance(node, dict):
        node_type = type_name(node)
        if "ReferenceValue" in node_type and not (
            "ReferenceValueCardAttribute" in node_type
            or "ReferenceValuePlayerAttribute" in node_type
            or "ReferenceValueCardCount" in node_type
            or "ReferenceValueCardTagCount" in node_type
            or "ReferenceValueAttributeChange" in node_type
        ):
            unsupported.append(node_type)
        for value in node.values():
            unsupported.extend(_unsupported_value_reference_types(value))
    elif isinstance(node, list):
        for value in node:
            unsupported.extend(_unsupported_value_reference_types(value))
    return _dedupe(unsupported)


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


def _feedback_card_labels(cards: list[PlacedCard]) -> list[str]:
    labels: list[str] = []
    for card in targetable_cards(cards):
        label = card_label(card)
        tier = str(card.tier or effective_tier(card) or "")
        labels.append(f"{label} [{tier}]" if tier else label)
    return labels


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
    sandstorm_config: SandstormConfig,
) -> str:
    payload = {
        "data_version": data.get("data_version"),
        "player": _jsonable_state(player_state),
        "monsters": monster_choices,
        "simulations": simulations,
        "duration_sec": duration_sec,
        "seed": seed,
        "sandstorm_config": _jsonable_state(sandstorm_config),
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
