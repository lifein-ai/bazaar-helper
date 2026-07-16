from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from build_strategy import format_build_timing_summary, get_game_stage_for_day
from app_paths import get_runtime_dir


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_API_KEY_FILE = get_runtime_dir() / "deepseek_api_key.txt"
AI_DEBUG_ENV_VAR = "BAZAARHELP_AI_DEBUG"
LAST_AI_PROMPT_DEBUG: dict[str, Any] = {}
LAST_AI_USAGE: dict[str, Any] = {}


STAGE_LABELS_ZH = {
    "early": "前期",
    "mid": "中期",
    "late": "后期",
}
RECOMMENDATION_LABELS_ZH = {
    "High Value": "优先选择",
    "Medium Value": "可以考虑",
    "Low Value": "优先级低",
}
ROLE_LABELS_ZH = {
    "core": "核心",
    "transition": "过渡",
    "optional": "可选",
    "unrelated": "无关",
}
AI_SYSTEM_PROMPT = (
    "你是《The Bazaar》的局势分析助手，始终使用简体中文。\n"
    "术语统一：Build 称为阵容，core 称为核心，optional 称为可选，tier 称为评级。\n"
    "你的职责是结合当前局势比较候选方向，解释主要收益、阵容适配和机会成本，为玩家提供有依据的参考倾向。不要替玩家做决定，不要使用绝对命令。\n"
    "只能使用输入中的实时状态、规则层结果、官方数据和攻略上下文。禁止编造未提供的卡牌、技能、价格、概率、阵容、机制或局内信息。\n"
    "事件存在后续选项时，必须把后续选项中的资源、技能、卡池和战斗收益纳入整体判断，不要因为父事件本身没有直接收益就低估它。\n"
    "存在攻略上下文时，必须将攻略作为重要策略依据，并判断其与当前状态是契合、部分契合还是暂不适用。攻略不能覆盖实时状态和规则层事实。\n"
    "严格区分商店阶段。进店前只判断商店是否值得占用当前三选一机会；结合商店规则摘要、事件收益、免费收益、金币和攻略；不讨论当前可见商品、具体购买目标、实时价格或刷新结果。"
    "进店后根据当前可见商品和商店内分析判断购买与刷新；当前已有强目标时，通常不优先建议刷新。刷新只属于当前商店，不会累计到下一家店。\n"
    "不要自行计算卡池概率。直接使用规则层提供的状态、目标数量和摘要，并将规则层结果视为参考倾向，不是绝对命令。\n"
    "回答前比较所有当前选项。重点解释真正影响决策的差异，不逐项复述输入。通常输出 2 到 4 个短段落，约 150 到 300 个中文字。避免表格、代码块和过重 Markdown。"
)


