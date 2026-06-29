from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

OFFICIAL_CARDS_PATH = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "Tempo Storm"
    / "The Bazaar"
    / "cache"
    / "cards.json"
)

sys.path.insert(0, str(BASE_DIR / "src"))

from app_paths import get_runtime_dir  # noqa: E402
from data_loader import load_all_data  # noqa: E402


RUNTIME_DIR = get_runtime_dir()
STATE_PATH = RUNTIME_DIR / "game_state.json"
GRAPH_PATH = RUNTIME_DIR / "observed_event_graph.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_source_id_index(data: dict[str, Any]) -> dict[str, str]:
    return {
        str(source_id).lower(): event_name
        for event_name, event_data in data.get("events", {}).items()
        if isinstance(event_data, dict)
        for source_id in event_data.get("source_ids", []) or []
    }


def load_official_cards_index() -> dict[str, dict[str, Any]]:
    if not OFFICIAL_CARDS_PATH.exists():
        print(f"未找到官方 cards.json：{OFFICIAL_CARDS_PATH}")
        return {}

    raw = load_json(OFFICIAL_CARDS_PATH)

    if not isinstance(raw, dict):
        print(f"官方 cards.json 顶层不是 dict：{OFFICIAL_CARDS_PATH}")
        return {}

    version_data = raw.get("2.0.0")

    if not isinstance(version_data, list):
        print(f"官方 cards.json 没有 2.0.0 列表：{OFFICIAL_CARDS_PATH}")
        return {}

    result: dict[str, dict[str, Any]] = {}

    for card in version_data:
        if not isinstance(card, dict):
            continue

        card_id = card.get("Id")
        if card_id:
            result[str(card_id).lower()] = card

    print(f"已加载官方 cards.json：{OFFICIAL_CARDS_PATH}，卡牌数：{len(result)}")
    return result


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

        action_type = str(action.get("$type") or "")

        if action_type != "TActionPlayerModifyAttribute":
            continue

        attribute = str(action.get("AttributeType") or "").lower()

        value_obj = action.get("Value", {})
        value = None
        if isinstance(value_obj, dict):
            value = value_obj.get("Value")

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
        else:
            rewards[attribute] = value

    return rewards


def enrich_child_from_official_cards(
    child_item: dict[str, Any],
    official_cards: dict[str, dict[str, Any]],
) -> None:
    source_id = str(child_item.get("source_id") or "").lower()
    if not source_id:
        return

    card = official_cards.get(source_id)
    if not card:
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


def normalize_kind(option: dict[str, Any]) -> str:
    option_id = str(option.get("id") or "")
    kind = str(option.get("kind") or "").lower()
    card_type = str(option.get("card_type") or "").lower()

    if option_id.startswith("ste_") or "encounterstep" in card_type:
        return "step"

    if option_id.startswith("com_") or "combat" in card_type:
        return "combat"

    if option_id.startswith("pvp_"):
        return "pvp"

    if option_id.startswith("enc_") or "eventencounter" in card_type:
        return "encounter"

    return kind or "unknown"


def option_to_event_name(
    option: dict[str, Any],
    source_id_index: dict[str, str],
) -> str | None:
    template_id = str(option.get("template_id") or "").lower()
    if template_id and template_id in source_id_index:
        return source_id_index[template_id]

    name = option.get("name")
    if name:
        return str(name)

    return None


def load_graph() -> dict[str, Any]:
    if not GRAPH_PATH.exists():
        return {}

    try:
        data = load_json(GRAPH_PATH)
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def observe_current_state() -> None:
    data = load_all_data(DATA_DIR)
    source_id_index = build_source_id_index(data)
    official_cards = load_official_cards_index()

    payload = load_json(STATE_PATH)
    detailed_options = payload.get("event_options_detailed", [])

    if not isinstance(detailed_options, list):
        print("当前 game_state.json 没有 event_options_detailed")
        return

    normalized_options: list[dict[str, Any]] = []

    for option in detailed_options:
        if not isinstance(option, dict):
            continue

        item = dict(option)
        item["kind"] = normalize_kind(item)
        item["event_name"] = option_to_event_name(item, source_id_index)
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

    if len(parents) != 1 or not children:
        print("当前界面不像父事件展开后的子选项界面，未记录。")
        print(f"- parents: {len(parents)}")
        print(f"- children: {len(children)}")
        return

    parent = parents[0]
    parent_name = parent.get("event_name")

    if not parent_name:
        print("父事件无法映射到 events.json 名称，未记录。")
        return

    graph = load_graph()

    item = graph.get(parent_name, {})
    if not isinstance(item, dict):
        item = {}

    item.setdefault("parent_event", parent_name)
    item.setdefault("parent_source_ids", [])
    item.setdefault("children", [])
    item["observed_count"] = int(item.get("observed_count", 0)) + 1

    parent_template_id = parent.get("template_id")
    if parent_template_id and parent_template_id not in item["parent_source_ids"]:
        item["parent_source_ids"].append(parent_template_id)

    existing_children = {
        child.get("source_id"): child
        for child in item["children"]
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
                "count": 0,
            }

            enrich_child_from_official_cards(child_item, official_cards)

            item["children"].append(child_item)

        child_item["count"] = int(child_item.get("count", 0)) + 1

        enrich_child_from_official_cards(child_item, official_cards)

    graph[parent_name] = item

    write_json(GRAPH_PATH, graph)

    print("已记录父事件子选项关系")
    print(f"- parent: {parent_name}")
    print(f"- children: {len(children)}")
    print(f"- output: {GRAPH_PATH}")


if __name__ == "__main__":
    observe_current_state()
