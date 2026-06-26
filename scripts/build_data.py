import csv
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = BASE_DIR / "raw_data"
DATA_DIR = BASE_DIR / "data"


def split_list(value: str) -> list[str]:
    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


def empty_to_none(value: str):
    value = value.strip()

    if value == "":
        return None

    return value


def read_csv(path: Path) -> list[dict]:
    """
    读取 CSV 文件。
    优先按 UTF-8 读取；如果失败，再尝试 GBK / GB18030。
    兼容 VS Code、Excel、WPS 导出的 CSV。
    """

    encodings = ["utf-8-sig", "utf-8", "gbk", "gb18030"]

    last_error = None

    for encoding in encodings:
        try:
            with path.open("r", encoding=encoding, newline="") as file:
                return list(csv.DictReader(file))
        except UnicodeDecodeError as error:
            last_error = error

    raise RuntimeError(f"无法读取 CSV 文件编码：{path}，最后错误：{last_error}")

def write_json(path: Path, data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def build_cards() -> dict:
    rows = read_csv(RAW_DATA_DIR / "cards.csv")
    cards = {}

    for row in rows:
        name = row["name"].strip()

        if not name:
            continue

        build_roles = {}

        for key, value in row.items():
            if key.endswith("_role") and value.strip():
                build_name = key.replace("_role", "")
                build_roles[build_name] = value.strip()

        cards[name] = {
            "tier": row["tier"].strip(),
            "tags": split_list(row["tags"]),
            "min_rarity": row["min_rarity"].strip(),
            "max_rarity": row["max_rarity"].strip(),
            "build_roles": build_roles
        }

    return cards


def build_events() -> dict:
    rows = read_csv(RAW_DATA_DIR / "events.csv")
    events = {}

    for row in rows:
        name = row["name"].strip()

        if not name:
            continue

        rarity_min = row["rarity_min"].strip()
        rarity_max = row["rarity_max"].strip()
        rarity_rule = row["rarity_rule"].strip()

        rarity_filter = None

        if rarity_min and rarity_max:
            rarity_filter = {
                "min": rarity_min,
                "max": rarity_max
            }

        events[name] = {
            "event_type": row["event_type"].strip(),
            "reward_tags": split_list(row["reward_tags"]),
            "match_mode": row["match_mode"].strip() or "any",
            "rarity_filter": rarity_filter,
            "rarity_rule": rarity_rule if rarity_rule else None,
            "resource_rewards": {
                "gold": int(row["gold"] or 0),
                "exp": int(row["exp"] or 0),
                "health": int(row["health"] or 0)
            },
            "notes": row["notes"].strip()
        }

    return events


def build_rarity_rules() -> dict:
    rows = read_csv(RAW_DATA_DIR / "rarity_rules.csv")
    rarity_rules = {}

    for row in rows:
        rule_name = row["rule_name"].strip()

        if not rule_name:
            continue

        if rule_name not in rarity_rules:
            rarity_rules[rule_name] = []

        to_day_raw = row["to_day"].strip()

        rarity_rules[rule_name].append({
            "from_day": int(row["from_day"]),
            "to_day": int(to_day_raw) if to_day_raw else None,
            "min": row["min"].strip(),
            "max": row["max"].strip()
        })

    return rarity_rules


def main() -> None:
    cards = build_cards()
    events = build_events()
    rarity_rules = build_rarity_rules()

    write_json(DATA_DIR / "cards.json", cards)
    write_json(DATA_DIR / "events.json", events)
    write_json(DATA_DIR / "rarity_rules.json", rarity_rules)

    print("数据生成完成：")
    print(f"- cards.json: {len(cards)} cards")
    print(f"- events.json: {len(events)} events")
    print(f"- rarity_rules.json: {len(rarity_rules)} rules")


if __name__ == "__main__":
    main()