from __future__ import annotations

import json
import os
import re
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
    "early": "\u524d\u671f",
    "mid": "\u4e2d\u671f",
    "late": "\u540e\u671f",
}
RECOMMENDATION_LABELS_ZH = {
    "High Value": "\u4f18\u5148\u9009\u62e9",
    "Medium Value": "\u53ef\u4ee5\u8003\u8651",
    "Low Value": "\u4f18\u5148\u7ea7\u4f4e",
}
ROLE_LABELS_ZH = {
    "core": "\u6838\u5fc3",
    "transition": "\u8fc7\u6e21",
    "optional": "\u53ef\u9009",
    "unrelated": "\u65e0\u5173",
}
AI_SYSTEM_PROMPT = (
    "\u4f60\u662f\u300aThe Bazaar\u300b\u7684\u5c40\u52bf\u5206\u6790\u52a9\u624b\uff0c\u59cb\u7ec8\u4f7f\u7528\u7b80\u4f53\u4e2d\u6587\u3002\n"
    "\u672f\u8bed\u7edf\u4e00\uff1aBuild \u79f0\u4e3a\u9635\u5bb9\uff0ccore \u79f0\u4e3a\u6838\u5fc3\uff0coptional \u79f0\u4e3a\u53ef\u9009\uff0ctier \u79f0\u4e3a\u8bc4\u7ea7\u3002\n"
    "\u4f60\u7684\u804c\u8d23\u662f\u7ed3\u5408\u5f53\u524d\u5c40\u52bf\u6bd4\u8f83\u5019\u9009\u65b9\u5411\uff0c\u89e3\u91ca\u4e3b\u8981\u6536\u76ca\u3001\u9635\u5bb9\u9002\u914d\u548c\u673a\u4f1a\u6210\u672c\uff0c\u4e3a\u73a9\u5bb6\u63d0\u4f9b\u6709\u4f9d\u636e\u7684\u53c2\u8003\u503e\u5411\u3002\u4e0d\u8981\u66ff\u73a9\u5bb6\u505a\u51b3\u5b9a\uff0c\u4e0d\u8981\u4f7f\u7528\u7edd\u5bf9\u547d\u4ee4\u3002\n"
    "\u53ea\u80fd\u4f7f\u7528\u8f93\u5165\u4e2d\u7684\u5b9e\u65f6\u72b6\u6001\u3001\u89c4\u5219\u5c42\u7ed3\u679c\u3001\u5b98\u65b9\u6570\u636e\u548c\u653b\u7565\u4e0a\u4e0b\u6587\u3002\u7981\u6b62\u7f16\u9020\u672a\u63d0\u4f9b\u7684\u5361\u724c\u3001\u6280\u80fd\u3001\u4ef7\u683c\u3001\u6982\u7387\u3001\u9635\u5bb9\u3001\u673a\u5236\u6216\u5c40\u5185\u4fe1\u606f\u3002\n"
    "\u4e8b\u4ef6\u5b58\u5728\u540e\u7eed\u9009\u9879\u65f6\uff0c\u5fc5\u987b\u628a\u540e\u7eed\u9009\u9879\u4e2d\u7684\u8d44\u6e90\u3001\u6280\u80fd\u3001\u5361\u6c60\u548c\u6218\u6597\u6536\u76ca\u7eb3\u5165\u6574\u4f53\u5224\u65ad\uff0c\u4e0d\u8981\u56e0\u4e3a\u7236\u4e8b\u4ef6\u672c\u8eab\u6ca1\u6709\u76f4\u63a5\u6536\u76ca\u5c31\u4f4e\u4f30\u5b83\u3002\n"
    "\u5b58\u5728\u653b\u7565\u4e0a\u4e0b\u6587\u65f6\uff0c\u5fc5\u987b\u5c06\u653b\u7565\u4f5c\u4e3a\u91cd\u8981\u7b56\u7565\u4f9d\u636e\uff0c\u5e76\u5224\u65ad\u5176\u4e0e\u5f53\u524d\u72b6\u6001\u662f\u5951\u5408\u3001\u90e8\u5206\u5951\u5408\u8fd8\u662f\u6682\u4e0d\u9002\u7528\u3002\u653b\u7565\u4e0d\u80fd\u8986\u76d6\u5b9e\u65f6\u72b6\u6001\u548c\u89c4\u5219\u5c42\u4e8b\u5b9e\u3002\n"
    "\u4e25\u683c\u533a\u5206\u5546\u5e97\u9636\u6bb5\u3002\u8fdb\u5e97\u524d\u53ea\u5224\u65ad\u5546\u5e97\u662f\u5426\u503c\u5f97\u5360\u7528\u5f53\u524d\u4e09\u9009\u4e00\u673a\u4f1a\uff1b\u7ed3\u5408\u5546\u5e97\u89c4\u5219\u6458\u8981\u3001\u4e8b\u4ef6\u6536\u76ca\u3001\u514d\u8d39\u6536\u76ca\u3001\u91d1\u5e01\u548c\u653b\u7565\uff1b\u4e0d\u8ba8\u8bba\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u3001\u5177\u4f53\u8d2d\u4e70\u76ee\u6807\u3001\u5b9e\u65f6\u4ef7\u683c\u6216\u5237\u65b0\u7ed3\u679c\u3002"
    "\u8fdb\u5e97\u540e\u6839\u636e\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u548c\u5546\u5e97\u5185\u5206\u6790\u5224\u65ad\u8d2d\u4e70\u4e0e\u5237\u65b0\uff1b\u5f53\u524d\u5df2\u6709\u5f3a\u76ee\u6807\u65f6\uff0c\u901a\u5e38\u4e0d\u4f18\u5148\u5efa\u8bae\u5237\u65b0\u3002\u5237\u65b0\u53ea\u5c5e\u4e8e\u5f53\u524d\u5546\u5e97\uff0c\u4e0d\u4f1a\u7d2f\u8ba1\u5230\u4e0b\u4e00\u5bb6\u5e97\u3002\n"
    "\u4e0d\u8981\u81ea\u884c\u8ba1\u7b97\u5361\u6c60\u6982\u7387\u3002\u76f4\u63a5\u4f7f\u7528\u89c4\u5219\u5c42\u63d0\u4f9b\u7684\u72b6\u6001\u3001\u76ee\u6807\u6570\u91cf\u548c\u6458\u8981\uff0c\u5e76\u5c06\u89c4\u5219\u5c42\u7ed3\u679c\u89c6\u4e3a\u53c2\u8003\u503e\u5411\uff0c\u4e0d\u662f\u7edd\u5bf9\u547d\u4ee4\u3002\n"
    "\u56de\u7b54\u524d\u6bd4\u8f83\u6240\u6709\u5f53\u524d\u9009\u9879\u3002\u91cd\u70b9\u89e3\u91ca\u771f\u6b63\u5f71\u54cd\u51b3\u7b56\u7684\u5dee\u5f02\uff0c\u4e0d\u9010\u9879\u590d\u8ff0\u8f93\u5165\u3002\u901a\u5e38\u8f93\u51fa 2 \u5230 4 \u4e2a\u77ed\u6bb5\u843d\uff0c\u7ea6 150 \u5230 300 \u4e2a\u4e2d\u6587\u5b57\u3002\u907f\u514d\u8868\u683c\u3001\u4ee3\u7801\u5757\u548c\u8fc7\u91cd Markdown\u3002"
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
        (r"^Gain (\d+) Max Health$", "\u83b7\u5f97 \1 \u6700\u5927\u751f\u547d\u503c"),
        (r"^Gain (\d+) gold$", "\u83b7\u5f97 \1 \u91d1\u5e01"),
        (r"^Gain (\d+) XP$", "\u83b7\u5f97 \1 \u7ecf\u9a8c"),
        (r"^Heal (\d+)$", "\u6cbb\u7597 \1"),
        (r"^Deal (\d+) Damage$", "\u9020\u6210 \1 \u4f24\u5bb3"),
        (r"^Get a ([A-Za-z]+)-tier Loot item$", "\u83b7\u5f97\u4e00\u4ef6\1\u7ea7\u6218\u5229\u54c1\u7269\u54c1"),
        (r"^\(if you have a ([^)]+)\) Choose a Skill$", "\uff08\u5982\u679c\u4f60\u62e5\u6709\1\uff09\u9009\u62e9\u4e00\u4e2a\u6280\u80fd"),
    ]
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    term_replacements = {
        "Bronze": "\u9752\u94dc",
        "Silver": "\u767d\u94f6",
        "Gold": "\u9ec4\u91d1",
        "Diamond": "\u94bb\u77f3",
        "Food": "\u98df\u7269",
        "Friend": "\u670b\u53cb",
        "Toy": "\u73a9\u5177",
        "Drone": "\u65e0\u4eba\u673a",
        "Loot": "\u6218\u5229\u54c1",
        "Skill": "\u6280\u80fd",
    }
    for source, translated in term_replacements.items():
        result = result.replace(source, translated)
    return result

