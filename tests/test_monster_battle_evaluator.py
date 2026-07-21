from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combat_simulator import HealCleanseConfig, PlacedCard, read_rules  # noqa: E402
from monster_battle_evaluator import (  # noqa: E402
    BattleCardRef,
    BattleEventScheduler,
    BattleSide,
    _advance_cooldowns,
    _apply_heal,
    _apply_damage,
    _apply_battle_rule,
    _add_cooldown_modifier,
    _add_runtime_state,
    _add_runtime_tags,
    _expire_cooldown_modifier,
    _expire_runtime_states_for_sides,
    _effective_status_duration,
    _effective_multicast,
    _apply_rage,
    _apply_max_health_modifier,
    _has_runtime_state,
    _make_battle_side,
    _process_burn_tick,
    _process_second_tick,
    _refresh_runtime_state_auras,
    _remove_runtime_state,
    _remove_runtime_tags,
    _resolve_charge_event,
    _simulate_two_sided_battle,
    clear_monster_evaluation_cache,
    evaluate_monster_choices,
)


def make_card(
    name: str,
    template_id: str,
    *,
    damage: float = 0,
    cooldown_ms: int = 5000,
    action_type: str = "TActionPlayerDamage",
    trigger_type: str = "TTriggerOnCardFired",
) -> dict:
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
                        "$type": f"BazaarGameShared.Domain.Effect.Trigger.{trigger_type}"
                    },
                    "Action": {
                        "$type": f"BazaarGameShared.Domain.Effect.Actions.{action_type}",
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
                        "HealAmount": damage,
                        "CooldownMax": cooldown_ms,
                    },
                    "AbilityIds": ["0"],
                    "AuraIds": [],
                }
            },
        },
    }


def state_with(template_id: str, *, health: int = 100, skills: list[dict] | None = None) -> dict:
    return {
        "combat_health": health,
        "board_items": [
            {
                "id": f"itm_{template_id}",
                "template_id": template_id,
                "rarity": "Bronze",
                "section": "Hand",
            }
        ],
        "skills": skills or [],
    }


def make_aura_skill(name: str, template_id: str, attr_type: str, value: float) -> dict:
    return {
        "id": template_id,
        "template_id": template_id,
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
                                "Tags": ["Weapon"],
                                "Operator": "Any",
                            },
                        },
                    },
                }
            },
            "tiers_raw": {"Diamond": {"Attributes": {}, "AbilityIds": [], "AuraIds": ["0"]}},
        },
    }


def make_damage_reduction_card(name: str, template_id: str) -> dict:
    card = make_card(name, template_id, damage=0)
    card["raw_effects"]["abilities"]["0"]["Action"] = {
        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerModifyAttribute",
        "AttributeType": "PercentDamageReduction",
        "Operation": "Add",
        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 100},
        "Duration": {
            "$type": "BazaarGameShared.Domain.Durations.TCombatDuration",
            "DurationInMs": 10000,
        },
        "Target": {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
            "TargetMode": "Self",
        },
    }
    return card


def make_revive_skill(name: str, template_id: str) -> dict:
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Skill",
        "size": "Medium",
        "tags": [],
        "hidden_tags": [],
        "tiers": ["Diamond"],
        "rarity": "Diamond",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnPlayerDied",
                        "Subject": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                            "TargetMode": "Self",
                        },
                    },
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerReviveHeal",
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                            "TargetMode": "Self",
                        },
                    },
                }
            },
            "auras": {},
            "tiers_raw": {"Diamond": {"Attributes": {}, "AbilityIds": ["0"], "AuraIds": []}},
        },
    }


def make_freeze_card(name: str, template_id: str, *, damage: float, freeze_sec: float, cooldown_ms: int) -> dict:
    card = make_card(name, template_id, damage=damage, cooldown_ms=cooldown_ms)
    card["raw_effects"]["abilities"]["1"] = {
        "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
        "Trigger": {"$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardFired"},
        "Action": {
            "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardFreeze",
            "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": freeze_sec},
            "Target": {
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
                "TargetSection": "OpponentHand",
            },
        },
    }
    card["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["FreezeAmount"] = freeze_sec
    card["raw_effects"]["tiers_raw"]["Bronze"]["AbilityIds"].append("1")
    return card


def make_fight_started_charge_skill(name: str, template_id: str, amount: float) -> dict:
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Skill",
        "size": "Medium",
        "tags": [],
        "hidden_tags": [],
        "tiers": ["Bronze"],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnFightStarted"
                    },
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardCharge",
                        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": amount},
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
                            "TargetSection": "SelfHand",
                        },
                    },
                }
            },
            "auras": {},
            "tiers_raw": {"Bronze": {"Attributes": {}, "AbilityIds": ["0"], "AuraIds": []}},
        },
    }


def make_cooldown_modifier_card(name: str, template_id: str, amount: float, *, operation: str = "Add", duration_ms: int = 0) -> dict:
    card = make_card(name, template_id, damage=0, cooldown_ms=1000)
    action = {
        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardModifyAttribute",
        "AttributeType": "CooldownMax",
        "Operation": operation,
        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": amount},
        "Target": {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
        },
    }
    if duration_ms > 0:
        action["Duration"] = {
            "$type": "BazaarGameShared.Domain.Durations.TCombatDuration",
            "DurationInMs": duration_ms,
        }
    card["raw_effects"]["abilities"]["1"] = {
        "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
        "Trigger": {"$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardFired"},
        "Action": action,
    }
    card["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["CooldownMax"] = 1000
    card["raw_effects"]["tiers_raw"]["Bronze"]["AbilityIds"].append("1")
    return card


def make_limited_item_used_shield_skill(name: str, template_id: str, *, limit: int, scope: str = "combat") -> dict:
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Skill",
        "size": "Medium",
        "tags": [],
        "hidden_tags": [],
        "tiers": ["Bronze"],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnItemUsed",
                        "MaxTriggers": limit,
                        "LimitScope": scope,
                    },
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerShieldApply",
                        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 10},
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                            "TargetMode": "Self",
                        },
                    },
                }
            },
            "auras": {},
            "tiers_raw": {"Bronze": {"Attributes": {}, "AbilityIds": ["0"], "AuraIds": []}},
        },
    }


