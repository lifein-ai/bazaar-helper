from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from build_strategy import format_build_timing_summary, get_game_stage_for_day
from app_paths import get_runtime_dir


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_API_KEY_FILE = get_runtime_dir() / "deepseek_api_key.txt"
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
    "你是《The Bazaar》的局势分析助手。你的职责是帮助玩家理解当前局势、"
    "比较不同方向，并提供有依据的参考倾向；你不是替玩家做决定的自动决策器。\n"
    "全程使用简体中文。把 Build 说成“阵容”，把 core 说成“核心”，"
    "把 optional 说成“可选”，把 tier 说成“评级”。\n"
    "分析时综合考虑当前天数和阶段、生命、声望、金币、收入、等级、背包压力、"
    "已有卡牌/物品/技能、当前阵容基础、缺失核心、事件收益、商店卡池和概率、"
    "当前候选物品、即时战力、成长上限、阵容联动、转型成本、经济压力和当前风险。\n"
    "当前实时状态决定玩家现在真实拥有和面对的内容；官方数据和规则层计算提供客观事实；"
    "高手攻略提供重要的策略知识和实战经验，是分析的重要依据之一，不要简单视为低优先级补充。\n"
    "事实层以当前实时状态、当前版本官方数据和规则层客观计算为准。"
    "当攻略与实时状态或当前版本官方数据明确冲突时，以实时状态和官方数据为准；"
    "如果能判断可能存在版本差异，可以简短提示。\n"
    "策略层结合攻略经验与规则指标综合判断。不要只按核心卡数量、卡池概率、旧规则推荐、"
    "阵容匹配数量直接得出结论；当攻略和规则指标有不同倾向但没有事实冲突时，"
    "需要分析差异原因，并结合当前局势判断哪个依据更有价值。\n"
    "规则层的旧档位、商店动作和旧推荐原因只是参考，不要直接复述或默认同意第一名；"
    "要比较所有当前事件、商店或物品选项。\n"
    "不要编造未提供的卡牌、技能、阵容、概率或游戏机制；不得假设玩家拥有当前状态中不存在的卡牌或技能。\n"
    "输出自然、简洁、易读，根据局势组织为 2 到 4 个短段落，默认约 150 到 300 个中文字，"
    "简单局势可以更短。重点说明当前局势特点、值得关注的选择、关键依据、不同方向的收益和风险、"
    "以及当前更偏向哪种思路和适用条件。\n"
    "允许存在多个合理方向。可以给出参考倾向，但保留玩家决策空间。"
    "避免命令式和绝对化表达，尤其避免“必须”“一定要”“直接选”“只能”“毫无疑问选择”。\n"
    "不要使用表格、代码块、大段 Markdown 或多层列表；不要逐项复述输入数据。\n"
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
        "剩余刷新次数": current_shop.get("refreshes_remaining"),
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
                    "关系": hit.get("relation_label")
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
                    "风险": candidate.get("risks", [])[:4],
                    "需要AI判断": candidate.get("needs_ai_judgement"),
                    "旧规则参考": {
                        "重要度": candidate.get("importance_label")
                        or candidate.get("importance"),
                        "动作": candidate.get("recommendation_type_label")
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
                        "动作": bundle.get("recommendation_label")
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
    build_data = data["builds"][build_name]
    current_stage = get_game_stage_for_day(current_day)
    compact_builds = _compact_build_matches(build_analysis)
    current_build_display = (
        build_data.get("name")
        or build_data.get("display_name")
        or build_name
    )

    payload = {
        "分析任务": "基于当前实时状态、规则层客观信息和相关攻略，分析当前事件/物品/阵容方向。",
        "英雄": hero,
        "天数": current_day,
        "当前阶段": STAGE_LABELS_ZH.get(current_stage, current_stage),
        "当前阵容": current_build_display,
        "当前阵容适用时机": format_build_timing_summary(build_data, current_day),
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

    return _prune_empty(payload)


def build_ai_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    summary_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    return [
        {
            "role": "system",
            "content": AI_SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": (
                "下面是当前局的实时状态、规则层客观计算、旧规则参考和相关攻略片段。请比较所有当前选项后给出分析：\n"
                f"{summary_json}"
            ),
        },
    ]


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

    # 去掉常见 Markdown 强调符号
    text = text.replace("**", "")
    text = text.replace("__", "")

    # 去掉标题符号
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)

    # 去掉行首项目符号
    text = re.sub(r"(?m)^\s*[\*\-•]\s*", "", text)

    # 压平过深缩进
    text = re.sub(r"(?m)^\s{2,}", "", text)

    # 压缩空行
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
        ("Recommendation", "推荐"),
        ("Reasons", "原因"),
        ("Reason", "原因"),
        ("Transition", "过渡"),
        ("transition", "过渡"),
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

    decoded = json.loads(response_body)
    return decoded["choices"][0]["message"]["content"]


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