SHOP_RULE_STATUS_LABELS_ZH = {
    "strong_candidate": "\u5f3a\u5019\u9009",
    "candidate": "\u53ef\u8003\u8651",
    "situational": "\u770b\u5c40\u52bf",
    "weak_candidate": "\u4e0d\u4f18\u5148",
    "not_actionable": "\u6682\u4e0d\u5efa\u8bae",
    "unknown": "\u4fe1\u606f\u4e0d\u8db3",
}
SHOP_PHASE_LABELS_ZH = {
    "before_entering_shop": "\u8fdb\u5e97\u524d\u8bc4\u4f30",
    "inside_shop": "\u5e97\u5185\u64cd\u4f5c",
}
SHOP_ACTION_LABELS_ZH = {
    "buy": "\u5148\u4e70\u76ee\u6807",
    "refresh": "\u53ef\u4ee5\u5237\u65b0",
    "skip": "\u4e0d\u5efa\u8bae\u5237\u65b0",
}
SHOP_DENSITY_LABELS_ZH = {
    "high": "\u9ad8",
    "medium": "\u4e2d",
    "low": "\u4f4e",
    "unknown": "\u672a\u77e5",
}
SHOP_GOLD_STATUS_LABELS_ZH = {
    "refresh_supported": "\u53ef\u4e70\u4e14\u53ef\u5237",
    "buy_supported": "\u5927\u81f4\u53ef\u4e70",
    "insufficient": "\u91d1\u5e01\u4e0d\u8db3",
    "unknown": "\u672a\u77e5",
}
SHOP_REASON_LABELS_ZH = {
    "current_gold_unknown": "\u5f53\u524d\u91d1\u5e01\u672a\u77e5",
    "price_unknown": "\u76ee\u6807\u4ef7\u683c\u4f30\u7b97\u4e0d\u8db3",
    "merchant_not_available_on_current_day": "\u8be5\u5546\u4eba\u4e0d\u5c5e\u4e8e\u5f53\u5929\u53ef\u51fa\u73b0\u5546\u4eba",
    "pool_contains_current_core_targets": "\u6c60\u5b50\u547d\u4e2d\u5f53\u524d\u6838\u5fc3\u76ee\u6807",
    "pool_contains_current_tempo_or_optional_targets": "\u6c60\u5b50\u547d\u4e2d\u5f53\u524d\u8fc7\u6e21\u6216\u53ef\u9009\u76ee\u6807",
    "only_future_core_targets_or_stash_value": "\u4e3b\u8981\u662f\u672a\u6765\u6838\u5fc3\u6216\u56e4\u8d27\u4ef7\u503c",
    "no_current_build_targets_in_pool": "\u6c60\u5b50\u6ca1\u6709\u5f53\u524d\u9635\u5bb9\u76ee\u6807",
    "gold_does_not_support_estimated_purchase": "\u91d1\u5e01\u4e0d\u652f\u6301\u9884\u4f30\u8d2d\u4e70",
    "theoretical_pool_hit_without_current_actionable_target": "\u53ea\u662f\u7406\u8bba\u6c60\u5b50\u547d\u4e2d\uff0c\u5f53\u524d\u4e0d\u591f\u53ef\u6267\u884c",
    "visible_target_before_refresh": "\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u5df2\u6709\u76ee\u6807\uff0c\u5148\u4e70\u76ee\u6807",
    "refresh_not_available": "\u5f53\u524d\u5546\u5e97\u4e0d\u53ef\u5237\u65b0",
    "refresh_cost_unknown": "\u5237\u65b0\u8d39\u7528\u672a\u77e5",
    "not_enough_gold_after_refresh": "\u5237\u65b0\u540e\u9884\u7b97\u4e0d\u8db3",
    "refresh_leaves_insufficient_purchase_budget": "\u5237\u65b0\u540e\u53ef\u80fd\u4e70\u4e0d\u8d77\u76ee\u6807",
    "no_visible_target_but_shop_pool_is_actionable": "\u5f53\u524d\u65e0\u53ef\u89c1\u76ee\u6807\uff0c\u4f46\u5546\u4eba\u6c60\u8d28\u91cf\u652f\u6301\u5237\u65b0",
    "no_visible_target_and_pool_quality_low": "\u5f53\u524d\u65e0\u53ef\u89c1\u76ee\u6807\uff0c\u4e14\u6c60\u8d28\u91cf\u4e0d\u652f\u6301\u5f3a\u5237",
}
SHOP_TARGET_COUNT_LABELS_ZH = {
    "missing_current_core": "\u7f3a\u5931\u7684\u5f53\u524d\u6838\u5fc3",
    "current_core": "\u5f53\u524d\u6838\u5fc3",
    "current_transition": "\u5f53\u524d\u8fc7\u6e21",
    "current_optional": "\u5f53\u524d\u53ef\u9009",
    "future_core": "\u672a\u6765\u6838\u5fc3",
    "expired_core": "\u8fc7\u671f\u6838\u5fc3",
    "unrelated": "\u65e0\u5173",
}
SHOP_POOL_STAGE_LABELS_ZH = {
    "bronze_only": "\u9752\u94dc\u9636\u6bb5",
    "silver_unlocked": "\u767d\u94f6\u9636\u6bb5",
    "gold_unlocked": "\u9ec4\u91d1\u9636\u6bb5",
    "diamond_unlocked": "\u94bb\u77f3\u9636\u6bb5",
}
SHOP_CACHE_STATUS_LABELS_ZH = {
    "hit": "\u5df2\u547d\u4e2d\u7f13\u5b58",
    "miss": "\u672a\u547d\u4e2d\u7f13\u5b58",
    "unavailable": "\u6682\u65e0\u7f13\u5b58",
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
        return f"\u76ee\u6807\u5bc6\u5ea6\u76f8\u5bf9\u5f53\u5929\u5546\u4eba\u4e3a{SHOP_DENSITY_LABELS_ZH.get(density, density)}"
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
        status_label = "\u91d1\u5e01\u672a\u77e5"
    elif gold_support.get("supports_entry") is False:
        status_label = "\u91d1\u5e01\u4e0d\u8db3"
    elif gold_support.get("price_known") is False:
        status_label = "\u5df2\u8bfb\u53d6\u91d1\u5e01\uff0c\u4f46\u4ef7\u683c\u4f30\u7b97\u4e0d\u8db3"
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
            "\u540d\u79f0": _zh_name(data, card.get("name")),
            "\u8bc4\u7ea7": card.get("tier"),
            "\u5b9a\u4f4d": _role_label(card.get("role")),
            "\u53ef\u5347\u7ea7": card.get("can_upgrade", False),
        }
        for card in sorted(priority_cards, key=sort_key)[:limit]
    ]