def _role_label(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return ROLE_LABELS_ZH.get(value, value)


def _round_ratio(value: float) -> float:
    return round(float(value), 4)


def _zh_name(data: dict[str, Any], name: Any) -> str:
    if not name:
        return ""
    return data.get("translations", {}).get("by_name", {}).get(str(name), str(name))


def _zh_text(data: dict[str, Any], text: Any) -> str:
    if not text:
        return ""
    result = str(text)
    by_name = data.get("translations", {}).get("by_name", {})
    for source_name in sorted(by_name, key=len, reverse=True):
        translated = by_name.get(source_name)
        if translated:
            result = result.replace(source_name, translated)
    return result


def _zh_id(data: dict[str, Any], source_id: Any) -> str:
    if not source_id:
        return ""
    return str(data.get("translations", {}).get("by_id", {}).get(str(source_id), ""))


def _translate_common_game_text(data: dict[str, Any], text: Any) -> str:
    result = _zh_text(data, text)
    if not result:
        return ""
    replacements = [
        (r"^Gain (\d+) Max Health$", "获得 \1 最大生命值"),
        (r"^Gain (\d+) gold$", "获得 \1 金币"),
        (r"^Gain (\d+) XP$", "获得 \1 经验"),
        (r"^Heal (\d+)$", "治疗 \1"),
        (r"^Deal (\d+) Damage$", "造成 \1 伤害"),
        (r"^Get a ([A-Za-z]+)-tier Loot item$", "获得一件\1级战利品物品"),
        (r"^\(if you have a ([^)]+)\) Choose a Skill$", "（如果你拥有\1）选择一个技能"),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    term_replacements = {
        "Bronze": "青铜",
        "Silver": "白银",
        "Gold": "黄金",
        "Diamond": "钻石",
        "Food": "食物",
        "Friend": "朋友",
        "Toy": "玩具",
        "Drone": "无人机",
        "Loot": "战利品",
        "Skill": "技能",
    }
    for source, translated in term_replacements.items():
        result = result.replace(source, translated)
    return result

SHOP_RULE_STATUS_LABELS_ZH = {
    "strong_candidate": "强候选",
    "candidate": "可考虑",
    "situational": "看局势",
    "weak_candidate": "不优先",
    "not_actionable": "暂不建议",
    "unknown": "信息不足",
}
SHOP_PHASE_LABELS_ZH = {
    "before_entering_shop": "进店前评估",
    "inside_shop": "店内操作",
}
SHOP_ACTION_LABELS_ZH = {
    "buy": "先买目标",
    "refresh": "可以刷新",
    "skip": "不建议刷新",
}
SHOP_DENSITY_LABELS_ZH = {
    "high": "高",
    "medium": "中",
    "low": "低",
    "unknown": "未知",
}
SHOP_GOLD_STATUS_LABELS_ZH = {
    "refresh_supported": "可买且可刷",
    "buy_supported": "大致可买",
    "insufficient": "金币不足",
    "unknown": "未知",
}
SHOP_REASON_LABELS_ZH = {
    "current_gold_unknown": "当前金币未知",
    "price_unknown": "目标价格估算不足",
    "merchant_not_available_on_current_day": "该商人不属于当天可出现商人",
    "pool_contains_current_core_targets": "池子命中当前核心目标",
    "pool_contains_current_tempo_or_optional_targets": "池子命中当前过渡或可选目标",
    "only_future_core_targets_or_stash_value": "主要是未来核心或囤货价值",
    "no_current_build_targets_in_pool": "池子没有当前阵容目标",
    "gold_does_not_support_estimated_purchase": "金币不支持预估购买",
    "theoretical_pool_hit_without_current_actionable_target": "只是理论池子命中，当前不够可执行",
    "visible_target_before_refresh": "当前可见商品已有目标，先买目标",
    "refresh_not_available": "当前商店不可刷新",
    "refresh_cost_unknown": "刷新费用未知",
    "not_enough_gold_after_refresh": "刷新后预算不足",
    "refresh_leaves_insufficient_purchase_budget": "刷新后可能买不起目标",
    "no_visible_target_but_shop_pool_is_actionable": "当前无可见目标，但商人池质量支持刷新",
    "no_visible_target_and_pool_quality_low": "当前无可见目标，且池质量不支持强刷",
}
SHOP_TARGET_COUNT_LABELS_ZH = {
    "missing_current_core": "缺失的当前核心",
    "current_core": "当前核心",
    "current_transition": "当前过渡",
    "current_optional": "当前可选",
    "future_core": "未来核心",
    "expired_core": "过期核心",
    "unrelated": "无关",
}
SHOP_POOL_STAGE_LABELS_ZH = {
    "bronze_only": "青铜阶段",
    "silver_unlocked": "白银阶段",
    "gold_unlocked": "黄金阶段",
    "diamond_unlocked": "钻石阶段",
}
SHOP_CACHE_STATUS_LABELS_ZH = {
    "hit": "已命中缓存",
    "miss": "未命中缓存",
    "unavailable": "暂无缓存",
}


def _label_map(mapping: dict[str, str], value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return mapping.get(value, value)


def _localized_reason(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if text.startswith("target_density_") and text.endswith("_vs_current_day_merchants"):
        density = text.removeprefix("target_density_").removesuffix("_vs_current_day_merchants")
        return f"目标密度相对当天商人为{SHOP_DENSITY_LABELS_ZH.get(density, density)}"
    return SHOP_REASON_LABELS_ZH.get(text, text)


def _localized_reasons(values: Any) -> list[str]:
    if isinstance(values, list):
        return [_localized_reason(value) for value in values if value]
    if isinstance(values, str):
        return [_localized_reason(part.strip()) for part in values.split(",") if part.strip()]
    return []


def _localized_gold_support(gold_support: Any) -> dict[str, Any]:
    if not isinstance(gold_support, dict):
        return {}
    result = dict(gold_support)
    current_gold = gold_support.get("current_gold")
    if current_gold is None and not gold_support.get("gold_known"):
        status_label = "金币未知"
    elif gold_support.get("supports_entry") is False:
        status_label = "金币不足"
    elif gold_support.get("price_known") is False:
        status_label = "已读取金币，但价格估算不足"
    else:
        status_label = _label_map(SHOP_GOLD_STATUS_LABELS_ZH, gold_support.get("status"))
    result["status_label"] = status_label
    result["reason_label"] = _localized_reason(gold_support.get("reason"))
    return _prune_empty(result)


def _localized_target_counts(target_counts: Any) -> dict[str, Any]:
    if not isinstance(target_counts, dict):
        return {}
    return {
        SHOP_TARGET_COUNT_LABELS_ZH.get(str(key), str(key)): value
        for key, value in target_counts.items()
    }


def _priority_cards(
    data: dict[str, Any],
    cards: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    priority_roles = {"core", "optional"}
    priority_cards = [card for card in cards if card.get("role") in priority_roles]

    def sort_key(card: dict[str, Any]) -> tuple[int, str]:
        role_rank = {"core": 0, "optional": 1}
        return role_rank.get(card.get("role", ""), 9), card.get("name", "")

    return [
        {
            "名称": _zh_name(data, card.get("name")),
            "评级": card.get("tier"),
            "定位": _role_label(card.get("role")),
            "可升级": card.get("can_upgrade", False),
        }
        for card in sorted(priority_cards, key=sort_key)[:limit]
    ]


def _gold_status(current_gold: Any) -> str:
    if current_gold in (None, ""):
        return "未知"
    try:
        gold = int(current_gold)
    except (TypeError, ValueError):
        return "未知"

    if gold <= 5:
        return "极低"
    if gold <= 12:
        return "偏低"
    if gold <= 25:
        return "正常"
    return "充足"


def _is_shop_event(event_data: dict[str, Any]) -> bool:
    event_category = str(event_data.get("event_category") or "").lower()
    event_type = str(event_data.get("event_type") or "").lower()
    return (
        event_category in {"shops", "skill_shops"}
        or event_type in {"shop", "item_shop", "skill_shop", "shop_event"}
        or "shop" in event_type
    )


def _affordability_summary(
    *,
    current_gold: Any,
    event_data: dict[str, Any],
    resource_rewards: dict[str, Any],
) -> dict[str, Any]:
    status = _gold_status(current_gold)
    is_shop = _is_shop_event(event_data)

    try:
        gained_gold = int(resource_rewards.get("gold") or 0)
    except (TypeError, ValueError):
        gained_gold = 0

    notes: list[str] = []
    risk = "未知" if status == "未知" else "无"

    if is_shop:
        if status == "极低":
            risk = "高"
            notes.append("当前金币极低，商店存在刷到目标物品但买不起的风险。")
            notes.append("免费奖励、固定奖励或金币事件的相对稳定性更高。")
        elif status == "偏低":
            risk = "中"
            notes.append("当前金币偏低，商店事件需要考虑购买力风险。")
            notes.append("小卡池、高命中商店优先于大卡池商店。")
        elif status == "正常":
            risk = "低"
            notes.append("当前金币正常，可以正常比较商店卡池质量。")
        elif status == "充足":
            risk = "低"
            notes.append("当前金币充足，高质量商店和转型商店更容易兑现收益。")
        else:
            notes.append("当前金币未知，无法判断商店奖励是否买得起。")
    else:
        if status == "未知":
            notes.append("当前金币未知，但该事件不是纯商店事件，购买力限制较弱。")
        else:
            notes.append("该事件不是纯商店事件，当前金币不会明显限制奖励获取。")

    if gained_gold > 0:
        if status in {"极低", "偏低"}:
            notes.append(f"该事件提供 {gained_gold} 金币，当前金币偏低时价值提高。")
        elif status in {"正常", "充足"}:
            notes.append(f"该事件提供 {gained_gold} 金币，但当前金币不低，边际价值相对下降。")
        else:
            notes.append(f"该事件提供 {gained_gold} 金币。")

    return {
        "当前金币": current_gold,
        "金币状态": status,
        "购买力风险": risk,
        "是否商店事件": is_shop,
        "说明": notes[:3],
    }


def _relation_label(value: Any) -> str:
    if value == "current_build":
        return "当前阶段"
    if value == "future_build":
        return "后续方向"
    if value == "late_build":
        return "后期方向"
    if value == "past_build":
        return "已过期"
    return str(value or "未知")


def _match_band_label(value: Any) -> str:
    if value == "locked":
        return "已成型"
    if value == "close":
        return "接近成型"
    if value == "developing":
        return "发展中"
    if value == "seed":
        return "有苗头"
    if value == "none":
        return "未成型"
    return str(value or "未知")


def _compact_build_matches(build_analysis: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(build_analysis, dict):
        return []

    raw_matches = build_analysis.get("best_matching_builds")
    if not isinstance(raw_matches, list) or not raw_matches:
        raw_matches = build_analysis.get("build_matches")
    if not isinstance(raw_matches, list):
        return []

    compact: list[dict[str, Any]] = []
    for item in raw_matches[:3]:
        if not isinstance(item, dict):
            continue
        owned_core = [
            str(name)
            for name in item.get("owned_core_display", item.get("owned_core", []))
            if name
        ]
        owned_optional = [
            str(name)
            for name in item.get("owned_optional_display", item.get("owned_optional", []))
            if name
        ]
        missing_core = [
            str(name)
            for name in item.get("missing_core_display", item.get("missing_core", []))
            if name
        ]
        compact.append(
            {
                "阵容": item.get("name") or item.get("build_id"),
                "适用阶段": STAGE_LABELS_ZH.get(item.get("phase"), item.get("phase")),
                "阶段关系": _relation_label(item.get("relation")),
                "成型状态": _match_band_label(item.get("match_band")),
                "已拥有核心卡数量": len(owned_core),
                "已拥有核心卡": owned_core,
                "缺失核心卡": missing_core[:6],
                "已拥有可选卡数量": len(owned_optional),
                "已拥有可选卡": owned_optional[:6],
            }
        )
    return compact


def _compact_card_entries(
    data: dict[str, Any],
    entries: Any,
    *,
    limit: int = 12,
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        return []

    compact: list[dict[str, Any]] = []
    for entry in entries[:limit]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("display_name") or _zh_name(data, entry.get("name"))
        if not name:
            continue
        item: dict[str, Any] = {"名称": name}
        for source, target in (
            ("rarity", "稀有度"),
            ("tier", "评级"),
            ("price", "价格"),
            ("card_type", "类型"),
            ("type", "类型"),
        ):
            value = entry.get(source)
            if value not in (None, "") and target not in item:
                item[target] = value
        compact.append(item)
    return compact


def _compact_owned_cards(
    data: dict[str, Any],
    owned_cards: dict[str, str],
    state_context: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if isinstance(state_context, dict):
        display_cards = _compact_card_entries(
            data,
            state_context.get("owned_cards_display"),
            limit=16,
        )
        if display_cards:
            return display_cards

    return [
        {
            "名称": _zh_name(data, name),
            "稀有度": rarity,
        }
        for name, rarity in sorted((owned_cards or {}).items())[:16]
    ]


def _compact_shop_context(
    data: dict[str, Any],
    current_shop: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(current_shop, dict):
        return None

    return {
        "商人": current_shop.get("merchant_display_name")
        or current_shop.get("merchant_name"),
        "可刷新": current_shop.get("refresh_available"),
        "刷新费用": current_shop.get("refresh_cost"),
        "本店剩余刷新次数": current_shop.get("refreshes_remaining"),
        "刷新次数语义": "仅限当前这家商店，不会跨商店累计",
        "预估平均物品价格": current_shop.get("estimated_avg_item_price"),
        "当前可见物品": _compact_card_entries(
            data,
            current_shop.get("visible_items"),
            limit=8,
        ),
    }


def _compact_state_context(
    *,
    data: dict[str, Any],
    owned_cards: dict[str, str],
    current_gold: int | None,
    current_shop: dict[str, Any] | None,
    state_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = state_context if isinstance(state_context, dict) else {}
    resource_fields = {
        "金币": current_gold if current_gold is not None else context.get("gold"),
        "生命": context.get("combat_health", context.get("health")),
        "声望": context.get("prestige"),
        "最大声望": context.get("max_prestige"),
        "收入": context.get("income"),
        "等级": context.get("level"),
        "经验": context.get("xp"),
        "背包占用": context.get("inventory_slots_used"),
        "背包上限": context.get("inventory_slots_total"),
    }

    return {
        "资源": {
            key: value
            for key, value in resource_fields.items()
            if value not in (None, "")
        },
        "已有卡牌": _compact_owned_cards(data, owned_cards, context),
        "已有物品": _compact_card_entries(
            data,
            context.get("owned_items_display"),
            limit=16,
        ),
        "已有技能": _compact_card_entries(
            data,
            context.get("skills_display"),
            limit=16,
        ),
        "当前商店": _compact_shop_context(data, current_shop),
    }


def _compact_pool_stats(pool_stats: Any) -> dict[str, Any]:
    if not isinstance(pool_stats, dict):
        return {}

    mapping = (
        ("draw_count", "展示数量"),
        ("selection_count", "可选数量"),
        ("total_pool_count", "卡池总数"),
        ("valuable_count", "阵容相关数量"),
        ("valuable_ratio", "阵容相关占比"),
        ("core_ratio", "核心占比"),
        ("expected_valuable_in_shop", "预期阵容相关数"),
        ("expected_core_in_shop", "预期核心数"),
        ("prob_valuable_in_shop", "命中阵容相关概率"),
        ("prob_core_in_shop", "命中核心概率"),
        ("expected_sell_gold", "预期出售金币"),
    )
    result: dict[str, Any] = {}
    for source, target in mapping:
        value = pool_stats.get(source)
        if value is None:
            continue
        if isinstance(value, float):
            value = round(value, 4)
        result[target] = value
    return result


def _compact_shop_phase_analyses(
    data: dict[str, Any],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        entry = result.get("shop_entry_analysis")
        inside = result.get("shop_inside_analysis")
        if isinstance(inside, dict):
            action = inside.get("action")
            reason = inside.get("reason")
            compact.append(
                _prune_empty(
                    {
                        "event_name": _zh_name(data, result.get("event_name")) or result.get("event_name"),
                        "phase": "inside_shop",
                        "phase_label": SHOP_PHASE_LABELS_ZH["inside_shop"],
                        "action": action,
                        "action_label": _label_map(SHOP_ACTION_LABELS_ZH, action),
                        "reason": reason,
                        "reason_label": _localized_reason(reason),
                        "visible_offer_count": inside.get("visible_offer_count"),
                        "worth_buying": [
                            {
                                **item,
                                "name": _zh_name(data, item.get("name")) or item.get("name"),
                                "role_label": _role_label(item.get("role")),
                            }
                            for item in inside.get("worth_buying", [])[:6]
                            if isinstance(item, dict)
                        ],
                        "unaffordable_targets": [
                            {
                                **item,
                                "name": _zh_name(data, item.get("name")) or item.get("name"),
                                "role_label": _role_label(item.get("role")),
                            }
                            for item in inside.get("unaffordable_targets", [])[:6]
                            if isinstance(item, dict)
                        ],
                        "refresh_available": inside.get("refresh_available"),
                        "refresh_cost": inside.get("refresh_cost"),
                        "refreshes_remaining_in_this_shop": inside.get("refreshes_remaining"),
                        "refresh_scope": inside.get("refresh_scope"),
                        "refresh_scope_label": "仅限当前这家商店，不会跨商店累计",
                        "refresh_carries_over": inside.get("refresh_carries_over"),
                        "gold_sufficient_for_refresh": inside.get("gold_sufficient_for_refresh"),
                        "pool_quality_band": inside.get("pool_quality_band"),
                        "pool_quality_label": _label_map(SHOP_DENSITY_LABELS_ZH, inside.get("pool_quality_band")),
                    }
                )
            )
        elif isinstance(entry, dict):
            debug = entry.get("debug") if isinstance(entry.get("debug"), dict) else {}
            status = entry.get("status")
            density = entry.get("target_density_band")
            compact.append(
                _prune_empty(
                    {
                        "event_name": _zh_name(data, result.get("event_name")) or result.get("event_name"),
                        "phase": "before_entering_shop",
                        "phase_label": SHOP_PHASE_LABELS_ZH["before_entering_shop"],
                        "rule_status": status,
                        "rule_status_label": _label_map(SHOP_RULE_STATUS_LABELS_ZH, status),
                        "merchant": _zh_name(data, entry.get("merchant")) or entry.get("merchant"),
                        "day": entry.get("day"),
                        "available_on_day": entry.get("available_on_day"),
                        "current_day_available_merchant_count": entry.get("day_available_merchant_count"),
                        "pool_count": entry.get("pool_count"),
                        "target_counts": entry.get("target_counts"),
                        "target_counts_label": _localized_target_counts(entry.get("target_counts")),
                        "target_density_band_vs_current_day_merchants": density,
                        "target_density_label": _label_map(SHOP_DENSITY_LABELS_ZH, density),
                        "target_density_rank": entry.get("target_density_rank"),
                        "top_day_merchants_by_density": [
                            {
                                "merchant": _zh_name(data, item.get("merchant")) or item.get("merchant"),
                                "weighted_density": item.get("weighted_density"),
                                "pool_count": item.get("pool_count"),
                            }
                            for item in entry.get("top_day_merchants_by_density", [])[:5]
                            if isinstance(item, dict)
                        ],
                        "gold_support": _localized_gold_support(entry.get("gold_support")),
                        "theoretical_only": entry.get("theoretical_only"),
                        "worth_spending_choice": entry.get("worth_spending_choice"),
                        "reasons_label": _localized_reasons(entry.get("reasons")),
                        "debug": {
                            "current_core_hits": debug.get("current_core_hits"),
                            "current_tempo_hits": debug.get("current_tempo_hits"),
                            "current_optional_hits": debug.get("current_optional_hits"),
                            "future_core_hits": debug.get("future_core_hits"),
                            "gold_support_status": debug.get("gold_support_status"),
                            "gold_support_status_label": _label_map(
                                SHOP_GOLD_STATUS_LABELS_ZH,
                                debug.get("gold_support_status"),
                            ),
                        },
                    }
                )
            )
    return compact


def _compact_shop_pool_summary(data: dict[str, Any], summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}

    return _prune_empty(
        {
            "cache_status": summary.get("cache_status"),
            "cache_status_label": _label_map(SHOP_CACHE_STATUS_LABELS_ZH, summary.get("cache_status")),
            "merchant_name": _zh_name(data, summary.get("merchant_name")) or summary.get("merchant_name"),
            "hero": summary.get("hero"),
            "stage": summary.get("stage"),
            "stage_label": _label_map(SHOP_POOL_STAGE_LABELS_ZH, summary.get("stage")),
            "pool_count": summary.get("pool_count"),
            "avg_price": summary.get("avg_price"),
            "min_price": summary.get("min_price"),
            "max_price": summary.get("max_price"),
            "available_days": summary.get("available_days"),
            "appearance_days": summary.get("appearance_days"),
            "available_on_day": summary.get("available_on_day"),
            "shop_tier": summary.get("shop_tier"),
            "base_refresh_cost": summary.get("base_refresh_cost"),
            "base_refresh_count": summary.get("base_refresh_count"),
            "refresh_enabled": summary.get("refresh_enabled"),
        }
    )


def _compact_followups(data: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    followups = result.get("followup_options")
    if not isinstance(followups, list):
        return []

    compact: list[dict[str, Any]] = []
    for option in followups[:3]:
        if not isinstance(option, dict):
            continue
        compact.append(
            {
                "名称": _zh_name(data, option.get("event_name") or option.get("name")),
                "事件类型": option.get("event_type"),
                "资源收益": option.get("resource_rewards") or {},
                "卡池摘要": _compact_pool_stats(option.get("pool_stats")),
            }
        )
    return compact


def _compact_parent_child_options(
    data: dict[str, Any],
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    child_options = result.get("child_options")
    if not isinstance(child_options, list):
        return []

    compact: list[dict[str, Any]] = []
    for option in child_options[:6]:
        if not isinstance(option, dict):
            continue
        name = (
            option.get("display_name")
            or option.get("name")
            or _zh_name(data, option.get("source_name"))
        )
        source_name = option.get("source_name")
        source_id = option.get("source_id")
        name = _zh_id(data, source_id) or _translate_common_game_text(data, _zh_name(data, name) or name)
        source_display_name = _zh_name(data, source_name)
        description = option.get("description")
        translated_description = (
            data.get("translations", {}).get("by_name", {}).get(str(description))
            if description
            else None
        )
        compact.append(
            _prune_empty(
                {
                    "name": name,
                    "source_display_name": source_display_name,
                    "kind": option.get("kind"),
                    "card_type": option.get("card_type"),
                    "description": translated_description or _translate_common_game_text(data, description),
                    "resource_rewards": option.get("resource_rewards") or {},
                    "reward_text": _translate_common_game_text(data, option.get("reward_text")),
                    "source": option.get("source"),
                    "seen": option.get("seen"),
                }
            )
        )
    return compact


def _compact_parent_followup_context(
    data: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    child_options = _compact_parent_child_options(data, result)
    if not child_options:
        return {}

    best_summary = result.get("best_followup_summary")
    if not isinstance(best_summary, dict):
        best_summary = result.get("followup_value_summary")
    if not isinstance(best_summary, dict):
        best_summary = {}

    return _prune_empty(
        {
            "must_consider": "这是父事件，必须同时评估它的后续选项；不要因为父事件本身没有直接收益就当成低价值。",
            "event_rule_status": result.get("event_rule_status"),
            "event_rule_status_label": "父事件" if result.get("event_rule_status") == "parent_event" else result.get("event_rule_status"),
            "best_followup": result.get("best_followup_display")
            or _zh_name(data, result.get("best_followup")),
            "best_followup_summary": best_summary,
            "child_options": child_options,
        }
    )


def _compact_event_results(
    data: dict[str, Any],
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        event_name = result.get("event_name")
        shop_decision = result.get("shop_decision")
        compact_item: dict[str, Any] = {
            "事件": _zh_name(data, event_name) or event_name,
            "事件原名": event_name,
            "事件类型": result.get("event_type"),
            "说明": _zh_text(data, result.get("notes")),
            "资源收益": result.get("resource_rewards") or {},
            "稀有度过滤": result.get("resolved_rarity_filter") or {},
            "角色命中数量": result.get("role_counts") or {},
            "高评级数量": result.get("high_tier_count") or 0,
            "可升级已有目标": [
                _zh_name(data, name)
                for name in result.get("upgrade_hits", [])[:6]
            ],
            "已有目标命中": _priority_cards(
                data,
                result.get("owned_target_hits", []),
                limit=6,
            ),
            "优先相关卡摘要": _priority_cards(
                data,
                result.get("possible_cards", []),
                limit=8,
            ),
            "卡池摘要": _compact_pool_stats(result.get("pool_stats")),
            "后续选项": _compact_followups(data, result),
            "最佳后续选项": _zh_name(data, result.get("best_followup"))
            if result.get("best_followup")
            else None,
            "转型核心命中数量": result.get("alt_core_card_count") or 0,
        }
        parent_followup_context = _compact_parent_followup_context(data, result)
        if parent_followup_context:
            compact_item["parent_followup_options"] = parent_followup_context
        shop_pool_summary = _compact_shop_pool_summary(data, result.get("shop_pool_summary"))
        if shop_pool_summary:
            compact_item["shop_pool_summary"] = shop_pool_summary

        if isinstance(shop_decision, dict):
            compact_item["商店刷新事实与旧规则参考"] = {
                "可刷新": shop_decision.get("refresh_available"),
                "刷新费用": shop_decision.get("refresh_cost"),
                "可见物品价格来源": shop_decision.get("purchase_budget_price_source"),
                "购买预算价格": shop_decision.get("purchase_budget_price"),
                "刷新后仍有购买力": shop_decision.get("gold_sufficient_for_refresh"),
                "刷新卡池相关占比": shop_decision.get("refresh_pool_valuable_ratio"),
                "旧规则动作参考": shop_decision.get("action"),
                "旧规则原因参考": _zh_text(data, shop_decision.get("reason")),
            }
        compact_item["旧规则档位参考"] = RECOMMENDATION_LABELS_ZH.get(
            result.get("recommendation"),
            result.get("recommendation"),
        )
        compact.append(_prune_empty(compact_item))
    return compact


def _compact_candidate_cards(
    data: dict[str, Any],
    build_analysis: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(build_analysis, dict):
        return []

    candidates = build_analysis.get("candidate_cards")
    if not isinstance(candidates, list):
        return []

    compact: list[dict[str, Any]] = []
    for candidate in candidates[:8]:
        if not isinstance(candidate, dict):
            continue
        hits: list[dict[str, Any]] = []
        for hit in candidate.get("build_hits", [])[:3]:
            if not isinstance(hit, dict):
                continue
            hits.append(
                {
                    "阵容": hit.get("build_display_name")
                    or hit.get("build_name")
                    or hit.get("build_id"),
                    "阶段": hit.get("build_phase_label") or hit.get("build_phase"),
                    "鍏崇郴": hit.get("relation_label")
                    or _relation_label(hit.get("relation")),
                    "定位": hit.get("role_label") or _role_label(hit.get("role")),
                }
            )
        compact.append(
            _prune_empty(
                {
                    "物品": candidate.get("card_display_name")
                    or _zh_name(data, candidate.get("card_name")),
                    "价格": candidate.get("price"),
                    "是否买得起": candidate.get("affordable"),
                    "阵容命中": hits,
                    "事实原因": candidate.get("reasons", [])[:4],
                    "椋庨櫓": candidate.get("risks", [])[:4],
                    "需要AI判断": candidate.get("needs_ai_judgement"),
                    "旧规则参考": {
                        "重要度": candidate.get("importance_label")
                        or candidate.get("importance"),
                        "鍔ㄤ綔": candidate.get("recommendation_type_label")
                        or candidate.get("recommendation_type"),
                    },
                }
            )
        )
    return compact


def _compact_visible_core_bundles(
    build_analysis: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(build_analysis, dict):
        return []

    bundles = build_analysis.get("visible_core_bundles")
    if not isinstance(bundles, list):
        return []

    compact: list[dict[str, Any]] = []
    for bundle in bundles[:3]:
        if not isinstance(bundle, dict):
            continue
        compact.append(
            _prune_empty(
                {
                    "阵容": bundle.get("build_name") or bundle.get("build_id"),
                    "可见核心": bundle.get("candidate_core_cards_display")
                    or bundle.get("candidate_core_cards")
                    or [],
                    "购买前已有核心": bundle.get("owned_core_before_display")
                    or bundle.get("owned_core_before")
                    or [],
                    "购买后核心": bundle.get("owned_core_after_if_bought_display")
                    or bundle.get("owned_core_after_if_bought")
                    or [],
                    "是否买得起": bundle.get("affordable"),
                    "旧规则参考": {
                        "重要度": bundle.get("importance_label")
                        or bundle.get("importance"),
                        "鍔ㄤ綔": bundle.get("recommendation_label")
                        or bundle.get("recommendation"),
                    },
                }
            )
        )
    return compact


def _prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: pruned
            for key, child in value.items()
            if (pruned := _prune_empty(child)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            pruned
            for child in value
            if (pruned := _prune_empty(child)) not in (None, "", [], {})
        ]
    return value


def compact_recommendations(
    *,
    data: dict[str, Any],
    hero: str,
    build_name: str,
    current_day: int,
    owned_cards: dict[str, str],
    results: list[dict[str, Any]],
    current_gold: int | None = None,
    current_shop: dict[str, Any] | None = None,
    build_analysis: dict[str, Any] | None = None,
    guide_context: list[dict[str, Any]] | None = None,
    state_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    builds = data.get("builds", {})
    build_data = builds.get(build_name, {}) if isinstance(builds, dict) else {}
    if not isinstance(build_data, dict):
        build_data = {}
    current_stage = get_game_stage_for_day(current_day)
    compact_builds = _compact_build_matches(build_analysis)
    current_build_display = (
        build_data.get("name")
        or build_data.get("display_name")
        or build_name
        or "未知阵容"
    )
    build_timing_summary = (
        format_build_timing_summary(build_data, current_day)
        if build_data
        else "当前阵容未匹配到本地阵容数据。"
    )

    payload = {
        "分析任务": "基于当前实时状态、规则层客观信息和相关攻略，分析当前事件/物品/阵容方向。",
        "英雄": hero,
        "天数": current_day,
        "当前阶段": STAGE_LABELS_ZH.get(current_stage, current_stage),
        "当前阵容": current_build_display,
        "当前阵容适用时机": build_timing_summary,
        "当前状态": _compact_state_context(
            data=data,
            owned_cards=owned_cards,
            current_gold=current_gold,
            current_shop=current_shop,
            state_context=state_context,
        ),
        "候选阵容": compact_builds,
        "事件客观信息": _compact_event_results(data, results),
        "候选物品与阵容关系": _compact_candidate_cards(data, build_analysis),
        "可见核心组合": _compact_visible_core_bundles(build_analysis),
        "资源压力指标": (
            build_analysis.get("operation_urgency", {})
            if isinstance(build_analysis, dict)
            else {}
        ),
        "商店旧规则参考": (
            {
                "动作": build_analysis.get("shop_action_label")
                or build_analysis.get("shop_action"),
                "原因": build_analysis.get("refresh_reason"),
            }
            if isinstance(build_analysis, dict)
            else {}
        ),
    }

    if guide_context:
        payload["相关攻略"] = guide_context

    shop_phase_analysis = _compact_shop_phase_analyses(data, results)
    if shop_phase_analysis:
        payload["shop_phase_analysis"] = shop_phase_analysis

    payload["AI_priority_rules"] = [
        "如果事件有 parent_followup_options，要把父事件和后续选项的收益一起判断。",
        "攻略内容是策略证据，不是低优先级附录；需要说明它是否契合当前局面。",
        "需要综合规则层、当前状态和攻略经验，不要只看阵容匹配或当前牌面。",
        "商店必须区分阶段：进店前只判断是否值得花三选一机会进入，不讨论可见商品；进店后再判断买什么和是否刷新。",
        "不要重新计算商店池概率；把 rule_status 当作规则层粗筛的参考倾向。",
    ]

    if guide_context:
        payload["strategy_guide_context"] = {
            "must_use": (
                "Use these guide sections as strategic evidence. If a guide point is relevant, "
                "say how it changes or supports the recommendation."
            ),
            "sections": guide_context,
        }

    return _prune_empty(payload)


def _json_char_count(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _count_fields(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_count_fields(child) for child in value.values())
    if isinstance(value, list):
        return sum(_count_fields(child) for child in value)
    return 0


def _remove_path(payload: dict[str, Any], removed: list[str], *path: str) -> None:
    current: Any = payload
    for key in path[:-1]:
        if not isinstance(current, dict):
            return
        current = current.get(key)
    if isinstance(current, dict) and path[-1] in current:
        current.pop(path[-1], None)
        removed.append(".".join(path))


def _detect_analysis_scene(payload: dict[str, Any]) -> str:
    phases = {
        item.get("phase")
        for item in payload.get("shop_phase_analysis", [])
        if isinstance(item, dict)
    }
    if "inside_shop" in phases:
        return "inside_shop"
    if "before_entering_shop" in phases:
        return "before_entering_shop"
    return "event_selection"


def _trim_event_for_ai(event: Any, scene: str, removed: list[str]) -> Any:
    if not isinstance(event, dict):
        return event
    trimmed = dict(event)
    if scene != "inside_shop":
        for key in ("商店刷新事实与旧规则参考",):
            if key in trimmed:
                trimmed.pop(key, None)
                removed.append(f"事件客观信息[].{key}")
    if "parent_followup_options" in trimmed:
        followup = dict(trimmed["parent_followup_options"])
        if "must_consider" in followup:
            followup.pop("must_consider", None)
            removed.append("事件客观信息[].parent_followup_options.must_consider")
        trimmed["parent_followup_options"] = followup
    if "旧规则档位参考" in trimmed:
        trimmed.pop("旧规则档位参考", None)
        removed.append("事件客观信息[].旧规则档位参考")
    return _prune_empty(trimmed)


def prepare_ai_payload_for_model(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    model_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    removed: list[str] = []
    original_field_count = _count_fields(model_payload)
    original_summary_chars = _json_char_count(model_payload)
    scene = _detect_analysis_scene(model_payload)

    _remove_path(model_payload, removed, "AI_priority_rules")
    if "strategy_guide_context" in model_payload and "相关攻略" in model_payload:
        _remove_path(model_payload, removed, "相关攻略")
    if isinstance(model_payload.get("strategy_guide_context"), dict):
        _remove_path(model_payload, removed, "strategy_guide_context", "must_use")

    shop_phase = model_payload.get("shop_phase_analysis")
    if isinstance(shop_phase, list):
        wanted_phase = "inside_shop" if scene == "inside_shop" else "before_entering_shop"
        filtered = [
            item
            for item in shop_phase
            if not isinstance(item, dict) or item.get("phase") == wanted_phase
        ]
        if len(filtered) != len(shop_phase):
            model_payload["shop_phase_analysis"] = filtered
            removed.append(f"shop_phase_analysis[phase!={wanted_phase}]")

    events = model_payload.get("事件客观信息")
    if scene == "inside_shop":
        _remove_path(model_payload, removed, "事件客观信息")
    elif isinstance(events, list):
        model_payload["事件客观信息"] = [
            _trim_event_for_ai(event, scene, removed) for event in events
        ]

    if scene == "before_entering_shop":
        _remove_path(model_payload, removed, "当前状态", "当前商店")
        _remove_path(model_payload, removed, "候选物品与阵容关系")
        _remove_path(model_payload, removed, "可见核心组合")
        _remove_path(model_payload, removed, "商店旧规则参考")
    elif scene == "event_selection":
        _remove_path(model_payload, removed, "当前状态", "当前商店")
        _remove_path(model_payload, removed, "商店旧规则参考")
    elif scene == "inside_shop":
        _remove_path(model_payload, removed, "商店旧规则参考")

    model_payload = _prune_empty(model_payload)
    debug = {
        "analysis_scene": scene,
        "summary_json_chars_before": original_summary_chars,
        "summary_json_chars_after": _json_char_count(model_payload),
        "field_count_before": original_field_count,
        "field_count_after": _count_fields(model_payload),
        "trimmed_fields": sorted(set(removed)),
    }
    return model_payload, debug


def _log_ai_debug(debug: dict[str, Any]) -> None:
    if os.environ.get(AI_DEBUG_ENV_VAR):
        print(
            "[AI prompt debug] "
            + json.dumps(debug, ensure_ascii=False, separators=(",", ":")),
            file=sys.stderr,
        )


def _build_ai_messages_impl(payload: dict[str, Any]) -> list[dict[str, str]]:
    model_payload, debug = prepare_ai_payload_for_model(payload)
    summary_json = json.dumps(model_payload, ensure_ascii=False, separators=(",", ":"))
    user_content = (
        "下面提供当前局的实时状态、规则层计算结果、商店阶段分析和相关攻略。\n"
        "请比较所有当前选项，结合即时收益、当前需求、阵容适配、后续价值和机会成本，给出简洁、有依据的局势分析。\n"
        "当前数据：\n"
        f"{summary_json}"
    )
    debug.update(
        {
            "system_prompt_chars": len(AI_SYSTEM_PROMPT),
            "user_prompt_chars": len(user_content),
            "final_prompt_chars": len(AI_SYSTEM_PROMPT) + len(user_content),
        }
    )
    LAST_AI_PROMPT_DEBUG.clear()
    LAST_AI_PROMPT_DEBUG.update(debug)
    _log_ai_debug(debug)

    return [
        {
            "role": "system",
            "content": AI_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]



def build_ai_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return _build_ai_messages_impl(payload)


def read_api_key_file(path: Path = DEFAULT_API_KEY_FILE) -> str | None:
    if not path.exists():
        return None

    api_key = path.read_text(encoding="utf-8").strip()
    return api_key or None


def resolve_api_key(api_key: str | None = None) -> str | None:
    return api_key or os.environ.get("DEEPSEEK_API_KEY") or read_api_key_file()


def clean_ai_output(text: str) -> str:
    """清理智能分析输出中的 Markdown 符号，避免前端直接显示 **、缩进列表等。"""
    if not text:
        return ""

    text = text.replace("\r\n", "\n")

    # 鍘绘帀甯歌 Markdown 寮鸿皟绗﹀彿
    text = text.replace("**", "")
    text = text.replace("__", "")

    # 去掉标题符号
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)

    # 鍘绘帀琛岄椤圭洰绗﹀彿
    text = re.sub(r"(?m)^\s*[\*\-•]\s*", "", text)

    # 鍘嬪钩杩囨繁缂╄繘
    text = re.sub(r"(?m)^\s{2,}", "", text)

    # 鍘嬬缉绌鸿
    text = re.sub(r"\n{3,}", "\n\n", text)

    replacements = [
        ("Current recommendation", "当前推荐"),
        ("Current base", "当前基础"),
        ("Main issue", "主要问题"),
        ("Next step", "下一步建议"),
        ("High Value", "优先选择"),
        ("Medium Value", "可以考虑"),
        ("Low Value", "优先级低"),
        ("current_build", "当前阶段"),
        ("future_build", "后续方向"),
        ("late_build", "后期方向"),
        ("past_build", "已过期"),
        ("Recommendation", "鎺ㄨ崘"),
        ("Reasons", "鍘熷洜"),
        ("Reason", "鍘熷洜"),
        ("Transition", "杩囨浮"),
        ("transition", "杩囨浮"),
        ("Optional", "可选"),
        ("optional", "可选"),
        ("Build", "阵容"),
        ("build", "阵容"),
        ("Core", "核心"),
        ("core", "核心"),
        ("tier", "评级"),
    ]
    for source, target in replacements:
        text = text.replace(source, target)

    return text.strip()


def call_deepseek(
    messages: list[dict[str, str]],
    *,
    api_key: str | None = None,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    timeout: int = 30,
) -> str:
    api_key = resolve_api_key(api_key)
    if not api_key:
        raise RuntimeError(
            "没有找到 DeepSeek API Key。请在启动 UI 前设置 DEEPSEEK_API_KEY，"
            f"或把 key 放到 {DEFAULT_API_KEY_FILE}。"
        )

    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API 返回 HTTP {exc.code}：{error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 DeepSeek API：{exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError("连接 DeepSeek API 超时。") from exc

    try:
        decoded = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("DeepSeek API 返回了无法解析的 JSON。") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("DeepSeek API 返回结构不符合预期。")

    usage = decoded.get("usage")
    LAST_AI_USAGE.clear()
    if isinstance(usage, dict):
        LAST_AI_USAGE.update(
            {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        )
        if os.environ.get(AI_DEBUG_ENV_VAR):
            print(
                "[AI usage] "
                + json.dumps(LAST_AI_USAGE, ensure_ascii=False, separators=(",", ":")),
                file=sys.stderr,
            )
    try:
        return decoded["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("DeepSeek API 返回结构不符合预期。") from exc


def analyze_with_ai(
    payload: dict[str, Any],
    *,
    model: str = DEFAULT_DEEPSEEK_MODEL,
    base_url: str = DEFAULT_DEEPSEEK_BASE_URL,
    timeout: int = 30,
) -> str:
    raw_text = call_deepseek(
        build_ai_messages(payload),
        model=model,
        base_url=base_url,
        timeout=timeout,
    )
    return clean_ai_output(raw_text)
