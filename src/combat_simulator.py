from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
import random
import re
from typing import Any, Callable


TIER_ORDER = ["Bronze", "Silver", "Gold", "Diamond", "Legendary"]
TIME_LIKE_ATTRIBUTES = {"ChargeAmount", "HasteAmount", "FreezeAmount", "SlowAmount"}


@dataclass(frozen=True)
class PlacedCard:
    placement_id: str
    card: dict[str, Any]
    start: int = 0
    width: int | None = None
    tier: str | None = None
    cooldown_override_sec: float | None = None
    shield_enchanted: bool = False


@dataclass
class RuleCondition:
    include_tags: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    include_sizes: list[str] = field(default_factory=list)
    exclude_sizes: list[str] = field(default_factory=list)
    attr_conditions: list[dict[str, Any]] = field(default_factory=list)
    require_cooldown: bool = False
    not_trigger_source: bool = False
    mode: str = "and"


@dataclass
class EffectRule:
    source_id: str
    action_type: str
    trigger_type: str
    amount: float
    attribute_type: str = ""
    target_type: str = ""
    target_mode: str = ""
    target_section: str = ""
    target_count: int | None = None
    target_exclude_self: bool = False
    target_include_origin: bool = False
    target_condition: RuleCondition = field(default_factory=RuleCondition)
    trigger_subject_type: str = ""
    trigger_subject_mode: str = ""
    trigger_condition: RuleCondition = field(default_factory=RuleCondition)
    trigger_exclude_self: bool = False
    trigger_attribute_changed: str = ""
    trigger_change_type: str = ""


@dataclass
class CombatSummary:
    duration_sec: float
    total_uses: float
    by_card: dict[str, float]
    total_damage: float
    total_burn_applied: float
    total_poison_applied: float
    total_burn_tick_damage: float
    total_poison_tick_damage: float
    total_shield: float
    by_card_damage: dict[str, float]
    by_card_burn: dict[str, float]
    by_card_poison: dict[str, float]
    by_card_shield: dict[str, float]
    cumulative_damage_by_second: list[float]
    debug_timeline: list[dict[str, Any]]
    random_trials: int | None = None
    total_damage_min: float | None = None
    total_damage_max: float | None = None
    total_damage_avg: float | None = None


@dataclass
class SelfTtkEstimate:
    target_health: float
    horizon_sec: float
    kill_time_sec: float | None
    direct_kill_time_sec: float | None
    total_damage: float
    direct_damage: float
    total_burn_tick_damage: float
    total_poison_tick_damage: float
    simulated_card_count: int
    skipped_cards: list[dict[str, Any]]
    by_card_uses: dict[str, float]
    by_card_damage: dict[str, float]
    cumulative_damage_by_second: list[float]
    timeline: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_health": self.target_health,
            "horizon_sec": self.horizon_sec,
            "kill_time_sec": self.kill_time_sec,
            "direct_kill_time_sec": self.direct_kill_time_sec,
            "total_damage": self.total_damage,
            "direct_damage": self.direct_damage,
            "total_burn_tick_damage": self.total_burn_tick_damage,
            "total_poison_tick_damage": self.total_poison_tick_damage,
            "simulated_card_count": self.simulated_card_count,
            "skipped_cards": self.skipped_cards,
            "by_card_uses": self.by_card_uses,
            "by_card_damage": self.by_card_damage,
            "cumulative_damage_by_second": self.cumulative_damage_by_second,
            "timeline": self.timeline,
        }

