from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SRC_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from ai_advisor import (
    LAST_AI_PROMPT_DEBUG,
    build_ai_messages,
    compact_recommendations,
    prepare_ai_payload_for_model,
)
from build_strategy import build_applies_to_day, get_game_stage_for_day
from data_loader import load_all_data as load_project_data
from game_state import GameState
from main import event_index_for_hero, parse_owned_cards, run_analysis
from advisor import analyze_game_state
from recommender import infer_possible_cards_for_event, probability_at_least_one
from recommender import analyze_event
from shop_pool_cache import clear_shop_pool_cache, get_cached_shop_pool_summary
from audit_event_pool import audit_event_pool
import recommender
import web_app
from web_app import analyze_payload, normalize_payload_for_analysis


DATA_DIR = PROJECT_ROOT / "data"
BUILD_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "builds.json"


def load_all_data(data_dir: Path) -> dict:
    data = load_project_data(data_dir)
    fixture_builds = json.loads(BUILD_FIXTURE_PATH.read_text(encoding="utf-8"))
    data["builds"].update(fixture_builds)
    return data


class RecommenderTests(unittest.TestCase):
    def test_project_uses_single_community_build_and_rating_sources(self) -> None:
        project_data = load_project_data(DATA_DIR)
        community_builds = json.loads(
            (DATA_DIR / "community_builds.json").read_text(encoding="utf-8")
        )

        self.assertFalse((DATA_DIR / "builds.json").exists())
        self.assertFalse((DATA_DIR / "cards.json").exists())
        self.assertEqual(project_data["builds"], community_builds)
        self.assertTrue((DATA_DIR / "card_ratings.json").exists())
        self.assertEqual(project_data["cards"]["Ballista"]["tier"], "A")
        self.assertEqual(project_data["cards"]["Ballista"]["build_roles"], {})

    @staticmethod
    def _analyze_alt_core_cards(
        card_names: list[str],
        current_day: int = 3,
        alt_build_updates: dict | None = None,
    ) -> dict:
        cards = {
            name: {
                "type": "Item",
                "hero": "Vanessa",
                "heroes": ["Vanessa"],
                "tags": [],
                "min_rarity": "bronze",
                "max_rarity": "diamond",
                "tier": "C",
            }
            for name in card_names
        }
        builds = {
            "CurrentBuild": {
                "hero": "Vanessa",
                "core_cards": ["Current Core"],
                "transition_cards": [],
                "optional_cards": [],
            },
            "AltBuild": {
                "hero": "Vanessa",
                "display_name": "备用阵容",
                "day_range": [1, 8],
                "core_cards": ["Alt Core One", "Alt Core Two"],
            },
            "OtherHeroBuild": {
                "hero": "Pygmalien",
                "display_name": "跨英雄阵容",
                "core_cards": ["Alt Core One"],
            },
        }
        if alt_build_updates:
            builds["AltBuild"].update(alt_build_updates)
        return analyze_event(
            event_name="Test Reward",
            event_data={
                "event_category": "item_rewards",
                "card_reward": {
                    "enabled": True,
                    "exact_names": card_names,
                    "hero_scope": "current",
                    "count": 1,
                },
            },
            cards=cards,
            build_name="CurrentBuild",
            build_data=builds["CurrentBuild"],
            current_day=current_day,
            rarity_rules={},
            current_hero="Vanessa",
            all_builds=builds,
        )

    def test_recognizes_other_current_hero_build_core(self) -> None:
        result = self._analyze_alt_core_cards(["Alt Core One"])

        self.assertEqual(result["alt_core_card_count"], 1)
        self.assertEqual(result["recommendation"], "Low Value")
        self.assertEqual(
            result["possible_cards"][0]["alt_core_build_hits"],
            [{"build_name": "AltBuild", "display_name": "备用阵容"}],
        )
        self.assertTrue(any("alternate build(s)" in reason for reason in result["reasons"]))

    def test_current_build_core_is_not_an_alt_core_hit(self) -> None:
        result = self._analyze_alt_core_cards(["Current Core"])

        self.assertEqual(result["possible_cards"][0]["role"], "core")
        self.assertEqual(result["possible_cards"][0]["alt_core_build_hits"], [])
        self.assertEqual(result["alt_core_card_count"], 0)

    def test_two_alt_core_cards_promote_low_to_medium(self) -> None:
        result = self._analyze_alt_core_cards(["Alt Core One", "Alt Core Two"])

        self.assertEqual(result["alt_core_card_count"], 2)
        self.assertEqual(result["recommendation"], "Medium Value")
        self.assertTrue(
            any("alternate-build core cards" in reason for reason in result["reasons"])
        )

    def test_alt_core_stage_filter_allows_early_late_but_excludes_past(self) -> None:
        early_result = self._analyze_alt_core_cards(
            ["Alt Core One"],
            current_day=3,
            alt_build_updates={
                "applicable_stages": ["late"],
                "day_range": None,
            },
        )
        late_result = self._analyze_alt_core_cards(
            ["Alt Core One"],
            current_day=10,
            alt_build_updates={
                "applicable_stages": ["mid"],
                "day_range": None,
            },
        )

        self.assertEqual(early_result["alt_core_card_count"], 1)
        self.assertEqual(late_result["alt_core_card_count"], 0)

    def test_probability_at_least_one(self) -> None:
        self.assertEqual(probability_at_least_one(0.0), 0.0)
        self.assertEqual(probability_at_least_one(1.0), 1.0)
        self.assertEqual(round(probability_at_least_one(0.5, draws=2), 2), 0.75)

    def test_parse_owned_cards(self) -> None:
        self.assertEqual(
            parse_owned_cards("Ambergris:Gold, Ballista:silver"),
            {
                "Ambergris": "gold",
                "Ballista": "silver",
            },
        )

    def test_run_analysis_returns_ranked_results(self) -> None:
        data = load_all_data(DATA_DIR)
        results = run_analysis(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=5,
            event_names=["Nautica", "Colt", "Goldie"],
            owned_cards={"Ballista": "gold"},
        )

        self.assertTrue([result["event_name"] for result in results])
        self.assertTrue(all("recommendation" in result for result in results))
        self.assertTrue(
            all(result["pool_stats"]["total_pool_count"] >= 0 for result in results)
        )
        self.assertIn(
            results[0]["recommendation"],
            {"High Value", "Medium Value", "Low Value"},
        )

    def test_analyze_game_state(self) -> None:
        data = load_all_data(DATA_DIR)
        state = GameState(
            hero="Vanessa",
            build="VanessaAquaticAmmo",
            day=5,
            event_options=["Nautica", "Colt"],
            owned_cards={"Ballista": "gold"},
        )

        result = analyze_game_state(data, state)

        self.assertFalse(result.warnings)
        self.assertEqual(len(result.recommendations), 2)
        self.assertIsNotNone(result.best)

    def test_imported_cache_cards_keep_identity_and_hidden_tags(self) -> None:
        data = load_all_data(DATA_DIR)
        ballista = data["cards"]["Ballista"]

        self.assertEqual(ballista["id"], "096e4b73-803c-4405-9710-db71b20fb183")
        self.assertIn("ammo", ballista["tags"])
        self.assertIn("damage", ballista["tags"])

    def test_size_filter_limits_shop_pool(self) -> None:
        data = load_all_data(DATA_DIR)
        cards, _ = infer_possible_cards_for_event(
            event_data=data["events"]["Ande"],
            cards=data["cards"],
            current_day=5,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertTrue(cards)
        self.assertTrue(all(card["raw"]["size"] == "Small" for card in cards))

    def test_non_weapon_shop_excludes_weapons_without_emptying_pool(self) -> None:
        data = load_all_data(DATA_DIR)
        cards, _ = infer_possible_cards_for_event(
            event_data=data["events"]["Kina"],
            cards=data["cards"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertTrue(cards)
        self.assertTrue(all("weapon" not in card["tags"] for card in cards))

    def test_hero_filter_limits_shop_pool(self) -> None:
        data = load_all_data(DATA_DIR)
        cards, _ = infer_possible_cards_for_event(
            event_data=data["events"]["Vanessa"],
            cards=data["cards"],
            current_day=5,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertTrue(cards)
        self.assertTrue(
            all("Vanessa" in card["raw"].get("heroes", []) for card in cards)
        )

    def test_default_shop_pool_uses_only_current_hero(self) -> None:
        data = load_all_data(DATA_DIR)
        cards, _ = infer_possible_cards_for_event(
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            current_day=5,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertTrue(cards)
        self.assertTrue(
            all(
                card["raw"]["hero"] == "Vanessa"
                or "Vanessa" in card["raw"].get("heroes", [])
                for card in cards
            )
        )

    def test_shop_pool_scope_and_default_tag_rules(self) -> None:
        def card(hero: str, *tags: str) -> dict:
            return {
                "type": "Item",
                "hero": hero,
                "heroes": [hero],
                "size": "Small",
                "tags": list(tags),
                "min_rarity": "bronze",
                "max_rarity": "diamond",
            }

        cards = {
            "Current": card("Vanessa"),
            "Neutral": card("Common"),
            "Other": card("Pygmalien"),
            "Loot": card("Vanessa", "loot"),
            "Package": card("Vanessa", "package"),
            "Legendary": card("Vanessa", "legendary"),
            "Debug": card("Vanessa", "debug"),
            "Template": card("Vanessa", "template"),
            "Debug Name": card("Vanessa"),
            "Internal Template": {
                **card("Vanessa"),
                "internal_name": "Shop Item Template",
            },
        }
        base_event = {
            "event_category": "shops",
            "event_heroes": ["Common"],
            "shop_pool": {},
        }

        current_pool, _ = infer_possible_cards_for_event(
            base_event, cards, 5, {}, current_hero="Vanessa"
        )
        self.assertEqual([entry["name"] for entry in current_pool], ["Current"])

        any_pool, _ = infer_possible_cards_for_event(
            {**base_event, "shop_pool": {"hero_scope": "any"}},
            cards,
            5,
            {},
            current_hero="Vanessa",
        )
        self.assertEqual(
            {entry["name"] for entry in any_pool},
            {"Current", "Neutral", "Other"},
        )

        common_pool, _ = infer_possible_cards_for_event(
            {
                **base_event,
                "shop_pool": {
                    "hero_scope": "fixed",
                    "hero_filter": "Common",
                },
            },
            cards,
            5,
            {},
            current_hero="Vanessa",
        )
        self.assertEqual([entry["name"] for entry in common_pool], ["Neutral"])

    def test_shop_special_tags_require_explicit_allow_flags(self) -> None:
        special_cards = {
            name: {
                "type": "Item",
                "hero": "Vanessa",
                "heroes": ["Vanessa"],
                "size": "Small",
                "tags": [tag],
                "min_rarity": "bronze",
                "max_rarity": "diamond",
            }
            for name, tag in (
                ("Loot", "loot"),
                ("Package", "package"),
                ("Quest", "quest"),
            )
        }

        for name, flag in (
            ("Loot", "allow_loot"),
            ("Package", "allow_package"),
            ("Quest", "allow_quest"),
        ):
            cards, _ = infer_possible_cards_for_event(
                {
                    "event_category": "shops",
                    "shop_pool": {
                        "exact_names": [name],
                        flag: True,
                    },
                },
                special_cards,
                5,
                {},
                current_hero="Vanessa",
            )
            self.assertEqual([entry["name"] for entry in cards], [name])

    def test_exact_names_can_allow_debug_or_template_cards(self) -> None:
        cards = {
            "Debug Prototype": {
                "type": "Item",
                "hero": "Vanessa",
                "heroes": ["Vanessa"],
                "tags": ["debug"],
                "min_rarity": "bronze",
                "max_rarity": "diamond",
            }
        }
        pool, _ = infer_possible_cards_for_event(
            {
                "event_category": "shops",
                "shop_pool": {"exact_names": ["Debug Prototype"]},
            },
            cards,
            3,
            {},
            current_hero="Vanessa",
        )
        self.assertEqual([entry["name"] for entry in pool], ["Debug Prototype"])

    def test_generated_special_shop_rules(self) -> None:
        data = load_all_data(DATA_DIR)
        events = data["events"]

        self.assertEqual(events["Aimbot"]["shop_pool"]["reward_tags"], ["crit"])
        self.assertEqual(events["Aimbot"]["shop_pool"]["hero_scope"], "any")
        self.assertEqual(events["Pinfeather"]["shop_pool"]["reward_tags"], ["flying"])
        self.assertEqual(events["Pinfeather"]["shop_pool"]["hero_scope"], "any")
        self.assertEqual(
            events["Barkun"]["shop_pool"]["size_filter"], ["medium", "large"]
        )
        self.assertEqual(
            events["Quixel"]["shop_pool"]["size_filter"], ["small", "medium"]
        )
        self.assertEqual(
            events["The Travel Agent"]["shop_pool"]["reward_tags"], ["ticket"]
        )
        self.assertEqual(events["The Travel Agent"]["shop_pool"]["hero_scope"], "any")
        self.assertEqual(events["Stickybeans"]["shop_pool"]["hero_scope"], "other")
        self.assertEqual(
            events["Prospero"]["shop_pool"]["reward_tags"],
            ["economyreference", "value", "income"],
        )
        self.assertTrue(
            events["Serafina"]["shop_pool"]["enchantment_required"]
        )
        self.assertEqual(events["Street Festival"]["event_category"], "utility_events")

    def test_special_shop_pools_follow_generated_rules(self) -> None:
        data = load_all_data(DATA_DIR)

        for event_name, required_tag in (
            ("Aimbot", "crit"),
            ("Pinfeather", "flying"),
            ("The Travel Agent", "ticket"),
        ):
            pool, _ = infer_possible_cards_for_event(
                data["events"][event_name],
                data["cards"],
                10,
                data["rarity_rules"],
                current_hero="Karnok",
            )
            self.assertTrue(pool, event_name)
            self.assertTrue(
                all(required_tag in card["tags"] for card in pool),
                event_name,
            )
            if event_name == "The Travel Agent":
                self.assertEqual(len(pool), 2)

        for event_name, allowed_sizes in (
            ("Barkun", {"medium", "large"}),
            ("Quixel", {"small", "medium"}),
        ):
            pool, _ = infer_possible_cards_for_event(
                data["events"][event_name],
                data["cards"],
                3,
                data["rarity_rules"],
                current_hero="Karnok",
            )
            self.assertTrue(pool, event_name)
            self.assertTrue(
                all(card["raw"]["size"].lower() in allowed_sizes for card in pool)
            )

        other_pool, _ = infer_possible_cards_for_event(
            data["events"]["Stickybeans"],
            data["cards"],
            3,
            data["rarity_rules"],
            current_hero="Karnok",
        )
        self.assertTrue(other_pool)
        self.assertTrue(
            all(
                "Karnok" not in card["raw"].get("heroes", [])
                and card["raw"].get("hero") != "Karnok"
                for card in other_pool
            )
        )

        enchanted_pool, _ = infer_possible_cards_for_event(
            data["events"]["Serafina"],
            data["cards"],
            3,
            data["rarity_rules"],
            current_hero="Karnok",
        )
        self.assertTrue(enchanted_pool)
        self.assertTrue(
            all(card["enchantment_required"] for card in enchanted_pool)
        )

    def test_ande_karnok_day_three_audit_matches_recommender(self) -> None:
        data = load_all_data(DATA_DIR)
        event = data["events"]["Ande"]
        report = audit_event_pool(
            event_name="Ande",
            event_data=event,
            cards=data["cards"],
            current_day=3,
            current_hero="Karnok",
            rarity_rules=data["rarity_rules"],
            recommender=recommender,
        )

        self.assertGreater(report["parity_check"]["audit_count"], 0)
        self.assertEqual(
            report["parity_check"]["audit_count"],
            report["parity_check"]["recommender_count"],
        )
        self.assertTrue(report["parity_check"]["matches_recommender"])

    def test_any_hero_shop_pool_can_include_other_heroes(self) -> None:
        data = load_all_data(DATA_DIR)
        cards, _ = infer_possible_cards_for_event(
            event_data=data["events"]["Gaseo"],
            cards=data["cards"],
            current_day=5,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertTrue(cards)
        self.assertTrue(any(card["raw"]["hero"] not in {"Vanessa", "Common"} for card in cards))

    def test_shop_pool_cache_is_hero_specific_and_matches_existing_resolver(self) -> None:
        data = load_all_data(DATA_DIR)
        clear_shop_pool_cache()

        vanessa_summary = get_cached_shop_pool_summary(
            data=data,
            event_name="Colt",
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            current_day=5,
            current_hero="Vanessa",
            rarity_rules=data["rarity_rules"],
            resolver=infer_possible_cards_for_event,
        )
        karnok_summary = get_cached_shop_pool_summary(
            data=data,
            event_name="Colt",
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            current_day=5,
            current_hero="Karnok",
            rarity_rules=data["rarity_rules"],
            resolver=infer_possible_cards_for_event,
        )
        early_aimbot_summary = get_cached_shop_pool_summary(
            data=data,
            event_name="Aimbot",
            event_data=data["events"]["Aimbot"],
            cards=data["cards"],
            current_day=1,
            current_hero="Vanessa",
            rarity_rules=data["rarity_rules"],
            resolver=infer_possible_cards_for_event,
        )
        direct_pool, _ = infer_possible_cards_for_event(
            data["events"]["Colt"],
            data["cards"],
            5,
            data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertEqual(vanessa_summary["cache_status"], "hit")
        self.assertEqual(vanessa_summary["stage"], "gold_unlocked")
        self.assertEqual(vanessa_summary["available_days"], "1-10+")
        self.assertTrue(vanessa_summary["available_on_day"])
        self.assertEqual(early_aimbot_summary["available_days"], "6-10+")
        self.assertFalse(early_aimbot_summary["available_on_day"])
        self.assertEqual(
            set(vanessa_summary["card_names"]),
            {card["name"] for card in direct_pool},
        )
        self.assertNotEqual(
            set(vanessa_summary["card_names"]),
            set(karnok_summary["card_names"]),
        )

    def test_shop_analysis_exposes_summary_without_full_pool_in_ai_payload(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Colt",
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=5,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            all_builds=data["builds"],
            data_context=data,
        )
        payload = compact_recommendations(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=5,
            owned_cards={},
            results=[result],
        )
        encoded = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(result["shop_pool_summary"]["cache_status"], "hit")
        self.assertEqual(result["shop_pool_summary"]["available_days"], "1-10+")
        self.assertEqual(
            result["shop_pool_summary"]["pool_count"],
            result["pool_stats"]["total_pool_count"],
        )
        self.assertIsNotNone(result["shop_entry_analysis"]["target_density_rank"])
        self.assertGreaterEqual(
            len(result["shop_entry_analysis"]["top_day_merchants_by_density"]),
            1,
        )
        self.assertIn("shop_pool_summary", encoded)
        self.assertIn("top_day_merchants_by_density", encoded)
        self.assertNotIn("card_names", encoded)
        self.assertNotIn("card_ids", encoded)

    def test_item_and_skill_pools_are_kept_separate(self) -> None:
        cards = {
            "Test Item": {
                "type": "Item",
                "hero": "Vanessa",
                "heroes": ["Vanessa"],
                "size": "Small",
                "tags": [],
                "min_rarity": "bronze",
                "max_rarity": "diamond",
            },
            "Test Skill": {
                "type": "Skill",
                "hero": "Vanessa",
                "heroes": ["Vanessa"],
                "size": "Medium",
                "tags": [],
                "min_rarity": "bronze",
                "max_rarity": "diamond",
            },
            "Test Encounter": {
                "type": "EventEncounter",
                "hero": "Common",
                "heroes": ["Common"],
                "size": "Medium",
                "tags": [],
                "min_rarity": "bronze",
                "max_rarity": "diamond",
            },
        }

        item_pool, _ = infer_possible_cards_for_event(
            event_data={"event_category": "shops", "shop_pool": {}},
            cards=cards,
            current_day=5,
            rarity_rules={},
            current_hero="Vanessa",
        )
        skill_pool, _ = infer_possible_cards_for_event(
            event_data={"event_category": "skill_shops", "shop_pool": {}},
            cards=cards,
            current_day=5,
            rarity_rules={},
            current_hero="Vanessa",
        )

        self.assertEqual([card["name"] for card in item_pool], ["Test Item"])
        self.assertEqual([card["name"] for card in skill_pool], ["Test Skill"])

    def test_game_state_requires_matching_hero(self) -> None:
        data = load_all_data(DATA_DIR)
        state = GameState(
            hero="Dooley",
            build="VanessaAquaticAmmo",
            day=5,
            event_options=["Nautica"],
        )

        self.assertIn(
            "阵容 VanessaAquaticAmmo 属于 Vanessa，不适用于 Dooley。",
            state.validate_against(data),
        )

    def test_game_state_rejects_event_unavailable_for_hero(self) -> None:
        data = load_all_data(DATA_DIR)
        state = GameState(
            hero="Vanessa",
            build="VanessaAquaticAmmo",
            day=5,
            event_options=["Aero"],
        )

        self.assertIn(
            "Vanessa 无法遇到这些事件：Aero",
            state.validate_against(data),
        )

    def test_available_event_index_filters_by_hero(self) -> None:
        data = load_all_data(DATA_DIR)
        event_names = set(event_index_for_hero(data, "Vanessa").values())

        self.assertNotIn("Aero", event_names)
        self.assertIn("Nautica", event_names)
        self.assertIn("Gaseo", event_names)

    def test_build_applicable_period_for_day(self) -> None:
        data = load_all_data(DATA_DIR)
        build_data = data["builds"]["VanessaAquaticAmmo"]

        self.assertEqual(get_game_stage_for_day(6), "mid")
        self.assertTrue(build_applies_to_day(build_data, current_day=6))
        self.assertFalse(build_applies_to_day(build_data, current_day=3))

    def test_game_stage_day_boundaries(self) -> None:
        self.assertEqual(get_game_stage_for_day(1), "early")
        self.assertEqual(get_game_stage_for_day(5), "early")
        self.assertEqual(get_game_stage_for_day(6), "mid")
        self.assertEqual(get_game_stage_for_day(9), "mid")
        self.assertEqual(get_game_stage_for_day(10), "late")

    def test_build_config_does_not_use_trap_cards(self) -> None:
        data = load_all_data(DATA_DIR)

        self.assertNotIn("trap_cards", data["builds"]["VanessaAquaticAmmo"])

    def test_ai_payload_is_build_focused_and_prompt_is_guarded(self) -> None:
        data = load_all_data(DATA_DIR)
        data["builds"]["VanessaAquaticAmmo"]["pilot_tips"] = [
            "前期先拿水产/弹药过渡",
            "有弩炮再考虑转入",
        ]
        results = run_analysis(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=6,
            event_names=["Colt", "Kina"],
            owned_cards={"Ballista": "gold"},
        )

        payload = compact_recommendations(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=6,
            owned_cards={"Ballista": "gold"},
            results=results,
            build_analysis={
                "current_phase": "mid",
                "best_matching_builds": [
                    {
                        "build_id": "VanessaAquaticAmmo",
                        "name": "Vanessa Aquatic Ammo",
                        "phase": "mid",
                        "relation": "current_build",
                        "match_band": "developing",
                        "owned_core": ["Ballista"],
                        "missing_core": ["Core Missing"],
                        "owned_optional": ["Optional Hit"],
                    }
                ],
            },
        )
        messages = build_ai_messages(payload)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_messages = json.dumps(messages, ensure_ascii=False)

        self.assertIn("候选阵容", payload)
        self.assertIn("当前状态", payload)
        self.assertIn("事件客观信息", payload)
        self.assertNotIn("候选物品与阵容关系", payload)
        self.assertEqual(
            {item["事件原名"] for item in payload["事件客观信息"]},
            {"Colt", "Kina"},
        )
        self.assertEqual(payload["候选阵容"][0]["已拥有核心卡数量"], 1)
        self.assertEqual(payload["候选阵容"][0]["已拥有可选卡数量"], 1)
        self.assertNotIn("possible_cards", serialized_payload)
        self.assertNotIn('"raw"', serialized_payload)
        self.assertNotIn('"trap"', serialized_payload)
        self.assertNotIn("过渡卡", serialized_payload)
        self.assertNotIn("判断规则", serialized_payload)
        self.assertIn("编造", serialized_messages)
        self.assertNotIn("固定为五行", serialized_messages)
        self.assertIn("2 到 4 个短段落", serialized_messages)
        self.assertIn("参考倾向", serialized_messages)
        self.assertIn("比较所有当前选项", serialized_messages)

    def test_ai_payload_keeps_parent_followup_rewards_and_guide_policy(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = compact_recommendations(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=5,
            owned_cards={},
            results=[
                {
                    "event_name": "Tiny Furry Monster",
                    "event_rule_status": "parent_event",
                    "recommendation": "Low Value",
                    "child_options": [
                        {
                            "name": "爱抚",
                            "source_name": "Pet It",
                            "description": "Gain 25 Max Health",
                            "resource_rewards": {"max_health": 25},
                            "source": "static_encounters_generated",
                        }
                    ],
                    "best_followup_summary": {
                        "resource_rewards": {"max_health": 25},
                    },
                }
            ],
            guide_context=[
                {
                    "章节标题": "事件选择",
                    "正文": "攻略认为前期稳定收益可以帮助后续运营。",
                }
            ],
        )
        messages = build_ai_messages(payload)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_messages = json.dumps(messages, ensure_ascii=False)

        self.assertIn("parent_followup_options", serialized_payload)
        self.assertIn("爱抚", serialized_payload)
        self.assertIn('"max_health": 25', serialized_payload)
        self.assertIn("strategy_guide_context", serialized_payload)
        self.assertIn("parent_followup_options", serialized_messages)
        self.assertIn("不要因为父事件本身没有直接收益", serialized_messages)

    def test_ai_payload_marks_shop_refresh_as_current_shop_only(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Colt",
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            build_name="huokai",
            build_data=data["builds"]["huokai"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            current_shop={
                "visible_items": [{"name": "Unknown Visible Item"}],
                "refresh_available": True,
                "refresh_cost": 1,
                "refreshes_remaining": 2,
            },
            current_gold=20,
            data_context=data,
        )
        payload = compact_recommendations(
            data=data,
            hero="Vanessa",
            build_name="huokai",
            current_day=6,
            owned_cards={},
            results=[result],
        )
        messages = build_ai_messages(payload)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_messages = json.dumps(messages, ensure_ascii=False)

        self.assertEqual(result["shop_inside_analysis"]["refresh_scope"], "current_shop_only")
        self.assertFalse(result["shop_inside_analysis"]["refresh_carries_over"])
        self.assertIn("refreshes_remaining_in_this_shop", serialized_payload)
        self.assertIn("current_shop_only", serialized_payload)
        self.assertIn("\u4e0d\u4f1a\u8de8\u5546\u5e97\u7d2f\u8ba1", serialized_payload)
        self.assertIn("\u4e0d\u4f1a\u7d2f\u8ba1\u5230\u4e0b\u4e00\u5bb6\u5e97", serialized_messages)

    def test_ai_model_payload_trims_repeated_rules_and_records_debug(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = compact_recommendations(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=5,
            owned_cards={},
            results=[
                {
                    "event_name": "Tiny Furry Monster",
                    "event_rule_status": "parent_event",
                    "recommendation": "Low Value",
                    "child_options": [
                        {
                            "name": "爱抚",
                            "source_name": "Pet It",
                            "description": "Gain 25 Max Health",
                            "resource_rewards": {"max_health": 25},
                        }
                    ],
                    "best_followup_summary": {
                        "resource_rewards": {"max_health": 25},
                    },
                }
            ],
            guide_context=[{"章节标题": "事件选择", "正文": "稳定资源可作为过渡依据。"}],
        )

        model_payload, debug = prepare_ai_payload_for_model(payload)
        messages = build_ai_messages(payload)
        serialized_messages = json.dumps(messages, ensure_ascii=False)

        self.assertIn("AI_priority_rules", payload)
        self.assertNotIn("AI_priority_rules", serialized_messages)
        self.assertIn("strategy_guide_context", model_payload)
        self.assertNotIn("相关攻略", model_payload)
        self.assertLess(debug["summary_json_chars_after"], debug["summary_json_chars_before"])
        self.assertEqual(LAST_AI_PROMPT_DEBUG["analysis_scene"], "event_selection")
        self.assertIn("final_prompt_chars", LAST_AI_PROMPT_DEBUG)

    def test_ai_model_payload_trims_before_entering_shop_context(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Colt",
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=5,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            all_builds=data["builds"],
            data_context=data,
        )
        payload = compact_recommendations(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=5,
            owned_cards={},
            results=[result],
            current_shop={
                "merchant_name": "Colt",
                "visible_items": [{"name": "Unknown Visible Item"}],
            },
            build_analysis={
                "candidate_cards": [{"card_display_name": "Shop Card"}],
                "visible_core_bundles": [{"build_name": "Bundle"}],
            },
        )

        model_payload, debug = prepare_ai_payload_for_model(payload)
        serialized_model = json.dumps(model_payload, ensure_ascii=False)

        self.assertEqual(debug["analysis_scene"], "before_entering_shop")
        self.assertIn("shop_phase_analysis", model_payload)
        self.assertNotIn("Unknown Visible Item", serialized_model)
        self.assertNotIn("Shop Card", serialized_model)
        self.assertNotIn("visible_items", serialized_model)

    def test_ai_model_payload_trims_inside_shop_context(self) -> None:
        data = load_all_data(DATA_DIR)
        current_shop = {
            "visible_items": [{"name": "Ballista", "price": 12}],
            "refresh_available": True,
            "refresh_cost": 1,
            "refreshes_remaining": 2,
        }
        result = analyze_event(
            event_name="Colt",
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            current_shop=current_shop,
            current_gold=20,
            data_context=data,
        )
        payload = compact_recommendations(
            data=data,
            hero="Vanessa",
            build_name="VanessaAquaticAmmo",
            current_day=6,
            owned_cards={},
            results=[result],
            current_gold=20,
            current_shop=current_shop,
        )

        model_payload, debug = prepare_ai_payload_for_model(payload)
        serialized_model = json.dumps(model_payload, ensure_ascii=False)

        self.assertEqual(debug["analysis_scene"], "inside_shop")
        self.assertNotIn("事件客观信息", model_payload)
        self.assertIn("弩炮", serialized_model)
        self.assertIn("refreshes_remaining_in_this_shop", serialized_model)

    def test_ai_payload_uses_all_event_options_when_ui_top_is_limited(self) -> None:
        web_app.ANALYSIS_CACHE.clear()
        web_app.AI_PAYLOAD_CACHE.clear()
        web_app.AI_ANALYSIS_CACHE.clear()
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_options": ["Colt", "Kina"],
            "owned_cards": [{"name": "Ballista", "rarity": "gold"}],
        }
        captured_payloads: list[dict] = []

        def capture_ai_payload(ai_payload: dict) -> str:
            captured_payloads.append(ai_payload)
            return "ok"

        with patch.object(web_app, "analyze_with_ai", side_effect=capture_ai_payload):
            response = analyze_payload(data, payload, top=1, include_ai=True)

        self.assertEqual(response["ai_analysis"], "ok")
        self.assertEqual(len(response["recommendations"]), 1)
        self.assertEqual(
            {item["事件原名"] for item in captured_payloads[0]["事件客观信息"]},
            {"Colt", "Kina"},
        )

    def test_web_payload_can_auto_select_build_for_minimal_plugin_state(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_options": ["Colt", "Kina"],
        }

        normalized = normalize_payload_for_analysis(data, payload)
        response = analyze_payload(data, payload)

        self.assertIn(normalized["build"], data["builds"])
        self.assertEqual(data["builds"][normalized["build"]]["hero"], "Vanessa")
        self.assertFalse(response["warnings"])
        self.assertEqual(response["state"]["build"], normalized["build"])
        self.assertEqual(len(response["recommendations"]), 2)

    def test_web_payload_matches_build_from_owned_cards_when_unselected(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Pygmalien",
            "day": 1,
            "event_options": ["Kina"],
            "owned_cards": [
                {
                    "name": "Belt",
                    "rarity": "bronze",
                }
            ],
        }

        normalized = normalize_payload_for_analysis(data, payload)

        self.assertEqual(normalized["build"], "BazaarDBPygmalienMeta")

    def test_web_payload_build_override_wins_over_owned_card_match(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_options": ["Colt"],
            "owned_cards": [
                {
                    "name": "Ballista",
                    "rarity": "gold",
                }
            ],
        }

        normalized = normalize_payload_for_analysis(
            data,
            payload,
            build_override="BazaarDBVanessaMeta",
        )

        self.assertEqual(normalized["build"], "BazaarDBVanessaMeta")

    def test_web_payload_ignores_build_from_plugin_state(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "build": "PluginShouldNotOwnThis",
            "day": 6,
            "event_options": ["Colt"],
        }

        normalized = normalize_payload_for_analysis(data, payload)

        self.assertNotEqual(normalized["build"], "PluginShouldNotOwnThis")
        self.assertIn(normalized["build"], data["builds"])
        self.assertEqual(data["builds"][normalized["build"]]["hero"], "Vanessa")

    def test_web_payload_maps_plugin_ids_to_names(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_option_ids": ["816e6ba0-8f5f-412e-9756-8e1901dd9d49"],
            "event_option_template_ids": ["816e6ba0-8f5f-412e-9756-8e1901dd9d49"],
            "owned_cards": [
                {
                    "template_id": "096e4b73-803c-4405-9710-db71b20fb183",
                    "rarity": "gold",
                }
            ],
        }

        normalized = normalize_payload_for_analysis(data, payload)
        response = analyze_payload(data, payload)

        self.assertEqual(normalized["event_options"], ["Colt"])
        self.assertEqual(normalized["owned_cards"][0]["name"], "Ballista")
        self.assertFalse(response["warnings"])

    def test_web_payload_prefers_event_template_ids_over_runtime_instance_ids(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_options": ["enc_bnJQzSX"],
            "event_option_ids": ["enc_bnJQzSX"],
            "event_option_template_ids": ["816e6ba0-8f5f-412e-9756-8e1901dd9d49"],
        }

        normalized = normalize_payload_for_analysis(data, payload)
        response = analyze_payload(data, payload)

        self.assertEqual(normalized["event_options"], ["Colt"])
        self.assertFalse(response["warnings"])

    def test_structured_shop_items_never_fall_back_to_event_options(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_option_ids": ["itm_offer_1"],
            "event_option_template_ids": [
                "096e4b73-803c-4405-9710-db71b20fb183"
            ],
            "event_options_detailed": [
                {
                    "id": "itm_offer_1",
                    "template_id": "096e4b73-803c-4405-9710-db71b20fb183",
                    "kind": "unknown",
                    "card_type": "Item",
                }
            ],
        }

        normalized = normalize_payload_for_analysis(data, payload)

        self.assertEqual(normalized["event_options"], [])

    def test_web_payload_limits_stale_event_history_to_current_instances(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Pygmalien",
            "day": 1,
            "event_options": ["enc_H6_byjM", "enc_odt0Itq", "enc_O0dkY3k"],
            "event_option_ids": [
                "enc_H6_byjM",
                "enc_odt0Itq",
                "enc_O0dkY3k",
                "enc_JLppbef",
                "enc_p4c7j2z",
                "enc_9Q_f_2f",
            ],
            "event_option_template_ids": [
                "5b7c5fc4-c942-44fe-9ca8-726dc36a2ad6",
                "c5326dd7-3e82-45c5-9a6c-81965a78bf89",
                "937489c5-22bf-4190-89b1-f396894c85f1",
                "71e387b6-bcb5-4c39-aa65-957ee5f6fbec",
                "87c6a586-fea5-4bbd-9296-2b2883bcac24",
                "912c8b09-3b76-4aa1-8e21-245e0dfb5046",
            ],
        }

        normalized = normalize_payload_for_analysis(data, payload)

        self.assertEqual(
            normalized["event_options"],
            ["Kina", "A Strange Mushroom", "Cache of Riches"],
        )

    def test_unknown_events_do_not_block_known_event_analysis(self) -> None:
        data = load_all_data(DATA_DIR)
        state = GameState(
            hero="Vanessa",
            build="VanessaAquaticAmmo",
            day=6,
            event_options=["Colt", "Mystery Event"],
            owned_cards={"Ballista": "gold"},
        )

        result = analyze_game_state(data, state)

        self.assertEqual(result.warnings, ["事件需要补充数据：Mystery Event"])
        self.assertEqual(len(result.recommendations), 2)
        self.assertIn(
            "Mystery Event",
            {recommendation["event_name"] for recommendation in result.recommendations},
        )

    def test_web_payload_records_missing_events_for_later_data_entry(self) -> None:
        data = load_all_data(DATA_DIR)
        missing_name = "测试缺失事件"
        payload = {
            "source": "test",
            "hero": "Vanessa",
            "day": 6,
            "event_options": [missing_name, "Colt"],
            "owned_cards": [{"name": "Ballista", "rarity": "gold"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_events_path = Path(temp_dir) / "missing_events.json"
            with patch.object(web_app, "MISSING_EVENTS_PATH", missing_events_path):
                response = analyze_payload(data, payload)
                missing = response["state"]["missing_events"]

                self.assertEqual(
                    missing,
                    [{"name": missing_name, "display_name": missing_name}],
                )
                self.assertTrue(missing_events_path.exists())
                saved = json.loads(missing_events_path.read_text(encoding="utf-8"))
                self.assertEqual(saved[missing_name]["last_seen_hero"], "Vanessa")

    def test_web_payload_uses_auto_build_for_unconfigured_hero(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Dooley",
            "day": 1,
            "event_option_template_ids": ["912c8b09-3b76-4aa1-8e21-245e0dfb5046"],
        }

        normalized = normalize_payload_for_analysis(data, payload)
        response = analyze_payload(data, payload)

        self.assertEqual(normalized["build"], "baojiheqian")
        self.assertEqual(normalized["event_options"], ["Mittel"])
        self.assertFalse(response["warnings"])
        self.assertEqual(len(response["recommendations"]), 1)

    def test_improve_item_event_depends_on_owned_matching_items(self) -> None:
        data = load_all_data(DATA_DIR)
        result_without_owned = analyze_event(
            event_name="Mad Maddie",
            event_data=data["events"]["Mad Maddie"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )
        result_with_owned = analyze_event(
            event_name="Mad Maddie",
            event_data=data["events"]["Mad Maddie"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={"Ballista": "gold"},
        )

        self.assertEqual(result_without_owned["recommendation"], "Low Value")
        self.assertEqual(result_with_owned["recommendation"], "High Value")
        self.assertEqual(result_with_owned["owned_target_hits"][0]["name"], "Ballista")

    def test_enchantment_tags_affect_owned_item_matching(self) -> None:
        data = load_all_data(DATA_DIR)
        state = GameState(
            hero="Vanessa",
            build="VanessaAquaticAmmo",
            day=6,
            event_options=["Flambe"],
            owned_cards={"Ballista": "gold"},
            owned_card_enchantments={"Ballista": ["Fiery"]},
        )

        result = analyze_game_state(data, state)

        self.assertFalse(result.warnings)
        self.assertEqual(result.best["recommendation"], "High Value")
        self.assertEqual(result.best["owned_target_hits"][0]["name"], "Ballista")
        self.assertIn("burn", result.best["owned_target_hits"][0]["tags"])

    def test_generated_events_include_non_shop_categories(self) -> None:
        data = load_all_data(DATA_DIR)

        self.assertEqual(data["events"]["Go Fishing"]["event_category"], "item_rewards")
        self.assertEqual(data["events"]["Ammo Cache"]["event_category"], "item_rewards")
        self.assertEqual(data["events"]["C4"]["event_category"], "skill_shops")
        self.assertEqual(data["events"]["C4"]["shop_pool"]["reward_tags"], ["ammo"])
        self.assertEqual(data["events"]["Burning Caldera"]["event_category"], "enchant_events")
        self.assertEqual(data["events"]["Deadly Duel"]["event_category"], "combat_events")
        self.assertEqual(data["events"]["Tok's Clocks"]["event_category"], "shops")

    def test_legacy_skill_shop_uses_skill_tags_as_its_card_pool(self) -> None:
        data = load_all_data(DATA_DIR)
        c4_event = dict(data["events"]["C4"])
        c4_event.pop("shop_pool", None)

        cards, rarity_filter = infer_possible_cards_for_event(
            event_data=c4_event,
            cards=data["cards"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertEqual(rarity_filter, {"min": "bronze", "max": "gold"})
        self.assertTrue(cards)
        self.assertTrue(all(card["raw"]["type"] == "Skill" for card in cards))
        self.assertTrue(all("ammo" in card["tags"] for card in cards))

    def test_generated_events_include_followup_options(self) -> None:
        data = load_all_data(DATA_DIR)
        mushroom_options = {
            option["name"]: option
            for option in data["events"]["A Strange Mushroom"]["followup_options"]
        }
        haddy_options = {
            option["name"]: option
            for option in data["events"]["Haddy"]["followup_options"]
        }

        self.assertEqual(mushroom_options["Keep it for Luck"]["event_category"], "resource_events")
        self.assertEqual(mushroom_options["Sell It"]["resource_rewards"], {"gold": 1})
        self.assertEqual(mushroom_options["Brew a Potion"]["event_category"], "item_rewards")
        self.assertEqual(haddy_options["Bag of Gold"]["resource_rewards"], {"gold": 1})
        self.assertEqual(haddy_options["Diamond Skill"]["event_category"], "skill_shops")
        self.assertEqual(haddy_options["Mystery Bundle"]["event_category"], "item_rewards")

    def test_followup_options_affect_recommendation_and_ai_payload(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="A Strange Mushroom",
            event_data=data["events"]["A Strange Mushroom"],
            cards=data["cards"],
            build_name="BazaarDBPygmalienMeta",
            build_data=data["builds"]["BazaarDBPygmalienMeta"],
            current_day=2,
            rarity_rules=data["rarity_rules"],
            current_hero="Pygmalien",
            owned_cards={},
        )
        payload = compact_recommendations(
            data=data,
            hero="Pygmalien",
            build_name="BazaarDBPygmalienMeta",
            current_day=2,
            owned_cards={},
            results=[result],
        )

        self.assertEqual(result["recommendation"], "Medium Value")
        self.assertTrue(result["followup_options"])
        self.assertNotIn("选项", payload)
        self.assertNotIn("候选阵容", payload)

    def test_followup_options_promote_parent_pool_stats_from_best_followup(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Parent Event",
            event_data={
                "name": "Parent Event",
                "followup_options": [
                    {
                        "name": "Core Item",
                        "event_category": "item_rewards",
                        "card_reward": {
                            "enabled": True,
                            "exact_names": ["Ballista"],
                            "reward_tags": [],
                            "match_mode": "any",
                            "rarity_filter": {"min": "gold", "max": "gold"},
                            "excluded_tags": [],
                            "hero_scope": "current",
                            "count": 1,
                        },
                        "resource_rewards": {},
                    },
                    {
                        "name": "Bag of Gold",
                        "event_category": "resource_events",
                        "resource_rewards": {"gold": 1},
                    },
                ],
            },
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )

        self.assertEqual(result["best_followup"], "Core Item")
        self.assertGreater(result["pool_stats"]["total_pool_count"], 0)
        self.assertGreater(result["pool_stats"]["expected_valuable_in_shop"], 0)
        self.assertFalse(
            any("暂未识别到明确的卡牌或资源收益" in reason for reason in result["reasons"])
        )

    def test_followup_resource_reward_is_reflected_in_parent_reason(self) -> None:
        data = {
            "cards": {},
            "builds": {
                "TestBuild": {
                    "core_cards": [],
                    "transition_cards": [],
                    "optional_cards": [],
                }
            },
            "rarity_rules": {},
        }
        result = analyze_event(
            event_name="Parent Event",
            event_data={
                "name": "Parent Event",
                "followup_options": [
                    {
                        "name": "Sell It",
                        "event_category": "resource_events",
                        "resource_rewards": {"gold": 4},
                    }
                ],
            },
            cards=data["cards"],
            build_name="TestBuild",
            build_data=data["builds"]["TestBuild"],
            current_day=1,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )

        self.assertEqual(result["recommendation"], "Medium Value")
        self.assertEqual(result["best_followup"], "Sell It")
        self.assertEqual(result["resource_rewards"], {"gold": 4})
        self.assertTrue(
            any("Best follow-up" in reason and "gold" in reason for reason in result["reasons"])
        )

    def test_exact_item_rewards_match_named_reward_cards(self) -> None:
        data = load_all_data(DATA_DIR)
        cards, rarity_filter = infer_possible_cards_for_event(
            event_data=data["events"]["Ammo Cache"],
            cards=data["cards"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )

        self.assertEqual(rarity_filter, {"min": "silver", "max": "diamond"})
        self.assertEqual([card["name"] for card in cards], ["Gunpowder"])

    def test_farai_offers_six_packages_and_grants_one(self) -> None:
        data = load_all_data(DATA_DIR)
        event = data["events"]["法莱"]
        cards, _ = infer_possible_cards_for_event(
            event_data=event,
            cards=data["cards"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )
        result = analyze_event(
            event_name="法莱",
            event_data=event,
            cards=data["cards"],
            build_name="5middle",
            build_data=data["builds"]["5middle"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )

        self.assertTrue(cards)
        self.assertTrue(all("package" in card["tags"] for card in cards))
        self.assertEqual(result["pool_stats"]["draw_count"], 6)
        self.assertEqual(result["pool_stats"]["selection_count"], 1)

    def test_packages_are_exclusive_to_farai_event_pools(self) -> None:
        data = load_all_data(DATA_DIR)

        for event_name, event in data["events"].items():
            cards, _ = infer_possible_cards_for_event(
                event_data=event,
                cards=data["cards"],
                current_day=6,
                rarity_rules=data["rarity_rules"],
                current_hero="Vanessa",
            )
            package_cards = [
                card for card in cards if "package" in card.get("tags", [])
            ]
            if package_cards:
                self.assertIn(event_name, {"法莱", "Farai"})

        farai_cards, _ = infer_possible_cards_for_event(
            event_data=data["events"]["法莱"],
            cards=data["cards"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
        )
        self.assertTrue(
            any("package" in card.get("tags", []) for card in farai_cards)
        )

    def test_exact_item_reward_reference_tags_can_match_build_wants(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Ammo Cache",
            event_data=data["events"]["Ammo Cache"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )

        self.assertEqual(result["recommendation"], "High Value")
        self.assertEqual(result["possible_cards"][0]["name"], "Gunpowder")
        self.assertEqual(result["possible_cards"][0]["role"], "optional")
        self.assertEqual(result["pool_stats"]["draw_count"], 1)
        self.assertEqual(result["pool_stats"]["expected_valuable_in_shop"], 1.0)

    def test_item_reward_does_not_treat_generic_wanted_tags_as_build_role(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Go Fishing",
            event_data=data["events"]["Go Fishing"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )

        self.assertLess(result["pool_stats"]["valuable_count"], result["pool_stats"]["total_pool_count"])
        self.assertEqual(result["pool_stats"]["draw_count"], 1)

    def test_unrelated_item_rewards_count_as_resale_gold(self) -> None:
        data = load_all_data(DATA_DIR)
        data["cards"]["Ambergris"]["buy_prices"] = {
            "bronze": 4,
            "silver": 8,
            "gold": 16,
            "diamond": 200,
        }
        result = analyze_event(
            event_name="Go Fishing",
            event_data=data["events"]["Go Fishing"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )
        shop_result = analyze_event(
            event_name="Colt",
            event_data=data["events"]["Colt"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )

        self.assertGreater(result["pool_stats"]["expected_sell_gold"], 0)
        self.assertEqual(result["recommendation"], "Low Value")
        self.assertTrue(
            any("sell value" in reason for reason in result["reasons"])
        )
        self.assertEqual(shop_result["pool_stats"]["expected_sell_gold"], 0.0)

    def test_unknown_events_do_not_contain_obvious_item_rewards(self) -> None:
        raw_events = json.loads((DATA_DIR / "events.json").read_text(encoding="utf-8"))
        item_words = {
            "item",
            "loot",
            "weapon",
            "potion",
            "core",
            "friend",
            "property",
            "aquatic",
            "ammo",
            "burn",
            "poison",
            "shield",
            "freeze",
            "haste",
            "slow",
            "chocolate",
            "cinders",
            "extract",
            "gumball",
            "scrap",
        }
        unknown_item_like = [
            event["name"]
            for event in raw_events.get("unknown_events", [])
            if any(word in event.get("notes", "").lower() for word in item_words)
        ]

        self.assertEqual(unknown_item_like, [])

    def test_upgrade_item_event_values_upgradeable_owned_core_cards(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Upgrade an item",
            event_data=data["events"]["Upgrade an item"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={"Ballista": "silver"},
        )

        self.assertEqual(result["recommendation"], "High Value")
        self.assertEqual(result["owned_target_hits"][0]["name"], "Ballista")
        self.assertTrue(result["owned_target_hits"][0]["can_upgrade"])


if __name__ == "__main__":
    unittest.main()
