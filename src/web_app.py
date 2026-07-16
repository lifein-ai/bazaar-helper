from __future__ import annotations

import argparse
import hashlib
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen
import re

from recommender import (
    estimated_avg_shop_item_price,
    get_card_role_for_build,
    infer_possible_cards_for_event,
)
from shop_pool_cache import get_cached_shop_pool_summary, hydrate_cached_shop_cards
from advisor import analyze_game_state
from ai_advisor import analyze_with_ai, compact_recommendations
from build_strategy import applicable_build_names, build_applies_to_day, get_game_stage_for_day
from combat_simulator import estimate_self_health_ttk
from data_loader import load_all_data
from game_state import GameState
from guide_retriever import guide_cache_marker, retrieve_guides_for_ai
from app_paths import get_app_root, get_runtime_dir
from stage_build_matcher import analyze_stage_builds
from shop_state import merge_effective_shop
from update_manager import (
    UpdateError,
    dismiss_update_prompt,
    find_update_package_candidates,
    get_update_status,
    launch_update_install,
    open_download_page,
    select_update_package_with_dialog,
    start_background_update_check,
    expected_update_info,
)


class BazaarHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can allow multiple helper processes to bind 8765,
    # which makes localhost requests land on stale instances unpredictably.
    allow_reuse_address = False


BASE_DIR = get_app_root()
DATA_DIR = BASE_DIR / "data"
RUNTIME_DIR = get_runtime_dir()
STATE_PATH = RUNTIME_DIR / "game_state.json"
MISSING_EVENTS_PATH = RUNTIME_DIR / "missing_events.json"
OFFICIAL_CARDS_PATH = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "Tempo Storm"
    / "The Bazaar"
    / "cache"
    / "cards.json"
)
OBSERVED_EVENT_GRAPH_PATH = RUNTIME_DIR / "observed_event_graph.json"
AUTO_BUILD_PREFIX = "Auto"
ANALYSIS_CACHE_MAX_ENTRIES = 16
AI_CACHE_MAX_ENTRIES = 8
RUNTIME_PAYLOAD_CACHE: tuple[str, int, int, dict[str, Any]] | None = None
VOLATILE_STATE_KEYS = {
    "updated_at_utc",
    "captured_at_utc",
    "timestamp",
    "last_updated",
    "frame",
    "frame_count",
    "_runtime_state_age_seconds",
}
AnalysisCacheKey = tuple[int, str, str, int | None, str]
ANALYSIS_CACHE: dict[AnalysisCacheKey, dict[str, Any]] = {}
AI_PAYLOAD_CACHE: dict[AnalysisCacheKey, dict[str, Any]] = {}
AI_ANALYSIS_CACHE: dict[AnalysisCacheKey, str] = {}
DEFAULT_ITEM_SPACE_TOTAL = 20
STAGE_LABELS_ZH = {
    "early": "前期",
    "mid": "中期",
    "late": "后期",
}
RARITY_LABELS_ZH = {
    "bronze": "青铜",
    "silver": "白银",
    "gold": "黄金",
    "diamond": "钻石",
    "legendary": "传奇",
    
}
RESOURCE_LABELS_ZH = {
    "gold": "金币",
    "exp": "经验",
    "experience": "经验",
    "health": "生命",
    "max_health": "最大生命",
    "healthmax": "最大生命",
    "income": "收入",
    "healthregen": "再生",
    "regen": "再生",
}
_OFFICIAL_CARDS_INDEX: dict[str, dict[str, Any]] | None = None
STATE_AGE_WARNING_SECONDS = 15.0
MAX_STATE_AGE_SECONDS = 120.0

def load_runtime_payload() -> tuple[dict[str, Any], Path]:
    global RUNTIME_PAYLOAD_CACHE
    try:
        state_stat = STATE_PATH.stat()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"实时状态文件不存在：{STATE_PATH}。请确认游戏和 Bazaar State Exporter 已启动。"
        )
    cache_key = (str(STATE_PATH), state_stat.st_mtime_ns, state_stat.st_size)
    if (
        RUNTIME_PAYLOAD_CACHE is not None
        and RUNTIME_PAYLOAD_CACHE[0] == cache_key[0]
        and RUNTIME_PAYLOAD_CACHE[1] == cache_key[1]
        and RUNTIME_PAYLOAD_CACHE[2] == cache_key[2]
    ):
        payload = dict(RUNTIME_PAYLOAD_CACHE[3])
    else:
        for attempt in range(3):
            try:
                payload = json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
                if isinstance(payload, dict):
                    RUNTIME_PAYLOAD_CACHE = (
                        cache_key[0],
                        cache_key[1],
                        cache_key[2],
                        dict(payload),
                    )
                break
            except (OSError, json.JSONDecodeError):
                if attempt >= 2:
                    raise
                time.sleep(0.02)
        else:
            raise RuntimeError("无法读取实时状态")

    if isinstance(payload, dict) and payload.get("source") == "installer":
        raise RuntimeError(
            "实时状态文件还只是安装器创建的占位文件。"
            "请先启动或重启 The Bazaar，并进入一局游戏，等待插件写入真实状态。"
        )
    if (
        isinstance(payload, dict)
        and payload.get("source") == "bepinex"
        and payload.get("status") == "waiting_for_game_state"
    ):
        raise RuntimeError(
            "插件已经加载并能写入实时状态文件，但还没有捕获到局内状态。"
            "请启动或重启 The Bazaar，并进入一局游戏。"
        )

    age_seconds = max(0.0, time.time() - STATE_PATH.stat().st_mtime)
    if age_seconds > MAX_STATE_AGE_SECONDS:
        raise RuntimeError(
            f"实时状态已停止更新（{age_seconds:.0f} 秒前）。"
            "请确认游戏正在运行，并重启游戏以重新加载插件配置。"
        )
    if age_seconds > STATE_AGE_WARNING_SECONDS:
        payload = dict(payload)
        payload["_runtime_state_age_seconds"] = age_seconds
        payload["_runtime_state_age_stale"] = True
    return payload, STATE_PATH


def runtime_state_is_plugin_owned(path: Path = STATE_PATH) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("source") == "bepinex"


def stable_cache_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): stable_cache_value(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
            if str(key) not in VOLATILE_STATE_KEYS
        }

    if isinstance(value, list):
        return [stable_cache_value(item) for item in value]

    return value


def analysis_cache_signature(payload: dict[str, Any]) -> str:
    stable_payload = stable_cache_value(payload)
    encoded = json.dumps(
        stable_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def signature_card_identity(item: Any) -> Any:
    if not isinstance(item, dict):
        return str(item) if item is not None else None

    return {
        "id": item.get("id"),
        "template_id": item.get("template_id"),
        "source_id": item.get("source_id"),
        "name": item.get("name"),
        "internal_name": item.get("internal_name"),
        "rarity": item.get("rarity"),
        "tier": item.get("tier"),
        "price": item.get("price"),
        "enchantment": item.get("enchantment"),
        "enchantments": stable_cache_value(item.get("enchantments", [])),
        "section": item.get("section"),
        "runtime_values": stable_cache_value(item.get("runtime_values", {})),
        "current_attributes": stable_cache_value(item.get("current_attributes", {})),
    }


def signature_option_identity(item: Any) -> Any:
    if not isinstance(item, dict):
        return str(item) if item is not None else None

    return {
        "id": item.get("id"),
        "template_id": item.get("template_id"),
        "source_id": item.get("source_id"),
        "event_name": item.get("event_name"),
        "name": item.get("name"),
        "kind": item.get("kind"),
        "card_type": item.get("card_type"),
        "branches": stable_cache_value(item.get("branches", [])),
    }


def state_signature_payload(payload: dict[str, Any]) -> dict[str, Any]:
    current_shop = payload.get("current_shop")
    visible_items = []
    shop_facts: dict[str, Any] = {}
    if isinstance(current_shop, dict):
        visible_items = current_shop.get("visible_items") or []
        shop_facts = {
            "merchant_id": current_shop.get("merchant_id"),
            "merchant_template_id": current_shop.get("merchant_template_id"),
            "merchant_name": current_shop.get("merchant_name"),
            "refresh_available": current_shop.get("refresh_available"),
            "refresh_cost": current_shop.get("refresh_cost"),
            "refreshes_used": current_shop.get("refreshes_used"),
            "refreshes_remaining": current_shop.get("refreshes_remaining"),
        }
    detailed_options = payload.get("event_options_detailed") or []
    reward_options = payload.get("current_reward_options") or []
    visible_cards = payload.get("visible_cards") or []
    owned_cards = payload.get("owned_cards")

    return {
        "source": payload.get("source"),
        "status": payload.get("status"),
        "screen_type": payload.get("screen_type"),
        "hero": payload.get("hero"),
        "build": payload.get("build"),
        "day": payload.get("day"),
        "event_name": payload.get("event_name"),
        "event_options": stable_cache_value(payload.get("event_options", [])),
        "event_option_ids": stable_cache_value(payload.get("event_option_ids", [])),
        "event_option_template_ids": stable_cache_value(
            payload.get("event_option_template_ids", [])
        ),
        "event_options_detailed": [
            signature_option_identity(item)
            for item in detailed_options
            if item is not None
        ],
        "shop_card_ids": [
            signature_card_identity(item)
            for item in visible_items
            if item is not None
        ],
        "reward_option_ids": [
            signature_card_identity(item)
            for item in reward_options
            if item is not None
        ],
        "visible_cards": [
            signature_card_identity(item)
            for item in visible_cards
            if item is not None
        ],
        "owned_cards": [
            signature_card_identity(item)
            for item in owned_cards
            if item is not None
        ]
        if isinstance(owned_cards, list)
        else stable_cache_value(owned_cards or {}),
        "gold": payload.get("gold"),
        "combat_health": payload.get("combat_health", payload.get("health")),
        "prestige": payload.get("prestige"),
        "max_prestige": payload.get("max_prestige"),
        "income": payload.get("income"),
        "level": payload.get("level"),
        "xp": payload.get("xp"),
        "inventory_slots_used": payload.get("inventory_slots_used"),
        "inventory_slots_total": payload.get("inventory_slots_total"),
        "current_shop": shop_facts,
    }


def state_signature(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        state_signature_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def remember_analysis_cache(
    cache_key: AnalysisCacheKey,
    response: dict[str, Any],
) -> None:
    if len(ANALYSIS_CACHE) >= ANALYSIS_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(ANALYSIS_CACHE))
        ANALYSIS_CACHE.pop(oldest_key, None)
    ANALYSIS_CACHE[cache_key] = response


def remember_ai_payload_cache(
    cache_key: AnalysisCacheKey,
    payload: dict[str, Any],
) -> None:
    if len(AI_PAYLOAD_CACHE) >= AI_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(AI_PAYLOAD_CACHE))
        AI_PAYLOAD_CACHE.pop(oldest_key, None)
        AI_ANALYSIS_CACHE.pop(oldest_key, None)
    AI_PAYLOAD_CACHE[cache_key] = payload


def attach_cached_ai_analysis(
    response: dict[str, Any],
    cache_key: AnalysisCacheKey,
) -> bool:
    if cache_key in AI_ANALYSIS_CACHE:
        response["ai_analysis"] = AI_ANALYSIS_CACHE[cache_key]
        return True

    ai_payload = AI_PAYLOAD_CACHE.get(cache_key)
    if ai_payload is None:
        return False

    try:
        ai_analysis = analyze_with_ai(ai_payload)
    except Exception as exc:  # noqa: BLE001 - AI failure should not fail rule analysis.
        response["ai_error"] = str(exc)
        return True

    AI_ANALYSIS_CACHE[cache_key] = ai_analysis
    response["ai_analysis"] = ai_analysis
    return True


def runtime_state_age_warnings(payload: dict[str, Any]) -> list[str]:
    if not payload.get("_runtime_state_age_stale"):
        return []
    return [
        f"实时状态超过 {STATE_AGE_WARNING_SECONDS:.0f} 秒未更新，"
        "当前分析可能不是最新局面；如果游戏还在运行，请稍等或重启游戏。"
    ]


def available_heroes(data: dict[str, Any]) -> list[str]:
    return sorted(
        {
            hero
            for card in data["cards"].values()
            for hero in card.get("heroes", [])
            if hero != "Common"
        }
    )


def build_belongs_to_hero(build_data: dict[str, Any], hero: str | None) -> bool:
    if not hero:
        return True

    build_hero = build_data.get("hero")
    return build_hero in (None, hero)


def build_options_for_hero(
    data: dict[str, Any],
    hero: str | None = None,
) -> list[dict[str, str]]:
    return [
        {
            "id": build_id,
            "name": (
                build_data.get("name")
                or build_data.get("display_name")
                or build_id
            ),
        }
        for build_id, build_data in sorted(data["builds"].items())
        if isinstance(build_data, dict) and build_belongs_to_hero(build_data, hero)
    ]


