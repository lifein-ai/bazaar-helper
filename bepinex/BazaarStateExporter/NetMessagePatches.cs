using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

namespace BazaarStateExporter
{
    public static class RuntimeStateCache
    {
        public static ManualLogSource Logger;
        public static object LatestGameStateSnapshot;
        public static int? LatestGold;
        public static int? LatestHealth;
        public static float LastResourceUpdateAt;
        public static string LastResourceSource;
        private static readonly object ResourcesLock = new object();
        private static readonly object CapturedCardsLock = new object();
        private static readonly Dictionary<string, CapturedCardEntry> CapturedCardsByInstanceId = new Dictionary<string, CapturedCardEntry>();

        public static void UpdateResources(int? gold, int? health, string source)
        {
            if (!gold.HasValue && !health.HasValue)
            {
                return;
            }

            lock (ResourcesLock)
            {
                // SnapshotFromGameStateDto runs repeatedly over the cached DTO. Once a live
                // message has supplied resources, that old DTO may only fill missing values.
                bool cachedDtoReplay = string.Equals(source, "game_state_sync", StringComparison.Ordinal)
                    && !string.IsNullOrEmpty(LastResourceSource)
                    && !string.Equals(LastResourceSource, "game_state_sync", StringComparison.Ordinal);
                int? acceptedGold = cachedDtoReplay && LatestGold.HasValue ? null : gold;
                int? acceptedHealth = cachedDtoReplay && LatestHealth.HasValue ? null : health;
                if (!acceptedGold.HasValue && !acceptedHealth.HasValue)
                {
                    return;
                }

                bool changed = (acceptedGold.HasValue && LatestGold != acceptedGold)
                    || (acceptedHealth.HasValue && LatestHealth != acceptedHealth);

                if (acceptedGold.HasValue)
                {
                    LatestGold = acceptedGold;
                }
                if (acceptedHealth.HasValue)
                {
                    LatestHealth = acceptedHealth;
                }

                LastResourceUpdateAt = Time.unscaledTime;
                LastResourceSource = source;

                if (changed)
                {
                    Logger?.LogInfo(
                        "Updated resources source="
                        + source
                        + " gold="
                        + (LatestGold.HasValue ? LatestGold.Value.ToString() : "null")
                        + " health="
                        + (LatestHealth.HasValue ? LatestHealth.Value.ToString() : "null"));
                }
            }
        }

