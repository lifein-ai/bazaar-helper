from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from ai_advisor import build_ai_messages, compact_recommendations
from build_strategy import build_applies_to_day, get_game_stage_for_day
from data_loader import load_all_data
from game_state import GameState
from main import event_index_for_hero, parse_owned_cards, run_analysis
from advisor import analyze_game_state
from recommender import infer_possible_cards_for_event, probability_at_least_one
from recommender import analyze_event
from web_app import MISSING_EVENTS_PATH, analyze_payload, normalize_payload_for_analysis


DATA_DIR = PROJECT_ROOT / "data"


class RecommenderTests(unittest.TestCase):
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

    def test_default_shop_pool_uses_current_hero_or_common(self) -> None:
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
                card["raw"]["hero"] in {"Vanessa", "Common"}
                or "Vanessa" in card["raw"].get("heroes", [])
                or "Common" in card["raw"].get("heroes", [])
                for card in cards
            )
        )

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

    def test_build_config_does_not_use_trap_cards(self) -> None:
        data = load_all_data(DATA_DIR)

        self.assertNotIn("trap_cards", data["builds"]["VanessaAquaticAmmo"])

    def test_ai_payload_is_compact_and_prompt_is_guarded(self) -> None:
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
        )
        messages = build_ai_messages(payload)
        serialized_payload = json.dumps(payload, ensure_ascii=False)
        serialized_messages = json.dumps(messages, ensure_ascii=False)

        self.assertIn("选项", payload)
        self.assertEqual(
            payload["实战Tips"],
            ["前期先拿水产/弹药过渡", "有弩炮再考虑转入"],
        )
        self.assertNotIn("possible_cards", serialized_payload)
        self.assertNotIn('"raw"', serialized_payload)
        self.assertNotIn('"trap"', serialized_payload)
        self.assertIn("编造", serialized_messages)

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

        self.assertEqual(normalized["build"], "VanessaAquaticAmmo")
        self.assertFalse(response["warnings"])
        self.assertEqual(response["state"]["build"], "VanessaAquaticAmmo")
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

        self.assertEqual(normalized["build"], "VanessaAquaticAmmo")

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
        original_text = (
            MISSING_EVENTS_PATH.read_text(encoding="utf-8")
            if MISSING_EVENTS_PATH.exists()
            else None
        )

        try:
            response = analyze_payload(data, payload)
            missing = response["state"]["missing_events"]

            self.assertEqual(missing, [{"name": missing_name, "display_name": missing_name}])
            self.assertTrue(MISSING_EVENTS_PATH.exists())
            saved = json.loads(MISSING_EVENTS_PATH.read_text(encoding="utf-8"))
            self.assertEqual(saved[missing_name]["last_seen_hero"], "Vanessa")
        finally:
            if original_text is None:
                MISSING_EVENTS_PATH.unlink(missing_ok=True)
            else:
                MISSING_EVENTS_PATH.write_text(original_text, encoding="utf-8")

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

        self.assertEqual(normalized["build"], "AutoDooley")
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
        self.assertEqual(data["events"]["Burning Caldera"]["event_category"], "enchant_events")
        self.assertEqual(data["events"]["Deadly Duel"]["event_category"], "combat_events")
        self.assertEqual(data["events"]["Tok's Clocks"]["event_category"], "shops")

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
        self.assertIn("后续选项", payload["选项"][0])
        self.assertTrue(payload["选项"][0]["后续选项"])

    def test_followup_options_promote_parent_pool_stats_from_best_followup(self) -> None:
        data = load_all_data(DATA_DIR)
        result = analyze_event(
            event_name="Haddy",
            event_data=data["events"]["Haddy"],
            cards=data["cards"],
            build_name="VanessaAquaticAmmo",
            build_data=data["builds"]["VanessaAquaticAmmo"],
            current_day=6,
            rarity_rules=data["rarity_rules"],
            current_hero="Vanessa",
            owned_cards={},
        )

        self.assertEqual(result["best_followup"], "Enchanted Item")
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
            any("最佳后续预计可获得" in reason and "金币" in reason for reason in result["reasons"])
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

        self.assertEqual(rarity_filter, {"min": "gold", "max": "gold"})
        self.assertEqual([card["name"] for card in cards], ["Gunpowder"])

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
        self.assertEqual(result["recommendation"], "Medium Value")
        self.assertTrue(
            any("卖出" in reason for reason in result["reasons"])
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
