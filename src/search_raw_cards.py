import json
from pathlib import Path


BAZAAR_CACHE_DIR = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "Tempo Storm"
    / "The Bazaar"
    / "cache"
)

RAW_CARDS_PATH = BAZAAR_CACHE_DIR / "cards.json"


def main():
    with RAW_CARDS_PATH.open("r", encoding="utf-8") as file:
        data = json.load(file)

    version = next(iter(data))
    cards = data[version]

    keywords = [
        "crit",
        "critical",
        "shield",
        "health",
        "haste",
        "slow",
        "burn",
        "poison"
    ]

    for keyword in keywords:
        print("=" * 80)
        print(f"搜索关键词：{keyword}")

        hits = []

        for card in cards:
            raw_text = json.dumps(card, ensure_ascii=False).lower()

            if keyword in raw_text:
                title = card.get("Title") or card.get("title") or "无标题"
                internal_name = card.get("InternalName") or card.get("internalName") or "无 InternalName"
                card_type = card.get("Type") or card.get("type")

                hits.append((title, internal_name, card_type))

        print(f"命中数量：{len(hits)}")

        for title, internal_name, card_type in hits[:30]:
            print(f"- {title} | {internal_name} | {card_type}")


if __name__ == "__main__":
    main()