#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def default_project_root() -> Path:
    here = Path(__file__).resolve()
    # Expected final location: <project>/scripts/audit_event_pool.py
    if here.parent.name == "scripts":
        return here.parent.parent
    return Path.cwd().resolve()


def safe_filename(text: str) -> str:
    text = re.sub(r"[^\w\-.]+", "_", text.strip(), flags=re.UNICODE)
    return text.strip("_") or "event"


def add_src_to_path(src_dir: Path) -> None:
    src = str(src_dir.resolve())
    if src not in sys.path:
        sys.path.insert(0, src)


def resolve_event(events: dict[str, Any], event_query: str) -> tuple[str, dict[str, Any]]:
    if event_query in events:
        return event_query, events[event_query]

    normalized_query = event_query.strip().lower()
    case_matches = [name for name in events if name.strip().lower() == normalized_query]
    if len(case_matches) == 1:
        name = case_matches[0]
        return name, events[name]

    partial_matches = [name for name in events if normalized_query in name.strip().lower()]
    if len(partial_matches) == 1:
        name = partial_matches[0]
        return name, events[name]

    if not case_matches and not partial_matches:
        raise SystemExit(f"Event not found: {event_query}")

    matches = case_matches or partial_matches
    preview = ", ".join(matches[:20])
    extra = "" if len(matches) <= 20 else f" ... +{len(matches) - 20} more"
    raise SystemExit(f"Event name is ambiguous: {event_query}\nMatches: {preview}{extra}")


def selected_card_fields(card_name: str, card_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": card_name,
        "internal_name": card_data.get("internal_name"),
        "type": card_data.get("type") or card_data.get("card_type"),
        "hero": card_data.get("hero"),
        "heroes": card_data.get("heroes", []),
        "size": card_data.get("size"),
        "rarity": card_data.get("rarity"),
        "min_rarity": card_data.get("min_rarity"),
        "max_rarity": card_data.get("max_rarity"),
        "tiers": card_data.get("tiers", []),
        "tags": card_data.get("tags", []),
        "visible_tags": card_data.get("visible_tags", []),
        "hidden_tags": card_data.get("hidden_tags", []),
        "buy_prices": card_data.get("buy_prices", {}),
        "sell_prices": card_data.get("sell_prices", {}),
        "description": card_data.get("description", ""),
        "source_id": card_data.get("source_id") or card_data.get("id"),
    }


def suspicious_flags_for_included(
    *,
    card_name: str,
    card_data: dict[str, Any],
    normalize_text: Callable[[str | None], str],
    normalize_text_list: Callable[[list[str] | None], list[str]],
) -> list[str]:
    tags = set(normalize_text_list(card_data.get("tags", [])))
    name_text = normalize_text(card_name)
    internal_name = normalize_text(card_data.get("internal_name"))
    flags: list[str] = []

    if "quest" in tags:
        flags.append("quest")
    if "loot" in tags:
        flags.append("loot")
    if "package" in tags:
        flags.append("package")
    if "legendary" in tags:
        flags.append("legendary_tag")
    if "debug" in tags or "[debug]" in name_text or "[debug]" in internal_name:
        flags.append("debug")
    if "template" in tags or "template" in name_text or "template" in internal_name:
        flags.append("template")

    return flags


