from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import web_app
from data_loader import load_all_data


DATA_DIR = PROJECT_ROOT / "data"


class WebAppResilienceTests(unittest.TestCase):
    def test_runtime_payload_does_not_fall_back_to_example_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_path = Path(tmp_dir) / "missing-state.json"
            with patch.object(web_app, "STATE_PATH", missing_path):
                with self.assertRaises(FileNotFoundError):
                    web_app.load_runtime_payload()

    def test_runtime_payload_rejects_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "game_state.json"
            state_path.write_text('{"source": "bepinex"}', encoding="utf-8")
            stale_time = state_path.stat().st_mtime + web_app.MAX_STATE_AGE_SECONDS + 1
            with (
                patch.object(web_app, "STATE_PATH", state_path),
                patch.object(web_app.time, "time", return_value=stale_time),
            ):
                with self.assertRaisesRegex(RuntimeError, "停止更新"):
                    web_app.load_runtime_payload()

    def test_runtime_payload_warns_before_rejecting_stale_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "game_state.json"
            state_path.write_text('{"source": "bepinex"}', encoding="utf-8")
            old_but_usable_time = (
                state_path.stat().st_mtime
                + web_app.STATE_AGE_WARNING_SECONDS
                + 1
            )
            with (
                patch.object(web_app, "STATE_PATH", state_path),
                patch.object(web_app.time, "time", return_value=old_but_usable_time),
            ):
                payload, _ = web_app.load_runtime_payload()

        self.assertTrue(payload["_runtime_state_age_stale"])
        self.assertGreater(payload["_runtime_state_age_seconds"], 0)

    def test_runtime_payload_explains_installer_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "game_state.json"
            state_path.write_text('{"source": "installer"}', encoding="utf-8")
            with patch.object(web_app, "STATE_PATH", state_path):
                with self.assertRaisesRegex(RuntimeError, "占位文件"):
                    web_app.load_runtime_payload()

    def test_runtime_payload_explains_bepinex_waiting_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "game_state.json"
            state_path.write_text(
                '{"source": "bepinex", "status": "waiting_for_game_state"}',
                encoding="utf-8",
            )
            with patch.object(web_app, "STATE_PATH", state_path):
                with self.assertRaisesRegex(RuntimeError, "还没有捕获"):
                    web_app.load_runtime_payload()

    def test_json_responses_disable_browser_cache(self) -> None:
        handler = object.__new__(web_app.BazaarHandler)
        handler.wfile = BytesIO()
        headers: dict[str, str] = {}
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: headers.__setitem__(name, value)
        handler.end_headers = lambda: None

        handler.send_json({"ok": True})

        self.assertIn("no-store", headers["Cache-Control"])
        self.assertEqual(headers["Pragma"], "no-cache")

    def test_root_returns_lightweight_api_health_payload(self) -> None:
        handler = object.__new__(web_app.BazaarHandler)
        handler.path = "/"
        handler.wfile = BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None

        handler.do_GET()

        body = json.loads(handler.wfile.getvalue().decode("utf-8"))
        self.assertTrue(body["ok"])
        self.assertEqual(body["mode"], "api-only")
        self.assertEqual(body["analysis_endpoint"], "/api/analysis")
        self.assertEqual(body["state_signature_endpoint"], "/api/state-signature")

    def test_display_name_list_uses_chinese_translation_table(self) -> None:
        data = {
            "translations": {
                "by_name": {
                    "A": "甲",
                    "B": "乙",
                },
            },
        }

        self.assertEqual(web_app.display_name_list(data, ["A", "B", "C"]), ["甲", "乙", "C"])

    def test_shop_does_not_record_observed_child_options(self) -> None:
        data = {
            "events": {
                "Aila": {
                    "event_category": "shops",
                    "source_ids": ["aila-template"],
                }
            }
        }
        payload = {
            "event_options_detailed": [
                {
                    "template_id": "aila-template",
                    "kind": "encounter",
                    "card_type": "EventEncounter",
                },
                {
                    "template_id": "combat-template",
                    "kind": "combat",
                    "card_type": "CombatEncounter",
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            graph_path = Path(tmp_dir) / "observed_event_graph.json"
            with patch.object(web_app, "OBSERVED_EVENT_GRAPH_PATH", graph_path):
                web_app.auto_observe_event_graph(data, payload)

            self.assertFalse(graph_path.exists())

    def test_runtime_plugin_state_is_detected_as_owned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "game_state.json"
            state_path.write_text(
                json.dumps({"source": "bepinex", "hero": "Karnok"}),
                encoding="utf-8",
            )

            self.assertTrue(web_app.runtime_state_is_plugin_owned(state_path))

    def test_build_options_filter_to_current_hero(self) -> None:
        data = {
            "builds": {
                "VanessaBuild": {"hero": "Vanessa", "display_name": "Vanessa"},
                "DooleyBuild": {"hero": "Dooley", "display_name": "Dooley"},
                "SharedBuild": {"display_name": "Shared"},
            }
        }

        options = web_app.build_options_for_hero(data, "Vanessa")

        self.assertEqual(
            [option["id"] for option in options],
            ["SharedBuild", "VanessaBuild"],
        )

    def test_choose_build_ignores_other_hero_override(self) -> None:
        data = {
            "cards": {},
            "builds": {
                "VanessaBuild": {"hero": "Vanessa"},
                "DooleyBuild": {"hero": "Dooley"},
            },
        }

        build = web_app.choose_build(
            data,
            hero="Vanessa",
            day=5,
            preferred="DooleyBuild",
            owned_cards=[],
        )

        self.assertEqual(build, "VanessaBuild")

    def test_build_detail_includes_displayed_card_groups(self) -> None:
        data = {
            "translations": {
                "by_name": {
                    "Core One": "核心一",
                    "Transition One": "过渡一",
                    "Optional One": "可选一",
                }
            },
            "builds": {
                "VanessaBuild": {
                    "hero": "Vanessa",
                    "display_name": "Vanessa Test",
                    "core_cards": ["Core One"],
                    "transition_cards": ["Transition One"],
                    "optional_cards": ["Optional One"],
                    "wanted_tags": ["ammo"],
                }
            },
        }

        detail = web_app.build_detail_for_state(data, "VanessaBuild")

        self.assertEqual(detail["display_name"], "Vanessa Test")
        self.assertEqual(detail["core_cards"][0]["display_name"], "核心一")
        self.assertNotIn("transition_cards", detail)
        self.assertEqual(
            [card["display_name"] for card in detail["optional_cards"]],
            ["可选一", "过渡一"],
        )
        self.assertEqual(detail["wanted_tags"], ["ammo"])

    def test_analysis_cache_ignores_volatile_timestamp(self) -> None:
        web_app.ANALYSIS_CACHE.clear()
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "updated_at_utc": "2026-07-04T00:00:00Z",
            "hero": "Vanessa",
            "day": 6,
            "event_options": ["Colt"],
        }

        with patch.object(web_app, "auto_observe_event_graph") as observe:
            first = web_app.analyze_payload(data, payload, top=3)
            payload["updated_at_utc"] = "2026-07-04T00:00:01Z"
            second = web_app.analyze_payload(data, payload, top=3)

        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(observe.call_count, 1)

    def test_state_signature_uses_key_state_not_volatile_timestamps(self) -> None:
        payload = {
            "source": "bepinex",
            "updated_at_utc": "2026-07-04T00:00:00Z",
            "screen_type": "shop",
            "event_name": "Colt",
            "event_option_ids": ["enc-1"],
            "current_shop": {
                "visible_items": [
                    {"id": "shop-1", "template_id": "card-1", "name": "Card One"}
                ],
                "refresh_cost": 1,
            },
        }
        same_state = dict(payload, updated_at_utc="2026-07-04T00:00:01Z")
        changed_event = dict(payload, event_option_ids=["enc-2"])
        changed_shop = {
            **payload,
            "current_shop": {
                "visible_items": [
                    {"id": "shop-2", "template_id": "card-2", "name": "Card Two"}
                ],
                "refresh_cost": 1,
            },
        }

        self.assertEqual(web_app.state_signature(payload), web_app.state_signature(same_state))
        self.assertNotEqual(web_app.state_signature(payload), web_app.state_signature(changed_event))
        self.assertNotEqual(web_app.state_signature(payload), web_app.state_signature(changed_shop))

    def test_state_signature_changes_when_owned_card_moves_between_stash_and_hand(self) -> None:
        stash_payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "owned_cards": [
                {
                    "id": "same-instance",
                    "template_id": "card-1",
                    "name": "Card One",
                    "section": "Stash",
                }
            ],
        }
        hand_payload = {
            **stash_payload,
            "owned_cards": [
                {
                    "id": "same-instance",
                    "template_id": "card-1",
                    "name": "Card One",
                    "section": "Hand",
                }
            ],
        }

        self.assertNotEqual(
            web_app.state_signature(stash_payload),
            web_app.state_signature(hand_payload),
        )

    def test_state_signature_changes_when_board_position_changes(self) -> None:
        left_payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "owned_cards": [
                {
                    "id": "same-instance",
                    "template_id": "card-1",
                    "name": "Card One",
                    "section": "Hand",
                    "position": 0,
                }
            ],
        }
        right_payload = {
            **left_payload,
            "owned_cards": [{**left_payload["owned_cards"][0], "position": 3}],
        }

        self.assertNotEqual(
            web_app.state_signature(left_payload),
            web_app.state_signature(right_payload),
        )

    def test_state_signature_infers_position_from_ui_socket_context(self) -> None:
        left_payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "owned_cards": [
                {
                    "id": "same-instance",
                    "template_id": "card-1",
                    "name": "Card One",
                    "section": "Hand",
                    "ui_context": "Card/PlayerItemSocket_0/Board",
                }
            ],
        }
        right_payload = {
            **left_payload,
            "owned_cards": [
                {
                    **left_payload["owned_cards"][0],
                    "ui_context": "Card/PlayerItemSocket_3/Board",
                }
            ],
        }

        self.assertNotEqual(
            web_app.state_signature(left_payload),
            web_app.state_signature(right_payload),
        )

    def test_ai_analysis_is_cached_by_state_signature(self) -> None:
        web_app.ANALYSIS_CACHE.clear()
        web_app.AI_PAYLOAD_CACHE.clear()
        web_app.AI_ANALYSIS_CACHE.clear()
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_options": ["Colt"],
            "owned_cards": [],
            "visible_cards": [],
        }

        web_app.analyze_payload(data, payload, top=3)
        with patch.object(web_app, "analyze_with_ai", return_value="cached ai") as ai:
            first = web_app.analyze_payload(data, payload, top=3, include_ai=True)
            second = web_app.analyze_payload(data, payload, top=3, include_ai=True)

        self.assertEqual(first["ai_analysis"], "cached ai")
        self.assertEqual(second["ai_analysis"], "cached ai")
        self.assertEqual(ai.call_count, 1)

    def test_ai_analysis_failure_does_not_fail_analysis(self) -> None:
        web_app.ANALYSIS_CACHE.clear()
        web_app.AI_PAYLOAD_CACHE.clear()
        web_app.AI_ANALYSIS_CACHE.clear()
        data = load_all_data(DATA_DIR)
        payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 6,
            "event_options": ["Colt"],
            "owned_cards": [],
            "visible_cards": [],
        }

        with patch.object(web_app, "analyze_with_ai", side_effect=ValueError("bad ai")):
            response = web_app.analyze_payload(data, payload, top=3, include_ai=True)

        self.assertIn("recommendations", response)
        self.assertEqual(response["ai_error"], "bad ai")

    def test_cached_ai_analysis_failure_does_not_fail_analysis(self) -> None:
        web_app.AI_PAYLOAD_CACHE.clear()
        web_app.AI_ANALYSIS_CACHE.clear()
        cache_key = (1, "state", "", 3, "guide")
        response = {"recommendations": [{"event_name": "Colt"}]}
        web_app.AI_PAYLOAD_CACHE[cache_key] = {"英雄": "Vanessa"}

        with patch.object(web_app, "analyze_with_ai", side_effect=ValueError("bad ai")):
            handled = web_app.attach_cached_ai_analysis(response, cache_key)

        self.assertTrue(handled)
        self.assertEqual(response["ai_error"], "bad ai")

    def test_waiting_runtime_state_does_not_error(self) -> None:
        web_app.ANALYSIS_CACHE.clear()
        data = load_all_data(DATA_DIR)
        response = web_app.analyze_payload(
            data,
            {
                "source": "bepinex",
                "hero": None,
                "day": 0,
                "event_options": [],
            },
        )

        self.assertEqual(response["recommendations"], [])
        self.assertTrue(response["warnings"])
        self.assertEqual(response["state"]["day"], 0)

    def test_owned_items_and_skills_are_displayed_separately(self) -> None:
        data = {
            "events": {},
            "translations": {"by_name": {"Calico": "三花"}},
            "cards": {
                "Test Item": {"type": "Item"},
                "Test Skill": {"type": "Skill"},
            },
            "builds": {"VanessaTest": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaTest",
            "day": 5,
            "event_options": [],
            "owned_cards": [
                {"name": "Test Item", "rarity": "gold"},
                {"name": "Test Skill", "rarity": "silver"},
            ],
            "visible_cards": [],
            "prestige": 7,
            "max_prestige": 10,
        }

        response = web_app.analyze_payload(data, payload)

        self.assertEqual(response["state"]["prestige"], 7)
        self.assertEqual(response["state"]["max_prestige"], 10)
        self.assertEqual(
            [item["name"] for item in response["state"]["owned_items_display"]],
            ["Test Item"],
        )
        self.assertEqual(
            [item["name"] for item in response["state"]["skills_display"]],
            ["Test Skill"],
        )

    def test_inventory_space_counts_all_items_against_board_plus_stash_capacity(self) -> None:
        data = {
            "events": {},
            "translations": {"by_name": {"Calico": "三花"}, "by_id": {}},
            "cards": {
                "Small Item": {"type": "Item", "size": "Small"},
                "Medium Item": {"type": "Item", "size": "Medium"},
                "Large Item": {"type": "Item", "size": "Large"},
            },
            "builds": {"VanessaTest": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaTest",
            "day": 5,
            "event_options": [],
            "owned_cards": [],
            "owned_items": [
                {"name": "Small Item"},
                {"name": "Medium Item"},
                {"name": "Large Item"},
            ],
            "board_items": [{"name": "Small Item"}],
            "stash_items": [{"name": "Medium Item"}, {"name": "Large Item"}],
            "visible_cards": [],
            "inventory_slots_total": 10,
        }

        normalized = web_app.normalize_payload_for_analysis(data, payload)

        self.assertEqual(normalized["inventory_slots_used"], 6)
        self.assertEqual(normalized["inventory_slots_total"], 20)

    def test_monster_runtime_fields_are_normalized_and_signed(self) -> None:
        data = {
            "events": {},
            "translations": {},
            "cards": {
                "Monster Claw": {
                    "id": "monster-claw-template",
                    "type": "Item",
                    "size": "Small",
                },
                "Monster Skill": {
                    "id": "monster-skill-template",
                    "type": "Skill",
                },
            },
            "builds": {"VanessaTest": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaTest",
            "day": 5,
            "event_options": [],
            "owned_cards": [],
            "visible_cards": [],
            "monster_health": 120,
            "monster_items": [{"template_id": "monster-claw-template"}],
            "monster_skills": [{"template_id": "monster-skill-template"}],
        }

        normalized = web_app.normalize_payload_for_analysis(data, payload)
        response = web_app.analyze_payload(data, payload)
        changed = {**payload, "monster_health": 90}

        self.assertEqual(normalized["monster_items"][0]["name"], "Monster Claw")
        self.assertEqual(normalized["monster_skills"][0]["name"], "Monster Skill")
        self.assertEqual(response["state"]["monster_health"], 120)
        self.assertEqual(response["state"]["monster_items"][0]["name"], "Monster Claw")
        self.assertNotEqual(web_app.state_signature(payload), web_app.state_signature(changed))

    def test_static_monster_choices_are_matched_from_combat_options(self) -> None:
        data = {
            "events": {},
            "translations": {"by_name": {"Calico": "三花"}, "by_id": {}},
            "cards": {
                "Claws": {"id": "claws-template", "type": "Item", "size": "Small"},
                "Seafaring": {"id": "seafaring-template", "type": "Skill"},
            },
            "monsters": {
                "calico-monster": {
                    "id": "calico-monster",
                    "template_id": "calico-monster",
                    "name": "Calico",
                    "health": 450,
                    "items": [{"template_id": "claws-template", "rarity": "Silver"}],
                    "skills": [{"template_id": "seafaring-template", "rarity": "Diamond"}],
                    "encounter_ids": ["calico-encounter"],
                    "encounters": [
                        {
                            "id": "calico-encounter",
                            "template_id": "calico-encounter",
                            "name": "Calico",
                        }
                    ],
                }
            },
            "builds": {"VanessaTest": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaTest",
            "day": 5,
            "event_options": [],
            "owned_cards": [],
            "visible_cards": [],
            "event_options_detailed": [
                {
                    "id": "com_runtime",
                    "template_id": "calico-encounter",
                    "kind": "combat",
                    "card_type": "CombatEncounter",
                    "name": "Calico",
                }
            ],
        }

        normalized = web_app.normalize_payload_for_analysis(data, payload)
        response = web_app.analyze_payload(data, payload)

        self.assertEqual(normalized["monster_health"], 450)
        self.assertEqual(normalized["monster_items"][0]["name"], "Claws")
        self.assertEqual(normalized["monster_skills"][0]["name"], "Seafaring")
        self.assertEqual(normalized["monster_choices"][0]["name"], "Calico")
        self.assertEqual(response["state"]["monster_choices"][0]["health"], 450)
        self.assertEqual(response["state"]["monster_choices"][0]["display_name"], "三花")

    def test_monster_display_name_strips_runtime_suffix_before_translation(self) -> None:
        data = {"translations": {"by_name": {"Coconut Crab": "椰子蟹", "Calico": "三花"}, "by_id": {}}}

        self.assertEqual(web_app.zh_monster_name(data, "Coconut Crab Monster"), "椰子蟹")
        self.assertEqual(web_app.zh_monster_name(data, "Coconut Crab 2"), "椰子蟹")
        self.assertEqual(web_app.zh_monster_name(data, "Calico (monster)"), "三花")

    def test_card_entries_map_template_and_source_ids_to_names(self) -> None:
        data = {
            "events": {},
            "translations": {},
            "cards": {
                "Alias Item": {
                    "id": "canonical-id",
                    "source_id": "source-id",
                    "source_ids": ["source-alias"],
                    "template_id": "template-id",
                    "type": "Item",
                    "size": "Small",
                }
            },
            "builds": {"VanessaTest": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaTest",
            "day": 5,
            "event_options": [],
            "owned_cards": [{"template_id": "template-id"}],
            "owned_items": [{"source_id": "source-id"}],
            "board_items": [{"template_id": "source-alias"}],
            "stash_items": [{"id": "canonical-id"}],
            "visible_cards": [],
        }

        normalized = web_app.normalize_payload_for_analysis(data, payload)

        self.assertEqual(normalized["owned_cards"][0]["name"], "Alias Item")
        self.assertEqual(normalized["owned_items"][0]["name"], "Alias Item")
        self.assertEqual(normalized["board_items"][0]["name"], "Alias Item")
        self.assertEqual(normalized["stash_items"][0]["name"], "Alias Item")

    def test_analysis_does_not_run_combat_simulation_automatically(self) -> None:
        data = {
            "events": {},
            "translations": {},
            "cards": {},
            "builds": {"VanessaTest": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaTest",
            "day": 5,
            "event_options": [],
            "owned_cards": [],
            "visible_cards": [],
        }

        response = web_app.analyze_payload(data, payload)

        self.assertNotIn("self_ttk", response)

    def test_http_server_disallows_duplicate_port_reuse(self) -> None:
        self.assertFalse(web_app.BazaarHTTPServer.allow_reuse_address)

    def test_item_and_enchant_events_count_as_value_rules(self) -> None:
        self.assertTrue(
            web_app.event_has_value_rule(
                {
                    "event_category": "item_events",
                    "effect": "upgrade_items",
                    "target_tags": [],
                }
            )
        )
        self.assertTrue(
            web_app.event_has_value_rule(
                {
                    "event_category": "enchant_events",
                    "effect": "enchant_items",
                    "enchantment_tags": ["burn"],
                }
            )
        )
        self.assertFalse(
            web_app.event_has_value_rule(
                {
                    "event_category": "utility_events",
                    "notes": "A cute but mysterious creature",
                }
            )
        )

    def test_known_event_without_value_rule_shows_description(self) -> None:
        data = {
            "events": {
                "Tiny Furry Monster": {
                    "event_category": "utility_events",
                    "notes": "A cute but mysterious creature",
                    "resource_rewards": {},
                }
            },
            "translations": {"by_name": {"Tiny Furry Monster": "茸茸小怪兽"}},
        }
        result = {
            "event_name": "Tiny Furry Monster",
            "recommendation": "Low Value",
            "reasons": ["暂未识别到明确的卡牌或资源收益。"],
        }

        with patch.object(web_app, "load_observed_event_graph", return_value={}):
            summary = web_app.summarize_recommendation(data, result)

        self.assertEqual(summary["recommendation_label"], "已识别")
        self.assertEqual(summary["reasons"][0], "描述：A cute but mysterious creature")
        self.assertFalse(any("当前数据源只提供了描述" in reason for reason in summary["reasons"]))

    def test_state_signature_includes_exported_event_branches(self) -> None:
        base_payload = {
            "source": "bepinex",
            "hero": "Vanessa",
            "day": 5,
            "event_options_detailed": [
                {
                    "id": "enc_parent",
                    "template_id": "parent-template",
                    "kind": "encounter",
                    "card_type": "EventEncounter",
                }
            ],
        }
        branched_payload = {
            **base_payload,
            "event_options_detailed": [
                {
                    **base_payload["event_options_detailed"][0],
                    "branches": [
                        {
                            "template_id": "child-template",
                            "kind": "step",
                            "card_type": "EncounterStep",
                        }
                    ],
                }
            ],
        }

        self.assertNotEqual(
            web_app.state_signature(base_payload),
            web_app.state_signature(branched_payload),
        )

    def test_current_combat_simulation_is_explicit(self) -> None:
        weapon = {
            "id": "tpl_weapon",
            "template_id": "tpl_weapon",
            "name": "Weapon",
            "type": "Item",
            "size": "Small",
            "tags": [],
            "hidden_tags": [],
            "tiers": ["Bronze"],
            "rarity": "Bronze",
            "raw_effects": {
                "abilities": {
                    "0": {
                        "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                        "Trigger": {
                            "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardFired"
                        },
                        "Action": {
                            "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerDamage",
                            "Target": {
                                "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                                "TargetMode": "Opponent",
                            },
                        },
                    }
                },
                "auras": {},
                "tiers_raw": {
                    "Bronze": {
                        "Attributes": {"DamageAmount": 25, "CooldownMax": 5000},
                        "AbilityIds": ["0"],
                        "AuraIds": [],
                    }
                },
            },
        }
        data = {
            "events": {},
            "translations": {},
            "cards": {"Weapon": weapon},
            "builds": {"VanessaTest": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaTest",
            "day": 5,
            "combat_health": 50,
            "board_items": [
                {
                    "id": "itm_weapon",
                    "template_id": "tpl_weapon",
                    "name": "Weapon",
                    "rarity": "Bronze",
                    "section": "Hand",
                }
            ],
        }

        response = web_app.simulate_current_combat_payload(data, payload, horizon_sec=10)

        self.assertTrue(response["ok"])
        self.assertEqual(response["combat"]["total_damage"], 50)
        self.assertEqual(response["combat"]["damage_per_second"], 5)
        self.assertEqual(response["combat"]["kill_time_sec"], 10)

    def test_priority_cards_exclude_other_build_cores_and_have_no_limit(self) -> None:
        cards = web_app.priority_cards(
            [
                {
                    "name": "Alternative Core",
                    "tier": "A",
                    "role": "unrelated",
                    "alt_core_build_hits": [
                        {"build_name": "AltBuild", "display_name": "备用阵容"}
                    ],
                },
                *[
                    {
                        "name": f"Current Card {index}",
                        "tier": "A",
                        "role": "optional",
                    }
                    for index in range(8)
                ],
            ]
        )

        self.assertEqual(len(cards), 8)
        self.assertTrue(all(card["role"] == "optional" for card in cards))

    def test_load_observed_event_graph_cleans_bad_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            graph_path = tmp_path / "observed_event_graph.json"
            graph_path.write_text(
                json.dumps(
                    {
                        "Bad Node": None,
                        "Good Node": {
                            "parent_source_ids": [None, "abc"],
                            "children": [None, {"source_id": "child-1"}],
                            "observed_count": "3",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(web_app, "OBSERVED_EVENT_GRAPH_PATH", graph_path),
                patch.object(web_app, "RUNTIME_DIR", tmp_path),
            ):
                graph = web_app.load_observed_event_graph()

        self.assertEqual(graph["Bad Node"], {})
        self.assertEqual(graph["Good Node"]["parent_source_ids"], ["abc"])
        self.assertEqual(graph["Good Node"]["observed_count"], 3)
        self.assertEqual(graph["Good Node"]["children"], [{"source_id": "child-1"}])

    def test_analyze_payload_survives_observation_failure(self) -> None:
        data = load_all_data(DATA_DIR)
        payload = {
            "hero": "Vanessa",
            "build": "VanessaAquaticAmmo",
            "day": 5,
            "event_options": ["Colt"],
            "owned_cards": [],
            "visible_cards": [],
        }

        with patch.object(
            web_app,
            "auto_observe_event_graph",
            side_effect=RuntimeError("observation failed"),
        ):
            response = web_app.analyze_payload(data, payload)

        self.assertIn("state", response)
        self.assertIn("recommendations", response)
        self.assertEqual(response["state"]["hero"], "Vanessa")
        self.assertTrue(response["warnings"])
        self.assertIn("observation", response["warnings"][0].lower())

    def test_analyze_payload_ignores_bad_observed_graph_file(self) -> None:
        data = {
            "events": {
                "Parent Event": {
                    "source_ids": ["parent-template"],
                }
            },
            "translations": {},
            "cards": {},
            "builds": {"VanessaAquaticAmmo": {"hero": "Vanessa"}},
            "rarity_rules": {},
        }
        payload = {
            "hero": "Vanessa",
            "build": "VanessaAquaticAmmo",
            "day": 5,
            "event_options": ["Colt"],
            "event_options_detailed": [
                {
                    "id": "enc_parent",
                    "template_id": "parent-template",
                    "kind": "encounter",
                    "card_type": "EventEncounter",
                },
                {
                    "id": "ste_child",
                    "template_id": "child-template",
                    "kind": "step",
                    "card_type": "EncounterStep",
                },
            ],
            "owned_cards": [],
            "visible_cards": [],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            graph_path = tmp_path / "observed_event_graph.json"
            graph_path.write_text(
                json.dumps(
                    {
                        "BadA": None,
                        "BadB": {
                            "children": None,
                            "parent_source_ids": None,
                            "observed_count": "bad",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with (
                patch.object(web_app, "OBSERVED_EVENT_GRAPH_PATH", graph_path),
                patch.object(web_app, "RUNTIME_DIR", tmp_path),
                patch.object(web_app, "load_official_cards_index", return_value={}),
            ):
                response = web_app.analyze_payload(data, payload)

        self.assertIn("state", response)
        self.assertIn("recommendations", response)
        self.assertEqual(response["state"]["hero"], "Vanessa")
        self.assertGreaterEqual(len(response["warnings"]), 0)

    def test_auto_observe_event_graph_ignores_bad_event_options(self) -> None:
        data = {
            "events": {
                "Parent Event": {
                    "source_ids": ["parent-template"],
                }
            }
        }
        payload = {
            "event_options_detailed": [
                None,
                "bad",
                {
                    "id": "enc_parent",
                    "template_id": "parent-template",
                    "kind": "encounter",
                    "card_type": "EventEncounter",
                },
                {
                    "id": "ste_child",
                    "template_id": "child-template",
                    "kind": "step",
                    "card_type": "EncounterStep",
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            graph_path = tmp_path / "observed_event_graph.json"

            with (
                patch.object(web_app, "OBSERVED_EVENT_GRAPH_PATH", graph_path),
                patch.object(web_app, "RUNTIME_DIR", tmp_path),
                patch.object(web_app, "load_official_cards_index", return_value={}),
            ):
                web_app.auto_observe_event_graph(data, payload)
                graph = json.loads(graph_path.read_text(encoding="utf-8"))

        self.assertIn("Parent Event", graph)
        self.assertEqual(graph["Parent Event"]["parent_event"], "Parent Event")
        self.assertEqual(len(graph["Parent Event"]["children"]), 1)
        self.assertTrue(graph["Parent Event"]["children"][0]["unresolved"])

    def test_auto_observe_event_graph_uses_exported_event_branches(self) -> None:
        data = {
            "events": {
                "Parent Event": {
                    "source_ids": ["parent-template"],
                },
                "Child Event": {
                    "source_ids": ["child-template"],
                },
            }
        }
        payload = {
            "event_options_detailed": [
                {
                    "id": "enc_parent",
                    "template_id": "parent-template",
                    "kind": "encounter",
                    "card_type": "EventEncounter",
                    "branches": [
                        {
                            "template_id": "child-template",
                            "kind": "step",
                            "card_type": "EncounterStep",
                            "source": "next_encounter_on_selection",
                        }
                    ],
                },
            ]
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            graph_path = tmp_path / "observed_event_graph.json"

            with (
                patch.object(web_app, "OBSERVED_EVENT_GRAPH_PATH", graph_path),
                patch.object(web_app, "RUNTIME_DIR", tmp_path),
                patch.object(web_app, "load_official_cards_index", return_value={}),
            ):
                web_app.auto_observe_event_graph(data, payload)
                graph = json.loads(graph_path.read_text(encoding="utf-8"))

        child = graph["Parent Event"]["children"][0]
        self.assertEqual(child["source_id"], "child-template")
        self.assertEqual(child["name"], "Child Event")
        self.assertEqual(child["source"], "next_encounter_on_selection")
        self.assertFalse(child["seen"])

    def test_summarize_recommendation_uses_static_followup_options(self) -> None:
        data = load_all_data(DATA_DIR)
        result = {
            "event_name": "Tiny Furry Monster",
            "recommendation": "Low Value",
            "reasons": ["暂未识别到明确的卡牌或资源收益。"],
        }

        with patch.object(web_app, "load_observed_event_graph", return_value={}):
            summary = web_app.summarize_recommendation(data, result)

        names = {item["name"] for item in summary["child_options"]}
        self.assertIn("爱抚", names)
        self.assertIn("吓唬", names)
        self.assertIn("玩捉迷藏", names)
        self.assertIn("投喂", names)
        self.assertIn("玩耍", names)
        pet_it = next(
            item
            for item in summary["child_options"]
            if item.get("source_name") == "Pet It"
        )
        self.assertNotIn("{ability.0}", pet_it["description"])
        self.assertNotIn("Gain 25 Max Health", pet_it["description"])
        self.assertIn("\u6700\u5927\u751f\u547d\u503c", pet_it["description"])
        self.assertEqual(pet_it["resource_rewards"].get("max_health"), 25)
        self.assertEqual(summary["event_rule_status"], "parent_event")

    def test_common_child_descriptions_are_localized(self) -> None:
        data = load_all_data(DATA_DIR)

        self.assertEqual(
            web_app.translate_common_game_text(data, "Get a Silver-tier Loot item"),
            "\u83b7\u5f97\u4e00\u4ef6\u767d\u94f6\u7ea7\u6218\u5229\u54c1\u7269\u54c1",
        )
        self.assertEqual(
            web_app.translate_common_game_text(data, "(if you have a Friend) Choose a Skill"),
            "\uff08\u5982\u679c\u4f60\u62e5\u6709\u670b\u53cb\uff09\u9009\u62e9\u4e00\u4e2a\u6280\u80fd",
        )
        self.assertEqual(
            web_app.translate_common_game_text(data, "(if you are Mak) Get a Small Silver-tier Potion"),
            "\uff08\u5982\u679c\u4f60\u662f\u9a6c\u514b\uff09\u83b7\u5f97\u4e00\u4ef6\u5c0f\u578b\u767d\u94f6\u7ea7\u836f\u6c34\u7269\u54c1",
        )
        self.assertEqual(
            web_app.translate_common_game_text(data, "Your Basket gains 10 and progresses 2 on its Quests"),
            "\u4f60\u7684\u63d0\u7bee\u83b7\u5f97 10\uff0c\u5e76\u4f7f\u5176\u4efb\u52a1\u8fdb\u5ea6\u63a8\u8fdb 2",
        )
        self.assertEqual(
            web_app.display_reason_text(data, "No clear card or resource value identified."),
            "\u6682\u672a\u8bc6\u522b\u5230\u660e\u786e\u7684\u5361\u724c\u6216\u8d44\u6e90\u6536\u76ca\u3002",
        )
        self.assertEqual(
            web_app.display_reason_text(data, "Best follow-up can provide exp +1."),
            "\u6700\u597d\u7684\u540e\u7eed\u9009\u9879\u53ef\u63d0\u4f9b\uff1a\u7ecf\u9a8c +1\u3002",
        )

    def test_static_followups_skip_reward_pool_items(self) -> None:
        data = load_all_data(DATA_DIR)

        obstacle_options = web_app.static_child_options_for_event(data, "Obstacle Course")
        strange_mushroom_options = web_app.static_child_options_for_event(data, "A Strange Mushroom")

        self.assertEqual(obstacle_options, [])
        self.assertFalse(
            any(item.get("source_name") == "Keep it for Luck" for item in strange_mushroom_options)
        )

    def test_static_followup_descriptions_use_official_zh(self) -> None:
        data = load_all_data(DATA_DIR)
        options = web_app.static_child_options_for_event(data, "A Strange Mushroom")
        by_source = {item.get("source_name"): item for item in options}

        self.assertEqual(by_source["Sell It"]["description"], "\u83b7\u5f974\u679a\u91d1\u5e01")
        for source_name in (
            "Trade It for Something",
            "Sell It",
            "Brew a Potion",
            "Share It With a Friend",
            "Add It to Your Bushel",
            "Add It to Your Basket",
            "No Place Like Home",
        ):
            self.assertNotRegex(
                by_source[source_name]["description"],
                r"\b(?:Gain|Your|Quests|Item)\b",
            )

    def test_shop_display_does_not_call_known_zero_gold_unknown(self) -> None:
        display = web_app.shop_rule_display_from_result(
            {
                "shop_entry_analysis": {
                    "status": "not_actionable",
                    "day_available_merchant_count": 3,
                    "pool_count": 10,
                    "target_density_band": "low",
                    "target_counts": {},
                    "gold_support": {
                        "status": "unknown",
                        "gold_known": True,
                        "price_known": False,
                        "current_gold": 0,
                        "supports_entry": False,
                    },
                    "debug": {},
                }
            },
            data={},
        )

        self.assertIn("当前 0 金", display["reason"])
        self.assertIn("金币不足", display["reason"])
        self.assertNotIn("金币状态：未知", display["reason"])

    def test_summarize_recommendation_localizes_rule_reasons(self) -> None:
        data = load_all_data(DATA_DIR)
        result = {
            "event_name": "Colt",
            "recommendation": "Low Value",
            "reasons": [
                "shop_entry_status=not_actionable",
                "Pool contains 3 current-build core cards.",
                "Can upgrade owned cards: Bee, Crook.",
                "Pool has 19 cards; 6 are build-relevant (32%).",
                "Reward gives 1 items; expected relevant cards 0.0, useful hit chance 4%.",
            ],
        }

        with patch.object(web_app, "load_observed_event_graph", return_value={}):
            summary = web_app.summarize_recommendation(data, result)

        joined = "\n".join(summary["reasons"])
        self.assertNotIn("shop_entry_status", joined)
        self.assertNotIn("Pool contains", joined)
        self.assertNotIn("Can upgrade", joined)
        self.assertNotIn("Reward gives", joined)
        self.assertIn("\u5546\u5e97\u5165\u53e3\u8bc4\u4f30", joined)
        self.assertIn("\u5f53\u524d\u9635\u5bb9\u6838\u5fc3\u5361", joined)
        self.assertIn("\u53ef\u5347\u7ea7\u5df2\u62e5\u6709\u5361", joined)


class WebAppStartupTests(unittest.TestCase):
    def test_local_bind_candidates_try_localhost_for_loopback(self) -> None:
        self.assertEqual(web_app.local_bind_candidates("127.0.0.1"), ["127.0.0.1", "localhost"])
        self.assertEqual(web_app.local_bind_candidates("localhost"), ["localhost", "127.0.0.1"])
        self.assertEqual(web_app.local_bind_candidates("0.0.0.0"), ["0.0.0.0"])

    def test_create_http_server_falls_back_after_permission_error(self) -> None:
        attempts: list[tuple[str, int]] = []

        class FakeServer:
            def __init__(self, address: tuple[str, int], handler: object) -> None:
                attempts.append(address)
                if address[0] == "127.0.0.1":
                    raise PermissionError("blocked")
                self.address = address

        server, bound_host = web_app.create_http_server(
            "127.0.0.1",
            8765,
            server_class=FakeServer,  # type: ignore[arg-type]
            health_check=lambda host, port: False,
        )

        self.assertEqual(attempts, [("127.0.0.1", 8765), ("localhost", 8765)])
        self.assertEqual(bound_host, "localhost")
        self.assertEqual(server.address, ("localhost", 8765))  # type: ignore[attr-defined]

    def test_create_http_server_reports_permission_denied(self) -> None:
        class BlockedServer:
            def __init__(self, address: tuple[str, int], handler: object) -> None:
                raise PermissionError("blocked")

        with self.assertRaises(SystemExit) as raised:
            web_app.create_http_server(
                "127.0.0.1",
                8765,
                server_class=BlockedServer,  # type: ignore[arg-type]
                health_check=lambda host, port: False,
            )

        message = str(raised.exception)
        self.assertIn("Unable to start BazaarHelper", message)
        self.assertIn("127.0.0.1, localhost:8765", message)
        self.assertIn("reserved/excluded TCP port", message)

    def test_create_http_server_exits_cleanly_when_helper_is_already_running(self) -> None:
        class AddressInUseServer:
            def __init__(self, address: tuple[str, int], handler: object) -> None:
                exc = OSError("in use")
                exc.winerror = 10048  # type: ignore[attr-defined]
                raise exc

        with self.assertRaises(SystemExit) as raised:
            web_app.create_http_server(
                "127.0.0.1",
                8765,
                server_class=AddressInUseServer,  # type: ignore[arg-type]
                health_check=lambda host, port: True,
            )

        self.assertIn("already running", str(raised.exception))

    def test_create_http_server_reports_address_in_use_when_not_helper(self) -> None:
        class AddressInUseServer:
            def __init__(self, address: tuple[str, int], handler: object) -> None:
                exc = OSError("in use")
                exc.winerror = 10048  # type: ignore[attr-defined]
                raise exc

        with self.assertRaises(SystemExit) as raised:
            web_app.create_http_server(
                "127.0.0.1",
                8765,
                server_class=AddressInUseServer,  # type: ignore[arg-type]
                health_check=lambda host, port: False,
            )

        message = str(raised.exception)
        self.assertIn("address is already in use", message)
        self.assertIn("127.0.0.1, localhost:8765", message)


if __name__ == "__main__":
    unittest.main()
