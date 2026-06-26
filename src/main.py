from __future__ import annotations

import argparse
import json
from pathlib import Path

from advisor import analyze_game_state
from ai_advisor import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    analyze_with_ai,
    compact_recommendations,
)
from build_strategy import applicable_build_names, format_build_timing_summary, get_game_stage_for_day
from data_loader import load_all_data
from game_state import GameState
from recommender import RECOMMENDATION_RANK, analyze_event, print_event_analysis


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def available_heroes(data: dict) -> list[str]:
    return sorted(
        {
            hero
            for card in data["cards"].values()
            for hero in card.get("heroes", [])
            if hero != "Common"
        }
    )


def event_available_for_hero(event_data: dict, hero: str) -> bool:
    event_heroes = event_data.get("event_heroes", [])
    if not event_heroes:
        return True
    return hero in event_heroes or "Common" in event_heroes


def event_index_for_hero(data: dict, hero: str) -> dict[int, str]:
    event_names = [
        name
        for name, event_data in data["events"].items()
        if event_available_for_hero(event_data, hero)
    ]
    return {index + 1: name for index, name in enumerate(event_names)}


def parse_owned_cards(raw_text: str) -> dict[str, str]:
    """Parse owned cards in the format: Ambergris:gold,Ballista:silver."""
    owned_cards: dict[str, str] = {}

    for pair in raw_text.strip().split(","):
        if ":" not in pair:
            continue

        card_name, rarity = pair.split(":", 1)
        card_name = card_name.strip()
        rarity = rarity.strip().lower()

        if card_name and rarity:
            owned_cards[card_name] = rarity

    return owned_cards


def choose_from_list(title: str, options: list[str]) -> str | None:
    print(f"\n{title}")
    for index, name in enumerate(options, 1):
        print(f"{index}. {name}")

    try:
        choice = int(input("\nChoose a number: "))
        if 1 <= choice <= len(options):
            return options[choice - 1]
    except ValueError:
        pass

    print("Invalid input.")
    return None


def choose_multiple(title: str, index_map: dict[int, str], count: int) -> list[str] | None:
    print(f"\n{title}")
    for number, name in index_map.items():
        print(f"{number}. {name}")

    selected: list[str] = []
    for index in range(1, count + 1):
        try:
            choice = int(input(f"Event {index} number: "))
            if choice not in index_map:
                raise ValueError
            selected.append(index_map[choice])
        except ValueError:
            print("Invalid event number.")
            return None

    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Analyze The Bazaar shops/events and recommend the best choice."
    )
    parser.add_argument("--hero", help="Hero name, for example Vanessa.")
    parser.add_argument("--build", help="Build name, for example VanessaAquaticAmmo.")
    parser.add_argument("--day", type=int, help="Current game day.")
    parser.add_argument(
        "--events",
        nargs="+",
        help="Event/shop names to compare, for example Nautica Colt Goldie.",
    )
    parser.add_argument(
        "--owned",
        default="",
        help="Owned cards, for example Ambergris:gold,Ballista:silver.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Only print the top N recommendations.",
    )
    parser.add_argument(
        "--state-json",
        type=Path,
        help="Read a full game state JSON file for realtime/manual advisor mode.",
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="Send the compact recommendation summary to DeepSeek for natural-language analysis.",
    )
    parser.add_argument(
        "--ai-dry-run",
        action="store_true",
        help="Print the compact AI prompt payload without calling DeepSeek.",
    )
    parser.add_argument(
        "--ai-model",
        default=DEFAULT_DEEPSEEK_MODEL,
        help=f"DeepSeek model name. Default: {DEFAULT_DEEPSEEK_MODEL}.",
    )
    parser.add_argument(
        "--ai-base-url",
        default=DEFAULT_DEEPSEEK_BASE_URL,
        help=f"DeepSeek-compatible API base URL. Default: {DEFAULT_DEEPSEEK_BASE_URL}.",
    )
    parser.add_argument(
        "--ai-timeout",
        type=int,
        default=30,
        help="DeepSeek API timeout in seconds.",
    )
    return parser.parse_args()


def resolve_inputs(args: argparse.Namespace, data: dict) -> tuple[str, str, int, list[str], dict[str, str]] | None:
    builds = data["builds"]
    events = data["events"]

    hero = args.hero
    if not hero:
        hero = choose_from_list("Available heroes:", available_heroes(data))

    if not hero:
        print("Hero is required.")
        return None

    if hero not in available_heroes(data):
        print(f"Unknown hero: {hero}")
        return None

    build_name = args.build
    if not build_name:
        preview_day = args.day or 1
        matching_builds = applicable_build_names(builds, hero, preview_day)
        if not matching_builds:
            matching_builds = [
                name
                for name, build_data in builds.items()
                if build_data.get("hero") in (None, hero)
            ]
        build_name = choose_from_list("Available builds:", matching_builds)

    if not build_name or build_name not in builds:
        print(f"Unknown build: {build_name}")
        return None

    build_hero = builds[build_name].get("hero")
    if build_hero and build_hero != hero:
        print(f"Build {build_name} is configured for {build_hero}, not {hero}.")
        return None

    current_day = args.day
    if current_day is None:
        try:
            current_day = int(input("\nCurrent day: "))
        except ValueError:
            print("Day must be a positive integer.")
            return None

    if current_day <= 0:
        print("Day must be a positive integer.")
        return None

    event_names = args.events
    if not event_names:
        event_names = choose_multiple("Available events:", event_index_for_hero(data, hero), count=3)

    if not event_names:
        return None

    missing_events = [name for name in event_names if name not in events]
    if missing_events:
        print(f"Unknown events: {', '.join(missing_events)}")
        return None

    unavailable_events = [
        name
        for name in event_names
        if name in events and not event_available_for_hero(events[name], hero)
    ]
    if unavailable_events:
        print(f"Events unavailable for {hero}: {', '.join(unavailable_events)}")
        return None

    owned_raw = args.owned
    if args.events is None:
        owned_raw = input(
            "\nOwned cards (format: Card:rarity,Card:rarity; leave empty if none): "
        ).strip()

    return hero, build_name, current_day, event_names, parse_owned_cards(owned_raw)


