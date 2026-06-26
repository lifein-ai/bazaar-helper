from __future__ import annotations

from collections import Counter, defaultdict
from math import ceil
from pathlib import Path
from typing import Any
import json


BASE_DIR = Path(__file__).resolve().parents[1]
RUNS_PATH = BASE_DIR / "data" / "bazaardb_runs.json"
BUILDS_PATH = BASE_DIR / "data" / "builds.json"
AUTO_SOURCE_TYPES = {"bazaardb_ten_win_run", "bazaardb_hero_meta"}
MANUAL_BUILD_KEYS = {"VanessaAquaticAmmo"}


def load_json(path: Path) -> Any:
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def normalize_key_text(text: str) -> str:
    return (
        text.replace(" ", "")
        .replace("-", "")
        .replace("'", "")
        .replace(".", "")
    )


def is_successful_run(run: dict[str, Any]) -> bool:
    record = str(run.get("record", ""))
    title = str(run.get("title", ""))
    result = str(run.get("result", ""))
    return record.startswith("10-") or "10 Wins" in title or "VICTORY" in result


def extract_unique_card_names(run: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for card in run.get("cards", []):
        name = str(card.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def run_reference(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "url": run.get("url"),
        "player": run.get("player"),
        "hero": run.get("hero"),
        "record": run.get("record"),
        "result": run.get("result"),
        "title": run.get("title"),
        "imported_at_utc": run.get("imported_at_utc"),
    }


def sorted_cards_by_frequency(counter: Counter[str]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def split_cards(counter: Counter[str], sample_count: int) -> tuple[list[str], list[str], list[str]]:
    ranked = sorted_cards_by_frequency(counter)
    if sample_count <= 1:
        core_count = min(12, len(ranked))
        return [name for name, _ in ranked[:core_count]], [], [
            name for name, _ in ranked[core_count:32]
        ]

    core_min_count = max(2, ceil(sample_count * 0.5))
    transition_min_count = max(2, ceil(sample_count * 0.34))

    core_cards = [name for name, count in ranked if count >= core_min_count]
    transition_cards = [
        name
        for name, count in ranked
        if name not in core_cards and count >= transition_min_count
    ]
    optional_cards = [
        name
        for name, _ in ranked
        if name not in core_cards and name not in transition_cards
    ][:24]

    return core_cards[:16], transition_cards[:16], optional_cards


def build_frequency_table(counter: Counter[str], sample_count: int) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "count": count,
            "frequency": round(count / sample_count, 4),
        }
        for name, count in sorted_cards_by_frequency(counter)[:60]
    ]


def convert_hero_runs_to_build(hero: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    sample_count = len(runs)
    card_counter: Counter[str] = Counter()
    for run in runs:
        card_counter.update(extract_unique_card_names(run))

    core_cards, transition_cards, optional_cards = split_cards(card_counter, sample_count)
    references = [run_reference(run) for run in runs]

    return {
        "hero": hero,
        "display_name": f"BazaarDB {hero} Meta",
        "source_type": "bazaardb_hero_meta",
        "confidence": "medium" if sample_count < 5 else "high",
        "sample_count": sample_count,
        "applicable_stages": ["mid", "late"],
        "day_range": [5, None],
        "build_summary": (
            "由 BazaarDB 胜利通关阵容自动聚合。核心卡代表多个样本里反复出现的成型方向，"
            "比单局样本更适合做事件和商店推荐。"
        ),
        "match_notes": [
            f"样本数：{sample_count}",
            "核心卡按出现频率筛选；样本越多，推荐越稳定。",
            "这是最终阵容聚合，不表示前期必须照单全拿；前期更适合把它当作转型方向。",
        ],
        "core_cards": core_cards,
        "transition_cards": transition_cards,
        "optional_cards": optional_cards,
        "wanted_tags": [],
        "event_priorities": [],
        "avoid_events": [],
        "card_frequencies": build_frequency_table(card_counter, sample_count),
        "source_runs": references,
    }


def remove_old_auto_builds(builds: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in builds.items():
        if key in MANUAL_BUILD_KEYS:
            cleaned[key] = value
            continue
        if isinstance(value, dict) and value.get("source_type") in AUTO_SOURCE_TYPES:
            continue
        cleaned[key] = value
    return cleaned


def main() -> None:
    runs = load_json(RUNS_PATH)
    if not isinstance(runs, list):
        raise ValueError("data/bazaardb_runs.json must be a list of run objects.")

    builds = load_json(BUILDS_PATH)
    if not isinstance(builds, dict):
        raise ValueError("data/builds.json must be a JSON object.")

    hero_runs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    skipped_count = 0
    for run in runs:
        hero = str(run.get("hero", "")).strip()
        if not hero or not is_successful_run(run):
            skipped_count += 1
            continue
        cards = extract_unique_card_names(run)
        if not cards:
            skipped_count += 1
            continue
        hero_runs[hero].append(run)

    builds = remove_old_auto_builds(builds)
    for hero, hero_run_list in sorted(hero_runs.items()):
        key = f"BazaarDB{normalize_key_text(hero)}Meta"
        builds[key] = convert_hero_runs_to_build(hero, hero_run_list)

    save_json(BUILDS_PATH, builds)

    print("Build generation finished.")
    print(f"Input: {RUNS_PATH}")
    print(f"Output: {BUILDS_PATH}")
    print(f"Generated hero meta builds: {len(hero_runs)}")
    print(f"Skipped runs: {skipped_count}")


if __name__ == "__main__":
    main()
