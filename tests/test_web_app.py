from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import web_app
from data_loader import load_all_data


DATA_DIR = PROJECT_ROOT / "data"


class WebAppResilienceTests(unittest.TestCase):
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
