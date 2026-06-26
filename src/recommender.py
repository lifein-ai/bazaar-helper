from __future__ import annotations

from typing import Any


RARITY_ORDER = {
    "bronze": 1,
    "silver": 2,
    "gold": 3,
    "diamond": 4,
    "legendary": 5,
}
RARITY_BY_ORDER = {order: rarity for rarity, order in RARITY_ORDER.items()}

ROLE_LABELS = {
    "core": "核心",
    "transition": "过渡",
    "optional": "可选",
    "unrelated": "无关",
}

RECOMMENDATION_RANK = {
    "High Value": 1,
    "Medium Value": 2,
    "Low Value": 3,
}

SHOP_CARD_COUNT = 6

ENCHANTMENT_TAGS = {
    "fiery": ["burn"],
    "flame": ["burn"],
    "burn": ["burn"],
    "toxic": ["poison"],
    "poison": ["poison"],
    "icy": ["freeze"],
    "freeze": ["freeze"],
    "shielded": ["shield"],
    "shield": ["shield"],
    "restorative": ["heal"],
    "heal": ["heal"],
    "turbo": ["haste"],
    "haste": ["haste"],
    "deadly": ["crit"],
    "crit": ["crit"],
    "shiny": ["value"],
    "golden": ["gold"],
    "heavy": ["damage"],
    "obsidian": ["damage"],
}


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().lower()


