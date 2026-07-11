from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from build_simulation_evaluator import evaluate_build


def make_card(name: str, template_id: str, damage: float, cooldown_ms: int) -> dict:
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
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
                    "Attributes": {
                        "DamageAmount": damage,
                        "CooldownMax": cooldown_ms,
                    },
                    "AbilityIds": ["0"],
                    "AuraIds": [],
                }
            },
        },
    }


class BuildSimulationEvaluatorTests(unittest.TestCase):
    def test_evaluate_build_compares_added_candidate(self) -> None:
        data = {
            "cards": {
                "Base": make_card("Base", "tpl_base", 20, 5000),
                "Candidate": make_card("Candidate", "tpl_candidate", 30, 5000),
            }
        }
        current_items = [
            {
                "id": "itm_base",
                "template_id": "tpl_base",
                "rarity": "Bronze",
                "section": "Hand",
            }
        ]

        result = evaluate_build(
            data=data,
            current_items=current_items,
            candidate_changes=[
                {
                    "operation": "add",
                    "card": {
                        "template_id": "tpl_candidate",
                        "rarity": "Bronze",
                    },
                }
            ],
            enemy_state={"health": 100},
            duration_sec=20,
        )

        self.assertEqual(result["baseline"]["total_damage"], 80)
        self.assertEqual(result["changed"]["total_damage"], 200)
        self.assertEqual(result["delta"]["total_damage"], 120)
        self.assertIsNone(result["baseline"]["battle_time_sec"])
        self.assertEqual(result["changed"]["battle_time_sec"], 10)
        self.assertEqual(result["changed"]["win_rate"], 1.0)

    def test_evaluate_build_compares_replaced_candidate(self) -> None:
        data = {
            "cards": {
                "Base": make_card("Base", "tpl_base", 20, 5000),
                "Candidate": make_card("Candidate", "tpl_candidate", 30, 5000),
            }
        }
        current_items = [
            {
                "id": "itm_base",
                "template_id": "tpl_base",
                "rarity": "Bronze",
                "section": "Hand",
                "slot": 2,
            }
        ]

        result = evaluate_build(
            data=data,
            current_items=current_items,
            candidate_changes=[
                {
                    "operation": "replace",
                    "match": {"id": "itm_base"},
                    "card": {
                        "template_id": "tpl_candidate",
                        "rarity": "Bronze",
                    },
                }
            ],
            enemy_state={"health": 100},
            duration_sec=20,
        )

        self.assertEqual(result["baseline"]["total_damage"], 80)
        self.assertEqual(result["changed"]["total_damage"], 120)
        self.assertEqual(result["delta"]["total_damage"], 40)
        self.assertEqual(result["changed"]["battle_time_sec"], 20)
        self.assertEqual(result["changed"]["win_rate"], 1.0)

    def test_evaluate_build_compares_removed_card(self) -> None:
        data = {
            "cards": {
                "Base": make_card("Base", "tpl_base", 20, 5000),
                "Extra": make_card("Extra", "tpl_extra", 30, 5000),
            }
        }
        current_items = [
            {
                "id": "itm_base",
                "template_id": "tpl_base",
                "rarity": "Bronze",
                "section": "Hand",
            },
            {
                "id": "itm_extra",
                "template_id": "tpl_extra",
                "rarity": "Bronze",
                "section": "Hand",
            },
        ]

        result = evaluate_build(
            data=data,
            current_items=current_items,
            candidate_changes=[
                {
                    "operation": "remove",
                    "match": {"id": "itm_extra"},
                }
            ],
            enemy_state={"health": 100},
            duration_sec=20,
        )

        self.assertEqual(result["baseline"]["total_damage"], 200)
        self.assertEqual(result["changed"]["total_damage"], 80)
        self.assertEqual(result["delta"]["total_damage"], -120)
        self.assertEqual(result["baseline"]["win_rate"], 1.0)
        self.assertEqual(result["changed"]["win_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
