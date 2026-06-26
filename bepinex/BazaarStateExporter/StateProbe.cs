using BepInEx.Logging;
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using UnityEngine;

namespace BazaarStateExporter
{
    public sealed class StateProbe
    {
        private readonly ManualLogSource logger;
        private bool warnedOnce;

        public StateProbe(ManualLogSource logger)
        {
            this.logger = logger;
        }

        public GameStateSnapshot TryReadCurrentState()
        {
            object dto = TryReadLatestGameStateFromProcessorHistory() 
                ?? RuntimeStateCache.LatestGameStateSnapshot;

            if (dto == null)
            {
                if (!warnedOnce)
                {
                    logger.LogInfo("Waiting for NetMessageGameStateSync.");
                    warnedOnce = true;
                }

                return null;
            }

            return SnapshotFromGameStateDto(dto);
        }

        private object TryReadLatestGameStateFromProcessorHistory()
        {
            MonoBehaviour[] behaviours = Resources.FindObjectsOfTypeAll<MonoBehaviour>();
            foreach (MonoBehaviour behaviour in behaviours)
            {
                if (behaviour == null)
                {
                    continue;
                }

                Type type = behaviour.GetType();
                if (type.FullName != "TheBazaar.NetMessageProcessor")
                {
                    continue;
                }

                object lastMessage = GetField(behaviour, "_lastMessage");
                object dto = TryGetDataFromGameStateMessage(lastMessage);
                if (dto != null)
                {
                    RuntimeStateCache.LatestGameStateSnapshot = dto;
                    logger.LogInfo("Recovered GameStateSnapshotDTO from NetMessageProcessor._lastMessage.");
                    return dto;
                }

                IList messages = GetField(behaviour, "_lastMessages") as IList;
                if (messages == null)
                {
                    continue;
                }

                for (int index = messages.Count - 1; index >= 0; index--)
                {
                    dto = TryGetDataFromGameStateMessage(messages[index]);
                    if (dto != null)
                    {
                        RuntimeStateCache.LatestGameStateSnapshot = dto;
                        logger.LogInfo("Recovered GameStateSnapshotDTO from NetMessageProcessor._lastMessages.");
                        return dto;
                    }
                }
            }

            return null;
        }

        private static object TryGetDataFromGameStateMessage(object message)
        {
            if (message == null)
            {
                return null;
            }

            Type type = message.GetType();
            if (type.FullName != "BazaarGameShared.Infra.Messages.NetMessageGameStateSync")
            {
                return null;
            }

            return GetProperty(message, "Data");
        }

        public void LogRuntimeHints()
        {
            logger.LogInfo("Runtime inspection started.");
            LogLoadedAssemblies();
            LogLikelyMonoBehaviours();
            logger.LogInfo("Runtime inspection finished.");
        }

        public void ScanVisibleUiCards()
        {
            MonoBehaviour[] behaviours = Resources.FindObjectsOfTypeAll<MonoBehaviour>();
            foreach (MonoBehaviour behaviour in behaviours)
            {
                if (behaviour == null)
                {
                    continue;
                }

                Type type = behaviour.GetType();
                if (type.FullName != "CardController"
                    && type.BaseType != null
                    && type.BaseType.FullName != "CardController")
                {
                    continue;
                }

                bool isVisible = BoolValue(GetProperty(behaviour, "IsCardVisible"))
                    || behaviour.gameObject.activeInHierarchy;
                if (!isVisible)
                {
                    continue;
                }

                UiCardCapture.TryCapture(behaviour, "visible_scan");
            }
        }

