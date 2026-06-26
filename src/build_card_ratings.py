import json
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# 这里填你的旧版手写卡牌数据文件名
OLD_CARDS_PATH = DATA_DIR / "cards.json"

# 生成的新人工评级文件
NEW_RATINGS_PATH = DATA_DIR / "card_ratings.json"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"找不到文件：{path}")

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path: Path, data: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def build_card_ratings(old_cards: dict[str, Any]) -> dict[str, Any]:
    """
    从旧版 cards.json 中提取人工评价字段：
    - tier
    - build_roles

    删除旧数据里的：
    - tags
    - min_rarity
    - max_rarity
    - 其他字段
    """

    card_ratings = {}

    for card_name, old_card_data in old_cards.items():
        tier = old_card_data.get("tier", "Unknown")
        build_roles = old_card_data.get("build_roles", {})

        card_ratings[card_name] = {
            "tier": tier,
            "build_roles": build_roles
        }

    return card_ratings


def main():
    old_cards = load_json(OLD_CARDS_PATH)

    card_ratings = build_card_ratings(old_cards)

    save_json(NEW_RATINGS_PATH, card_ratings)

    print("转换完成！")
    print(f"旧文件：{OLD_CARDS_PATH}")
    print(f"新文件：{NEW_RATINGS_PATH}")
    print(f"转换卡牌数量：{len(card_ratings)}")


if __name__ == "__main__":
    main()