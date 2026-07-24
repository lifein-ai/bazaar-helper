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
        public const string ScreenModeEvents = "events";
        public const string ScreenModeShop = "shop";
        public const string ScreenModeMonsterChoices = "monster_choices";
        public static ManualLogSource Logger;
        public static object LatestGameStateSnapshot;
        public static string LatestGameStateSource;
        public static string LatestGameStateSummary;
        public static object NetMessageProcessor;
        public static int? LatestGold;
        public static int? LatestHealth;
        public static float LastResourceUpdateAt;
        public static string LastResourceSource;
        public static bool? ShopRefreshAvailable;
        public static int? ShopRefreshCost;
        public static int? ShopRefreshesRemaining;
        public static string CurrentScreenMode;
        public static float LastScreenModeAt;
        private static string LastLoggedShopRefresh;
        private static readonly object ResourcesLock = new object();
        private static readonly object CapturedCardsLock = new object();
        private static readonly Dictionary<string, CapturedCardEntry> CapturedCardsByInstanceId = new Dictionary<string, CapturedCardEntry>();
        private static List<CardSnapshot> CurrentVisibleCards = new List<CardSnapshot>();
        private static float CurrentVisibleCardsSeenAt;
        private static UiScanDebugSnapshot LastUiScanDebug = new UiScanDebugSnapshot();

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
                ShopRefreshAvailable = null;
                ShopRefreshCost = null;
                ShopRefreshesRemaining = null;
                CurrentScreenMode = null;
                LastScreenModeAt = 0f;
            }

            lock (CapturedCardsLock)
            {
                CapturedCardsByInstanceId.Clear();
                CurrentVisibleCards.Clear();
                CurrentVisibleCardsSeenAt = 0f;
                LastUiScanDebug = new UiScanDebugSnapshot();
            }

            Logger?.LogInfo("Cleared runtime resource and UI card caches for new run.");
        }

        public static void ClearTransientUiState(string reason)
        {
            ClearShopRefresh();
            lock (ResourcesLock)
            {
                CurrentScreenMode = null;
                LastScreenModeAt = 0f;
            }

            lock (CapturedCardsLock)
            {
                CapturedCardsByInstanceId.Clear();
                CurrentVisibleCards.Clear();
                CurrentVisibleCardsSeenAt = 0f;
                LastUiScanDebug = new UiScanDebugSnapshot();
            }

            Logger?.LogInfo(
                "Cleared transient UI card caches"
                + (string.IsNullOrEmpty(reason) ? "." : ": " + reason));
        }

        public static void UpdateShopRefresh(
            bool? available,
            int? cost,
            int? remaining)
        {
            ShopRefreshAvailable = available;
            ShopRefreshCost = cost;
            ShopRefreshesRemaining = remaining;
            bool usableShopRefreshState =
                available == true
                || (cost.HasValue && cost.Value >= 0)
                || remaining.HasValue;
            if (usableShopRefreshState)
            {
                SetScreenMode(ScreenModeShop, "reroll_state");
            }
            string signature =
                (available.HasValue ? available.Value.ToString() : "null")
                + "/"
                + (cost.HasValue ? cost.Value.ToString() : "null")
                + "/"
                + (remaining.HasValue ? remaining.Value.ToString() : "null");
            if (!string.Equals(LastLoggedShopRefresh, signature, StringComparison.Ordinal))
            {
                LastLoggedShopRefresh = signature;
                Logger?.LogInfo(
                    "Captured reroll state available="
                    + (available.HasValue ? available.Value.ToString() : "null")
                    + " cost="
                    + (cost.HasValue ? cost.Value.ToString() : "null")
                    + " remaining="
                    + (remaining.HasValue ? remaining.Value.ToString() : "null"));
            }
        }

        public static void ClearShopRefresh()
        {
            ShopRefreshAvailable = null;
            ShopRefreshCost = null;
            ShopRefreshesRemaining = null;
            LastLoggedShopRefresh = null;
        }

        public static void SetScreenMode(string mode, string source)
        {
            if (string.IsNullOrEmpty(mode))
            {
                return;
            }

            bool changed = !string.Equals(CurrentScreenMode, mode, StringComparison.Ordinal);
            CurrentScreenMode = mode;
            LastScreenModeAt = Time.unscaledTime;
            if (changed)
            {
                Logger?.LogInfo("Screen mode=" + mode + " source=" + source);
            }
        }

        public static string GetScreenMode(float maxAgeSeconds)
        {
            if (string.IsNullOrEmpty(CurrentScreenMode))
            {
                return null;
            }
            return Time.unscaledTime - LastScreenModeAt <= maxAgeSeconds
                ? CurrentScreenMode
                : null;
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
                    || existing.Card.card_type != card.card_type
                    || existing.Card.ui_context != card.ui_context
                    || existing.Card.price != card.price;

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
            return GetCapturedUiCards(maxAgeSeconds, 0f);
        }

        public static List<CardSnapshot> GetCapturedUiCards(
            float maxAgeSeconds,
            float minSeenAt)
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
                        if (item.Value.LastSeenAt >= minSeenAt)
                        {
                            result.Add(item.Value.Card);
                        }
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

        public static List<CardSnapshot> GetLatestOpponentItemSocketCards(
            float maxAgeSeconds)
        {
            return GetLatestOpponentItemSocketCards(maxAgeSeconds, 0f);
        }

        public static List<CardSnapshot> GetLatestOpponentItemSocketCards(
            float maxAgeSeconds,
            float minSeenAt)
        {
            float now = Time.unscaledTime;
            Dictionary<string, CapturedCardEntry> latestBySocket =
                new Dictionary<string, CapturedCardEntry>();
            lock (CapturedCardsLock)
            {
                foreach (CapturedCardEntry entry in CapturedCardsByInstanceId.Values)
                {
                    CardSnapshot card = entry.Card;
                    string context = card == null ? "" : card.ui_context ?? "";
                    int start = context.IndexOf(
                        "OpponentItemSocket_",
                        StringComparison.OrdinalIgnoreCase);
                    if (start < 0
                        || now - entry.LastSeenAt > maxAgeSeconds
                        || entry.LastSeenAt < minSeenAt)
                    {
                        continue;
                    }
                    int end = context.IndexOf('/', start);
                    string socket = end < 0
                        ? context.Substring(start)
                        : context.Substring(start, end - start);
                    CapturedCardEntry existing;
                    if (!latestBySocket.TryGetValue(socket, out existing)
                        || entry.LastSeenAt > existing.LastSeenAt)
                    {
                        latestBySocket[socket] = entry;
                    }
                }
            }
            return latestBySocket
                .OrderBy(item => item.Key)
                .Select(item => item.Value.Card)
                .ToList();
        }

        public static CardSnapshot GetLatestMerchantCard(
            float maxAgeSeconds,
            float minSeenAt)
        {
            float now = Time.unscaledTime;
            CapturedCardEntry latest = null;
            lock (CapturedCardsLock)
            {
                foreach (CapturedCardEntry entry in CapturedCardsByInstanceId.Values)
                {
                    CardSnapshot card = entry.Card;
                    if (card == null
                        || now - entry.LastSeenAt > maxAgeSeconds
                        || entry.LastSeenAt < minSeenAt)
                    {
                        continue;
                    }

                    string context = card.ui_context ?? "";
                    string cardType = card.card_type ?? "";
                    bool merchantPortrait = context.IndexOf(
                        "OpponentPortraitSocketMerchant",
                        StringComparison.OrdinalIgnoreCase) >= 0;
                    bool merchantEncounter = cardType.IndexOf(
                            "EventEncounter",
                            StringComparison.OrdinalIgnoreCase) >= 0
                        && context.IndexOf(
                            "Merchant",
                            StringComparison.OrdinalIgnoreCase) >= 0;
                    if (!merchantPortrait && !merchantEncounter)
                    {
                        continue;
                    }

                    if (latest == null || entry.LastSeenAt > latest.LastSeenAt)
                    {
                        latest = entry;
                    }
                }
            }

            return latest == null ? null : latest.Card;
        }

        public static void SetCurrentVisibleCards(List<CardSnapshot> cards)
        {
            lock (CapturedCardsLock)
            {
                CurrentVisibleCards = cards == null
                    ? new List<CardSnapshot>()
                    : new List<CardSnapshot>(cards);
                CurrentVisibleCardsSeenAt = Time.unscaledTime;
            }
        }

        public static List<CardSnapshot> GetCurrentVisibleCards()
        {
            return GetCurrentVisibleCards(30f, 0f);
        }

        public static List<CardSnapshot> GetCurrentVisibleCards(
            float maxAgeSeconds,
            float minSeenAt)
        {
            lock (CapturedCardsLock)
            {
                float now = Time.unscaledTime;
                if (CurrentVisibleCardsSeenAt <= 0f
                    || now - CurrentVisibleCardsSeenAt > maxAgeSeconds
                    || CurrentVisibleCardsSeenAt < minSeenAt)
                {
                    return new List<CardSnapshot>();
                }
                return new List<CardSnapshot>(CurrentVisibleCards);
            }
        }

        public static void SetLastUiScanDebug(UiScanDebugSnapshot debug)
        {
            lock (CapturedCardsLock)
            {
                LastUiScanDebug = CloneUiScanDebug(debug);
            }
        }

        public static UiScanDebugSnapshot GetLastUiScanDebug()
        {
            lock (CapturedCardsLock)
            {
                return CloneUiScanDebug(LastUiScanDebug);
            }
        }

        private static UiScanDebugSnapshot CloneUiScanDebug(UiScanDebugSnapshot debug)
        {
            UiScanDebugSnapshot clone = new UiScanDebugSnapshot();
            if (debug == null)
            {
                return clone;
            }

            clone.card_controller_total = debug.card_controller_total;
            clone.active_card_controller_count = debug.active_card_controller_count;
            clone.ui_snapshot_success_count = debug.ui_snapshot_success_count;
            clone.ui_snapshot_failed_count = debug.ui_snapshot_failed_count;
            if (debug.captured_cards != null)
            {
                foreach (CapturedCardDebugSnapshot card in debug.captured_cards)
                {
                    if (card == null)
                    {
                        continue;
                    }
                    clone.captured_cards.Add(new CapturedCardDebugSnapshot
                    {
                        name = card.name,
                        id = card.id,
                        template_id = card.template_id,
                        card_type = card.card_type,
                        section = card.section,
                        ui_context = card.ui_context,
                        classification = card.classification,
                        ignore_reason = card.ignore_reason,
                    });
                }
            }
            return clone;
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
                RuntimeStateCache.LatestGameStateSource = "harmony_specific";
                RuntimeStateCache.LatestGameStateSummary = StateProbe.DescribeGameStateDto(data);
                RuntimeStateCache.Logger?.LogInfo("Captured NetMessageGameStateSync via Harmony patch.");
                Plugin.RequestEventExport("game_state_sync");
            }
        }
    }

    [HarmonyPatch]
    public static class NetMessageReceiveOrQueuePatch
    {
        public static MethodBase TargetMethod()
        {
            Type processorType = AccessTools.TypeByName("TheBazaar.NetMessageProcessor");
            Type messageType = AccessTools.TypeByName("BazaarGameShared.Infra.Messages.INetMessage");
            if (processorType == null || messageType == null)
            {
                RuntimeStateCache.Logger?.LogWarning("Could not find NetMessageProcessor.ReceiveOrQueue for live state patching.");
                return null;
            }

            return AccessTools.Method(processorType, "ReceiveOrQueue", new[] { messageType });
        }

        public static void Prefix(object __instance, object __0)
        {
            RuntimeStateCache.NetMessageProcessor = __instance;
            object dto = StateProbe.TryGetGameStateDtoFromMessage(__0);
            if (dto == null)
            {
                return;
            }

            RuntimeStateCache.LatestGameStateSnapshot = dto;
            RuntimeStateCache.LatestGameStateSource = "receive_or_queue";
            RuntimeStateCache.LatestGameStateSummary = StateProbe.DescribeGameStateDto(dto);
            RuntimeStateCache.Logger?.LogInfo(
                "Captured live GameStateSnapshotDTO via NetMessageProcessor.ReceiveOrQueue. "
                + RuntimeStateCache.LatestGameStateSummary);
            Plugin.RequestEventExport("receive_or_queue_game_state");
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

        public static void Prefix(object __instance, object __0)
        {
            RuntimeStateCache.NetMessageProcessor = __instance;
            object message = __0;
            try
            {
                object gameStateDto = StateProbe.TryGetGameStateDtoFromMessage(message);
                if (gameStateDto != null)
                {
                    RuntimeStateCache.LatestGameStateSnapshot = gameStateDto;
                    RuntimeStateCache.LatestGameStateSource = "harmony_generic";
                    RuntimeStateCache.LatestGameStateSummary = StateProbe.DescribeGameStateDto(gameStateDto);
                    RuntimeStateCache.Logger?.LogInfo(
                        "Captured NetMessageGameStateSync via generic Handle patch.");
                }

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
            Plugin.RequestEventExport("resource_message");
        }

        public static void Postfix(object __instance)
        {
            RuntimeStateCache.NetMessageProcessor = __instance;
            if (RuntimeStateCache.LatestGameStateSnapshot == null)
            {
                object dto = StateProbe.TryReadLatestGameStateFromProcessor(__instance);
                if (dto != null)
                {
                    RuntimeStateCache.LatestGameStateSnapshot = dto;
                    RuntimeStateCache.LatestGameStateSource = "processor_postfix_recovery";
                    RuntimeStateCache.LatestGameStateSummary = StateProbe.DescribeGameStateDto(dto);
                    RuntimeStateCache.Logger?.LogInfo(
                        "Recovered initial GameStateSnapshotDTO from current NetMessageProcessor.");
                }
            }

            Plugin.RequestEventExport("processor_postfix");
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

            if (LooksLikeCurrentResourceContainer(value))
            {
                // Attribute dictionaries are the canonical source and are checked before named members.
                object attributes = SafeGetMember(value, "Attributes");
                ExtractAttributeCollection(attributes, ref gold, ref health);

                ExtractNamedValue(value, "Gold", ref gold);
                ExtractNamedValue(value, "CurrentGold", ref gold);
                ExtractNamedValue(value, "Health", ref health);
                ExtractNamedValue(value, "CurrentHealth", ref health);
            }

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
                    if (IsGoldAttributeKey(keyText)
                        && TryInt(value, out parsed))
                    {
                        gold = parsed;
                    }
                    if (IsHealthAttributeKey(keyText)
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

        private static bool LooksLikeCurrentResourceContainer(object value)
        {
            if (value == null)
            {
                return false;
            }

            string typeName = value.GetType().FullName ?? value.GetType().Name ?? "";
            string lower = typeName.ToLowerInvariant();
            if (lower.Contains("reward")
                || lower.Contains("income")
                || lower.Contains("effect")
                || lower.Contains("ability")
                || lower.Contains("encounter")
                || lower.Contains("option")
                || lower.Contains("shop")
                || lower.Contains("price")
                || lower.Contains("cost")
                || lower.Contains("card")
                || lower.Contains("item"))
            {
                return false;
            }

            return lower.Contains("player")
                || lower.Contains("wallet")
                || lower.Contains("currency")
                || lower.Contains("resource")
                || lower.Contains("gamestate")
                || lower.Contains("game_state")
                || lower.Contains("runstate")
                || lower.Contains("state");
        }

        private static bool IsGoldAttributeKey(string key)
        {
            return EqualsKey(key, "Gold")
                || EqualsKey(key, "CurrentGold")
                || EqualsKey(key, "Current Gold")
                || EqualsKey(key, "Coins")
                || EqualsKey(key, "Coin");
        }

        private static bool IsHealthAttributeKey(string key)
        {
            return EqualsKey(key, "Health")
                || EqualsKey(key, "CurrentHealth")
                || EqualsKey(key, "Current Health");
        }

        private static bool EqualsKey(string left, string right)
        {
            return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
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
    public static class RerollButtonPatch
    {
        public static IEnumerable<MethodBase> TargetMethods()
        {
            Type type = AccessTools.TypeByName("TheBazaar.RerollButton");
            if (type == null)
            {
                return Enumerable.Empty<MethodBase>();
            }
            return new[] { "OnEnable", "OnDisable", "UpdateView", "OnRerollCostChanged" }
                .Select(name => AccessTools.Method(type, name))
                .Where(method => method != null)
                .Cast<MethodBase>()
                .ToArray();
        }

        public static void Postfix(object __instance, MethodBase __originalMethod)
        {
            if (__originalMethod != null && __originalMethod.Name == "OnDisable")
            {
                RuntimeStateCache.ClearShopRefresh();
                Plugin.RequestEventExport("shop_refresh_hidden");
                return;
            }
            Type type = __instance.GetType();
            bool? enabled = ReadBool(type, __instance, "IsEnabled");
            bool? canInteract = InvokeBool(type, __instance, "CanInteract");
            bool? canAfford = InvokeBool(type, __instance, "CanAffordReroll");
            RuntimeStateCache.UpdateShopRefresh(
                CombineAvailable(enabled, canInteract, canAfford),
                ReadInt(type, __instance, "_rerollCost"),
                ReadInt(type, __instance, "_rerollsRemaining"));
            Plugin.RequestEventExport("shop_refresh");
        }

        private static int? ReadInt(Type type, object instance, string name)
        {
            PropertyInfo property = AccessTools.Property(type, name);
            object value = property == null ? null : property.GetValue(instance, null);
            if (value == null)
            {
                return null;
            }
            try
            {
                return Convert.ToInt32(value);
            }
            catch
            {
                return null;
            }
        }

        private static bool? ReadBool(Type type, object instance, string name)
        {
            PropertyInfo property = AccessTools.Property(type, name);
            object value = property == null ? null : property.GetValue(instance, null);
            return value is bool ? (bool?)value : null;
        }

        private static bool? InvokeBool(Type type, object instance, string name)
        {
            MethodInfo method = AccessTools.Method(type, name);
            if (method == null || method.GetParameters().Length != 0)
            {
                return null;
            }
            try
            {
                object value = method.Invoke(instance, null);
                return value is bool ? (bool?)value : null;
            }
            catch
            {
                return null;
            }
        }

        private static bool? CombineAvailable(params bool?[] values)
        {
            if (values.Any(value => value == false))
            {
                return false;
            }
            return values.Any(value => value.HasValue) ? (bool?)true : null;
        }
    }

    [HarmonyPatch]
    public static class CardControllerUpdatePriceTagPatch
    {
        public static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName("CardController");
            return type == null ? null : AccessTools.Method(type, "UpdatePriceTag");
        }

        public static void Postfix(object __instance)
        {
            UiCardCapture.TryCapture(__instance, "update_price_tag");
        }
    }

    [HarmonyPatch]
    public static class CardControllerOnEnablePatch
    {
        public static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName("CardController");
            return type == null ? null : AccessTools.Method(type, "OnEnable");
        }

        public static void Postfix(object __instance)
        {
            UiCardCapture.TryCapture(__instance, "on_enable");
        }
    }

    [HarmonyPatch]
    public static class CardControllerSetCardDataPatch
    {
        public static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName("CardController");
            return type == null ? null : AccessTools.Method(type, "SetCardData");
        }

        public static void Postfix(object __instance)
        {
            UiCardCapture.TryCapture(__instance, "set_card_data");
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
        public static CardSnapshot TryBuildSnapshot(object controller, string source)
        {
            try
            {
                return BuildCardSnapshot(controller, source);
            }
            catch (Exception ex)
            {
                RuntimeStateCache.Logger?.LogDebug("UI card snapshot failed: " + ex.Message);
                return null;
            }
        }

        public static void TryCapture(object controller, string source)
        {
            try
            {
                CardSnapshot card = TryBuildSnapshot(controller, source);
                bool changed = RuntimeStateCache.RecordUiCard(card);
                if (IsCurrentEventOptionCard(card))
                {
                    RuntimeStateCache.ClearShopRefresh();
                    RuntimeStateCache.SetScreenMode(
                        RuntimeStateCache.ScreenModeEvents,
                        source);
                    changed = true;
                }
                else if (IsMonsterChoiceCard(card))
                {
                    RuntimeStateCache.ClearShopRefresh();
                    RuntimeStateCache.SetScreenMode(
                        RuntimeStateCache.ScreenModeMonsterChoices,
                        source);
                    changed = true;
                }
                else if (IsShopOfferCard(card))
                {
                    RuntimeStateCache.SetScreenMode(
                        RuntimeStateCache.ScreenModeShop,
                        source);
                    changed = true;
                }
                if (RuntimeStateCache.LatestGameStateSnapshot == null)
                {
                    object dto = StateProbe.TryRecoverInitialGameState();
                    if (dto != null)
                    {
                        RuntimeStateCache.LatestGameStateSnapshot = dto;
                        RuntimeStateCache.Logger?.LogInfo(
                            "Recovered initial game state after first live card event.");
                    }
                }
                if (changed)
                {
                    Plugin.RequestEventExport("ui_card_changed");
                }
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
                        + card.section
                        + " context="
                        + card.ui_context);
                }
            }
            catch (Exception ex)
            {
                RuntimeStateCache.Logger?.LogDebug("UI card capture failed: " + ex.Message);
            }
        }

        private static bool IsCurrentEventOptionCard(CardSnapshot card)
        {
            if (card == null || string.IsNullOrEmpty(card.id))
            {
                return false;
            }

            string cardType = card.card_type ?? "";
            bool eventEncounter = cardType.IndexOf(
                    "EventEncounter",
                    StringComparison.OrdinalIgnoreCase) >= 0
                || card.id.StartsWith("enc_", StringComparison.OrdinalIgnoreCase);
            if (!eventEncounter)
            {
                return false;
            }

            string context = card.ui_context ?? "";
            return context.IndexOf("Merchant", StringComparison.OrdinalIgnoreCase) < 0
                && context.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) < 0;
        }

        private static bool IsMonsterChoiceCard(CardSnapshot card)
        {
            if (card == null || string.IsNullOrEmpty(card.id))
            {
                return false;
            }

            string cardType = card.card_type ?? "";
            bool combatEncounter = cardType.IndexOf(
                    "CombatEncounter",
                    StringComparison.OrdinalIgnoreCase) >= 0
                || card.id.StartsWith("com_", StringComparison.OrdinalIgnoreCase);
            if (!combatEncounter)
            {
                return false;
            }

            string context = card.ui_context ?? "";
            return context.IndexOf("OpponentBoardAnchor", StringComparison.OrdinalIgnoreCase) >= 0
                && context.IndexOf("Merchant", StringComparison.OrdinalIgnoreCase) < 0
                && context.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) < 0;
        }

        private static bool IsShopOfferCard(CardSnapshot card)
        {
            if (card == null
                || string.IsNullOrEmpty(card.id)
                || !IsShopOfferCardType(card.card_type))
            {
                return false;
            }

            string section = card.section ?? "";
            string context = card.ui_context ?? "";
            if (section.IndexOf("Reward", StringComparison.OrdinalIgnoreCase) >= 0
                || section.IndexOf("Selection", StringComparison.OrdinalIgnoreCase) >= 0
                || string.Equals(section, "Hand", StringComparison.OrdinalIgnoreCase)
                || string.Equals(section, "Stash", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            return section.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0
                || context.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0
                || context.IndexOf("Merchant", StringComparison.OrdinalIgnoreCase) >= 0
                || context.IndexOf("OpponentItemSocket_", StringComparison.OrdinalIgnoreCase) >= 0
                || context.IndexOf("OpponentPortraitSocketMerchant", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool IsShopOfferCardType(string cardType)
        {
            return string.Equals(cardType, "Item", StringComparison.OrdinalIgnoreCase)
                || string.Equals(cardType, "Skill", StringComparison.OrdinalIgnoreCase);
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
                ui_context = GetUiContext(controller),
                price = string.Equals(
                    StringValue(GetProperty(cardData, "Type")),
                    "Skill",
                    StringComparison.OrdinalIgnoreCase)
                    ? null
                    : GetCurrentPrice(controller),
            };

            if (HasValue(enchantment))
            {
                card.enchantments.Add(StringValue(enchantment));
            }

            RuntimeCardInstanceReader.AddRuntimeSnapshot(
                card,
                cardData,
                "ui_card_data");
            RuntimeCardInstanceReader.AddRuntimeSnapshot(
                card,
                controller,
                "ui_controller");
            CardSnapshotPosition.Fill(card);

            return card;
        }

        private static int? GetCurrentPrice(object controller)
        {
            object priceContainer = GetProperty(controller, "ActivePriceContainer");
            if (priceContainer == null)
            {
                return null;
            }
            FieldInfo currentPriceField = priceContainer.GetType().GetField(
                "currentPrice",
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            object textComponent = currentPriceField == null
                ? null
                : currentPriceField.GetValue(priceContainer);
            object rawText = GetProperty(textComponent, "text");
            string text = StringValue(rawText);
            if (string.IsNullOrEmpty(text))
            {
                return null;
            }
            string digits = new string(text.Where(char.IsDigit).ToArray());
            int value;
            return int.TryParse(digits, out value) ? (int?)value : null;
        }

        private static string GetUiContext(object controller)
        {
            Component component = controller as Component;
            if (component == null || component.transform == null)
            {
                return null;
            }

            List<string> names = new List<string>();
            Transform current = component.transform;
            for (int depth = 0; current != null && depth < 12; depth++)
            {
                names.Add(current.name ?? "");
                current = current.parent;
            }
            return string.Join("/", names.ToArray());
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