        private void LogLoadedAssemblies()
        {
            Assembly[] assemblies = AppDomain.CurrentDomain.GetAssemblies();
            foreach (Assembly assembly in assemblies.OrderBy(item => item.GetName().Name))
            {
                string name = assembly.GetName().Name;
                if (LooksInteresting(name))
                {
                    logger.LogInfo("[Asm] " + assembly.FullName);
                }
            }

            foreach (Type type in FindLoadedTypes().Where(type => type.FullName != null && type.FullName.IndexOf("NetMessageProcessor", StringComparison.OrdinalIgnoreCase) >= 0))
            {
                logger.LogInfo("[NetMessageProcessorType] " + type.FullName + " asm=" + type.Assembly.GetName().Name);
                foreach (MethodInfo method in type.GetMethods(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic).Where(IsInterestingMessageMethod).Take(80))
                {
                    logger.LogInfo("  [Method] " + method.Name + "(" + string.Join(", ", method.GetParameters().Select(parameter => parameter.ParameterType.FullName + " " + parameter.Name).ToArray()) + ")");
                }
            }
        }

        private static IEnumerable<Type> FindLoadedTypes()
        {
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (ReflectionTypeLoadException ex)
                {
                    types = ex.Types;
                }

                foreach (Type type in types)
                {
                    if (type != null)
                    {
                        yield return type;
                    }
                }
            }
        }

        private static bool IsInterestingMessageMethod(MethodInfo method)
        {
            if (method.Name.IndexOf("Handle", StringComparison.OrdinalIgnoreCase) >= 0
                || method.Name.IndexOf("Message", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return true;
            }

            return method.GetParameters().Any(parameter => (parameter.ParameterType.FullName ?? "").IndexOf("NetMessage", StringComparison.OrdinalIgnoreCase) >= 0);
        }

        private void LogLikelyMonoBehaviours()
        {
            MonoBehaviour[] behaviours = Resources.FindObjectsOfTypeAll<MonoBehaviour>();
            int logged = 0;
            foreach (MonoBehaviour behaviour in behaviours)
            {
                if (behaviour == null)
                {
                    continue;
                }

                Type type = behaviour.GetType();
                string fullName = type.FullName ?? type.Name;
                string objectName = behaviour.name ?? "";
                if (!LooksInteresting(fullName) && !LooksInteresting(objectName))
                {
                    continue;
                }

                logger.LogInfo("[Obj] " + fullName + " name=" + objectName);
                LogMembers(type);
                logged++;
                if (logged >= 80)
                {
                    logger.LogInfo("Runtime inspection stopped after 80 objects.");
                    break;
                }
            }

            logger.LogInfo("Runtime inspection matched objects=" + logged + " totalMonoBehaviours=" + behaviours.Length);
        }

        private void LogMembers(Type type)
        {
            BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            foreach (FieldInfo field in type.GetFields(flags).Where(field => LooksInteresting(field.Name) || LooksInteresting(field.FieldType.FullName)).Take(24))
            {
                logger.LogInfo("  [Field] " + field.FieldType.FullName + " " + field.Name);
            }

            foreach (PropertyInfo property in type.GetProperties(flags).Where(property => LooksInteresting(property.Name) || LooksInteresting(property.PropertyType.FullName)).Take(24))
            {
                logger.LogInfo("  [Prop] " + property.PropertyType.FullName + " " + property.Name);
            }
        }

        private static bool LooksInteresting(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return false;
            }

            string lower = value.ToLowerInvariant();
            return lower.Contains("run")
                || lower.Contains("session")
                || lower.Contains("player")
                || lower.Contains("hero")
                || lower.Contains("shop")
                || lower.Contains("store")
                || lower.Contains("encounter")
                || lower.Contains("event")
                || lower.Contains("card")
                || lower.Contains("item")
                || lower.Contains("inventory")
                || lower.Contains("gold")
                || lower.Contains("health")
                || lower.Contains("day")
                || lower.Contains("state")
                || lower.Contains("board")
                || lower.Contains("choice")
                || lower.Contains("option");
        }

