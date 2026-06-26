import json
from pathlib import Path
from typing import Any


# 游戏本地缓存目录
BAZAAR_CACHE_DIR = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "Tempo Storm"
    / "The Bazaar"
    / "cache"
)

# 项目根目录：D:\bazzarhelp
BASE_DIR = Path(__file__).resolve().parent.parent

# 输出到你项目的 data 文件夹
OUTPUT_DIR = BASE_DIR / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

RAW_CARDS_PATH = BAZAAR_CACHE_DIR / "cards.json"
OUTPUT_PATH = OUTPUT_DIR / "vanessa_cards_from_game.json"


def load_raw_cards(path: Path) -> list[dict[str, Any]]:
    """
    读取游戏原始 cards.json。

    目前结构是：
    {
        "2.0.0": [
            {...},
            {...}
        ]
    }
    """

    if not path.exists():
        raise FileNotFoundError(f"找不到游戏 cards.json：{path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise TypeError("cards.json 顶层不是 dict")

    version = next(iter(data))
    cards = data[version]

    if not isinstance(cards, list):
        raise TypeError(f"版本 {version} 下面不是卡牌列表")

    print(f"读取版本：{version}")
    print(f"原始卡牌总数：{len(cards)}")

    return cards


def get_title(card: dict[str, Any]) -> str:
    """
    获取卡牌显示名。
    """

    return (
        card.get("Localization", {})
        .get("Title", {})
        .get("Text")
        or ""
    )


def get_description(card: dict[str, Any]) -> str:
    """
    获取卡牌描述文本。
    有些卡牌有多个 tooltip，这里合并成一段文本。
    """

    tooltips = (
        card.get("Localization", {})
        .get("Tooltips", [])
    )

    texts = []

    for tooltip in tooltips:
        content = tooltip.get("Content", {})
        text = content.get("Text")

        if text:
            texts.append(text)

    return "\n".join(texts)


def get_tier_names(card: dict[str, Any]) -> list[str]:
    """
    获取这张卡有哪些等级。
    """

    tiers = card.get("Tiers", {})

    if isinstance(tiers, dict):
        return list(tiers.keys())

    return []


def get_buy_prices(card: dict[str, Any]) -> dict[str, int | None]:
    """
    获取每个等级的购买价格。
    """

    result = {}

    tiers = card.get("Tiers", {})

    if not isinstance(tiers, dict):
        return result

    for tier_name, tier_data in tiers.items():
        attributes = tier_data.get("Attributes", {})
        result[tier_name] = attributes.get("BuyPrice")

    return result


def is_vanessa_card(card: dict[str, Any]) -> bool:
    """
    判断是否是 Vanessa 相关卡牌。

    这里用两层判断：
    1. 优先看 Heroes 字段
    2. 如果字段结构不确定，就退一步全文搜索 Vanessa
    """

    heroes = card.get("Heroes")

    if isinstance(heroes, list):
        for hero in heroes:
            if str(hero).lower() == "vanessa":
                return True

    # 兜底：把整张卡转成字符串搜索 Vanessa
    # 这样能兼容字段名不确定的情况
    raw_text = json.dumps(card, ensure_ascii=False).lower()

    return "vanessa" in raw_text


def convert_card(card: dict[str, Any]) -> dict[str, Any]:
    """
    把游戏原始卡牌格式转换成你项目更容易用的格式。
    """

    title = get_title(card)
    internal_name = card.get("InternalName") or ""

    return {
        "id": card.get("Id"),
        "name": title,
        "internal_name": internal_name,
        "type": card.get("Type"),
        "hero": "Vanessa",
        "size": card.get("Size"),
        "starting_tier": card.get("StartingTier"),
        "tiers": get_tier_names(card),
        "tags": card.get("Tags", []),
        "buy_prices": get_buy_prices(card),
        "description": get_description(card),
    }


def main():
    raw_cards = load_raw_cards(RAW_CARDS_PATH)

    results = []

    for card in raw_cards:
        if not isinstance(card, dict):
            continue

        # 只要物品卡
        if card.get("Type") != "Item":
            continue

        # 只要 Vanessa 相关
        if not is_vanessa_card(card):
            continue

        results.append(convert_card(card))

    # 按名字排序，方便查看
    results.sort(key=lambda item: item["name"])

    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(results, file, ensure_ascii=False, indent=2)

    print(f"Vanessa 物品卡数量：{len(results)}")
    print(f"已生成：{OUTPUT_PATH}")

    print("\n前 20 张：")
    for index, card in enumerate(results[:20], start=1):
        print(
            f"{index}. "
            f"{card['name']} | "
            f"{card['starting_tier']} | "
            f"{card['size']} | "
            f"Tags={card['tags']}"
        )


if __name__ == "__main__":
    main()