def audit_event_pool(
    *,
    event_name: str,
    event_data: dict[str, Any],
    cards: dict[str, Any],
    current_day: int,
    current_hero: str,
    rarity_rules: dict[str, Any],
    recommender: Any,
) -> dict[str, Any]:
    normalize_text = recommender.normalize_text
    normalize_text_list = recommender.normalize_text_list

    pool_rule = recommender.get_event_card_pool_rule(event_data)
    if pool_rule is None:
        return {
            "event_name": event_name,
            "hero": current_hero,
            "day": current_day,
            "error": "This event has no card pool rule.",
            "event": event_data,
            "included_cards": [],
            "excluded_cards": [],
            "stats": {
                "included_count": 0,
                "excluded_count": len(cards),
            },
        }

    reward_tags = normalize_text_list(pool_rule.get("reward_tags", []))
    exact_names = set(pool_rule.get("exact_names", []))
    match_mode = pool_rule.get("match_mode", "any")
    excluded_tags = normalize_text_list(pool_rule.get("excluded_tags", []))
    size_filter = normalize_text_list(pool_rule.get("size_filter", []))
    hero_filter = normalize_text(pool_rule.get("hero_filter") or event_data.get("hero_filter"))
    hero_scope = normalize_text(pool_rule.get("hero_scope") or "current")
    normalized_current_hero = normalize_text(current_hero)
    event_category = normalize_text(event_data.get("event_category"))
    normalized_event_name = normalize_text(event_data.get("name") or event_name)
    allows_packages = bool(pool_rule.get("allow_package"))
    allows_loot = bool(pool_rule.get("allow_loot"))
    allows_quest = bool(pool_rule.get("allow_quest"))
    expected_card_type = "skill" if recommender.event_has_skill_reward(event_data) else "item"

    rarity_filter = recommender.resolve_event_rarity_filter(
        pool_rule,
        current_day,
        rarity_rules,
    )
    if rarity_filter is None:
        rarity_filter = {"min": "bronze", "max": "diamond"}

    included_cards: list[dict[str, Any]] = []
    excluded_cards: list[dict[str, Any]] = []
    first_reason_counts: Counter[str] = Counter()
    reason_flag_counts: Counter[str] = Counter()
    suspicious_included: list[dict[str, Any]] = []
    suspicious_counts: Counter[str] = Counter()

    for card_name, card_data in cards.items():
        reason_flags: list[str] = []
        first_reason: str | None = None

        def reject(reason: str) -> None:
            nonlocal first_reason
            reason_flags.append(reason)
            if first_reason is None:
                first_reason = reason

        card_type = normalize_text(card_data.get("type") or card_data.get("card_type"))
        if card_type != expected_card_type:
            reject("type")

        card_tags = normalize_text_list(card_data.get("tags", []))
        card_tag_set = set(card_tags)
        name_text = normalize_text(card_name)
        internal_name = normalize_text(card_data.get("internal_name"))

        exact_match = card_name in exact_names
        if first_reason is None and event_category != "shops" and "package" in card_tag_set and normalized_event_name not in {"farai", "法莱"}:
            reject("package")

        if first_reason is None and event_category == "shops":
            if "loot" in card_tag_set and not allows_loot:
                reject("shop_default_loot")
            elif "package" in card_tag_set and not allows_packages:
                reject("shop_default_package")
            elif "quest" in card_tag_set and not allows_quest:
                reject("shop_default_quest")
            elif not exact_match and "legendary" in card_tag_set:
                reject("shop_default_legendary")
            elif not exact_match and ("debug" in card_tag_set or "debug" in name_text or "debug" in internal_name):
                reject("shop_default_debug")
            elif not exact_match and ("template" in card_tag_set or "template" in name_text or "template" in internal_name):
                reject("shop_default_template")

        card_min = normalize_text(card_data.get("min_rarity"))
        card_max = normalize_text(card_data.get("max_rarity"))
        if first_reason is None and (not card_min or not card_max):
            reject("missing_rarity")

        card_hero = normalize_text(card_data.get("hero"))
        card_heroes = {normalize_text(hero) for hero in card_data.get("heroes", [])}
        card_hero_pool = {card_hero} | card_heroes
        card_hero_pool.discard("")

        if first_reason is None:
            if hero_scope == "fixed":
                if not hero_filter or hero_filter not in card_hero_pool:
                    reject("wrong_hero")
            elif hero_scope == "current":
                if not normalized_current_hero or normalized_current_hero not in card_hero_pool:
                    reject("wrong_hero")
            elif hero_scope == "other":
                if not normalized_current_hero or normalized_current_hero in card_hero_pool:
                    reject("wrong_hero")
            elif hero_scope != "any":
                reject("invalid_hero_scope")

        if first_reason is None and size_filter and normalize_text(card_data.get("size")) not in size_filter:
            reject("size")

        if first_reason is None:
            matched_excluded_tags = sorted(card_tag_set & set(excluded_tags))
            if matched_excluded_tags and not (
                event_category == "shops" and exact_match
            ):
                reject("excluded_tag:" + ",".join(matched_excluded_tags))

        if first_reason is None:
            if exact_names:
                if card_name not in exact_names:
                    reject("exact_name")
            elif not recommender.tags_match(card_tags, reward_tags, match_mode):
                reject("reward_tags")

        if first_reason is None:
            try:
                rarity_ok = recommender.rarity_range_intersects(
                    card_min,
                    card_max,
                    rarity_filter["min"],
                    rarity_filter["max"],
                )
            except ValueError as exc:
                reject(f"rarity_error:{exc}")
            else:
                if not rarity_ok:
                    reject("rarity")

        if first_reason is None:
            card_record = selected_card_fields(card_name, card_data)
            included_cards.append(card_record)
            flags = suspicious_flags_for_included(
                card_name=card_name,
                card_data=card_data,
                normalize_text=normalize_text,
                normalize_text_list=normalize_text_list,
            )
            if flags:
                suspicious_counts.update(flags)
                suspicious_included.append({
                    **card_record,
                    "suspicious_flags": flags,
                })
        else:
            first_reason_counts[first_reason] += 1
            reason_flag_counts.update(reason_flags)
            excluded_cards.append({
                **selected_card_fields(card_name, card_data),
                "first_reason": first_reason,
                "reason_flags": reason_flags,
            })

    canonical_cards, canonical_rarity_filter = recommender.infer_possible_cards_for_event(
        event_data,
        cards,
        current_day,
        rarity_rules,
        current_hero,
    )
    canonical_names = {card["name"] for card in canonical_cards}
    audit_names = {card["name"] for card in included_cards}
    parity = {
        "matches_recommender": canonical_names == audit_names,
        "recommender_count": len(canonical_cards),
        "audit_count": len(included_cards),
        "only_in_recommender": sorted(canonical_names - audit_names),
        "only_in_audit": sorted(audit_names - canonical_names),
        "recommender_rarity_filter": canonical_rarity_filter,
    }

    return {
        "event_name": event_name,
        "hero": current_hero,
        "day": current_day,
        "event": event_data,
        "pool_rule": pool_rule,
        "resolved_pool": {
            "expected_card_type": expected_card_type,
            "reward_tags": reward_tags,
            "exact_names": sorted(exact_names),
            "match_mode": match_mode,
            "excluded_tags": excluded_tags,
            "size_filter": size_filter,
            "hero_filter": hero_filter,
            "hero_scope": hero_scope,
            "allows_packages": allows_packages,
            "allows_loot": allows_loot,
            "allows_quest": allows_quest,
            "rarity_filter": rarity_filter,
        },
        "stats": {
            "total_cards_scanned": len(cards),
            "included_count": len(included_cards),
            "excluded_count": len(excluded_cards),
            "excluded_first_reason_counts": dict(first_reason_counts.most_common()),
            "excluded_reason_flag_counts": dict(reason_flag_counts.most_common()),
            "suspicious_included_counts": dict(suspicious_counts.most_common()),
        },
        "parity_check": parity,
        "included_cards": sorted(included_cards, key=lambda card: card["name"].lower()),
        "suspicious_included_cards": sorted(
            suspicious_included,
            key=lambda card: (card["suspicious_flags"], card["name"].lower()),
        ),
        "excluded_cards": sorted(
            excluded_cards,
            key=lambda card: (card["first_reason"], card["name"].lower()),
        ),
    }


