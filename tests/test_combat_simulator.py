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


def aura_modify_attribute_skill(
    name: str,
    attr_type: str,
    value: float,
    *,
    tag: str = "Weapon",
) -> dict:
    return {
        "id": name.lower(),
        "template_id": name.lower(),
        "name": name,
        "type": "Skill",
        "size": "Medium",
        "tags": [],
        "hidden_tags": [],
        "tiers": ["Diamond"],
        "rarity": "Diamond",
        "raw_effects": {
            "abilities": {},
            "auras": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAura",
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.AuraActions.TAuraActionCardModifyAttribute",
                        "AttributeType": attr_type,
                        "Operation": "Add",
                        "Value": {
                            "$type": "BazaarGameShared.Domain.Values.TFixedValue",
                            "Value": value,
                        },
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
                            "TargetSection": "SelfHand",
                            "Conditions": {
                                "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
                                "Tags": [tag],
                                "Operator": "Any",
                            },
                        },
                    },
                }
            },
            "tiers_raw": {
                "Diamond": {
                    "Attributes": {},
                    "AbilityIds": [],
                    "AuraIds": ["0"],
                }
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

    def test_simulates_direct_shield_and_heal_from_raw_effects(self) -> None:
        card = make_card(
            "Support",
            {"ShieldApplyAmount": 12, "HealAmount": 8, "CooldownMax": 5000},
            {
                "0": fired_action("TActionPlayerShieldApply", target_mode="Player"),
                "1": fired_action("TActionPlayerHealApply", target_mode="Player"),
            },
        )

        summary = simulate_combat([PlacedCard("support", card, tier="Bronze")], duration_sec=11)

        self.assertEqual(summary.total_uses, 2)
        self.assertEqual(summary.total_shield, 24)
        self.assertEqual(summary.total_heal, 16)
        self.assertEqual(summary.by_card_shield["support"], 24)
        self.assertEqual(summary.by_card_heal["support"], 16)
        self.assertTrue(any(event["kind"] == "shield" for event in summary.debug_timeline))
        self.assertTrue(any(event["kind"] == "heal" for event in summary.debug_timeline))

    def test_static_skill_aura_grants_lifesteal(self) -> None:
        weapon = make_card("Weapon", {"DamageAmount": 30, "CooldownMax": 5000}, {"0": fired_action("TActionPlayerDamage")})
        weapon["tags"] = ["Weapon"]
        data = {
            "cards": {
                "Weapon": {**weapon, "template_id": "tpl_weapon"},
                "Lifesteal Skill": {**aura_modify_attribute_skill("Lifesteal Skill", "Lifesteal", 100), "template_id": "tpl_skill"},
            }
        }
        state = {
            "board_items": [{"id": "itm_1", "template_id": "tpl_weapon", "rarity": "Bronze", "section": "Hand"}],
            "skills": [{"id": "skill_1", "template_id": "tpl_skill", "rarity": "Diamond"}],
        }

        placed, skipped = build_current_board_placements(data, state, include_skills=True)
        summary = simulate_combat(placed, duration_sec=6)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_damage, 30)
        self.assertEqual(summary.total_heal, 30)
        self.assertTrue(any(event.get("reason") == "lifesteal" for event in summary.debug_timeline))

    def test_static_skill_aura_adds_expected_crit_damage(self) -> None:
        weapon = make_card("Weapon", {"DamageAmount": 40, "CooldownMax": 5000}, {"0": fired_action("TActionPlayerDamage")})
        weapon["tags"] = ["Weapon"]
        data = {
            "cards": {
                "Weapon": {**weapon, "template_id": "tpl_weapon"},
                "Crit Skill": {**aura_modify_attribute_skill("Crit Skill", "CritChance", 50), "template_id": "tpl_skill"},
            }
        }
        state = {
            "board_items": [{"id": "itm_1", "template_id": "tpl_weapon", "rarity": "Bronze", "section": "Hand"}],
            "skills": [{"id": "skill_1", "template_id": "tpl_skill", "rarity": "Diamond"}],
        }

        placed, skipped = build_current_board_placements(data, state, include_skills=True)
        summary = simulate_combat(placed, duration_sec=6)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_damage, 60)

    def test_heal_alias_and_regen_are_simulated(self) -> None:
        card = make_card(
            "Regenerator",
            {"HealAmount": 5, "RegenApplyAmount": 3, "CooldownMax": 5000},
            {
                "0": fired_action("TActionPlayerHeal", target_mode="Player"),
                "1": fired_action("TActionPlayerRegenApply", target_mode="Player"),
            },
        )

        summary = simulate_combat([PlacedCard("regen", card, tier="Bronze")], duration_sec=7)

        self.assertEqual(summary.total_uses, 1)
        self.assertEqual(summary.total_heal, 14)
        self.assertTrue(any(event["kind"] == "regen-apply" for event in summary.debug_timeline))

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
