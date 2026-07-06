from __future__ import annotations

import argparse
import hashlib
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import re

from recommender import get_card_role_for_build
from advisor import analyze_game_state
from ai_advisor import analyze_with_ai, compact_recommendations
from build_strategy import applicable_build_names, build_applies_to_day, get_game_stage_for_day
from data_loader import load_all_data
from game_state import GameState
from app_paths import get_app_root, get_runtime_dir
from stage_build_matcher import analyze_stage_builds


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
VOLATILE_STATE_KEYS = {
    "updated_at_utc",
    "captured_at_utc",
    "timestamp",
    "last_updated",
    "frame",
    "frame_count",
}
ANALYSIS_CACHE: dict[tuple[int, str, str, int | None], dict[str, Any]] = {}
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
MAX_STATE_AGE_SECONDS = 15.0

def load_runtime_payload() -> tuple[dict[str, Any], Path]:
    if not STATE_PATH.exists():
        raise FileNotFoundError(
            f"实时状态文件不存在：{STATE_PATH}。请确认游戏和 Bazaar State Exporter 已启动。"
        )
    for attempt in range(3):
        try:
            payload = json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
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


def remember_analysis_cache(
    cache_key: tuple[int, str, str, int | None],
    response: dict[str, Any],
) -> None:
    if len(ANALYSIS_CACHE) >= ANALYSIS_CACHE_MAX_ENTRIES:
        oldest_key = next(iter(ANALYSIS_CACHE))
        ANALYSIS_CACHE.pop(oldest_key, None)
    ANALYSIS_CACHE[cache_key] = response


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
                "在配置真实阵容前，不会把任何卡视为核心、过渡或可选。"
            ],
            "core_cards": [],
            "transition_cards": [],
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
    if normalized.get("inventory_slots_used") is None:
        normalized["inventory_slots_used"] = inventory_slots_used(
            data, normalized.get("board_items")
        )
    current_shop = normalized.get("current_shop")
    if isinstance(current_shop, dict):
        current_shop = dict(current_shop)
        current_shop["visible_items"] = normalize_card_entries(
            data, current_shop.get("visible_items")
        )
        normalized["current_shop"] = current_shop
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


def inventory_slots_used(data: dict[str, Any], board_items: Any) -> int | None:
    if not isinstance(board_items, list):
        return None

    size_slots = {"small": 1, "medium": 2, "large": 3}
    total = 0
    for item in board_items:
        if not isinstance(item, dict) or not item.get("name"):
            return None
        card_data = data.get("cards", {}).get(str(item["name"]))
        if not isinstance(card_data, dict):
            return None
        slots = size_slots.get(str(card_data.get("size", "")).lower())
        if slots is None:
            return None
        total += slots
    return total


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
        return str(description["Text"])

    return str(card.get("InternalDescription") or "")