        private GameStateSnapshot SnapshotFromGameStateDto(object dto)
        {
            object run = GetField(dto, "Run");
            object currentState = GetField(dto, "CurrentState");
            object player = GetField(dto, "Player");

            GameStateSnapshot snapshot = new GameStateSnapshot
            {
                source = "bepinex",
                hero = StringValue(GetField(player, "Hero")),
                day = IntValue(GetField(run, "Day"), 1),
                event_option_ids = StringList(GetField(currentState, "SelectionSet")),
            };

            snapshot.event_options.AddRange(snapshot.event_option_ids);
            snapshot.owned_cards.AddRange(CardList(GetProperty(dto, "GetPlayerHandCards")));
            snapshot.owned_cards.AddRange(CardList(GetProperty(dto, "GetPlayerStashCards")));
            snapshot.owned_cards.AddRange(CardList(GetProperty(dto, "GetPlayerSkillsCards")));

            object allCards = GetField(dto, "Cards");
            HashSet<string> eventOptionIdSet = new HashSet<string>(snapshot.event_option_ids);
            foreach (CardSnapshot card in CardList(allCards))
            {
                if (!string.IsNullOrEmpty(card.id) && eventOptionIdSet.Contains(card.id) && !string.IsNullOrEmpty(card.template_id))
                {
                    snapshot.event_option_template_ids.Add(card.template_id);
                }

                string section = card.section ?? "";
                if (section.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0
                    || section.IndexOf("Selection", StringComparison.OrdinalIgnoreCase) >= 0
                    || section.IndexOf("Reward", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    snapshot.visible_cards.Add(card);
                }
            }
            MergeCapturedUiCards(snapshot, eventOptionIdSet);

            Dictionary<string, int> attributes = AttributeDictionary(GetField(player, "Attributes"));
            snapshot.gold = FindAttribute(attributes, "Gold");
            snapshot.health = FindAttribute(attributes, "Health");

            if (snapshot.event_option_ids.Count > 0 || snapshot.owned_cards.Count > 0)
            {
                logger.LogInfo(
                    "Captured game state hero="
                    + snapshot.hero
                    + " day="
                    + snapshot.day
                    + " options="
                    + snapshot.event_option_ids.Count
                    + "/"
                    + snapshot.event_option_template_ids.Count
                    + " owned="
                    + snapshot.owned_cards.Count
                    + " visible="
                    + snapshot.visible_cards.Count);
            }

            return snapshot;
        }

        private static void MergeCapturedUiCards(GameStateSnapshot snapshot, HashSet<string> eventOptionIdSet)
        {
            List<CardSnapshot> capturedCards = RuntimeStateCache.GetCapturedUiCards(2.0f);
            List<CardSnapshot> recentEventCards = capturedCards
                .Where(card => card != null
                    && !string.IsNullOrEmpty(card.id)
                    && ((card.card_type ?? "").IndexOf("Encounter", StringComparison.OrdinalIgnoreCase) >= 0))
                .ToList();

            if (recentEventCards.Count > 0)
            {
                snapshot.event_options.Clear();
                snapshot.event_option_ids.Clear();
                snapshot.event_option_template_ids.Clear();
                eventOptionIdSet.Clear();
            }

            HashSet<string> visibleIds = new HashSet<string>(snapshot.visible_cards.Select(card => card.id).Where(id => !string.IsNullOrEmpty(id)));
            HashSet<string> ownedIds = new HashSet<string>(snapshot.owned_cards.Select(card => card.id).Where(id => !string.IsNullOrEmpty(id)));
            HashSet<string> templateIds = new HashSet<string>(snapshot.event_option_template_ids);
            HashSet<string> eventNames = new HashSet<string>(snapshot.event_options);

            foreach (CardSnapshot card in capturedCards)
            {
                if (card == null || string.IsNullOrEmpty(card.id))
                {
                    continue;
                }

                if (eventOptionIdSet.Contains(card.id))
                {
                    if (!string.IsNullOrEmpty(card.template_id) && templateIds.Add(card.template_id))
                    {
                        snapshot.event_option_template_ids.Add(card.template_id);
                    }
                    if (!string.IsNullOrEmpty(card.name) && eventNames.Add(card.name))
                    {
                        snapshot.event_options.Add(card.name);
                    }
                    continue;
                }

                string section = card.section ?? "";
                string cardType = card.card_type ?? "";
                bool eventCard = cardType.IndexOf("Encounter", StringComparison.OrdinalIgnoreCase) >= 0;
                if (eventCard)
                {
                    if (!eventOptionIdSet.Contains(card.id))
                    {
                        eventOptionIdSet.Add(card.id);
                        snapshot.event_option_ids.Add(card.id);
                    }
                    if (!string.IsNullOrEmpty(card.template_id) && templateIds.Add(card.template_id))
                    {
                        snapshot.event_option_template_ids.Add(card.template_id);
                    }
                    if (!string.IsNullOrEmpty(card.name) && eventNames.Add(card.name))
                    {
                        snapshot.event_options.Add(card.name);
                    }
                    continue;
                }

                if ((section.IndexOf("Hand", StringComparison.OrdinalIgnoreCase) >= 0
                    || section.IndexOf("Stash", StringComparison.OrdinalIgnoreCase) >= 0)
                    && ownedIds.Add(card.id))
                {
                    CardSnapshot owned = CloneCard(card);
                    owned.source = "ui_scan";
                    snapshot.owned_cards.Add(owned);
                }

                bool visibleCandidate = section.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0
                    || section.IndexOf("Selection", StringComparison.OrdinalIgnoreCase) >= 0
                    || section.IndexOf("Reward", StringComparison.OrdinalIgnoreCase) >= 0
                    || card.source == "show";

                if (visibleCandidate && visibleIds.Add(card.id))
                {
                    snapshot.visible_cards.Add(card);
                }
            }
        }

        private static CardSnapshot CloneCard(CardSnapshot card)
        {
            CardSnapshot clone = new CardSnapshot
            {
                id = card.id,
                template_id = card.template_id,
                name = card.name,
                rarity = card.rarity,
                section = card.section,
                card_type = card.card_type,
                source = card.source,
            };
            clone.enchantments.AddRange(card.enchantments);
            return clone;
        }

        private static IEnumerable<CardSnapshot> CardList(object value)
        {
            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null)
            {
                yield break;
            }

            foreach (object item in enumerable)
            {
                if (item == null)
                {
                    continue;
                }

                object enchantment = GetField(item, "Enchantment");
                CardSnapshot card = new CardSnapshot
                {
                    id = StringValue(GetField(item, "InstanceId")),
                    template_id = StringValue(GetField(item, "TemplateId")),
                    rarity = NormalizeTier(StringValue(GetField(item, "Tier"))),
                    section = StringValue(GetField(item, "Section")),
                    card_type = StringValue(GetField(item, "Type")),
                    source = "game_state",
                };

                if (HasValue(enchantment))
                {
                    card.enchantments.Add(StringValue(enchantment));
                }

                yield return card;
            }
        }