def type_name(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    value = str(node.get("$type") or node.get("type") or "")
    return value.rsplit(".", 1)[-1]


def get_field(node: Any, *names: str, default: Any = None) -> Any:
    if not isinstance(node, dict):
        return default
    for name in names:
        if name in node:
            return node[name]
        lower = name[:1].lower() + name[1:]
        if lower in node:
            return node[lower]
    return default


def normalize_tier(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "legendary" in text:
        return "Legendary"
    if "diamond" in text:
        return "Diamond"
    if "gold" in text:
        return "Gold"
    if "silver" in text:
        return "Silver"
    return "Bronze"


def tier_index(tier: str) -> int:
    try:
        return TIER_ORDER.index(normalize_tier(tier))
    except ValueError:
        return 0


def normalize_tag(value: Any) -> str:
    return str(value or "").strip().lower()


def normalize_size(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "small" in text:
        return "small"
    if "medium" in text:
        return "medium"
    if "large" in text:
        return "large"
    return text


def normalize_seconds(value: float, attr_type: str = "") -> float:
    if not math.isfinite(value) or value <= 0:
        return 0.0
    if attr_type in TIME_LIKE_ATTRIBUTES and abs(value) >= 100:
        return value / 1000.0
    if attr_type.lower() in {"cooldownmax", "cooldown"} and abs(value) >= 100:
        return value / 1000.0
    return value


def card_width(card: dict[str, Any]) -> int:
    size = normalize_size(card.get("size"))
    if size == "large":
        return 3
    if size == "medium":
        return 2
    return 1


def coerce_placed_cards(cards: list[PlacedCard | dict[str, Any]]) -> list[PlacedCard]:
    placed: list[PlacedCard] = []
    cursor = 0
    for index, item in enumerate(cards):
        if isinstance(item, PlacedCard):
            width = item.width or card_width(item.card)
            placed.append(
                PlacedCard(
                    placement_id=item.placement_id,
                    card=item.card,
                    start=item.start,
                    width=width,
                    tier=item.tier,
                    cooldown_override_sec=item.cooldown_override_sec,
                    shield_enchanted=item.shield_enchanted,
                )
            )
            continue
        card = item.get("card") if isinstance(item.get("card"), dict) else item
        width = int(item.get("width") or card_width(card))
        start = int(item.get("start", cursor))
        placement_id = str(item.get("placement_id") or item.get("placementId") or card.get("id") or card.get("name") or index)
        placed.append(
            PlacedCard(
                placement_id=placement_id,
                card=card,
                start=start,
                width=width,
                tier=item.get("tier"),
                cooldown_override_sec=item.get("cooldown_override_sec") or item.get("cooldownOverrideSec"),
                shield_enchanted=bool(item.get("shield_enchanted") or item.get("shieldEnchanted")),
            )
        )
        cursor = max(cursor, start + width)
    return placed


def effective_tier(card: PlacedCard) -> str:
    requested = normalize_tier(card.tier or card.card.get("rarity") or card.card.get("min_rarity"))
    tiers = [normalize_tier(x) for x in card.card.get("tiers", []) if x]
    if not tiers:
        tiers_raw = card.card.get("raw_effects", {}).get("tiers_raw", {})
        tiers = [normalize_tier(x) for x in tiers_raw.keys()]
    return requested if requested in tiers or not tiers else tiers[0]


def effect_rows(card: dict[str, Any]) -> list[dict[str, Any]]:
    raw = card.get("raw_effects") or {}
    rows: list[dict[str, Any]] = []
    for group_name in ("abilities", "auras"):
        group = raw.get(group_name) or {}
        if isinstance(group, dict):
            rows.extend(v for v in group.values() if isinstance(v, dict))
        elif isinstance(group, list):
            rows.extend(v for v in group if isinstance(v, dict))
    return rows


def tier_attributes(card: dict[str, Any], tier: str) -> dict[str, Any]:
    raw = card.get("raw_effects") or {}
    tiers = raw.get("tiers_raw") or {}
    preferred = normalize_tier(tier)
    if preferred in tiers and isinstance(tiers[preferred], dict):
        return tiers[preferred].get("Attributes") or tiers[preferred].get("attributes") or {}
    for label in TIER_ORDER:
        if label in tiers and isinstance(tiers[label], dict):
            attrs = tiers[label].get("Attributes") or tiers[label].get("attributes") or {}
            if attrs:
                return attrs
    return {}


def get_attr_value_by_tier(card: dict[str, Any], attr_type: str, tier: str) -> float:
    attr_type = str(attr_type or "")
    attrs = tier_attributes(card, tier)
    value = attrs.get(attr_type)
    if value is None:
        lower = attr_type.lower()
        for key, candidate in attrs.items():
            if str(key).lower() == lower:
                value = candidate
                break
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return normalize_seconds(number, attr_type)


def resolve_value(value_node: Any, source: PlacedCard) -> float:
    if value_node is None:
        return 0.0
    if isinstance(value_node, (int, float)):
        return float(value_node)
    if not isinstance(value_node, dict):
        return 0.0

    node_type = type_name(value_node)
    if "FixedValue" in node_type:
        try:
            return float(get_field(value_node, "Value", default=0) or 0)
        except (TypeError, ValueError):
            return 0.0

    if "ReferenceValueCardAttribute" in node_type:
        attr = str(get_field(value_node, "AttributeType", default=""))
        base = get_attr_value_by_tier(source.card, attr, effective_tier(source))
        modifier = get_field(value_node, "Modifier")
        if isinstance(modifier, dict):
            mode = str(get_field(modifier, "ModifyMode", default=""))
            mv = resolve_value(get_field(modifier, "Value"), source)
            if mode == "Multiply":
                base *= mv
            elif mode == "Add":
                base += mv
            if bool(get_field(modifier, "ShouldRound", default=False)):
                base = round(base)
        return base

    if "RangeValue" in node_type:
        for key in ("DefaultValue", "MinValue", "MaxValue"):
            try:
                return float(get_field(value_node, key, default=0) or 0)
            except (TypeError, ValueError):
                pass

    try:
        return float(get_field(value_node, "Value", "DefaultValue", default=0) or 0)
    except (TypeError, ValueError):
        return 0.0


def action_amount(action: dict[str, Any], source: PlacedCard, default_attr: str = "") -> float:
    value_node = get_field(action, "Value")
    if value_node is not None:
        value = resolve_value(value_node, source)
        if math.isfinite(value):
            return normalize_seconds(value, str(get_field(action, "AttributeType", default=default_attr)))
    attr = str(get_field(action, "AttributeType", default=default_attr) or default_attr)
    return get_attr_value_by_tier(source.card, attr, effective_tier(source))


def default_attribute_for_action(action_type: str) -> str:
    return {
        "TActionPlayerDamage": "DamageAmount",
        "TActionPlayerBurnApply": "BurnApplyAmount",
        "TActionPlayerPoisonApply": "PoisonApplyAmount",
        "TActionPlayerShieldApply": "ShieldApplyAmount",
        "TActionCardCharge": "ChargeAmount",
        "TActionCardHaste": "HasteAmount",
        "TActionCardSlow": "SlowAmount",
        "TActionCardFreeze": "FreezeAmount",
        "TActionCardReload": "ReloadAmount",
    }.get(action_type, "")


def card_tags(card: PlacedCard, aura_tags: dict[str, set[str]] | None = None) -> set[str]:
    tags = set()
    for key in ("tags", "hidden_tags", "visible_tags"):
        for value in card.card.get(key, []) or []:
            tags.add(normalize_tag(value))
    if card.shield_enchanted:
        tags.add("shield")
    if aura_tags and card.placement_id in aura_tags:
        tags.update(aura_tags[card.placement_id])
    return tags


def get_card_cooldown_sec(card: PlacedCard, cards: list[PlacedCard] | None = None) -> float:
    if card.cooldown_override_sec is not None:
        return max(0.0, float(card.cooldown_override_sec))
    cooldown = get_attr_value_by_tier(card.card, "CooldownMax", effective_tier(card))
    if cooldown <= 0:
        cooldown = get_attr_value_by_tier(card.card, "Cooldown", effective_tier(card))
    if cooldown <= 0:
        return 0.0
    if not any(type_name(get_field(row, "Trigger")) in {"TTriggerOnCardFired", "TTriggerOnItemUsed"} for row in effect_rows(card.card)):
        return 0.0
    return cooldown


def get_card_ammo_max(card: PlacedCard) -> int:
    return max(0, int(round(get_attr_value_by_tier(card.card, "AmmoMax", effective_tier(card)))))


def extract_condition_meta(node: Any) -> RuleCondition:
    meta = RuleCondition()
    if not isinstance(node, dict):
        return meta

    node_type = type_name(node)
    lower_type = node_type.lower()
    if "conditionalor" in lower_type:
        meta.mode = "or"

    tags = [str(x) for x in (get_field(node, "Tags", "HiddenTags", default=[]) or [])]
    operator = str(get_field(node, "Operator", default="")).lower()
    is_not = bool(get_field(node, "IsNot", default=False))
    if tags:
        if operator == "none" or is_not:
            meta.exclude_tags.extend(tags)
        else:
            meta.include_tags.extend(tags)

    if "size" in lower_type:
        sizes = [str(x) for x in (get_field(node, "Sizes", default=[]) or [])]
        if is_not:
            meta.exclude_sizes.extend(sizes)
        else:
            meta.include_sizes.extend(sizes)

    if "attribute" in lower_type:
        attr = str(get_field(node, "Attribute", default=""))
        op = str(get_field(node, "ComparisonOperator", default=""))
        value = resolve_value(get_field(node, "ComparisonValue"), PlacedCard("__dummy__", {}, 0, 1, "Bronze"))
        if attr and op:
            meta.attr_conditions.append({"attribute": attr, "operator": op, "value": value})
        if attr.lower().startswith("cooldown") and normalize_comparator(op) == "gt" and value >= 0:
            meta.require_cooldown = True

    if "triggersource" in lower_type and is_not:
        meta.not_trigger_source = True

    children = []
    for key in ("Conditions", "conditions"):
        child = get_field(node, key)
        if isinstance(child, list):
            children.extend(child)
        elif isinstance(child, dict):
            children.append(child)
    for child in children:
        nested = extract_condition_meta(child)
        meta.include_tags.extend(nested.include_tags)
        meta.exclude_tags.extend(nested.exclude_tags)
        meta.include_sizes.extend(nested.include_sizes)
        meta.exclude_sizes.extend(nested.exclude_sizes)
        meta.attr_conditions.extend(nested.attr_conditions)
        meta.require_cooldown = meta.require_cooldown or nested.require_cooldown
        meta.not_trigger_source = meta.not_trigger_source or nested.not_trigger_source
        if nested.mode == "or":
            meta.mode = "or"

    meta.include_tags = sorted(set(meta.include_tags))
    meta.exclude_tags = sorted(set(meta.exclude_tags))
    meta.include_sizes = sorted(set(meta.include_sizes))
    meta.exclude_sizes = sorted(set(meta.exclude_sizes))
    return meta


def normalize_comparator(op: str) -> str:
    text = str(op or "").strip().lower()
    return {
        "equal": "eq",
        "==": "eq",
        "notequal": "ne",
        "!=": "ne",
        "greaterthan": "gt",
        ">": "gt",
        "greaterthanorequal": "ge",
        ">=": "ge",
        "lessthan": "lt",
        "<": "lt",
        "lessthanorequal": "le",
        "<=": "le",
    }.get(text, "")


def compare_number(left: float, op: str, right: float) -> bool:
    cmp = normalize_comparator(op)
    if cmp == "eq":
        return left == right
    if cmp == "ne":
        return left != right
    if cmp == "gt":
        return left > right
    if cmp == "ge":
        return left >= right
    if cmp == "lt":
        return left < right
    if cmp == "le":
        return left <= right
    return False


def resolve_card_attribute(card: PlacedCard, attr_name: str, cards: list[PlacedCard], aura_tags: dict[str, set[str]] | None = None) -> float:
    lower = str(attr_name or "").lower()
    if lower in {"cooldownmax", "cooldown"}:
        return get_card_cooldown_sec(card, cards)
    if lower == "ammomax":
        return float(get_card_ammo_max(card))
    if lower == "flying":
        return 1.0 if "flying" in card_tags(card, aura_tags) else 0.0
    return get_attr_value_by_tier(card.card, attr_name, effective_tier(card))


def matches_card(
    card: PlacedCard,
    condition: RuleCondition,
    cards: list[PlacedCard],
    aura_tags: dict[str, set[str]] | None = None,
) -> bool:
    if condition.require_cooldown and get_card_cooldown_sec(card, cards) <= 0:
        return False
    tags = card_tags(card, aura_tags)
    include_tags = {normalize_tag(x) for x in condition.include_tags if normalize_tag(x)}
    exclude_tags = {normalize_tag(x) for x in condition.exclude_tags if normalize_tag(x)}
    if tags & exclude_tags:
        return False
    size = normalize_size(card.card.get("size"))
    include_sizes = {normalize_size(x) for x in condition.include_sizes if normalize_size(x)}
    exclude_sizes = {normalize_size(x) for x in condition.exclude_sizes if normalize_size(x)}
    if size in exclude_sizes:
        return False
    tag_pass = True if not include_tags else bool(tags & include_tags)
    size_pass = True if not include_sizes else size in include_sizes
    attr_checks = [
        compare_number(
            resolve_card_attribute(card, str(c.get("attribute")), cards, aura_tags),
            str(c.get("operator")),
            float(c.get("value", 0)),
        )
        for c in condition.attr_conditions
    ]
    attr_pass = all(attr_checks) if condition.mode != "or" else (any(attr_checks) if attr_checks else True)
    if condition.mode == "or" and include_tags and include_sizes:
        return (tag_pass or size_pass) and attr_pass
    return tag_pass and size_pass and attr_pass


def expand_trigger_branches(trigger: Any) -> list[dict[str, Any]]:
    if not isinstance(trigger, dict):
        return [{"type": "", "subject": {}, "raw": {}}]
    if type_name(trigger) == "TTriggerOr":
        out: list[dict[str, Any]] = []
        for child in get_field(trigger, "Triggers", default=[]) or []:
            out.extend(expand_trigger_branches(child))
        return out or [{"type": "", "subject": {}, "raw": {}}]
    return [{"type": type_name(trigger), "subject": get_field(trigger, "Subject", default={}) or {}, "raw": trigger}]


def read_rules(card: PlacedCard, action_types: set[str] | None = None) -> list[EffectRule]:
    rules: list[EffectRule] = []
    for row in effect_rows(card.card):
        action = get_field(row, "Action", default={}) or {}
        action_type = type_name(action)
        if action_types is not None and action_type not in action_types:
            continue
        target = get_field(action, "Target", default={}) or {}
        target_condition = extract_condition_meta(get_field(target, "Conditions"))
        amount = action_amount(
            action,
            card,
            default_attr=str(get_field(action, "AttributeType", default="") or default_attribute_for_action(action_type)),
        )
        for branch in expand_trigger_branches(get_field(row, "Trigger", default={})):
            subject = branch.get("subject") or {}
            trigger_condition = extract_condition_meta(get_field(subject, "Conditions"))
            raw_trigger = branch.get("raw") or {}
            count = get_field(target, "TargetCount", default=get_field(action, "TargetCount"))
            rules.append(
                EffectRule(
                    source_id=card.placement_id,
                    action_type=action_type,
                    trigger_type=str(branch.get("type") or ""),
                    amount=amount,
                    attribute_type=str(get_field(action, "AttributeType", default="")),
                    target_type=type_name(target),
                    target_mode=str(get_field(target, "TargetMode", default="")),
                    target_section=str(get_field(target, "TargetSection", default="")),
                    target_count=int(count) if isinstance(count, (int, float)) and count > 0 else None,
                    target_exclude_self=bool(get_field(target, "ExcludeSelf", default=False)),
                    target_include_origin=bool(get_field(target, "IncludeOrigin", default=False)),
                    target_condition=target_condition,
                    trigger_subject_type=type_name(subject),
                    trigger_subject_mode=str(get_field(subject, "TargetMode", default="")),
                    trigger_condition=trigger_condition,
                    trigger_exclude_self=bool(get_field(subject, "ExcludeSelf", default=False)),
                    trigger_attribute_changed=str(get_field(raw_trigger, "AttributeChanged", default="")),
                    trigger_change_type=str(get_field(raw_trigger, "ChangeType", default="")),
                )
            )
    return rules


def left_card(cards: list[PlacedCard], source: PlacedCard) -> PlacedCard | None:
    return next((c for c in cards if c.start + (c.width or card_width(c.card)) == source.start), None)


def right_card(cards: list[PlacedCard], source: PlacedCard) -> PlacedCard | None:
    return next((c for c in cards if c.start == source.start + (source.width or card_width(source.card))), None)


def pick_x_most(cards: list[PlacedCard], mode: str) -> PlacedCard | None:
    if not cards:
        return None
    if mode == "LeftMostCard":
        return sorted(cards, key=lambda c: c.start)[0]
    return sorted(cards, key=lambda c: c.start + (c.width or card_width(c.card)))[-1]


def resolve_targets(
    cards: list[PlacedCard],
    source: PlacedCard,
    trigger_card: PlacedCard,
    rule: EffectRule,
    rng: Callable[[], float] | None = None,
    aura_tags: dict[str, set[str]] | None = None,
) -> list[PlacedCard]:
    def match(card: PlacedCard) -> bool:
        if rule.target_exclude_self and card.placement_id == source.placement_id:
            return False
        if rule.target_condition.not_trigger_source and card.placement_id == trigger_card.placement_id:
            return False
        return matches_card(card, rule.target_condition, cards, aura_tags)

    if rule.target_type == "TTargetCardSelf":
        return [source] if match(source) else []
    if rule.target_type == "TTargetCardSection":
        return [c for c in cards if match(c)]
    if rule.target_type == "TTargetCardXMost":
        chosen = pick_x_most([c for c in cards if match(c)], rule.target_mode or "RightMostCard")
        return [chosen] if chosen else []
    if rule.target_type == "TTargetCardRandom":
        pool = [c for c in cards if match(c)]
        if not pool:
            return []
        count = min(len(pool), max(1, int(rule.target_count or 1)))
        if rng is None:
            return sorted(pool, key=lambda c: c.start)[:count]
        mutable = list(pool)
        out: list[PlacedCard] = []
        for _ in range(count):
            if not mutable:
                break
            index = max(0, min(len(mutable) - 1, int(rng() * len(mutable))))
            out.append(mutable.pop(index))
        return out

    left = left_card(cards, source)
    right = right_card(cards, source)
    all_right = sorted(
        [c for c in cards if c.start >= source.start + (source.width or card_width(source.card))],
        key=lambda c: c.start,
    )
    if rule.target_mode == "LeftCard":
        return [left] if left and match(left) else []
    if rule.target_mode == "RightCard":
        return [right] if right and match(right) else []
    if rule.target_mode == "Neighbor":
        return [c for c in (left, right) if c and match(c)]
    if rule.target_mode == "AllRightCards":
        out = [c for c in all_right if match(c)]
        if rule.target_include_origin and match(source):
            return [source, *out]
        return out
    return [source] if rule.target_include_origin and match(source) else []


def trigger_matches(
    cards: list[PlacedCard],
    source: PlacedCard,
    rule: EffectRule,
    fired: PlacedCard,
    performed: dict[str, float],
    aura_tags: dict[str, set[str]] | None = None,
) -> bool:
    trigger = rule.trigger_type
    lower = trigger.lower()
    if not trigger or trigger == "TTriggerOnCardFired":
        if not rule.trigger_subject_type and not rule.trigger_subject_mode and not rule.trigger_condition.include_tags:
            return source.placement_id == fired.placement_id
    if "itemused" in lower or trigger == "TTriggerOnCardFired":
        return matches_card(fired, rule.trigger_condition, cards, aura_tags)
    performed_map = {
        "performedslow": "slow",
        "performedhaste": "haste",
        "performedfreeze": "freeze",
        "performedburn": "burn",
        "performedpoison": "poison",
        "performeddamage": "damage",
        "performedshield": "shield",
        "performedreload": "reload",
        "performeddestruction": "destruction",
    }
    for token, key in performed_map.items():
        if token in lower:
            return performed.get(key, 0) > 0
    return False


def base_on_use_amount(card: PlacedCard, action_type: str, opponent_only: bool = False) -> float:
    total = 0.0
    for row in effect_rows(card.card):
        trigger = type_name(get_field(row, "Trigger", default={}))
        if trigger and trigger != "TTriggerOnCardFired":
            continue
        action = get_field(row, "Action", default={}) or {}
        if type_name(action) != action_type:
            continue
        if opponent_only:
            target = get_field(action, "Target", default={}) or {}
            if not (type_name(target) == "TTargetPlayerRelative" and str(get_field(target, "TargetMode", default="")) == "Opponent"):
                continue
        total += max(
            0.0,
            action_amount(
                action,
                card,
                str(get_field(action, "AttributeType", default="") or default_attribute_for_action(action_type)),
            ),
        )
    return total


def compute_multicast_map(cards: list[PlacedCard]) -> dict[str, int]:
    values: dict[str, int] = {}
    for card in cards:
        multicast = get_attr_value_by_tier(card.card, "Multicast", effective_tier(card))
        values[card.placement_id] = max(1, int(round(multicast or 1)))
    return values


def build_damage_curve(events: list[dict[str, float]], duration_sec: float) -> list[float]:
    max_sec = max(1, int(math.floor(duration_sec)))
    ordered = sorted(events, key=lambda e: e["time"])
    curve: list[float] = []
    index = 0
    total = 0.0
    for sec in range(max_sec + 1):
        while index < len(ordered) and ordered[index]["time"] <= sec + 1e-6:
            total += ordered[index]["amount"]
            index += 1
        curve.append(total)
    return curve


def simulate_combat(
    cards_input: list[PlacedCard | dict[str, Any]],
    duration_sec: float = 20.0,
    *,
    random_trials: int = 1,
    opponent_active_count: int = 7,
    rng: Callable[[], float] | None = None,
    stop_at_damage: float = 0.0,
) -> CombatSummary:
    cards = coerce_placed_cards(cards_input)
    if random_trials > 1 and rng is None:
        runs = [
            simulate_combat(
                cards,
                duration_sec,
                random_trials=1,
                opponent_active_count=opponent_active_count,
                rng=random.random,
                stop_at_damage=stop_at_damage,
            )
            for _ in range(random_trials)
        ]
        return aggregate_summaries(runs, duration_sec)

    rng = rng or (lambda: 0.0)
    active = [card for card in cards if get_card_cooldown_sec(card, cards) > 0]
    multicast = compute_multicast_map(cards)
    rule_types = {
        "TActionCardCharge",
        "TActionCardHaste",
        "TActionCardSlow",
        "TActionCardForceUse",
        "TActionCardReload",
        "TActionCardFreeze",
        "TActionPlayerShieldApply",
        "TActionPlayerPoisonApply",
        "TActionCardModifyAttribute",
    }
    rules_by_source = {card.placement_id: read_rules(card, rule_types) for card in cards}

    cooldown_state = {card.placement_id: get_card_cooldown_sec(card, cards) for card in active}
    haste_until = {card.placement_id: 0.0 for card in active}
    ammo_state = {
        card.placement_id: {"max": get_card_ammo_max(card), "current": get_card_ammo_max(card), "empty": False}
        for card in active
        if get_card_ammo_max(card) > 0
    }
    uses = {card.placement_id: 0.0 for card in active}
    damage_by_card = {card.placement_id: 0.0 for card in active}
    burn_by_card = {card.placement_id: 0.0 for card in active}
    poison_by_card = {card.placement_id: 0.0 for card in active}
    shield_by_card = {card.placement_id: 0.0 for card in active}
    bonus = {
        "DamageAmount": {card.placement_id: 0.0 for card in active},
        "BurnApplyAmount": {card.placement_id: 0.0 for card in active},
        "PoisonApplyAmount": {card.placement_id: 0.0 for card in active},
        "ShieldApplyAmount": {card.placement_id: 0.0 for card in active},
    }

    total_damage = 0.0
    total_burn_applied = 0.0
    total_poison_applied = 0.0
    total_shield = 0.0
    burn_events: list[dict[str, Any]] = []
    poison_events: list[dict[str, Any]] = []
    damage_events: list[dict[str, float]] = []
    timeline: list[dict[str, Any]] = []
    now = 0.0
    guard = 0
    epsilon = 1e-6

    while now < duration_sec and guard < 1600:
        guard += 1
        dt = math.inf
        for card in active:
            ammo = ammo_state.get(card.placement_id)
            if ammo and ammo["empty"]:
                continue
            speed = 2.0 if now < haste_until.get(card.placement_id, 0) else 1.0
            dt = min(dt, cooldown_state[card.placement_id] / speed)
        if not math.isfinite(dt) or now + dt > duration_sec:
            break
        for card in active:
            ammo = ammo_state.get(card.placement_id)
            if ammo and ammo["empty"]:
                continue
            speed = 2.0 if now < haste_until.get(card.placement_id, 0) else 1.0
            cooldown_state[card.placement_id] = max(0.0, cooldown_state[card.placement_id] - dt * speed)
        now += dt
        ready = [card for card in active if cooldown_state[card.placement_id] <= epsilon and not ammo_state.get(card.placement_id, {}).get("empty")]
        queue = [{"card": card, "forced": False} for card in sorted(ready, key=lambda c: c.start)]
        qguard = 0
        while queue and qguard < 260:
            qguard += 1
            event = queue.pop(0)
            fired = event["card"]
            forced = event["forced"]
            ammo = ammo_state.get(fired.placement_id)
            if ammo and ammo["current"] <= 0:
                ammo["empty"] = True
                cooldown_state[fired.placement_id] = 0.0
                continue
            casts = max(1, multicast.get(fired.placement_id, 1))
            if ammo:
                ammo["current"] = max(0, ammo["current"] - 1)
                ammo["empty"] = False
            if not forced:
                cooldown_state[fired.placement_id] += get_card_cooldown_sec(fired, cards)
            uses[fired.placement_id] += casts

            base_damage = base_on_use_amount(fired, "TActionPlayerDamage", opponent_only=True)
            base_burn = base_on_use_amount(fired, "TActionPlayerBurnApply", opponent_only=True)
            base_poison = base_on_use_amount(fired, "TActionPlayerPoisonApply", opponent_only=True)
            dealt = max(0.0, base_damage + bonus["DamageAmount"].get(fired.placement_id, 0.0)) * casts
            burn = max(0.0, base_burn + bonus["BurnApplyAmount"].get(fired.placement_id, 0.0)) * casts
            poison = max(0.0, base_poison + bonus["PoisonApplyAmount"].get(fired.placement_id, 0.0)) * casts
            performed = {
                "damage": casts if dealt > 0 else 0,
                "burn": casts if burn > 0 else 0,
                "poison": casts if poison > 0 else 0,
                "slow": 0,
                "haste": 0,
                "freeze": 0,
                "reload": 0,
                "destruction": 0,
                "shield": 0,
            }
            if dealt:
                total_damage += dealt
                damage_by_card[fired.placement_id] += dealt
                damage_events.append({"time": now, "amount": dealt})
            if burn:
                total_burn_applied += burn
                burn_by_card[fired.placement_id] += burn
                burn_events.append({"time": now, "amount": burn})
            if poison:
                total_poison_applied += poison
                poison_by_card[fired.placement_id] += poison
                poison_events.append({"time": now, "amount": poison})
            timeline.append({"time": now, "kind": "use", "source": card_label(fired), "value": dealt})

            for source in cards:
                for rule in rules_by_source.get(source.placement_id, []):
                    if (
                        rule.action_type == "TActionPlayerPoisonApply"
                        and rule.trigger_type == "TTriggerOnCardFired"
                        and source.placement_id == fired.placement_id
                    ):
                        continue
                    if not trigger_matches(cards, source, rule, fired, performed):
                        continue
                    targets = resolve_targets(cards, source, fired, rule, rng)
                    if not targets and "Opponent" in rule.target_section:
                        performed_key = action_performed_key(rule.action_type)
                        if performed_key:
                            performed[performed_key] += max(0, min(10, opponent_active_count)) * casts
                        continue
                    amount = max(0.0, rule.amount) * casts
                    if amount <= 0:
                        continue
                    if rule.action_type == "TActionCardCharge":
                        for target in targets:
                            if target.placement_id not in cooldown_state:
                                continue
                            cooldown_state[target.placement_id] -= amount
                            timeline.append({"time": now, "kind": "charge", "source": card_label(source), "target": card_label(target), "value": amount})
                            while cooldown_state[target.placement_id] <= epsilon:
                                queue.append({"card": target, "forced": True})
                                cooldown_state[target.placement_id] += get_card_cooldown_sec(target, cards)
                    elif rule.action_type == "TActionCardHaste":
                        for target in targets:
                            if target.placement_id in haste_until:
                                haste_until[target.placement_id] = max(haste_until[target.placement_id], now + amount)
                                performed["haste"] += 1
                    elif rule.action_type == "TActionCardSlow":
                        for target in targets:
                            if target.placement_id in cooldown_state:
                                cooldown_state[target.placement_id] += amount
                                performed["slow"] += 1
                    elif rule.action_type == "TActionCardFreeze":
                        for target in targets:
                            if target.placement_id in cooldown_state:
                                cooldown_state[target.placement_id] += amount
                                performed["freeze"] += 1
                    elif rule.action_type == "TActionCardForceUse":
                        for target in targets:
                            if target.placement_id in cooldown_state:
                                queue.append({"card": target, "forced": True})
                    elif rule.action_type == "TActionCardReload":
                        for target in targets:
                            ammo_target = ammo_state.get(target.placement_id)
                            if not ammo_target:
                                continue
                            ammo_target["current"] = min(ammo_target["max"], ammo_target["current"] + amount)
                            ammo_target["empty"] = ammo_target["current"] <= 0
                            performed["reload"] += 1
                    elif rule.action_type == "TActionPlayerShieldApply":
                        total_shield += amount
                        shield_by_card[source.placement_id] = shield_by_card.get(source.placement_id, 0.0) + amount
                        performed["shield"] += 1
                    elif rule.action_type == "TActionPlayerPoisonApply":
                        total_poison_applied += amount
                        poison_by_card[source.placement_id] = poison_by_card.get(source.placement_id, 0.0) + amount
                        poison_events.append({"time": now, "amount": amount})
                        performed["poison"] += 1
                    elif rule.action_type == "TActionCardModifyAttribute":
                        mapped = normalize_offense_attr(rule.attribute_type)
                        if mapped:
                            for target in targets:
                                if target.placement_id in bonus[mapped]:
                                    bonus[mapped][target.placement_id] += amount
                    performed_key = action_performed_key(rule.action_type)
                    if performed_key:
                        performed[performed_key] += max(1, len(targets)) * casts
            if stop_at_damage > 0 and total_damage >= stop_at_damage:
                break
        if stop_at_damage > 0 and total_damage >= stop_at_damage:
            break

    poison_tick_damage = calculate_poison_ticks(poison_events, duration_sec, damage_events, timeline)
    burn_tick_damage = calculate_burn_ticks(burn_events, duration_sec, damage_events, timeline)
    total_damage += poison_tick_damage + burn_tick_damage
    return CombatSummary(
        duration_sec=duration_sec,
        total_uses=sum(uses.values()),
        by_card=uses,
        total_damage=total_damage,
        total_burn_applied=total_burn_applied,
        total_poison_applied=total_poison_applied,
        total_burn_tick_damage=burn_tick_damage,
        total_poison_tick_damage=poison_tick_damage,
        total_shield=total_shield,
        by_card_damage=damage_by_card,
        by_card_burn=burn_by_card,
        by_card_poison=poison_by_card,
        by_card_shield=shield_by_card,
        cumulative_damage_by_second=build_damage_curve(damage_events, duration_sec),
        debug_timeline=sorted(timeline, key=lambda item: item["time"]),
    )


def action_performed_key(action_type: str) -> str:
    return {
        "TActionCardHaste": "haste",
        "TActionCardSlow": "slow",
        "TActionCardFreeze": "freeze",
        "TActionCardReload": "reload",
        "TActionPlayerDamage": "damage",
        "TActionPlayerBurnApply": "burn",
        "TActionPlayerPoisonApply": "poison",
        "TActionPlayerShieldApply": "shield",
        "TActionCardDestroy": "destruction",
        "TActionCardTransformDestroyed": "destruction",
    }.get(action_type, "")


def normalize_offense_attr(attribute_type: str) -> str:
    if attribute_type == "DamageAmount":
        return "DamageAmount"
    if attribute_type in {"BurnAmount", "BurnApplyAmount"}:
        return "BurnApplyAmount"
    if attribute_type in {"PoisonAmount", "PoisonApplyAmount"}:
        return "PoisonApplyAmount"
    if attribute_type == "ShieldApplyAmount":
        return "ShieldApplyAmount"
    return ""


def calculate_poison_ticks(
    events: list[dict[str, Any]],
    duration_sec: float,
    damage_events: list[dict[str, float]],
    timeline: list[dict[str, Any]],
) -> float:
    ordered = sorted(events, key=lambda item: item["time"])
    index = 0
    stack = 0.0
    total = 0.0
    for tick in range(1, max(1, int(math.floor(duration_sec))) + 1):
        while index < len(ordered) and ordered[index]["time"] <= tick + 1e-6:
            stack += float(ordered[index]["amount"])
            index += 1
        if stack > 0:
            total += stack
            damage_events.append({"time": float(tick), "amount": stack})
            timeline.append({"time": float(tick), "kind": "poison-tick", "source": "poison", "value": stack})
    return total


def calculate_burn_ticks(
    events: list[dict[str, Any]],
    duration_sec: float,
    damage_events: list[dict[str, float]],
    timeline: list[dict[str, Any]],
) -> float:
    ordered = sorted(events, key=lambda item: item["time"])
    index = 0
    stack = 0.0
    total = 0.0
    ticks = max(1, int(math.floor(duration_sec / 0.5)))
    for i in range(1, ticks + 1):
        tick = i * 0.5
        while index < len(ordered) and ordered[index]["time"] <= tick + 1e-6:
            stack += float(ordered[index]["amount"])
            index += 1
        if stack > 0:
            total += stack
            damage_events.append({"time": tick, "amount": stack})
            timeline.append({"time": tick, "kind": "burn-tick", "source": "burn", "value": stack})
            stack = max(0.0, math.ceil(stack - max(1, math.ceil(stack * 0.03))))
    return total


def aggregate_summaries(summaries: list[CombatSummary], duration_sec: float) -> CombatSummary:
    count = max(1, len(summaries))

    def avg_map(attr: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for summary in summaries:
            for key, value in getattr(summary, attr).items():
                out[key] = out.get(key, 0.0) + float(value)
        return {key: value / count for key, value in out.items()}

    damages = [summary.total_damage for summary in summaries]
    max_curve = max((len(summary.cumulative_damage_by_second) for summary in summaries), default=0)
    curve = [
        sum((summary.cumulative_damage_by_second[i] if i < len(summary.cumulative_damage_by_second) else 0.0) for summary in summaries) / count
        for i in range(max_curve)
    ]
    return CombatSummary(
        duration_sec=duration_sec,
        total_uses=sum(summary.total_uses for summary in summaries) / count,
        by_card=avg_map("by_card"),
        total_damage=sum(damages) / count if damages else 0.0,
        total_burn_applied=sum(summary.total_burn_applied for summary in summaries) / count,
        total_poison_applied=sum(summary.total_poison_applied for summary in summaries) / count,
        total_burn_tick_damage=sum(summary.total_burn_tick_damage for summary in summaries) / count,
        total_poison_tick_damage=sum(summary.total_poison_tick_damage for summary in summaries) / count,
        total_shield=sum(summary.total_shield for summary in summaries) / count,
        by_card_damage=avg_map("by_card_damage"),
        by_card_burn=avg_map("by_card_burn"),
        by_card_poison=avg_map("by_card_poison"),
        by_card_shield=avg_map("by_card_shield"),
        cumulative_damage_by_second=curve,
        debug_timeline=[],
        random_trials=count,
        total_damage_min=min(damages) if damages else 0.0,
        total_damage_max=max(damages) if damages else 0.0,
        total_damage_avg=sum(damages) / count if damages else 0.0,
    )


def card_label(card: PlacedCard) -> str:
    return str(card.card.get("name") or card.card.get("internal_name") or card.card.get("id") or card.placement_id)


def detect_charge_cycles(cards_input: list[PlacedCard | dict[str, Any]]) -> list[list[str]]:
    cards = coerce_placed_cards(cards_input)
    edges: dict[str, set[str]] = {card.placement_id: set() for card in cards}
    for source in cards:
        for rule in read_rules(source, {"TActionCardCharge"}):
            for target in resolve_targets(cards, source, source, rule):
                edges[source.placement_id].add(target.placement_id)

    cycles: list[list[str]] = []
    stack: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> None:
        visiting.add(node)
        stack.append(node)
        for nxt in edges.get(node, set()):
            if nxt in visiting:
                idx = stack.index(nxt)
                cycle = stack[idx:] + [nxt]
                if cycle not in cycles:
                    cycles.append(cycle)
            elif nxt not in visited:
                dfs(nxt)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        if node not in visited:
            dfs(node)
    return cycles


def compare_layouts(
    layouts: list[list[PlacedCard | dict[str, Any]]],
    duration_sec: float = 20.0,
    *,
    random_trials: int = 1,
) -> list[dict[str, Any]]:
    scored = []
    for index, layout in enumerate(layouts):
        summary = simulate_combat(layout, duration_sec, random_trials=random_trials)
        scored.append({"layout_index": index, "score": summary.total_damage, "combat": summary})
    return sorted(scored, key=lambda item: item["score"], reverse=True)


def estimate_self_health_ttk(
    data: dict[str, Any],
    state: Any,
    *,
    horizon_sec: float = 60.0,
    random_trials: int = 1,
) -> SelfTtkEstimate | None:
    target_health = state_value(state, "combat_health", None)
    if target_health is None:
        target_health = state_value(state, "health", None)
    try:
        target = float(target_health)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None

    placed, skipped = build_current_board_placements(data, state)
    if not placed:
        return SelfTtkEstimate(
            target_health=target,
            horizon_sec=horizon_sec,
            kill_time_sec=None,
            direct_kill_time_sec=None,
            total_damage=0.0,
            direct_damage=0.0,
            total_burn_tick_damage=0.0,
            total_poison_tick_damage=0.0,
            simulated_card_count=0,
            skipped_cards=skipped,
            by_card_uses={},
            by_card_damage={},
            cumulative_damage_by_second=[],
            timeline=[],
        )

    summary = simulate_combat(
        placed,
        duration_sec=horizon_sec,
        random_trials=random_trials,
        stop_at_damage=0.0,
    )
    direct_damage = max(
        0.0,
        summary.total_damage
        - summary.total_burn_tick_damage
        - summary.total_poison_tick_damage,
    )
    timeline = damage_timeline(summary)
    return SelfTtkEstimate(
        target_health=target,
        horizon_sec=horizon_sec,
        kill_time_sec=first_time_to_damage(timeline, target),
        direct_kill_time_sec=first_time_to_damage(timeline, target, kinds={"use"}),
        total_damage=summary.total_damage,
        direct_damage=direct_damage,
        total_burn_tick_damage=summary.total_burn_tick_damage,
        total_poison_tick_damage=summary.total_poison_tick_damage,
        simulated_card_count=len(placed),
        skipped_cards=skipped,
        by_card_uses=summary.by_card,
        by_card_damage=summary.by_card_damage,
        cumulative_damage_by_second=summary.cumulative_damage_by_second,
        timeline=timeline[:80],
    )


def build_current_board_placements(
    data: dict[str, Any],
    state: Any,
) -> tuple[list[PlacedCard], list[dict[str, Any]]]:
    card_index = template_index(data.get("cards", {}))
    entries = current_board_entries(state)
    placed: list[PlacedCard] = []
    skipped: list[dict[str, Any]] = []
    cursor = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        template = find_card_template(entry, card_index)
        if template is None:
            skipped.append(
                {
                    "id": entry.get("id"),
                    "template_id": entry.get("template_id"),
                    "name": entry.get("name"),
                    "reason": "template_not_found",
                }
            )
            continue

        card = apply_instance_snapshot(template, entry)
        width = runtime_width(entry, card)
        start = runtime_start(entry, cursor)
        cursor = max(cursor, start + width)
        placed.append(
            PlacedCard(
                placement_id=str(
                    entry.get("id")
                    or entry.get("instance_id")
                    or entry.get("template_id")
                    or entry.get("name")
                    or index
                ),
                card=card,
                start=start,
                width=width,
                tier=str(entry.get("rarity") or entry.get("tier") or card.get("rarity") or ""),
                shield_enchanted=has_enchantment(entry, "shield"),
            )
        )
    return placed, skipped


def state_value(state: Any, name: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(name, default)
    return getattr(state, name, default)


def current_board_entries(state: Any) -> list[dict[str, Any]]:
    board = state_value(state, "board_items", None)
    if isinstance(board, list) and board:
        return [item for item in board if isinstance(item, dict)]

    owned_items = state_value(state, "owned_items", None)
    if isinstance(owned_items, list):
        entries = [
            item
            for item in owned_items
            if isinstance(item, dict)
            and str(item.get("section", "")).lower() in {"hand", "board"}
        ]
        if entries:
            return entries

    owned_cards = state_value(state, "owned_cards", None)
    if isinstance(owned_cards, list):
        return [
            item
            for item in owned_cards
            if isinstance(item, dict)
            and str(item.get("card_type", "item")).lower() != "skill"
            and str(item.get("section", "")).lower() in {"hand", "board"}
        ]
    return []


def template_index(cards: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for key, card in cards.items():
        if not isinstance(card, dict):
            continue
        for value in (
            key,
            card.get("name"),
            card.get("internal_name"),
            card.get("id"),
            card.get("source_id"),
            card.get("template_id"),
        ):
            if value:
                index[str(value).lower()] = card
    return index


def find_card_template(
    entry: dict[str, Any],
    index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in ("template_id", "source_id", "id", "name", "internal_name"):
        value = entry.get(key)
        if value and str(value).lower() in index:
            return index[str(value).lower()]
    return None


def apply_instance_snapshot(template: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    card = deepcopy(template)
    if entry.get("name"):
        card["name"] = entry["name"]
    tier = normalize_tier(entry.get("rarity") or entry.get("tier") or card.get("rarity"))
    attrs = entry.get("current_attributes")
    if isinstance(attrs, dict) and attrs:
        raw = card.setdefault("raw_effects", {})
        tiers_raw = raw.setdefault("tiers_raw", {})
        tier_raw = tiers_raw.setdefault(tier, {})
        tier_attrs = tier_raw.setdefault("Attributes", {})
        for key, value in attrs.items():
            tier_attrs[str(key)] = value
    runtime_values = entry.get("runtime_values")
    if isinstance(runtime_values, dict):
        size = runtime_values.get("Size") or runtime_values.get("size")
        if size:
            card["size"] = str(size)
    return card


def runtime_width(entry: dict[str, Any], card: dict[str, Any]) -> int:
    runtime_values = entry.get("runtime_values")
    if isinstance(runtime_values, dict):
        size = runtime_values.get("Size") or runtime_values.get("size")
        if size:
            return size_to_width(size)
    return card_width(card)


def size_to_width(size: Any) -> int:
    normalized = normalize_size(size)
    if normalized == "large":
        return 3
    if normalized == "medium":
        return 2
    return 1


def runtime_start(entry: dict[str, Any], fallback: int) -> int:
    for key in ("position", "Position", "slot", "Slot", "index", "Index", "board_index", "BoardIndex"):
        value = entry.get(key)
        try:
            if value is not None:
                return max(0, int(value))
        except (TypeError, ValueError):
            pass
    runtime_values = entry.get("runtime_values")
    if isinstance(runtime_values, dict):
        for key in ("Position", "Slot", "Index", "BoardIndex", "position", "slot", "index", "board_index"):
            try:
                if runtime_values.get(key) is not None:
                    return max(0, int(runtime_values[key]))
            except (TypeError, ValueError):
                pass
    context = str(entry.get("ui_context") or "")
    match = re.search(r"PlayerItemSocket_(\d+)", context)
    if match:
        try:
            return max(0, int(match.group(1)))
        except ValueError:
            pass
    return fallback


def has_enchantment(entry: dict[str, Any], name: str) -> bool:
    needle = str(name).lower()
    values = entry.get("enchantments") or []
    if entry.get("enchantment"):
        values = [entry["enchantment"], *values]
    return any(needle in str(value).lower() for value in values)


def damage_timeline(summary: CombatSummary) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in sorted(summary.debug_timeline, key=lambda item: float(item.get("time", 0) or 0)):
        kind = str(event.get("kind") or "")
        if kind not in {"use", "burn-tick", "poison-tick"}:
            continue
        try:
            amount = float(event.get("value") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            continue
        out.append(
            {
                "time": float(event.get("time") or 0),
                "kind": kind,
                "source": event.get("source"),
                "amount": amount,
            }
        )
    return out


def first_time_to_damage(
    timeline: list[dict[str, Any]],
    target: float,
    *,
    kinds: set[str] | None = None,
) -> float | None:
    total = 0.0
    for event in timeline:
        if kinds is not None and str(event.get("kind")) not in kinds:
            continue
        total += float(event.get("amount") or 0)
        if total >= target:
            return float(event.get("time") or 0)
    return None