        public static void ResetForNewRun()
        {
            lock (ResourcesLock)
            {
                LatestGold = null;
                LatestHealth = null;
                LastResourceUpdateAt = 0f;
                LastResourceSource = null;
            }

            lock (CapturedCardsLock)
            {
                CapturedCardsByInstanceId.Clear();
            }

            Logger?.LogInfo("Cleared runtime resource and UI card caches for new run.");
        }

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
            object data = null;
            try
            {
                data = dataProperty == null ? null : dataProperty.GetValue(message, null);
            }
            catch (Exception ex)
            {
                RuntimeStateCache.Logger?.LogDebug("Could not read NetMessageGameStateSync.Data: " + ex.Message);
            }
            if (data != null)
            {
                RuntimeStateCache.LatestGameStateSnapshot = data;
                RuntimeStateCache.Logger?.LogInfo("Captured NetMessageGameStateSync via Harmony patch.");
            }
        }
    }

    [HarmonyPatch]
    public static class NetMessageResourcePatch
    {
        public static IEnumerable<MethodBase> TargetMethods()
        {
            Type processorType = AccessTools.TypeByName("TheBazaar.NetMessageProcessor");
            if (processorType == null)
            {
                RuntimeStateCache.Logger?.LogWarning("Could not find NetMessageProcessor for resource patching.");
                return Enumerable.Empty<MethodBase>();
            }

            return processorType
                .GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
                .Where(method =>
                {
                    ParameterInfo[] parameters = method.GetParameters();
                    return method.Name == "Handle"
                        && parameters.Length == 1
                        && (parameters[0].ParameterType.FullName ?? "").IndexOf(
                            "NetMessage",
                            StringComparison.OrdinalIgnoreCase) >= 0;
                })
                .Cast<MethodBase>()
                .ToArray();
        }

        public static void Prefix(object __0)
        {
            object message = __0;
            try
            {
                int? gold;
                int? health;
                if (ResourceReflection.TryExtract(message, out gold, out health))
                {
                    RuntimeStateCache.UpdateResources(gold, health, message == null ? "net_message" : message.GetType().Name);
                }

                object data = ResourceReflection.SafeGetMember(message, "Data");
                if (data != null && ResourceReflection.TryExtract(data, out gold, out health))
                {
                    RuntimeStateCache.UpdateResources(gold, health, message.GetType().Name + ".Data");
                }
            }
            catch (Exception ex)
            {
                RuntimeStateCache.Logger?.LogDebug("Resource message capture failed: " + ex.Message);
            }
        }
    }

    internal static class ResourceReflection
    {
        private const int MaxDepth = 4;

        public static bool TryExtract(object value, out int? gold, out int? health)
        {
            gold = null;
            health = null;
            HashSet<object> visited = new HashSet<object>(ReferenceComparer.Instance);
            Extract(value, 0, visited, ref gold, ref health);
            return gold.HasValue || health.HasValue;
        }

        public static object SafeGetMember(object target, string name)
        {
            if (target == null)
            {
                return null;
            }

            Type type = target.GetType();
            try
            {
                PropertyInfo property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (property != null && property.GetIndexParameters().Length == 0)
                {
                    return property.GetValue(target, null);
                }
            }
            catch
            {
            }

            try
            {
                FieldInfo field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                return field == null ? null : field.GetValue(target);
            }
            catch
            {
                return null;
            }
        }

        private static void Extract(
            object value,
            int depth,
            HashSet<object> visited,
            ref int? gold,
            ref int? health)
        {
            if (value == null || depth > MaxDepth || (gold.HasValue && health.HasValue))
            {
                return;
            }

            Type type = value.GetType();
            if (!type.IsValueType && !visited.Add(value))
            {
                return;
            }

            // Attribute dictionaries are the canonical source and are checked before named members.
            object attributes = SafeGetMember(value, "Attributes");
            ExtractAttributeCollection(attributes, ref gold, ref health);

            ExtractNamedValue(value, "Gold", ref gold);
            ExtractNamedValue(value, "CurrentGold", ref gold);
            ExtractNamedValue(value, "Health", ref health);
            ExtractNamedValue(value, "CurrentHealth", ref health);

            if (gold.HasValue && health.HasValue)
            {
                return;
            }

            foreach (object child in SafeChildren(value))
            {
                Extract(child, depth + 1, visited, ref gold, ref health);
                if (gold.HasValue && health.HasValue)
                {
                    return;
                }
            }
        }

        private static void ExtractAttributeCollection(object attributes, ref int? gold, ref int? health)
        {
            IEnumerable items = attributes as IEnumerable;
            if (items == null || attributes is string)
            {
                return;
            }

            try
            {
                foreach (object item in items)
                {
                    object key = SafeGetMember(item, "Key");
                    object value = SafeGetMember(item, "Value");
                    string keyText = SafeString(key);
                    int parsed;
                    if (keyText.IndexOf("Gold", StringComparison.OrdinalIgnoreCase) >= 0
                        && TryInt(value, out parsed))
                    {
                        gold = parsed;
                    }
                    if (keyText.IndexOf("Health", StringComparison.OrdinalIgnoreCase) >= 0
                        && TryInt(value, out parsed))
                    {
                        health = parsed;
                    }
                }
            }
            catch
            {
            }
        }

        private static void ExtractNamedValue(object target, string name, ref int? destination)
        {
            if (destination.HasValue)
            {
                return;
            }

            int parsed;
            if (TryInt(SafeGetMember(target, name), out parsed))
            {
                destination = parsed;
            }
        }

        private static IEnumerable<object> SafeChildren(object value)
        {
            Type type = value.GetType();
            if (IsTerminal(type) || value is IEnumerable)
            {
                yield break;
            }

            BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            FieldInfo[] fields;
            PropertyInfo[] properties;
            try
            {
                fields = type.GetFields(flags);
                properties = type.GetProperties(flags);
            }
            catch
            {
                yield break;
            }

            foreach (FieldInfo field in fields)
            {
                object child = null;
                try
                {
                    child = field.GetValue(value);
                }
                catch
                {
                }
                if (child != null)
                {
                    yield return child;
                }
            }

            foreach (PropertyInfo property in properties)
            {
                if (property.GetIndexParameters().Length != 0)
                {
                    continue;
                }

                object child = null;
                try
                {
                    child = property.GetValue(value, null);
                }
                catch
                {
                }
                if (child != null)
                {
                    yield return child;
                }
            }
        }

        private static bool IsTerminal(Type type)
        {
            return type.IsPrimitive
                || type.IsEnum
                || type == typeof(string)
                || type == typeof(decimal)
                || type == typeof(DateTime)
                || type == typeof(Guid);
        }

        private static bool TryInt(object value, out int result)
        {
            try
            {
                result = Convert.ToInt32(value);
                return value != null;
            }
            catch
            {
                result = 0;
                return false;
            }
        }

        private static string SafeString(object value)
        {
            try
            {
                return value == null ? "" : value.ToString() ?? "";
            }
            catch
            {
                return "";
            }
        }

        private sealed class ReferenceComparer : IEqualityComparer<object>
        {
            public static readonly ReferenceComparer Instance = new ReferenceComparer();

            public new bool Equals(object left, object right)
            {
                return ReferenceEquals(left, right);
            }

            public int GetHashCode(object value)
            {
                return RuntimeHelpers.GetHashCode(value);
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
