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
    priority_roles = {"core", "optional"}
    priority_cards = [card for card in cards if card.get("role") in priority_roles]

    def sort_key(card: dict[str, Any]) -> tuple[int, str]:
        role_rank = {"core": 0, "optional": 1}
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
    for item in raw_matches[:5]:
        if not isinstance(item, dict):
            continue
        owned_core = [str(name) for name in item.get("owned_core", []) if name]
        owned_optional = [str(name) for name in item.get("owned_optional", []) if name]
        missing_core = [str(name) for name in item.get("missing_core", []) if name]
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
        "AI任务": "根据当前天数和候选阵容命中情况进行简短阵容分析",
        "英雄": hero,
        "天数": current_day,
        "当前阶段": STAGE_LABELS_ZH.get(current_stage, current_stage),
        "当前阵容": current_build_display,
        "当前阵容适用时机": format_build_timing_summary(build_data, current_day),
        "候选阵容": compact_builds,
        "判断规则": [
            "当前天数决定优先关注当前阶段阵容。",
            "核心卡权重高于可选卡。",
            "可选卡很多但核心卡很少，只能说明有关联，不能认为稳定成型。",
            "阶段不匹配的阵容可以作为后续方向，但当前不一定适合过早投入。",
        ],
    }

    return payload


def build_ai_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    summary_json = json.dumps(payload, ensure_ascii=False, indent=2)

    return [
        {
            "role": "system",
            "content": (
                "你是《The Bazaar》的阵容判断助手。只能根据结构化数据解释当前更适合关注哪个阵容。\n"
                "禁止编造未提供的卡牌、阵容、概率或游戏机制。\n"
                "核心卡权重必须高于可选卡。\n"
                "如果某个阵容可选卡很多但核心卡很少，要说明它有关联但还不能认为稳定成型。\n"
                "如果某个阵容阶段和当前天数不匹配，要说明它可能是后续方向，当前不一定适合过早投入。\n"
                "不要使用 Markdown、表格、代码块或多层列表。\n"
                "输出必须简短，并固定为五行：\n"
                "当前推荐：\n"
                "推荐原因：\n"
                "当前基础：\n"
                "主要问题：\n"
                "下一步建议：\n"
            ),
        },
        {
            "role": "user",
            "content": (
                "下面是规则系统计算后的阵容候选数据，只能基于这些数据判断：\n"
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
