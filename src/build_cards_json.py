import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent

INPUT_PATH = BASE_DIR / "data" / "vanessa_cards_from_game.json"
OUTPUT_PATH = BASE_DIR / "data" / "cards_generated.json"


def convert_to_project_card(card: dict[str, Any]) -> dict[str, Any]:
    """
    转换成项目内部使用的卡牌格式。
    """

    return {
        "hero": card.get("hero"),
        "type": card.get("type"),
        "size": card.get("size"),
        "min_rarity": card.get("starting_tier"),
        "tiers": card.get("tiers", []),
        "tags": card.get("tags", []),
        "buy_prices": card.get("buy_prices", {}),
        "description": card.get("description", ""),
        "source_id": card.get("id"),
        "internal_name": card.get("internal_name"),
    }


def main():
    if not INPUT_PATH.exists():
        raise FileNotFoundError(f"找不到输入文件：{INPUT_PATH}")

    with INPUT_PATH.open("r", encoding="utf-8") as file:
        raw_cards = json.load(file)

    cards = {}

    for card in raw_cards:
        name = card.get("name")

        if not name:
            continue

        cards[name] = convert_to_project_card(card)

    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(cards, file, ensure_ascii=False, indent=2)

    print(f"输入卡牌数量：{len(raw_cards)}")
    print(f"输出卡牌数量：{len(cards)}")
    print(f"已生成：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()