def _gold_status(current_gold: Any) -> str:
    if current_gold in (None, ""):
        return "\u672a\u77e5"
    try:
        gold = int(current_gold)
    except (TypeError, ValueError):
        return "\u672a\u77e5"

    if gold <= 5:
        return "\u6781\u4f4e"
    if gold <= 12:
        return "\u504f\u4f4e"
    if gold <= 25:
        return "\u6b63\u5e38"
    return "\u5145\u8db3"


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
    risk = "\u672a\u77e5" if status == "\u672a\u77e5" else "\u65e0"

    if is_shop:
        if status == "\u6781\u4f4e":
            risk = "\u9ad8"
            notes.append("\u5f53\u524d\u91d1\u5e01\u6781\u4f4e\uff0c\u5546\u5e97\u5b58\u5728\u5237\u5230\u76ee\u6807\u7269\u54c1\u4f46\u4e70\u4e0d\u8d77\u7684\u98ce\u9669\u3002")
            notes.append("\u514d\u8d39\u5956\u52b1\u3001\u56fa\u5b9a\u5956\u52b1\u6216\u91d1\u5e01\u4e8b\u4ef6\u7684\u76f8\u5bf9\u7a33\u5b9a\u6027\u66f4\u9ad8\u3002")
        elif status == "\u504f\u4f4e":
            risk = "\u4e2d"
            notes.append("\u5f53\u524d\u91d1\u5e01\u504f\u4f4e\uff0c\u5546\u5e97\u4e8b\u4ef6\u9700\u8981\u8003\u8651\u8d2d\u4e70\u529b\u98ce\u9669\u3002")
            notes.append("\u5c0f\u5361\u6c60\u3001\u9ad8\u547d\u4e2d\u5546\u5e97\u4f18\u5148\u4e8e\u5927\u5361\u6c60\u5546\u5e97\u3002")
        elif status == "\u6b63\u5e38":
            risk = "\u4f4e"
            notes.append("\u5f53\u524d\u91d1\u5e01\u6b63\u5e38\uff0c\u53ef\u4ee5\u6b63\u5e38\u6bd4\u8f83\u5546\u5e97\u5361\u6c60\u8d28\u91cf\u3002")
        elif status == "\u5145\u8db3":
            risk = "\u4f4e"
            notes.append("\u5f53\u524d\u91d1\u5e01\u5145\u8db3\uff0c\u9ad8\u8d28\u91cf\u5546\u5e97\u548c\u8f6c\u578b\u5546\u5e97\u66f4\u5bb9\u6613\u5151\u73b0\u6536\u76ca\u3002")
        else:
            notes.append("\u5f53\u524d\u91d1\u5e01\u672a\u77e5\uff0c\u65e0\u6cd5\u5224\u65ad\u5546\u5e97\u5956\u52b1\u662f\u5426\u4e70\u5f97\u8d77\u3002")
    else:
        if status == "\u672a\u77e5":
            notes.append("\u5f53\u524d\u91d1\u5e01\u672a\u77e5\uff0c\u4f46\u8be5\u4e8b\u4ef6\u4e0d\u662f\u7eaf\u5546\u5e97\u4e8b\u4ef6\uff0c\u8d2d\u4e70\u529b\u9650\u5236\u8f83\u5f31\u3002")
        else:
            notes.append("\u8be5\u4e8b\u4ef6\u4e0d\u662f\u7eaf\u5546\u5e97\u4e8b\u4ef6\uff0c\u5f53\u524d\u91d1\u5e01\u4e0d\u4f1a\u660e\u663e\u9650\u5236\u5956\u52b1\u83b7\u53d6\u3002")

    if gained_gold > 0:
        if status in {"\u6781\u4f4e", "\u504f\u4f4e"}:
            notes.append(f"\u8be5\u4e8b\u4ef6\u63d0\u4f9b {gained_gold} \u91d1\u5e01\uff0c\u5f53\u524d\u91d1\u5e01\u504f\u4f4e\u65f6\u4ef7\u503c\u63d0\u9ad8\u3002")
        elif status in {"\u6b63\u5e38", "\u5145\u8db3"}:
            notes.append(f"\u8be5\u4e8b\u4ef6\u63d0\u4f9b {gained_gold} \u91d1\u5e01\uff0c\u4f46\u5f53\u524d\u91d1\u5e01\u4e0d\u4f4e\uff0c\u8fb9\u9645\u4ef7\u503c\u76f8\u5bf9\u4e0b\u964d\u3002")
        else:
            notes.append(f"\u8be5\u4e8b\u4ef6\u63d0\u4f9b {gained_gold} \u91d1\u5e01\u3002")

    return {
        "\u5f53\u524d\u91d1\u5e01": current_gold,
        "\u91d1\u5e01\u72b6\u6001": status,
        "\u8d2d\u4e70\u529b\u98ce\u9669": risk,
        "\u662f\u5426\u5546\u5e97\u4e8b\u4ef6": is_shop,
        "\u8bf4\u660e": notes[:3],
    }


