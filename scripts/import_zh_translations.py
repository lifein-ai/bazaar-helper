from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_GAME_CACHE = (
    Path.home()
    / "AppData"
    / "LocalLow"
    / "Tempo Storm"
    / "The Bazaar"
    / "prod"
    / "cache"
)
DEFAULT_GAME_DB = DEFAULT_GAME_CACHE / "GameData.db"
DEFAULT_TRANSLATION_DB = DEFAULT_GAME_CACHE / "translations" / "zh-CN.bytes"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "translations_zh_cn.json"


def load_translation_table(path: Path) -> dict[str, str]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("select hash, text from translation").fetchall()
    return {str(key): str(text) for key, text in rows}


def iter_game_records(game_db: Path) -> list[tuple[str, dict[str, Any]]]:
    records: list[tuple[str, dict[str, Any]]] = []
    with sqlite3.connect(game_db) as connection:
        for table in ("cards", "monsters"):
            if not table_exists(connection, table):
                continue
            for record_id, raw_data in connection.execute(f"select Id, Data from {table}"):
                try:
                    records.append((str(record_id), json.loads(raw_data)))
                except (TypeError, json.JSONDecodeError):
                    continue
    return records


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "select 1 from sqlite_master where type = 'table' and name = ?",
        (table,),
    ).fetchone()
    return row is not None


def localized_text(record: dict[str, Any], translations: dict[str, str], field: str) -> str | None:
    localization = record.get("Localization") or {}
    field_data = localization.get(field) or {}
    key = field_data.get("Key")
    if field == "Title" and not key:
        key = record.get("TranslationKey")
    if not key:
        return None
    return translations.get(str(key))


def english_text(record: dict[str, Any], field: str) -> str | None:
    localization = record.get("Localization") or {}
    field_data = localization.get(field) or {}
    if isinstance(field_data, dict) and field_data.get("Text"):
        return field_data.get("Text")
    if field == "Title":
        return record.get("InternalName")
    if field == "Description":
        return record.get("InternalDescription")
    return None


def localized_title(record: dict[str, Any], translations: dict[str, str]) -> str | None:
    return localized_text(record, translations, "Title")


def english_title(record: dict[str, Any]) -> str | None:
    return english_text(record, "Title")


def build_translation_payload(game_db: Path, translation_db: Path) -> dict[str, Any]:
    translations = load_translation_table(translation_db)
    by_name: dict[str, str] = {}
    by_id: dict[str, str] = {}
    description_by_text: dict[str, str] = {}
    description_by_id: dict[str, str] = {}

    for record_id, record in iter_game_records(game_db):
        zh_name = localized_title(record, translations)
        en_name = english_title(record)
        if zh_name:
            by_id[record_id] = zh_name
            if en_name:
                by_name[en_name] = zh_name

        zh_description = localized_text(record, translations, "Description")
        en_description = english_text(record, "Description")
        if zh_description:
            description_by_id[record_id] = zh_description
            if en_description:
                description_by_text[en_description] = zh_description

    return {
        "source_game_db": str(game_db),
        "source_translation_db": str(translation_db),
        "locale": "zh-CN",
        "by_name": dict(sorted(by_name.items())),
        "by_id": dict(sorted(by_id.items())),
        "description_by_text": dict(sorted(description_by_text.items())),
        "description_by_id": dict(sorted(description_by_id.items())),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import The Bazaar built-in Chinese translations.")
    parser.add_argument("--game-db", type=Path, default=DEFAULT_GAME_DB)
    parser.add_argument("--translation-db", type=Path, default=DEFAULT_TRANSLATION_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_translation_payload(args.game_db, args.translation_db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")
    print(f"Names: {len(payload['by_name'])}")
    print(f"Ids: {len(payload['by_id'])}")
    print(f"Descriptions: {len(payload['description_by_id'])}")


if __name__ == "__main__":
    main()