def choose_build(
    data: dict[str, Any],
    hero: str,
    day: int,
    preferred: str | None = None,
    owned_cards: Any = None,
) -> str:
    if (
        preferred
        and preferred in data["builds"]
        and build_belongs_to_hero(data["builds"][preferred], hero)
    ):
        return preferred

    best_match = match_build_from_owned_cards(data, hero, day, owned_cards)
    if best_match:
        return best_match

    hero_builds = [
        name
        for name, build_data in data["builds"].items()
        if build_data.get("hero") in (None, hero)
    ]
    if hero_builds:
        return hero_builds[0]

    return ensure_auto_build(data, hero, day)


def match_build_from_owned_cards(
    data: dict[str, Any],
    hero: str,
    day: int,
    owned_cards: Any,
) -> str | None:
    owned_names = extract_owned_card_names(owned_cards)
    hero_builds = [
        (name, build_data)
        for name, build_data in data["builds"].items()
        if build_data.get("hero") in (None, hero)
    ]
    if not hero_builds:
        return None

    scored = [
        (
            score_build_match(data, build_name, build_data, owned_names, day),
            build_name,
        )
        for build_name, build_data in hero_builds
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_name = scored[0]
    if best_score > 0:
        return best_name

    matching = applicable_build_names(data["builds"], hero, day)
    if matching:
        return matching[0]
    return hero_builds[0][0]


def extract_owned_card_names(owned_cards: Any) -> set[str]:
    if isinstance(owned_cards, dict):
        return {str(name) for name in owned_cards.keys() if name}

    if isinstance(owned_cards, list):
        names: set[str] = set()
        for item in owned_cards:
            if isinstance(item, dict) and item.get("name"):
                names.add(str(item["name"]))
            elif isinstance(item, str):
                names.add(item)
        return names

    return set()


def score_build_match(
    data: dict[str, Any],
    build_name: str,
    build_data: dict[str, Any],
    owned_names: set[str],
    day: int,
) -> float:
    """
    根据已拥有卡牌判断当前 build 的匹配度。

    原则：
    - 只按每张卡的最终定位加一次分，避免社区阵容和卡牌评级重复计分。
    - 定位判断复用 recommender.py 的 get_card_role_for_build()。
    - 当前天数适合该 build 时，给少量加成。
    """

    role_scores = {
        "core": 5.0,
        "transition": 3.0,
        "optional": 1.0,
    }

    score = 0.0

    for card_name in owned_names:
        card_data = data["cards"].get(card_name)
        if not card_data:
            continue

        role = get_card_role_for_build(
            card_name=card_name,
            card_data=card_data,
            build_name=build_name,
            build_data=build_data,
        )

        score += role_scores.get(role, 0.0)

    if score and build_applies_to_day(build_data, day):
        score += 0.25

    return score


def ensure_auto_build(data: dict[str, Any], hero: str, day: int) -> str:
    build_name = f"{AUTO_BUILD_PREFIX}{hero}"
    if build_name not in data["builds"]:
        data["builds"][build_name] = {
            "hero": hero,
            "display_name": f"自动匹配 {hero}",
            "applicable_stages": ["early", "mid", "late"],
            "day_range": [1, None],
            "build_summary": "没有配置英雄专属阵容时使用的自动兜底阵容。",
            "match_notes": [
                "在配置真实阵容前，不会把任何卡视为核心或可选。"
            ],
            "core_cards": [],
            "optional_cards": [],
            "wanted_tags": [],
            "event_priorities": [],
            "avoid_events": [],
        }
    return build_name


def normalize_payload_for_analysis(
    data: dict[str, Any],
    payload: dict[str, Any],
    build_override: str | None = None,
) -> dict[str, Any]:
    normalized = dict(payload)
    gold_value = first_optional_int(
        normalized.get("gold"),
        normalized.get("current_gold"),
        normalized.get("coins"),
        normalized.get("coin"),
        normalized.get("money"),
        normalized.get("player_gold"),
        nested_value(normalized, ("resources", "gold")),
        nested_value(normalized, ("player", "gold")),
        nested_value(normalized, ("economy", "gold")),
    )
    if gold_value is not None:
        normalized["gold"] = gold_value
    normalized["event_options"] = normalize_event_options(data, normalized)
    normalized["owned_cards"] = normalize_card_entries(data, normalized.get("owned_cards", []))
    normalized["visible_cards"] = normalize_card_entries(data, normalized.get("visible_cards", []))
    for field_name in (
        "owned_items",
        "board_items",
        "stash_items",
        "skills",
        "current_reward_options",
    ):
        normalized[field_name] = normalize_card_entries(
            data, normalized.get(field_name)
        )
    computed_slots_used = inventory_slots_used(data, normalized)
    if computed_slots_used is not None:
        normalized["inventory_slots_used"] = computed_slots_used
    elif normalized.get("inventory_slots_used") is None:
        normalized["inventory_slots_used"] = inventory_slots_used(
            data, normalized.get("board_items")
        )
    normalized["inventory_slots_total"] = inventory_slots_total(
        normalized.get("inventory_slots_total")
    )
    current_shop = normalized.get("current_shop")
    if isinstance(current_shop, dict):
        current_shop = dict(current_shop)
        current_shop["visible_items"] = normalize_card_entries(
            data, current_shop.get("visible_items")
        )
        normalized["current_shop"] = current_shop
        normalized["effective_shop"] = merge_effective_shop(
            data,
            current_shop,
            normalized.get("event_options", []),
        )
        attach_effective_shop_price_estimate(data, normalized)
    else:
        normalized["effective_shop"] = None
    hero = str(normalized.get("hero", ""))
    day = int(normalized.get("day", 1))
    normalized.pop("build", None)
    normalized["build"] = choose_build(
        data,
        hero,
        day,
        build_override,
        normalized.get("owned_cards", []),
    )
    normalized.setdefault("source", "runtime")
    normalized.setdefault("event_options", [])
    return normalized


def attach_effective_shop_price_estimate(
    data: dict[str, Any],
    normalized: dict[str, Any],
) -> None:
    effective_shop = normalized.get("effective_shop")
    if not isinstance(effective_shop, dict):
        return

    day = int(normalized.get("day") or 1)
    hero = str(normalized.get("hero") or "")
    candidate_event_names = list(normalized.get("event_options", []))
    merchant_name = effective_shop.get("merchant_name")
    if merchant_name:
        candidate_event_names.append(str(merchant_name))

    for event_name in dict.fromkeys(candidate_event_names):
        event_data = data.get("events", {}).get(event_name)
        if not isinstance(event_data, dict):
            continue
        if event_data.get("event_category") not in {"shops", "skill_shops"}:
            continue
        summary = None
        try:
            summary = get_cached_shop_pool_summary(
                data=data,
                event_name=event_name,
                event_data=event_data,
                cards=data.get("cards", {}),
                current_day=day,
                current_hero=hero,
                rarity_rules=data.get("rarity_rules", {}),
                resolver=infer_possible_cards_for_event,
            )
        except Exception:
            summary = None

        merchant_pool = (
            hydrate_cached_shop_cards(summary, data.get("cards", {}))
            if isinstance(summary, dict) and summary.get("cache_status") == "hit"
            else []
        )
        if not merchant_pool:
            merchant_pool, _ = infer_possible_cards_for_event(
                event_data,
                data.get("cards", {}),
                day,
                data.get("rarity_rules", {}),
                hero,
            )
        estimate = estimated_avg_shop_item_price(
            day,
            merchant_pool,
            data.get("cards", {}),
            data.get("rarity_rules", {}),
        )
        if estimate is not None:
            effective_shop["estimated_avg_item_price"] = estimate
            effective_shop["estimated_avg_item_price_source"] = (
                "shop_pool_cache"
                if isinstance(summary, dict) and summary.get("cache_status") == "hit"
                else "shop_item_tier_distribution_by_day"
            )
        if isinstance(summary, dict):
            effective_shop["shop_pool_summary"] = {
                key: summary.get(key)
                for key in (
                    "cache_status",
                    "merchant_name",
                    "current_hero",
                    "stage",
                    "stage_label",
                    "pool_count",
                    "avg_price",
                    "median_price",
                    "pool_focus",
                    "available_day_range",
                    "available_days",
                    "available_on_day",
                )
        }
        return


def nested_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def first_optional_int(*values: Any) -> int | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            result = int(value)
        except (TypeError, ValueError):
            continue
        if result >= 0:
            return result
    return None


def inventory_slots_total(value: Any = None) -> int:
    try:
        configured = int(value)
    except (TypeError, ValueError):
        configured = 0
    return max(DEFAULT_ITEM_SPACE_TOTAL, configured)


def inventory_slots_used(data: dict[str, Any], payload_or_items: Any) -> int | None:
    if isinstance(payload_or_items, dict):
        items = payload_or_items.get("owned_items")
        if not isinstance(items, list) or not items:
            items = [
                *(
                    payload_or_items.get("board_items")
                    if isinstance(payload_or_items.get("board_items"), list)
                    else []
                ),
                *(
                    payload_or_items.get("stash_items")
                    if isinstance(payload_or_items.get("stash_items"), list)
                    else []
                ),
            ]
    else:
        items = payload_or_items

    if not isinstance(items, list):
        return None

    total = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            return None
        slots = item_size_slots(data, item)
        if slots is None:
            return None
        total += slots
    return total


def item_size_slots(data: dict[str, Any], item: dict[str, Any]) -> int | None:
    size_slots = {"small": 1, "medium": 2, "large": 3}
    runtime_values = item.get("runtime_values")
    if isinstance(runtime_values, dict):
        for key in ("Size", "size"):
            slots = size_slots.get(normalize_size_label(runtime_values.get(key)))
            if slots is not None:
                return slots
    for key in ("size", "Size"):
        slots = size_slots.get(normalize_size_label(item.get(key)))
        if slots is not None:
            return slots

    card_data = data.get("cards", {}).get(str(item["name"]))
    if isinstance(card_data, dict):
        return size_slots.get(normalize_size_label(card_data.get("size")))
    return None


def normalize_size_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def normalize_event_options(data: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """
    把插件导出的事件选项归一化为 events.json 中的事件名。

    优先级：
    1. event_options_detailed：插件新导出的结构化事件选项
    2. event_option_template_ids + event_option_ids：旧平行数组兜底
    3. event_options：兼容手动输入事件名
    4. selected_encounter_ids：最后兜底

    注意：
    - enc_ 通常是当前事件实例 ID。
    - ste_ 通常是事件内部步骤 / 按钮选项。
    - com_ 通常是战斗选项。
    - pvp_ 通常是 PVP 对手。
    当前推荐系统只分析事件/商店，不把 ste_/com_/pvp_ 当成事件。
    """

    source_id_index = {
        str(source_id).lower(): event_name
        for event_name, event_data in data["events"].items()
        for source_id in event_data.get("source_ids", [])
    }
    event_names = set(data["events"])
    translations = data.get("translations", {})
    by_id = translations.get("by_id", {})

    option_ids = [str(value) for value in payload.get("event_option_ids", [])]
    template_ids = [str(value) for value in payload.get("event_option_template_ids", [])]
    raw_event_options = [str(value) for value in payload.get("event_options", [])]
    selected_encounter_ids = [
        str(value)
        for value in payload.get("selected_encounter_ids", [])
    ]

    candidates: list[str] = []

    # 新逻辑：优先使用插件导出的结构化事件选项。
    detailed_options = payload.get("event_options_detailed", [])
    has_structured_detailed_options = (
        isinstance(detailed_options, list) and bool(detailed_options)
    )
    if isinstance(detailed_options, list):
        for option in detailed_options:
            if not is_detailed_encounter_option(option):
                continue

            template_id = str(option.get("template_id") or "")
            name = str(option.get("name") or "")
            option_id = str(option.get("id") or "")

            if template_id:
                candidates.append(template_id)
            elif name and not is_runtime_generated_id(name):
                candidates.append(name)
            elif option_id and not is_runtime_generated_id(option_id):
                candidates.append(option_id)

    # 旧逻辑兜底：如果没有 detailed，再用 template_id + instance_id。
    if not candidates and not has_structured_detailed_options:
        template_limit = len(raw_event_options) if raw_event_options else len(template_ids)
        for index, template_id in enumerate(template_ids[:template_limit]):
            instance_id = option_ids[index] if index < len(option_ids) else ""

            if is_non_event_runtime_id(instance_id):
                continue

            candidates.append(template_id)

    # 兼容手动传入事件名的情况。
    if not candidates:
        for option in raw_event_options:
            if is_runtime_generated_id(option):
                continue
            candidates.append(option)

    # 最后兜底：如果没有 template_id，才考虑 selected_encounter_ids。
    # 但 enc_ 这种短实例 ID 本身通常不能直接映射到 events.json。
    if not candidates:
        for option in selected_encounter_ids:
            if is_runtime_generated_id(option):
                continue
            candidates.append(option)

    normalized: list[str] = []
    seen: set[str] = set()

    for option in candidates:
        option_text = str(option)

        event_name = source_id_index.get(option_text.lower())

        if event_name is None and option_text in event_names:
            event_name = option_text

        if event_name is None and looks_like_uuid(option_text):
            translated = by_id.get(option_text)
            if translated in event_names:
                event_name = translated
            else:
                event_name = translated or option_text

        if event_name is None:
            event_name = option_text

        if is_runtime_generated_id(event_name):
            continue

        if event_name in seen:
            continue

        seen.add(event_name)
        normalized.append(event_name)

    return normalized

def is_detailed_encounter_option(option: Any) -> bool:
    """判断插件导出的 detailed option 是否是真正的事件选项。"""
    if not isinstance(option, dict):
        return False

    option_id = str(option.get("id") or "").lower()
    kind = str(option.get("kind") or "").lower()
    card_type = str(option.get("card_type") or "").lower()

    if kind in {"step", "combat", "pvp"}:
        return False

    if option_id.startswith(("ste_", "com_", "pvp_")):
        return False

    if "combat" in card_type or "pvp" in card_type:
        return False

    if kind == "encounter":
        return True

    if "encounter" in card_type:
        return True

    if option_id.startswith("enc_"):
        return True

    return False

def is_runtime_generated_id(value: str) -> bool:
    return value.startswith(("enc_", "ste_", "com_", "pvp_"))


def is_non_event_runtime_id(value: str) -> bool:
    return value.startswith(("ste_", "com_", "pvp_"))

def looks_like_uuid(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            value,
        )
    )


def _coerce_observed_graph_node(node: Any) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}

    cleaned = dict(node)

    parent_source_ids = cleaned.get("parent_source_ids")
    if isinstance(parent_source_ids, list):
        cleaned["parent_source_ids"] = [
            str(source_id)
            for source_id in parent_source_ids
            if source_id not in (None, "")
        ]
    else:
        cleaned["parent_source_ids"] = []

    children = cleaned.get("children")
    if isinstance(children, list):
        cleaned["children"] = [dict(child) for child in children if isinstance(child, dict)]
    else:
        cleaned["children"] = []

    try:
        cleaned["observed_count"] = int(cleaned.get("observed_count") or 0)
    except (TypeError, ValueError):
        cleaned["observed_count"] = 0

    if "parent_event" in cleaned and cleaned["parent_event"] is not None:
        cleaned["parent_event"] = str(cleaned["parent_event"])

    return cleaned