def run_analysis(
    data: dict,
    hero: str,
    build_name: str,
    current_day: int,
    event_names: list[str],
    owned_cards: dict[str, str],
    top: int | None = None,
) -> list[dict]:
    results = []

    for event_name in event_names:
        result = analyze_event(
            event_name=event_name,
            event_data=data["events"][event_name],
            cards=data["cards"],
            build_name=build_name,
            build_data=data["builds"][build_name],
            current_day=current_day,
            rarity_rules=data["rarity_rules"],
            current_hero=hero,
            owned_cards=owned_cards,
        )
        results.append(result)

    results.sort(
        key=lambda result: (
            RECOMMENDATION_RANK.get(result["recommendation"], 99),
            -result["pool_stats"]["expected_valuable_in_shop"],
            result["event_name"],
        )
    )

    return results[:top] if top else results


def run_state_analysis(data: dict, state: GameState, top: int | None = None) -> list[dict]:
    advisor_result = analyze_game_state(data, state, top=top)
    if advisor_result.warnings:
        for warning in advisor_result.warnings:
            print(f"Warning: {warning}")
        return []

    print("=== The Bazaar Realtime Advisor ===")
    print(f"Source: {state.source}")
    print(f"Hero: {state.hero}")
    print(f"Build: {state.build}")
    print(f"Day: {state.day}")
    print(f"Game stage: {get_game_stage_for_day(state.day)}")
    print(f"Build timing: {format_build_timing_summary(data['builds'][state.build], state.day)}")
    if state.visible_cards:
        print(f"Visible cards: {', '.join(state.visible_cards)}")

    for result in advisor_result.recommendations:
        print_event_analysis(result)

    return advisor_result.recommendations


def load_state_json(path: Path) -> GameState:
    return GameState.from_dict(json.loads(path.read_text(encoding="utf-8")))


def run_ai_analysis(
    args: argparse.Namespace,
    data: dict,
    hero: str,
    build_name: str,
    current_day: int,
    owned_cards: dict[str, str],
    results: list[dict],
) -> None:
    payload = compact_recommendations(
        data=data,
        hero=hero,
        build_name=build_name,
        current_day=current_day,
        owned_cards=owned_cards,
        results=results,
    )

    if args.ai_dry_run:
        print("\n=== AI Prompt Payload Preview ===")
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    if not args.ai:
        return

    print("\n=== AI Analysis ===")
    try:
        print(
            analyze_with_ai(
                payload,
                model=args.ai_model,
                base_url=args.ai_base_url,
                timeout=args.ai_timeout,
            )
        )
    except RuntimeError as exc:
        print(f"AI analysis failed: {exc}")


def main() -> None:
    args = parse_args()
    data = load_all_data(DATA_DIR)

    if args.state_json:
        state = load_state_json(args.state_json)
        results = run_state_analysis(data, state, top=args.top)
        if results and (args.ai or args.ai_dry_run):
            run_ai_analysis(
                args=args,
                data=data,
                hero=state.hero,
                build_name=state.build,
                current_day=state.day,
                owned_cards=state.owned_cards,
                results=results,
            )
        return

    resolved = resolve_inputs(args, data)
    if resolved is None:
        return

    hero, build_name, current_day, event_names, owned_cards = resolved

    print("=== The Bazaar Event & Build Decision Helper ===")
    print(f"Hero: {hero}")
    print(f"Build: {build_name}")
    print(f"Game stage: {get_game_stage_for_day(current_day)}")
    print(f"Build timing: {format_build_timing_summary(data['builds'][build_name], current_day)}")

    results = run_analysis(
        data=data,
        hero=hero,
        build_name=build_name,
        current_day=current_day,
        event_names=event_names,
        owned_cards=owned_cards,
        top=args.top,
    )

    for result in results:
        print_event_analysis(result)

    if results and (args.ai or args.ai_dry_run):
        run_ai_analysis(
            args=args,
            data=data,
            hero=hero,
            build_name=build_name,
            current_day=current_day,
            owned_cards=owned_cards,
            results=results,
        )


if __name__ == "__main__":
    main()
