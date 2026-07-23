from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from combat_simulator import HealCleanseConfig, PlacedCard, build_current_board_placements, card_tags, read_rules  # noqa: E402
from monster_battle_evaluator import (  # noqa: E402
    BattleCardRef,
    BattleEventScheduler,
    BattleSide,
    SandstormConfig,
    SandstormState,
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
    _fire_battle_card,
    _apply_rage,
    _apply_max_health_modifier,
    cleanse_combat_status,
    destroy_card,
    _has_runtime_state,
    _make_battle_side,
    _process_burn_tick,
    _process_sandstorm_tick,
    _process_second_tick,
    _refresh_runtime_state_auras,
    _remove_runtime_state,
    _remove_runtime_tags,
    _resolve_charge_event,
    _runtime_aura_tags,
    _sandstorm_tick_damage,
    _simulate_two_sided_battle,
    _start_sandstorm,
    repair_card,
    transform_card_instance,
    transform_destroyed_card,
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


def make_heal_card(name: str, template_id: str, *, heal: float, cooldown_ms: int = 1000) -> dict:
    card = make_card(name, template_id, damage=heal, cooldown_ms=cooldown_ms, action_type="TActionPlayerHeal")
    card["raw_effects"]["abilities"]["0"]["Action"]["Target"]["TargetMode"] = "Player"
    return card


def make_transform_destroyed_card(
    name: str,
    template_id: str,
    *,
    target_type: str = "TTargetCardSelf",
    trigger_type: str = "TTriggerOnCardDisabled",
    spawn_id: str | None = None,
    query_constraints: list[dict] | None = None,
    inherit_tier: bool = True,
) -> dict:
    if spawn_id:
        filter_node = {
            "$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.TSpawnFilterIdList",
            "Ids": [spawn_id],
        }
    else:
        filter_node = {
            "$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.TSpawnFilterQuery",
            "Constraints": {
                "$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.Constraints.ConstraintAnd",
                "Constraints": query_constraints or [],
            },
        }
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Item",
        "size": "Small",
        "tags": [],
        "hidden_tags": [],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": f"BazaarGameShared.Domain.Effect.Trigger.{trigger_type}"
                    },
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardTransformDestroyed",
                        "SpawnContext": {
                            "$type": "BazaarGameShared.Domain.Spawning.SpawningContexts.TSpawnContextQuery",
                            "Groups": [
                                {
                                    "$type": "BazaarGameShared.Domain.Spawning.SpawnGroups.TSpawnGroup",
                                    "Filters": [filter_node],
                                    "RandomWeight": 0,
                                }
                            ],
                            "Behaviors": [
                                {
                                    "$type": "BazaarGameShared.Domain.Spawning.SpawnBehaviors.TSpawnBehaviorInheritTier",
                                    "Inherits": inherit_tier,
                                }
                            ],
                        },
                        "Target": {
                            "$type": f"BazaarGameShared.Domain.Targeting.{target_type}",
                        },
                    },
                }
            },
            "tiers_raw": {"Bronze": {"Attributes": {"CooldownMax": 1000}}},
        },
    }


def make_transform_card(
    name: str,
    template_id: str,
    *,
    target_type: str = "TTargetCardSelf",
    trigger_type: str = "TTriggerOnCardFired",
    spawn_id: str | None = None,
    query_constraints: list[dict] | None = None,
    filter_target: dict | None = None,
    inherit_tier: bool = True,
) -> dict:
    if filter_target:
        filter_node = {
            "$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.TSpawnFilterTarget",
            "Target": filter_target,
        }
    elif spawn_id:
        filter_node = {
            "$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.TSpawnFilterIdList",
            "Ids": [spawn_id],
        }
    else:
        filter_node = {
            "$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.TSpawnFilterQuery",
            "Constraints": {
                "$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.Constraints.ConstraintAnd",
                "Constraints": query_constraints or [],
            },
        }
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Item",
        "size": "Small",
        "tags": [],
        "hidden_tags": [],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": f"BazaarGameShared.Domain.Effect.Trigger.{trigger_type}"
                    },
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardTransform",
                        "SpawnContext": {
                            "$type": "BazaarGameShared.Domain.Spawning.SpawningContexts.TSpawnContextQuery",
                            "Groups": [
                                {
                                    "$type": "BazaarGameShared.Domain.Spawning.SpawnGroups.TSpawnGroup",
                                    "Filters": [filter_node],
                                    "RandomWeight": 0,
                                }
                            ],
                            "Behaviors": [
                                {
                                    "$type": "BazaarGameShared.Domain.Spawning.SpawnBehaviors.TSpawnBehaviorInheritTier",
                                    "Inherits": inherit_tier,
                                }
                            ],
                        },
                        "Target": {
                            "$type": f"BazaarGameShared.Domain.Targeting.{target_type}",
                        },
                    },
                }
            },
            "tiers_raw": {"Bronze": {"Attributes": {"CooldownMax": 1000}}},
        },
    }


def make_card_transformed_listener(
    name: str,
    template_id: str,
    *,
    action_type: str = "TActionPlayerShieldApply",
    action_amount: float = 5,
    subject_type: str = "TTargetCardSection",
    subject_section: str = "SelfHand",
    include_tags: list[str] | None = None,
) -> dict:
    subject: dict = {
        "$type": f"BazaarGameShared.Domain.Targeting.{subject_type}",
        "TargetSection": subject_section,
        "ExcludeSelf": False,
    }
    if include_tags:
        subject["Conditions"] = {
            "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
            "Tags": include_tags,
            "Operator": "Any",
        }
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Item",
        "size": "Small",
        "tags": [],
        "hidden_tags": [],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardTransformed",
                        "Subject": subject,
                    },
                    "Action": {
                        "$type": f"BazaarGameShared.Domain.Effect.Actions.{action_type}",
                        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": action_amount},
                        "Target": {"$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative"},
                    },
                }
            },
            "tiers_raw": {"Bronze": {"Attributes": {"CooldownMax": 1000}}},
        },
    }


def make_overheal_listener(
    name: str,
    template_id: str,
    *,
    action_type: str = "TActionPlayerShieldApply",
    action_amount: float = 5,
    subject_type: str = "TTargetCardSection",
    subject_section: str = "SelfBoard",
    action_target_type: str = "TTargetPlayerRelative",
    action_target_mode: str = "",
    include_tags: list[str] | None = None,
    max_triggers: int | None = None,
) -> dict:
    subject: dict = {
        "$type": f"BazaarGameShared.Domain.Targeting.{subject_type}",
        "TargetSection": subject_section,
        "ExcludeSelf": False,
    }
    if include_tags:
        subject["Conditions"] = {
            "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
            "Tags": include_tags,
            "Operator": "Any",
        }
    target: dict = {"$type": f"BazaarGameShared.Domain.Targeting.{action_target_type}"}
    if action_target_mode:
        target["TargetMode"] = action_target_mode
    trigger: dict = {
        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardPerformedOverHeal",
        "Subject": subject,
    }
    if max_triggers is not None:
        trigger["MaxTriggers"] = max_triggers
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Item",
        "size": "Small",
        "tags": [],
        "hidden_tags": [],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": trigger,
                    "Action": {
                        "$type": f"BazaarGameShared.Domain.Effect.Actions.{action_type}",
                        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": action_amount},
                        "Target": target,
                    },
                }
            },
            "tiers_raw": {"Bronze": {"Attributes": {"CooldownMax": 1000}}},
        },
    }


def make_attribute_changed_card(
    name: str,
    template_id: str,
    *,
    listened_attribute: str,
    change_type: str = "Gain",
    subject_type: str = "TTargetCardSelf",
    subject_section: str = "",
    subject_exclude_self: bool = False,
    action_type: str = "TActionCardCharge",
    action_attribute: str = "",
    action_amount: float = 0,
    action_target_type: str = "TTargetCardSelf",
    trigger_condition: dict | None = None,
) -> dict:
    action: dict = {
        "$type": f"BazaarGameShared.Domain.Effect.Actions.{action_type}",
        "Target": {
            "$type": f"BazaarGameShared.Domain.Targeting.{action_target_type}",
            "ExcludeSelf": False,
        },
    }
    if action_type == "TActionCardModifyAttribute":
        action.update(
            {
                "AttributeType": action_attribute,
                "Operation": "Add",
                "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": action_amount},
            }
        )
    subject: dict = {
        "$type": f"BazaarGameShared.Domain.Targeting.{subject_type}",
        "ExcludeSelf": subject_exclude_self,
    }
    if subject_section:
        subject["TargetSection"] = subject_section
    if trigger_condition is not None:
        subject["Conditions"] = trigger_condition
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": "Item",
        "size": "Small",
        "tags": [],
        "hidden_tags": [],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
                    "Trigger": {
                        "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnCardAttributeChanged",
                        "Subject": subject,
                        "AttributeChanged": listened_attribute,
                        "ChangeType": change_type,
                    },
                    "Action": action,
                }
            },
            "tiers_raw": {
                "Bronze": {
                    "Attributes": {
                        "ChargeAmount": action_amount,
                        "DamageAmount": action_amount,
                        "ShieldApplyAmount": action_amount,
                        "CooldownMax": 1000,
                    }
                }
            },
        },
    }


def make_card_attribute_modifier(
    name: str,
    template_id: str,
    *,
    attribute: str,
    amount: float,
    target_type: str = "TTargetCardSection",
    target_section: str = "SelfHand",
    target_mode: str = "",
    trigger_type: str = "TTriggerOnCardFired",
) -> dict:
    card = make_card(name, template_id, damage=0, action_type="TActionCardModifyAttribute", trigger_type=trigger_type)
    target: dict = {"$type": f"BazaarGameShared.Domain.Targeting.{target_type}"}
    if target_section:
        target["TargetSection"] = target_section
    if target_mode:
        target["TargetMode"] = target_mode
    card["raw_effects"]["abilities"]["0"]["Action"] = {
        "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardModifyAttribute",
        "AttributeType": attribute,
        "Operation": "Add",
        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": amount},
        "Target": target,
    }
    return card


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