def _relation_label(value: Any) -> str:
    if value == "current_build":
        return "\u5f53\u524d\u9636\u6bb5"
    if value == "future_build":
        return "\u540e\u7eed\u65b9\u5411"
    if value == "late_build":
        return "\u540e\u671f\u65b9\u5411"
    if value == "past_build":
        return "\u5df2\u8fc7\u671f"
    return str(value or "\u672a\u77e5")


def _match_band_label(value: Any) -> str:
    if value == "locked":
        return "\u5df2\u6210\u578b"
    if value == "close":
        return "\u63a5\u8fd1\u6210\u578b"
    if value == "developing":
        return "\u53d1\u5c55\u4e2d"
    if value == "seed":
        return "\u6709\u82d7\u5934"
    if value == "none":
        return "\u672a\u6210\u578b"
    return str(value or "\u672a\u77e5")


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
                "\u9635\u5bb9": item.get("name") or item.get("build_id"),
                "\u9002\u7528\u9636\u6bb5": STAGE_LABELS_ZH.get(item.get("phase"), item.get("phase")),
                "\u9636\u6bb5\u5173\u7cfb": _relation_label(item.get("relation")),
                "\u6210\u578b\u72b6\u6001": _match_band_label(item.get("match_band")),
                "\u5df2\u62e5\u6709\u6838\u5fc3\u5361\u6570\u91cf": len(owned_core),
                "\u5df2\u62e5\u6709\u6838\u5fc3\u5361": owned_core,
                "\u7f3a\u5931\u6838\u5fc3\u5361": missing_core[:6],
                "\u5df2\u62e5\u6709\u53ef\u9009\u5361\u6570\u91cf": len(owned_optional),
                "\u5df2\u62e5\u6709\u53ef\u9009\u5361": owned_optional[:6],
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
        item: dict[str, Any] = {"\u540d\u79f0": name}
        for source, target in (
            ("rarity", "\u7a00\u6709\u5ea6"),
            ("tier", "\u8bc4\u7ea7"),
            ("price", "\u4ef7\u683c"),
            ("card_type", "\u7c7b\u578b"),
            ("type", "\u7c7b\u578b"),
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
            "\u540d\u79f0": _zh_name(data, name),
            "\u7a00\u6709\u5ea6": rarity,
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
        "\u5546\u4eba": current_shop.get("merchant_display_name")
        or current_shop.get("merchant_name"),
        "\u53ef\u5237\u65b0": current_shop.get("refresh_available"),
        "\u5237\u65b0\u8d39\u7528": current_shop.get("refresh_cost"),
        "\u672c\u5e97\u5269\u4f59\u5237\u65b0\u6b21\u6570": current_shop.get("refreshes_remaining"),
        "\u5237\u65b0\u6b21\u6570\u8bed\u4e49": "\u4ec5\u9650\u5f53\u524d\u8fd9\u5bb6\u5546\u5e97\uff0c\u4e0d\u4f1a\u8de8\u5546\u5e97\u7d2f\u8ba1",
        "\u9884\u4f30\u5e73\u5747\u7269\u54c1\u4ef7\u683c": current_shop.get("estimated_avg_item_price"),
        "\u5f53\u524d\u53ef\u89c1\u7269\u54c1": _compact_card_entries(
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
        "\u91d1\u5e01": current_gold if current_gold is not None else context.get("gold"),
        "\u751f\u547d": context.get("combat_health", context.get("health")),
        "\u58f0\u671b": context.get("prestige"),
        "\u6700\u5927\u58f0\u671b": context.get("max_prestige"),
        "\u6536\u5165": context.get("income"),
        "\u7b49\u7ea7": context.get("level"),
        "\u7ecf\u9a8c": context.get("xp"),
        "\u80cc\u5305\u5360\u7528": context.get("inventory_slots_used"),
        "\u80cc\u5305\u4e0a\u9650": context.get("inventory_slots_total"),
    }

    return {
        "\u8d44\u6e90": {
            key: value
            for key, value in resource_fields.items()
            if value not in (None, "")
        },
        "\u5df2\u6709\u5361\u724c": _compact_owned_cards(data, owned_cards, context),
        "\u5df2\u6709\u7269\u54c1": _compact_card_entries(
            data,
            context.get("owned_items_display"),
            limit=16,
        ),
        "\u5df2\u6709\u6280\u80fd": _compact_card_entries(
            data,
            context.get("skills_display"),
            limit=16,
        ),
        "\u5f53\u524d\u5546\u5e97": _compact_shop_context(data, current_shop),
    }


def _compact_pool_stats(pool_stats: Any) -> dict[str, Any]:
    if not isinstance(pool_stats, dict):
        return {}

    mapping = (
        ("draw_count", "\u5c55\u793a\u6570\u91cf"),
        ("selection_count", "\u53ef\u9009\u6570\u91cf"),
        ("total_pool_count", "\u5361\u6c60\u603b\u6570"),
        ("valuable_count", "\u9635\u5bb9\u76f8\u5173\u6570\u91cf"),
        ("valuable_ratio", "\u9635\u5bb9\u76f8\u5173\u5360\u6bd4"),
        ("core_ratio", "\u6838\u5fc3\u5360\u6bd4"),
        ("expected_valuable_in_shop", "\u9884\u671f\u9635\u5bb9\u76f8\u5173\u6570"),
        ("expected_core_in_shop", "\u9884\u671f\u6838\u5fc3\u6570"),
        ("prob_valuable_in_shop", "\u547d\u4e2d\u9635\u5bb9\u76f8\u5173\u6982\u7387"),
        ("prob_core_in_shop", "\u547d\u4e2d\u6838\u5fc3\u6982\u7387"),
        ("expected_sell_gold", "\u9884\u671f\u51fa\u552e\u91d1\u5e01"),
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
                        "refresh_scope_label": "\u4ec5\u9650\u5f53\u524d\u8fd9\u5bb6\u5546\u5e97\uff0c\u4e0d\u4f1a\u8de8\u5546\u5e97\u7d2f\u8ba1",
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
                "\u540d\u79f0": _zh_name(data, option.get("event_name") or option.get("name")),
                "\u4e8b\u4ef6\u7c7b\u578b": option.get("event_type"),
                "\u8d44\u6e90\u6536\u76ca": option.get("resource_rewards") or {},
                "\u5361\u6c60\u6458\u8981": _compact_pool_stats(option.get("pool_stats")),
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
            "must_consider": "\u8fd9\u662f\u7236\u4e8b\u4ef6\uff0c\u5fc5\u987b\u540c\u65f6\u8bc4\u4f30\u5b83\u7684\u540e\u7eed\u9009\u9879\uff1b\u4e0d\u8981\u56e0\u4e3a\u7236\u4e8b\u4ef6\u672c\u8eab\u6ca1\u6709\u76f4\u63a5\u6536\u76ca\u5c31\u5f53\u6210\u4f4e\u4ef7\u503c\u3002",
            "event_rule_status": result.get("event_rule_status"),
            "event_rule_status_label": "\u7236\u4e8b\u4ef6" if result.get("event_rule_status") == "parent_event" else result.get("event_rule_status"),
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
            "\u4e8b\u4ef6": _zh_name(data, event_name) or event_name,
            "\u4e8b\u4ef6\u539f\u540d": event_name,
            "\u4e8b\u4ef6\u7c7b\u578b": result.get("event_type"),
            "\u8bf4\u660e": _zh_text(data, result.get("notes")),
            "\u8d44\u6e90\u6536\u76ca": result.get("resource_rewards") or {},
            "\u7a00\u6709\u5ea6\u8fc7\u6ee4": result.get("resolved_rarity_filter") or {},
            "\u89d2\u8272\u547d\u4e2d\u6570\u91cf": result.get("role_counts") or {},
            "\u9ad8\u8bc4\u7ea7\u6570\u91cf": result.get("high_tier_count") or 0,
            "\u53ef\u5347\u7ea7\u5df2\u6709\u76ee\u6807": [
                _zh_name(data, name)
                for name in result.get("upgrade_hits", [])[:6]
            ],
            "\u5df2\u6709\u76ee\u6807\u547d\u4e2d": _priority_cards(
                data,
                result.get("owned_target_hits", []),
                limit=6,
            ),
            "\u4f18\u5148\u76f8\u5173\u5361\u6458\u8981": _priority_cards(
                data,
                result.get("possible_cards", []),
                limit=8,
            ),
            "\u5361\u6c60\u6458\u8981": _compact_pool_stats(result.get("pool_stats")),
            "\u540e\u7eed\u9009\u9879": _compact_followups(data, result),
            "\u6700\u4f73\u540e\u7eed\u9009\u9879": _zh_name(data, result.get("best_followup"))
            if result.get("best_followup")
            else None,
            "\u8f6c\u578b\u6838\u5fc3\u547d\u4e2d\u6570\u91cf": result.get("alt_core_card_count") or 0,
        }
        parent_followup_context = _compact_parent_followup_context(data, result)
        if parent_followup_context:
            compact_item["parent_followup_options"] = parent_followup_context
        shop_pool_summary = _compact_shop_pool_summary(data, result.get("shop_pool_summary"))
        if shop_pool_summary:
            compact_item["shop_pool_summary"] = shop_pool_summary

        if isinstance(shop_decision, dict):
            compact_item["\u5546\u5e97\u5237\u65b0\u4e8b\u5b9e\u4e0e\u65e7\u89c4\u5219\u53c2\u8003"] = {
                "\u53ef\u5237\u65b0": shop_decision.get("refresh_available"),
                "\u5237\u65b0\u8d39\u7528": shop_decision.get("refresh_cost"),
                "\u53ef\u89c1\u7269\u54c1\u4ef7\u683c\u6765\u6e90": shop_decision.get("purchase_budget_price_source"),
                "\u8d2d\u4e70\u9884\u7b97\u4ef7\u683c": shop_decision.get("purchase_budget_price"),
                "\u5237\u65b0\u540e\u4ecd\u6709\u8d2d\u4e70\u529b": shop_decision.get("gold_sufficient_for_refresh"),
                "\u5237\u65b0\u5361\u6c60\u76f8\u5173\u5360\u6bd4": shop_decision.get("refresh_pool_valuable_ratio"),
                "\u65e7\u89c4\u5219\u52a8\u4f5c\u53c2\u8003": shop_decision.get("action"),
                "\u65e7\u89c4\u5219\u539f\u56e0\u53c2\u8003": _zh_text(data, shop_decision.get("reason")),
            }
        compact_item["\u65e7\u89c4\u5219\u6863\u4f4d\u53c2\u8003"] = RECOMMENDATION_LABELS_ZH.get(
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
                    "\u9635\u5bb9": hit.get("build_display_name")
                    or hit.get("build_name")
                    or hit.get("build_id"),
                    "\u9636\u6bb5": hit.get("build_phase_label") or hit.get("build_phase"),
                    "鍏崇郴": hit.get("relation_label")
                    or _relation_label(hit.get("relation")),
                    "\u5b9a\u4f4d": hit.get("role_label") or _role_label(hit.get("role")),
                }
            )
        compact.append(
            _prune_empty(
                {
                    "\u7269\u54c1": candidate.get("card_display_name")
                    or _zh_name(data, candidate.get("card_name")),
                    "\u4ef7\u683c": candidate.get("price"),
                    "\u662f\u5426\u4e70\u5f97\u8d77": candidate.get("affordable"),
                    "\u9635\u5bb9\u547d\u4e2d": hits,
                    "\u4e8b\u5b9e\u539f\u56e0": candidate.get("reasons", [])[:4],
                    "椋庨櫓": candidate.get("risks", [])[:4],
                    "\u9700\u8981AI\u5224\u65ad": candidate.get("needs_ai_judgement"),
                    "\u65e7\u89c4\u5219\u53c2\u8003": {
                        "\u91cd\u8981\u5ea6": candidate.get("importance_label")
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
                    "\u9635\u5bb9": bundle.get("build_name") or bundle.get("build_id"),
                    "\u53ef\u89c1\u6838\u5fc3": bundle.get("candidate_core_cards_display")
                    or bundle.get("candidate_core_cards")
                    or [],
                    "\u8d2d\u4e70\u524d\u5df2\u6709\u6838\u5fc3": bundle.get("owned_core_before_display")
                    or bundle.get("owned_core_before")
                    or [],
                    "\u8d2d\u4e70\u540e\u6838\u5fc3": bundle.get("owned_core_after_if_bought_display")
                    or bundle.get("owned_core_after_if_bought")
                    or [],
                    "\u662f\u5426\u4e70\u5f97\u8d77": bundle.get("affordable"),
                    "\u65e7\u89c4\u5219\u53c2\u8003": {
                        "\u91cd\u8981\u5ea6": bundle.get("importance_label")
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
    build_data = data["builds"][build_name]
    current_stage = get_game_stage_for_day(current_day)
    compact_builds = _compact_build_matches(build_analysis)
    current_build_display = (
        build_data.get("name")
        or build_data.get("display_name")
        or build_name
    )

    payload = {
        "\u5206\u6790\u4efb\u52a1": "\u57fa\u4e8e\u5f53\u524d\u5b9e\u65f6\u72b6\u6001\u3001\u89c4\u5219\u5c42\u5ba2\u89c2\u4fe1\u606f\u548c\u76f8\u5173\u653b\u7565\uff0c\u5206\u6790\u5f53\u524d\u4e8b\u4ef6/\u7269\u54c1/\u9635\u5bb9\u65b9\u5411\u3002",
        "\u82f1\u96c4": hero,
        "\u5929\u6570": current_day,
        "\u5f53\u524d\u9636\u6bb5": STAGE_LABELS_ZH.get(current_stage, current_stage),
        "\u5f53\u524d\u9635\u5bb9": current_build_display,
        "\u5f53\u524d\u9635\u5bb9\u9002\u7528\u65f6\u673a": format_build_timing_summary(build_data, current_day),
        "\u5f53\u524d\u72b6\u6001": _compact_state_context(
            data=data,
            owned_cards=owned_cards,
            current_gold=current_gold,
            current_shop=current_shop,
            state_context=state_context,
        ),
        "\u5019\u9009\u9635\u5bb9": compact_builds,
        "\u4e8b\u4ef6\u5ba2\u89c2\u4fe1\u606f": _compact_event_results(data, results),
        "\u5019\u9009\u7269\u54c1\u4e0e\u9635\u5bb9\u5173\u7cfb": _compact_candidate_cards(data, build_analysis),
        "\u53ef\u89c1\u6838\u5fc3\u7ec4\u5408": _compact_visible_core_bundles(build_analysis),
        "\u8d44\u6e90\u538b\u529b\u6307\u6807": (
            build_analysis.get("operation_urgency", {})
            if isinstance(build_analysis, dict)
            else {}
        ),
        "\u5546\u5e97\u65e7\u89c4\u5219\u53c2\u8003": (
            {
                "\u52a8\u4f5c": build_analysis.get("shop_action_label")
                or build_analysis.get("shop_action"),
                "\u539f\u56e0": build_analysis.get("refresh_reason"),
            }
            if isinstance(build_analysis, dict)
            else {}
        ),
    }

    if guide_context:
        payload["\u76f8\u5173\u653b\u7565"] = guide_context

    shop_phase_analysis = _compact_shop_phase_analyses(data, results)
    if shop_phase_analysis:
        payload["shop_phase_analysis"] = shop_phase_analysis

    payload["AI_priority_rules"] = [
        "\u5982\u679c\u4e8b\u4ef6\u6709 parent_followup_options\uff0c\u8981\u628a\u7236\u4e8b\u4ef6\u548c\u540e\u7eed\u9009\u9879\u7684\u6536\u76ca\u4e00\u8d77\u5224\u65ad\u3002",
        "\u653b\u7565\u5185\u5bb9\u662f\u7b56\u7565\u8bc1\u636e\uff0c\u4e0d\u662f\u4f4e\u4f18\u5148\u7ea7\u9644\u5f55\uff1b\u9700\u8981\u8bf4\u660e\u5b83\u662f\u5426\u5951\u5408\u5f53\u524d\u5c40\u9762\u3002",
        "\u9700\u8981\u7efc\u5408\u89c4\u5219\u5c42\u3001\u5f53\u524d\u72b6\u6001\u548c\u653b\u7565\u7ecf\u9a8c\uff0c\u4e0d\u8981\u53ea\u770b\u9635\u5bb9\u5339\u914d\u6216\u5f53\u524d\u724c\u9762\u3002",
        "\u5546\u5e97\u5fc5\u987b\u533a\u5206\u9636\u6bb5\uff1a\u8fdb\u5e97\u524d\u53ea\u5224\u65ad\u662f\u5426\u503c\u5f97\u82b1\u4e09\u9009\u4e00\u673a\u4f1a\u8fdb\u5165\uff0c\u4e0d\u8ba8\u8bba\u53ef\u89c1\u5546\u54c1\uff1b\u8fdb\u5e97\u540e\u518d\u5224\u65ad\u4e70\u4ec0\u4e48\u548c\u662f\u5426\u5237\u65b0\u3002",
        "\u4e0d\u8981\u91cd\u65b0\u8ba1\u7b97\u5546\u5e97\u6c60\u6982\u7387\uff1b\u628a rule_status \u5f53\u4f5c\u89c4\u5219\u5c42\u7c97\u7b5b\u7684\u53c2\u8003\u503e\u5411\u3002",
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
        for key in ("\u5546\u5e97\u5237\u65b0\u4e8b\u5b9e\u4e0e\u65e7\u89c4\u5219\u53c2\u8003",):
            if key in trimmed:
                trimmed.pop(key, None)
                removed.append(f"\u4e8b\u4ef6\u5ba2\u89c2\u4fe1\u606f[].{key}")
    if "parent_followup_options" in trimmed:
        followup = dict(trimmed["parent_followup_options"])
        if "must_consider" in followup:
            followup.pop("must_consider", None)
            removed.append("\u4e8b\u4ef6\u5ba2\u89c2\u4fe1\u606f[].parent_followup_options.must_consider")
        trimmed["parent_followup_options"] = followup
    if "\u65e7\u89c4\u5219\u6863\u4f4d\u53c2\u8003" in trimmed:
        trimmed.pop("\u65e7\u89c4\u5219\u6863\u4f4d\u53c2\u8003", None)
        removed.append("\u4e8b\u4ef6\u5ba2\u89c2\u4fe1\u606f[].\u65e7\u89c4\u5219\u6863\u4f4d\u53c2\u8003")
    return _prune_empty(trimmed)


def prepare_ai_payload_for_model(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    model_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    removed: list[str] = []
    original_field_count = _count_fields(model_payload)
    original_summary_chars = _json_char_count(model_payload)
    scene = _detect_analysis_scene(model_payload)

    _remove_path(model_payload, removed, "AI_priority_rules")
    if "strategy_guide_context" in model_payload and "\u76f8\u5173\u653b\u7565" in model_payload:
        _remove_path(model_payload, removed, "\u76f8\u5173\u653b\u7565")
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

    events = model_payload.get("\u4e8b\u4ef6\u5ba2\u89c2\u4fe1\u606f")
    if scene == "inside_shop":
        _remove_path(model_payload, removed, "\u4e8b\u4ef6\u5ba2\u89c2\u4fe1\u606f")
    elif isinstance(events, list):
        model_payload["\u4e8b\u4ef6\u5ba2\u89c2\u4fe1\u606f"] = [
            _trim_event_for_ai(event, scene, removed) for event in events
        ]

    if scene == "before_entering_shop":
        _remove_path(model_payload, removed, "\u5f53\u524d\u72b6\u6001", "\u5f53\u524d\u5546\u5e97")
        _remove_path(model_payload, removed, "\u5019\u9009\u7269\u54c1\u4e0e\u9635\u5bb9\u5173\u7cfb")
        _remove_path(model_payload, removed, "\u53ef\u89c1\u6838\u5fc3\u7ec4\u5408")
        _remove_path(model_payload, removed, "\u5546\u5e97\u65e7\u89c4\u5219\u53c2\u8003")
    elif scene == "event_selection":
        _remove_path(model_payload, removed, "\u5f53\u524d\u72b6\u6001", "\u5f53\u524d\u5546\u5e97")
        _remove_path(model_payload, removed, "\u5546\u5e97\u65e7\u89c4\u5219\u53c2\u8003")
    elif scene == "inside_shop":
        _remove_path(model_payload, removed, "\u5546\u5e97\u65e7\u89c4\u5219\u53c2\u8003")

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
        "\u4e0b\u9762\u63d0\u4f9b\u5f53\u524d\u5c40\u7684\u5b9e\u65f6\u72b6\u6001\u3001\u89c4\u5219\u5c42\u8ba1\u7b97\u7ed3\u679c\u3001\u5546\u5e97\u9636\u6bb5\u5206\u6790\u548c\u76f8\u5173\u653b\u7565\u3002\n"
        "\u8bf7\u6bd4\u8f83\u6240\u6709\u5f53\u524d\u9009\u9879\uff0c\u7ed3\u5408\u5373\u65f6\u6536\u76ca\u3001\u5f53\u524d\u9700\u6c42\u3001\u9635\u5bb9\u9002\u914d\u3001\u540e\u7eed\u4ef7\u503c\u548c\u673a\u4f1a\u6210\u672c\uff0c\u7ed9\u51fa\u7b80\u6d01\u3001\u6709\u4f9d\u636e\u7684\u5c40\u52bf\u5206\u6790\u3002\n"
        "\u5f53\u524d\u6570\u636e\uff1a\n"
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
    """\u6e05\u7406\u667a\u80fd\u5206\u6790\u8f93\u51fa\u4e2d\u7684 Markdown \u7b26\u53f7\uff0c\u907f\u514d\u524d\u7aef\u76f4\u63a5\u663e\u793a **\u3001\u7f29\u8fdb\u5217\u8868\u7b49\u3002"""
    if not text:
        return ""

    text = text.replace("\r\n", "\n")

    # 鍘绘帀甯歌 Markdown 寮鸿皟绗﹀彿
    text = text.replace("**", "")
    text = text.replace("__", "")

    # \u53bb\u6389\u6807\u9898\u7b26\u53f7
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)

    # 鍘绘帀琛岄椤圭洰绗﹀彿
    text = re.sub(r"(?m)^\s*[\*\-•]\s*", "", text)

    # 鍘嬪钩杩囨繁缂╄繘
    text = re.sub(r"(?m)^\s{2,}", "", text)

    # 鍘嬬缉绌鸿
    text = re.sub(r"\n{3,}", "\n\n", text)

    replacements = [
        ("Current recommendation", "\u5f53\u524d\u63a8\u8350"),
        ("Current base", "\u5f53\u524d\u57fa\u7840"),
        ("Main issue", "\u4e3b\u8981\u95ee\u9898"),
        ("Next step", "\u4e0b\u4e00\u6b65\u5efa\u8bae"),
        ("High Value", "\u4f18\u5148\u9009\u62e9"),
        ("Medium Value", "\u53ef\u4ee5\u8003\u8651"),
        ("Low Value", "\u4f18\u5148\u7ea7\u4f4e"),
        ("current_build", "\u5f53\u524d\u9636\u6bb5"),
        ("future_build", "\u540e\u7eed\u65b9\u5411"),
        ("late_build", "\u540e\u671f\u65b9\u5411"),
        ("past_build", "\u5df2\u8fc7\u671f"),
        ("Recommendation", "鎺ㄨ崘"),
        ("Reasons", "鍘熷洜"),
        ("Reason", "鍘熷洜"),
        ("Transition", "杩囨浮"),
        ("transition", "杩囨浮"),
        ("Optional", "\u53ef\u9009"),
        ("optional", "\u53ef\u9009"),
        ("Build", "\u9635\u5bb9"),
        ("build", "\u9635\u5bb9"),
        ("Core", "\u6838\u5fc3"),
        ("core", "\u6838\u5fc3"),
        ("tier", "\u8bc4\u7ea7"),
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
            "\u6ca1\u6709\u627e\u5230 DeepSeek API Key\u3002\u8bf7\u5728\u542f\u52a8 UI \u524d\u8bbe\u7f6e DEEPSEEK_API_KEY\uff0c"
            f"\u6216\u628a key \u653e\u5230 {DEFAULT_API_KEY_FILE}\u3002"
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
