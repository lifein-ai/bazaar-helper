from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from build_strategy import format_build_timing_summary, get_game_stage_for_day
from recommender import format_resource_rewards
from app_paths import get_app_root


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_API_KEY_FILE = get_app_root() / "runtime" / "deepseek_api_key.txt"
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
    priority_roles = {"core", "transition", "optional"}
    priority_cards = [card for card in cards if card.get("role") in priority_roles]

    def sort_key(card: dict[str, Any]) -> tuple[int, str]:
        role_rank = {"core": 0, "transition": 1, "optional": 2}
        return role_rank.get(card.get("role", ""), 9), card.get("name", "")

    return [
        {
            "名称": _zh_name(data, card.get("name")),
            "tier": card.get("tier"),
            "定位": ROLE_LABELS_ZH.get(card.get("role"), card.get("role")),
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


def compact_recommendations(
    *,
    data: dict[str, Any],
    hero: str,
    build_name: str,
    current_day: int,
    owned_cards: dict[str, str],
    results: list[dict[str, Any]],
    current_gold: int | None = None,
) -> dict[str, Any]:
    build_data = data["builds"][build_name]

    payload = {
        "英雄": hero,
        "阵容": build_name,
        "天数": current_day,
        "当前金币": current_gold,
        "金币状态": _gold_status(current_gold),
        "阶段": STAGE_LABELS_ZH.get(get_game_stage_for_day(current_day), get_game_stage_for_day(current_day)),
        "阵容时机": format_build_timing_summary(build_data, current_day),
        "阵容摘要": build_data.get("build_summary", ""),
        "实战Tips": build_data.get("pilot_tips", []),
        "已拥有卡牌": owned_cards,
        "后续选择规则": "如果某个事件有后续选项，表示选择父事件后只能从后续子事件中选择一个；不能把多个子事件收益相加，也不能把子事件收益当作父事件直接收益。",
        "选项": [],
    }

    for result in results:
        event_name = result.get("event_name")
        event_data = data.get("events", {}).get(event_name, {})
        if not isinstance(event_data, dict):
            event_data = {}

        resource_rewards = result.get("resource_rewards", {})
        if not isinstance(resource_rewards, dict):
            resource_rewards = {}

        pool_stats = result.get("pool_stats", {})
        if not isinstance(pool_stats, dict):
            pool_stats = {}

        followup_summary = result.get("followup_value_summary") or {}
        if not isinstance(followup_summary, dict):
            followup_summary = {}

        followup_stats = followup_summary.get("pool_stats") or {}
        if not isinstance(followup_stats, dict):
            followup_stats = {}

        followup_resource_rewards = followup_summary.get("resource_rewards") or {}
        if not isinstance(followup_resource_rewards, dict):
            followup_resource_rewards = {}

        best_followup_name = (
            result.get("best_followup")
            or followup_summary.get("best_followup")
        )

        payload["选项"].append(
            {
                "事件名": _zh_name(data, event_name),
                "推荐等级": RECOMMENDATION_LABELS_ZH.get(
                    result.get("recommendation"),
                    result.get("recommendation"),
                ),
                "事件类型": event_data.get("event_type") or event_data.get("event_category") or "",
                "购买力判断": _affordability_summary(
                    current_gold=current_gold,
                    event_data=event_data,
                    resource_rewards=resource_rewards,
                ),
                "说明": "",
                "原因": [
                    _zh_text(data, reason)
                    for reason in result.get("reasons", [])[:3]
                ],
                "关键卡": _priority_cards(data, result.get("possible_cards", [])),
                "其他阵容核心卡数量": int(result.get("alt_core_card_count") or 0),
                "其他阵容核心命中": result.get("alt_core_build_hits", []),
                "已拥有命中": [
                    {
                        "名称": _zh_name(data, card.get("name")),
                        "评级": card.get("tier"),
                        "定位": ROLE_LABELS_ZH.get(card.get("role"), card.get("role")),
                        "可升级": card.get("can_upgrade", False),
                        "附魔": card.get("enchantments", []),
                    }
                    for card in result.get("owned_target_hits", [])[:5]
                ],
                "父事件直接资源收益": format_resource_rewards(resource_rewards),
                "最佳后续": _zh_name(data, best_followup_name),
                "最佳后续收益": {
                    "推荐等级": RECOMMENDATION_LABELS_ZH.get(
                        followup_summary.get("followup_recommendation_level"),
                        followup_summary.get("followup_recommendation_level"),
                    ),
                    "资源收益": format_resource_rewards(followup_resource_rewards),
                    "候选卡数量": int(followup_stats.get("total_pool_count") or 0),
                    "构筑相关卡数量": int(followup_stats.get("valuable_count") or 0),
                    "预期命中数量": _round_ratio(
                        followup_stats.get("expected_valuable_in_shop") or 0.0
                    ),
                    "命中相关卡概率": _round_ratio(
                        followup_stats.get("prob_valuable_in_shop") or 0.0
                    ),
                    "命中核心卡概率": _round_ratio(
                        followup_stats.get("prob_core_in_shop") or 0.0
                    ),
                    "预期卖价金币": _round_ratio(
                        followup_stats.get("expected_sell_gold") or 0.0
                    ),
                },
                "后续选项": [
                    {
                        "名称": _zh_name(data, option.get("name")),
                        "推荐等级": RECOMMENDATION_LABELS_ZH.get(
                            option.get("recommendation"),
                            option.get("recommendation"),
                        ),
                        "说明": "",
                        "资源收益": format_resource_rewards(
                            option.get("resource_rewards", {})
                        ),
                        "预期卖价金币": _round_ratio(
                            option.get("expected_sell_gold", 0.0)
                        ),
                        "关键卡": [
                            {
                                **card,
                                "name": _zh_name(data, card.get("name")),
                                "role": ROLE_LABELS_ZH.get(card.get("role"), card.get("role")),
                            }
                            for card in option.get("priority_cards", [])[:3]
                        ],
                    }
                    for option in result.get("followup_options", [])[:6]
                ],
                "父事件直接统计": {
                    "候选卡数量": int(pool_stats.get("total_pool_count") or 0),
                    "构筑相关卡数量": int(pool_stats.get("valuable_count") or 0),
                    "预期命中数量": _round_ratio(
                        pool_stats.get("expected_valuable_in_shop") or 0.0
                    ),
                    "命中相关卡概率": _round_ratio(
                        pool_stats.get("prob_valuable_in_shop") or 0.0
                    ),
                    "命中核心卡概率": _round_ratio(
                        pool_stats.get("prob_core_in_shop") or 0.0
                    ),
                    "预期卖价金币": _round_ratio(
                        pool_stats.get("expected_sell_gold") or 0.0
                    ),
                },
            }
        )

    return payload


def build_ai_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    summary_json = json.dumps(payload, ensure_ascii=False, indent=2)

    option_names = [
        str(option.get("事件名"))
        for option in payload.get("选项", [])
        if option.get("事件名")
    ]
    option_name_text = "、".join(option_names) if option_names else "无"

    return [
        {
            "role": "system",
            "content": (
                "你是《The Bazaar》的事件选择建议助手。规则系统已经完成事实计算，你只负责解释结构化结果。\n"
                "禁止编造卡牌、事件、概率、机制或候选之外的操作。\n"
                "必须从候选事件名中选一个，不能跳过、放弃或建议等下一回合。\n"
                "必须严格遵守推荐等级：优先选择 > 可以考虑 > 优先级低。有更高等级候选时，不要推荐低等级候选。\n"
                "可以使用当前金币和购买力判断解释商店风险：金币低时，商店有刷到但买不起的风险；免费奖励、固定奖励、金币事件相对更稳。\n"
                "父子事件只能按最佳后续解释，不能把多个子事件收益相加，也不能把子事件收益当作父事件直接收益。\n"
                "不要假设未提供的信息，包括血量压力、棋盘强度、已有格子、敌人强度。\n"
                "输出中文纯文本，不使用 Markdown、表格、代码块或多层列表，控制在 180 到 320 字。\n"
                "格式固定为三段：\n"
                "推荐：候选事件名之一\n"
                "核心判断：结合推荐等级、卡池/资源/技能/后续收益和金币购买力说明主要价值\n"
                "对比理由：说明为什么比其他候选更好，并简要说明不确定项\n"
            ),
        },
        {
            "role": "user",
            "content": (
                f"本轮候选事件名只能从这里选择：{option_name_text}\n"
                "下面是规则系统计算后的结构化事件候选数据，只能基于这些数据判断：\n"
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
    """清理 AI 输出中的 Markdown 符号，避免前端直接显示 **、缩进列表等。"""
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
            "或把 key 放到 runtime/deepseek_api_key.txt。"
        )

    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        },
        ensure_ascii=False,
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
