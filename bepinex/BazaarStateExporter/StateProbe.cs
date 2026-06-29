using BepInEx.Logging;
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text.RegularExpressions;
using HarmonyLib;
using UnityEngine;

namespace BazaarStateExporter
{
    public sealed class StateProbe
    {
        private readonly ManualLogSource logger;
        private bool warnedOnce;
        private readonly HashSet<int> loggedUiResourceObjects = new HashSet<int>();
        private int? lastLoggedUiGold;
        private int? lastLoggedUiHealth;
        private int? latestUiDay;
        private int? lastLoggedUiDay;
        private bool loggedUiCandidate;
        private string lastSnapshotHero;

        public StateProbe(ManualLogSource logger)
        {
            this.logger = logger;
        }

        public GameStateSnapshot TryReadCurrentState()
        {
            object processor = RuntimeStateCache.NetMessageProcessor;
            if (processor != null)
            {
                object latestDto = TryReadLatestGameStateFromProcessor(processor);
                if (latestDto != null)
                {
                    RuntimeStateCache.LatestGameStateSnapshot = latestDto;
                }
            }

            object dto = RuntimeStateCache.LatestGameStateSnapshot;
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

        public GameStateSnapshot TryReadCachedState()
        {
            return TryReadCurrentState();
        }

        public static object TryReadLatestGameStateFromProcessor(object processor)
        {
            if (processor == null
                || processor.GetType().FullName != "TheBazaar.NetMessageProcessor")
            {
                return null;
            }

            object lastMessage = GetField(processor, "_lastMessage");
            object dto = TryGetDataFromGameStateMessage(lastMessage);
            if (dto != null)
            {
                return dto;
            }

            IList messages = GetField(processor, "_lastMessages") as IList;
            if (messages == null)
            {
                return null;
            }

            for (int index = messages.Count - 1; index >= 0; index--)
            {
                dto = TryGetDataFromGameStateMessage(messages[index]);
                if (dto != null)
                {
                    return dto;
                }
            }

            return null;
        }

        public static object TryRecoverInitialGameState()
        {
            Type processorType = AccessTools.TypeByName("TheBazaar.NetMessageProcessor");
            if (processorType == null)
            {
                return null;
            }

            UnityEngine.Object[] processors = Resources.FindObjectsOfTypeAll(processorType);
            foreach (UnityEngine.Object processor in processors)
            {
                RuntimeStateCache.NetMessageProcessor = processor;
                object dto = TryReadLatestGameStateFromProcessor(processor);
                if (dto != null)
                {
                    return dto;
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
            Type cardControllerType = AccessTools.TypeByName("CardController");
            if (cardControllerType == null)
            {
                RuntimeStateCache.SetCurrentVisibleCards(new List<CardSnapshot>());
                return;
            }

            List<CardSnapshot> visibleCards = new List<CardSnapshot>();
            UnityEngine.Object[] controllers = Resources.FindObjectsOfTypeAll(cardControllerType);
            foreach (UnityEngine.Object controller in controllers)
            {
                MonoBehaviour behaviour = controller as MonoBehaviour;
                if (behaviour == null || !behaviour.gameObject.activeInHierarchy)
                {
                    continue;
                }

                CardSnapshot card = UiCardCapture.TryBuildSnapshot(behaviour, "visible_scan");
                if (card != null && !string.IsNullOrEmpty(card.id))
                {
                    visibleCards.Add(card);
                }
            }

            RuntimeStateCache.SetCurrentVisibleCards(visibleCards);
        }

        public void ScanUiResources()
        {
            int? gold;
            int? health;
            TryReadUiResources(logger, out gold, out health);
            RuntimeStateCache.UpdateResources(gold, health, "ui_hud");
            latestUiDay = TryReadUiDay();
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
            string hero = StringValue(GetField(player, "Hero"));
            if (!string.IsNullOrEmpty(lastSnapshotHero)
                && !string.Equals(lastSnapshotHero, hero, StringComparison.OrdinalIgnoreCase))
            {
                RuntimeStateCache.ResetForNewRun();
                loggedUiResourceObjects.Clear();
                loggedUiCandidate = false;
                lastLoggedUiGold = null;
                lastLoggedUiHealth = null;
                latestUiDay = null;
                lastLoggedUiDay = null;
            }
            if (!string.IsNullOrEmpty(hero))
            {
                lastSnapshotHero = hero;
            }

            GameStateSnapshot snapshot = new GameStateSnapshot
            {
                source = "bepinex",
                hero = hero,
                day = IntValue(GetField(run, "Day"), 1),
                event_option_ids = StringList(GetField(currentState, "SelectionSet")),
            };
            if (latestUiDay.HasValue)
            {
                snapshot.day = latestUiDay.Value;
            }

            object allCards = GetField(dto, "Cards");
            List<CardSnapshot> allCardSnapshots = CardList(allCards).ToList();
            snapshot.event_options.AddRange(snapshot.event_option_ids);
            snapshot.owned_cards.AddRange(BuildCurrentOwnedCards(dto, allCardSnapshots));

            HashSet<string> eventOptionIdSet = new HashSet<string>(snapshot.event_option_ids);
            HashSet<string> detailedEventOptionIds = new HashSet<string>();
            foreach (CardSnapshot card in allCardSnapshots)
            {
                if (!string.IsNullOrEmpty(card.id) && eventOptionIdSet.Contains(card.id))
                {
                    AddEventOptionDetailed(snapshot, card, detailedEventOptionIds);

                    if (!string.IsNullOrEmpty(card.template_id))
                    {
                        snapshot.event_option_template_ids.Add(card.template_id);
                    }
                }

                string section = card.section ?? "";
                if (section.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0
                    || section.IndexOf("Selection", StringComparison.OrdinalIgnoreCase) >= 0
                    || section.IndexOf("Reward", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    snapshot.visible_cards.Add(card);
                }
            }
            foreach (string optionId in snapshot.event_option_ids)
            {
                if (!detailedEventOptionIds.Contains(optionId))
                {
                    AddEventOptionDetailed(
                        snapshot,
                        new CardSnapshot
                        {
                            id = optionId,
                            source = "selection_set",
                        },
                        detailedEventOptionIds);
                }
            }

            MergeCapturedUiCards(snapshot, eventOptionIdSet, detailedEventOptionIds);

            Dictionary<string, int> attributes = AttributeDictionary(GetField(player, "Attributes"));
            snapshot.gold = FindAttribute(attributes, "Gold");
            snapshot.health = FindAttribute(attributes, "Health");
            RuntimeStateCache.UpdateResources(snapshot.gold, snapshot.health, "game_state_sync");

            if (RuntimeStateCache.LatestGold.HasValue)
            {
                snapshot.gold = RuntimeStateCache.LatestGold;
            }
            if (RuntimeStateCache.LatestHealth.HasValue)
            {
                snapshot.health = RuntimeStateCache.LatestHealth;
            }

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

        private static List<CardSnapshot> BuildCurrentOwnedCards(
            object dto,
            List<CardSnapshot> allCards)
        {
            List<CardSnapshot> result = new List<CardSnapshot>();
            HashSet<string> seenIds = new HashSet<string>();

            // Only the live card section decides item ownership. Historical
            // hand/stash getters can retain an instance after it is sold.
            foreach (CardSnapshot card in allCards)
            {
                if (card == null || !IsOwnedItemSection(card.section))
                {
                    continue;
                }

                AddUniqueCard(result, seenIds, card);
            }

            // Skills do not consistently use Hand/Stash sections.
            foreach (CardSnapshot skill in CardList(GetProperty(dto, "GetPlayerSkillsCards")))
            {
                AddUniqueCard(result, seenIds, skill);
            }

            return result;
        }

        private static bool IsOwnedItemSection(string section)
        {
            return string.Equals(section, "Hand", StringComparison.OrdinalIgnoreCase)
                || string.Equals(section, "Stash", StringComparison.OrdinalIgnoreCase);
        }

        private static void AddUniqueCard(
            List<CardSnapshot> cards,
            HashSet<string> seenIds,
            CardSnapshot card)
        {
            if (card == null)
            {
                return;
            }

            string identity = !string.IsNullOrEmpty(card.id)
                ? "id:" + card.id
                : "template:" + (card.template_id ?? "") + "|name:" + (card.name ?? "");
            if (seenIds.Add(identity))
            {
                cards.Add(card);
            }
        }

        private void TryReadUiResources(ManualLogSource log, out int? gold, out int? health)
        {
            gold = null;
            health = null;
            int goldScore = int.MinValue;
            int healthScore = int.MinValue;

            GameObject[] objects = Resources.FindObjectsOfTypeAll<GameObject>();
            foreach (GameObject gameObject in objects)
            {
                if (gameObject == null)
                {
                    continue;
                }

                string objectName = gameObject.name ?? "";
                bool isGold = objectName.IndexOf("Gold_Number", StringComparison.OrdinalIgnoreCase) >= 0;
                bool isHealth = objectName.IndexOf("Health_Value", StringComparison.OrdinalIgnoreCase) >= 0;
                if (!isGold && !isHealth)
                {
                    continue;
                }

                int parsed;
                List<string> diagnostics;
                bool parsedSuccessfully = TryReadIntegerFromComponents(gameObject, out parsed, out diagnostics);
                LogUiResourceObjectOnce(log, gameObject, diagnostics, parsedSuccessfully, parsed);
                if (parsedSuccessfully)
                {
                    int score = ScoreUiResourceObject(gameObject);
                    if (isGold && score > goldScore)
                    {
                        gold = parsed;
                        goldScore = score;
                    }
                    if (isHealth && score > healthScore)
                    {
                        health = parsed;
                        healthScore = score;
                    }
                }
            }

            if (goldScore < 1000)
            {
                gold = null;
            }
            if (healthScore < 1000)
            {
                health = null;
            }

            if (!gold.HasValue || !health.HasValue)
            {
                MonoBehaviour[] components = Resources.FindObjectsOfTypeAll<MonoBehaviour>();
                foreach (MonoBehaviour component in components)
                {
                    if (component == null || component.gameObject == null)
                    {
                        continue;
                    }

                    GameObject gameObject = component.gameObject;
                    bool isGold;
                    bool isHealth;
                    if (!TryClassifyActiveResourceText(component, out isGold, out isHealth))
                    {
                        continue;
                    }
                    int parsed;
                    List<string> diagnostics = new List<string>();
                    bool parsedSuccessfully = TryReadIntegerFromComponent(component, out parsed, diagnostics);
                    LogUiResourceObjectOnce(log, gameObject, diagnostics, parsedSuccessfully, parsed);
                    if (parsedSuccessfully)
                    {
                        int score = ScoreUiResourceObject(gameObject);
                        if (isGold && score > goldScore)
                        {
                            gold = parsed;
                            goldScore = score;
                        }
                        if (isHealth && score > healthScore)
                        {
                            health = parsed;
                            healthScore = score;
                        }
                    }
                }
            }

            // Inactive objects are still scanned and logged, but they are prefab/hidden
            // copies rather than the HUD currently shown to the player.
            if (goldScore < 1000)
            {
                gold = null;
            }
            if (healthScore < 1000)
            {
                health = null;
            }

            if (gold.HasValue || health.HasValue)
            {
                if (!loggedUiCandidate || lastLoggedUiGold != gold || lastLoggedUiHealth != health)
                {
                    log?.LogInfo(
                        "UI resource candidate gold="
                        + (gold.HasValue ? gold.Value.ToString() : "null")
                        + " health="
                        + (health.HasValue ? health.Value.ToString() : "null"));
                    loggedUiCandidate = true;
                    lastLoggedUiGold = gold;
                    lastLoggedUiHealth = health;
                }
            }
        }

        private static bool TryClassifyActiveResourceText(
            MonoBehaviour component,
            out bool isGold,
            out bool isHealth)
        {
            isGold = false;
            isHealth = false;
            if (!component.gameObject.activeInHierarchy)
            {
                return false;
            }

            string typeName = component.GetType().FullName ?? component.GetType().Name;
            if (typeName.IndexOf("Text", StringComparison.OrdinalIgnoreCase) < 0)
            {
                return false;
            }

            string hierarchy = GetHierarchyPath(component.transform);
            string lower = hierarchy.ToLowerInvariant();
            if (lower.Contains("tooltip")
                || lower.Contains("monster")
                || lower.Contains("reward")
                || lower.Contains("enemy")
                || lower.Contains("opponent"))
            {
                return false;
            }

            isGold = lower.Contains("gold")
                || lower.Contains("currency")
                || lower.Contains("wallet")
                || lower.Contains("coins");
            string objectName = (component.gameObject.name ?? "").ToLowerInvariant();
            isHealth = !objectName.Contains("regen")
                && (objectName.Contains("hpnumber")
                    || objectName.Contains("hp_number")
                    || objectName.Contains("healthnumber")
                    || objectName.Contains("health_number")
                    || objectName.Contains("currenthealth")
                    || objectName.Contains("current_health"));
            return isGold || isHealth;
        }

        private static int ScoreUiResourceObject(GameObject gameObject)
        {
            int score = 0;
            if (gameObject.activeInHierarchy)
            {
                score += 1000;
            }
            if (gameObject.activeSelf)
            {
                score += 100;
            }
            if (gameObject.scene.IsValid())
            {
                score += 50;
            }
            if (gameObject.scene.isLoaded)
            {
                score += 50;
            }

            Component[] components;
            try
            {
                components = gameObject.GetComponents<Component>();
            }
            catch
            {
                return score;
            }

            foreach (Component component in components)
            {
                Behaviour behaviour = component as Behaviour;
                if (behaviour != null && behaviour.enabled)
                {
                    score += 10;
                }
            }

            return score;
        }

        private int? TryReadUiDay()
        {
            int bestScore = int.MinValue;
            int? bestDay = null;
            MonoBehaviour[] components = Resources.FindObjectsOfTypeAll<MonoBehaviour>();
            foreach (MonoBehaviour component in components)
            {
                if (component == null
                    || component.gameObject == null
                    || !component.gameObject.activeInHierarchy)
                {
                    continue;
                }

                string typeName = component.GetType().FullName ?? component.GetType().Name;
                if (typeName.IndexOf("Text", StringComparison.OrdinalIgnoreCase) < 0)
                {
                    continue;
                }

                GameObject gameObject = component.gameObject;
                string objectName = (gameObject.name ?? "").ToLowerInvariant();
                string hierarchy = GetHierarchyPath(gameObject.transform).ToLowerInvariant();
                if (!objectName.Contains("day") && !hierarchy.Contains("day"))
                {
                    continue;
                }
                if (hierarchy.Contains("tooltip")
                    || hierarchy.Contains("reward")
                    || hierarchy.Contains("card")
                    || hierarchy.Contains("history"))
                {
                    continue;
                }

                int parsed;
                List<string> diagnostics = new List<string>();
                if (!TryReadIntegerFromComponent(component, out parsed, diagnostics)
                    || parsed < 1
                    || parsed > 20)
                {
                    continue;
                }

                int score = ScoreUiResourceObject(gameObject);
                if (objectName.Contains("daynumber")
                    || objectName.Contains("day_number")
                    || objectName.Contains("dayvalue")
                    || objectName.Contains("day_value")
                    || objectName.Contains("currentday"))
                {
                    score += 500;
                }
                else if (objectName.Contains("day"))
                {
                    score += 300;
                }

                if (score > bestScore)
                {
                    bestScore = score;
                    bestDay = parsed;
                }
            }

            if (bestScore < 1400)
            {
                return null;
            }
            if (bestDay.HasValue && lastLoggedUiDay != bestDay)
            {
                logger?.LogInfo("UI day candidate day=" + bestDay.Value);
                lastLoggedUiDay = bestDay;
            }
            return bestDay;
        }

        private static bool TryReadIntegerFromComponents(
            GameObject gameObject,
            out int value,
            out List<string> diagnostics)
        {
            value = 0;
            diagnostics = new List<string>();
            Component[] components;
            try
            {
                components = gameObject.GetComponents<Component>();
            }
            catch
            {
                return false;
            }

            bool found = false;
            foreach (Component component in components)
            {
                if (component == null)
                {
                    continue;
                }

                int parsed;
                if (TryReadIntegerFromComponent(component, out parsed, diagnostics) && !found)
                {
                    value = parsed;
                    found = true;
                }
            }

            return found;
        }

        private static bool TryReadIntegerFromComponent(
            Component component,
            out int value,
            List<string> diagnostics)
        {
            value = 0;
            Type type = component.GetType();
            diagnostics.Add("component=" + (type.FullName ?? type.Name));

            bool parsedAny = false;
            foreach (string memberName in new[] { "text", "Text", "m_text" })
            {
                string text;
                bool found;
                SafeTextMember(component, memberName, out text, out found);
                if (!found)
                {
                    continue;
                }

                int parsed;
                bool parsedSuccessfully = TryParseFirstInteger(text, out parsed);
                diagnostics.Add(
                    memberName
                    + "=\""
                    + (text ?? "null")
                    + "\" parse="
                    + (parsedSuccessfully ? parsed.ToString() : "failed"));
                if (parsedSuccessfully && !parsedAny)
                {
                    value = parsed;
                    parsedAny = true;
                }
            }

            return parsedAny;
        }

        private void LogUiResourceObjectOnce(
            ManualLogSource log,
            GameObject gameObject,
            List<string> diagnostics,
            bool parsed,
            int parsedValue)
        {
            int instanceId = gameObject.GetInstanceID();
            if (!loggedUiResourceObjects.Add(instanceId))
            {
                return;
            }

            Component[] components;
            try
            {
                components = gameObject.GetComponents<Component>();
            }
            catch
            {
                components = new Component[0];
            }

            string componentTypes = string.Join(
                ",",
                components
                    .Where(component => component != null)
                    .Select(component => component.GetType().FullName ?? component.GetType().Name)
                    .ToArray());
            log?.LogInfo(
                "UI resource object name="
                + gameObject.name
                + " activeSelf="
                + gameObject.activeSelf
                + " activeInHierarchy="
                + gameObject.activeInHierarchy
                + " scene="
                + gameObject.scene.name
                + " sceneValid="
                + gameObject.scene.IsValid()
                + " sceneLoaded="
                + gameObject.scene.isLoaded
                + " hierarchy="
                + GetHierarchyPath(gameObject.transform)
                + " components=["
                + componentTypes
                + "] values=["
                + string.Join("; ", diagnostics.ToArray())
                + "] parse="
                + (parsed ? parsedValue.ToString() : "failed"));
        }

        private static string GetHierarchyPath(Transform transform)
        {
            List<string> names = new List<string>();
            Transform current = transform;
            while (current != null && names.Count < 16)
            {
                names.Add(current.name);
                current = current.parent;
            }
            names.Reverse();
            return string.Join("/", names.ToArray());
        }

        private static void SafeTextMember(
            object target,
            string name,
            out string text,
            out bool found)
        {
            text = null;
            found = false;
            if (target == null)
            {
                return;
            }

            Type type = target.GetType();
            try
            {
                PropertyInfo property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (property != null && property.GetIndexParameters().Length == 0)
                {
                    found = true;
                    object value = property.GetValue(target, null);
                    text = value as string;
                    return;
                }
            }
            catch
            {
            }

            try
            {
                FieldInfo field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (field == null)
                {
                    return;
                }

                found = true;
                object value = field == null ? null : field.GetValue(target);
                text = value as string;
            }
            catch
            {
            }
        }

        private static bool TryParseFirstInteger(string text, out int value)
        {
            value = 0;
            if (string.IsNullOrEmpty(text))
            {
                return false;
            }

            Match match = Regex.Match(text, @"[-+]?\d[\d,]*");
            return match.Success
                && int.TryParse(match.Value.Replace(",", ""), out value);
        }

        private static void MergeCapturedUiCards(
            GameStateSnapshot snapshot,
            HashSet<string> eventOptionIdSet,
            HashSet<string> detailedEventOptionIds)
        {
            List<CardSnapshot> capturedCards = RuntimeStateCache.GetCurrentVisibleCards();
            List<CardSnapshot> currentEventCards = capturedCards
                .Where(card => card != null
                    && !string.IsNullOrEmpty(card.id)
                    && (card.card_type ?? "").IndexOf(
                        "Encounter",
                        StringComparison.OrdinalIgnoreCase) >= 0)
                .ToList();

            if (currentEventCards.Count > 0)
            {
                snapshot.event_options.Clear();
                snapshot.event_option_ids.Clear();
                snapshot.event_option_template_ids.Clear();
                snapshot.event_options_detailed.Clear();
                eventOptionIdSet.Clear();
                detailedEventOptionIds.Clear();
            }

            HashSet<string> visibleIds = new HashSet<string>(snapshot.visible_cards.Select(card => card.id).Where(id => !string.IsNullOrEmpty(id)));
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
                    AddEventOptionDetailed(snapshot, card, detailedEventOptionIds);

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
                bool eventCard = (card.card_type ?? "").IndexOf(
                    "Encounter",
                    StringComparison.OrdinalIgnoreCase) >= 0;
                if (eventCard)
                {
                    if (eventOptionIdSet.Add(card.id))
                    {
                        snapshot.event_option_ids.Add(card.id);
                    }
                    AddEventOptionDetailed(snapshot, card, detailedEventOptionIds);
                    if (!string.IsNullOrEmpty(card.template_id)
                        && templateIds.Add(card.template_id))
                    {
                        snapshot.event_option_template_ids.Add(card.template_id);
                    }
                    if (!string.IsNullOrEmpty(card.name) && eventNames.Add(card.name))
                    {
                        snapshot.event_options.Add(card.name);
                    }
                    continue;
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

        private static void AddEventOptionDetailed(
            GameStateSnapshot snapshot,
            CardSnapshot card,
            HashSet<string> detailedEventOptionIds)
        {
            if (card == null || string.IsNullOrEmpty(card.id))
            {
                return;
            }

            if (!detailedEventOptionIds.Add(card.id))
            {
                return;
            }

            snapshot.event_options_detailed.Add(new EventOptionSnapshot
            {
                id = card.id,
                template_id = card.template_id,
                name = card.name,
                kind = EventKindFromCard(card),
                card_type = card.card_type,
                section = card.section,
                source = string.IsNullOrEmpty(card.source) ? "unknown" : card.source,
            });
        }

        private static string EventKindFromCard(CardSnapshot card)
        {
            string id = card == null ? "" : card.id ?? "";
            string cardType = card == null ? "" : card.card_type ?? "";

            if (cardType.IndexOf("Encounter", StringComparison.OrdinalIgnoreCase) >= 0
                || id.StartsWith("enc_", StringComparison.OrdinalIgnoreCase))
            {
                return "encounter";
            }

            if (id.StartsWith("ste_", StringComparison.OrdinalIgnoreCase))
            {
                return "step";
            }

            if (id.StartsWith("com_", StringComparison.OrdinalIgnoreCase))
            {
                return "combat";
            }

            if (id.StartsWith("pvp_", StringComparison.OrdinalIgnoreCase))
            {
                return "pvp";
            }

            return "unknown";
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
