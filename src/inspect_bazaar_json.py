import json
from pathlib import Path


DATA_DIR = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "Tempo Storm"
    / "The Bazaar"
    / "cache"
)

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "debug_output"
OUTPUT_DIR.mkdir(exist_ok=True)


def get_short_card_info(card: dict) -> dict:
    return {
        "Id": card.get("Id"),
        "InternalName": card.get("InternalName"),
        "Type": card.get("Type"),
        "Size": card.get("Size"),
        "StartingTier": card.get("StartingTier"),
        "Tags": card.get("Tags"),
        "Title": (
            card.get("Localization", {})
            .get("Title", {})
            .get("Text")
        ),
        "TierNames": list(card.get("Tiers", {}).keys()),
    }


def main():
    cards_path = DATA_DIR / "cards.json"

    print("cards.json 路径：", cards_path)

    if not cards_path.exists():
        raise FileNotFoundError(f"找不到文件：{cards_path}")

    with cards_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    print("cards.json 顶层类型：", type(data).__name__)
    print("顶层 keys：", list(data.keys()))

    # 关键：先进入版本号这一层
    version = next(iter(data))
    version_data = data[version]

    print("当前版本：", version)
    print("版本层类型：", type(version_data).__name__)

    # 如果 version_data 是 list，说明这里就是卡牌列表
    if isinstance(version_data, list):
        cards = version_data

    # 如果 version_data 是 dict，就继续看它下面有什么 key
    elif isinstance(version_data, dict):
        print("版本层 keys：", list(version_data.keys()))

        # 常见可能字段，逐个尝试
        if "Cards" in version_data:
            cards = version_data["Cards"]
        elif "cards" in version_data:
            cards = version_data["cards"]
        elif "Data" in version_data:
            cards = version_data["Data"]
        elif "data" in version_data:
            cards = version_data["data"]
        else:
            raise KeyError(
                "没找到卡牌列表字段。请把“版本层 keys”发我。"
            )

    else:
        raise TypeError("版本层既不是 list 也不是 dict，无法处理。")

    print("卡牌总数：", len(cards))

    short_preview = []

    for card in cards[:20]:
        if isinstance(card, dict):
            short_preview.append(get_short_card_info(card))

    output_path = OUTPUT_DIR / "cards_short_preview.json"

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(short_preview, file, ensure_ascii=False, indent=2)

    print("已生成简略预览文件：", output_path)

    print("\n前 20 条简略信息：")
    for index, card in enumerate(short_preview, start=1):
        print(
            f"{index}. "
            f"{card['Title']} | "
            f"{card['InternalName']} | "
            f"{card['Type']} | "
            f"{card['StartingTier']} | "
            f"{card['Size']} | "
            f"Tags={card['Tags']}"
        )


if __name__ == "__main__":
    main()