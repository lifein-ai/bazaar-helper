from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combat_simulator import (
    PlacedCard,
    RuleCondition,
    build_current_board_placements,
    calculate_burn_ticks,
    card_tags,
    coerce_placed_cards,
    estimate_self_health_ttk,
    extract_condition_meta,
    get_attr_value_by_tier,
    matches_card,
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
    def test_existing_enchantment_adds_effective_tags(self) -> None:
        card = make_card("Blade", {"DamageAmount": 5, "CooldownMax": 1000}, {"0": fired_action("TActionPlayerDamage")})
        placed = PlacedCard("blade", card, tier="Bronze", enchantment="Obsidian")

        self.assertEqual(card_tags(placed), {"enchanted", "obsidian", "damage"})

        coerced = coerce_placed_cards([{"placement_id": "blade", "card": card, "enchantment": "Toxic"}])[0]
        self.assertEqual(coerced.enchantment, "Toxic")
        self.assertIn("poison", card_tags(coerced))

    def test_has_enchantment_condition_matches_existing_enchantment(self) -> None:
        card = make_card("Blade", {"DamageAmount": 5, "CooldownMax": 1000}, {"0": fired_action("TActionPlayerDamage")})
        plain = PlacedCard("plain", card, tier="Bronze")
        enchanted = PlacedCard("enchanted", card, tier="Bronze", enchantment="Obsidian")
        cards = [plain, enchanted]
        any_enchanted = extract_condition_meta(
            {
                "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalHasEnchantment",
                "IsNot": True,
            }
        )
        non_enchanted = extract_condition_meta(
            {
                "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalHasEnchantment",
                "IsNot": False,
            }
        )
        obsidian = extract_condition_meta(
            {
                "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalHasEnchantment",
                "Enchantment": "Obsidian",
            }
        )

        self.assertTrue(matches_card(enchanted, any_enchanted, cards))
        self.assertFalse(matches_card(plain, any_enchanted, cards))
        self.assertTrue(matches_card(plain, non_enchanted, cards))
        self.assertFalse(matches_card(enchanted, non_enchanted, cards))
        self.assertTrue(matches_card(enchanted, obsidian, cards))
        self.assertFalse(matches_card(plain, obsidian, cards))

    def test_matches_card_does_not_treat_exported_heated_or_chilled_as_runtime_state(self) -> None:
        card = make_card("Food", {"Heated": 1, "Chilled": 1, "CooldownMax": 1000}, {})
        placed = PlacedCard("food", card, tier="Bronze")
        heated = RuleCondition(
            attr_conditions=[{"attribute": "Heated", "operator": "gt", "value": 0}]
        )
        chilled = RuleCondition(
            attr_conditions=[{"attribute": "Chilled", "operator": "gt", "value": 0}]
        )

        self.assertFalse(matches_card(placed, heated, [placed]))
        self.assertFalse(matches_card(placed, chilled, [placed]))

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

    def test_burn_tick_decays_by_floor_three_percent_after_damage(self) -> None:
        damage_events: list[dict[str, float]] = []
        timeline: list[dict] = []

        total = calculate_burn_ticks(
            [{"time": 0.0, "amount": 60}],
            duration_sec=1.0,
            damage_events=damage_events,
            timeline=timeline,
        )

        self.assertEqual(total, 119)
        self.assertEqual([event["amount"] for event in damage_events], [60, 59])
        self.assertEqual([event["value"] for event in timeline], [60, 59])

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

    def test_damage_crit_adds_an_extra_base_damage_multiplier(self) -> None:
        weapon = make_card(
            "Weapon",
            {"DamageAmount": 40, "CooldownMax": 5000, "CritChance": 100, "DamageCrit": 100},
            {"0": fired_action("TActionPlayerDamage")},
        )

        summary = simulate_combat([PlacedCard("weapon", weapon, tier="Bronze")], duration_sec=6)

        self.assertEqual(summary.total_damage, 120)

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

    def test_inactive_heated_snapshot_does_not_make_burn_permanent(self) -> None:
        card = make_card(
            "LauLau",
            {"BurnApplyAmount": 0, "RegenApplyAmount": 6, "CooldownMax": 5000},
            {
                "0": fired_action("TActionPlayerRegenApply", target_mode="Player"),
                "1": fired_action("TActionPlayerBurnApply"),
            },
        )
        card["description"] = "Regen {ability.0}\nHeated: Burn equal to this item's Regen"
        data = {"cards": {"LauLau": {**card, "template_id": "tpl_laulau"}}}
        state = {
            "board_items": [
                {
                    "id": "itm_1",
                    "template_id": "tpl_laulau",
                    "rarity": "Bronze",
                    "section": "Hand",
                    "current_attributes": {
                        "BurnApplyAmount": 6,
                        "CooldownMax": 5000,
                        "Heated": 0,
                        "RegenApplyAmount": 6,
                    },
                }
            ],
        }

        placed, skipped = build_current_board_placements(data, state)
        summary = simulate_combat(placed, duration_sec=6)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_burn_applied, 0)

    def test_active_heated_snapshot_keeps_conditional_burn(self) -> None:
        card = make_card(
            "LauLau",
            {"BurnApplyAmount": 0, "RegenApplyAmount": 6, "CooldownMax": 5000},
            {
                "0": fired_action("TActionPlayerRegenApply", target_mode="Player"),
                "1": fired_action("TActionPlayerBurnApply"),
            },
        )
        card["description"] = "Regen {ability.0}\nHeated: Burn equal to this item's Regen"
        data = {"cards": {"LauLau": {**card, "template_id": "tpl_laulau"}}}
        state = {
            "board_items": [
                {
                    "id": "itm_1",
                    "template_id": "tpl_laulau",
                    "rarity": "Bronze",
                    "section": "Hand",
                    "current_attributes": {
                        "BurnApplyAmount": 6,
                        "CooldownMax": 5000,
                        "Heated": 1,
                        "RegenApplyAmount": 6,
                    },
                }
            ],
        }

        placed, skipped = build_current_board_placements(data, state)
        summary = simulate_combat(placed, duration_sec=6)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_burn_applied, 6)

    def test_fiery_snapshot_adds_missing_burn_action(self) -> None:
        card = make_card(
            "Trail Mix",
            {"RegenApplyAmount": 5, "CooldownMax": 8000},
            {"0": fired_action("TActionPlayerRegenApply", target_mode="Player")},
        )
        data = {"cards": {"Trail Mix": {**card, "template_id": "tpl_trail_mix"}}}
        state = {
            "board_items": [
                {
                    "id": "itm_1",
                    "template_id": "tpl_trail_mix",
                    "rarity": "Bronze",
                    "section": "Hand",
                    "enchantments": ["Fiery"],
                    "current_attributes": {
                        "BurnApplyAmount": 5,
                        "CooldownMax": 8000,
                        "RegenApplyAmount": 5,
                    },
                }
            ],
        }

        placed, skipped = build_current_board_placements(data, state)
        summary = simulate_combat(placed, duration_sec=9)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_burn_applied, 5)

    def test_enchanted_snapshot_adds_missing_combat_actions_from_runtime_attrs(self) -> None:
        card = make_card(
            "Snack",
            {"CooldownMax": 5000},
            {},
        )
        data = {"cards": {"Snack": {**card, "template_id": "tpl_snack"}}}
        state = {
            "board_items": [
                {
                    "id": "itm_1",
                    "template_id": "tpl_snack",
                    "rarity": "Bronze",
                    "section": "Hand",
                    "enchantments": ["Shielded"],
                    "current_attributes": {
                        "CooldownMax": 5000,
                        "DamageAmount": 4,
                        "ShieldApplyAmount": 7,
                    },
                }
            ],
        }

        placed, skipped = build_current_board_placements(data, state)
        summary = simulate_combat(placed, duration_sec=6)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_damage, 4)
        self.assertEqual(summary.total_shield, 7)

    def test_restorative_snapshot_derives_heal_from_regen_enchantment_rule(self) -> None:
        card = make_card(
            "LauLau",
            {"CooldownMax": 5000, "RegenApplyAmount": 6},
            {"0": fired_action("TActionPlayerRegenApply", target_mode="Player")},
        )
        data = {"cards": {"LauLau": {**card, "template_id": "tpl_laulau"}}}
        state = {
            "board_items": [
                {
                    "id": "itm_1",
                    "template_id": "tpl_laulau",
                    "rarity": "Bronze",
                    "section": "Hand",
                    "enchantments": ["Restorative"],
                    "current_attributes": {
                        "CooldownMax": 5000,
                        "RegenApplyAmount": 6,
                    },
                }
            ],
        }

        placed, skipped = build_current_board_placements(data, state)
        summary = simulate_combat(placed, duration_sec=6)

        self.assertEqual(skipped, [])
        self.assertEqual(summary.total_heal, 42)
        self.assertEqual(get_attr_value_by_tier(placed[0].card, "HealAmount", "Bronze"), 30)

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
