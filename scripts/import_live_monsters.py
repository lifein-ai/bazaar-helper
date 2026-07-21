from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

from app_paths import get_runtime_dir  # noqa: E402


RUNTIME_DIR = get_runtime_dir()
DATA_DIR = BASE_DIR / "data"
LIVE_MONSTERS_PATH = RUNTIME_DIR / "live_monsters_raw.json"
MONSTERS_OUTPUT_PATH = DATA_DIR / "monsters_generated.json"
MONSTERS_BACKUP_PATH = DATA_DIR / "monsters_generated.backup.json"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_list(values: Any) -> list[Any]:
    return values if isinstance(values, list) else []


def compact_card_instance(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    item = dict(raw)
    template_id = normalize_text(
        item.get("template_id") or item.get("source_id") or item.get("id")
    )
    if template_id:
        item["template_id"] = template_id
        item.setdefault("source_id", template_id)
    item.setdefault("name", normalize_text(item.get("internal_name")))
    item.setdefault("card_type", "Skill" if str(item.get("section")).lower() == "skill" else "Item")
    return item


def compact_encounter(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    encounter = dict(raw)
    source_id = normalize_text(
        encounter.get("source_id") or encounter.get("template_id") or encounter.get("id")
    )
    if source_id:
        encounter["source_id"] = source_id
        encounter.setdefault("template_id", source_id)
        encounter.setdefault("id", source_id)
    return encounter


def unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = normalize_text(value)
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        result.append(text)
    return result


def compact_monster(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    monster_id = normalize_text(raw.get("id") or raw.get("template_id"))
    if not monster_id:
        return None

    items = [
        item
        for item in (compact_card_instance(entry) for entry in normalize_list(raw.get("items")))
        if item
    ]
    skills = [
        item
        for item in (compact_card_instance(entry) for entry in normalize_list(raw.get("skills")))
        if item
    ]
    encounters = [
        item
        for item in (compact_encounter(entry) for entry in normalize_list(raw.get("encounters")))
        if item
    ]
    health = optional_int(raw.get("health"))
    if health is None and isinstance(raw.get("attributes"), dict):
        health = optional_int(
            raw["attributes"].get("HealthMax") or raw["attributes"].get("health_max")
        )

    name = normalize_text(raw.get("name") or raw.get("internal_name") or monster_id)
    internal_name = normalize_text(raw.get("internal_name"))

    return {
        "id": monster_id,
        "source_id": monster_id,
        "template_id": normalize_text(raw.get("template_id") or monster_id),
        "internal_name": internal_name,
        "name": name,
        "monster_name": name,
        "health": health,
        "combat_health": health,
        "max_health": health,
        "attributes": raw.get("attributes") if isinstance(raw.get("attributes"), dict) else {},
        "items": items,
        "board_items": items,
        "skills": skills,
        "encounters": encounters,
        "encounter_ids": unique(
            [
                value
                for encounter in encounters
                for value in (
                    encounter.get("source_id"),
                    encounter.get("template_id"),
                    encounter.get("id"),
                )
            ]
        ),
        "encounter_names": unique(
            [
                value
                for encounter in encounters
                for value in (
                    encounter.get("name"),
                    encounter.get("internal_name"),
                )
            ]
        ),
    }


def import_live_monsters(
    *,
    live_path: Path = LIVE_MONSTERS_PATH,
    output_path: Path = MONSTERS_OUTPUT_PATH,
    backup_path: Path = MONSTERS_BACKUP_PATH,
    create_backup: bool = True,
) -> dict[str, Any]:
    raw = read_json(live_path)
    if not isinstance(raw, list):
        raise ValueError(f"Live monster export must be a JSON array: {live_path}")

    monsters: dict[str, Any] = {}
    for entry in raw:
        monster = compact_monster(entry)
        if monster is None:
            continue
        key = str(monster["id"])
        monsters[key] = monster

    if create_backup and output_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output_path, backup_path)
    write_json(output_path, monsters)
    return monsters


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import the runtime one-shot monster export into data/monsters_generated.json."
    )
    parser.add_argument("--live-path", type=Path, default=LIVE_MONSTERS_PATH)
    parser.add_argument("--output", type=Path, default=MONSTERS_OUTPUT_PATH)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    monsters = import_live_monsters(
        live_path=args.live_path,
        output_path=args.output,
        create_backup=not args.no_backup,
    )
    print(f"Imported {len(monsters)} monsters into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
