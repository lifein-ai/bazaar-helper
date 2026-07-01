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

    def test_runtime_payload_explains_installer_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "game_state.json"
            state_path.write_text('{"source": "installer"}', encoding="utf-8")
            with patch.object(web_app, "STATE_PATH", state_path):
                with self.assertRaisesRegex(RuntimeError, "占位文件"):
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

    def test_owned_items_and_skills_are_displayed_separately(self) -> None:
        data = {
            "events": {},
            "translations": {},
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


if __name__ == "__main__":
    unittest.main()
