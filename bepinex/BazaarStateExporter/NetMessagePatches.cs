using System;
using System.Collections.Generic;
using System.Reflection;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

namespace BazaarStateExporter
{
    public static class RuntimeStateCache
    {
        public static ManualLogSource Logger;
        public static object LatestGameStateSnapshot;
        private static readonly object CapturedCardsLock = new object();
        private static readonly Dictionary<string, CapturedCardEntry> CapturedCardsByInstanceId = new Dictionary<string, CapturedCardEntry>();

        public static bool RecordUiCard(CardSnapshot card)
        {
            if (card == null || string.IsNullOrEmpty(card.id))
            {
                return false;
            }

            lock (CapturedCardsLock)
            {
                bool changed = !CapturedCardsByInstanceId.TryGetValue(card.id, out CapturedCardEntry existing)
                    || existing.Card.template_id != card.template_id
                    || existing.Card.name != card.name
                    || existing.Card.rarity != card.rarity
                    || existing.Card.section != card.section
                    || existing.Card.card_type != card.card_type;

                CapturedCardsByInstanceId[card.id] = new CapturedCardEntry
                {
                    Card = card,
                    LastSeenAt = Time.unscaledTime,
                };
                return changed;
            }
        }

        public static List<CardSnapshot> GetCapturedUiCards(float maxAgeSeconds)
        {
            float now = Time.unscaledTime;
            List<string> expired = new List<string>();
            List<CardSnapshot> result = new List<CardSnapshot>();
            lock (CapturedCardsLock)
            {
                foreach (KeyValuePair<string, CapturedCardEntry> item in CapturedCardsByInstanceId)
                {
                    if (now - item.Value.LastSeenAt <= maxAgeSeconds)
                    {
                        result.Add(item.Value.Card);
                    }
                    else
                    {
                        expired.Add(item.Key);
                    }
                }

                foreach (string key in expired)
                {
                    CapturedCardsByInstanceId.Remove(key);
                }
            }

            return result;
        }

        private sealed class CapturedCardEntry
        {
            public CardSnapshot Card;
            public float LastSeenAt;
        }
    }

    [HarmonyPatch]
    public static class NetMessageGameStateSyncPatch
    {
        public static MethodBase TargetMethod()
        {
            Type processorType = AccessTools.TypeByName("TheBazaar.NetMessageProcessor");
            Type messageType = AccessTools.TypeByName("BazaarGameShared.Infra.Messages.NetMessageGameStateSync");
            if (processorType == null || messageType == null)
            {
                RuntimeStateCache.Logger?.LogWarning("Could not find NetMessageProcessor or NetMessageGameStateSync for patching.");
                return null;
            }

            return AccessTools.Method(processorType, "Handle", new[] { messageType });
        }

        public static void Prefix(object message)
        {
            if (message == null)
            {
                return;
            }

            PropertyInfo dataProperty = message.GetType().GetProperty("Data", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            object data = dataProperty == null ? null : dataProperty.GetValue(message, null);
            if (data != null)
            {
                RuntimeStateCache.LatestGameStateSnapshot = data;
                RuntimeStateCache.Logger?.LogInfo("Captured NetMessageGameStateSync via Harmony patch.");
            }
        }
    }

    [HarmonyPatch]
    public static class CardControllerShowCardPatch
    {
        public static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName("CardController");
            return type == null ? null : AccessTools.Method(type, "ShowCard", new[] { typeof(bool) });
        }

        public static void Postfix(object __instance, bool show)
        {
            if (show)
            {
                UiCardCapture.TryCapture(__instance, "show");
            }
        }
    }

    [HarmonyPatch]
    public static class CardControllerPointerUpPatch
    {
        public static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName("CardController");
            Type eventType = AccessTools.TypeByName("UnityEngine.EventSystems.PointerEventData");
            return type == null || eventType == null ? null : AccessTools.Method(type, "OnPointerUp", new[] { eventType });
        }