def normalize_text_list(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for value in values or []:
        normalized = normalize_text(value)
        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


def tags_from_enchantments(enchantments: list[str] | None) -> list[str]:
    tags: list[str] = []
    for enchantment in normalize_text_list(enchantments):
        tags.extend(ENCHANTMENT_TAGS.get(enchantment, []))
    return normalize_text_list(tags)


def tag_family(tag: str) -> str:
    normalized = normalize_text(tag)
    if normalized.endswith("reference"):
        return normalized.removesuffix("reference")
    return normalized


def tags_overlap_build_wants(card_tags: list[str], build_data: dict[str, Any]) -> bool:
    card_tag_families = {tag_family(tag) for tag in normalize_text_list(card_tags)}
    wanted_tag_families = {
        tag_family(tag)
        for tag in normalize_text_list(build_data.get("wanted_tags", []))
    }
    return bool(card_tag_families & wanted_tag_families)


def effective_card_tags(
    card_data: dict[str, Any],
    enchantments: list[str] | None = None,
) -> list[str]:
    return normalize_text_list(
        card_data.get("tags", []) + tags_from_enchantments(enchantments)
    )


def rarity_range_intersects(
    card_min: str,
    card_max: str,
    event_min: str,
    event_max: str,
) -> bool:
    card_min = normalize_text(card_min)
    card_max = normalize_text(card_max)
    event_min = normalize_text(event_min)
    event_max = normalize_text(event_max)

    for label, rarity in {
        "card min rarity": card_min,
        "card max rarity": card_max,
        "event min rarity": event_min,
        "event max rarity": event_max,
    }.items():
        if rarity not in RARITY_ORDER:
            raise ValueError(f"Unknown {label}: {rarity}")

    return (
        RARITY_ORDER[card_min] <= RARITY_ORDER[event_max]
        and RARITY_ORDER[event_min] <= RARITY_ORDER[card_max]
    )


def tags_match(card_tags: list[str], reward_tags: list[str], match_mode: str) -> bool:
    reward_tags = normalize_text_list(reward_tags)
    if not reward_tags:
        return True

    card_tag_set = set(normalize_text_list(card_tags))
    reward_tag_set = set(reward_tags)

    if match_mode == "any":
        return bool(card_tag_set & reward_tag_set)

    if match_mode == "all":
        return reward_tag_set.issubset(card_tag_set)

    raise ValueError(f"Unknown match_mode: {match_mode}")


def resolve_event_rarity_filter(
    pool_rule: dict[str, Any],
    current_day: int,
    rarity_rules: dict[str, Any],
) -> dict[str, str] | None:
    fixed_filter = pool_rule.get("rarity_filter")
    if fixed_filter:
        return {
            "min": normalize_text(fixed_filter["min"]),
            "max": normalize_text(fixed_filter["max"]),
        }

    rule_name = pool_rule.get("rarity_rule")
    if not rule_name:
        return None

    if rule_name not in rarity_rules:
        raise ValueError(f"Rarity rule not found: {rule_name}")

    for item in rarity_rules[rule_name]:
        from_day = item["from_day"]
        to_day = item["to_day"]

        if current_day >= from_day and (to_day is None or current_day <= to_day):
            return {
                "min": normalize_text(item["min"]),
                "max": normalize_text(item["max"]),
            }

    raise ValueError(f"Rarity rule {rule_name} does not cover Day {current_day}")


def get_event_card_pool_rule(event_data: dict[str, Any]) -> dict[str, Any] | None:
    event_category = event_data.get("event_category")

    if event_category in {"shops", "skill_shops"}:
        return event_data.get("shop_pool")

    if event_category == "resource_events":
        card_reward = event_data.get("card_reward", {})
        return card_reward if card_reward.get("enabled") else None

    if event_category == "item_rewards":
        card_reward = event_data.get("card_reward")
        if card_reward:
            return card_reward

    return None


def infer_possible_cards_for_event(
    event_data: dict[str, Any],
    cards: dict[str, Any],
    current_day: int,
    rarity_rules: dict[str, Any],
    current_hero: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
    pool_rule = get_event_card_pool_rule(event_data)
    if pool_rule is None:
        return [], None

    reward_tags = normalize_text_list(pool_rule.get("reward_tags", []))
    exact_names = set(pool_rule.get("exact_names", []))
    match_mode = pool_rule.get("match_mode", "any")
    excluded_tags = normalize_text_list(pool_rule.get("excluded_tags", []))
    size_filter = normalize_text_list(pool_rule.get("size_filter", []))
    hero_filter = normalize_text(
        pool_rule.get("hero_filter") or event_data.get("hero_filter")
    )
    hero_scope = normalize_text(pool_rule.get("hero_scope") or "current")
    current_hero = normalize_text(current_hero)

    rarity_filter = resolve_event_rarity_filter(pool_rule, current_day, rarity_rules)
    if rarity_filter is None:
        rarity_filter = {"min": "bronze", "max": "diamond"}

    possible_cards: list[dict[str, Any]] = []

    for card_name, card_data in cards.items():
        card_tags = normalize_text_list(card_data.get("tags", []))
        card_min = normalize_text(card_data.get("min_rarity"))
        card_max = normalize_text(card_data.get("max_rarity"))

        if not card_min or not card_max:
            continue

        card_hero = normalize_text(card_data.get("hero"))
        card_heroes = {normalize_text(hero) for hero in card_data.get("heroes", [])}

        if hero_filter and hero_filter not in ({card_hero} | card_heroes):
            continue

        if not hero_filter and hero_scope != "any" and current_hero:
            if current_hero not in ({card_hero} | card_heroes) and "common" not in card_heroes:
                continue

        if size_filter and normalize_text(card_data.get("size")) not in size_filter:
            continue

        if any(tag in card_tags for tag in excluded_tags):
            continue

        if exact_names:
            if card_name not in exact_names:
                continue
        elif not tags_match(card_tags, reward_tags, match_mode):
            continue

        if not rarity_range_intersects(
            card_min,
            card_max,
            rarity_filter["min"],
            rarity_filter["max"],
        ):
            continue

        possible_cards.append(
            {
                "name": card_name,
                "tier": card_data.get("tier", "Unknown"),
                "tags": card_tags,
                "min_rarity": card_min,
                "max_rarity": card_max,
                "raw": card_data,
            }
        )

    return possible_cards, rarity_filter


def get_card_role_for_build(
    card_name: str,
    card_data: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
) -> str:
    build_roles = card_data.get("build_roles", {})
    if build_name in build_roles:
        role = build_roles[build_name]
        return "unrelated" if role == "trap" else role

    if card_name in build_data.get("core_cards", []):
        return "core"
    if card_name in build_data.get("transition_cards", []):
        return "transition"
    if card_name in build_data.get("optional_cards", []):
        return "optional"

    return "unrelated"


def probability_at_least_one(hit_ratio: float, draws: int = SHOP_CARD_COUNT) -> float:
    if hit_ratio <= 0:
        return 0.0
    if hit_ratio >= 1:
        return 1.0
    return 1 - (1 - hit_ratio) ** draws


def rarity_names_in_range(min_rarity: str, max_rarity: str) -> list[str]:
    min_order = RARITY_ORDER[normalize_text(min_rarity)]
    max_order = RARITY_ORDER[normalize_text(max_rarity)]
    return [
        RARITY_BY_ORDER[order]
        for order in range(min_order, max_order + 1)
        if order in RARITY_BY_ORDER
    ]


def expected_card_sell_gold(
    card_data: dict[str, Any],
    rarity_filter: dict[str, str] | None,
) -> float:
    buy_prices = {
        normalize_text(rarity): price
        for rarity, price in (card_data.get("buy_prices") or {}).items()
        if isinstance(price, (int, float))
    }
    if not buy_prices:
        return 0.0

    card_min = normalize_text(card_data.get("min_rarity"))
    card_max = normalize_text(card_data.get("max_rarity"))
    if not card_min or not card_max:
        return 0.0

    event_min = normalize_text(rarity_filter.get("min") if rarity_filter else card_min)
    event_max = normalize_text(rarity_filter.get("max") if rarity_filter else card_max)
    min_order = max(RARITY_ORDER[card_min], RARITY_ORDER[event_min])
    max_order = min(RARITY_ORDER[card_max], RARITY_ORDER[event_max])
    if min_order > max_order:
        return 0.0

    sell_values = [
        buy_prices[rarity] / 2
        for rarity in rarity_names_in_range(
            RARITY_BY_ORDER[min_order],
            RARITY_BY_ORDER[max_order],
        )
        if rarity in buy_prices
    ]
    if not sell_values:
        return 0.0

    return sum(sell_values) / len(sell_values)


def expected_unrelated_sell_gold(
    analyzed_cards: list[dict[str, Any]],
    event_data: dict[str, Any],
    draw_count: int,
) -> float:
    if event_data.get("event_category") != "item_rewards":
        return 0.0
    if not analyzed_cards:
        return 0.0

    total_sell_value = sum(
        card.get("sell_gold", 0.0)
        for card in analyzed_cards
        if card.get("role") == "unrelated"
    )
    return draw_count * total_sell_value / len(analyzed_cards)


def analyze_event(
    event_name: str,
    event_data: dict[str, Any],
    cards: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
    current_day: int,
    rarity_rules: dict[str, Any],
    current_hero: str | None = None,
    owned_cards: dict[str, str] | None = None,
    owned_card_enchantments: dict[str, list[str]] | None = None,
    include_followups: bool = True,
) -> dict[str, Any]:
    owned_cards = owned_cards or {}
    owned_card_enchantments = owned_card_enchantments or {}
    possible_cards, resolved_rarity_filter = infer_possible_cards_for_event(
        event_data,
        cards,
        current_day,
        rarity_rules,
        current_hero,
    )

    analyzed_cards: list[dict[str, Any]] = []
    role_counts = {role: 0 for role in ROLE_LABELS}
    high_tier_count = 0
    upgrade_hits: list[str] = []
    owned_target_hits: list[dict[str, Any]] = []

    for card in possible_cards:
        card_name = card["name"]
        card_data = card["raw"]
        tier = card.get("tier", "Unknown")
        role = get_card_role_for_build(card_name, card_data, build_name, build_data)
        if (
            role == "unrelated"
            and event_data.get("event_category") == "item_rewards"
            and event_data.get("card_reward", {}).get("exact_names")
            and tags_overlap_build_wants(card.get("tags", []), build_data)
        ):
            role = "optional"
        role_counts[role] = role_counts.get(role, 0) + 1

        if tier in {"S", "A"}:
            high_tier_count += 1

        owned_rarity = owned_cards.get(card_name)
        can_upgrade = bool(
            owned_rarity
            and normalize_text(owned_rarity) != normalize_text(card_data.get("max_rarity"))
        )

        if can_upgrade:
            upgrade_hits.append(card_name)

        analyzed_cards.append(
            {
                "name": card_name,
                "tier": tier,
                "role": role,
                "role_label": ROLE_LABELS.get(role, role.title()),
                "can_upgrade": can_upgrade,
                "owned_rarity": owned_rarity,
                "tags": card.get("tags", []),
                "sell_gold": expected_card_sell_gold(card_data, resolved_rarity_filter),
            }
        )

    if event_data.get("event_category") in {"item_events", "enchant_events"}:
        owned_target_hits = analyze_owned_target_hits(
            event_data=event_data,
            cards=cards,
            build_name=build_name,
            build_data=build_data,
            owned_cards=owned_cards,
            owned_card_enchantments=owned_card_enchantments,
        )

    resource_rewards = event_data.get("resource_rewards", {})
    has_resource_reward = any(value > 0 for value in resource_rewards.values())

    total_pool_count = len(analyzed_cards)
    valuable_count = (
        role_counts.get("core", 0)
        + role_counts.get("transition", 0)
        + role_counts.get("optional", 0)
    )
    core_count = role_counts.get("core", 0)

    valuable_ratio = valuable_count / total_pool_count if total_pool_count else 0.0
    core_ratio = core_count / total_pool_count if total_pool_count else 0.0
    high_tier_ratio = high_tier_count / total_pool_count if total_pool_count else 0.0

    draw_count = 1 if event_data.get("event_category") == "item_rewards" else SHOP_CARD_COUNT
    expected_sell_gold = expected_unrelated_sell_gold(
        analyzed_cards,
        event_data,
        draw_count,
    )

    pool_stats = {
        "draw_count": draw_count,
        "total_pool_count": total_pool_count,
        "valuable_count": valuable_count,
        "valuable_ratio": valuable_ratio,
        "core_ratio": core_ratio,
        "high_tier_ratio": high_tier_ratio,
        "expected_valuable_in_shop": draw_count * valuable_ratio,
        "expected_core_in_shop": draw_count * core_ratio,
        "expected_high_tier_in_shop": draw_count * high_tier_ratio,
        "prob_valuable_in_shop": probability_at_least_one(valuable_ratio, draw_count),
        "prob_core_in_shop": probability_at_least_one(core_ratio, draw_count),
        "prob_high_tier_in_shop": probability_at_least_one(high_tier_ratio, draw_count),
        "expected_sell_gold": expected_sell_gold,
    }

    recommendation, reasons = decide_recommendation(
        analyzed_cards,
        role_counts,
        high_tier_count,
        upgrade_hits,
        has_resource_reward,
        resource_rewards,
        pool_stats,
        owned_target_hits,
        event_data,
    )
    followup_results: list[dict[str, Any]] = []
    if include_followups:
        for option in event_data.get("followup_options", []):
            followup_results.append(
                analyze_event(
                    event_name=option.get("name", "Follow-up option"),
                    event_data=option,
                    cards=cards,
                    build_name=build_name,
                    build_data=build_data,
                    current_day=current_day,
                    rarity_rules=rarity_rules,
                    current_hero=current_hero,
                    owned_cards=owned_cards,
                    owned_card_enchantments=owned_card_enchantments,
                    include_followups=False,
                )
            )
        recommendation, reasons = apply_followup_value(
            recommendation,
            reasons,
            followup_results,
        )

    return {
        "event_name": event_name,
        "event_type": event_data.get("event_category", "unknown"),
        "notes": event_data.get("notes", ""),
        "current_day": current_day,
        "resolved_rarity_filter": resolved_rarity_filter,
        "possible_cards": analyzed_cards,
        "role_counts": role_counts,
        "high_tier_count": high_tier_count,
        "upgrade_hits": upgrade_hits,
        "owned_target_hits": owned_target_hits,
        "resource_rewards": resource_rewards,
        "followup_options": summarize_followup_results(followup_results),
        "pool_stats": pool_stats,
        "recommendation": recommendation,
        "reasons": reasons,
    }


def apply_followup_value(
    recommendation: str,
    reasons: list[str],
    followup_results: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    if not followup_results:
        return recommendation, reasons

    ranked = sorted(
        followup_results,
        key=lambda result: (
            RECOMMENDATION_RANK.get(result.get("recommendation"), 99),
            result.get("event_name", ""),
        ),
    )
    best = ranked[0]
    best_recommendation = best.get("recommendation", "Low Value")
    useful = [
        result
        for result in ranked
        if result.get("recommendation") in {"High Value", "Medium Value"}
    ]
    useful_names = ", ".join(result.get("event_name", "") for result in useful[:4])
    if useful_names:
        reasons.append(f"后续选项里有可用收益：{useful_names}。")
    else:
        reasons.append("检测到后续选项，但目前看收益有限。")

    current_rank = RECOMMENDATION_RANK.get(recommendation, 99)
    best_rank = RECOMMENDATION_RANK.get(best_recommendation, 99)
    if best_rank >= current_rank:
        return recommendation, reasons

    if recommendation == "Low Value" and best_recommendation == "High Value":
        return "Medium Value", reasons
    return best_recommendation, reasons


def summarize_followup_results(followup_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for result in followup_results:
        pool_stats = result.get("pool_stats", {})
        summaries.append(
            {
                "name": result.get("event_name"),
                "recommendation": result.get("recommendation"),
                "event_type": result.get("event_type"),
                "notes": result.get("notes", ""),
                "resource_rewards": result.get("resource_rewards", {}),
                "valuable_count": int(pool_stats.get("valuable_count", 0)),
                "total_pool_count": int(pool_stats.get("total_pool_count", 0)),
                "expected_sell_gold": float(pool_stats.get("expected_sell_gold", 0.0)),
                "priority_cards": [
                    {
                        "name": card.get("name"),
                        "tier": card.get("tier"),
                        "role": card.get("role"),
                    }
                    for card in result.get("possible_cards", [])
                    if card.get("role") in {"core", "transition", "optional"}
                ][:5],
            }
        )
    return summaries


def analyze_owned_target_hits(
    event_data: dict[str, Any],
    cards: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
    owned_cards: dict[str, str],
    owned_card_enchantments: dict[str, list[str]],
) -> list[dict[str, Any]]:
    target_tags = event_data.get("target_tags", [])
    if not target_tags and event_data.get("event_category") == "enchant_events":
        target_tags = event_data.get("enchantment_tags", [])

    target_tags = normalize_text_list(target_tags)
    effect = event_data.get("effect")
    matches_any_owned_item = effect in {
        "upgrade_items",
        "transform_items",
        "enhance_offensive_items",
    }
    if effect == "enhance_offensive_items" and not target_tags:
        target_tags = ["weapon", "damage"]

    if not target_tags and not matches_any_owned_item:
        return []

    hits: list[dict[str, Any]] = []
    for card_name, rarity in owned_cards.items():
        card_data = cards.get(card_name)
        if not card_data:
            continue

        enchantments = owned_card_enchantments.get(card_name, [])
        card_tags = effective_card_tags(card_data, enchantments)
        if target_tags and not tags_match(card_tags, target_tags, event_data.get("match_mode", "any")):
            continue

        role = get_card_role_for_build(card_name, card_data, build_name, build_data)
        can_upgrade = normalize_text(rarity) != normalize_text(card_data.get("max_rarity"))
        hits.append(
            {
                "name": card_name,
                "rarity": rarity,
                "tier": card_data.get("tier", "Unknown"),
                "role": role,
                "role_label": ROLE_LABELS.get(role, role.title()),
                "can_upgrade": can_upgrade,
                "tags": card_tags,
                "enchantments": enchantments,
            }
        )

    return hits


def decide_recommendation(
    analyzed_cards: list[dict[str, Any]],
    role_counts: dict[str, int],
    high_tier_count: int,
    upgrade_hits: list[str],
    has_resource_reward: bool,
    resource_rewards: dict[str, int],
    pool_stats: dict[str, float],
    owned_target_hits: list[dict[str, Any]],
    event_data: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    total_pool_count = int(pool_stats.get("total_pool_count", 0))
    valuable_count = int(pool_stats.get("valuable_count", 0))
    valuable_ratio = pool_stats.get("valuable_ratio", 0.0)
    expected_valuable = pool_stats.get("expected_valuable_in_shop", 0.0)
    expected_core = pool_stats.get("expected_core_in_shop", 0.0)
    expected_sell_gold = pool_stats.get("expected_sell_gold", 0.0)
    prob_valuable = pool_stats.get("prob_valuable_in_shop", 0.0)
    prob_core = pool_stats.get("prob_core_in_shop", 0.0)
    draw_count = int(pool_stats.get("draw_count", SHOP_CARD_COUNT))

    core_count = role_counts.get("core", 0)
    transition_count = role_counts.get("transition", 0)
    owned_valuable_hits = [
        card
        for card in owned_target_hits
        if card.get("role") in {"core", "transition", "optional"}
    ]
    upgradeable_valuable_hits = [
        card
        for card in owned_valuable_hits
        if card.get("can_upgrade")
    ]

    high_tier_core_cards = [
        card
        for card in analyzed_cards
        if card["role"] == "core" and card["tier"] in {"S", "A"}
    ]

    if high_tier_core_cards:
        names = ", ".join(card["name"] for card in high_tier_core_cards)
        reasons.append(f"可能命中高评级核心卡：{names}。")

    if core_count >= 2:
        reasons.append(f"候选池里有 {core_count} 张可能适配当前构筑的核心卡。")

    if upgrade_hits:
        reasons.append(f"可能升级已拥有的卡：{', '.join(upgrade_hits)}。")

    if owned_target_hits:
        names = ", ".join(card["name"] for card in owned_target_hits[:5])
        reasons.append(f"能作用到已拥有的匹配物品：{names}。")

    if transition_count > 0:
        reasons.append(f"包含 {transition_count} 张过渡卡，可以帮助前中期稳定。")

    if high_tier_count > 0:
        reasons.append(f"候选池里有 {high_tier_count} 张 S/A 评级卡。")

    if has_resource_reward:
        reasons.append(f"额外提供资源：{format_resource_rewards(resource_rewards)}。")

    if expected_sell_gold > 0:
        reasons.append(
            f"无用物品也可以卖出，预期约 {expected_sell_gold:.1f} 金币。"
        )

    if event_data.get("event_category") == "enchant_events":
        enchantment_tags = event_data.get("enchantment_tags", [])
        if enchantment_tags:
            reasons.append(f"可以提供附魔方向：{', '.join(enchantment_tags)}。")
        else:
            reasons.append("可以给物品附魔，但具体附魔价值暂不明确。")

    if total_pool_count > 0:
        reasons.append(
            f"候选池共有 {total_pool_count} 张卡，其中 {valuable_count} 张与当前构筑相关"
            f"（{valuable_ratio:.0%}）。"
        )
        if draw_count == SHOP_CARD_COUNT:
            reasons.append(
                f"商店展示 {SHOP_CARD_COUNT} 张卡，预期命中 {expected_valuable:.1f} 张构筑相关卡；"
                f"至少看到一张的概率为 {prob_valuable:.0%}。"
            )
        else:
            reasons.append(
                f"该奖励给 {draw_count} 张物品，预期命中 {expected_valuable:.1f} 张构筑相关卡；"
                f"有用概率为 {prob_valuable:.0%}。"
            )
        reasons.append(f"至少命中一张核心卡的概率为 {prob_core:.0%}。")

    if (
        not analyzed_cards
        and not has_resource_reward
        and not owned_target_hits
        and event_data.get("event_category") != "enchant_events"
    ):
        reasons.append("暂未识别到明确的卡牌或资源收益。")

    if event_data.get("effect") == "upgrade_items" and upgradeable_valuable_hits:
        return "High Value", reasons
    if event_data.get("effect") == "upgrade_items" and owned_target_hits:
        return "Medium Value", reasons
    if owned_valuable_hits and event_data.get("effect") != "transform_items":
        return "High Value", reasons
    if owned_target_hits:
        return "Medium Value", reasons
    if event_data.get("event_category") in {"skill_shops", "enchant_events"}:
        return "Medium Value", reasons
    if high_tier_core_cards and expected_core >= 0.4:
        return "High Value", reasons
    if core_count >= 2 and expected_core >= 0.3:
        return "High Value", reasons
    if upgrade_hits and expected_valuable >= 0.3:
        return "High Value", reasons
    if expected_valuable >= 0.6:
        return "High Value", reasons
    if expected_valuable >= 0.25:
        return "Medium Value", reasons
    if has_resource_reward:
        return "Medium Value", reasons
    if expected_sell_gold >= 1:
        return "Medium Value", reasons

    if analyzed_cards:
        reasons.append("存在少量可用卡，但命中率偏低。")

    return "Low Value", reasons


def format_resource_rewards(resource_rewards: dict[str, int]) -> str:
    labels = {
        "exp": "经验",
        "gold": "金币",
        "health": "生命",
        "income": "收入",
        "regen": "恢复",
        "speed": "加速",
        "toughness": "韧性",
    }
    parts = [
        f"{labels.get(name, name)} +{value}"
        for name, value in sorted(resource_rewards.items())
        if value > 0
    ]

    return ", ".join(parts) if parts else "无"


def format_rarity_filter(rarity_filter: dict[str, str] | None) -> str:
    if rarity_filter is None:
        return "无卡牌稀有度限制"
    return f"{rarity_filter['min']} - {rarity_filter['max']}"


def print_event_analysis(result: dict[str, Any]) -> None:
    print("=" * 72)
    print(f"Event: {result['event_name']}")
    print(f"Recommendation: {result['recommendation']}")
    print(f"Day: {result['current_day']}")
    print(f"Resolved rarity range: {format_rarity_filter(result['resolved_rarity_filter'])}")

    if result["notes"]:
        print(f"Notes: {result['notes']}")

    print("\nReasons:")
    for reason in result["reasons"]:
        print(f"- {reason}")

    pool_stats = result.get("pool_stats", {})
    if pool_stats:
        print("\nPool stats:")
        print(f"- Candidate cards: {int(pool_stats['total_pool_count'])}")
        print(
            f"- Build-relevant cards: {int(pool_stats['valuable_count'])} "
            f"({pool_stats['valuable_ratio']:.0%})"
        )
        print(
            f"- Expected build-relevant cards in shop: "
            f"{pool_stats['expected_valuable_in_shop']:.1f}"
        )
        print(f"- Probability of at least one useful card: {pool_stats['prob_valuable_in_shop']:.0%}")
        print(f"- Probability of at least one core card: {pool_stats['prob_core_in_shop']:.0%}")

    print("\nPriority cards:")
    priority_cards = [
        card
        for card in result["possible_cards"]
        if card["role"] in {"core", "transition"}
    ]

    if not priority_cards:
        print("- No core or transition cards in this pool.")
    else:
        for card in priority_cards:
            upgrade_text = ""
            if card["can_upgrade"]:
                upgrade_text = f" | owned {card['owned_rarity']}, upgrade possible"

            print(f"- {card['name']} | {card['tier']} | {card['role_label']}{upgrade_text}")

    owned_target_hits = result.get("owned_target_hits", [])
    if owned_target_hits:
        print("\nAffected owned cards:")
        for card in owned_target_hits:
            enchantment_text = ""
            if card.get("enchantments"):
                enchantment_text = f" | enchantments: {', '.join(card['enchantments'])}"
            upgrade_text = " | upgradeable" if card.get("can_upgrade") else ""
            print(
                f"- {card['name']} | {card['tier']} | {card['role_label']}"
                f" | owned {card['rarity']}{upgrade_text}{enchantment_text}"
            )

    print(f"\nResources: {format_resource_rewards(result['resource_rewards'])}")