        private static Dictionary<string, int> AttributeDictionary(object value)
        {
            Dictionary<string, int> result = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null)
            {
                return result;
            }

            foreach (object item in enumerable)
            {
                object key = GetProperty(item, "Key");
                object val = GetProperty(item, "Value");
                if (key != null && val != null)
                {
                    result[StringValue(key)] = IntValue(val, 0);
                }
            }

            return result;
        }

        private static int? FindAttribute(Dictionary<string, int> attributes, string name)
        {
            foreach (KeyValuePair<string, int> item in attributes)
            {
                if (item.Key.IndexOf(name, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return item.Value;
                }
            }

            return null;
        }

        private static object GetField(object target, string name)
        {
            if (target == null)
            {
                return null;
            }

            FieldInfo field = target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            return field == null ? null : field.GetValue(target);
        }

        private static bool BoolValue(object value)
        {
            return value is bool boolValue && boolValue;
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

        private static List<string> StringList(object value)
        {
            List<string> result = new List<string>();
            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null || value is string)
            {
                return result;
            }

            foreach (object item in enumerable)
            {
                string text = StringValue(item);
                if (!string.IsNullOrEmpty(text))
                {
                    result.Add(text);
                }
            }

            return result;
        }

        private static string StringValue(object value)
        {
            if (value == null)
            {
                return null;
            }

            return value.ToString();
        }

        private static int IntValue(object value, int fallback)
        {
            if (value == null)
            {
                return fallback;
            }

            try
            {
                return Convert.ToInt32(value);
            }
            catch
            {
                return fallback;
            }
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

            return lower;
        }
    }
}
