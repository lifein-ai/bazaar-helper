from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combat_simulator import (
    PlacedCard,
    build_current_board_placements,
    estimate_self_health_ttk,
    get_attr_value_by_tier,
    simulate_combat,
)


def make_card(name: str, attrs: dict[str, float], abilities: dict[str, dict]) -> dict:
    return {
        "id": name.lower(),
        "name": name,
        "type": "Item",
        "size": "Small",
        "tags": [],
        "hidden_tags": [],
        "tiers": ["Bronze"],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": abilities,
            "auras": {},
            "tiers_raw": {
                "Bronze": {
                    "Attributes": attrs,
                    "AbilityIds": list(abilities.keys()),
                    "AuraIds": [],
                }
            },
        },
    }


def fired_action(action_type: str, target_mode: str = "Opponent") -> dict:
    return {
        "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
        "Trigger": {"$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardFired"},
        "Action": {
            "$type": f"BazaarGameShared.Domain.Effect.Actions.{action_type}",
            "Target": {
                "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                "TargetMode": target_mode,
            },
        },
    }


class CombatSimulatorTests(unittest.TestCase):
    def test_reads_current_raw_effect_tier_attributes(self) -> None:
        card = make_card("Weapon", {"DamageAmount": 30, "CooldownMax": 5000}, {"0": fired_action("TActionPlayerDamage")})

        self.assertEqual(get_attr_value_by_tier(card, "DamageAmount", "Bronze"), 30)
        self.assertEqual(get_attr_value_by_tier(card, "CooldownMax", "Bronze"), 5)

    def test_simulates_cooldown_damage_from_raw_effects(self) -> None:
        card = make_card("Weapon", {"DamageAmount": 30, "CooldownMax": 5000}, {"0": fired_action("TActionPlayerDamage")})

        summary = simulate_combat([PlacedCard("weapon", card, tier="Bronze")], duration_sec=11)

        self.assertEqual(summary.total_uses, 2)
        self.assertEqual(summary.total_damage, 60)
        self.assertEqual(summary.by_card_damage["weapon"], 60)

    def test_simulates_poison_tick_damage(self) -> None:
        card = make_card("Poisoner", {"PoisonApplyAmount": 5, "CooldownMax": 5000}, {"0": fired_action("TActionPlayerPoisonApply")})

        summary = simulate_combat([PlacedCard("poisoner", card, tier="Bronze")], duration_sec=6)

        self.assertEqual(summary.total_poison_applied, 5)
        self.assertEqual(summary.total_poison_tick_damage, 10)
        self.assertEqual(summary.total_damage, 10)

    def test_builds_current_board_with_instance_attribute_overrides(self) -> None:
        card = make_card("Weapon", {"DamageAmount": 10, "CooldownMax": 5000}, {"0": fired_action("TActionPlayerDamage")})
        data = {"cards": {"Weapon": {**card, "template_id": "tpl_weapon"}}}
        state = {
            "combat_health": 80,
            "board_items": [
                {
                    "id": "itm_1",
                    "template_id": "tpl_weapon",
                    "rarity": "Bronze",
                    "section": "Hand",
                    "current_attributes": {"DamageAmount": 40, "CooldownMax": 2000},
                    "runtime_values": {"Size": "Small"},
                }
            ],
        }

        placed, skipped = build_current_board_placements(data, state)
        summary = simulate_combat(placed, duration_sec=4)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_uses, 2)
        self.assertEqual(summary.total_damage, 80)

    def test_estimates_self_health_ttk_against_current_health(self) -> None:
        card = make_card("Weapon", {"DamageAmount": 10, "CooldownMax": 5000}, {"0": fired_action("TActionPlayerDamage")})
        data = {"cards": {"Weapon": {**card, "template_id": "tpl_weapon"}}}
        state = {
            "combat_health": 80,
            "board_items": [
                {
                    "id": "itm_1",
                    "template_id": "tpl_weapon",
                    "rarity": "Bronze",
                    "section": "Hand",
                    "current_attributes": {"DamageAmount": 40, "CooldownMax": 2000},
                }
            ],
        }

        estimate = estimate_self_health_ttk(data, state, horizon_sec=10)

        self.assertIsNotNone(estimate)
        self.assertEqual(estimate.kill_time_sec, 4)
        self.assertEqual(estimate.direct_kill_time_sec, 4)
        self.assertEqual(estimate.target_health, 80)
        self.assertEqual(estimate.simulated_card_count, 1)


if __name__ == "__main__":
    unittest.main()