def extract_resource_rewards_from_card(card: dict[str, Any]) -> dict[str, Any]:
    rewards: dict[str, Any] = {}

    abilities = card.get("Abilities", {})
    if not isinstance(abilities, dict):
        return rewards

    for ability in abilities.values():
        if not isinstance(ability, dict):
            continue

        action = ability.get("Action", {})
        if not isinstance(action, dict):
            continue

        if action.get("$type") != "TActionPlayerModifyAttribute":
            continue

        attribute = str(action.get("AttributeType") or "").lower()

        value_obj = action.get("Value", {})
        value = value_obj.get("Value") if isinstance(value_obj, dict) else None

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
            child_item = {
                "name": child.get("event_name") or child.get("name") or "",
                "source_id": source_id,
                "kind": child.get("kind"),
                "card_type": child.get("card_type"),
                "seen": True,
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

    card_id_index = {
        str(card_data.get("id")).lower(): card_name
        for card_name, card_data in data["cards"].items()
        if card_data.get("id")
    }
    normalized = []
    for entry in entries:
        if not isinstance(entry, dict):
            normalized.append(entry)
            continue

        item = dict(entry)
        if not item.get("name"):
            card_id = item.get("template_id") or item.get("id")
            if card_id:
                item["name"] = card_id_index.get(str(card_id).lower(), "")
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


def build_detail_for_state(data: dict[str, Any], build_name: str) -> dict[str, Any]:
    build_data = data.get("builds", {}).get(build_name, {})
    if not isinstance(build_data, dict):
        build_data = {}

    return {
        "id": build_name,
        "display_name": (
            build_data.get("name")
            or build_data.get("display_name")
            or build_name
        ),
        "core_cards": display_build_card_names(data, build_data.get("core_cards", [])),
        "transition_cards": display_build_card_names(
            data,
            build_data.get("transition_cards", []),
        ),
        "optional_cards": display_build_card_names(
            data,
            build_data.get("optional_cards", []),
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
        "transition": "过渡",
        "optional": "可选",
        "unrelated": "无关",
    }.get(role or "", role or "")


def recommendation_label(label: str | None) -> str:
    return {
        "High Value": "优先选择",
        "Medium Value": "可以考虑",
        "Low Value": "优先级低",
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


def summarize_parent_child_options(parent_graph: dict[str, Any] | None) -> list[dict[str, Any]]:
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

        name = child.get("name") or ""
        if not name:
            source_id = str(child.get("source_id") or "")
            name = f"未知子选项 {source_id[:8]}" if source_id else "未知子选项"

        result.append(
            {
                "name": name,
                "source_id": child.get("source_id", ""),
                "kind": child.get("kind", ""),
                "card_type": child.get("card_type", ""),
                "description": child.get("description", ""),
                "resource_rewards": resource_rewards,
                "reward_text": reward_text,
                "unresolved": bool(child.get("unresolved", False)),
                "count": int(child.get("count", 0)),
            }
        )

    return result


def parent_event_reason_text(child_options: list[dict[str, Any]]) -> str:
    if not child_options:
        return "这是一个父事件，但当前还没有观察到可分析的子选项收益。"

    parts: list[str] = []

    for child in child_options[:5]:
        name = child.get("name") or "未知子选项"
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

def event_has_value_rule(event_data: dict[str, Any] | None) -> bool:
    """判断一个已识别事件是否有可计算收益规则。"""
    if not event_data:
        return False
    
    if event_has_skill_reward(event_data):
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
    child_options = summarize_parent_child_options(parent_graph)
    is_parent_event = bool(child_options)
    has_skill_child_reward = child_options_have_skill_reward(child_options)

    known = bool(event_name) and event_name in data["events"]
    has_value_rule = event_has_value_rule(event_data) if known else False
    has_skill_reward = (event_has_skill_reward(event_data) if known else False) or has_skill_child_reward

    recommendation = result.get("recommendation")
    recommendation_label_zh = recommendation_label(recommendation)
    event_rule_status = "normal"

    base_reasons = [
        zh_text(data, reason)
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
        recommendation_label_zh = "已识别，暂无收益规则"
        base_reasons.insert(
            0,
            "事件已识别，但当前缺少可计算收益规则：events.json 中有这个事件，但没有 shop_pool、card_reward、resource_rewards 或 followup_options，所以暂时无法计算实际收益。",
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
            "有可计算收益规则，但当前 Build 下命中核心卡、过渡卡或有效收益较低，所以显示为低收益事件。",
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
    }


def analyze_payload(
    data: dict[str, Any],
    payload: dict[str, Any],
    *,
    build_override: str | None = None,
    include_ai: bool = False,
    top: int | None = None,
) -> dict[str, Any]:
    cache_signature = analysis_cache_signature(payload)
    cache_key = (id(data), cache_signature, build_override or "", top)
    render_signature = f"{cache_signature}:{build_override or ''}:{top or ''}"
    if not include_ai and cache_key in ANALYSIS_CACHE:
        cached = dict(ANALYSIS_CACHE[cache_key])
        cached["cache_hit"] = True
        return cached

    observation_warning: str | None = None
    try:
        auto_observe_event_graph(data, payload)
    except Exception as exc:  # noqa: BLE001 - observation failure must not block analysis.
        observation_warning = f"observation graph update failed: {exc}"

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
            "warnings": ["等待游戏内实时状态。进入一局后会开始分析。"],
            "build_analysis": {},
            "analysis_signature": render_signature,
            "cache_hit": False,
        }
        remember_analysis_cache(cache_key, response)
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
        current_shop=state.current_shop,
    )
    for candidate in build_analysis.get("candidate_cards", []):
        candidate["card_display_name"] = zh_name(
            data, candidate.get("card_name")
        )
    for bundle in build_analysis.get("visible_core_bundles", []):
        bundle["candidate_core_cards_display"] = [
            zh_name(data, name)
            for name in bundle.get("candidate_core_cards", [])
        ]
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
        "warnings": result.warnings,
        "recommendations": [
            summarize_recommendation(data, item)
            for item in result.recommendations
        ],
        "build_analysis": build_analysis,
        "analysis_signature": render_signature,
        "cache_hit": False,
    }

    if include_ai and (
        response["recommendations"]
        or build_analysis.get("candidate_cards")
    ):
        ai_results: list[dict[str, Any]] = []

        for raw_item, display_item in zip(result.recommendations, response["recommendations"]):
            item = dict(raw_item)

            # AI 必须吃到 UI 展示层修正后的推荐等级。
            item["recommendation"] = display_item.get(
                "recommendation",
                raw_item.get("recommendation"),
            )

            # AI 也吃展示层修正后的理由，比如“检测到技能收益，最低按可以考虑处理”。
            display_reasons = display_item.get("reasons", [])
            if isinstance(display_reasons, list) and display_reasons:
                item["reasons"] = display_reasons

            ai_results.append(item)

        ai_payload = compact_recommendations(
            data=data,
            hero=state.hero,
            build_name=state.build,
            current_day=state.day,
            owned_cards=state.owned_cards,
            results=ai_results,
            current_gold=state.gold,
            current_shop=state.current_shop,
            build_analysis=build_analysis,
        )
        try:
            response["ai_analysis"] = analyze_with_ai(ai_payload)
        except RuntimeError as exc:
            response["ai_error"] = str(exc)

    if observation_warning:
        response["warnings"] = [observation_warning, *response["warnings"]]

    if not include_ai:
        remember_analysis_cache(cache_key, response)

    return response


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
            if parsed.path == "/":
                self.send_text(HTML_PAGE, content_type="text/html; charset=utf-8")
                return
            if parsed.path == "/api/state":
                payload, path = load_runtime_payload()
                self.send_json({"path": str(path), "payload": payload})
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

            self.send_error(HTTPStatus.NOT_FOUND, "未找到")
        except Exception as exc:  # noqa: BLE001 - this is a small local dev server.
            self.send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/state":
            self.send_error(HTTPStatus.NOT_FOUND, "未找到")
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

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text: str, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _optional_int(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


HTML_PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>The Bazaar AI 助手</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101214;
      --panel: #171b1f;
      --panel-2: #20262b;
      --line: #343c43;
      --text: #eef2f3;
      --muted: #9aa6ad;
      --accent: #78d6b5;
      --warn: #e4b860;
      --bad: #e07777;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: #13171a;
    }
    h1 { margin: 0; font-size: 20px; font-weight: 650; }
    button, select {
      border: 1px solid var(--line);
      background: var(--panel-2);
      color: var(--text);
      border-radius: 6px;
      padding: 8px 11px;
      font: inherit;
    }
    button { cursor: pointer; }
    button.primary { border-color: #4aa789; background: #1f4c40; }
    main {
      display: grid;
      grid-template-columns: minmax(300px, 360px) 1fr;
      min-height: calc(100vh - 70px);
    }
    aside {
      border-right: 1px solid var(--line);
      padding: 18px;
      background: var(--panel);
    }
    section { padding: 18px 22px; }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
    }
    .state-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin: 14px 0;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: var(--panel-2);
      min-height: 68px;
    }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 3px;
    }
    .metric-button {
      width: 100%;
      text-align: left;
      cursor: pointer;
    }
    .metric-button:focus-visible {
      outline: 2px solid var(--accent);
      outline-offset: 2px;
    }
    .build-detail {
      display: none;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #111518;
      padding: 10px;
      margin: 10px 0 14px;
    }
    .build-detail.open { display: block; }
    .build-detail h3 {
      margin: 10px 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .build-detail h3:first-child { margin-top: 0; }
    .build-detail .list { margin-bottom: 8px; }
    .tag-row {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .tag {
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      padding: 2px 7px;
      font-size: 12px;
    }
    .panel-title {
      margin: 18px 0 8px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 650;
    }
    .list {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
    }
    .list-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #111518;
      padding: 9px 10px;
      min-height: 40px;
    }
    .list-item small {
      color: var(--muted);
      white-space: nowrap;
    }
    .list-item span {
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .event-list {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 12px;
    }
    .event {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
      padding: 14px;
    }
    .event h2 {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin: 0 0 8px;
      font-size: 17px;
    }
    .badge {
      white-space: nowrap;
      color: #08110e;
      background: var(--accent);
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 650;
    }
    .badge.medium { background: var(--warn); }
    .badge.low { background: var(--bad); color: #170808; }
    .muted { color: var(--muted); }
    ul { padding-left: 18px; margin: 8px 0; }
    .ai {
      border-left: 3px solid var(--accent);
      background: #111b18;
      padding: 12px;
      margin-bottom: 14px;
      white-space: pre-wrap;
    }
    .error {
      border-left: 3px solid var(--bad);
      background: #211414;
      padding: 12px;
      margin-bottom: 14px;
      color: #ffdede;
    }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
    }
  </style>
</head>
<body>
  <header>
    <h1>The Bazaar AI 助手</h1>
    <div class="toolbar">
      <select id="buildSelect"></select>
      <button id="refreshBtn">刷新</button>
      <button id="aiBtn" class="primary">AI 分析</button>
    </div>
  </header>
  <main>
    <aside>
      <div class="state-grid">
        <div class="metric"><span>英雄</span><strong id="hero">-</strong></div>
        <div class="metric"><span>天数</span><strong id="day">-</strong></div>
        <div class="metric"><span>金币</span><strong id="gold">-</strong></div>
        <div class="metric"><span>生命</span><strong id="health">-</strong></div>
        <div class="metric"><span>声望</span><strong id="prestige">-</strong></div>
        <div class="metric"><span>收入</span><strong id="income">-</strong></div>
      </div>
      <div class="metric metric-button" id="buildMetric" role="button" tabindex="0" aria-expanded="false">
        <span>阵容目标</span><strong id="build">-</strong><div class="muted" id="stage">-</div>
      </div>
      <div class="build-detail" id="buildDetail"></div>
      <h2 class="panel-title">当前事件</h2>
      <div class="list" id="currentEvents"></div>
      <h2 class="panel-title">已拥有物品</h2>
      <div class="list" id="ownedItems"></div>
      <h2 class="panel-title">已拥有技能</h2>
      <div class="list" id="ownedSkills"></div>
      <h2 class="panel-title">当前可见卡</h2>
      <div class="list" id="visibleCards"></div>
    </aside>
    <section>
      <div id="message"></div>
      <div id="aiBox"></div>
      <div class="event-list" id="events"></div>
    </section>
  </main>
  <script>
    const eventsEl = document.querySelector("#events");
    const messageEl = document.querySelector("#message");
    const aiBox = document.querySelector("#aiBox");
    const buildSelect = document.querySelector("#buildSelect");
    const buildMetric = document.querySelector("#buildMetric");
    const buildDetail = document.querySelector("#buildDetail");
    let aiRequestInFlight = false;
    let currentBuildIds = [];
    let currentOptionsHero = null;
    let buildDetailOpen = false;
    let analysisRequestInFlight = false;
    let lastRenderedSignature = null;

    function pct(value) {
      return `${Math.round((value || 0) * 100)}%`;
    }

    function badgeClass(label) {
      if (label === "Medium Value") return "badge medium";
      if (label === "Low Value") return "badge low";
      return "badge";
    }

    async function loadOptions(hero = null, preferredBuild = null) {
      const url = hero ? `/api/options?hero=${encodeURIComponent(hero)}` : "/api/options";
      const previousBuild = preferredBuild ?? buildSelect.value;
      const res = await fetch(url, { cache: "no-store" });
      const data = await res.json();
      currentBuildIds = data.builds || [];
      currentOptionsHero = hero || null;
      buildSelect.innerHTML = [
        `<option value="">自动匹配已有卡牌</option>`,
        ...(data.build_options || data.builds.map((id) => ({ id, name: id })))
          .map((build) => `<option value="${build.id}">${build.name}</option>`),
      ].join("");
      const savedBuild = localStorage.getItem("bazaar_selected_build");
      if (previousBuild && currentBuildIds.includes(previousBuild)) {
        buildSelect.value = previousBuild;
      } else if (savedBuild === "") {
        buildSelect.value = "";
      } else if (savedBuild && currentBuildIds.includes(savedBuild)) {
        buildSelect.value = savedBuild;
      } else {
        buildSelect.value = "";
      }
    }

    async function loadState() {
      const res = await fetch("/api/state", { cache: "no-store" });
      const data = await res.json();
      return data.payload;
    }

    async function analyze(includeAi = false) {
      if (!includeAi && document.hidden) return;
      if (!includeAi && analysisRequestInFlight) return;
      messageEl.innerHTML = "";
      if (includeAi) {
        aiRequestInFlight = true;
        aiBox.innerHTML = `<div class="ai">AI 分析中...</div>`;
      } else {
        analysisRequestInFlight = true;
      }

      const selectedBuild = buildSelect.value || "";
      const build = encodeURIComponent(selectedBuild);
      let data;
      try {
        const res = await fetch(
          `/api/analysis?top=3&build=${build}&ai=${includeAi ? "1" : "0"}`,
          { cache: "no-store" }
        );
        data = await res.json();
      } catch (error) {
        if (includeAi) {
          aiBox.innerHTML = `<div class="error">AI 请求失败：${error.message}</div>`;
          aiRequestInFlight = false;
        } else {
          messageEl.innerHTML = `<div class="error">刷新失败：${error.message}</div>`;
          analysisRequestInFlight = false;
        }
        return;
      }

      if (data.error) {
        messageEl.innerHTML = `<div class="error">${data.error}</div>`;
        if (includeAi) aiRequestInFlight = false;
        if (!includeAi) analysisRequestInFlight = false;
        return;
      }

      const responseSignature = data.analysis_signature || null;
      if (!includeAi && responseSignature && responseSignature === lastRenderedSignature) {
        analysisRequestInFlight = false;
        return;
      }
      if (responseSignature) lastRenderedSignature = responseSignature;

      const state = data.state || {};
      if (state.hero && currentOptionsHero !== state.hero) {
        await loadOptions(state.hero, selectedBuild);
      } else if (selectedBuild && !currentBuildIds.includes(selectedBuild)) {
        buildSelect.value = "";
        localStorage.setItem("bazaar_selected_build", "");
      }
      document.querySelector("#hero").textContent = state.hero || "-";
      document.querySelector("#day").textContent = state.day || "-";
      document.querySelector("#gold").textContent = state.gold ?? "-";
      document.querySelector("#health").textContent = state.health ?? "-";
      document.querySelector("#prestige").textContent = formatPrestige(state);
      document.querySelector("#income").textContent = state.income ?? "-";
      document.querySelector("#build").textContent = state.build_display_name || state.build || "-";
      document.querySelector("#stage").textContent = state.game_stage_display || state.game_stage || "-";
      if (selectedBuild && state.build && currentBuildIds.includes(state.build)) {
        buildSelect.value = state.build;
      }
      renderBuildDetail(state.build_detail || null);
      renderStateLists(state);

      if (data.warnings && data.warnings.length) {
        messageEl.innerHTML = `<div class="error">${data.warnings.join("<br>")}</div>`;
      }

      if (data.ai_analysis) {
        aiBox.innerHTML = `<div class="ai">${data.ai_analysis}</div>`;
      } else if (data.ai_error) {
        aiBox.innerHTML = `<div class="error">${data.ai_error}</div>`;
      }
      if (includeAi) {
        aiRequestInFlight = false;
      } else {
        analysisRequestInFlight = false;
      }

      const eventCards = (data.recommendations || []).map((item) => {
        const stats = item.pool_stats || {};
        const cards = (item.priority_cards || []).map((card) =>
          `<li>${card.display_name || card.name} <span class="muted">${card.tier || ""} ${card.role_label_zh || card.role || ""}</span></li>`
        ).join("");
        const ownedHits = (item.owned_target_hits || []).map((card) =>
          `<li>${card.display_name || card.name} <span class="muted">${card.tier || ""} ${card.role_label_zh || card.role || ""}${card.can_upgrade ? " · 可升级" : ""}${card.enchantments && card.enchantments.length ? " · " + card.enchantments.join(", ") : ""}</span></li>`
        ).join("");
        const childOptions = (item.child_options || []).map((child) => {
        const reward = child.reward_text ? ` <span class="muted">${child.reward_text}</span>` : "";
        const desc = child.description && !child.reward_text ? ` <span class="muted">${child.description}</span>` : "";
        const unresolved = child.unresolved ? ` <span class="muted">未完全解析</span>` : "";
        return `<li>${child.name || "未知子选项"}${reward}${desc}${unresolved}</li>`;
        }).join("");
        const reasons = (item.reasons || []).map((reason) => `<li>${reason}</li>`).join("");
        const altCoreHits = (item.alt_core_build_hits || []).map((hit) => {
          const buildNames = (hit.builds || []).map((build) =>
            build.display_name || build.build_name
          ).filter(Boolean).join("、");
          return `<li>${hit.card_display_name || hit.card_name}：${buildNames || "其他阵容"}核心卡</li>`;
        }).join("");
        const sellGold = Number(stats.expected_sell_gold || 0);
        const sellText = sellGold > 0 ? ` · 卖价 +${sellGold.toFixed(1)}g` : "";
        return `
          <article class="event">
            <h2>${item.event_display_name || item.event_name}<span class="${badgeClass(item.recommendation)}">${item.recommendation_label || item.recommendation}</span></h2>
            <div class="muted">${item.notes || ""}</div>
            <p>命中率 ${pct(stats.prob_relevant_in_shop)} · 核心 ${pct(stats.prob_core_in_shop)} · 期望 ${stats.expected_relevant_in_shop}${sellText}</p>
            <strong>关键卡</strong>
            <ul>${cards || "<li>暂无</li>"}</ul>
            <strong>已拥有命中</strong>
            <ul>${ownedHits || "<li>暂无</li>"}</ul>
            ${childOptions ? `<strong>可能后续</strong><ul>${childOptions}</ul>` : ""}
            <strong>原因</strong>
            <ul>${reasons || "<li>暂无</li>"}</ul>
            ${item.alt_core_card_count ? `
              <strong>转型/备选阵容</strong>
              <p class="muted">其他阵容核心命中 ${item.alt_core_card_count} 张，可作为转型或备选阵容参考。</p>
              <ul>${altCoreHits}</ul>
            ` : ""}
          </article>
        `;
      }).join("");

      const buildAnalysis = data.build_analysis || {};
      const shopCandidates = (buildAnalysis.candidate_cards || []).map((card) => {
        const hits = (card.build_hits || []).map((hit) =>
          `<li>${hit.build_name} · ${hit.build_phase} · ${hit.role} · ${hit.relation}</li>`
        ).join("");
        const reasons = (card.reasons || []).map((reason) => `<li>${reason}</li>`).join("");
        const risks = (card.risks || []).map((risk) => `<li>${risk}</li>`).join("");
        return `
          <article class="event">
            <h2>${card.card_display_name || card.card_name}<span class="badge ${card.importance === "medium" ? "medium" : card.importance === "low" || card.importance === "ignored" ? "low" : ""}">${card.importance}</span></h2>
            <p><strong>${card.recommendation_type}</strong>${card.price != null ? ` · ${card.price}g` : " · 价格未知"}${card.affordable === true ? " · 买得起" : card.affordable === false ? " · 金币不足" : ""}</p>
            <strong>Build 命中</strong>
            <ul>${hits || "<li>未命中已维护 Build，不代表废卡</li>"}</ul>
            <strong>原因</strong>
            <ul>${reasons || "<li>暂无</li>"}</ul>
            ${risks ? `<strong>风险 / 不确定性</strong><ul>${risks}</ul>` : ""}
          </article>
        `;
      }).join("");
      const shopSummary = buildAnalysis.shop_action ? `
        <article class="event">
          <h2>商店操作<span class="badge">${buildAnalysis.shop_action}</span></h2>
          <p>${buildAnalysis.refresh_reason || ""}</p>
        </article>
      ` : "";
      eventsEl.innerHTML = shopSummary + shopCandidates + eventCards;
    }

    function renderStateLists(state) {
      renderList(
        "#currentEvents",
        state.event_options_display || [],
        (item) => item.display_name || item.name,
        (item) => item.known === false ? "待补充" : ""
      );
      const fallbackOwnedCards = state.owned_cards_display || [];
      const fallbackOwnedItems = fallbackOwnedCards.filter(
        (item) => String(item.card_type || "").toLowerCase() !== "skill"
      );
      const fallbackOwnedSkills = fallbackOwnedCards.filter(
        (item) => String(item.card_type || "").toLowerCase() === "skill"
      );
      renderList(
        "#ownedItems",
        state.owned_items_display || fallbackOwnedItems,
        (item) => item.display_name || item.name,
        ownedMeta
      );
      renderList(
        "#ownedSkills",
        state.skills_display || fallbackOwnedSkills,
        (item) => item.display_name || item.name,
        ownedMeta
      );
      const shopVisible = state.current_shop && Array.isArray(state.current_shop.visible_items)
        ? state.current_shop.visible_items
        : [];
      renderList(
        "#visibleCards",
        shopVisible.length ? shopVisible : (state.visible_cards_display || []),
        (item) => item.display_name || item.name || item.template_id
      );
    }

    function renderBuildDetail(detail) {
      if (!detail) {
        buildDetail.innerHTML = `<div class="muted">暂无</div>`;
        return;
      }

      const sections = [
        ["核心卡", detail.core_cards || []],
        ["过渡卡", detail.transition_cards || []],
        ["可选卡", detail.optional_cards || []],
      ].map(([title, cards]) => `
        <h3>${title}</h3>
        <div class="list">
          ${cards.length ? cards.map((card) => `
            <div class="list-item"><span>${card.display_name || card.name}</span></div>
          `).join("") : `<div class="muted">暂无</div>`}
        </div>
      `).join("");

      const tags = detail.wanted_tags || [];
      const tagSection = tags.length ? `
        <h3>需求标签</h3>
        <div class="tag-row">${tags.map((tag) => `<span class="tag">${tag}</span>`).join("")}</div>
      ` : "";

      buildDetail.innerHTML = sections + tagSection;
      buildDetail.classList.toggle("open", buildDetailOpen);
      buildMetric.setAttribute("aria-expanded", buildDetailOpen ? "true" : "false");
    }

    function toggleBuildDetail() {
      buildDetailOpen = !buildDetailOpen;
      buildDetail.classList.toggle("open", buildDetailOpen);
      buildMetric.setAttribute("aria-expanded", buildDetailOpen ? "true" : "false");
    }

    function formatPrestige(state) {
      if (state.prestige == null) return "-";
      if (state.max_prestige == null) return state.prestige;
      return `${state.prestige}/${state.max_prestige}`;
    }

    function ownedMeta(item) {
      const parts = [];
      if (item.rarity) parts.push(item.rarity);
      if (item.section) parts.push(sectionLabel(item.section));
      return parts.filter(Boolean).join(" · ");
    }

    function sectionLabel(section) {
      const labels = {
        board: "场上",
        hand: "手牌",
        stash: "仓库",
      };
      return labels[String(section || "").toLowerCase()] || section;
    }

    function renderList(selector, items, titleFn, metaFn = null) {
      const el = document.querySelector(selector);
      if (!items.length) {
        el.innerHTML = `<div class="muted">暂无</div>`;
        return;
      }
      el.innerHTML = items.map((item) => {
        const title = titleFn(item) || "-";
        const meta = metaFn ? metaFn(item) : "";
        return `<div class="list-item"><span>${title}</span>${meta ? `<small>${meta}</small>` : ""}</div>`;
      }).join("");
    }

    document.querySelector("#refreshBtn").addEventListener("click", () => analyze(false));
    document.querySelector("#aiBtn").addEventListener("click", () => analyze(true));
    buildSelect.addEventListener("change", () => {
      localStorage.setItem("bazaar_selected_build", buildSelect.value);
      aiBox.innerHTML = "";
      lastRenderedSignature = null;
      analyze(false);
    });
    buildMetric.addEventListener("click", toggleBuildDetail);
    buildMetric.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleBuildDetail();
      }
    });

    loadState()
      .then((state) => loadOptions(state && state.hero ? state.hero : null))
      .then(() => analyze(false));
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) analyze(false);
    });
    setInterval(() => analyze(false), 5000);
  </script>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local The Bazaar AI helper UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((args.host, args.port), BazaarHandler)
    print(f"The Bazaar AI helper UI: http://{args.host}:{args.port}")
    print(f"Runtime state file: {STATE_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