        public static void Postfix(object __instance)
        {
            UiCardCapture.TryCapture(__instance, "pointer_up");
        }
    }

    [HarmonyPatch]
    public static class CardControllerPointerClickPatch
    {
        public static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName("CardController");
            Type eventType = AccessTools.TypeByName("UnityEngine.EventSystems.PointerEventData");
            return type == null || eventType == null ? null : AccessTools.Method(type, "OnPointerClick", new[] { eventType });
        }

        public static void Postfix(object __instance)
        {
            UiCardCapture.TryCapture(__instance, "pointer_click");
        }
    }

    [HarmonyPatch]
    public static class CardControllerPointerEnterPatch
    {
        public static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName("CardController");
            Type eventType = AccessTools.TypeByName("UnityEngine.EventSystems.PointerEventData");
            return type == null || eventType == null ? null : AccessTools.Method(type, "OnPointerEnter", new[] { eventType });
        }

        public static void Postfix(object __instance)
        {
            UiCardCapture.TryCapture(__instance, "pointer_enter");
        }
    }

    public static class UiCardCapture
    {
        public static void TryCapture(object controller, string source)
        {
            try
            {
                CardSnapshot card = BuildCardSnapshot(controller, source);
                bool changed = RuntimeStateCache.RecordUiCard(card);
                if (changed && card != null && !string.IsNullOrEmpty(card.id))
                {
                    RuntimeStateCache.Logger?.LogInfo(
                        "Captured UI card source="
                        + source
                        + " id="
                        + card.id
                        + " template="
                        + card.template_id
                        + " name="
                        + card.name
                        + " type="
                        + card.card_type
                        + " section="
                        + card.section);
                }
            }
            catch (Exception ex)
            {
                RuntimeStateCache.Logger?.LogDebug("UI card capture failed: " + ex.Message);
            }
        }

        private static CardSnapshot BuildCardSnapshot(object controller, string source)
        {
            object cardData = GetProperty(controller, "CardData");
            if (cardData == null)
            {
                return null;
            }

            object enchantment = GetProperty(cardData, "Enchantment");
            CardSnapshot card = new CardSnapshot
            {
                id = StringValue(GetProperty(cardData, "InstanceId")),
                template_id = StringValue(GetProperty(cardData, "TemplateId")),
                name = StringValue(GetProperty(cardData, "Name")),
                rarity = NormalizeTier(StringValue(GetProperty(cardData, "Tier"))),
                section = StringValue(GetProperty(cardData, "Section")),
                card_type = StringValue(GetProperty(cardData, "Type")),
                source = source,
            };

            if (HasValue(enchantment))
            {
                card.enchantments.Add(StringValue(enchantment));
            }

            return card;
        }

        private static object GetProperty(object target, string name)
        {
            if (target == null)
            {
                return null;
            }

            PropertyInfo property = target.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return property == null ? null : property.GetValue(target, null);
        }

        private static string StringValue(object value)
        {
            return value == null ? null : value.ToString();
        }

        private static bool HasValue(object nullable)
        {
            if (nullable == null)
            {
                return false;
            }

            PropertyInfo hasValue = nullable.GetType().GetProperty("HasValue");
            if (hasValue == null)
            {
                return true;
            }

            return (bool)hasValue.GetValue(nullable, null);
        }

        private static string NormalizeTier(string tier)
        {
            if (string.IsNullOrEmpty(tier))
            {
                return null;
            }

            string lower = tier.ToLowerInvariant();
            if (lower.Contains("bronze"))
            {
                return "bronze";
            }
            if (lower.Contains("silver"))
            {
                return "silver";
            }
            if (lower.Contains("gold"))
            {
                return "gold";
            }
            if (lower.Contains("diamond"))
            {
                return "diamond";
            }
            if (lower.Contains("legendary"))
            {
                return "legendary";
            }

            return lower;
        }
    }
}
