from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

import battle_simulation_service as service
from combat_simulator import PlacedCard


def make_damage_card(name: str, template_id: str, damage: int, cooldown_ms: int) -> dict:
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
                    "Attributes": {"DamageAmount": damage, "CooldownMax": cooldown_ms},
                    "AbilityIds": ["0"],
                    "AuraIds": [],
                }
            },
        },
    }


def make_shield_card(name: str, template_id: str, shield: int, cooldown_ms: int) -> dict:
    card = make_damage_card(name, template_id, shield, cooldown_ms)
    card["raw_effects"]["abilities"]["0"]["Action"] = {
        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerShieldApply",
        "Target": {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
            "TargetMode": "Self",
        },
    }
    card["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"] = {
        "ShieldApplyAmount": shield,
        "CooldownMax": cooldown_ms,
    }
    return card


class BattleSimulationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        service.SIMULATION_CACHE.clear()

    def test_selected_monster_simulates_only_the_clicked_choice(self) -> None:
        data = {"data_version": {}, "cards": {}}
        payload = {
            "combat_health": 100,
            "board_items": [],
            "monster_choices": [
                {"id": "monster-a", "name": "A", "health": 100, "items": []},
                {"id": "monster-b", "name": "B", "health": 100, "items": []},
                {"id": "monster-c", "name": "C", "health": 100, "items": []},
            ],
        }

        with patch.object(service, "evaluate_monster_choices") as evaluate:
            evaluate.return_value = {
                "warnings": [],
                "results": [
                    {
                        "monster_id": "monster-b",
                        "monster_name": "B",
                        "wins": 0,
                        "draws": 0,
                        "simulations_completed": 1,
                        "battle_log": [],
                    }
                ],
            }
            response = service.simulate_selected_monster(
                data=data,
                player_payload=payload,
                monster_id="monster-b",
                simulation_count=1,
                use_cache=False,
            )

        self.assertTrue(response["available"])
        self.assertEqual(evaluate.call_count, 1)
        self.assertEqual(evaluate.call_args.kwargs["monster_choices"], [payload["monster_choices"][1]])
        self.assertTrue(evaluate.call_args.kwargs["sandstorm_config"].enabled)

    def test_selected_monster_missing_data_returns_clear_unavailable_result(self) -> None:
        response = service.simulate_selected_monster(
            data={"data_version": {}, "cards": {}},
            player_payload={"monster_choices": []},
            monster_id="missing",
            simulation_count=1,
            use_cache=False,
        )

        self.assertFalse(response["available"])
        self.assertEqual(response["reason"], "monster_data_not_found")

    def test_monster_simulation_count_is_limited(self) -> None:
        with self.assertRaises(service.BattleSimulationInputError) as raised:
            service.simulate_selected_monster(
                data={"data_version": {}, "cards": {}},
                player_payload={"monster_choices": [{"id": "a"}]},
                monster_id="a",
                simulation_count=51,
                use_cache=False,
            )

        self.assertEqual(raised.exception.field, "simulation_count")

    def test_monster_summary_reports_unsupported_items_and_skills(self) -> None:
        payload = {
            "combat_health": 100,
            "board_items": [],
            "monster_choices": [{"id": "monster-a", "name": "A", "health": 100, "items": []}],
        }

        with patch.object(service, "evaluate_monster_choices") as evaluate:
            evaluate.return_value = {
                "warnings": [],
                "results": [
                    {
                        "monster_id": "monster-a",
                        "monster_name": "A",
                        "status": "unsupported",
                        "confidence": "low",
                        "wins": 1,
                        "draws": 0,
                        "simulations_completed": 1,
                        "player_cards": ["Player Blade [Bronze]"],
                        "monster_cards": ["Odd Blade [Diamond]"],
                        "player_card_details": [
                            {
                                "name": "Player Blade",
                                "template_id": "tpl_player",
                                "tier": "Bronze",
                                "cooldown_sec": 5,
                                "base_damage": 10,
                                "attributes": {"DamageAmount": 10, "CooldownMax": 5000},
                            }
                        ],
                        "monster_card_details": [
                            {
                                "name": "Odd Blade",
                                "template_id": "tpl_odd",
                                "tier": "Diamond",
                                "cooldown_sec": 4,
                                "base_damage": 20,
                                "attributes": {"DamageAmount": 20, "CooldownMax": 4000},
                            }
                        ],
                        "battle_log": [],
                        "warnings": ["current_simulator_is_bounded_two_sided_timeline"],
                        "unsupported_cards": [
                            {"side": "monster", "card": "Missing Blade", "reason": "template_not_found"}
                        ],
                        "unsupported_skills": [
                            {"side": "monster", "skill": "Missing Skill", "reason": "template_not_found"}
                        ],
                        "unsupported_effects": [
                            {
                                "side": "monster",
                                "card": "Odd Blade",
                                "card_type": "item",
                                "effect": "UnsupportedAction",
                                "reason": "unsupported_attribute",
                            },
                            {
                                "side": "monster",
                                "card": "Arms Race",
                                "card_type": "skill",
                                "effect": "TReferenceValueCardCount",
                                "reason": "unsupported_value",
                            },
                        ],
                    }
                ],
            }
            response = service.simulate_selected_monster(
                data={"data_version": {}, "cards": {}},
                player_payload=payload,
                monster_id="monster-a",
                simulation_count=1,
                use_cache=False,
            )

        summary = response["summary"]
        self.assertEqual(summary["unsupported_item_count"], 2)
        self.assertEqual(summary["unsupported_skill_count"], 2)
        self.assertEqual(summary["unsupported_items"][0], "Missing Blade")
        self.assertTrue(any("Odd Blade" in item for item in summary["unsupported_items"]))
        self.assertTrue(any("Arms Race" in item for item in summary["unsupported_skills"]))
        self.assertTrue(summary["feedback_available"])
        self.assertIn("Player Blade [Bronze]", "\n".join(summary["feedback_lines"]))
        self.assertIn("Odd Blade [Diamond]", "\n".join(summary["feedback_lines"]))
        self.assertEqual(summary["player_card_details"][0]["cooldown_sec"], 5)
        self.assertEqual(summary["monster_card_details"][0]["template_id"], "tpl_odd")
        self.assertIn("player_card_details:", "\n".join(summary["feedback_lines"]))
        self.assertIn('"CooldownMax":5000', "\n".join(summary["feedback_lines"]))

    def test_monster_summary_feedback_is_available_even_without_detected_issue(self) -> None:
        summary = service._monster_summary(
            {
                "monster_id": "monster-ok",
                "monster_name": "Clean Monster",
                "status": "ok",
                "confidence": "high",
                "estimated_win_rate": 1.0,
                "wins": 1,
                "draws": 0,
                "simulations_completed": 1,
                "player_cards": ["Player Blade [Bronze]"],
                "monster_cards": ["Clean Claw [Bronze]"],
                "battle_log": [],
                "warnings": [],
                "unsupported_cards": [],
                "unsupported_skills": [],
                "unsupported_effects": [],
            },
            requested=1,
        )

        self.assertTrue(summary["feedback_available"])
        text = "\n".join(summary["feedback_lines"])
        self.assertIn("Clean Monster", text)
        self.assertIn("Player Blade [Bronze]", text)
        self.assertIn("Clean Claw [Bronze]", text)

    def test_training_dummy_stops_at_requested_duration_and_aggregates_metrics(self) -> None:
        weapon = make_damage_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        shield = make_shield_card("Shield", "tpl_shield", shield=3, cooldown_ms=1000)
        data = {
            "data_version": {},
            "cards": {"Weapon": weapon, "Shield": shield},
        }
        payload = {
            "combat_health": 100,
            "board_items": [
                {
                    "template_id": "tpl_weapon",
                    "name": "Weapon",
                    "rarity": "Bronze",
                    "section": "Hand",
                },
                {
                    "template_id": "tpl_shield",
                    "name": "Shield",
                    "rarity": "Bronze",
                    "section": "Hand",
                },
            ],
        }

        response = service.simulate_training_dummy(
            data=data,
            player_payload=payload,
            duration_seconds=5,
            dummy_max_health=1000,
            use_cache=False,
        )

        summary = response["summary"]
        self.assertTrue(response["ok"])
        self.assertEqual(summary["actual_duration_seconds"], 5)
        self.assertEqual(summary["total_damage"], 50)
        self.assertEqual(summary["damage_per_second"], 10)
        self.assertEqual(summary["shield_generated"], 15)
        self.assertEqual(summary["ending_player_shield"], 15)
        self.assertEqual(summary["card_uses"], 10)
        self.assertFalse(summary["dummy_killed"])
        self.assertTrue(
            all(event["time"] <= 5 for event in response["battle"]["sample_timeline"])
        )

    def test_training_dummy_metrics_include_defense_healing_and_control(self) -> None:
        card = PlacedCard("support", {"name": "Support"}, tier="Bronze")
        timeline = [
            {"time": 1, "side": "player", "target_side": "player", "kind": "shield", "source": "Support", "value": 12},
            {"time": 1, "side": "player", "target_side": "player", "kind": "regen-apply", "source": "Support", "value": 5},
            {"time": 2, "side": "environment", "target_side": "player", "kind": "regen-heal", "source": "regen", "value": 4},
            {"time": 2, "side": "player", "target_side": "player", "kind": "overheal", "source": "Support", "value": 3},
            {"time": 3, "side": "player", "target_side": "monster", "kind": "burn-apply", "source": "Support", "value": 7},
            {"time": 3, "side": "player", "target_side": "monster", "kind": "poison-apply", "source": "Support", "value": 2},
            {"time": 3, "side": "player", "target_side": "monster", "kind": "slow", "source": "Support", "value": 2, "effective_duration": 1},
            {"time": 3, "side": "player", "target_side": "monster", "kind": "freeze", "source": "Support", "value": 3},
            {"time": 3, "side": "player", "target_side": "player", "kind": "haste", "source": "Support", "value": 4},
            {"time": 4, "side": "player", "target_side": "player", "kind": "charge-resolved", "source": "Support", "value": 1.5},
        ]

        summary = service.aggregate_training_dummy_metrics(
            timeline,
            player_cards=[card],
            requested_duration=10,
            actual_duration=10,
            dummy_max_health=1000,
            dummy_remaining_health=1000,
            dummy_remaining_shield=0,
            player_remaining_shield=12,
            dummy_killed=False,
        )

        self.assertEqual(summary["shield_generated"], 12)
        self.assertEqual(summary["ending_player_shield"], 12)
        self.assertEqual(summary["effective_heal"], 4)
        self.assertEqual(summary["overheal"], 3)
        self.assertEqual(summary["requested_heal"], 7)
        self.assertEqual(summary["regen_applied"], 5)
        self.assertEqual(summary["burn_applied"], 7)
        self.assertEqual(summary["poison_applied"], 2)
        self.assertEqual(summary["slow_duration"], 1)
        self.assertEqual(summary["freeze_duration"], 3)
        self.assertEqual(summary["haste_duration"], 4)
        self.assertEqual(summary["charge_seconds"], 1.5)

    def test_training_dummy_rejects_too_long_duration(self) -> None:
        with self.assertRaises(service.BattleSimulationInputError) as raised:
            service.simulate_training_dummy(
                data={"data_version": {}, "cards": {}},
                player_payload={"combat_health": 100},
                duration_seconds=121,
                use_cache=False,
            )

        self.assertEqual(raised.exception.field, "duration_seconds")

    def test_training_dummy_accepts_zero_duration(self) -> None:
        response = service.simulate_training_dummy(
            data={"data_version": {}, "cards": {}},
            player_payload={"combat_health": 100, "board_items": []},
            duration_seconds=0,
            use_cache=False,
        )

        self.assertTrue(response["ok"])
        self.assertEqual(response["summary"]["actual_duration_seconds"], 0)

    def test_training_dummy_cache_changes_when_player_state_changes(self) -> None:
        weapon = make_damage_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        data = {"data_version": {}, "cards": {"Weapon": weapon}}
        payload = {
            "combat_health": 100,
            "board_items": [
                {
                    "template_id": "tpl_weapon",
                    "name": "Weapon",
                    "rarity": "Bronze",
                    "section": "Hand",
                }
            ],
        }

        first = service.simulate_training_dummy(data=data, player_payload=payload, duration_seconds=5)
        cached = service.simulate_training_dummy(data=data, player_payload=payload, duration_seconds=5)
        changed = service.simulate_training_dummy(
            data=data,
            player_payload={**payload, "combat_health": 90},
            duration_seconds=5,
        )

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(cached["cache"]["hit"])
        self.assertFalse(changed["cache"]["hit"])


if __name__ == "__main__":
    unittest.main()