def print_summary(report: dict[str, Any], out_path: Path) -> None:
    stats = report.get("stats", {})
    print(f"Event: {report.get('event_name')}")
    print(f"Hero/Day: {report.get('hero')} / Day {report.get('day')}")
    print(f"Included pool count: {stats.get('included_count', 0)}")
    print(f"Excluded count: {stats.get('excluded_count', 0)}")

    rarity_filter = report.get("resolved_pool", {}).get("rarity_filter")
    if rarity_filter:
        print(f"Resolved rarity: {rarity_filter.get('min')} - {rarity_filter.get('max')}")

    print("Excluded first-reason counts:")
    counts = stats.get("excluded_first_reason_counts", {})
    if counts:
        for reason, count in counts.items():
            print(f"  {reason}: {count}")
    else:
        print("  none")

    suspicious_counts = stats.get("suspicious_included_counts", {})
    if suspicious_counts:
        print("Suspicious included counts:")
        for reason, count in suspicious_counts.items():
            print(f"  {reason}: {count}")

    parity = report.get("parity_check", {})
    if parity and not parity.get("matches_recommender", True):
        print("WARNING: audit result does not match recommender.infer_possible_cards_for_event().")
        print(f"  recommender_count: {parity.get('recommender_count')}")
        print(f"  audit_count: {parity.get('audit_count')}")

    print(f"Output file: {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit the final card pool for a Bazaar event/shop without printing card names to the console.",
    )
    parser.add_argument("--event", required=True, help="Event/shop name, e.g. Ande")
    parser.add_argument("--hero", required=True, help="Current hero, e.g. Karnok")
    parser.add_argument("--day", required=True, type=int, help="Current day number")
    parser.add_argument("--project-root", type=Path, default=None, help="Project root. Defaults to parent of scripts/ or cwd.")
    parser.add_argument("--src-dir", type=Path, default=None, help="src directory. Defaults to <project-root>/src.")
    parser.add_argument("--data-dir", type=Path, default=None, help="data directory. Defaults to <project-root>/data.")
    parser.add_argument("--out", type=Path, default=None, help="Output JSON path. Defaults to runtime/pool_audit_<event>_<hero>_day<day>.json")
    args = parser.parse_args()

    project_root = (args.project_root or default_project_root()).resolve()
    src_dir = (args.src_dir or (project_root / "src")).resolve()
    data_dir = (args.data_dir or (project_root / "data")).resolve()
    add_src_to_path(src_dir)

    try:
        import data_loader
        import recommender
    except ImportError as exc:
        raise SystemExit(
            f"Failed to import project modules from {src_dir}: {exc}\n"
            "Run this from the project root, or pass --project-root / --src-dir."
        ) from exc

    cards_path = data_dir / "cards_generated.json"
    events_path = data_dir / "events.json"
    overrides_path = data_dir / "event_overrides.json"
    rarity_rules_path = data_dir / "rarity_rules.json"

    for path in [cards_path, events_path, rarity_rules_path]:
        if not path.exists():
            raise SystemExit(f"Required file not found: {path}")

    cards = load_json(cards_path)
    raw_events = load_json(events_path)
    flattened_events = data_loader.flatten_events_list(raw_events)
    if overrides_path.exists():
        event_overrides = load_json(overrides_path)
        flattened_events = data_loader.apply_event_overrides(flattened_events, event_overrides)
    rarity_rules = load_json(rarity_rules_path)

    resolved_event_name, event_data = resolve_event(flattened_events, args.event)

    report = audit_event_pool(
        event_name=resolved_event_name,
        event_data=event_data,
        cards=cards,
        current_day=args.day,
        current_hero=args.hero,
        rarity_rules=rarity_rules,
        recommender=recommender,
    )

    out_path = args.out
    if out_path is None:
        out_path = project_root / "runtime" / (
            f"pool_audit_{safe_filename(resolved_event_name)}_"
            f"{safe_filename(args.hero)}_day{args.day}.json"
        )
    elif not out_path.is_absolute():
        out_path = project_root / out_path

    write_json(out_path, report)
    print_summary(report, out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