def make_max_health_card(name: str, template_id: str, amount: float, *, operation: str = "Add") -> dict:
    card = make_card(name, template_id, damage=0, cooldown_ms=1000)
    card["raw_effects"]["abilities"]["0"]["Action"] = {
        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerModifyAttribute",
        "AttributeType": "MaxHealth",
        "Operation": operation,
        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": amount},
        "Target": {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
            "TargetMode": "Self",
        },
    }
    return card


def make_started_flying_shield_skill(name: str, template_id: str, amount: float) -> dict:
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Skill",
        "size": "Medium",
        "tags": [],
        "hidden_tags": [],
        "tiers": ["Bronze"],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardStartedFlying",
                        "Subject": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
                            "TargetSection": "SelfHand",
                        },
                    },
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerShieldApply",
                        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": amount},
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                            "TargetMode": "Self",
                        },
                    },
                }
            },
            "auras": {},
            "tiers_raw": {"Bronze": {"Attributes": {}, "AbilityIds": ["0"], "AuraIds": []}},
        },
    }


def make_enraged_shield_skill(name: str, template_id: str, amount: float) -> dict:
    skill = make_started_flying_shield_skill(name, template_id, amount)
    skill["raw_effects"]["abilities"]["0"]["Trigger"] = {
        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnPlayerEnraged",
        "Subject": {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
            "TargetMode": "Self",
        },
    }
    return skill


def make_runtime_aura_skill(
    name: str,
    template_id: str,
    attr_type: str,
    value: float,
    state_attr: str,
    *,
    operation: str = "Add",
    target_mode: str = "SelfHand",
) -> dict:
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Skill",
        "size": "Medium",
        "tags": [],
        "hidden_tags": [],
        "tiers": ["Bronze"],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {},
            "auras": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAura",
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.AuraActions.TAuraActionCardModifyAttribute",
                        "AttributeType": attr_type,
                        "Operation": operation,
                        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": value},
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
                            "TargetSection": target_mode,
                            "Conditions": {
                                "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalAttribute",
                                "Attribute": state_attr,
                                "ComparisonOperator": "NotEqual",
                                "ComparisonValue": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 0},
                            },
                        },
                    },
                }
            },
            "tiers_raw": {"Bronze": {"Attributes": {}, "AbilityIds": [], "AuraIds": ["0"]}},
        },
    }


def make_state_apply_card(
    name: str,
    template_id: str,
    state_attr: str,
    *,
    cooldown_ms: int = 1000,
    duration_ms: int = 1000,
) -> dict:
    card = make_card(name, template_id, damage=0, cooldown_ms=cooldown_ms)
    card["raw_effects"]["abilities"]["0"]["Action"] = {
        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardModifyAttribute",
        "AttributeType": state_attr,
        "Operation": "Add",
        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 1},
        "Duration": {
            "$type": "BazaarGameShared.Domain.Durations.TCombatDuration",
            "DurationInMs": duration_ms,
        },
        "Target": {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
        },
    }
    return card


def make_tagged_item_used_shield_skill(name: str, template_id: str, tag: str, amount: float) -> dict:
    skill = make_started_flying_shield_skill(name, template_id, amount)
    skill["raw_effects"]["abilities"]["0"]["Trigger"] = {
        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnItemUsed",
        "Subject": {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
            "TargetSection": "SelfHand",
            "Conditions": {
                "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
                "Tags": [tag],
                "Operator": "Any",
            },
        },
    }
    return skill


class MonsterBattleEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_monster_evaluation_cache()

    def _charge_scheduler_target(self, *, cooldown_ms: int = 6000) -> tuple[BattleEventScheduler, BattleSide, BattleCardRef, PlacedCard]:
        timeline: list[dict] = []
        target_card = make_card("Target", "tpl_target", damage=0, cooldown_ms=cooldown_ms)
        side = _make_battle_side("player", [PlacedCard("target", target_card, tier="Bronze")], 100, 10)
        source = PlacedCard("source", make_fight_started_charge_skill("Source", "tpl_source", 1), tier="Bronze")
        return BattleEventScheduler(timeline), side, BattleCardRef(side, side.active[0]), source

    def test_same_charge_port_queues_charge_icd(self) -> None:
        scheduler, side, target, source = self._charge_scheduler_target()

        for _ in range(3):
            scheduler.trigger_charge(target, 0.0, source_side=side, source=source, port_id="port-a", amount=1)

        times = [round(event.execute_time, 2) for event in sorted(scheduler.events)]
        self.assertEqual(times, [0.0, 0.25, 0.5])

    def test_different_charge_ports_resolve_together(self) -> None:
        scheduler, side, target, source = self._charge_scheduler_target()

        for port_id in ("port-a", "port-b", "port-c"):
            scheduler.trigger_charge(target, 0.0, source_side=side, source=source, port_id=port_id, amount=1)

        times = [round(event.execute_time, 2) for event in sorted(scheduler.events)]
        self.assertEqual(times, [0.0, 0.0, 0.0])

    def test_item_use_requests_are_limited_by_item_icd(self) -> None:
        scheduler, _, target, _ = self._charge_scheduler_target()

        for _ in range(3):
            scheduler.request_item_use(target, 0.0, reason="test")

        times = [round(event.execute_time, 2) for event in sorted(scheduler.events)]
        self.assertEqual(times, [0.0, 0.25, 0.5])

    def test_multicast_creates_icd_spaced_item_uses(self) -> None:
        multi = make_card("Multi", "tpl_multi", damage=0, cooldown_ms=1000)
        multi["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["Multicast"] = 3
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("multi", multi, tier="Bronze")],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=2,
            rng=None,
        )

        times = [
            round(float(event["time"]), 2)
            for event in outcome.timeline
            if event["kind"] == "item-used" and event["source"] == "Multi"
        ]
        self.assertEqual(times[:3], [1.0, 1.25, 1.5])

    def test_charge_cannot_bypass_item_use_icd(self) -> None:
        target = make_card("Target", "tpl_target", damage=0, cooldown_ms=10000)
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)
        player_cards = [
            PlacedCard("target", target, start=0, tier="Bronze"),
            PlacedCard("charge_a", make_fight_started_charge_skill("A", "tpl_a", 10), start=1, tier="Bronze"),
            PlacedCard("charge_b", make_fight_started_charge_skill("B", "tpl_b", 10), start=2, tier="Bronze"),
            PlacedCard("charge_c", make_fight_started_charge_skill("C", "tpl_c", 10), start=3, tier="Bronze"),
        ]

        outcome = _simulate_two_sided_battle(
            player_cards=player_cards,
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=1,
            rng=None,
        )

        times = [
            round(float(event["time"]), 2)
            for event in outcome.timeline
            if event["kind"] == "item-used" and event["source"] == "Target"
        ]
        self.assertEqual(times[:3], [0.0, 0.25, 0.5])

    def test_queued_charge_continues_after_cooldown_cycle_resets(self) -> None:
        scheduler, side, target, source = self._charge_scheduler_target(cooldown_ms=6000)
        timeline = scheduler.timeline
        for _ in range(4):
            scheduler.trigger_charge(target, 0.0, source_side=side, source=source, port_id="same-port", amount=2)

        now = 0.0
        while scheduler.events:
            event_time = scheduler.next_time()
            _advance_cooldowns([side], now, event_time - now)
            now = event_time
            event = scheduler.pop_due(now)
            if event is not None and event.kind == "CHARGE_RESOLVED":
                _resolve_charge_event(event, scheduler, now, timeline)

        late_charge = [
            event
            for event in timeline
            if event.get("kind") == "charge-resolved" and round(float(event.get("time") or 0), 2) == 0.75
        ][0]
        self.assertEqual(late_charge["old_remaining"], 5.75)
        self.assertEqual(late_charge["new_remaining"], 3.75)

    def test_cooldown_progresses_while_waiting_for_charge_icd(self) -> None:
        scheduler, side, target, source = self._charge_scheduler_target(cooldown_ms=10000)
        side.haste_until[target.card.placement_id] = 1.0
        timeline = scheduler.timeline
        for _ in range(2):
            scheduler.trigger_charge(target, 0.0, source_side=side, source=source, port_id="same-port", amount=1)

        now = 0.0
        while scheduler.events:
            event_time = scheduler.next_time()
            _advance_cooldowns([side], now, event_time - now)
            now = event_time
            event = scheduler.pop_due(now)
            if event is not None and event.kind == "CHARGE_RESOLVED":
                _resolve_charge_event(event, scheduler, now, timeline)

        second_charge = [
            event
            for event in timeline
            if event.get("kind") == "charge-resolved" and round(float(event.get("time") or 0), 2) == 0.25
        ][0]
        self.assertEqual(second_charge["old_remaining"], 8.5)
        self.assertEqual(second_charge["new_remaining"], 7.5)

    def test_effective_heal_cleanses_burn_and_poison_separately(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=50, max_health=100, burn_stack=100, poison_stack=60)

        actual = _apply_heal(side, 40, 2.0, "Heal", timeline)

        self.assertEqual(actual, 40)
        self.assertEqual(side.burn_stack, 90)
        self.assertEqual(side.poison_stack, 54)

    def test_overheal_does_not_cleanse_by_default(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=100, max_health=100, burn_stack=100, poison_stack=60)

        actual = _apply_heal(side, 40, 2.0, "Heal", timeline)

        self.assertEqual(actual, 0)
        self.assertEqual(side.burn_stack, 100)
        self.assertEqual(side.poison_stack, 60)

    def test_heal_cleanse_keeps_burn_and_poison_independent(self) -> None:
        burn_timeline: list[dict] = []
        burn_side = BattleSide("player", [], health=50, max_health=100, burn_stack=20, poison_stack=0)
        poison_timeline: list[dict] = []
        poison_side = BattleSide("player", [], health=50, max_health=100, burn_stack=0, poison_stack=20)

        _apply_heal(burn_side, 10, 1.0, "Heal", burn_timeline)
        _apply_heal(poison_side, 10, 1.0, "Heal", poison_timeline)

        self.assertEqual(burn_side.burn_stack, 18)
        self.assertEqual(burn_side.poison_stack, 0)
        self.assertEqual(poison_side.burn_stack, 0)
        self.assertEqual(poison_side.poison_stack, 18)

    def test_heal_cleanse_can_use_actual_heal_basis(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=50, max_health=100, burn_stack=100, poison_stack=60)
        config = HealCleanseConfig(basis="actual_heal", ratio=0.10, rounding="ceil", require_actual_heal=True)

        _apply_heal(side, 40, 2.0, "Heal", timeline, cleanse_config=config)

        self.assertEqual(side.burn_stack, 96)
        self.assertEqual(side.poison_stack, 56)

    def test_overheal_emits_independent_event(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=90, max_health=100)

        actual = _apply_heal(side, 40, 2.0, "Heal", timeline)

        self.assertEqual(actual, 10)
        self.assertEqual(side.health, 100)
        self.assertTrue(any(event["kind"] == "heal" and event["value"] == 10 for event in timeline))
        self.assertTrue(any(event["kind"] == "overheal" and event["value"] == 30 for event in timeline))

    def test_full_health_heal_emits_overheal_without_cleanse(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=100, max_health=100, burn_stack=10, poison_stack=10)

        actual = _apply_heal(side, 5, 1.0, "Heal", timeline)

        self.assertEqual(actual, 0)
        self.assertEqual(side.burn_stack, 10)
        self.assertEqual(side.poison_stack, 10)
        self.assertFalse(any(event["kind"] == "heal" for event in timeline))
        self.assertTrue(any(event["kind"] == "overheal" and event["value"] == 5 for event in timeline))

    def test_lifesteal_does_not_cleanse_or_overheal_by_default(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=100, max_health=100, burn_stack=100, poison_stack=60)

        actual = _apply_heal(side, 20, 1.0, "Weapon", timeline, reason="lifesteal")

        self.assertEqual(actual, 0)
        self.assertEqual(side.burn_stack, 100)
        self.assertEqual(side.poison_stack, 60)
        self.assertFalse(any(event["kind"] == "overheal" for event in timeline))

    def test_heal_cleanse_rounding_and_low_stack_are_capped(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=90, max_health=100, burn_stack=1, poison_stack=11)

        _apply_heal(side, 1, 1.0, "Heal", timeline)

        self.assertEqual(side.burn_stack, 0)
        self.assertEqual(side.poison_stack, 9)

    def test_burn_tick_is_halved_before_shield_absorbs(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100)
        monster = BattleSide("monster", [], health=100, max_health=100, shield=1, burn_stack=100)

        _process_burn_tick(player, monster, 0.5, timeline)

        self.assertEqual(monster.shield, 0)
        self.assertEqual(monster.health, 51)
        burn_event = [event for event in timeline if event["kind"] == "burn-tick"][0]
        self.assertEqual(burn_event["raw"], 100)
        self.assertEqual(burn_event["reduced"], 50)
        self.assertEqual(burn_event["absorbed"], 1)

    def test_poison_tick_bypasses_shield(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100)
        monster = BattleSide("monster", [], health=100, max_health=100, shield=20, poison_stack=30)

        _process_second_tick(player, monster, 1.0, timeline)

        self.assertEqual(monster.shield, 20)
        self.assertEqual(monster.health, 70)
        poison_event = [event for event in timeline if event["kind"] == "poison-tick"][0]
        self.assertTrue(poison_event["bypass_shield"])

    def test_poison_and_regen_resolve_as_net_periodic_damage(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100)
        monster = BattleSide("monster", [], health=100, max_health=100, shield=20, poison_stack=50, regen_stack=20)

        _process_second_tick(player, monster, 1.0, timeline)

        self.assertEqual(monster.shield, 20)
        self.assertEqual(monster.health, 70)
        periodic_event = [event for event in timeline if event["kind"] == "periodic-health-resolution"][0]
        self.assertEqual(periodic_event["poison"], 50)
        self.assertEqual(periodic_event["regen"], 20)
        poison_event = [event for event in timeline if event["kind"] == "poison-tick"][0]
        self.assertEqual(poison_event["value"], 30)
        self.assertEqual(poison_event["raw"], 50)

    def test_regen_net_tick_does_not_cleanse_as_normal_heal(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=50, max_health=100, burn_stack=100, poison_stack=20, regen_stack=50)
        monster = BattleSide("monster", [], health=100, max_health=100)

        _process_second_tick(player, monster, 1.0, timeline)

        self.assertEqual(player.health, 80)
        self.assertEqual(player.burn_stack, 100)
        self.assertEqual(player.poison_stack, 20)
        self.assertTrue(any(event["kind"] == "regen-heal" and event["value"] == 30 for event in timeline))
        self.assertFalse(any(event["kind"] in {"heal", "burn-cleansed", "poison-cleansed"} for event in timeline))

    def test_flying_halves_slow_and_freeze_duration_only(self) -> None:
        timeline: list[dict] = []
        card = make_card("Target", "tpl_target", damage=0, cooldown_ms=5000)
        side = _make_battle_side("player", [PlacedCard("target", card, tier="Bronze")], 100, 10)
        target = BattleCardRef(side, side.active[0])

        _add_runtime_state(
            side,
            target.card,
            "flying",
            value=1,
            source_id="test",
            source_label="Test",
            source_side=side,
            now=0,
            duration_sec=10,
            timeline=timeline,
        )

        self.assertEqual(_effective_status_duration(target, "slow", 4), 2)
        self.assertEqual(_effective_status_duration(target, "freeze", 4), 2)
        self.assertEqual(_effective_status_duration(target, "haste", 4), 4)

    def test_runtime_state_sources_stack_until_all_removed(self) -> None:
        timeline: list[dict] = []
        card = make_card("Target", "tpl_target", damage=0, cooldown_ms=5000)
        side = _make_battle_side("player", [PlacedCard("target", card, tier="Bronze")], 100, 10)
        target = side.active[0]

        for source_id in ("a", "b"):
            _add_runtime_state(
                side,
                target,
                "chilled",
                value=1,
                source_id=source_id,
                source_label="Chill",
                source_side=side,
                now=0,
                duration_sec=10,
                timeline=timeline,
            )

        _remove_runtime_state(side, target, "chilled", source_id="a", source_label="Chill", source_side=side, now=1, timeline=timeline)
        self.assertTrue(_has_runtime_state(side, target, "chilled", 1))

        _remove_runtime_state(side, target, "chilled", source_id="b", source_label="Chill", source_side=side, now=2, timeline=timeline)
        self.assertFalse(_has_runtime_state(side, target, "chilled", 2))
        self.assertEqual(len([event for event in timeline if event["kind"] == "item-chill-ended"]), 1)

    def test_rage_enters_and_expires_enrage(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100, rage_max=50, enraged_duration_sec=1)
        monster = BattleSide("monster", [], health=100, max_health=100)

        self.assertFalse(_apply_rage(player, 30, 0.0, "Rage", timeline))
        self.assertFalse(player.is_enraged)
        self.assertTrue(_apply_rage(player, 20, 0.5, "Rage", timeline))
        self.assertTrue(player.is_enraged)
        self.assertEqual(player.rage, 0)
        self.assertTrue(any(event["kind"] == "player-enraged" for event in timeline))

        _expire_runtime_states_for_sides(player, monster, 1.5, timeline)

        self.assertFalse(player.is_enraged)
        self.assertTrue(any(event["kind"] == "player-enrage-ended" for event in timeline))

    def test_flying_action_can_trigger_started_flying_rules(self) -> None:
        flying_card = make_card("Drone", "tpl_drone", damage=0, cooldown_ms=1000)
        flying_card["raw_effects"]["abilities"]["0"]["Action"] = {
            "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardFlyingStart",
            "Target": {
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
            },
        }
        skill = make_started_flying_shield_skill("Watcher", "tpl_watcher", 10)
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[
                PlacedCard("drone", flying_card, tier="Bronze"),
                PlacedCard("watcher", skill, tier="Bronze"),
            ],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=1.1,
            rng=None,
        )

        self.assertTrue(any(event["kind"] == "item-started-flying" and event["target"] == "Drone" for event in outcome.timeline))
        self.assertTrue(any(event["kind"] == "shield" and event["source"] == "Watcher" and event["value"] == 10 for event in outcome.timeline))

    def test_rage_apply_can_trigger_enraged_rules(self) -> None:
        rage_card = make_card("Rager", "tpl_rager", damage=0, cooldown_ms=1000, action_type="TActionPlayerRageApply")
        rage_card["raw_effects"]["abilities"]["0"]["Action"]["Target"]["TargetMode"] = "Self"
        rage_card["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["RageApplyAmount"] = 50
        skill = make_enraged_shield_skill("Roar", "tpl_roar", 10)
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[
                PlacedCard("rager", rage_card, tier="Bronze"),
                PlacedCard("roar", skill, tier="Bronze"),
            ],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=1.1,
            rng=None,
            player_attributes={"RageMax": 50, "EnragedDurationMax": 1000},
        )

        self.assertTrue(any(event["kind"] == "player-enraged" for event in outcome.timeline))
        self.assertTrue(any(event["kind"] == "shield" and event["source"] == "Roar" and event["value"] == 10 for event in outcome.timeline))

    def test_heated_runtime_aura_reduces_and_restores_cooldown(self) -> None:
        heater = make_state_apply_card("Heater", "tpl_heater", "Heated", cooldown_ms=2000, duration_ms=500)
        aura = make_runtime_aura_skill("Hot Clock", "tpl_hot_clock", "FlatCooldownReduction", 1, "Heated")
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)
        self.assertEqual(read_rules(PlacedCard("heater", heater, tier="Bronze"), {"TActionCardModifyAttribute"})[0].duration_sec, 0.5)

        outcome = _simulate_two_sided_battle(
            player_cards=[
                PlacedCard("heater", heater, tier="Bronze"),
                PlacedCard("hot_clock", aura, tier="Bronze"),
            ],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=3.6,
            rng=None,
        )

        times = [round(float(event["time"]), 2) for event in outcome.timeline if event["kind"] == "item-used" and event["source"] == "Heater"]
        self.assertEqual(times[:2], [2.0, 3.5])
        self.assertTrue(any(event["kind"] == "runtime-aura-added" and event["attribute"] == "FlatCooldownReduction" for event in outcome.timeline))
        self.assertTrue(
            any(
                event["kind"] == "cooldown-modified"
                and event.get("new_effective_cooldown") == 2
                for event in outcome.timeline
            )
        )

    def test_chilled_runtime_multicast_snapshot_restores_after_state_removed(self) -> None:
        timeline: list[dict] = []
        card = make_card("Burst", "tpl_burst", damage=0, cooldown_ms=1000)
        side = _make_battle_side("player", [PlacedCard("burst", card, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        aura = PlacedCard(
            "aura",
            make_runtime_aura_skill("Cold Echo", "tpl_cold_echo", "Multicast", 2, "Chilled"),
            tier="Bronze",
        )
        side.cards.append(aura)
        scheduler = BattleEventScheduler(timeline)

        _add_runtime_state(side, side.active[0], "chilled", value=1, source_id="chill", source_label="Chill", source_side=side, now=0, duration_sec=10, timeline=timeline)
        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)
        self.assertEqual(_effective_multicast(BattleCardRef(side, side.active[0])), 3)

        _remove_runtime_state(side, side.active[0], "chilled", source_id="chill", source_label="Chill", source_side=side, now=1, timeline=timeline)
        _refresh_runtime_state_auras(side, monster, scheduler, 1.0, timeline, None)
        self.assertEqual(_effective_multicast(BattleCardRef(side, side.active[0])), 1)

    def test_enraged_runtime_ammo_max_clips_and_restore_does_not_refill(self) -> None:
        timeline: list[dict] = []
        card = make_card("Ammo", "tpl_ammo", damage=0, cooldown_ms=1000)
        card["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["AmmoMax"] = 3
        side = _make_battle_side("player", [PlacedCard("ammo", card, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        aura = PlacedCard(
            "aura",
            make_runtime_aura_skill("Tight Mag", "tpl_tight_mag", "AmmoMax", 2, "Enraged", operation="Subtract"),
            tier="Bronze",
        )
        side.cards.append(aura)
        scheduler = BattleEventScheduler(timeline)

        _add_runtime_state(side, None, "enraged", value=1, source_id="rage", source_label="Rage", source_side=side, now=0, duration_sec=10, timeline=timeline)
        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)

        self.assertEqual(side.ammo["ammo"]["max"], 1)
        self.assertEqual(side.ammo["ammo"]["current"], 1)
        self.assertTrue(any(event["kind"] == "ammo-clipped" for event in timeline))

        _remove_runtime_state(side, None, "enraged", source_id="rage", source_label="Rage", source_side=side, now=1, timeline=timeline)
        _refresh_runtime_state_auras(side, monster, scheduler, 1.0, timeline, None)

        self.assertEqual(side.ammo["ammo"]["max"], 3)
        self.assertEqual(side.ammo["ammo"]["current"], 1)

    def test_dynamic_charge_amount_keeps_charge_port_icd(self) -> None:
        timeline: list[dict] = []
        source_card = make_card("Charger", "tpl_charger", damage=0, cooldown_ms=10000, action_type="TActionCardCharge")
        source_card["raw_effects"]["abilities"]["0"]["Action"]["Target"] = {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
        }
        source_card["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["ChargeAmount"] = 1000
        player = _make_battle_side("player", [PlacedCard("charger", source_card, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        source = player.active[0]
        player.runtime_aura_bonus["ChargeAmount"][source.placement_id] = 1
        rule = read_rules(source, {"TActionCardCharge"})[0]
        scheduler = BattleEventScheduler(timeline)

        for _ in range(2):
            _apply_battle_rule(
                player,
                monster,
                player,
                monster,
                source,
                source,
                rule,
                rule.amount,
                1,
                scheduler,
                0.0,
                timeline,
                None,
                {source.placement_id: [rule]},
                10,
                {},
            )

        events = sorted(scheduler.events)
        self.assertEqual([round(event.execute_time, 2) for event in events], [0.0, 0.25])
        self.assertEqual([event.amount for event in events], [2, 2])

    def test_taction_and_expands_children_in_order(self) -> None:
        combo = make_card("Combo", "tpl_combo", damage=0, cooldown_ms=1000)
        combo["raw_effects"]["abilities"]["0"]["Action"] = {
            "$type": "BazaarGameShared.Domain.Effect.Actions.TActionAnd",
            "Actions": [
                {
                    "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerDamage",
                    "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 5},
                    "Target": {
                        "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                        "TargetMode": "Opponent",
                    },
                },
                {
                    "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerShieldApply",
                    "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 7},
                    "Target": {
                        "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                        "TargetMode": "Self",
                    },
                },
            ],
        }
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("combo", combo, tier="Bronze")],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=1.1,
            rng=None,
        )

        kinds = [event["kind"] for event in outcome.timeline if event["source"] == "Combo" and event["kind"] in {"damage", "shield", "use"}]
        self.assertIn("shield", kinds)
        self.assertTrue(any(event["kind"] in {"damage", "use"} and event["value"] == 5 for event in outcome.timeline if event["source"] == "Combo"))

    def test_runtime_tags_affect_later_trigger_conditions_without_mutating_card(self) -> None:
        tagger = make_card("Tagger", "tpl_tagger", damage=0, cooldown_ms=1000, action_type="TActionCardAddTagsList")
        tagger["raw_effects"]["abilities"]["0"]["Action"]["Tags"] = ["Weapon"]
        tagger["raw_effects"]["abilities"]["0"]["Action"]["Target"] = {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
        }
        skill = make_tagged_item_used_shield_skill("Armory", "tpl_armory", "Weapon", 9)
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[
                PlacedCard("tagger", tagger, tier="Bronze"),
                PlacedCard("armory", skill, tier="Bronze"),
            ],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=1.1,
            rng=None,
        )

        self.assertTrue(any(event["kind"] == "runtime-tags-added" and event["tags"] == ["weapon"] for event in outcome.timeline))
        self.assertTrue(any(event["kind"] == "shield" and event["source"] == "Armory" and event["value"] == 9 for event in outcome.timeline))
        self.assertEqual(tagger["tags"], [])

    def test_dynamic_cooldown_reduction_changes_next_use_time(self) -> None:
        card = make_cooldown_modifier_card("Cooler", "tpl_cooler", 0.5, operation="Subtract")
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("cooler", card, tier="Bronze")],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=2.1,
            rng=None,
        )

        times = [round(float(event["time"]), 2) for event in outcome.timeline if event["kind"] == "item-used" and event["source"] == "Cooler"]
        self.assertEqual(times[:2], [1.0, 1.5])

    def test_dynamic_cooldown_increase_changes_next_use_time(self) -> None:
        card = make_cooldown_modifier_card("Warmer", "tpl_warmer", 1.0, operation="Add")
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("warmer", card, tier="Bronze")],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=3.1,
            rng=None,
        )

        times = [round(float(event["time"]), 2) for event in outcome.timeline if event["kind"] == "item-used" and event["source"] == "Warmer"]
        self.assertEqual(times[:2], [1.0, 3.0])

    def test_temporary_cooldown_modifier_restores_effective_cooldown(self) -> None:
        timeline: list[dict] = []
        scheduler, side, target, source = self._charge_scheduler_target(cooldown_ms=1000)
        card = make_cooldown_modifier_card("Temp", "tpl_temp", 0.5, operation="Subtract", duration_ms=500)
        rule = [
            rule
            for rule in read_rules(PlacedCard("source", card, tier="Bronze"), {"TActionCardModifyAttribute"})
            if rule.action_type == "TActionCardModifyAttribute"
        ][0]

        _add_cooldown_modifier(target, side, source, rule, 0.5, 0.0, scheduler, timeline, 10)
        event = scheduler.pop_due(0.5)
        self.assertEqual(target.side.cooldowns[target.card.placement_id].effective_cooldown, 0.5)
        self.assertIsNotNone(event)
        if event is not None:
            _expire_cooldown_modifier(event, scheduler, 0.5, timeline)

        self.assertEqual(target.side.cooldowns[target.card.placement_id].effective_cooldown, 1.0)

    def test_multicast_interval_override_is_respected(self) -> None:
        multi = make_card("Slow Multi", "tpl_slow_multi", damage=0, cooldown_ms=1000)
        attrs = multi["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]
        attrs["Multicast"] = 3
        attrs["MulticastInterval"] = 500
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("multi", multi, tier="Bronze")],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=2.5,
            rng=None,
        )

        times = [round(float(event["time"]), 2) for event in outcome.timeline if event["kind"] == "item-used" and event["source"] == "Slow Multi"]
        self.assertEqual(times[:3], [1.0, 1.5, 2.0])

    def test_trigger_counter_limits_once_per_combat(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=0, cooldown_ms=1000)
        skill = make_limited_item_used_shield_skill("Once", "tpl_once", limit=1, scope="combat")
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("weapon", weapon, tier="Bronze"), PlacedCard("skill", skill, tier="Bronze")],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=3.2,
            rng=None,
        )

        shield_events = [event for event in outcome.timeline if event["kind"] == "shield" and event["source"] == "Once"]
        self.assertEqual(len(shield_events), 1)
        self.assertTrue(any(event["kind"] == "trigger-blocked" for event in outcome.timeline))

    def test_trigger_counter_can_be_independent_per_source_item(self) -> None:
        weapon_a = make_card("A", "tpl_a", damage=0, cooldown_ms=1000)
        weapon_b = make_card("B", "tpl_b", damage=0, cooldown_ms=1000)
        skill = make_limited_item_used_shield_skill("Per Item", "tpl_per_item", limit=1, scope="source_item")
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[
                PlacedCard("a", weapon_a, start=0, tier="Bronze"),
                PlacedCard("b", weapon_b, start=1, tier="Bronze"),
                PlacedCard("skill", skill, start=100, tier="Bronze"),
            ],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=1.2,
            rng=None,
        )

        shield_events = [event for event in outcome.timeline if event["kind"] == "shield" and event["source"] == "Per Item"]
        self.assertEqual(len(shield_events), 2)

    def test_multicast_each_actual_use_enters_trigger_counter(self) -> None:
        multi = make_card("Multi", "tpl_multi", damage=0, cooldown_ms=1000)
        multi["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["Multicast"] = 3
        skill = make_limited_item_used_shield_skill("Twice", "tpl_twice", limit=2, scope="combat")
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("multi", multi, tier="Bronze"), PlacedCard("skill", skill, tier="Bronze")],
            monster_cards=[PlacedCard("monster", monster, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=2,
            rng=None,
        )

        shield_events = [event for event in outcome.timeline if event["kind"] == "shield" and event["source"] == "Twice"]
        self.assertEqual(len(shield_events), 2)
        self.assertTrue(any(event["kind"] == "trigger-blocked" for event in outcome.timeline))

    def test_half_health_condition_only_enters_on_threshold_crossing(self) -> None:
        timeline: list[dict] = []
        attacker = BattleSide("monster", [], health=100, max_health=100)
        player = BattleSide("player", [], health=100, max_health=100)

        _apply_damage(attacker, player, 40, 1.0, "Hit", "damage", timeline)
        _apply_damage(attacker, player, 10, 2.0, "Hit", "damage", timeline)
        _apply_damage(attacker, player, 5, 3.0, "Hit", "damage", timeline)

        entered = [event for event in timeline if event["kind"] == "condition-entered" and event["condition"] == "below_half_health"]
        self.assertEqual(len(entered), 1)
        self.assertEqual(entered[0]["time"], 2.0)

    def test_max_health_change_can_move_half_health_threshold(self) -> None:
        timeline: list[dict] = []
        source = PlacedCard("source", make_max_health_card("Max", "tpl_max", 100), tier="Bronze")
        rule = read_rules(source, {"TActionPlayerModifyAttribute"})[0]
        side = BattleSide("player", [], health=60, max_health=100, base_max_health=100)

        _apply_max_health_modifier(side, side, source, rule, 100, 1.0, timeline)

        self.assertEqual(side.max_health, 200)
        self.assertTrue(
            any(event["kind"] == "condition-entered" and event["condition"] == "below_half_health" for event in timeline)
        )

    def test_would_die_precedes_revive_and_battle_continues(self) -> None:
        timeline: list[dict] = []
        attacker = BattleSide("monster", [], health=100, max_health=100)
        player = BattleSide(
            "player",
            [],
            health=100,
            max_health=100,
            revives=[{"value": 50, "mode": "health"}],
        )

        _apply_damage(attacker, player, 120, 1.0, "Hit", "damage", timeline)

        kinds = [event["kind"] for event in timeline]
        self.assertLess(kinds.index("would-die"), kinds.index("revive"))
        self.assertEqual(player.health, 50)

    def test_revive_pool_exhaustion_records_death(self) -> None:
        timeline: list[dict] = []
        attacker = BattleSide("monster", [], health=100, max_health=100)
        player = BattleSide("player", [], health=100, max_health=100)

        _apply_damage(attacker, player, 120, 1.0, "Hit", "damage", timeline)

        self.assertTrue(any(event["kind"] == "would-die" for event in timeline))
        self.assertTrue(any(event["kind"] == "player-died" for event in timeline))

    def test_player_stable_win(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=60),
                "Monster": make_card("Monster", "tpl_monster", damage=10),
            }
        }

        response = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Weak Monster"}],
            simulations=5,
            duration_sec=20,
        )

        result = response["results"][0]
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["estimated_win_rate"], 1.0)
        self.assertEqual(result["wins"], 5)
        self.assertEqual(response["simulator_model"], "two_sided_timeline")
        self.assertEqual(result["battle_model"], "two_sided_timeline")

    def test_player_stable_loss(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=10),
                "Monster": make_card("Monster", "tpl_monster", damage=60),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Strong Monster"}],
            simulations=5,
            duration_sec=20,
        )["results"][0]

        self.assertEqual(result["estimated_win_rate"], 0.0)
        self.assertEqual(result["losses"], 5)

    def test_two_sided_control_delays_opponent_items(self) -> None:
        data = {
            "cards": {
                "Player": make_freeze_card("Player", "tpl_player", damage=25, freeze_sec=5, cooldown_ms=4000),
                "Monster": make_card("Monster", "tpl_monster", damage=60, cooldown_ms=5000),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=3,
            duration_sec=25,
        )["results"][0]

        self.assertEqual(result["estimated_win_rate"], 1.0)
        self.assertTrue(
            any(
                event["kind"] == "freeze" and event["target_side"] == "monster"
                for event in result["sample_timeline"]
            )
        )

    def test_supported_skill_aura_can_change_outcome(self) -> None:
        player = make_card("Player", "tpl_player", damage=20)
        player["tags"] = ["Weapon"]
        data = {
            "cards": {
                "Player": player,
                "Monster": make_card("Monster", "tpl_monster", damage=60),
                "Skill": make_aura_skill("Skill", "tpl_skill", "DamageAmount", 90),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with(
                "tpl_player",
                health=100,
                skills=[{"id": "skill_1", "template_id": "tpl_skill", "rarity": "Diamond"}],
            ),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=3,
            duration_sec=20,
        )["results"][0]

        self.assertEqual(result["estimated_win_rate"], 1.0)
        self.assertEqual(result["unsupported_skills"], [])

    def test_damage_reduction_can_prevent_lethal_hit(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=50),
                "Invuln": make_damage_reduction_card("Invuln", "tpl_invuln"),
                "Monster": make_card("Monster", "tpl_monster", damage=100),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state={
                "combat_health": 100,
                "board_items": [
                    {"id": "itm_player", "template_id": "tpl_player", "rarity": "Bronze", "section": "Hand", "position": 0},
                    {"id": "itm_invuln", "template_id": "tpl_invuln", "rarity": "Bronze", "section": "Hand", "position": 1},
                ],
            },
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=3,
            duration_sec=20,
        )["results"][0]

        self.assertEqual(result["estimated_win_rate"], 1.0)
        self.assertIn("player_damage_reduction_is_approximated", result["warnings"])

    def test_revive_can_absorb_first_lethal_hit(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=100, cooldown_ms=6000),
                "Monster": make_card("Monster", "tpl_monster", damage=120),
                "Revive": make_revive_skill("Revive", "tpl_revive"),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with(
                "tpl_player",
                health=100,
                skills=[{"id": "skill_1", "template_id": "tpl_revive", "rarity": "Diamond"}],
            ),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=3,
            duration_sec=12,
        )["results"][0]

        self.assertEqual(result["estimated_win_rate"], 1.0)
        self.assertIn("player_revive_is_approximated", result["warnings"])

    def test_player_shield_can_change_damage_race_outcome(self) -> None:
        shield_player = make_card("Shield Player", "tpl_player", damage=20)
        shield_player["raw_effects"]["abilities"]["1"] = {
            "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
            "Trigger": {"$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardFired"},
            "Action": {
                "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerShieldApply",
                "Target": {
                    "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                    "TargetMode": "Player",
                },
            },
        }
        shield_player["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["ShieldApplyAmount"] = 100
        data = {
            "cards": {
                "Player": shield_player,
                "Monster": make_card("Monster", "tpl_monster", damage=60),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=3,
            duration_sec=30,
        )["results"][0]

        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["estimated_win_rate"], 1.0)
        self.assertGreater(result["average_remaining_health_on_win"], 0)

    def test_random_effect_can_run_multiple_simulations(self) -> None:
        random_card = make_card(
            "Random Freezer",
            "tpl_random",
            damage=20,
            trigger_type="TTriggerOnCardFired",
        )
        random_card["raw_effects"]["abilities"]["1"] = {
            "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
            "Trigger": {"$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardFired"},
            "Action": {
                "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardFreeze",
                "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 1},
                "Target": {
                    "$type": "BazaarGameShared.Domain.Targeting.TTargetCardRandom",
                    "TargetCount": 1,
                },
            },
        }
        data = {
            "cards": {
                "Random": random_card,
                "Monster": make_card("Monster", "tpl_monster", damage=10),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_random", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=20,
            duration_sec=20,
            seed=42,
        )["results"][0]

        self.assertEqual(result["simulations_completed"], 20)
        self.assertIn(result["confidence"], {"high", "medium"})

    def test_unsupported_card_is_reported(self) -> None:
        data = {"cards": {"Player": make_card("Player", "tpl_player", damage=20)}}

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("missing_tpl", health=100), "name": "Unknown"}],
            simulations=5,
        )["results"][0]

        self.assertEqual(result["confidence"], "low")
        self.assertTrue(result["unsupported_cards"])
        self.assertIsNone(result["estimated_win_rate"])

    def test_unsupported_skill_is_reported(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=60),
                "Monster": make_card("Monster", "tpl_monster", damage=10),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100, skills=[{"name": "Skill"}]),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=5,
        )["results"][0]

        self.assertEqual(result["confidence"], "low")
        self.assertTrue(result["unsupported_skills"])
        self.assertIsNone(result["estimated_win_rate"])

    def test_missing_input_data_is_low_confidence(self) -> None:
        data = {"cards": {"Player": make_card("Player", "tpl_player", damage=20)}}

        result = evaluate_monster_choices(
            data=data,
            player_state={"board_items": []},
            monster_choices=[{"name": "Incomplete Monster"}],
            simulations=5,
        )["results"][0]

        self.assertEqual(result["status"], "unsupported")
        self.assertEqual(result["confidence"], "low")
        self.assertIsNone(result["estimated_win_rate"])

    def test_single_simulation_exception_is_error_result(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=20),
                "Monster": make_card("Monster", "tpl_monster", damage=20),
            }
        }

        def broken_simulator(*args, **kwargs):
            raise RuntimeError("boom")

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=1,
            simulate_fn=broken_simulator,
        )["results"][0]

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["simulations_completed"], 0)

    def test_partial_simulation_failures_are_reported(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=60),
                "Monster": make_card("Monster", "tpl_monster", damage=10),
            }
        }
        calls = {"count": 0}

        from combat_simulator import simulate_combat

        def flaky_simulator(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("first trial failed")
            return simulate_combat(*args, **kwargs)

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=3,
            simulate_fn=flaky_simulator,
        )["results"][0]

        self.assertEqual(result["simulations_completed"], 2)
        self.assertEqual(result["simulations_failed"], 1)
        self.assertEqual(result["confidence"], "medium")

    def test_three_monsters_are_evaluated_separately(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=30),
                "A": make_card("A", "tpl_a", damage=10),
                "B": make_card("B", "tpl_b", damage=20),
                "C": make_card("C", "tpl_c", damage=40),
            }
        }

        response = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[
                {**state_with("tpl_a", health=100), "name": "A"},
                {**state_with("tpl_b", health=100), "name": "B"},
                {**state_with("tpl_c", health=100), "name": "C"},
            ],
            simulations=3,
        )

        self.assertEqual([item["monster_name"] for item in response["results"]], ["A", "B", "C"])

    def test_same_input_uses_cache(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=20),
                "Monster": make_card("Monster", "tpl_monster", damage=20),
            }
        }
        kwargs = {
            "data": data,
            "player_state": state_with("tpl_player", health=100),
            "monster_choices": [{**state_with("tpl_monster", health=100), "name": "Monster"}],
            "simulations": 2,
        }

        first = evaluate_monster_choices(**kwargs)
        second = evaluate_monster_choices(**kwargs)

        self.assertFalse(first["cache"]["hit"])
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(first["cache"]["key"], second["cache"]["key"])

    def test_state_change_invalidates_cache(self) -> None:
        data = {
            "cards": {
                "Player": make_card("Player", "tpl_player", damage=20),
                "Monster": make_card("Monster", "tpl_monster", damage=20),
            }
        }

        first = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=2,
        )
        second = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=90),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=2,
        )

        self.assertNotEqual(first["cache"]["key"], second["cache"]["key"])
        self.assertFalse(second["cache"]["hit"])

    def test_low_confidence_does_not_emit_fake_precise_win_rate(self) -> None:
        data = {
            "cards": {
                "Player": make_card(
                    "Player",
                    "tpl_player",
                    damage=50,
                    action_type="TActionPlayerLifestealApply",
                ),
                "Monster": make_card("Monster", "tpl_monster", damage=10),
            }
        }

        result = evaluate_monster_choices(
            data=data,
            player_state=state_with("tpl_player", health=100),
            monster_choices=[{**state_with("tpl_monster", health=100), "name": "Monster"}],
            simulations=5,
        )["results"][0]

        self.assertEqual(result["confidence"], "low")
        self.assertTrue(result["unsupported_effects"])
        self.assertIsNone(result["estimated_win_rate"])
        self.assertIsNone(result["win_rate_range"])


if __name__ == "__main__":
    unittest.main()