def write_observed_event_graph(graph: dict[str, Any]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    cleaned: dict[str, Any] = {}
    if isinstance(graph, dict):
        for name, node in graph.items():
            if not name:
                continue
            cleaned[str(name)] = _coerce_observed_graph_node(node)
    OBSERVED_EVENT_GRAPH_PATH.write_text(
        json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_official_cards_index() -> dict[str, dict[str, Any]]:
    global _OFFICIAL_CARDS_INDEX

    if _OFFICIAL_CARDS_INDEX is not None:
        return _OFFICIAL_CARDS_INDEX

    if not OFFICIAL_CARDS_PATH.exists():
        _OFFICIAL_CARDS_INDEX = {}
        return _OFFICIAL_CARDS_INDEX

    raw = json.loads(OFFICIAL_CARDS_PATH.read_text(encoding="utf-8-sig"))

    version_data = raw.get("2.0.0") if isinstance(raw, dict) else None
    if not isinstance(version_data, list):
        _OFFICIAL_CARDS_INDEX = {}
        return _OFFICIAL_CARDS_INDEX

    result: dict[str, dict[str, Any]] = {}

    for card in version_data:
        if not isinstance(card, dict):
            continue

        card_id = card.get("Id")
        if card_id:
            result[str(card_id).lower()] = card

    _OFFICIAL_CARDS_INDEX = result
    return _OFFICIAL_CARDS_INDEX


def official_card_title(card: dict[str, Any]) -> str:
    localization = card.get("Localization", {})
    title = localization.get("Title", {}) if isinstance(localization, dict) else {}

    if isinstance(title, dict) and title.get("Text"):
        return str(title["Text"])

    return str(card.get("InternalName") or "")


def official_card_description(card: dict[str, Any]) -> str:
    localization = card.get("Localization", {})
    description = localization.get("Description", {}) if isinstance(localization, dict) else {}

    if isinstance(description, dict) and description.get("Text"):
        return render_card_text(card, str(description["Text"]))

    return render_card_text(card, str(card.get("InternalDescription") or ""))


def card_abilities(card: dict[str, Any]) -> dict[str, Any]:
    for key in ("Abilities", "abilities"):
        abilities = card.get(key)
        if isinstance(abilities, dict):
            return abilities

    raw_effects = card.get("raw_effects")
    if isinstance(raw_effects, dict):
        for key in ("Abilities", "abilities"):
            abilities = raw_effects.get(key)
            if isinstance(abilities, dict):
                return abilities

    return {}


def value_number(value_obj: Any) -> Any:
    if not isinstance(value_obj, dict):
        return None

    for key in ("Value", "DefaultValue"):
        value = value_obj.get(key)
        if value is not None:
            return value

    modifier = value_obj.get("Modifier")
    if isinstance(modifier, dict):
        modifier_value = value_number(modifier.get("Value"))
        if modifier_value is not None:
            return modifier_value

    return None


def action_value(action: dict[str, Any]) -> Any:
    return value_number(action.get("Value"))


def format_ability_value(value: Any, suffix: str | None = None) -> str:
    if isinstance(value, float):
        if suffix == "mod" and 0 < abs(value) < 1:
            percent = value * 100
            return f"{percent:g}%"
        return f"{value:g}"
    return str(value)


def render_card_text(card: dict[str, Any], text: str) -> str:
    if not text or "{ability." not in text:
        return text

    values: dict[str, Any] = {}
    for index, ability in enumerate(card_abilities(card).values()):
        if not isinstance(ability, dict):
            continue

        action = ability.get("Action", {})
        if not isinstance(action, dict):
            continue

        value = action_value(action)
        if value is None:
            continue

        ability_id = str(ability.get("Id") or index)
        values.setdefault(str(index), value)
        values[ability_id] = value

    def replace(match: re.Match[str]) -> str:
        ability_id = match.group(1)
        suffix = match.group(2)
        value = values.get(ability_id)
        if value is None:
            return match.group(0)
        if suffix and suffix not in {"mod", "value"}:
            return match.group(0)
        return format_ability_value(value, suffix)

    return re.sub(r"\{ability\.(\d+)(?:\.([A-Za-z_]+))?\}", replace, text)


def extract_resource_rewards_from_card(card: dict[str, Any]) -> dict[str, Any]:
    rewards: dict[str, Any] = {}

    abilities = card_abilities(card)
    if not isinstance(abilities, dict):
        return rewards

    for ability in abilities.values():
        if not isinstance(ability, dict):
            continue

        action = ability.get("Action", {})
        if not isinstance(action, dict):
            continue

        action_type = str(action.get("$type") or "")
        if not action_type.endswith("TActionPlayerModifyAttribute"):
            continue

        attribute = str(action.get("AttributeType") or "").lower()

        value = action_value(action)

        if value is None:
            continue

        if attribute == "gold":
            rewards["gold"] = value
        elif attribute == "health":
            rewards["health"] = value
        elif attribute == "healthmax":
            rewards["max_health"] = value
        elif attribute == "income":
            rewards["income"] = value
        elif attribute == "experience":
            rewards["exp"] = value
        elif attribute == "healthregen":
            rewards["healthregen"] = value
        else:
            rewards[attribute] = value

    return rewards


def enrich_child_from_official_cards(
    child_item: dict[str, Any],
    official_cards: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(child_item, dict):
        return

    if not isinstance(official_cards, dict):
        official_cards = {}

    source_id = str(child_item.get("source_id") or "").lower()
    if not source_id:
        return

    card = official_cards.get(source_id)
    if not card:
        child_item["name"] = child_item.get("name") or f"未知子选项 {source_id[:8]}"
        child_item["unresolved"] = True
        child_item["notes"] = "未能在官方 cards.json 中按 source_id 找到该子选项。"
        return

    child_item["name"] = official_card_title(card)
    child_item["internal_name"] = card.get("InternalName", "")
    child_item["description"] = official_card_description(card)
    child_item["official_type"] = card.get("$type", "")
    child_item["heroes"] = card.get("Heroes", [])
    child_item["tags"] = card.get("Tags", [])
    child_item["hidden_tags"] = card.get("HiddenTags", [])

    resource_rewards = extract_resource_rewards_from_card(card)
    if resource_rewards:
        child_item["resource_rewards"] = resource_rewards


def detailed_option_kind(option: dict[str, Any]) -> str:
    option_id = str(option.get("id") or "").lower()
    kind = str(option.get("kind") or "").lower()
    card_type = str(option.get("card_type") or "").lower()

    if option_id.startswith("ste_") or kind == "step" or "encounterstep" in card_type:
        return "step"

    if option_id.startswith("com_") or kind == "combat" or "combat" in card_type:
        return "combat"

    if option_id.startswith("pvp_") or kind == "pvp" or "pvp" in card_type:
        return "pvp"

    if option_id.startswith("enc_") or "eventencounter" in card_type:
        return "encounter"

    return kind or "unknown"


def event_name_from_source_id(data: dict[str, Any], source_id: str) -> str | None:
    source_id_lower = source_id.lower()

    for event_name, event_data in data.get("events", {}).items():
        if not isinstance(event_data, dict):
            continue

        for candidate in event_data.get("source_ids", []) or []:
            if str(candidate).lower() == source_id_lower:
                return event_name

    return None


def auto_observe_event_graph(data: dict[str, Any], payload: dict[str, Any]) -> None:
    detailed_options = payload.get("event_options_detailed", [])
    if not isinstance(detailed_options, list):
        return

    normalized_options: list[dict[str, Any]] = []

    for option in detailed_options:
        if not isinstance(option, dict):
            continue

        item = dict(option)
        item["kind"] = detailed_option_kind(item)

        template_id = str(item.get("template_id") or "")
        if template_id:
            item["event_name"] = event_name_from_source_id(data, template_id)

        normalized_options.append(item)

    parents = [
        option
        for option in normalized_options
        if option.get("kind") == "encounter"
    ]

    children = [
        option
        for option in normalized_options
        if option.get("kind") in {"step", "combat", "pvp"}
    ]

    # 只在“一个父事件 + 至少一个子选项”的界面记录
    if len(parents) == 1:
        branch_children: list[dict[str, Any]] = []
        for branch in parents[0].get("branches") or []:
            if not isinstance(branch, dict):
                continue

            branch_item = dict(branch)
            source_id = str(branch_item.get("template_id") or "")
            if source_id and not branch_item.get("event_name"):
                branch_item["event_name"] = event_name_from_source_id(data, source_id)
            if not branch_item.get("source"):
                branch_item["source"] = "next_encounter_on_selection"
            branch_children.append(branch_item)
        if branch_children:
            children = branch_children

    if len(parents) != 1 or not children:
        return

    parent = parents[0]
    parent_name = parent.get("event_name")

    if not parent_name:
        return
    parent_event_data = data.get("events", {}).get(parent_name, {})
    if parent_event_data.get("event_category") in {"shops", "skill_shops"}:
        return

    official_cards = load_official_cards_index()
    graph = load_observed_event_graph()

    parent_record = _coerce_observed_graph_node(graph.get(parent_name))
    graph[parent_name] = parent_record
    if not isinstance(parent_record, dict):
        parent_record = {}

    parent_record.setdefault("parent_event", parent_name)
    parent_record.setdefault("parent_source_ids", [])
    parent_record.setdefault("children", [])
    parent_record["observed_count"] = int(parent_record.get("observed_count", 0)) + 1

    parent_template_id = parent.get("template_id")
    if parent_template_id and parent_template_id not in parent_record["parent_source_ids"]:
        parent_record["parent_source_ids"].append(parent_template_id)

    # 用 source_id 做唯一键：见过的子选项不重复添加，新子选项自动加入
    existing_children = {
        child.get("source_id"): child
        for child in parent_record["children"]
        if isinstance(child, dict)
    }

    for child in children:
        source_id = child.get("template_id")
        if not source_id:
            continue

        child_item = existing_children.get(source_id)

        if not child_item:
            inferred = child.get("source") == "next_encounter_on_selection"
            child_item = {
                "name": child.get("event_name") or child.get("name") or "",
                "source_id": source_id,
                "kind": child.get("kind"),
                "card_type": child.get("card_type"),
                "seen": not inferred,
                "source": child.get("source") or "visible_event_option",
            }

            parent_record["children"].append(child_item)
            existing_children[source_id] = child_item

        # 旧子选项也会补充官方 cards.json 信息，但不会被删除或覆盖成空
        if not isinstance(child_item, dict):
            continue

        enrich_child_from_official_cards(child_item, official_cards)

    graph[parent_name] = parent_record

    # 这里每次父事件展开都写入，方便 observed_count 更新；
    # 但 children 是并集，不会因为本次没出现某个子选项就删除它。
    write_observed_event_graph(graph)


def normalize_card_entries(data: dict[str, Any], entries: Any) -> Any:
    if not isinstance(entries, list):
        return entries

    card_id_index: dict[str, str] = {}
    for card_name, card_data in data["cards"].items():
        if not isinstance(card_data, dict):
            continue
        for key in ("id", "source_id", "template_id"):
            value = card_data.get(key)
            if value:
                card_id_index[str(value).lower()] = card_name
        source_ids = card_data.get("source_ids")
        if isinstance(source_ids, list):
            for value in source_ids:
                if value:
                    card_id_index[str(value).lower()] = card_name
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            normalized.append(entry)
            continue

        item = dict(entry)
        if not item.get("name"):
            for card_id in (
                item.get("template_id"),
                item.get("source_id"),
                item.get("id"),
            ):
                if card_id:
                    name = card_id_index.get(str(card_id).lower())
                    if name:
                        item["name"] = name
                        break
            else:
                item["name"] = ""
        normalized.append(item)
    return normalized


def zh_name(data: dict[str, Any], name: Any, template_id: Any = None) -> str:
    translations = data.get("translations", {})
    by_id = translations.get("by_id", {})
    by_name = translations.get("by_name", {})
    if template_id:
        translated = by_id.get(str(template_id))
        if translated:
            return translated
    if name:
        return by_name.get(str(name), str(name))
    return ""


def zh_text(data: dict[str, Any], text: Any) -> str:
    if not text:
        return ""

    result = str(text)
    by_name = data.get("translations", {}).get("by_name", {})
    for source_name in sorted(by_name, key=len, reverse=True):
        translated = by_name.get(source_name)
        if translated:
            result = result.replace(source_name, translated)
    return result


def zh_id(data: dict[str, Any], source_id: Any) -> str:
    if not source_id:
        return ""
    return str(data.get("translations", {}).get("by_id", {}).get(str(source_id), ""))


def translate_common_game_text(data: dict[str, Any], text: Any) -> str:
    result = zh_text(data, text)
    if not result:
        return ""

    replacements = [
        (r"^Gain (\d+) Max Health$", r"获得 \1 最大生命值"),
        (r"^Gain (\d+) gold$", r"获得 \1 金币"),
        (r"^Gain (\d+) XP$", r"获得 \1 经验"),
        (r"^Heal (\d+)$", r"治疗 \1"),
        (r"^Deal (\d+) Damage$", r"造成 \1 伤害"),
        (r"^Get a ([A-Za-z]+)-tier Loot item$", r"获得一件\1级战利品物品"),
        (r"^\(if you have a ([^)]+)\) Choose a Skill$", r"（如果你拥有\1）选择一个技能"),
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


def gold_support_label(gold: dict[str, Any]) -> str:
    current_gold = gold.get("current_gold")
    if current_gold is None and not gold.get("gold_known"):
        return "\u91d1\u5e01\u672a\u77e5"
    if gold.get("supports_entry") is False:
        return "\u91d1\u5e01\u4e0d\u8db3"
    if gold.get("price_known") is False:
        return "\u5df2\u8bfb\u53d6\u91d1\u5e01\uff0c\u4f46\u4ef7\u683c\u4f30\u7b97\u4e0d\u8db3"
    return SHOP_GOLD_STATUS_LABELS.get(str(gold.get("status") or "unknown"), "\u672a\u77e5")


def display_reason_text(data: dict[str, Any], reason: Any) -> str:
    text = str(reason or "").strip()
    if not text:
        return ""

    shop_status_labels = {
        "strong_candidate": "\u5f3a\u5019\u9009",
        "candidate": "\u53ef\u8003\u8651",
        "situational": "\u770b\u5c40\u52bf",
        "weak_candidate": "\u4e0d\u4f18\u5148",
        "not_actionable": "\u6682\u4e0d\u8fdb\u5e97",
        "unknown": "\u4fe1\u606f\u4e0d\u8db3",
    }
    if text.startswith("shop_entry_status="):
        status = text.split("=", 1)[1]
        return f"\u5546\u5e97\u5165\u53e3\u8bc4\u4f30\uff1a{shop_status_labels.get(status, status)}"

    exact = {
        "visible_target_before_refresh": "\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u5df2\u6709\u76ee\u6807\uff0c\u4f18\u5148\u770b\u8d2d\u4e70\uff0c\u4e0d\u8981\u5148\u5237\u65b0\u3002",
        "no_visible_target_but_shop_pool_is_actionable": "\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u6ca1\u6709\u76ee\u6807\uff0c\u4f46\u8fd9\u5bb6\u5e97\u7684\u6c60\u8d28\u91cf\u652f\u6301\u8003\u8651\u5237\u65b0\u3002",
        "current_gold_unknown": "\u91d1\u5e01\u672a\u63a5\u5165\u5230\u8be5\u89c4\u5219\u9879\uff0c\u65e0\u6cd5\u5224\u65ad\u8d2d\u4e70\u529b\u3002",
        "refresh_cost_unknown": "\u5237\u65b0\u8d39\u7528\u672a\u77e5\uff0c\u4e0d\u5f3a\u63a8\u5237\u65b0\u3002",
    }
    if text in exact:
        return exact[text]

    patterns = [
        (r"^Can hit high-tier core cards: (.+)\.$", "\u53ef\u80fd\u547d\u4e2d\u9ad8\u8bc4\u7ea7\u6838\u5fc3\u5361\uff1a{names}\u3002"),
        (r"^Pool contains (\d+) current-build core cards\.$", "\u6c60\u5b50\u5305\u542b {0} \u5f20\u5f53\u524d\u9635\u5bb9\u6838\u5fc3\u5361\u3002"),
        (r"^Can upgrade owned cards: (.+)\.$", "\u53ef\u5347\u7ea7\u5df2\u62e5\u6709\u5361\uff1a{names}\u3002"),
        (r"^Affects owned matching items: (.+)\.$", "\u5f71\u54cd\u5df2\u62e5\u6709\u7684\u5339\u914d\u7269\u54c1\uff1a{names}\u3002"),
        (r"^Pool contains (\d+) transition cards\.$", "\u6c60\u5b50\u5305\u542b {0} \u5f20\u8fc7\u6e21\u5361\u3002"),
        (r"^Pool contains (\d+) S/A tier cards\.$", "\u6c60\u5b50\u5305\u542b {0} \u5f20 S/A \u8bc4\u7ea7\u5361\u3002"),
        (r"^Pool contains (\d+) alternate-build core cards\.$", "\u6c60\u5b50\u5305\u542b {0} \u5f20\u8f6c\u578b/\u5907\u9009\u9635\u5bb9\u6838\u5fc3\u5361\u3002"),
        (r"^Pool has (\d+) cards; (\d+) are build-relevant \((\d+)%\)\.$", "\u6c60\u5b50\u5171 {0} \u5f20\uff1b{1} \u5f20\u548c\u5f53\u524d\u9635\u5bb9\u76f8\u5173\uff08{2}%\uff09\u3002"),
        (r"^Shop view expects ([\d.]+) relevant cards; chance to see at least one relevant card (\d+)%\.$", "\u8fdb\u5e97\u540e\u9884\u671f\u53ef\u89c1 {0} \u5f20\u76f8\u5173\u5361\uff1b\u81f3\u5c11\u770b\u5230\u4e00\u5f20\u76f8\u5173\u5361\u7684\u6982\u7387\u7ea6 {1}%\u3002"),
        (r"^Reward gives (\d+) items; expected relevant cards ([\d.]+), useful hit chance (\d+)%\.$", "\u5956\u52b1\u7ed9 {0} \u4e2a\u7269\u54c1\uff1b\u9884\u671f\u76f8\u5173\u5361 {1} \u5f20\uff0c\u6709\u7528\u547d\u4e2d\u7387\u7ea6 {2}%\u3002"),
        (r"^Chance to hit at least one core card is (\d+)%\.$", "\u81f3\u5c11\u547d\u4e2d\u4e00\u5f20\u6838\u5fc3\u5361\u7684\u6982\u7387\u7ea6 {0}%\u3002"),
        (r"^Unrelated items still have estimated sell value around ([\d.]+) gold\.$", "\u65e0\u5173\u7269\u54c1\u4ecd\u6709\u7ea6 {0} \u91d1\u5e01\u7684\u8f6c\u5356\u671f\u671b\u3002"),
    ]
    for pattern, template in patterns:
        match = re.match(pattern, text)
        if not match:
            continue
        groups = match.groups()
        if "{names}" in template:
            names = display_name_list(
                data,
                [part.strip() for part in groups[0].split(",") if part.strip()],
            )
            return template.format(names="\u3001".join(names))
        return template.format(*groups)

    return translate_common_game_text(data, text)


def display_card_entry(data: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    name = card.get("name") or ""
    template_id = card.get("template_id") or card.get("id")
    rarity = card.get("rarity")
    return {
        **card,
        "display_name": zh_name(data, name, template_id),
        "rarity": RARITY_LABELS_ZH.get(str(rarity).lower(), rarity) if rarity else rarity,
        "card_type": display_card_type(data, card),
    }


def display_card_names(data: dict[str, Any], cards: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "display_name": zh_name(data, name),
            "rarity": RARITY_LABELS_ZH.get(str(rarity).lower(), rarity),
            "card_type": display_card_type(data, {"name": name}),
        }
        for name, rarity in sorted(cards.items())
    ]


def display_build_card_names(data: dict[str, Any], card_names: Any) -> list[dict[str, str]]:
    if not isinstance(card_names, list):
        return []

    return [
        {
            "name": str(name),
            "display_name": zh_name(data, name),
        }
        for name in card_names
        if name
    ]


def display_name_list(data: dict[str, Any], card_names: Any) -> list[str]:
    if not isinstance(card_names, list):
        return []

    return [zh_name(data, name) for name in card_names if name]


def build_detail_for_state(data: dict[str, Any], build_name: str) -> dict[str, Any]:
    build_data = data.get("builds", {}).get(build_name, {})
    if not isinstance(build_data, dict):
        build_data = {}

    optional_cards = [
        *(
            build_data.get("optional_cards", [])
            if isinstance(build_data.get("optional_cards", []), list)
            else []
        ),
        *(
            build_data.get("transition_cards", [])
            if isinstance(build_data.get("transition_cards", []), list)
            else []
        ),
    ]

    return {
        "id": build_name,
        "display_name": (
            build_data.get("name")
            or build_data.get("display_name")
            or build_name
        ),
        "core_cards": display_build_card_names(data, build_data.get("core_cards", [])),
        "optional_cards": display_build_card_names(
            data,
            list(dict.fromkeys(optional_cards)),
        ),
        "wanted_tags": [
            str(tag)
            for tag in build_data.get("wanted_tags", [])
            if tag
        ]
        if isinstance(build_data.get("wanted_tags", []), list)
        else [],
    }


def display_card_type(data: dict[str, Any], card: dict[str, Any]) -> str:
    card_type = card.get("card_type") or card.get("type")
    if card_type:
        return str(card_type)

    name = card.get("name")
    card_data = data.get("cards", {}).get(str(name)) if name else None
    if isinstance(card_data, dict):
        return str(card_data.get("type") or "")
    return ""


def displayed_owned_groups(
    data: dict[str, Any],
    state: GameState,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    owned_items = (
        [display_card_entry(data, item) for item in state.owned_items]
        if isinstance(state.owned_items, list)
        else []
    )
    skills = (
        [display_card_entry(data, item) for item in state.skills]
        if isinstance(state.skills, list)
        else []
    )
    if owned_items or skills:
        return owned_items, skills

    all_owned = display_card_names(data, state.owned_cards)
    return (
        [card for card in all_owned if str(card.get("card_type", "")).lower() != "skill"],
        [card for card in all_owned if str(card.get("card_type", "")).lower() == "skill"],
    )


def role_label(role: str | None) -> str:
    return {
        "core": "核心",
        "transition": "可选",
        "optional": "可选",
        "unrelated": "无关",
    }.get(role or "", role or "")


def recommendation_label(label: str | None) -> str:
    return {
        "High Value": "优先选择",
        "Medium Value": "可以考虑",
        "Low Value": "优先级低",
    }.get(label or "", label or "")


def importance_label(label: str | None) -> str:
    return {
        "critical": "关键",
        "high": "高",
        "medium": "中",
        "low": "低",
        "ignored": "忽略",
        "unknown": "未知",
    }.get(label or "", label or "")


def shop_recommendation_label(label: str | None) -> str:
    return {
        "buy_now": "建议购买",
        "tempo_upgrade": "节奏补强",
        "stash_future": "留作后期",
        "observe": "观察",
        "skip": "跳过",
        "consider_buying_together": "可成组购买",
        "prioritize_best_core": "优先最强核心",
        "unknown": "待判断",
    }.get(label or "", label or "")


def relation_label(label: str | None) -> str:
    return {
        "current_build": "当前阶段",
        "future_build": "后续阶段",
        "late_build": "后期方向",
        "past_build": "已过期",
    }.get(label or "", label or "")


def stages_label(stages: Any) -> str:
    if not isinstance(stages, list):
        return ""
    labels = [
        STAGE_LABELS_ZH.get(str(stage).lower(), str(stage))
        for stage in stages
        if str(stage).lower() in STAGE_LABELS_ZH
    ]
    return "/".join(labels)


def shop_action_label(label: str | None) -> str:
    return {
        "buy_visible": "购买可见目标",
        "consider_bundle": "考虑组合购买",
        "skip": "跳过刷新",
        "unknown": "暂不强推",
    }.get(label or "", label or "")


def load_observed_event_graph() -> dict[str, Any]:
    if not OBSERVED_EVENT_GRAPH_PATH.exists():
        return {}

    try:
        data = json.loads(OBSERVED_EVENT_GRAPH_PATH.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}

    if not isinstance(data, dict):
        return {}

    cleaned: dict[str, Any] = {}
    for name, node in data.items():
        if not name:
            continue
        cleaned[str(name)] = _coerce_observed_graph_node(node)

    return cleaned


def format_resource_rewards(resource_rewards: dict[str, Any]) -> str:
    if not isinstance(resource_rewards, dict) or not resource_rewards:
        return ""

    parts: list[str] = []

    for key, value in resource_rewards.items():
        if value in (None, "", 0):
            continue

        label = RESOURCE_LABELS_ZH.get(str(key).lower(), str(key))

        if isinstance(value, float) and value.is_integer():
            value_text = str(int(value))
        else:
            value_text = str(value)

        parts.append(f"+{value_text} {label}")

    return "，".join(parts)


SHOP_ENTRY_STATUS_LABELS = {
    "strong_candidate": "\u503c\u5f97\u8fdb\u5e97",
    "candidate": "\u53ef\u4ee5\u8fdb\u5e97",
    "situational": "\u770b\u5c40\u52bf",
    "weak_candidate": "\u4e0d\u4f18\u5148",
    "not_actionable": "\u6682\u4e0d\u8fdb\u5e97",
    "unknown": "\u4fe1\u606f\u4e0d\u8db3",
}
SHOP_DENSITY_LABELS = {"high": "\u9ad8", "medium": "\u4e2d", "low": "\u4f4e", "unknown": "\u672a\u77e5"}
SHOP_GOLD_STATUS_LABELS = {
    "refresh_supported": "\u53ef\u4e70\u4e14\u53ef\u5237",
    "buy_supported": "\u5927\u81f4\u53ef\u4e70",
    "insufficient": "\u91d1\u5e01\u4e0d\u8db3",
    "unknown": "\u672a\u77e5",
}
SHOP_POOL_STAGE_LABELS = {
    "bronze_only": "\u9752\u94dc\u9636\u6bb5",
    "silver_unlocked": "\u767d\u94f6\u9636\u6bb5",
    "gold_unlocked": "\u9ec4\u91d1\u9636\u6bb5",
    "diamond_unlocked": "\u94bb\u77f3\u9636\u6bb5",
}
SHOP_CACHE_STATUS_LABELS = {
    "hit": "\u5df2\u547d\u4e2d\u7f13\u5b58",
    "miss": "\u672a\u547d\u4e2d\u7f13\u5b58",
    "unavailable": "\u6682\u65e0\u7f13\u5b58",
}
SHOP_INSIDE_ACTION_LABELS = {"buy": "\u5148\u4e70\u76ee\u6807", "refresh": "\u53ef\u4ee5\u5237\u65b0", "skip": "\u4e0d\u5efa\u8bae\u5237\u65b0"}
SHOP_INSIDE_REASON_LABELS = {
    "visible_target_before_refresh": "\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u5df2\u6709\u9635\u5bb9\u76ee\u6807\uff0c\u5148\u4e70\u76ee\u6807\uff0c\u4e0d\u8981\u5148\u5237\u65b0\u3002",
    "refresh_not_available": "\u5f53\u524d\u5546\u5e97\u4e0d\u53ef\u5237\u65b0\u3002",
    "refresh_cost_unknown": "\u5237\u65b0\u8d39\u7528\u672a\u77e5\uff0c\u4e0d\u5f3a\u63a8\u5237\u65b0\u3002",
    "current_gold_unknown": "\u91d1\u5e01\u672a\u77e5\uff0c\u65e0\u6cd5\u5224\u65ad\u5237\u65b0\u540e\u8d2d\u4e70\u529b\u3002",
    "not_enough_gold_after_refresh": "\u5237\u65b0\u540e\u6ca1\u6709\u53ef\u9760\u8d2d\u4e70\u9884\u7b97\u3002",
    "refresh_leaves_insufficient_purchase_budget": "\u91d1\u5e01\u867d\u53ef\u652f\u4ed8\u5237\u65b0\uff0c\u4f46\u5237\u65b0\u540e\u53ef\u80fd\u4e70\u4e0d\u8d77\u76ee\u6807\u3002",
    "no_visible_target_but_shop_pool_is_actionable": "\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u6ca1\u6709\u76ee\u6807\uff0c\u4f46\u5546\u4eba\u6c60\u8d28\u91cf\u53ef\u652f\u6301\u5237\u65b0\u3002",
    "no_visible_target_and_pool_quality_low": "\u5f53\u524d\u53ef\u89c1\u5546\u54c1\u65e0\u76ee\u6807\uff0c\u4e14\u5546\u4eba\u6c60\u8d28\u91cf\u4e0d\u652f\u6301\u5f3a\u5237\u3002",
}


def shop_rule_display_from_result(
    result: dict[str, Any],
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inside = result.get("shop_inside_analysis")
    if isinstance(inside, dict):
        action = str(inside.get("action") or "skip")
        reason_code = str(inside.get("reason") or "")
        visible_targets = int(inside.get("visible_target_count") or 0)
        refresh_ratio = float(inside.get("refresh_pool_valuable_ratio") or 0.0)
        reason = SHOP_INSIDE_REASON_LABELS.get(reason_code, reason_code)
        if action == "refresh" and refresh_ratio:
            reason = f"{reason} \u5237\u65b0\u6c60\u76f8\u5173\u5360\u6bd4\u7ea6 {refresh_ratio:.0%}\u3002"
        if action == "buy" and visible_targets:
            reason = f"{reason} \u53ef\u89c1\u76ee\u6807 {visible_targets} \u4e2a\u3002"
        if action == "refresh":
            reason = f"{reason} \u5237\u65b0\u53ea\u5c5e\u4e8e\u5f53\u524d\u8fd9\u5bb6\u5546\u5e97\uff0c\u4e0d\u4f1a\u8de8\u5e97\u7d2f\u8ba1\u3002"
        return {"phase": "inside", "phase_label": "\u5e97\u5185\u64cd\u4f5c", "action": action, "action_label": SHOP_INSIDE_ACTION_LABELS.get(action, action), "reason": reason}

    entry = result.get("shop_entry_analysis")
    if not isinstance(entry, dict):
        return {}
    status = str(entry.get("status") or "unknown")
    counts = entry.get("target_counts") if isinstance(entry.get("target_counts"), dict) else {}
    gold = entry.get("gold_support") if isinstance(entry.get("gold_support"), dict) else {}
    debug = entry.get("debug") if isinstance(entry.get("debug"), dict) else {}
    density = SHOP_DENSITY_LABELS.get(str(entry.get("target_density_band") or "unknown"), "\u672a\u77e5")
    gold_status = gold_support_label(gold)
    current_gold = gold.get("current_gold")
    merchant_count = entry.get("day_available_merchant_count") or "\u672a\u77e5"
    pool_count = entry.get("pool_count") or "\u672a\u77e5"
    density_rank = entry.get("target_density_rank")
    core_hits = int(debug.get("current_core_hits") or 0)
    tempo_hits = int(debug.get("current_tempo_hits") or 0)
    optional_hits = int(debug.get("current_optional_hits") or 0)
    future_hits = int(debug.get("future_core_hits") or counts.get("future_core") or 0)
    reason = (
        f"\u8fdb\u5e97\u524d\u8bc4\u4f30\uff1a\u5f53\u5929\u53ef\u51fa\u73b0\u5546\u4eba {merchant_count} \u5bb6\uff0c"
        f"\u8be5\u5e97\u6c60\u5b50 {pool_count} \u5f20\uff0c\u76ee\u6807\u5bc6\u5ea6\u76f8\u5bf9\u5f53\u5929\u4e3a{density}\uff1b"
        f"\u5f53\u524d\u6838\u5fc3 {core_hits}\u3001\u8fc7\u6e21 {tempo_hits}\u3001\u53ef\u9009 {optional_hits}\u3001\u672a\u6765\u6838\u5fc3 {future_hits}\uff1b"
        f"\u91d1\u5e01\u72b6\u6001\uff1a{gold_status}"
    )
    if current_gold is not None:
        reason += f"\uff08\u5f53\u524d {current_gold} \u91d1\uff09"
    reason += "\u3002"
    if density_rank and isinstance(merchant_count, int):
        reason += f" \u5bc6\u5ea6\u6392\u540d\uff1a\u7b2c {density_rank}/{merchant_count}\u3002"
    top_merchants = [
        zh_name(data or {}, item.get("merchant"))
        for item in entry.get("top_day_merchants_by_density", [])[:3]
        if isinstance(item, dict) and item.get("merchant")
    ]
    if top_merchants:
        reason += f" \u5f53\u5929\u76ee\u6807\u5bc6\u5ea6\u6700\u9ad8\u7684\u5e97\uff1a{'、'.join(top_merchants)}\u3002"
    if entry.get("theoretical_only"):
        reason += " \u4e3b\u8981\u662f\u7406\u8bba\u6c60\u5b50\u76ee\u6807\uff0c\u5f53\u524d\u76f4\u63a5\u6536\u76ca\u4e0d\u7a33\u3002"
    if status == "not_actionable":
        reason += " \u5f53\u524d\u8d2d\u4e70\u529b\u6216\u673a\u4f1a\u6210\u672c\u4e0d\u652f\u6301\u5f3a\u63a8\u3002"
    elif status == "strong_candidate":
        reason += " \u89c4\u5219\u5c42\u8ba4\u4e3a\u5ba2\u89c2\u9002\u5408\uff0c\u4f46\u4ecd\u8981\u6bd4\u8f83\u540c\u5c4f\u514d\u8d39\u6536\u76ca\u3002"
    action = "enter" if status in {"strong_candidate", "candidate"} else "defer"
    return {"phase": "entry", "phase_label": "\u8fdb\u5e97\u524d\u8bc4\u4f30", "action": action, "action_label": SHOP_ENTRY_STATUS_LABELS.get(status, status), "reason": reason, "status": status}


def apply_shop_rule_display_to_build_analysis(
    build_analysis: dict[str, Any],
    recommendations: list[dict[str, Any]],
    data: dict[str, Any] | None = None,
) -> None:
    for item in recommendations:
        if not isinstance(item, dict):
            continue
        display = shop_rule_display_from_result(item, data)
        if not display:
            continue
        build_analysis["shop_rule_display"] = display
        build_analysis["shop_phase"] = display.get("phase")
        build_analysis["shop_phase_label"] = display.get("phase_label")
        build_analysis["shop_action"] = display.get("action")
        build_analysis["shop_action_label"] = display.get("action_label")
        build_analysis["refresh_reason"] = display.get("reason")
        return


def summarize_parent_child_options(
    data: dict[str, Any],
    parent_graph: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(parent_graph, dict):
        return []

    children = parent_graph.get("children", [])
    if not isinstance(children, list):
        return []

    result: list[dict[str, Any]] = []

    for child in children:
        if not isinstance(child, dict):
            continue

        resource_rewards = child.get("resource_rewards", {})
        reward_text = format_resource_rewards(resource_rewards)
        source_id = str(child.get("source_id") or "")
        official_card = load_official_cards_index().get(source_id.lower()) if source_id else None

        name = child.get("name") or ""
        if not name:
            source_id = str(child.get("source_id") or "")
            name = f"未知子选项 {source_id[:8]}" if source_id else "未知子选项"
        display_name = (
            zh_id(data, source_id)
            or zh_text(data, zh_name(data, child.get("display_name") or name))
        )
        description = child.get("description", "")
        translated_description = (
            data.get("translations", {}).get("by_name", {}).get(str(description))
            if description
            else None
        )
        if official_card:
            official_title = official_card_title(official_card)
            official_description = official_card_description(official_card)
            if official_title:
                display_name = translate_common_game_text(data, official_title)
            if official_description:
                description = official_description
                translated_description = data.get("translations", {}).get("by_name", {}).get(str(description))
        source_name = child.get("source_name", "")

        result.append(
            {
                "name": display_name or zh_text(data, name),
                "display_name": display_name,
                "source_id": child.get("source_id", ""),
                "source_name": source_name,
                "source_display_name": zh_name(data, source_name),
                "kind": child.get("kind", ""),
                "card_type": child.get("card_type", ""),
                "description": translated_description or translate_common_game_text(data, description),
                "resource_rewards": resource_rewards,
                "reward_text": translate_common_game_text(data, reward_text),
                "unresolved": bool(child.get("unresolved", False)),
                "count": int(child.get("count", 0)),
            }
        )

    return result


def static_child_options_for_event(data: dict[str, Any], event_name: str | None) -> list[dict[str, Any]]:
    if not event_name:
        return []

    event_data = data.get("events", {}).get(event_name)
    if not isinstance(event_data, dict):
        return []

    if event_data.get("event_category") in {"shops", "skill_shops"}:
        return []

    source_ids = [str(value).lower() for value in event_data.get("source_ids", []) if value]
    if not source_ids:
        return []

    encounters = data.get("encounters", {})
    if not isinstance(encounters, dict):
        return []

    encounter_by_source_id: dict[str, dict[str, Any]] = {}
    for encounter in encounters.values():
        if not isinstance(encounter, dict):
            continue
        for key in ("source_id", "template_id", "id"):
            source_id = str(encounter.get(key) or "").lower()
            if source_id:
                encounter_by_source_id[source_id] = encounter
        for source_id in encounter.get("source_ids", []) or []:
            source_id_text = str(source_id or "").lower()
            if source_id_text:
                encounter_by_source_id[source_id_text] = encounter

    children: list[dict[str, Any]] = []
    seen_child_ids: set[str] = set()
    seen_child_labels: set[str] = set()

    for source_id in source_ids:
        parent = encounter_by_source_id.get(source_id)
        if not parent:
            continue

        for child_id in extract_spawn_context_ids(parent.get("raw_effects", {})):
            child_id_lower = child_id.lower()
            if child_id_lower == source_id or child_id_lower in seen_child_ids:
                continue
            seen_child_ids.add(child_id_lower)

            child = encounter_by_source_id.get(child_id_lower)
            child_name = event_name_from_source_id(data, child_id) or (
                str(child.get("name") or child.get("internal_name") or "")
                if isinstance(child, dict)
                else ""
            )
            child_source_name = child_name or child_id
            child_display_name = zh_name(data, child_source_name, child_id)
            child_label_key = child_display_name.strip().lower()
            if child_label_key and child_label_key in seen_child_labels:
                continue
            if child_label_key:
                seen_child_labels.add(child_label_key)
            child_description = (
                render_card_text(child, str(child.get("description") or ""))
                if isinstance(child, dict)
                else ""
            )
            child_type = str(child.get("type") or child.get("card_type") or "") if isinstance(child, dict) else ""

            child_item = {
                "name": child_display_name,
                "source_name": child_source_name,
                "display_name": child_display_name,
                "source_id": child_id,
                "kind": static_child_kind(child_type),
                "card_type": child_type,
                "description": translate_common_game_text(data, child_description) if child_description else "",
                "resource_rewards": (
                    extract_resource_rewards_from_card(child)
                    if isinstance(child, dict)
                    else {}
                ),
                "source": "static_encounters_generated",
                "seen": False,
            }
            children.append(child_item)

    return children


def extract_spawn_context_ids(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    def add_id(raw: Any) -> None:
        text = str(raw or "").strip()
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        result.append(text)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_type = str(node.get("$type") or "")
            if node_type.endswith("TSpawnFilterIdList"):
                ids = node.get("Ids")
                if isinstance(ids, list):
                    for item in ids:
                        add_id(item)
                elif ids:
                    add_id(ids)
                return

            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return result


def static_child_kind(card_type: str) -> str:
    lower = card_type.lower()
    if "encounterstep" in lower:
        return "step"
    if "combat" in lower:
        return "combat"
    if "pvp" in lower:
        return "pvp"
    if "eventencounter" in lower:
        return "encounter"
    return "unknown"


def parent_event_reason_text(child_options: list[dict[str, Any]]) -> str:
    if not child_options:
        return "这是一个父事件，但当前还没有观察到可分析的子选项收益。"

    parts: list[str] = []

    for child in child_options[:5]:
        name = child.get("display_name") or child.get("name") or "未知子选项"
        reward_text = child.get("reward_text") or ""
        description = child.get("description") or ""

        if reward_text:
            parts.append(f"{name}：{reward_text}")
        elif description:
            parts.append(f"{name}：{description}")
        else:
            parts.append(f"{name}：暂未解析收益")

    return "这是一个父事件，已根据运行时观察到的子选项估算可能收益：" + "；".join(parts)


def tier_label(tier: Any) -> str:
    if tier in (None, "", "Unknown"):
        return ""
    return str(tier)


def priority_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = {"core": 0, "transition": 1, "optional": 2}
    filtered = [card for card in cards if card.get("role") in roles]
    filtered.sort(key=lambda card: (roles[card.get("role")], card.get("name", "")))
    return [
        {
            "name": card.get("name"),
            "tier": tier_label(card.get("tier")),
            "role": card.get("role"),
            "can_upgrade": card.get("can_upgrade", False),
            "alt_core_build_hits": card.get("alt_core_build_hits", []),
        }
        for card in filtered
    ]

def text_has_skill_reward(*values: Any) -> bool:
    text = " ".join(str(value or "").lower() for value in values if value)

    skill_keywords = [
        "skill",
        "skills",
        "choose 1 of 2 skills",
        "choose 1 of 3 skills",
        "choose a skill",
        "gain a skill",
        "技能",
    ]

    return any(keyword in text for keyword in skill_keywords)


def event_has_skill_reward(event_data: dict[str, Any] | None) -> bool:
    """判断事件是否包含技能收益。用于 UI 展示层兜底。"""
    if not isinstance(event_data, dict):
        return False

    event_category = str(event_data.get("event_category") or "").strip().lower()
    event_type = str(event_data.get("event_type") or "").strip().lower()
    effect = str(event_data.get("effect") or "").strip().lower()

    if event_category == "skill_shops":
        return True

    if event_type in {"skill_shop", "skill_event", "skill_reward"}:
        return True

    if effect in {"gain_skill", "choose_skill", "skill_reward"}:
        return True

    qualitative_rewards = event_data.get("qualitative_rewards", [])
    if isinstance(qualitative_rewards, list):
        for reward in qualitative_rewards:
            if text_has_skill_reward(reward):
                return True

    return text_has_skill_reward(
        event_data.get("name", ""),
        event_data.get("notes", ""),
        event_data.get("description", ""),
    )


def child_option_has_skill_reward(child: dict[str, Any]) -> bool:
    """判断运行时观察到的子选项是否包含技能收益。"""
    if not isinstance(child, dict):
        return False

    return text_has_skill_reward(
        child.get("name", ""),
        child.get("description", ""),
        child.get("card_type", ""),
        child.get("official_type", ""),
    )


def child_options_have_skill_reward(child_options: list[dict[str, Any]]) -> bool:
    return any(child_option_has_skill_reward(child) for child in child_options)


def event_description_text(event_data: dict[str, Any] | None) -> str:
    if not isinstance(event_data, dict):
        return ""

    for key in ("description", "notes", "summary", "effect_text"):
        value = str(event_data.get(key) or "").strip()
        if value:
            return value

    qualitative_rewards = event_data.get("qualitative_rewards", [])
    if isinstance(qualitative_rewards, list):
        values = [str(value).strip() for value in qualitative_rewards if str(value or "").strip()]
        if values:
            return "；".join(values[:3])

    return ""


def event_has_value_rule(event_data: dict[str, Any] | None) -> bool:
    """判断一个已识别事件是否有可计算收益规则。"""
    if not event_data:
        return False

    event_category = str(event_data.get("event_category") or "").strip().lower()
    effect = str(event_data.get("effect") or "").strip().lower()
    
    if event_has_skill_reward(event_data):
        return True

    if event_category in {"item_events", "enchant_events"}:
        if effect:
            return True
        if event_data.get("target_tags"):
            return True
        if event_data.get("enchantment_tags"):
            return True
        if event_data.get("rarity_filter") or event_data.get("rarity_rule"):
            return True
    
    if event_data.get("shop_pool"):
        return True

    card_reward = event_data.get("card_reward")
    if isinstance(card_reward, dict) and card_reward.get("enabled"):
        return True

    if event_data.get("followup_options"):
        return True

    resource_rewards = event_data.get("resource_rewards", {})
    if isinstance(resource_rewards, dict) and any(resource_rewards.values()):
        return True

    qualitative_rewards = event_data.get("qualitative_rewards", [])
    if isinstance(qualitative_rewards, list) and qualitative_rewards:
        return True
    
    return False

def summarize_recommendation(data: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    event_name = result.get("event_name")
    event_data = data["events"].get(event_name) if event_name else None
    observed_event_graph = load_observed_event_graph()
    parent_graph = observed_event_graph.get(event_name) if event_name else None
    if event_data and event_data.get("event_category") in {"shops", "skill_shops"}:
        parent_graph = None
    child_options = summarize_parent_child_options(data, parent_graph)
    if not child_options:
        child_options = static_child_options_for_event(data, event_name)
    is_parent_event = bool(child_options)
    has_skill_child_reward = child_options_have_skill_reward(child_options)

    known = bool(event_name) and event_name in data["events"]
    has_value_rule = event_has_value_rule(event_data) if known else False
    has_skill_reward = (event_has_skill_reward(event_data) if known else False) or has_skill_child_reward

    recommendation = result.get("recommendation")
    recommendation_label_zh = recommendation_label(recommendation)
    event_rule_status = "normal"

    base_reasons = [
        display_reason_text(data, reason)
        for reason in result.get("reasons", [])[:4]
    ]

    # 父事件已经有运行时观察到的子选项时，过滤掉“无直接收益”的旧提示。
    if is_parent_event:
        blocked_reason_parts = [
            "暂未识别到明确的卡牌或资源收益",
            "当前缺少可计算收益规则",
            "暂时无法计算实际收益",
        ]
        base_reasons = [
            reason
            for reason in base_reasons
            if not any(part in reason for part in blocked_reason_parts)
        ]

    if not known:
        event_rule_status = "missing_event"
        recommendation = "Low Value"
        recommendation_label_zh = "事件数据缺失"
        base_reasons.insert(
            0,
            f"事件数据缺失：这个事件没有在 events.json 中找到，应该已经记录到 {MISSING_EVENTS_PATH}，当前无法计算卡池、核心命中率或资源收益。",
        )
    elif is_parent_event:
        event_rule_status = "parent_event"

        recommendation = result.get("recommendation")
        if has_skill_reward and recommendation == "Low Value":
            recommendation = "Medium Value"

        recommendation_label_zh = recommendation_label(recommendation)

        reason_text = parent_event_reason_text(child_options)
        if has_skill_reward:
            reason_text += "；检测到技能收益，最低按“可以考虑”处理。"

        base_reasons.insert(0, reason_text)

    elif not has_value_rule:
        event_rule_status = "known_without_value_rule"
        recommendation = "Low Value"
        description_text = event_description_text(event_data)
        if description_text:
            recommendation_label_zh = "已识别"
            reason_text = f"描述：{description_text}"
        elif event_data and event_data.get("event_category") in {"utility_events", "unknown_events"}:
            recommendation_label_zh = "已识别，数据不足"
            reason_text = "事件已识别，但当前数据源尚未包含描述、可计算奖励或后续选项。"
        else:
            recommendation_label_zh = "已识别，暂无收益规则"
            reason_text = "事件已识别，但当前没有可计算奖励或后续选项。"
        base_reasons.insert(
            0,
            reason_text,
        )
    elif has_skill_reward and recommendation == "Low Value":
        event_rule_status = "skill_reward"
        recommendation = "Medium Value"
        recommendation_label_zh = recommendation_label(recommendation)
        base_reasons.insert(
            0,
            "检测到技能收益事件，最低按“可以考虑”处理。",
        )
    elif recommendation == "Low Value":
        event_rule_status = "normal_low_value"
        base_reasons.insert(
            0,
            "有可计算收益规则，但当前阵容下命中核心卡、可选卡或有效收益较低，所以显示为低收益事件。",
        )

    pool_stats = result.get("pool_stats", {})
    followup_summary = result.get("followup_value_summary") or {}
    if not isinstance(followup_summary, dict):
        followup_summary = {}

    followup_stats = followup_summary.get("pool_stats") or {}
    if not isinstance(followup_stats, dict):
        followup_stats = {}

    followup_resource_rewards = followup_summary.get("resource_rewards") or {}
    if not isinstance(followup_resource_rewards, dict):
        followup_resource_rewards = {}

    best_followup = result.get("best_followup") or followup_summary.get("best_followup")
    shop_pool_summary = result.get("shop_pool_summary")
    if not isinstance(shop_pool_summary, dict):
        shop_pool_summary = {}
    shop_rule_display = shop_rule_display_from_result(result, data)

    return {
        "event_name": event_name,
        "event_display_name": zh_name(data, event_name),
        "known": known,
        "has_value_rule": has_value_rule,
        "event_rule_status": event_rule_status,
        "recommendation": recommendation,
        "recommendation_label": recommendation_label_zh,
        "notes": "",
        "reasons": base_reasons,
        "priority_cards": [
            {
                **card,
                "display_name": zh_name(data, card.get("name")),
                "role_label_zh": role_label(card.get("role")),
            }
            for card in priority_cards(result.get("possible_cards", []))
        ],
        "alt_core_card_count": int(result.get("alt_core_card_count") or 0),
        "alt_core_build_hits": [
            {
                **hit,
                "card_display_name": zh_name(data, hit.get("card_name")),
            }
            for hit in result.get("alt_core_build_hits", [])
        ],
        "owned_target_hits": [
            {
                **card,
                "display_name": zh_name(data, card.get("name")),
                "role_label_zh": role_label(card.get("role")),
                "tier": tier_label(card.get("tier")),
            }
            for card in result.get("owned_target_hits", [])[:6]
        ],
        "resource_rewards": result.get("resource_rewards", {}),
        "child_options": child_options,
        "best_followup": best_followup,
        "best_followup_display": zh_name(data, best_followup),
        "best_followup_summary": {
            "recommendation": followup_summary.get("followup_recommendation_level"),
            "recommendation_label": recommendation_label(
                followup_summary.get("followup_recommendation_level")
            ),
            "resource_rewards": followup_resource_rewards,
            "resource_reward_text": format_resource_rewards(followup_resource_rewards),
            "candidate_cards": int(followup_stats.get("total_pool_count") or 0),
            "build_relevant_cards": int(followup_stats.get("valuable_count") or 0),
            "expected_relevant": round(
                float(followup_stats.get("expected_valuable_in_shop") or 0.0),
                2,
            ),
            "prob_relevant": round(
                float(followup_stats.get("prob_valuable_in_shop") or 0.0),
                4,
            ),
            "prob_core": round(
                float(followup_stats.get("prob_core_in_shop") or 0.0),
                4,
            ),
            "expected_sell_gold": round(
                float(followup_stats.get("expected_sell_gold") or 0.0),
                2,
            ),
        },
        "parent_event_observed_count": int(parent_graph.get("observed_count", 0)) if isinstance(parent_graph, dict) else 0,
        "pool_stats": {
            "candidate_cards": int(pool_stats.get("total_pool_count", 0)),
            "build_relevant_cards": int(pool_stats.get("valuable_count", 0)),
            "expected_relevant_in_shop": round(
                float(pool_stats.get("expected_valuable_in_shop", 0.0)),
                2,
            ),
            "prob_relevant_in_shop": round(
                float(pool_stats.get("prob_valuable_in_shop", 0.0)),
                4,
            ),
            "prob_core_in_shop": round(float(pool_stats.get("prob_core_in_shop", 0.0)), 4),
            "expected_sell_gold": round(float(pool_stats.get("expected_sell_gold", 0.0)), 2),
        },
        "shop_pool_summary": {
            "cache_status": shop_pool_summary.get("cache_status"),
            "cache_status_label": SHOP_CACHE_STATUS_LABELS.get(
                str(shop_pool_summary.get("cache_status") or ""),
                shop_pool_summary.get("cache_status"),
            ),
            "merchant_name": zh_name(data, shop_pool_summary.get("merchant_name")),
            "hero": shop_pool_summary.get("hero"),
            "stage": shop_pool_summary.get("stage"),
            "stage_label": SHOP_POOL_STAGE_LABELS.get(
                str(shop_pool_summary.get("stage") or ""),
                shop_pool_summary.get("stage"),
            ),
            "pool_count": int(shop_pool_summary.get("pool_count") or 0),
            "avg_price": shop_pool_summary.get("avg_price"),
            "base_refresh_cost": shop_pool_summary.get("base_refresh_cost"),
            "base_refresh_count": shop_pool_summary.get("base_refresh_count"),
            "refresh_enabled": shop_pool_summary.get("refresh_enabled"),
            "appearance_days": shop_pool_summary.get("appearance_days", []),
            "available_on_day": shop_pool_summary.get("available_on_day"),
        },
        "shop_entry_analysis": result.get("shop_entry_analysis"),
        "shop_inside_analysis": result.get("shop_inside_analysis"),
        "shop_entry_analysis_display": shop_rule_display if shop_rule_display.get("phase") == "entry" else {},
        "shop_inside_analysis_display": shop_rule_display if shop_rule_display.get("phase") == "inside" else {},
        "shop_rule_display": shop_rule_display,
    }


def analyze_payload(
    data: dict[str, Any],
    payload: dict[str, Any],
    *,
    build_override: str | None = None,
    include_ai: bool = False,
    top: int | None = None,
) -> dict[str, Any]:
    cache_signature = state_signature(payload)
    guide_marker = guide_cache_marker() if include_ai else ""
    analysis_cache_key = (id(data), cache_signature, build_override or "", top, "")
    cache_key = (id(data), cache_signature, build_override or "", top, guide_marker)
    render_signature = f"{cache_signature}:{build_override or ''}:{top or ''}"
    payload_warnings = runtime_state_age_warnings(payload)
    if analysis_cache_key in ANALYSIS_CACHE:
        cached = dict(ANALYSIS_CACHE[analysis_cache_key])
        cached["cache_hit"] = True
        if include_ai and attach_cached_ai_analysis(cached, cache_key):
            return cached
        if include_ai:
            # Fall through only when this cached entry was produced before AI payload caching existed.
            pass
        else:
            return cached

    observation_warning: str | None = None
    try:
        auto_observe_event_graph(data, payload)
    except Exception as exc:  # noqa: BLE001 - observation failure must not block analysis.
        observation_warning = f"事件关系观察记录更新失败：{exc}"

    normalized = normalize_payload_for_analysis(data, payload, build_override)
    if not str(normalized.get("hero") or "").strip() or int(normalized.get("day") or 0) <= 0:
        response = {
            "state": {
                "source": normalized.get("source", "runtime"),
                "hero": normalized.get("hero"),
                "build": normalized.get("build"),
                "build_display_name": normalized.get("build"),
                "build_detail": None,
                "build_options": [],
                "day": int(normalized.get("day") or 0),
                "game_stage": "",
                "game_stage_display": "",
                "event_options": normalized.get("event_options", []),
                "event_options_display": [],
                "missing_events": [],
                "owned_cards": normalized.get("owned_cards", []),
                "visible_cards": normalized.get("visible_cards", []),
                "owned_items": normalized.get("owned_items", []),
                "board_items": normalized.get("board_items", []),
                "stash_items": normalized.get("stash_items", []),
                "skills": normalized.get("skills", []),
                "current_events": normalized.get("current_events", []),
                "current_shop": normalized.get("current_shop"),
                "effective_shop": normalized.get("effective_shop"),
                "current_reward_options": normalized.get("current_reward_options", []),
                "gold": normalized.get("gold"),
                "health": normalized.get("combat_health", normalized.get("health")),
                "prestige": normalized.get("prestige"),
                "max_prestige": normalized.get("max_prestige"),
                "income": normalized.get("income"),
                "level": normalized.get("level"),
                "xp": normalized.get("xp"),
                "inventory_slots_used": normalized.get("inventory_slots_used"),
                "inventory_slots_total": normalized.get("inventory_slots_total"),
            },
            "recommendations": [],
            "warnings": [
                *payload_warnings,
                "等待游戏内实时状态。进入一局后会开始分析。",
            ],
            "build_analysis": {},
            "state_signature": cache_signature,
            "analysis_signature": render_signature,
            "cache_hit": False,
        }
        remember_analysis_cache(analysis_cache_key, response)
        return response

    state = GameState.from_dict(normalized)
    missing_events = [
        event_name
        for event_name in state.event_options
        if event_name not in data["events"]
    ]
    if missing_events:
        record_missing_events(missing_events, state, payload)
    result = analyze_game_state(data, state, top=top)
    actual_candidates: list[dict[str, Any]] = []
    if isinstance(state.current_shop, dict):
        visible_items = state.current_shop.get("visible_items")
        if isinstance(visible_items, list):
            actual_candidates.extend(
                item for item in visible_items if isinstance(item, dict)
            )
    if isinstance(state.current_reward_options, list):
        actual_candidates.extend(state.current_reward_options)
    deduplicated_candidates: list[dict[str, Any]] = []
    seen_candidates: set[str] = set()
    for candidate in actual_candidates:
        identity = str(
            candidate.get("id")
            or candidate.get("template_id")
            or candidate.get("name")
            or ""
        )
        if not identity or identity in seen_candidates:
            continue
        seen_candidates.add(identity)
        deduplicated_candidates.append(candidate)
    build_analysis = analyze_stage_builds(
        data=data,
        hero=state.hero,
        day=state.day,
        owned_cards=set(state.owned_cards),
        candidates=deduplicated_candidates,
        gold=state.gold,
        prestige=state.prestige,
        inventory_slots_used=state.inventory_slots_used,
        inventory_slots_total=state.inventory_slots_total,
        current_shop=state.effective_shop,
    )
    for match in build_analysis.get("build_matches", []):
        match["phase_label"] = stages_label(match.get("applicable_stages")) or STAGE_LABELS_ZH.get(
            match.get("phase"),
            match.get("phase") or "",
        )
        match["importance_label"] = importance_label(match.get("importance"))
        match["relation_label"] = relation_label(match.get("relation"))
        match["owned_core_display"] = display_name_list(data, match.get("owned_core"))
        match["missing_core_display"] = display_name_list(data, match.get("missing_core"))
        match["owned_optional_display"] = display_name_list(data, match.get("owned_optional"))
        match["reasons"] = [
            zh_text(data, reason)
            for reason in match.get("reasons", [])
        ]
    for candidate in build_analysis.get("candidate_cards", []):
        candidate["card_display_name"] = zh_name(
            data, candidate.get("card_name")
        )
        candidate["reasons"] = [
            zh_text(data, reason)
            for reason in candidate.get("reasons", [])
        ]
        candidate["risks"] = [
            zh_text(data, risk)
            for risk in candidate.get("risks", [])
        ]
        candidate["importance_label"] = importance_label(candidate.get("importance"))
        candidate["recommendation_type_label"] = shop_recommendation_label(
            candidate.get("recommendation_type")
        )
        for hit in candidate.get("build_hits", []):
            hit["build_display_name"] = hit.get("build_name") or hit.get("build_id") or ""
            hit["build_phase_label"] = stages_label(hit.get("applicable_stages")) or STAGE_LABELS_ZH.get(
                hit.get("build_phase"),
                hit.get("build_phase") or "",
            )
            hit["role_label"] = role_label(hit.get("role"))
            hit["relation_label"] = relation_label(hit.get("relation"))
    if build_analysis.get("shop_action"):
        build_analysis["shop_action_label"] = shop_action_label(
            build_analysis.get("shop_action")
        )
    apply_shop_rule_display_to_build_analysis(
        build_analysis,
        result.recommendations,
        data,
    )
    for bundle in build_analysis.get("visible_core_bundles", []):
        bundle["candidate_core_cards_display"] = [
            zh_name(data, name)
            for name in bundle.get("candidate_core_cards", [])
        ]
        bundle["owned_core_before_display"] = display_name_list(
            data,
            bundle.get("owned_core_before"),
        )
        bundle["owned_core_after_if_bought_display"] = display_name_list(
            data,
            bundle.get("owned_core_after_if_bought"),
        )
        bundle["reasons"] = [
            zh_text(data, reason)
            for reason in bundle.get("reasons", [])
        ]
        bundle["importance_label"] = importance_label(bundle.get("importance"))
        bundle["recommendation_label"] = shop_recommendation_label(
            bundle.get("recommendation")
        )
    owned_items_display, skills_display = displayed_owned_groups(data, state)

    response: dict[str, Any] = {
        "state": {
            "source": state.source,
            "hero": state.hero,
            "build": state.build,
            "build_display_name": (
                data.get("builds", {}).get(state.build, {}).get("name")
                or data.get("builds", {}).get(state.build, {}).get("display_name")
                or state.build
            ),
            "build_detail": build_detail_for_state(data, state.build),
            "build_options": build_options_for_hero(data, state.hero),
            "day": state.day,
            "game_stage": get_game_stage_for_day(state.day),
            "game_stage_display": STAGE_LABELS_ZH.get(
                get_game_stage_for_day(state.day),
                get_game_stage_for_day(state.day),
            ),
            "event_options": state.event_options,
            "owned_cards": state.owned_cards,
            "owned_cards_display": display_card_names(data, state.owned_cards),
            "owned_items_display": owned_items_display,
            "skills_display": skills_display,
            "owned_card_enchantments": state.owned_card_enchantments,
            "visible_cards": state.visible_cards,
            "visible_cards_display": [
                {
                    "name": name,
                    "display_name": zh_name(data, name),
                }
                for name in state.visible_cards
            ],
            "gold": state.gold,
            "health": state.combat_health,
            "combat_health": state.combat_health,
            "prestige": state.prestige,
            "max_prestige": state.max_prestige,
            "income": state.income,
            "level": state.level,
            "xp": state.xp,
            "owned_items": state.owned_items,
            "board_items": state.board_items,
            "stash_items": state.stash_items,
            "skills": state.skills,
            "current_events": state.current_events,
            "current_shop": (
                {
                    **state.current_shop,
                    "visible_items": [
                        display_card_entry(data, item)
                        for item in state.current_shop.get("visible_items", [])
                        if isinstance(item, dict)
                    ],
                }
                if isinstance(state.current_shop, dict)
                else None
            ),
            "effective_shop": (
                {
                    **state.effective_shop,
                    "visible_items": [
                        display_card_entry(data, item)
                        for item in state.effective_shop.get("visible_items", [])
                        if isinstance(item, dict)
                    ],
                }
                if isinstance(state.effective_shop, dict)
                else None
            ),
            "current_reward_options": state.current_reward_options,
            "inventory_slots_used": state.inventory_slots_used,
            "inventory_slots_total": state.inventory_slots_total,
            "event_options_display": [
                {
                    "name": name,
                    "display_name": zh_name(data, name),
                    "known": name in data["events"],
                }
                for name in state.event_options
            ],
            "missing_events": [
                {
                    "name": name,
                    "display_name": zh_name(data, name),
                }
                for name in missing_events
            ],
        },
        "warnings": [*payload_warnings, *result.warnings],
        "recommendations": [
            summarize_recommendation(data, item)
            for item in result.recommendations
        ],
        "build_analysis": build_analysis,
        "state_signature": cache_signature,
        "analysis_signature": render_signature,
        "cache_hit": False,
    }

    if (
        response["recommendations"]
        or build_analysis.get("candidate_cards")
    ):
        ai_results: list[dict[str, Any]] = []
        ai_rule_recommendations = result.recommendations
        if top:
            ai_rule_recommendations = analyze_game_state(
                data,
                state,
                top=None,
            ).recommendations

        for raw_item in ai_rule_recommendations:
            display_item = summarize_recommendation(data, raw_item)
            item = dict(raw_item)

            # AI 使用展示层修正后的规则参考，但仍会拿到全部当前选项。
            item["recommendation"] = display_item.get(
                "recommendation",
                raw_item.get("recommendation"),
            )

            display_reasons = display_item.get("reasons", [])
            if isinstance(display_reasons, list) and display_reasons:
                item["reasons"] = display_reasons
            for field_name in (
                "event_rule_status",
                "child_options",
                "best_followup_summary",
                "best_followup_display",
                "event_display_name",
            ):
                if field_name in display_item:
                    item[field_name] = display_item[field_name]

            ai_results.append(item)

        guide_context = retrieve_guides_for_ai(
            data=data,
            state=state,
            build_analysis=build_analysis,
            recommendations=ai_results,
        )
        ai_payload = compact_recommendations(
            data=data,
            hero=state.hero,
            build_name=state.build,
            current_day=state.day,
            owned_cards=state.owned_cards,
            results=ai_results,
            current_gold=state.gold,
            current_shop=state.effective_shop,
            build_analysis=build_analysis,
            guide_context=guide_context,
            state_context=response["state"],
        )
        remember_ai_payload_cache(cache_key, ai_payload)

    if include_ai and (
        response["recommendations"]
        or build_analysis.get("candidate_cards")
    ):
        try:
            response["ai_analysis"] = analyze_with_ai(ai_payload)
            AI_ANALYSIS_CACHE[cache_key] = response["ai_analysis"]
        except Exception as exc:  # noqa: BLE001 - AI failure should not fail rule analysis.
            response["ai_error"] = str(exc)

    if observation_warning:
        response["warnings"] = [observation_warning, *response["warnings"]]

    if not include_ai:
        remember_analysis_cache(analysis_cache_key, response)

    return response


def simulate_current_combat_payload(
    data: dict[str, Any],
    payload: dict[str, Any],
    *,
    horizon_sec: float = 60.0,
    random_trials: int = 1,
) -> dict[str, Any]:
    normalized = normalize_payload_for_analysis(data, payload)
    state = GameState.from_dict(normalized)
    estimate = estimate_self_health_ttk(
        data,
        state,
        horizon_sec=max(1.0, min(180.0, float(horizon_sec or 60.0))),
        random_trials=max(1, min(20, int(random_trials or 1))),
    )
    combat = estimate.to_dict() if estimate is not None else None
    if combat is not None:
        horizon = float(combat.get("horizon_sec") or 0.0)
        total_damage = float(combat.get("total_damage") or 0.0)
        combat["damage_per_second"] = total_damage / horizon if horizon > 0 else 0.0
    return {
        "ok": estimate is not None,
        "combat": combat,
        "state": {
            "hero": state.hero,
            "build": state.build,
            "day": state.day,
            "health": state.combat_health,
            "combat_health": state.combat_health,
            "board_item_count": len(state.board_items or []),
            "owned_item_count": len(state.owned_items or []),
        },
        "state_signature": state_signature(payload),
    }


def record_missing_events(
    event_names: list[str],
    state: GameState,
    payload: dict[str, Any],
) -> None:
    try:
        existing = json.loads(MISSING_EVENTS_PATH.read_text(encoding="utf-8-sig")) if MISSING_EVENTS_PATH.exists() else {}
    except json.JSONDecodeError:
        existing = {}

    if not isinstance(existing, dict):
        existing = {}

    for name in event_names:
        item = existing.get(name, {})
        count = int(item.get("count", 0)) + 1 if isinstance(item, dict) else 1
        existing[name] = {
            "name": name,
            "count": count,
            "last_seen_hero": state.hero,
            "last_seen_day": state.day,
            "last_seen_source": state.source,
            "raw_event_options": payload.get("event_options", []),
            "raw_event_option_ids": payload.get("event_option_ids", []),
            "raw_event_option_template_ids": payload.get("event_option_template_ids", []),
            "raw_event_options_detailed": payload.get("event_options_detailed", []),
        }

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    MISSING_EVENTS_PATH.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class BazaarHandler(BaseHTTPRequestHandler):
    data = load_all_data(DATA_DIR)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        try:
            if parsed.path in {"/", "/health"}:
                self.send_json(
                    {
                        "ok": True,
                        "mode": "api-only",
                        "message": "Browser UI is deprecated. Use the in-game overlay.",
                        "analysis_endpoint": "/api/analysis",
                        "options_endpoint": "/api/options",
                        "state_signature_endpoint": "/api/state-signature",
                    }
                )
                return
            if parsed.path == "/api/state":
                payload, path = load_runtime_payload()
                self.send_json({"path": str(path), "payload": payload})
                return
            if parsed.path == "/api/state-signature":
                payload, path = load_runtime_payload()
                self.send_json(
                    {
                        "path": str(path),
                        "state_signature": state_signature(payload),
                    }
                )
                return
            if parsed.path == "/api/options":
                hero = query.get("hero", [None])[0]
                build_options = build_options_for_hero(self.data, hero)
                self.send_json(
                    {
                        "heroes": available_heroes(self.data),
                        "builds": [build["id"] for build in build_options],
                        "build_options": build_options,
                        "events": sorted(self.data["events"].keys()),
                    }
                )
                return
            if parsed.path == "/api/update/status":
                if query.get("refresh", ["0"])[0] == "1":
                    start_background_update_check(force=True)
                self.send_json(get_update_status(wait_seconds=3.0))
                return
            if parsed.path == "/api/update/candidates":
                self.send_json({"candidates": find_update_package_candidates()})
                return
            if parsed.path == "/api/analysis":
                payload, path = load_runtime_payload()
                response = analyze_payload(
                    self.data,
                    payload,
                    build_override=query.get("build", [None])[0],
                    include_ai=query.get("ai", ["0"])[0] == "1",
                    top=_optional_int(query.get("top", [None])[0]),
                )
                response["state_path"] = str(path)
                self.send_json(response)
                return
            if parsed.path == "/api/combat-simulation":
                payload, path = load_runtime_payload()
                response = simulate_current_combat_payload(
                    self.data,
                    payload,
                    horizon_sec=_optional_float(query.get("duration", [None])[0], 60.0),
                    random_trials=_optional_int(query.get("trials", [None])[0]) or 1,
                )
                response["state_path"] = str(path)
                self.send_json(response)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except Exception as exc:  # noqa: BLE001 - this is a small local dev server.
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/update/"):
            self.handle_update_post(parsed.path)
            return
        if parsed.path != "/api/state":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        query = parse_qs(parsed.query)
        if query.get("force", ["0"])[0] != "1" and runtime_state_is_plugin_owned():
            self.send_json(
                {
                    "error": "当前状态由 BepInEx 插件维护，网页不能覆盖。"
                    "如需手动替换，请显式使用 /api/state?force=1。"
                },
                status=HTTPStatus.CONFLICT,
            )
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw_body)
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except json.JSONDecodeError as exc:
            self.send_json({"error": f"JSON 格式无效：{exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        self.send_json({"ok": True, "path": str(STATE_PATH)})

    def handle_update_post(self, path: str) -> None:
        try:
            if path == "/api/update/check":
                start_background_update_check(force=True)
                self.send_json(get_update_status())
                return
            if path == "/api/update/dismiss":
                self.send_json(dismiss_update_prompt())
                return
            if path == "/api/update/open-download":
                open_download_page()
                self.send_json({"ok": True})
                return
            if path == "/api/update/select-package":
                package = select_update_package_with_dialog()
                self.send_json({"ok": True, "package": package.to_dict()})
                return
            if path == "/api/update/install":
                payload = self.read_json_body()
                package_path = str(payload.get("path", "")).strip()
                if not package_path:
                    raise UpdateError("缺少更新包路径。")
                launch_update_install(Path(package_path), expected_update_info())
                self.send_json({"ok": True, "message": "更新器已启动，程序即将退出并安装新版。"})
                return

            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
        except UpdateError as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001 - local update endpoint should report errors.
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise UpdateError(f"JSON 格式无效：{exc}") from exc
        if not isinstance(payload, dict):
            raise UpdateError("请求体必须是 JSON 对象。")
        return payload

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_float(value: str | None, fallback: float) -> float:
    if value in (None, ""):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local The Bazaar AI helper service.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--api-only",
        action="store_true",
        help="Deprecated compatibility flag. The service is always API-only.",
    )
    return parser.parse_args()


def local_bind_candidates(host: str) -> list[str]:
    requested = (host or "").strip() or "127.0.0.1"
    candidates: list[str] = []

    def add(candidate: str) -> None:
        if candidate not in candidates:
            candidates.append(candidate)

    add(requested)
    if requested == "127.0.0.1":
        add("localhost")
    elif requested == "localhost":
        add("127.0.0.1")
    return candidates


def local_probe_candidates(host: str) -> list[str]:
    requested = (host or "").strip() or "127.0.0.1"
    if requested in {"0.0.0.0", "::"}:
        return ["127.0.0.1", "localhost"]
    return local_bind_candidates(requested)


def existing_helper_is_healthy(host: str, port: int, timeout_seconds: float = 0.5) -> bool:
    for candidate in local_probe_candidates(host):
        try:
            with urlopen(f"http://{candidate}:{port}/", timeout=timeout_seconds) as response:
                if response.status != HTTPStatus.OK:
                    continue
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
            continue

        if (
            isinstance(payload, dict)
            and payload.get("ok") is True
            and payload.get("mode") == "api-only"
            and payload.get("analysis_endpoint") == "/api/analysis"
        ):
            return True
    return False


def create_http_server(
    host: str,
    port: int,
    server_class: type[ThreadingHTTPServer] = BazaarHTTPServer,
    health_check: Any = existing_helper_is_healthy,
) -> tuple[ThreadingHTTPServer, str]:
    permission_errors: list[tuple[str, PermissionError]] = []
    address_errors: list[tuple[str, OSError]] = []
    for candidate in local_bind_candidates(host):
        try:
            return server_class((candidate, port), BazaarHandler), candidate
        except PermissionError as exc:
            if health_check(candidate, port):
                raise SystemExit(
                    f"BazaarHelper is already running at http://{candidate}:{port}"
                ) from exc
            permission_errors.append((candidate, exc))
        except OSError as exc:
            if getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) in {48, 98}:
                if health_check(candidate, port):
                    raise SystemExit(
                        f"BazaarHelper is already running at http://{candidate}:{port}"
                    ) from exc
            address_errors.append((candidate, exc))

    if permission_errors:
        tried_hosts = ", ".join(candidate for candidate, _ in permission_errors)
        message = (
            f"Unable to start BazaarHelper on {tried_hosts}:{port} because Windows denied the bind.\n"
            "This is usually caused by a reserved/excluded TCP port or security software blocking local servers.\n"
            f"Please check whether port {port} is reserved or blocked, then start BazaarHelper again."
        )
        raise SystemExit(message) from permission_errors[0][1]

    if address_errors:
        tried_hosts = ", ".join(candidate for candidate, _ in address_errors)
        message = (
            f"Unable to start BazaarHelper on {tried_hosts}:{port} because the address is already in use.\n"
            "If the game overlay is already working, BazaarHelper is probably already running.\n"
            f"Otherwise, close the program using port {port} and start BazaarHelper again."
        )
        raise SystemExit(message) from address_errors[0][1]

    raise RuntimeError("No bind candidates were available.")


def main() -> None:
    args = parse_args()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    if existing_helper_is_healthy(args.host, args.port):
        print(f"BazaarHelper is already running at http://{args.host}:{args.port}")
        return
    start_background_update_check()
    server, bound_host = create_http_server(args.host, args.port)
    print(f"The Bazaar AI helper API service: http://{bound_host}:{args.port}")
    print(f"Runtime state file: {STATE_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