def make_tag_aura_card(
    name: str,
    template_id: str,
    *,
    tags: list[str] | None = None,
    target: dict | None = None,
    source_selector: dict | None = None,
    action_type: str = "TAuraActionCardAddTagsList",
    card_type: str = "Item",
    base_tags: list[str] | None = None,
) -> dict:
    action = {
        "$type": f"BazaarGameShared.Domain.Effect.AuraActions.{action_type}",
        "Target": target
        or {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
        },
    }
    if tags is not None:
        action["Tags"] = tags
    if source_selector is not None:
        action["Source"] = source_selector
    return {
        "id": template_id,
        "template_id": template_id,
        "name": name,
        "type": card_type,
        "size": "Small",
        "tags": base_tags or [],
        "hidden_tags": [],
        "tiers": ["Bronze"],
        "rarity": "Bronze",
        "raw_effects": {
            "abilities": {},
            "auras": {
                "0": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAura",
                    "Action": action,
                }
            },
            "tiers_raw": {
                "Bronze": {
                    "Attributes": {"CooldownMax": 0},
                    "AbilityIds": [],
                    "AuraIds": ["0"],
                }
            },
        },
    }


def make_status_cleanse_card(
    name: str,
    template_id: str,
    *,
    status: str = "burn",
    ratio: float = 0.5,
    cooldown_ms: int = 1000,
    trigger_type: str = "TTriggerOnCardFired",
) -> dict:
    status_label = "Burn" if status == "burn" else "Poison"
    action_type = f"TActionPlayer{status_label}Remove"
    attr_type = f"{status_label}RemoveAmount"
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
                    "Trigger": {"$type": f"BazaarGameShared.Domain.Effect.Trigger.{trigger_type}"},
                    "Action": {
                        "$type": f"BazaarGameShared.Domain.Effect.Actions.{action_type}",
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                            "TargetMode": "Self",
                        },
                    },
                }
            },
            "auras": {
                "1": {
                    "$type": "BazaarGameShared.Domain.Effect.TCardAura",
                    "Action": {
                        "$type": "BazaarGameShared.Domain.Effect.AuraActions.TAuraActionCardModifyAttribute",
                        "AttributeType": attr_type,
                        "Operation": "Add",
                        "Value": {
                            "$type": "BazaarGameShared.Domain.Values.ReferenceValues.TReferenceValuePlayerAttribute",
                            "AttributeType": status_label,
                            "Target": {
                                "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                                "TargetMode": "Self",
                            },
                            "DefaultValue": 0,
                            "Modifier": {
                                "$type": "BazaarGameShared.Domain.Values.TValueModifier",
                                "ModifyMode": "Multiply",
                                "Value": {
                                    "$type": "BazaarGameShared.Domain.Values.TFixedValue",
                                    "Value": ratio,
                                },
                                "ShouldRound": True,
                            },
                        },
                        "Target": {
                            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
                        },
                    },
                }
            },
            "tiers_raw": {
                "Bronze": {
                    "Attributes": {
                        "CooldownMax": cooldown_ms,
                        attr_type: 0,
                    },
                    "AbilityIds": ["0"],
                    "AuraIds": ["1"],
                }
            },
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

    def test_performed_overheal_trigger_fires_with_complete_heal_result(self) -> None:
        healer = make_heal_card("Healer", "tpl_healer", heal=30)
        listener = make_overheal_listener("Healthy Jolt", "tpl_jolt", action_amount=7)
        side = _make_battle_side("player", [PlacedCard("healer", healer, tier="Bronze"), PlacedCard("listener", listener, start=1, tier="Bronze")], 80, 10)
        side.max_health = 100
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _fire_battle_card(BattleCardRef(side, side.cards[0]), side, monster, rules, BattleEventScheduler(timeline), 1.0, timeline, None, forced=False)

        self.assertEqual(side.health, 100)
        self.assertEqual(side.shield, 7)
        overheal_events = [event for event in timeline if event["kind"] == "CARD_PERFORMED_OVERHEAL"]
        self.assertEqual(len(overheal_events), 1)
        event = overheal_events[0]
        self.assertEqual(event["requested_heal"], 30)
        self.assertEqual(event["effective_heal"], 20)
        self.assertEqual(event["overheal"], 10)
        self.assertEqual(event["health_before"], 80)
        self.assertEqual(event["health_after"], 100)
        self.assertEqual(event["max_health"], 100)
        self.assertEqual(event["source_card"], "Healer")
        self.assertEqual(event["target_player"], "player")

    def test_performed_overheal_trigger_skips_when_no_overheal_or_zero_heal(self) -> None:
        healer = make_heal_card("Healer", "tpl_healer", heal=30)
        zero_healer = make_heal_card("Zero Healer", "tpl_zero", heal=0)
        listener = make_overheal_listener("Healthy Jolt", "tpl_jolt", action_amount=7)
        side = _make_battle_side(
            "player",
            [PlacedCard("healer", healer, tier="Bronze"), PlacedCard("zero", zero_healer, start=1, tier="Bronze"), PlacedCard("listener", listener, start=2, tier="Bronze")],
            50,
            10,
        )
        side.max_health = 100
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _fire_battle_card(BattleCardRef(side, side.cards[0]), side, monster, rules, BattleEventScheduler(timeline), 1.0, timeline, None, forced=False)
        _fire_battle_card(BattleCardRef(side, side.cards[1]), side, monster, rules, BattleEventScheduler(timeline), 2.0, timeline, None, forced=False)

        self.assertEqual(side.shield, 0)
        self.assertFalse(any(event["kind"] == "CARD_PERFORMED_OVERHEAL" for event in timeline))

    def test_full_health_performed_overheal_triggers_even_with_no_effective_heal(self) -> None:
        healer = make_heal_card("Healer", "tpl_healer", heal=20)
        listener = make_overheal_listener("Healthy Jolt", "tpl_jolt", action_amount=4)
        side = _make_battle_side("player", [PlacedCard("healer", healer, tier="Bronze"), PlacedCard("listener", listener, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _fire_battle_card(BattleCardRef(side, side.cards[0]), side, monster, rules, BattleEventScheduler(timeline), 1.0, timeline, None, forced=False)

        self.assertEqual(side.shield, 4)
        event = [event for event in timeline if event["kind"] == "CARD_PERFORMED_OVERHEAL"][0]
        self.assertEqual(event["requested_heal"], 20)
        self.assertEqual(event["effective_heal"], 0)
        self.assertEqual(event["overheal"], 20)

    def test_overheal_subject_self_filters_to_healing_source(self) -> None:
        self_healer = make_heal_card("Self Healer", "tpl_self", heal=20)
        self_healer["raw_effects"]["abilities"]["1"] = make_overheal_listener(
            "Self Healer",
            "tpl_self_listener",
            subject_type="TTargetCardSelf",
            action_amount=3,
        )["raw_effects"]["abilities"]["0"]
        other_healer = make_heal_card("Other Healer", "tpl_other", heal=20)
        side = _make_battle_side("player", [PlacedCard("self", self_healer, tier="Bronze"), PlacedCard("other", other_healer, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _fire_battle_card(BattleCardRef(side, side.cards[1]), side, monster, rules, BattleEventScheduler(timeline), 1.0, timeline, None, forced=False)
        self.assertEqual(side.shield, 0)
        _fire_battle_card(BattleCardRef(side, side.cards[0]), side, monster, rules, BattleEventScheduler(timeline), 2.0, timeline, None, forced=False)
        self.assertEqual(side.shield, 3)

    def test_overheal_trigger_source_targets_healing_card(self) -> None:
        healer = make_heal_card("Healer", "tpl_healer", heal=20, cooldown_ms=10000)
        listener = make_overheal_listener(
            "Jolt",
            "tpl_jolt",
            action_type="TActionCardCharge",
            action_amount=2,
            action_target_type="TTargetCardTriggerSource",
        )
        side = _make_battle_side("player", [PlacedCard("healer", healer, tier="Bronze"), PlacedCard("listener", listener, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        scheduler = BattleEventScheduler(timeline)
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _fire_battle_card(BattleCardRef(side, side.cards[0]), side, monster, rules, scheduler, 1.0, timeline, None, forced=False)

        self.assertTrue(any(event.kind == "CHARGE_RESOLVED" and event.ref is not None and event.ref.card.placement_id == "healer" for event in scheduler.events))

    def test_overheal_and_heal_cleanse_share_one_overheal_event(self) -> None:
        healer = make_heal_card("Healer", "tpl_healer", heal=40)
        listener = make_overheal_listener("Healthy Jolt", "tpl_jolt", action_amount=6)
        side = _make_battle_side("player", [PlacedCard("healer", healer, tier="Bronze"), PlacedCard("listener", listener, start=1, tier="Bronze")], 90, 10)
        side.max_health = 100
        side.burn_stack = 100
        side.poison_stack = 60
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _fire_battle_card(BattleCardRef(side, side.cards[0]), side, monster, rules, BattleEventScheduler(timeline), 1.0, timeline, None, forced=False)

        self.assertEqual(side.health, 100)
        self.assertEqual(side.burn_stack, 90)
        self.assertEqual(side.poison_stack, 54)
        self.assertEqual(side.shield, 6)
        self.assertEqual(len([event for event in timeline if event["kind"] == "CARD_PERFORMED_OVERHEAL"]), 1)
        self.assertEqual([event for event in timeline if event["kind"] == "CARD_PERFORMED_OVERHEAL"][0]["overheal"], 30)

    def test_overheal_recursive_heal_is_depth_limited(self) -> None:
        healer = make_heal_card("Healer", "tpl_healer", heal=10)
        listener = make_overheal_listener("Loop Heal", "tpl_loop", action_type="TActionPlayerHeal", action_amount=1)
        side = _make_battle_side("player", [PlacedCard("healer", healer, tier="Bronze"), PlacedCard("loop", listener, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _fire_battle_card(BattleCardRef(side, side.cards[0]), side, monster, rules, BattleEventScheduler(timeline), 1.0, timeline, None, forced=False)

        self.assertTrue(any(event["kind"] == "trigger-depth-limited" and event.get("trigger") == "TTriggerOnCardPerformedOverHeal" for event in timeline))
        self.assertGreaterEqual(len([event for event in timeline if event["kind"] == "CARD_PERFORMED_OVERHEAL"]), 2)

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

    def test_explicit_burn_cleanse_fixed_amount_caps_at_current_stack(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=100, max_health=100, burn_stack=8, poison_stack=30)

        removed = cleanse_combat_status(side, "burn", 20, 1.0, "Cleanser", timeline)

        self.assertEqual(removed, 8)
        self.assertEqual(side.burn_stack, 0)
        self.assertEqual(side.poison_stack, 30)
        event = [item for item in timeline if item["kind"] == "burn-cleansed"][0]
        self.assertEqual(event["requested_amount"], 20)
        self.assertEqual(event["removed_amount"], 8)

    def test_explicit_poison_cleanse_fixed_amount_and_zero_stack_skip(self) -> None:
        timeline: list[dict] = []
        side = BattleSide("player", [], health=100, max_health=100, burn_stack=40, poison_stack=30)

        removed = cleanse_combat_status(side, "poison", 12, 1.0, "Cleanser", timeline)
        zero_removed = cleanse_combat_status(side, "poison", 50, 1.1, "Cleanser", timeline)

        self.assertEqual(removed, 12)
        self.assertEqual(zero_removed, 18)
        self.assertEqual(side.burn_stack, 40)
        self.assertEqual(side.poison_stack, 0)
        self.assertEqual([item["removed_amount"] for item in timeline if item["kind"] == "poison-cleansed"], [12, 18])
        self.assertEqual(cleanse_combat_status(side, "poison", 5, 1.2, "Cleanser", timeline), 0)
        self.assertEqual(len([item for item in timeline if item["kind"] == "poison-cleansed"]), 2)

    def test_status_cleanse_remove_action_uses_dynamic_burn_remove_amount(self) -> None:
        timeline: list[dict] = []
        card = make_status_cleanse_card("Coolant", "tpl_coolant", status="burn", ratio=0.5)
        side = _make_battle_side("player", [PlacedCard("coolant", card, tier="Bronze")], 100, 10)
        side.burn_stack = 40
        side.poison_stack = 30
        monster = BattleSide("monster", [], health=100, max_health=100)
        scheduler = BattleEventScheduler(timeline)
        rules = {item.placement_id: read_rules(item) for item in side.cards}

        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)
        _apply_battle_rule(side, monster, side, monster, side.cards[0], side.cards[0], rules["coolant"][0], 0, 1, scheduler, 0.0, timeline, None, rules, 10, {})

        self.assertEqual(side.burn_stack, 20)
        self.assertEqual(side.poison_stack, 30)
        event = [item for item in timeline if item["kind"] == "burn-cleansed"][0]
        self.assertEqual(event["removed_amount"], 20)

    def test_status_cleanse_remove_action_uses_dynamic_poison_remove_amount(self) -> None:
        timeline: list[dict] = []
        card = make_status_cleanse_card("Purge", "tpl_purge", status="poison", ratio=0.5)
        side = _make_battle_side("player", [PlacedCard("purge", card, tier="Bronze")], 100, 10)
        side.burn_stack = 40
        side.poison_stack = 30
        monster = BattleSide("monster", [], health=100, max_health=100)
        scheduler = BattleEventScheduler(timeline)
        rules = {item.placement_id: read_rules(item) for item in side.cards}

        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)
        _apply_battle_rule(side, monster, side, monster, side.cards[0], side.cards[0], rules["purge"][0], 0, 1, scheduler, 0.0, timeline, None, rules, 10, {})

        self.assertEqual(side.burn_stack, 40)
        self.assertEqual(side.poison_stack, 15)
        self.assertTrue(any(item["kind"] == "poison-cleansed" and item["removed_amount"] == 15 for item in timeline))

    def test_status_cleanse_before_burn_tick_uses_remaining_burn_for_damage_and_decay(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100)
        monster = BattleSide("monster", [], health=100, max_health=100, burn_stack=100)

        cleanse_combat_status(monster, "burn", 20, 0.49, "Cleanser", timeline, source_side=player)
        _process_burn_tick(player, monster, 0.5, timeline)

        self.assertEqual(monster.health, 20)
        self.assertEqual(monster.burn_stack, 78)
        self.assertEqual([item["kind"] for item in timeline if item["kind"] in {"burn-cleansed", "burn-tick", "burn-decayed"}], ["burn-cleansed", "burn-tick", "burn-decayed"])

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

    def test_burn_tick_decays_by_floor_three_percent_after_damage(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100)
        monster = BattleSide("monster", [], health=100, max_health=100, burn_stack=60)

        _process_burn_tick(player, monster, 0.5, timeline)

        self.assertEqual(monster.health, 40)
        self.assertEqual(monster.burn_stack, 59)
        decay_event = [event for event in timeline if event["kind"] == "burn-decayed"][0]
        self.assertEqual(decay_event["value"], 1)
        self.assertEqual(decay_event["old_stack"], 60)
        self.assertEqual(decay_event["new_stack"], 59)

    def test_sandstorm_naturally_starts_and_ticks_after_interval(self) -> None:
        outcome = _simulate_two_sided_battle(
            player_cards=[],
            monster_cards=[],
            player_health=100,
            monster_health=100,
            duration_sec=30.7,
            rng=None,
        )

        starts = [event for event in outcome.timeline if event["kind"] == "sandstorm-start"]
        ticks = [event for event in outcome.timeline if event["kind"] == "sandstorm-tick-summary"]

        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["time"], 30.0)
        self.assertEqual([event["time"] for event in ticks[:3]], [30.2, 30.4, 30.6])
        self.assertEqual([event["raw_damage"] for event in ticks[:3]], [1.0, 3.0, 5.0])
        self.assertEqual(outcome.sandstorm["ticks"], 3)

    def test_active_sandstorm_start_does_not_restart_at_natural_time(self) -> None:
        starter = make_card("Storm Starter", "tpl_starter", damage=0, action_type="TActionCardBeginSandstorm")

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("starter", starter, tier="Bronze")],
            monster_cards=[],
            player_health=100,
            monster_health=100,
            duration_sec=30.1,
            rng=None,
        )

        starts = [event for event in outcome.timeline if event["kind"] == "sandstorm-start"]
        ignored = [event for event in outcome.timeline if event["kind"] == "sandstorm-start-ignored"]

        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["time"], 5.0)
        self.assertEqual(starts[0]["source"], "Storm Starter")
        self.assertEqual(ignored, [])
        self.assertEqual(outcome.sandstorm["trigger_mode"], "active")

    def test_sandstorm_started_trigger_resolves_before_first_tick_damage(self) -> None:
        shield_skill = make_card(
            "Storm Shelter",
            "tpl_shelter",
            damage=5,
            action_type="TActionPlayerShieldApply",
            trigger_type="TTriggerOnSandstorm",
        )
        shield_skill["type"] = "Skill"
        shield_skill["raw_effects"]["abilities"]["0"]["Action"]["Target"]["TargetMode"] = "Player"
        shield_skill["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["ShieldApplyAmount"] = 5

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("shelter", shield_skill, tier="Bronze")],
            monster_cards=[],
            player_health=100,
            monster_health=100,
            duration_sec=30.3,
            rng=None,
        )

        shield_event = [event for event in outcome.timeline if event["kind"] == "shield"][0]
        first_hit = [event for event in outcome.timeline if event["kind"] == "sandstorm-tick" and event["target_side"] == "player"][0]

        self.assertEqual(shield_event["time"], 30.0)
        self.assertEqual(first_hit["time"], 30.2)
        self.assertEqual(first_hit["absorbed"], 1)
        self.assertEqual(first_hit["after_health"], 100)

    def test_sandstorm_damage_uses_shield_before_health(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100, shield=2)
        monster = BattleSide("monster", [], health=100, max_health=100, shield=3)
        state = SandstormState(active=True, started_at=0.0, next_tick_time=0.2, tick_index=1)

        _process_sandstorm_tick(player, monster, state, SandstormConfig(), 0.2, timeline)

        self.assertEqual(player.shield, 0)
        self.assertEqual(player.health, 99)
        self.assertEqual(monster.shield, 0)
        self.assertEqual(monster.health, 100)

    def test_sandstorm_damage_uses_existing_damage_reduction(self) -> None:
        timeline: list[dict] = []
        player = BattleSide("player", [], health=100, max_health=100, damage_reduction=[{"start": 0.0, "duration": 10.0, "value": 50}])
        monster = BattleSide("monster", [], health=100, max_health=100)
        state = SandstormState(active=True, started_at=0.0, next_tick_time=0.2, tick_index=2)

        _process_sandstorm_tick(player, monster, state, SandstormConfig(), 0.2, timeline)

        self.assertEqual(player.health, 97.5)
        self.assertEqual(monster.health, 95)
        player_event = [event for event in timeline if event["kind"] == "sandstorm-tick" and event["target_side"] == "player"][0]
        self.assertEqual(player_event["reduced"], 2.5)

    def test_sandstorm_tick_applies_to_both_sides_before_double_ko_result(self) -> None:
        outcome = _simulate_two_sided_battle(
            player_cards=[],
            monster_cards=[],
            player_health=1,
            monster_health=1,
            duration_sec=30.3,
            rng=None,
        )

        sandstorm_hits = [event for event in outcome.timeline if event["kind"] == "sandstorm-tick"]

        self.assertEqual(outcome.winner, "draw")
        self.assertEqual(outcome.end_reason, "sandstorm")
        self.assertEqual([event["target_side"] for event in sandstorm_hits], ["player", "monster"])
        self.assertEqual(outcome.player_remaining_health, 0)
        self.assertEqual(outcome.monster_remaining_health, 0)

    def test_duplicate_sandstorm_start_is_ignored_without_resetting_tick(self) -> None:
        timeline: list[dict] = []
        state = SandstormState()
        config = SandstormConfig()

        self.assertTrue(_start_sandstorm(state, config, 5.0, timeline, trigger_source="A", trigger_mode="active"))
        state.tick_index = 4
        self.assertFalse(_start_sandstorm(state, config, 10.0, timeline, trigger_source="B", trigger_mode="active"))

        self.assertEqual(state.started_at, 5.0)
        self.assertEqual(state.tick_index, 4)
        self.assertEqual(state.duplicate_starts, 1)
        self.assertEqual([event["kind"] for event in timeline], ["sandstorm-start", "sandstorm-start-ignored"])

    def test_sandstorm_tick_damage_caps_without_stopping(self) -> None:
        config = SandstormConfig(max_tick_damage=7)

        self.assertEqual([_sandstorm_tick_damage(index, config) for index in range(6)], [1, 3, 5, 7, 7, 7])

    def test_sandstorm_can_be_disabled_by_config(self) -> None:
        outcome = _simulate_two_sided_battle(
            player_cards=[],
            monster_cards=[],
            player_health=100,
            monster_health=100,
            duration_sec=31,
            rng=None,
            sandstorm_config=SandstormConfig(enabled=False),
        )

        self.assertFalse(any(event["kind"].startswith("sandstorm") for event in outcome.timeline))
        self.assertFalse(outcome.sandstorm["started"])
        self.assertEqual(outcome.player_remaining_health, 100)
        self.assertEqual(outcome.monster_remaining_health, 100)

    def test_card_disable_destroys_target_and_prevents_future_use(self) -> None:
        destroyer = make_card("Destroyer", "tpl_destroyer", action_type="TActionCardDisable", cooldown_ms=1000)
        weapon = make_card("Weapon", "tpl_weapon", damage=50, cooldown_ms=2000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("destroyer", destroyer, tier="Bronze")],
            monster_cards=[PlacedCard("weapon", weapon, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=5,
            rng=None,
            sandstorm_config=SandstormConfig(enabled=False),
        )

        destroyed = [event for event in outcome.timeline if event["kind"] == "card-destroyed"]
        monster_uses = [event for event in outcome.timeline if event["kind"] == "item-used" and event["side"] == "monster"]

        self.assertEqual(len(destroyed), 1)
        self.assertEqual(destroyed[0]["target"], "Weapon")
        self.assertEqual(monster_uses, [])
        self.assertEqual(outcome.player_remaining_health, 100)

    def test_destroyed_card_in_pending_use_queue_is_cancelled(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=50, cooldown_ms=1000)
        destroyer = make_card("Destroyer", "tpl_destroyer", action_type="TActionCardDisable", cooldown_ms=1000)
        player = _make_battle_side("player", [PlacedCard("destroyer", destroyer, tier="Bronze")], 100, 10)
        monster = _make_battle_side("monster", [PlacedCard("weapon", weapon, tier="Bronze")], 100, 10)
        timeline: list[dict] = []
        scheduler = BattleEventScheduler(timeline)
        target_ref = BattleCardRef(monster, monster.active[0])

        scheduler.request_item_use(target_ref, 1.0, reason="cooldown")
        destroy_card(
            player,
            monster,
            target_ref,
            player,
            player.active[0],
            {card.placement_id: read_rules(card) for card in [*player.cards, *monster.cards]},
            scheduler,
            1.0,
            timeline,
            None,
            10,
        )
        event = scheduler.pop_due(1.0)
        self.assertIsNotNone(event)
        _fire_battle_card(
            event.ref,
            player,
            monster,
            {},
            scheduler,
            1.0,
            timeline,
            None,
            forced=False,
            requested_time=1.0,
        )

        self.assertEqual(monster.uses.get("weapon", 0), 0)
        self.assertTrue(any(item["kind"] == "item-use-cancelled" for item in timeline))
        self.assertEqual(player.health, 100)

    def test_before_destroy_trigger_runs_before_state_commit(self) -> None:
        target = make_card("Target", "tpl_target", damage=0, action_type="TActionPlayerShieldApply", trigger_type="TTriggerOnBeforeCardDestroyed")
        target["raw_effects"]["abilities"]["0"]["Action"]["Target"]["TargetMode"] = "Player"
        target["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["ShieldApplyAmount"] = 7
        destroyer = make_card("Destroyer", "tpl_destroyer", action_type="TActionCardDisable")
        player = _make_battle_side("player", [PlacedCard("destroyer", destroyer, tier="Bronze")], 100, 10)
        monster = _make_battle_side("monster", [PlacedCard("target", target, tier="Bronze")], 100, 10)
        timeline: list[dict] = []
        scheduler = BattleEventScheduler(timeline)

        destroy_card(
            player,
            monster,
            BattleCardRef(monster, monster.cards[0]),
            player,
            player.active[0],
            {card.placement_id: read_rules(card) for card in [*player.cards, *monster.cards]},
            scheduler,
            1.0,
            timeline,
            None,
            10,
        )

        shield_event = [event for event in timeline if event["kind"] == "shield"][0]
        destroyed_event = [event for event in timeline if event["kind"] == "card-destroyed"][0]
        self.assertLess(timeline.index(shield_event), timeline.index(destroyed_event))
        self.assertEqual(monster.shield, 7)
        self.assertIn("target", monster.destroyed)

    def test_card_disabled_trigger_runs_after_state_commit_once(self) -> None:
        target = make_card("Target", "tpl_target", damage=0)
        observer = make_card("Observer", "tpl_observer", damage=0, action_type="TActionPlayerShieldApply", trigger_type="TTriggerOnCardDisabled")
        observer["raw_effects"]["abilities"]["0"]["Action"]["Target"]["TargetMode"] = "Player"
        observer["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["ShieldApplyAmount"] = 5
        destroyer = make_card("Destroyer", "tpl_destroyer", action_type="TActionCardDisable")
        player = _make_battle_side("player", [PlacedCard("destroyer", destroyer, tier="Bronze")], 100, 10)
        monster = _make_battle_side(
            "monster",
            [PlacedCard("target", target, tier="Bronze"), PlacedCard("observer", observer, start=1, tier="Bronze")],
            100,
            10,
        )
        timeline: list[dict] = []
        scheduler = BattleEventScheduler(timeline)
        rules = {card.placement_id: read_rules(card) for card in [*player.cards, *monster.cards]}
        target_ref = BattleCardRef(monster, monster.active[0])

        destroy_card(player, monster, target_ref, player, player.active[0], rules, scheduler, 1.0, timeline, None, 10)
        destroy_card(player, monster, target_ref, player, player.active[0], rules, scheduler, 2.0, timeline, None, 10)

        self.assertIn("target", monster.destroyed)
        self.assertEqual(len([event for event in timeline if event["kind"] == "card-destroyed"]), 1)
        self.assertEqual(len([event for event in timeline if event["kind"] == "shield"]), 1)
        self.assertTrue(any(event["kind"] == "card-destroy-ignored" for event in timeline))

    def test_destroyed_card_cooldown_pauses_and_repair_preserves_instance(self) -> None:
        card = make_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=10000)
        repairer = make_card("Repairer", "tpl_repairer", action_type="TActionCardRepair")
        side = _make_battle_side("player", [PlacedCard("weapon", card, tier="Bronze"), PlacedCard("repairer", repairer, start=1, tier="Bronze")], 100, 20)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        target = BattleCardRef(side, side.active[0])
        source = side.active[1]
        rules = {card_ref.placement_id: read_rules(card_ref) for card_ref in side.cards}

        _advance_cooldowns([side], 0.0, 2.0)
        before_destroy_remaining = side.cooldowns["weapon"].remaining_cooldown
        destroy_card(side, monster, target, side, source, rules, BattleEventScheduler(timeline), 2.0, timeline, None, 20)
        _advance_cooldowns([side], 2.0, 5.0)

        self.assertEqual(side.cooldowns["weapon"].remaining_cooldown, before_destroy_remaining)
        self.assertTrue(repair_card(target, side, source, 7.0, timeline))
        self.assertIs(target.card, side.active[0])
        _advance_cooldowns([side], 7.0, 1.0)
        self.assertEqual(side.cooldowns["weapon"].remaining_cooldown, before_destroy_remaining - 1.0)
        self.assertTrue(any(event["kind"] == "card-repaired" for event in timeline))

    def test_transform_destroyed_card_replaces_self_after_card_disabled_trigger(self) -> None:
        transformed = make_card("Milk", "tpl_milk", damage=3, cooldown_ms=1000)
        cow = make_transform_destroyed_card("Cow", "tpl_cow", spawn_id="tpl_milk")
        destroyer = make_card("Destroyer", "tpl_destroyer", action_type="TActionCardDisable")
        player = _make_battle_side("player", [PlacedCard("destroyer", destroyer, tier="Bronze")], 100, 10)
        monster = _make_battle_side("monster", [PlacedCard("cow", cow, tier="Silver")], 100, 10)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in [*player.cards, *monster.cards]}

        destroy_card(
            player,
            monster,
            BattleCardRef(monster, monster.cards[0]),
            player,
            player.active[0],
            rules,
            BattleEventScheduler(timeline),
            1.0,
            timeline,
            None,
            10,
            card_index={"tpl_milk": transformed},
        )

        self.assertEqual(len(monster.cards), 1)
        self.assertEqual(monster.cards[0].card["name"], "Milk")
        self.assertEqual(monster.cards[0].tier, "Silver")
        self.assertNotIn("cow", monster.destroyed)
        self.assertFalse(any(card.placement_id == "cow" for card in monster.cards))
        self.assertTrue(any(event["kind"] == "CARD_TRANSFORMED" for event in timeline))
        self.assertIn(monster.cards[0].placement_id, rules)

    def test_transform_destroyed_targets_only_destroyed_items(self) -> None:
        transformed = make_card("New Item", "tpl_new", damage=1, cooldown_ms=1000)
        transformer = make_transform_destroyed_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new", trigger_type="TTriggerOnCardFired")
        normal = make_card("Normal", "tpl_normal")
        destroyed = make_card("Destroyed", "tpl_destroyed")
        side = _make_battle_side(
            "player",
            [
                PlacedCard("transformer", transformer, tier="Bronze"),
                PlacedCard("normal", normal, start=1, tier="Bronze"),
                PlacedCard("destroyed", destroyed, start=2, tier="Bronze"),
            ],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        side.destroyed.add("destroyed")
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}
        rule = rules["transformer"][0]

        _apply_battle_rule(side, monster, side, monster, side.active[0], side.active[0], rule, 0, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={"tpl_new": transformed})

        self.assertEqual([card.card["name"] for card in side.cards], ["Transformer", "Normal", "New Item"])
        self.assertEqual(len([event for event in timeline if event["kind"] == "card-transformed"]), 1)

    def test_transform_destroyed_without_target_safely_skips(self) -> None:
        transformed = make_card("New Item", "tpl_new")
        transformer = make_transform_destroyed_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new", trigger_type="TTriggerOnCardFired")
        side = _make_battle_side("player", [PlacedCard("transformer", transformer, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.active[0], side.active[0], rules["transformer"][0], 0, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={"tpl_new": transformed})

        self.assertEqual(len(side.cards), 1)
        self.assertFalse(any(event["kind"] == "card-transformed" for event in timeline))

    def test_transform_destroyed_invalidates_old_queued_use(self) -> None:
        transformed = make_card("New Weapon", "tpl_new", damage=7, cooldown_ms=1000)
        old_weapon = make_card("Old Weapon", "tpl_old", damage=10, cooldown_ms=1000)
        transformer = make_transform_destroyed_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new", trigger_type="TTriggerOnCardFired")
        side = _make_battle_side("player", [PlacedCard("old", old_weapon, tier="Bronze"), PlacedCard("transformer", transformer, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        old_ref = BattleCardRef(side, side.active[0])
        side.destroyed.add("old")
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        transform_destroyed_card(BattleCardRef(side, side.cards[0]), side, side.active[1], rules["transformer"][0], {"tpl_new": transformed}, rules, 1.0, timeline, None)
        _fire_battle_card(old_ref, side, monster, rules, BattleEventScheduler(timeline), 1.1, timeline, None, forced=False, requested_time=1.0)

        self.assertTrue(any(event["kind"] == "item-use-cancelled" and event["reason"] == "replaced" for event in timeline))
        self.assertEqual(side.cards[0].card["name"], "New Weapon")

    def test_transform_destroyed_restarts_new_card_cooldown(self) -> None:
        transformed = make_card("New Weapon", "tpl_new", damage=7, cooldown_ms=1000)
        old = make_card("Old", "tpl_old")
        transformer = make_transform_destroyed_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new", trigger_type="TTriggerOnCardFired")
        side = _make_battle_side("player", [PlacedCard("old", old, tier="Bronze"), PlacedCard("transformer", transformer, start=1, tier="Bronze")], 100, 10)
        side.destroyed.add("old")
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        new_card = transform_destroyed_card(BattleCardRef(side, side.cards[0]), side, side.active[1], rules["transformer"][0], {"tpl_new": transformed}, rules, 2.0, timeline, None)

        self.assertIsNotNone(new_card)
        assert new_card is not None
        state = side.cooldowns[new_card.placement_id]
        self.assertEqual(state.remaining_cooldown, 1.0)
        _advance_cooldowns([side], 2.0, 0.5)
        self.assertEqual(state.remaining_cooldown, 0.5)

    def test_repair_does_not_restore_old_card_after_transform(self) -> None:
        transformed = make_card("New Item", "tpl_new")
        old = make_card("Old", "tpl_old")
        transformer = make_transform_destroyed_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new", trigger_type="TTriggerOnCardFired")
        repairer = make_card("Repairer", "tpl_repairer", action_type="TActionCardRepair")
        side = _make_battle_side("player", [PlacedCard("old", old, tier="Bronze"), PlacedCard("transformer", transformer, start=1, tier="Bronze"), PlacedCard("repairer", repairer, start=2, tier="Bronze")], 100, 10)
        old_ref = BattleCardRef(side, side.cards[0])
        side.destroyed.add("old")
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        transform_destroyed_card(old_ref, side, side.active[1], rules["transformer"][0], {"tpl_new": transformed}, rules, 1.0, timeline, None)
        self.assertFalse(repair_card(old_ref, side, side.active[1], 2.0, timeline))

        self.assertEqual(len(side.cards), 3)
        self.assertEqual(side.cards[0].card["name"], "New Item")
        self.assertNotIn("old", side.destroyed)

    def test_transform_destroyed_duplicate_request_only_succeeds_once(self) -> None:
        transformed = make_card("New Item", "tpl_new")
        old = make_card("Old", "tpl_old")
        transformer = make_transform_destroyed_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new", trigger_type="TTriggerOnCardFired")
        side = _make_battle_side("player", [PlacedCard("old", old, tier="Bronze"), PlacedCard("transformer", transformer, start=1, tier="Bronze")], 100, 10)
        old_ref = BattleCardRef(side, side.cards[0])
        side.destroyed.add("old")
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        first = transform_destroyed_card(old_ref, side, side.active[1], rules["transformer"][0], {"tpl_new": transformed}, rules, 1.0, timeline, None)
        second = transform_destroyed_card(old_ref, side, side.active[1], rules["transformer"][0], {"tpl_new": transformed}, rules, 1.1, timeline, None)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len([event for event in timeline if event["kind"] == "CARD_TRANSFORMED"]), 1)

    def test_transform_destroyed_query_spawn_context_uses_matching_card(self) -> None:
        bad = make_card("Bad", "tpl_bad")
        good = make_card("Good Drone Reagent", "tpl_good", cooldown_ms=1000)
        good["tags"] = ["Drone", "Reagent"]
        good["size"] = "Small"
        constraints = [
            {"$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.Constraints.ConstraintCardType", "Types": ["Item"]},
            {"$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.Constraints.ConstraintTag", "Tags": ["Drone"]},
            {"$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.Constraints.ConstraintTag", "Tags": ["Reagent"]},
            {"$type": "BazaarGameShared.Domain.Spawning.SpawnFilters.Constraints.ConstraintSize", "Sizes": ["Small"]},
        ]
        transformer = make_transform_destroyed_card(
            "Transformer",
            "tpl_transformer",
            target_type="TTargetCardSection",
            trigger_type="TTriggerOnCardFired",
            query_constraints=constraints,
            inherit_tier=False,
        )
        old = make_card("Old", "tpl_old")
        side = _make_battle_side("player", [PlacedCard("old", old, tier="Bronze"), PlacedCard("transformer", transformer, start=1, tier="Bronze")], 100, 10)
        side.destroyed.add("old")
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        new_card = transform_destroyed_card(BattleCardRef(side, side.cards[0]), side, side.active[1], rules["transformer"][0], {"tpl_bad": bad, "tpl_good": good}, rules, 1.0, timeline, None)

        self.assertIsNotNone(new_card)
        assert new_card is not None
        self.assertEqual(new_card.card["name"], "Good Drone Reagent")

    def test_transform_card_replaces_active_target_without_destroying(self) -> None:
        transformed = make_card("New Weapon", "tpl_new", damage=7, cooldown_ms=2000)
        old = make_card("Old Weapon", "tpl_old", damage=3, cooldown_ms=5000)
        transformer = make_transform_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new")
        side = _make_battle_side("player", [PlacedCard("old", old, tier="Silver"), PlacedCard("transformer", transformer, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _advance_cooldowns([side], 0.0, 1.0)
        new_card = transform_card_instance(BattleCardRef(side, side.cards[0]), side, side.cards[1], rules["transformer"][0], {"tpl_new": transformed}, rules, 1.0, timeline, None)

        self.assertIsNotNone(new_card)
        assert new_card is not None
        self.assertEqual(side.cards[0].card["name"], "New Weapon")
        self.assertEqual(side.cards[0].start, 0)
        self.assertEqual(side.cards[0].width, 1)
        self.assertNotEqual(side.cards[0].placement_id, "old")
        self.assertNotIn("old", side.destroyed)
        self.assertEqual(side.transform_count["transformer"], 1)
        self.assertEqual(side.cooldowns[side.cards[0].placement_id].remaining_cooldown, 2.0)
        self.assertFalse(any(event["kind"] in {"card-destroyed", "card-disabled"} for event in timeline))
        self.assertEqual(len([event for event in timeline if event["kind"] == "CARD_TRANSFORMED"]), 1)

    def test_transform_card_excludes_destroyed_targets_but_destroyed_wrapper_handles_them(self) -> None:
        transformed = make_card("New Item", "tpl_new")
        old = make_card("Old", "tpl_old")
        transformer = make_transform_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_new")
        side = _make_battle_side("player", [PlacedCard("old", old, tier="Bronze"), PlacedCard("transformer", transformer, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        side.destroyed.add("old")
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        skipped = transform_card_instance(BattleCardRef(side, side.cards[0]), side, side.cards[1], rules["transformer"][0], {"tpl_new": transformed}, rules, 1.0, timeline, None)

        self.assertIsNone(skipped)
        self.assertEqual(side.cards[0].card["name"], "Old")
        self.assertFalse(any(event["kind"] == "CARD_TRANSFORMED" for event in timeline))

        destroyed_transformer = make_transform_destroyed_card("Destroyed Transformer", "tpl_destroyed_transformer", target_type="TTargetCardSection", spawn_id="tpl_new", trigger_type="TTriggerOnCardFired")
        side.cards[1] = PlacedCard("destroyed_transformer", destroyed_transformer, start=1, tier="Bronze")
        rules = {card.placement_id: read_rules(card) for card in side.cards}
        transform_destroyed_card(BattleCardRef(side, side.cards[0]), side, side.cards[1], rules["destroyed_transformer"][0], {"tpl_new": transformed}, rules, 2.0, timeline, None)

        self.assertEqual(side.cards[0].card["name"], "New Item")

    def test_transform_card_spawn_filter_target_copies_board_target(self) -> None:
        copied = make_card("Copied Small", "tpl_copied", damage=4, cooldown_ms=3000)
        mirror = make_transform_card(
            "Mirror",
            "tpl_mirror",
            target_type="TTargetCardSelf",
            filter_target={
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardPositional",
                "Origin": "Self",
                "TargetMode": "LeftCard",
                "IncludeOrigin": False,
            },
        )
        side = _make_battle_side("player", [PlacedCard("copied", copied, tier="Gold"), PlacedCard("mirror", mirror, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.cards[1], side.cards[1], rules["mirror"][0], 0, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={})

        self.assertEqual(side.cards[1].card["name"], "Copied Small")
        self.assertEqual(side.cards[1].tier, "Bronze")
        self.assertTrue(any(event["kind"] == "card-transformed" and event["spawn_resolution"] == "target" for event in timeline))

    def test_card_transformed_trigger_uses_new_card_as_trigger_target_once(self) -> None:
        transformed = make_card("Potion", "tpl_potion", cooldown_ms=1000)
        transformed["tags"] = ["Potion"]
        old = make_card("Old", "tpl_old")
        old["tags"] = ["TransformTarget"]
        transformer = make_transform_card("Transformer", "tpl_transformer", target_type="TTargetCardSection", spawn_id="tpl_potion")
        transformer["raw_effects"]["abilities"]["0"]["Action"]["Target"]["Conditions"] = {
            "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
            "Tags": ["TransformTarget"],
            "Operator": "Any",
        }
        listener = make_card_transformed_listener("Listener", "tpl_listener", include_tags=["Potion"], action_amount=9)
        side = _make_battle_side(
            "player",
            [PlacedCard("old", old, tier="Bronze"), PlacedCard("transformer", transformer, start=1, tier="Bronze"), PlacedCard("listener", listener, start=2, tier="Bronze")],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.cards[1], side.cards[1], rules["transformer"][0], 0, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={"tpl_potion": transformed})

        self.assertEqual(side.shield, 9)
        self.assertEqual(len([event for event in timeline if event["kind"] == "shield" and event["source"] == "Listener"]), 1)
        self.assertTrue(all(event.get("target") != "Old" for event in timeline if event["kind"] == "shield"))

    def test_old_self_transformed_trigger_does_not_survive_replacement(self) -> None:
        transformed = make_card("New Item", "tpl_new")
        old = make_transform_card("Old Transformer", "tpl_old_transformer", target_type="TTargetCardSelf", spawn_id="tpl_new")
        old["raw_effects"]["abilities"]["1"] = make_card_transformed_listener("Old Transformer", "tpl_old_transformer_listener", action_amount=7)["raw_effects"]["abilities"]["0"]
        side = _make_battle_side("player", [PlacedCard("old", old, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.cards[0], side.cards[0], rules["old"][0], 0, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={"tpl_new": transformed})

        self.assertEqual(side.cards[0].card["name"], "New Item")
        self.assertEqual(side.shield, 0)
        self.assertFalse(any(event["kind"] == "shield" for event in timeline))

    def test_card_attribute_changed_emits_after_effective_damage_change(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        weapon["tags"] = ["Weapon"]
        modifier = make_card_attribute_modifier("Modifier", "tpl_modifier", attribute="DamageAmount", amount=5)
        modifier["raw_effects"]["abilities"]["0"]["Action"]["Target"]["Conditions"] = {
            "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
            "Tags": ["Weapon"],
            "Operator": "Any",
        }
        listener = make_attribute_changed_card(
            "Listener",
            "tpl_listener",
            listened_attribute="DamageAmount",
            subject_type="TTargetCardSection",
            subject_section="SelfHand",
            action_type="TActionPlayerShieldApply",
            action_amount=7,
        )
        side = _make_battle_side(
            "player",
            [PlacedCard("weapon", weapon, tier="Bronze"), PlacedCard("modifier", modifier, start=1, tier="Bronze"), PlacedCard("listener", listener, start=2, tier="Bronze")],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.active[1], side.active[1], rules["modifier"][0], 5, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={})

        event = [item for item in timeline if item["kind"] == "CARD_ATTRIBUTE_CHANGED"][0]
        self.assertEqual(event["attribute"], "DamageAmount")
        self.assertEqual(event["old_value"], 10)
        self.assertEqual(event["new_value"], 15)
        self.assertEqual(event["delta"], 5)
        self.assertEqual(side.shield, 7)

    def test_card_attribute_changed_does_not_emit_for_zero_effective_change(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        modifier = make_card_attribute_modifier("Modifier", "tpl_modifier", attribute="DamageAmount", amount=0, target_type="TTargetCardSelf")
        side = _make_battle_side("player", [PlacedCard("weapon", weapon, tier="Bronze"), PlacedCard("modifier", modifier, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.active[1], side.active[1], rules["modifier"][0], 0, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={})

        self.assertFalse(any(item["kind"] == "CARD_ATTRIBUTE_CHANGED" for item in timeline))

    def test_card_attribute_changed_filters_attribute_and_subject(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        other = make_card("Other", "tpl_other", damage=10, cooldown_ms=1000)
        modifier = make_card_attribute_modifier("Modifier", "tpl_modifier", attribute="DamageAmount", amount=5, target_type="TTargetCardSelf")
        listener = make_attribute_changed_card(
            "Listener",
            "tpl_listener",
            listened_attribute="CritChance",
            subject_type="TTargetCardSelf",
            action_type="TActionPlayerShieldApply",
            action_amount=7,
        )
        side = _make_battle_side(
            "player",
            [PlacedCard("weapon", weapon, tier="Bronze"), PlacedCard("other", other, start=1, tier="Bronze"), PlacedCard("modifier", modifier, start=2, tier="Bronze"), PlacedCard("listener", listener, start=3, tier="Bronze")],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.active[2], side.active[2], rules["modifier"][0], 5, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={})

        self.assertEqual(side.shield, 0)
        self.assertEqual(len([item for item in timeline if item["kind"] == "CARD_ATTRIBUTE_CHANGED"]), 1)

    def test_card_attribute_changed_trigger_reads_new_value_for_followup_growth(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        weapon["tags"] = ["Weapon"]
        modifier = make_card_attribute_modifier("Modifier", "tpl_modifier", attribute="DamageAmount", amount=5)
        modifier["raw_effects"]["abilities"]["0"]["Action"]["Target"]["Conditions"] = {
            "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
            "Tags": ["Weapon"],
            "Operator": "Any",
        }
        listener = make_attribute_changed_card(
            "Listener",
            "tpl_listener",
            listened_attribute="DamageAmount",
            subject_type="TTargetCardSection",
            subject_section="SelfHand",
            action_type="TActionCardModifyAttribute",
            action_attribute="DamageAmount",
            action_amount=2,
            action_target_type="TTargetCardTriggerSource",
        )
        listener["raw_effects"]["abilities"]["0"]["Trigger"]["MaxTriggers"] = 1
        side = _make_battle_side("player", [PlacedCard("weapon", weapon, tier="Bronze"), PlacedCard("modifier", modifier, start=1, tier="Bronze"), PlacedCard("listener", listener, start=2, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.active[1], side.active[1], rules["modifier"][0], 5, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={})

        changes = [item for item in timeline if item["kind"] == "CARD_ATTRIBUTE_CHANGED" and item["target"] == "Weapon"]
        self.assertEqual(changes[0]["new_value"], 15)
        self.assertEqual(changes[-1]["new_value"], 17)
        self.assertEqual(side.bonus["DamageAmount"]["weapon"], 7)

    def test_ammo_loss_triggers_card_attribute_changed_but_natural_cooldown_does_not(self) -> None:
        ammo_card = make_card("Ammo Weapon", "tpl_ammo", damage=1, cooldown_ms=1000)
        ammo_card["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["AmmoMax"] = 2
        listener = make_attribute_changed_card(
            "Listener",
            "tpl_listener",
            listened_attribute="Ammo",
            change_type="Loss",
            subject_type="TTargetCardSection",
            subject_section="SelfHand",
            action_type="TActionPlayerShieldApply",
            action_amount=4,
        )
        side = _make_battle_side("player", [PlacedCard("ammo", ammo_card, tier="Bronze"), PlacedCard("listener", listener, start=1, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _advance_cooldowns([side], 0.0, 0.5)
        self.assertFalse(any(item["kind"] == "CARD_ATTRIBUTE_CHANGED" for item in timeline))
        _fire_battle_card(BattleCardRef(side, side.active[0]), side, monster, rules, BattleEventScheduler(timeline), 1.0, timeline, None, forced=False)

        self.assertTrue(any(item["kind"] == "CARD_ATTRIBUTE_CHANGED" and item["attribute"] == "Ammo" for item in timeline))
        self.assertEqual(side.shield, 4)

    def test_flying_gain_and_loss_trigger_card_attribute_changed(self) -> None:
        flier = make_card("Flier", "tpl_flier", damage=0, action_type="TActionCardFlyingStart", cooldown_ms=1000)
        flier["raw_effects"]["abilities"]["0"]["Action"]["Target"] = {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf"
        }
        listener = make_attribute_changed_card(
            "Listener",
            "tpl_listener",
            listened_attribute="Flying",
            change_type="Gain",
            subject_type="TTargetCardSection",
            subject_section="SelfHand",
            action_type="TActionPlayerShieldApply",
            action_amount=3,
        )
        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("flier", flier, tier="Bronze"), PlacedCard("listener", listener, start=1, tier="Bronze")],
            monster_cards=[],
            player_health=100,
            monster_health=100,
            duration_sec=1.5,
            rng=None,
            sandstorm_config=SandstormConfig(enabled=False),
        )

        self.assertTrue(any(item["kind"] == "CARD_ATTRIBUTE_CHANGED" and item["attribute"] == "Flying" and item["delta"] > 0 for item in outcome.timeline))
        self.assertTrue(any(item["kind"] == "shield" and item["source"] == "Listener" for item in outcome.timeline))

    def test_destroyed_listener_ignores_attribute_changes_and_repair_restores(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        modifier = make_card_attribute_modifier("Modifier", "tpl_modifier", attribute="DamageAmount", amount=5, target_type="TTargetCardSelf")
        listener = make_attribute_changed_card(
            "Listener",
            "tpl_listener",
            listened_attribute="DamageAmount",
            subject_type="TTargetCardSection",
            subject_section="SelfHand",
            action_type="TActionPlayerShieldApply",
            action_amount=3,
        )
        side = _make_battle_side("player", [PlacedCard("weapon", weapon, tier="Bronze"), PlacedCard("modifier", modifier, start=1, tier="Bronze"), PlacedCard("listener", listener, start=2, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}
        side.destroyed.add("listener")

        _apply_battle_rule(side, monster, side, monster, side.active[1], side.active[1], rules["modifier"][0], 5, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={})
        self.assertEqual(side.shield, 0)
        self.assertTrue(repair_card(BattleCardRef(side, side.cards[2]), side, side.active[1], 2.0, timeline))
        _apply_battle_rule(side, monster, side, monster, side.active[1], side.active[1], rules["modifier"][0], 5, 1, BattleEventScheduler(timeline), 2.0, timeline, None, rules, 10, {}, card_index={})
        self.assertEqual(side.shield, 3)

    def test_card_attribute_changed_recursive_growth_is_depth_limited(self) -> None:
        weapon = make_card("Weapon", "tpl_weapon", damage=10, cooldown_ms=1000)
        weapon["tags"] = ["Weapon"]
        modifier = make_card_attribute_modifier("Modifier", "tpl_modifier", attribute="DamageAmount", amount=5)
        modifier["raw_effects"]["abilities"]["0"]["Action"]["Target"]["Conditions"] = {
            "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
            "Tags": ["Weapon"],
            "Operator": "Any",
        }
        listener = make_attribute_changed_card(
            "Listener",
            "tpl_listener",
            listened_attribute="DamageAmount",
            subject_type="TTargetCardSection",
            subject_section="SelfHand",
            action_type="TActionCardModifyAttribute",
            action_attribute="DamageAmount",
            action_amount=1,
            action_target_type="TTargetCardTriggerSource",
        )
        side = _make_battle_side("player", [PlacedCard("weapon", weapon, tier="Bronze"), PlacedCard("modifier", modifier, start=1, tier="Bronze"), PlacedCard("listener", listener, start=2, tier="Bronze")], 100, 10)
        monster = BattleSide("monster", [], health=100, max_health=100)
        timeline: list[dict] = []
        rules = {card.placement_id: read_rules(card) for card in side.cards}

        _apply_battle_rule(side, monster, side, monster, side.active[1], side.active[1], rules["modifier"][0], 5, 1, BattleEventScheduler(timeline), 1.0, timeline, None, rules, 10, {}, card_index={})

        self.assertTrue(any(item["kind"] == "trigger-depth-limited" and item.get("trigger") == "TTriggerOnCardAttributeChanged" for item in timeline))
        self.assertGreaterEqual(len([item for item in timeline if item["kind"] == "CARD_ATTRIBUTE_CHANGED"]), 2)

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

    def test_runtime_snapshot_attributes_do_not_double_apply_auras(self) -> None:
        shield_item = make_card("Shield", "tpl_shield", damage=0, cooldown_ms=7000, action_type="TActionPlayerShieldApply")
        shield_item["tags"] = ["Weapon"]
        shield_item["hidden_tags"] = ["Weapon"]
        shield_item["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"].update(
            {
                "ShieldApplyAmount": 30,
                "Multicast": 1,
                "Chilled": 0,
            }
        )
        static_aura = make_aura_skill("Static Aura", "tpl_static_aura", "ShieldApplyAmount", 10)
        runtime_aura = make_runtime_aura_skill("Cold Aura", "tpl_cold_aura", "Multicast", 1, "Chilled")
        data = {
            "cards": {
                "Shield": shield_item,
                "Static Aura": static_aura,
                "Cold Aura": runtime_aura,
            }
        }
        placements, skipped = build_current_board_placements(
            data,
            {
                "board_items": [
                    {
                        "id": "itm_shield",
                        "template_id": "tpl_shield",
                        "rarity": "Bronze",
                        "section": "Hand",
                        "current_attributes": {
                            "CooldownMax": 7000,
                            "ShieldApplyAmount": 40,
                            "Multicast": 2,
                            "Chilled": 1,
                        },
                    }
                ],
                "skills": [
                    {"id": "skill_static", "template_id": "tpl_static_aura", "rarity": "Diamond"},
                    {"id": "skill_cold", "template_id": "tpl_cold_aura", "rarity": "Bronze"},
                ],
            },
            include_skills=True,
        )

        self.assertEqual(skipped, [])
        outcome = _simulate_two_sided_battle(
            player_cards=placements,
            monster_cards=[],
            player_health=100,
            monster_health=1_000_000,
            duration_sec=8,
            rng=None,
        )

        shield_events = [event for event in outcome.timeline if event["kind"] == "shield" and event["source"] == "Shield"]
        self.assertEqual([event["value"] for event in shield_events], [40.0, 40.0])

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

    def test_tag_aura_list_grants_effective_tags_without_mutating_base_tags(self) -> None:
        timeline: list[dict] = []
        target_card = make_card("Blade", "tpl_blade", damage=4, cooldown_ms=1000)
        aura_card = make_tag_aura_card(
            "Saddle",
            "tpl_saddle",
            tags=["Vehicle", "Tool"],
            target={
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardPositional",
                "TargetMode": "RightCard",
            },
        )
        side = _make_battle_side(
            "player",
            [
                PlacedCard("aura", aura_card, start=0, tier="Bronze"),
                PlacedCard("target", target_card, start=1, tier="Bronze"),
            ],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        scheduler = BattleEventScheduler(timeline)

        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)

        effective = card_tags(side.cards[1], _runtime_aura_tags(side))
        self.assertEqual(effective, {"tool", "vehicle"})
        self.assertEqual(target_card["tags"], [])
        first_log_count = len([event for event in timeline if event["kind"] == "runtime-tags-added"])

        _refresh_runtime_state_auras(side, monster, scheduler, 0.1, timeline, None)

        self.assertEqual(len([event for event in timeline if event["kind"] == "runtime-tags-added"]), first_log_count)
        self.assertEqual(len(side.runtime_tags_by_source["target"]), 1)

    def test_tag_aura_immediately_drives_tag_conditioned_attribute_aura(self) -> None:
        timeline: list[dict] = []
        target_card = make_card("Blade", "tpl_blade", damage=4, cooldown_ms=1000)
        tag_aura = make_tag_aura_card(
            "Badge",
            "tpl_badge",
            tags=["Weapon"],
            target={
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardPositional",
                "TargetMode": "RightCard",
            },
        )
        damage_aura = make_aura_skill("Forge", "tpl_forge", "DamageAmount", 3)
        side = _make_battle_side(
            "player",
            [
                PlacedCard("tag_aura", tag_aura, start=0, tier="Bronze"),
                PlacedCard("target", target_card, start=1, tier="Bronze"),
                PlacedCard("damage_aura", damage_aura, start=2, tier="Diamond"),
            ],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        scheduler = BattleEventScheduler(timeline)

        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)

        self.assertEqual(side.runtime_aura_bonus["DamageAmount"]["target"], 3)
        self.assertFalse(any(event["kind"] == "card-attribute-changed" and event.get("attribute") == "tags" for event in timeline))

    def test_tag_aura_source_destroy_repair_and_refcounts(self) -> None:
        timeline: list[dict] = []
        target_card = make_card("Blade", "tpl_blade", damage=4, cooldown_ms=1000)
        target = {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
            "TargetSection": "SelfHand",
            "Conditions": {
                "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
                "Tags": ["Weapon"],
                "Operator": "None",
            },
        }
        aura_a = make_tag_aura_card("Banner A", "tpl_banner_a", tags=["Tool"], target=target)
        aura_b = make_tag_aura_card("Banner B", "tpl_banner_b", tags=["Tool"], target=target)
        side = _make_battle_side(
            "player",
            [
                PlacedCard("a", aura_a, start=0, tier="Bronze"),
                PlacedCard("b", aura_b, start=1, tier="Bronze"),
                PlacedCard("target", target_card, start=2, tier="Bronze"),
            ],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        scheduler = BattleEventScheduler(timeline)

        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)
        self.assertIn("tool", card_tags(side.cards[2], _runtime_aura_tags(side)))
        self.assertEqual(len(side.runtime_tags_by_source["target"]), 2)

        side.destroyed.add("a")
        _refresh_runtime_state_auras(side, monster, scheduler, 0.5, timeline, None)
        self.assertIn("tool", card_tags(side.cards[2], _runtime_aura_tags(side)))
        self.assertEqual(len(side.runtime_tags_by_source["target"]), 1)

        side.destroyed.add("b")
        _refresh_runtime_state_auras(side, monster, scheduler, 1.0, timeline, None)
        self.assertNotIn("tool", card_tags(side.cards[2], _runtime_aura_tags(side)))

        repair_card(BattleCardRef(side, side.cards[0]), side, side.cards[2], 1.5, timeline)
        _refresh_runtime_state_auras(side, monster, scheduler, 1.5, timeline, None)
        self.assertIn("tool", card_tags(side.cards[2], _runtime_aura_tags(side)))
        self.assertEqual(len(side.runtime_tags_by_source["target"]), 1)

    def test_tag_aura_by_source_copies_effective_tags_and_converges(self) -> None:
        timeline: list[dict] = []
        provider = make_tag_aura_card(
            "Diving Helmet",
            "tpl_helmet",
            tags=["Aquatic"],
            target={
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardPositional",
                "TargetMode": "RightCard",
            },
        )
        source_card = make_card("Harpoon", "tpl_harpoon", damage=4, cooldown_ms=1000)
        source_card["tags"] = ["Weapon"]
        by_source = make_tag_aura_card(
            "Cargo Shorts",
            "tpl_cargo",
            action_type="TAuraActionCardAddTagsBySource",
            source_selector={
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
                "TargetSection": "SelfHand",
                "ExcludeSelf": True,
            },
            target={
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf",
            },
        )
        side = _make_battle_side(
            "player",
            [
                PlacedCard("provider", provider, start=0, tier="Bronze"),
                PlacedCard("source", source_card, start=1, tier="Bronze"),
                PlacedCard("copy", by_source, start=2, tier="Bronze"),
            ],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        scheduler = BattleEventScheduler(timeline)

        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)

        effective = card_tags(side.cards[2], _runtime_aura_tags(side))
        self.assertIn("weapon", effective)
        self.assertIn("aquatic", effective)
        self.assertFalse(any(event["kind"] == "runtime-tag-aura-refresh-limited" for event in timeline))

        side.destroyed.add("provider")
        _refresh_runtime_state_auras(side, monster, scheduler, 0.5, timeline, None)
        effective = card_tags(side.cards[2], _runtime_aura_tags(side))
        self.assertIn("weapon", effective)
        self.assertNotIn("aquatic", effective)

    def test_tag_aura_skill_sources_are_stable_and_destroyed_targets_are_excluded(self) -> None:
        timeline: list[dict] = []
        target_card = make_card("Blade", "tpl_blade", damage=4, cooldown_ms=1000)
        skill = make_tag_aura_card(
            "Free Ride",
            "tpl_free_ride",
            tags=["Vehicle"],
            card_type="Skill",
            target={
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardXMost",
                "TargetSection": "SelfHand",
                "TargetMode": "LeftMostCard",
                "ExcludeSelf": True,
            },
        )
        side = _make_battle_side(
            "player",
            [
                PlacedCard("target", target_card, start=0, tier="Bronze"),
                PlacedCard("skill", skill, start=1, tier="Bronze"),
            ],
            100,
            10,
        )
        monster = BattleSide("monster", [], health=100, max_health=100)
        scheduler = BattleEventScheduler(timeline)

        side.destroyed.add("target")
        _refresh_runtime_state_auras(side, monster, scheduler, 0.0, timeline, None)
        self.assertNotIn("target", side.runtime_tags_by_source)

        side.destroyed.discard("target")
        _refresh_runtime_state_auras(side, monster, scheduler, 0.5, timeline, None)
        first_source_key = next(iter(side.runtime_tags_by_source["target"]))
        _refresh_runtime_state_auras(side, monster, scheduler, 0.6, timeline, None)
        self.assertEqual(next(iter(side.runtime_tags_by_source["target"])), first_source_key)
        self.assertTrue(first_source_key.startswith("tag-aura:player:skill:"))

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

    def test_two_sided_damage_crit_turns_double_crit_into_triple_damage(self) -> None:
        player = make_card("Player", "tpl_player", damage=40, cooldown_ms=1000)
        player["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"].update(
            {"CritChance": 100, "DamageCrit": 100}
        )

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("player", player, tier="Bronze")],
            monster_cards=[],
            player_health=100,
            monster_health=500,
            duration_sec=2,
            rng=lambda: 0.0,
        )

        damage_events = [
            event
            for event in outcome.timeline
            if event["kind"] in {"damage", "use"} and event["source"] == "Player"
        ]
        self.assertEqual(damage_events[0]["value"], 120)

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

    def test_card_count_reference_and_damage_crit_are_supported(self) -> None:
        player = make_card("Player", "tpl_player", damage=100)
        monster = make_card("Tiny Cutlass", "tpl_cutlass", damage=40)
        monster["raw_effects"]["auras"]["1"] = {
            "$type": "BazaarGameShared.Domain.Effect.TCardAura",
            "Action": {
                "$type": "BazaarGameShared.Domain.Effect.AuraActions.TAuraActionCardModifyAttribute",
                "AttributeType": "DamageCrit",
                "Operation": "Add",
                "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 100},
                "Target": {"$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf"},
            },
        }
        monster["raw_effects"]["tiers_raw"]["Bronze"]["AuraIds"].append("1")
        skill = make_aura_skill("Arms Race", "tpl_arms_race", "CritChance", 0)
        skill["raw_effects"]["auras"]["0"]["Action"]["Value"] = {
            "$type": "BazaarGameShared.Domain.Values.ReferenceValues.TReferenceValueCardCount",
            "Target": {
                "$type": "BazaarGameShared.Domain.Targeting.TTargetCardSection",
                "TargetSection": "SelfHand",
                "Conditions": {
                    "$type": "BazaarGameShared.Domain.Prerequisites.Conditionals.TCardConditionalTag",
                    "Tags": ["Weapon"],
                    "Operator": "Any",
                },
            },
            "DefaultValue": 0,
        }
        monster["tags"] = ["Weapon"]

        result = evaluate_monster_choices(
            data={"cards": {"Player": player, "Tiny Cutlass": monster, "Arms Race": skill}},
            player_state=state_with("tpl_player", health=100),
            monster_choices=[
                {
                    **state_with(
                        "tpl_cutlass",
                        health=100,
                        skills=[{"id": "skill_1", "template_id": "tpl_arms_race", "rarity": "Diamond"}],
                    ),
                    "name": "Monster",
                }
            ],
            simulations=3,
            duration_sec=10,
        )["results"][0]

        effects = result["unsupported_effects"]
        self.assertFalse(
            any(item["card"] == "Tiny Cutlass" and item["effect"] == "DamageCrit" for item in effects)
        )
        self.assertFalse(
            any(
                item["card"] == "Arms Race"
                and item["card_type"] == "skill"
                and "ReferenceValueCardCount" in item["effect"]
                and item["reason"] == "unsupported_value"
                for item in effects
            )
        )

    def test_custom_attribute_bonus_is_used_by_reference_value_damage(self) -> None:
        player = make_card("Player", "tpl_player", damage=0, cooldown_ms=10000)
        monster = make_card("Custom Striker", "tpl_striker", damage=0, cooldown_ms=1000)
        monster["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["Custom_0"] = 10
        monster["raw_effects"]["abilities"]["0"]["Action"] = {
            "$type": "BazaarGameShared.Domain.Effect.Actions.TActionAnd",
            "Actions": [
                {
                    "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardModifyAttribute",
                    "AttributeType": "Custom_0",
                    "Operation": "Add",
                    "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 5},
                    "Target": {"$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf"},
                },
                {
                    "$type": "BazaarGameShared.Domain.Effect.Actions.TActionPlayerDamage",
                    "Value": {
                        "$type": "BazaarGameShared.Domain.Values.ReferenceValues.TReferenceValueCardAttribute",
                        "AttributeType": "Custom_0",
                        "Target": {"$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf"},
                        "DefaultValue": 0,
                    },
                    "Target": {
                        "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                        "TargetMode": "Opponent",
                    },
                },
            ],
        }

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("player", player, start=0, tier="Bronze")],
            monster_cards=[PlacedCard("striker", monster, start=0, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=2,
            rng=None,
        )

        damage_events = [
            event
            for event in outcome.timeline
            if event["kind"] == "damage" and event["source"] == "Custom Striker"
        ]
        self.assertTrue(damage_events)
        self.assertEqual(damage_events[0]["value"], 15)

    def test_status_target_count_attribute_controls_random_freeze_targets(self) -> None:
        freezer = make_card("Freezer", "tpl_freezer", damage=0, cooldown_ms=1000, action_type="TActionCardFreeze")
        freezer["raw_effects"]["abilities"]["0"]["Action"]["Target"] = {
            "$type": "BazaarGameShared.Domain.Targeting.TTargetCardRandom",
            "TargetSection": "OpponentHand",
        }
        freezer["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["FreezeAmount"] = 1000
        freezer["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["FreezeTargets"] = 2
        dummy_a = make_card("Dummy A", "tpl_dummy_a", damage=0, cooldown_ms=10000)
        dummy_b = make_card("Dummy B", "tpl_dummy_b", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[PlacedCard("freezer", freezer, start=0, tier="Bronze")],
            monster_cards=[
                PlacedCard("dummy_a", dummy_a, start=0, tier="Bronze"),
                PlacedCard("dummy_b", dummy_b, start=1, tier="Bronze"),
            ],
            player_health=100,
            monster_health=100,
            duration_sec=2,
            rng=None,
        )

        frozen_targets = {
            event["target"]
            for event in outcome.timeline
            if event["kind"] == "freeze" and event["source"] == "Freezer"
        }
        self.assertEqual(frozen_targets, {"Dummy A", "Dummy B"})

    def test_attribute_change_reference_uses_rage_delta(self) -> None:
        rager = make_card("Rager", "tpl_rager", damage=0, cooldown_ms=1000, action_type="TActionPlayerRageApply")
        rager["raw_effects"]["abilities"]["0"]["Action"]["Target"]["TargetMode"] = "Self"
        rager["raw_effects"]["tiers_raw"]["Bronze"]["Attributes"]["RageApplyAmount"] = 5
        cleaver = make_card("Cleaver", "tpl_cleaver", damage=10, cooldown_ms=1500)
        cleaver["raw_effects"]["abilities"]["1"] = {
            "$type": "BazaarGameShared.Domain.Effect.TCardAbility",
            "Trigger": {
                "$type": "BazaarGameShared.Domain.Effect.Trigger.TTriggerOnPlayerAttributeChanged",
                "Subject": {
                    "$type": "BazaarGameShared.Domain.Targeting.TTargetPlayerRelative",
                    "TargetMode": "Self",
                },
                "AttributeType": "Rage",
                "ChangeType": "Gain",
            },
            "Action": {
                "$type": "BazaarGameShared.Domain.Effect.Actions.TActionCardModifyAttribute",
                "Value": {
                    "$type": "BazaarGameShared.Domain.Values.ReferenceValues.TReferenceValueAttributeChange",
                    "DefaultValue": 0,
                    "Modifier": {
                        "$type": "BazaarGameShared.Domain.Values.TValueModifier",
                        "ModifyMode": "Multiply",
                        "Value": {"$type": "BazaarGameShared.Domain.Values.TFixedValue", "Value": 2},
                        "ShouldRound": True,
                    },
                },
                "AttributeType": "DamageAmount",
                "Operation": "Add",
                "Target": {"$type": "BazaarGameShared.Domain.Targeting.TTargetCardSelf"},
            },
        }
        cleaver["raw_effects"]["tiers_raw"]["Bronze"]["AbilityIds"].append("1")
        monster = make_card("Monster", "tpl_monster", damage=0, cooldown_ms=10000)

        outcome = _simulate_two_sided_battle(
            player_cards=[
                PlacedCard("rager", rager, start=0, tier="Bronze"),
                PlacedCard("cleaver", cleaver, start=1, tier="Bronze"),
            ],
            monster_cards=[PlacedCard("monster", monster, start=0, tier="Bronze")],
            player_health=100,
            monster_health=100,
            duration_sec=2,
            rng=None,
        )

        damage_events = [
            event
            for event in outcome.timeline
            if event["kind"] == "use" and event["source"] == "Cleaver"
        ]
        self.assertTrue(damage_events)
        self.assertEqual(damage_events[0]["value"], 20)

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
