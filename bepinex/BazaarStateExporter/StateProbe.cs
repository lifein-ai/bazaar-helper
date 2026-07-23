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
        private int? lastSnapshotDay;
        private float suppressCachedSnapshotUntil;
        private string pendingNewRunHero;
        private int pendingNewRunDay = 1;
        private string lastLoggedAttributeKeys;
        private static Dictionary<string, object> staticCardTemplatesById;
        private static bool staticDataUnavailableLogged;
        private static bool branchSummaryDiagnosticLogged;
        private static ManualLogSource sharedLogger;
        private string lastLoggedSnapshotSummary;

        public StateProbe(ManualLogSource logger)
        {
            this.logger = logger;
            sharedLogger = logger;
        }

        public GameStateSnapshot TryReadCurrentState()
        {
            if (Time.unscaledTime < suppressCachedSnapshotUntil)
            {
                return CreateNewRunTransitionSnapshot();
            }

            object processor = RuntimeStateCache.NetMessageProcessor;
            object dto = RuntimeStateCache.LatestGameStateSnapshot;
            bool hasLiveDto = dto != null
                && string.Equals(
                    RuntimeStateCache.LatestGameStateSource,
                    "receive_or_queue",
                    StringComparison.Ordinal);
            if (!hasLiveDto && processor != null)
            {
                object latestDto = TryReadLatestGameStateFromProcessor(processor);
                if (latestDto != null && !object.ReferenceEquals(latestDto, dto))
                {
                    RuntimeStateCache.LatestGameStateSnapshot = latestDto;
                    RuntimeStateCache.LatestGameStateSource = "processor_latest";
                    RuntimeStateCache.LatestGameStateSummary = DescribeGameStateDto(latestDto);
                    dto = latestDto;
                }
            }

            if (dto == null)
            {
                dto = TryRecoverInitialGameState();
                if (dto != null)
                {
                    RuntimeStateCache.LatestGameStateSnapshot = dto;
                    RuntimeStateCache.LatestGameStateSource = "processor_recovery";
                    RuntimeStateCache.LatestGameStateSummary = DescribeGameStateDto(dto);
                    logger.LogInfo("Recovered current GameStateSnapshotDTO during polling.");
                }
            }

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
                    RuntimeStateCache.LatestGameStateSource = "initial_recovery";
                    RuntimeStateCache.LatestGameStateSummary = DescribeGameStateDto(dto);
                    return dto;
                }
            }

            return null;
        }

        private static object TryGetDataFromGameStateMessage(object message)
        {
            return TryGetGameStateDtoFromMessage(message);
        }

        public static object TryGetGameStateDtoFromMessage(object message)
        {
            if (message == null)
            {
                return null;
            }

            Type type = message.GetType();
            string fullName = type.FullName ?? "";
            if (fullName == "BazaarGameShared.Infra.Messages.NetMessageAggregate")
            {
                return TryGetGameStateDtoFromAggregate(message);
            }

            if (fullName != "BazaarGameShared.Infra.Messages.NetMessageGameStateSync"
                && fullName.IndexOf(
                    "GameStateSync",
                    StringComparison.OrdinalIgnoreCase) < 0)
            {
                return null;
            }

            object data = GetProperty(message, "Data");
            return LooksLikeGameStateDto(data) ? data : null;
        }

        private static object TryGetGameStateDtoFromAggregate(object message)
        {
            IEnumerable messages = GetProperty(message, "Messages") as IEnumerable
                ?? GetField(message, "<Messages>k__BackingField") as IEnumerable;
            if (messages == null)
            {
                return null;
            }

            foreach (object innerMessage in messages)
            {
                object dto = TryGetGameStateDtoFromMessage(innerMessage);
                if (dto != null)
                {
                    return dto;
                }
            }

            return null;
        }

        public static bool LooksLikeGameStateDto(object dto)
        {
            if (dto == null)
            {
                return false;
            }

            object run = GetField(dto, "Run");
            object player = GetField(dto, "Player");
            if (run == null || player == null)
            {
                return false;
            }

            string hero = StringValue(GetField(player, "Hero"));
            return !string.IsNullOrEmpty(hero);
        }

        public static string DescribeGameStateDto(object dto)
        {
            if (dto == null)
            {
                return "null";
            }

            object run = GetField(dto, "Run");
            object currentState = GetField(dto, "CurrentState");
            object player = GetField(dto, "Player");
            string hero = StringValue(GetField(player, "Hero")) ?? "";
            int day = IntValue(GetField(run, "Day"), 0);
            List<string> selectionSet = StringList(GetField(currentState, "SelectionSet"));
            List<CardSnapshot> cards = CardList(GetField(dto, "Cards")).ToList();
            int owned = cards.Count(card => card != null && IsOwnedItemSection(card.section));
            int skills = CardList(GetProperty(dto, "GetPlayerSkillsCards")).Count();
            return "type="
                + (dto.GetType().FullName ?? dto.GetType().Name)
                + " hero="
                + hero
                + " day="
                + day
                + " selection="
                + selectionSet.Count
                + " cards="
                + cards.Count
                + " owned="
                + owned
                + " skills="
                + skills;
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
            List<CardSnapshot> visibleCards = new List<CardSnapshot>();
            HashSet<string> seenIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            if (cardControllerType != null)
            {
                UnityEngine.Object[] controllers = Resources.FindObjectsOfTypeAll(cardControllerType);
                foreach (UnityEngine.Object controller in controllers)
                {
                    MonoBehaviour behaviour = controller as MonoBehaviour;
                    if (behaviour == null || !behaviour.gameObject.activeInHierarchy)
                    {
                        continue;
                    }

                    AddVisibleUiCard(visibleCards, seenIds, behaviour, "visible_scan");
                }
            }

            foreach (MonoBehaviour behaviour in Resources.FindObjectsOfTypeAll<MonoBehaviour>())
            {
                if (behaviour == null
                    || behaviour.gameObject == null
                    || !behaviour.gameObject.activeInHierarchy
                    || (cardControllerType != null && cardControllerType.IsInstanceOfType(behaviour)))
                {
                    continue;
                }

                string hierarchy = GetHierarchyPath(behaviour.transform);
                if (!LooksLikeMonsterBoardContext(hierarchy)
                    || GetProperty(behaviour, "CardData") == null)
                {
                    continue;
                }

                AddVisibleUiCard(visibleCards, seenIds, behaviour, "monster_board_visible_scan");
            }

            RuntimeStateCache.SetCurrentVisibleCards(visibleCards);
            if (visibleCards.Any(IsShopOfferCard))
            {
                RuntimeStateCache.SetScreenMode(
                    RuntimeStateCache.ScreenModeShop,
                    "visible_scan");
            }
            else if (visibleCards.Any(IsCurrentEventOptionCard))
            {
                RuntimeStateCache.ClearShopRefresh();
                RuntimeStateCache.SetScreenMode(
                    RuntimeStateCache.ScreenModeEvents,
                    "visible_scan");
            }
        }

        private static void AddVisibleUiCard(
            List<CardSnapshot> visibleCards,
            HashSet<string> seenIds,
            MonoBehaviour behaviour,
            string source)
        {
            CardSnapshot card = UiCardCapture.TryBuildSnapshot(behaviour, source);
            if (card == null || string.IsNullOrEmpty(card.id))
            {
                return;
            }

            string key = card.id + "|" + (card.ui_context ?? "");
            if (seenIds.Add(key))
            {
                visibleCards.Add(card);
            }
            RuntimeStateCache.RecordUiCard(card);
        }

        private static bool LooksLikeMonsterBoardContext(string hierarchy)
        {
            if (string.IsNullOrEmpty(hierarchy))
            {
                return false;
            }

            return hierarchy.IndexOf("Tooltip_MonsterBoard", StringComparison.OrdinalIgnoreCase) >= 0
                || hierarchy.IndexOf("MonsterBoard", StringComparison.OrdinalIgnoreCase) >= 0
                || hierarchy.IndexOf("Monster_Board", StringComparison.OrdinalIgnoreCase) >= 0
                || hierarchy.IndexOf("OpponentBoardAnchor", StringComparison.OrdinalIgnoreCase) >= 0;
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
            int dtoDay = IntValue(GetField(run, "Day"), 1);
            int day = dtoDay;
            bool uiDayOverridesDto = latestUiDay.HasValue
                && latestUiDay.Value != dtoDay;
            if (latestUiDay.HasValue)
            {
                day = latestUiDay.Value;
            }

            bool heroChanged = !string.IsNullOrEmpty(lastSnapshotHero)
                && !string.Equals(lastSnapshotHero, hero, StringComparison.OrdinalIgnoreCase);
            bool dayRestarted = !heroChanged
                && lastSnapshotDay.HasValue
                && day <= 2
                && day < lastSnapshotDay.Value;

            if (dayRestarted)
            {
                ResetLocalRunState();
                RuntimeStateCache.ResetForNewRun();
                RuntimeStateCache.LatestGameStateSnapshot = null;
                pendingNewRunHero = hero;
                pendingNewRunDay = day;
                lastSnapshotHero = hero;
                lastSnapshotDay = day;
                suppressCachedSnapshotUntil = Time.unscaledTime + 2.0f;
                logger.LogInfo(
                    "Detected a new run from day rollback; cleared previous-run state. hero="
                    + hero
                    + " day="
                    + day);
                return CreateNewRunTransitionSnapshot();
            }

            if (heroChanged)
            {
                RuntimeStateCache.ResetForNewRun();
                ResetLocalRunState();
            }
            else if (lastSnapshotDay.HasValue && day != lastSnapshotDay.Value)
            {
                RuntimeStateCache.ClearTransientUiState(
                    "day changed "
                    + lastSnapshotDay.Value
                    + " -> "
                    + day);
                lastLoggedSnapshotSummary = null;
            }
            if (!string.IsNullOrEmpty(hero))
            {
                lastSnapshotHero = hero;
            }
            lastSnapshotDay = day;
            pendingNewRunHero = null;

            GameStateSnapshot snapshot = new GameStateSnapshot
            {
                source = "bepinex",
                hero = hero,
                day = day,
                max_prestige = 20,
                inventory_slots_total = 20,
                event_option_ids = uiDayOverridesDto
                    ? new List<string>()
                    : StringList(GetField(currentState, "SelectionSet")),
            };

            object allCards = GetField(dto, "Cards");
            List<CardSnapshot> allCardSnapshots = CardList(allCards).ToList();
            string screenMode = RuntimeStateCache.GetScreenMode(10f);
            bool screenModeIsShop = string.Equals(
                screenMode,
                RuntimeStateCache.ScreenModeShop,
                StringComparison.Ordinal);
            bool screenModeIsEvents = string.Equals(
                screenMode,
                RuntimeStateCache.ScreenModeEvents,
                StringComparison.Ordinal);
            List<CardSnapshot> selectedCards = allCardSnapshots
                .Where(card => !string.IsNullOrEmpty(card.id)
                    && snapshot.event_option_ids.Contains(card.id))
                .ToList();
            bool selectionSetHasEncounters = selectedCards.Any(card =>
                (card.card_type ?? "").IndexOf(
                    "Encounter",
                    StringComparison.OrdinalIgnoreCase) >= 0
                || (card.id ?? "").StartsWith(
                    "enc_",
                    StringComparison.OrdinalIgnoreCase));
            if (selectionSetHasEncounters && !screenModeIsShop)
            {
                RuntimeStateCache.ClearShopRefresh();
            }
            bool selectionSetIsShopOffers =
                (!selectionSetHasEncounters || screenModeIsShop)
                && RuntimeStateCache.ShopRefreshAvailable.HasValue
                && selectedCards.Count > 0
                && selectedCards.All(card => IsShopOfferCardType(card.card_type));
            if (selectionSetIsShopOffers)
            {
                snapshot.event_options.Clear();
                snapshot.event_option_ids.Clear();
            }
            snapshot.event_options.AddRange(snapshot.event_option_ids);
            snapshot.owned_cards.AddRange(BuildCurrentOwnedCards(dto, allCardSnapshots));
            MergeCapturedOwnedCards(snapshot.owned_cards);
            foreach (CardSnapshot owned in snapshot.owned_cards)
            {
                if (string.Equals(owned.card_type, "Skill", StringComparison.OrdinalIgnoreCase))
                {
                    snapshot.skills.Add(owned);
                    continue;
                }

                snapshot.owned_items.Add(owned);
                if (IsBoardOwnedItem(owned))
                {
                    snapshot.board_items.Add(owned);
                }
                else if (IsStashOwnedItem(owned))
                {
                    snapshot.stash_items.Add(owned);
                }
            }

            HashSet<string> eventOptionIdSet = new HashSet<string>(snapshot.event_option_ids);
            HashSet<string> detailedEventOptionIds = new HashSet<string>();
            List<CardSnapshot> shopCards = new List<CardSnapshot>();
            if (selectionSetIsShopOffers && !screenModeIsShop)
            {
                shopCards.AddRange(selectedCards);
            }
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
                if (section.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    shopCards.Add(card);
                }
                if (section.IndexOf("Reward", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    snapshot.current_reward_options.Add(card);
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

            if (screenModeIsShop)
            {
                snapshot.event_options.Clear();
                snapshot.event_option_ids.Clear();
                snapshot.event_option_template_ids.Clear();
                snapshot.event_options_detailed.Clear();
                eventOptionIdSet.Clear();
                detailedEventOptionIds.Clear();
            }
            else if (!uiDayOverridesDto)
            {
                float eventCaptureMinSeenAt = screenModeIsEvents
                    ? Math.Max(0f, RuntimeStateCache.LastScreenModeAt - 1.0f)
                    : 0f;
                MergeCapturedUiCards(
                    snapshot,
                    eventOptionIdSet,
                    detailedEventOptionIds,
                    eventCaptureMinSeenAt);
            }
            snapshot.current_events.AddRange(snapshot.event_options_detailed);

            // Rebuild screen-specific groups after merging UI-captured cards so
            // current_shop never includes Selection/Reward cards.
            if (!selectionSetIsShopOffers && !screenModeIsShop)
            {
                shopCards.Clear();
            }
            snapshot.current_reward_options.Clear();
            float shopCaptureMinSeenAt = screenModeIsShop
                ? Math.Max(0f, RuntimeStateCache.LastScreenModeAt - 1.0f)
                : 0f;
            List<CardSnapshot> recentlyCapturedCards =
                RuntimeStateCache.GetCapturedUiCards(30f, shopCaptureMinSeenAt);
            List<CardSnapshot> latestSocketOffers =
                RuntimeStateCache.GetLatestOpponentItemSocketCards(30f, shopCaptureMinSeenAt);
            CardSnapshot latestMerchant =
                RuntimeStateCache.GetLatestMerchantCard(30f, shopCaptureMinSeenAt);
            bool hasMerchantPortrait = recentlyCapturedCards.Any(card =>
                (card.ui_context ?? "").IndexOf(
                    "OpponentPortraitSocketMerchant",
                    StringComparison.OrdinalIgnoreCase) >= 0);
            bool merchantScreen = screenModeIsShop
                || (hasMerchantPortrait && latestSocketOffers.Count > 0)
                || (!screenModeIsEvents
                && !selectionSetHasEncounters
                && (
                    hasMerchantPortrait
                    || RuntimeStateCache.ShopRefreshAvailable.HasValue
                    || RuntimeStateCache.ShopRefreshCost.HasValue
                    || RuntimeStateCache.ShopRefreshesRemaining.HasValue
                ));
            if (merchantScreen)
            {
                if (latestSocketOffers.Count > 0)
                {
                    shopCards.Clear();
                    shopCards.AddRange(latestSocketOffers);
                }
                foreach (CardSnapshot capturedOffer in recentlyCapturedCards.Where(IsShopOfferCard))
                {
                    if (latestSocketOffers.Count > 0
                        && !latestSocketOffers.Any(card =>
                            card.id == capturedOffer.id))
                    {
                        continue;
                    }
                    UpsertCardById(shopCards, capturedOffer);
                }
            }
            foreach (CardSnapshot visible in snapshot.visible_cards)
            {
                string section = visible.section ?? "";
                string uiContext = visible.ui_context ?? "";
                if (!screenModeIsShop
                    && (section.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0
                    || uiContext.IndexOf("Shop", StringComparison.OrdinalIgnoreCase) >= 0)
                )
                {
                    UpsertCardById(shopCards, visible);
                }
                if (section.IndexOf("Reward", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    snapshot.current_reward_options.Add(visible);
                }
            }
            if (shopCards.Count > 0
                || RuntimeStateCache.ShopRefreshAvailable.HasValue
                || RuntimeStateCache.ShopRefreshCost.HasValue
                || RuntimeStateCache.ShopRefreshesRemaining.HasValue)
            {
                snapshot.current_shop = new CurrentShopSnapshot();
                if (latestMerchant != null)
                {
                    snapshot.current_shop.merchant_id = latestMerchant.id;
                    snapshot.current_shop.merchant_template_id = latestMerchant.template_id;
                    snapshot.current_shop.merchant_name = MerchantName(latestMerchant);
                }
                snapshot.current_shop.visible_items.AddRange(shopCards);
                snapshot.current_shop.refresh_available =
                    RuntimeStateCache.ShopRefreshAvailable;
                snapshot.current_shop.refresh_cost =
                    RuntimeStateCache.ShopRefreshCost;
                snapshot.current_shop.refreshes_remaining =
                    RuntimeStateCache.ShopRefreshesRemaining;
            }

            Dictionary<string, int> attributes = AttributeDictionary(GetField(player, "Attributes"));
            string attributeKeys = string.Join(
                ",",
                attributes.Keys.OrderBy(key => key, StringComparer.OrdinalIgnoreCase));
            if (!string.Equals(
                lastLoggedAttributeKeys,
                attributeKeys,
                StringComparison.Ordinal))
            {
                lastLoggedAttributeKeys = attributeKeys;
                logger.LogInfo("Player attribute keys: " + attributeKeys);
            }
            snapshot.gold = FindAttributeExact(
                attributes,
                "Gold",
                "CurrentGold",
                "Current Gold",
                "Coins",
                "Coin");
            snapshot.health = FindAttributeExact(
                attributes,
                "Health",
                "CurrentHealth",
                "Current Health");
            snapshot.combat_health = snapshot.health;
            snapshot.income = FindAttributeExact(attributes, "Income");
            snapshot.level = FindAttributeExact(attributes, "Level");
            snapshot.xp = FindAttributeExact(attributes, "XP", "Experience");
            snapshot.prestige = FindAttributeExact(attributes, "Prestige");
            snapshot.max_prestige = FindAttributeExact(
                attributes,
                "MaxPrestige",
                "Max Prestige",
                "PrestigeMax") ?? 20;
            RuntimeStateCache.UpdateResources(snapshot.gold, snapshot.health, "game_state_sync");

            if (RuntimeStateCache.LatestGold.HasValue)
            {
                snapshot.gold = RuntimeStateCache.LatestGold;
            }
            if (RuntimeStateCache.LatestHealth.HasValue)
            {
                snapshot.health = RuntimeStateCache.LatestHealth;
                snapshot.combat_health = RuntimeStateCache.LatestHealth;
            }
            snapshot.monster_health = TryReadOpponentUiHealth();
            if (!uiDayOverridesDto)
            {
                FillMonsterCards(snapshot, screenModeIsShop, screenModeIsEvents);
            }

            if (snapshot.event_option_ids.Count > 0 || snapshot.owned_cards.Count > 0)
            {
                string summary =
                    snapshot.hero
                    + "|"
                    + snapshot.day
                    + "|"
                    + snapshot.event_option_ids.Count
                    + "/"
                    + snapshot.event_option_template_ids.Count
                    + "|"
                    + snapshot.owned_cards.Count
                    + "|"
                    + snapshot.visible_cards.Count;
                if (!string.Equals(lastLoggedSnapshotSummary, summary, StringComparison.Ordinal))
                {
                    lastLoggedSnapshotSummary = summary;
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
            }

            return snapshot;
        }

        private static string MerchantName(CardSnapshot merchant)
        {
            if (!string.IsNullOrWhiteSpace(merchant.name))
            {
                return merchant.name;
            }

            string context = merchant.ui_context ?? "";
            int slashIndex = context.IndexOf('/');
            return slashIndex > 0
                ? context.Substring(0, slashIndex).Trim()
                : "";
        }

        private GameStateSnapshot CreateNewRunTransitionSnapshot()
        {
            return new GameStateSnapshot
            {
                source = "bepinex",
                hero = pendingNewRunHero,
                day = pendingNewRunDay,
            };
        }

        private void ResetLocalRunState()
        {
            loggedUiResourceObjects.Clear();
            loggedUiCandidate = false;
            lastLoggedUiGold = null;
            lastLoggedUiHealth = null;
            latestUiDay = null;
            lastLoggedUiDay = null;
            lastLoggedSnapshotSummary = null;
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
                if (!IsOwnedItemCard(card))
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

        private static void MergeCapturedOwnedCards(List<CardSnapshot> ownedCards)
        {
            if (ownedCards == null)
            {
                return;
            }

            Dictionary<string, CardSnapshot> cardsById = ownedCards
                .Where(card => card != null && !string.IsNullOrEmpty(card.id))
                .GroupBy(card => card.id)
                .ToDictionary(group => group.Key, group => group.First());

            List<CardSnapshot> candidates = RuntimeStateCache.GetCurrentVisibleCards();
            foreach (CardSnapshot recentCard in RuntimeStateCache.GetCapturedUiCards(8f))
            {
                if (recentCard == null || string.IsNullOrEmpty(recentCard.id))
                {
                    continue;
                }
                if (candidates.Any(card => card != null && card.id == recentCard.id))
                {
                    continue;
                }
                candidates.Add(recentCard);
            }

            foreach (CardSnapshot card in candidates)
            {
                if (!IsCapturedOwnedItemCard(card))
                {
                    continue;
                }
                CardSnapshot existing;
                if (cardsById.TryGetValue(card.id, out existing))
                {
                    MergeCapturedOwnedCard(existing, card);
                }
                else
                {
                    ownedCards.Add(card);
                    cardsById[card.id] = card;
                }
            }
        }

        private static void MergeCapturedOwnedCard(
            CardSnapshot existing,
            CardSnapshot captured)
        {
            string latestSection = CapturedOwnedSection(captured);
            if (!string.IsNullOrEmpty(latestSection))
            {
                existing.section = latestSection;
            }
            if (!string.IsNullOrEmpty(captured.template_id))
            {
                existing.template_id = captured.template_id;
            }
            if (!string.IsNullOrEmpty(captured.name))
            {
                existing.name = captured.name;
            }
            if (!string.IsNullOrEmpty(captured.rarity))
            {
                existing.rarity = captured.rarity;
            }
            if (!string.IsNullOrEmpty(captured.card_type))
            {
                existing.card_type = captured.card_type;
            }
            if (!string.IsNullOrEmpty(captured.source))
            {
                existing.source = captured.source;
            }
            if (!string.IsNullOrEmpty(captured.ui_context))
            {
                existing.ui_context = captured.ui_context;
            }
            if (captured.price.HasValue)
            {
                existing.price = captured.price;
            }
            if (captured.position.HasValue)
            {
                existing.position = captured.position;
            }
            if (!string.IsNullOrEmpty(captured.runtime_type))
            {
                existing.runtime_type = captured.runtime_type;
            }
            if (captured.enchantments.Count > 0)
            {
                existing.enchantments.Clear();
                existing.enchantments.AddRange(captured.enchantments);
            }
            foreach (string source in captured.runtime_sources)
            {
                if (!existing.runtime_sources.Contains(source))
                {
                    existing.runtime_sources.Add(source);
                }
            }
            CopyObjectDictionary(captured.runtime_values, existing.runtime_values);
            CopyObjectDictionary(captured.current_attributes, existing.current_attributes);
            CopyObjectDictionary(captured.base_attributes, existing.base_attributes);
            CopyObjectDictionary(captured.attribute_modifiers, existing.attribute_modifiers);
            CardSnapshotPosition.Fill(existing);
        }

        private static string CapturedOwnedSection(CardSnapshot card)
        {
            string context = card.ui_context ?? "";
            if (context.IndexOf(
                    "PlayerStorageSocket_",
                    StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "Stash";
            }
            if (context.IndexOf(
                    "PlayerItemSocket_",
                    StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "Hand";
            }
            return IsOwnedItemSection(card.section) ? card.section : "";
        }

        private static bool IsCapturedOwnedItemCard(CardSnapshot card)
        {
            if (card == null
                || string.IsNullOrEmpty(card.id)
                || !string.Equals(card.card_type, "Item", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            string section = card.section ?? "";
            string context = card.ui_context ?? "";
            return IsOwnedItemSection(section) || IsOwnedItemContext(context);
        }

        private static bool IsOwnedItemSection(string section)
        {
            return IsBoardItemSection(section) || IsStashItemSection(section);
        }

        private static bool IsBoardItemSection(string section)
        {
            if (string.IsNullOrEmpty(section))
            {
                return false;
            }

            return string.Equals(section, "Hand", StringComparison.OrdinalIgnoreCase)
                || string.Equals(section, "Board", StringComparison.OrdinalIgnoreCase)
                || section.IndexOf("PlayerHand", StringComparison.OrdinalIgnoreCase) >= 0
                || section.IndexOf("PlayerBoard", StringComparison.OrdinalIgnoreCase) >= 0
                || section.IndexOf("Inventory", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool IsStashItemSection(string section)
        {
            if (string.IsNullOrEmpty(section))
            {
                return false;
            }

            return string.Equals(section, "Stash", StringComparison.OrdinalIgnoreCase)
                || string.Equals(section, "Storage", StringComparison.OrdinalIgnoreCase)
                || section.IndexOf("PlayerStorage", StringComparison.OrdinalIgnoreCase) >= 0
                || section.IndexOf("Backpack", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool IsOwnedItemContext(string context)
        {
            if (string.IsNullOrEmpty(context))
            {
                return false;
            }

            return context.IndexOf("PlayerItemSocket_", StringComparison.OrdinalIgnoreCase) >= 0
                || context.IndexOf("PlayerStorageSocket_", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool IsOwnedItemCard(CardSnapshot card)
        {
            if (card == null
                || string.IsNullOrEmpty(card.id)
                || !string.Equals(card.card_type, "Item", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }

            return IsOwnedItemSection(card.section ?? "")
                || IsOwnedItemContext(card.ui_context ?? "");
        }

        private static bool IsBoardOwnedItem(CardSnapshot card)
        {
            if (card == null)
            {
                return false;
            }

            string section = card.section ?? "";
            string context = card.ui_context ?? "";
            return IsBoardItemSection(section)
                || context.IndexOf("PlayerItemSocket_", StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static bool IsStashOwnedItem(CardSnapshot card)
        {
            if (card == null)
            {
                return false;
            }

            string section = card.section ?? "";
            string context = card.ui_context ?? "";
            return IsStashItemSection(section)
                || context.IndexOf("PlayerStorageSocket_", StringComparison.OrdinalIgnoreCase) >= 0;
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
                || IsOwnedItemCard(card))
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

        private static void FillMonsterCards(
            GameStateSnapshot snapshot,
            bool screenModeIsShop,
            bool screenModeIsEvents)
        {
            if (snapshot == null)
            {
                return;
            }

            List<CardSnapshot> capturedCards = RuntimeStateCache.GetCapturedUiCards(30f);
            bool hasMonsterTooltipCards = capturedCards.Any(IsMonsterTooltipCard);

            HashSet<string> seenItems = new HashSet<string>();
            foreach (CardSnapshot card in RuntimeStateCache.GetLatestOpponentItemSocketCards(30f))
            {
                if (!IsOpponentItemCard(card, hasMonsterTooltipCards))
                {
                    continue;
                }
                AddUniqueCard(snapshot.monster_items, seenItems, card);
            }
            foreach (CardSnapshot card in capturedCards)
            {
                if (!IsOpponentItemCard(card, hasMonsterTooltipCards))
                {
                    continue;
                }
                AddUniqueCard(snapshot.monster_items, seenItems, card);
            }

            HashSet<string> seenSkills = new HashSet<string>();
            foreach (CardSnapshot card in capturedCards)
            {
                if (!IsOpponentSkillCard(card, hasMonsterTooltipCards))
                {
                    continue;
                }
                AddUniqueCard(snapshot.monster_skills, seenSkills, card);
            }
        }

        private static bool IsOpponentSkillCard(
            CardSnapshot card,
            bool requireMonsterTooltip)
        {
            if (card == null)
            {
                return false;
            }
            string cardType = card.card_type ?? "";
            if (!string.Equals(cardType, "Skill", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            string context = (card.ui_context ?? "").ToLowerInvariant();
            bool monsterTooltip = context.Contains("tooltip_monsterboard");
            if (requireMonsterTooltip && !monsterTooltip)
            {
                return false;
            }
            return (monsterTooltip
                    || context.Contains("opponentboardanchor")
                    || context.Contains("enemy")
                    || context.Contains("monster"))
                && !context.Contains("merchant")
                && !context.Contains("shop")
                && !context.Contains("playeritemsocket")
                && !context.Contains("playerstoragesocket");
        }

        private static bool IsOpponentItemCard(
            CardSnapshot card,
            bool requireMonsterTooltip)
        {
            if (card == null)
            {
                return false;
            }
            string cardType = card.card_type ?? "";
            if (!string.Equals(cardType, "Item", StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            string context = (card.ui_context ?? "").ToLowerInvariant();
            bool monsterTooltip = context.Contains("tooltip_monsterboard");
            if (requireMonsterTooltip && !monsterTooltip)
            {
                return false;
            }
            return (context.Contains("opponentitemsocket_")
                    || context.Contains("opponentboardanchor")
                    || monsterTooltip)
                && !context.Contains("merchant")
                && !context.Contains("shop")
                && !context.Contains("playeritemsocket")
                && !context.Contains("playerstoragesocket");
        }

        private static bool IsMonsterTooltipCard(CardSnapshot card)
        {
            return card != null
                && (card.ui_context ?? "").IndexOf(
                    "Tooltip_MonsterBoard",
                    StringComparison.OrdinalIgnoreCase) >= 0;
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

        private static void UpsertCardById(List<CardSnapshot> cards, CardSnapshot card)
        {
            if (cards == null || card == null)
            {
                return;
            }

            int existingIndex = cards.FindIndex(existing =>
                !string.IsNullOrEmpty(existing.id)
                && existing.id == card.id);
            if (existingIndex >= 0)
            {
                cards[existingIndex] = card;
            }
            else
            {
                cards.Add(card);
            }
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
                return;
            }

            int existingIndex = cards.FindIndex(existing => CardIdentity(existing) == identity);
            if (existingIndex >= 0 && ShouldPreferOwnedCardSnapshot(card, cards[existingIndex]))
            {
                cards[existingIndex] = card;
            }
        }

        private static string CardIdentity(CardSnapshot card)
        {
            if (card == null)
            {
                return "";
            }

            return !string.IsNullOrEmpty(card.id)
                ? "id:" + card.id
                : "template:" + (card.template_id ?? "") + "|name:" + (card.name ?? "");
        }

        private static bool ShouldPreferOwnedCardSnapshot(CardSnapshot candidate, CardSnapshot existing)
        {
            int candidateScore = OwnedCardLocationScore(candidate);
            int existingScore = OwnedCardLocationScore(existing);
            if (candidateScore != existingScore)
            {
                return candidateScore > existingScore;
            }

            bool candidateHasContext = !string.IsNullOrEmpty(candidate.ui_context);
            bool existingHasContext = !string.IsNullOrEmpty(existing.ui_context);
            if (candidateHasContext != existingHasContext)
            {
                return candidateHasContext;
            }

            return !string.IsNullOrEmpty(candidate.section)
                && string.IsNullOrEmpty(existing.section);
        }

        private static int OwnedCardLocationScore(CardSnapshot card)
        {
            if (card == null)
            {
                return 0;
            }

            if (IsBoardOwnedItem(card))
            {
                return 3;
            }
            if (IsStashOwnedItem(card))
            {
                return 2;
            }
            if (IsOwnedItemCard(card))
            {
                return 1;
            }
            return 0;
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
                || lower.Contains("income")
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
                    || component.gameObject == null)
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

        private int? TryReadOpponentUiHealth()
        {
            int bestScore = int.MinValue;
            int? bestHealth = null;
            MonoBehaviour[] components = Resources.FindObjectsOfTypeAll<MonoBehaviour>();
            foreach (MonoBehaviour component in components)
            {
                if (component == null
                    || component.gameObject == null)
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
                bool monsterBoardContext = hierarchy.Contains("tooltip_monsterboard")
                    || hierarchy.Contains("monsterboard")
                    || hierarchy.Contains("monster_board");
                bool opponentContext = monsterBoardContext
                    || hierarchy.Contains("opponenthealth")
                    || hierarchy.Contains("opponent_health")
                    || hierarchy.Contains("enemyhealth")
                    || hierarchy.Contains("enemy_health");
                bool healthContext = objectName.Contains("hp")
                    || objectName.Contains("health")
                    || hierarchy.Contains("hpnumber")
                    || hierarchy.Contains("hp_number")
                    || hierarchy.Contains("healthnumber")
                    || hierarchy.Contains("health_number")
                    || hierarchy.Contains("currenthealth")
                    || hierarchy.Contains("current_health");
                if (!opponentContext
                    || !healthContext
                    || hierarchy.Contains("reward")
                    || hierarchy.Contains("regen"))
                {
                    continue;
                }

                int parsed;
                List<string> diagnostics = new List<string>();
                if (!TryReadIntegerFromComponent(component, out parsed, diagnostics)
                    || parsed <= 0
                    || parsed > 999999)
                {
                    continue;
                }

                int score = ScoreUiResourceObject(gameObject);
                if (monsterBoardContext)
                {
                    score += 1000;
                }
                if (objectName.Contains("hpnumber")
                    || objectName.Contains("hp_number")
                    || objectName.Contains("healthnumber")
                    || objectName.Contains("health_number")
                    || objectName.Contains("health_value")
                    || objectName.Contains("currenthealth")
                    || objectName.Contains("current_health"))
                {
                    score += 500;
                }
                else if (objectName.Contains("hp") || objectName.Contains("health"))
                {
                    score += 300;
                }

                if (score > bestScore)
                {
                    bestScore = score;
                    bestHealth = parsed;
                }
            }

            return bestScore >= 500 ? bestHealth : null;
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
            HashSet<string> detailedEventOptionIds,
            float minSeenAt)
        {
            List<CardSnapshot> capturedCards =
                RuntimeStateCache.GetCurrentVisibleCards(30f, minSeenAt);
            foreach (CardSnapshot recentCard in RuntimeStateCache.GetCapturedUiCards(15f, minSeenAt))
            {
                if (recentCard == null || string.IsNullOrEmpty(recentCard.id))
                {
                    continue;
                }
                if (capturedCards.Any(card => card != null && card.id == recentCard.id))
                {
                    continue;
                }
                capturedCards.Add(recentCard);
            }
            List<CardSnapshot> currentEventCards = capturedCards
                .Where(IsCurrentEventOptionCard)
                .ToList();

            bool uiLooksLikeFullReplacement =
                currentEventCards.Count > 0
                && (
                    eventOptionIdSet.Count == 0
                    || currentEventCards.Count >= eventOptionIdSet.Count
                    || !currentEventCards.Any(card => eventOptionIdSet.Contains(card.id))
                );
            if (uiLooksLikeFullReplacement)
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
                bool eventCard = IsCurrentEventOptionCard(card);
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

            EventOptionSnapshot option = new EventOptionSnapshot
            {
                id = card.id,
                template_id = card.template_id,
                name = card.name,
                kind = EventKindFromCard(card),
                card_type = card.card_type,
                section = card.section,
                source = string.IsNullOrEmpty(card.source) ? "unknown" : card.source,
            };

            AddTemplateBranchSummaries(option);
            snapshot.event_options_detailed.Add(option);
        }

        private static void AddTemplateBranchSummaries(EventOptionSnapshot option)
        {
            if (option == null || string.IsNullOrEmpty(option.template_id))
            {
                return;
            }

            object template = TryResolveStaticCardTemplate(option.template_id);
            if (template == null)
            {
                return;
            }

            HashSet<string> branchTemplateIds = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            int nextEncounterBranchCount = 0;

            foreach (object branch in EnumerateValues(ReadFirstMember(
                template,
                "NextEncounterOnSelection",
                "<NextEncounterOnSelection>k__BackingField")))
            {
                string branchTemplateId = StringValue(ReadMember(branch, "Id"));
                if (string.IsNullOrEmpty(branchTemplateId))
                {
                    branchTemplateId = StringValue(ReadMember(branch, "TemplateId"));
                }
                if (!string.IsNullOrEmpty(branchTemplateId))
                {
                    nextEncounterBranchCount++;
                }
                AddBranchSummary(
                    option,
                    branchTemplateIds,
                    branchTemplateId,
                    "next_encounter_on_selection");
            }

            object selectionContext = ReadFirstMember(
                template,
                "SelectionContext",
                "<SelectionContext>k__BackingField");
            object spawnContext = ReadFirstMember(
                selectionContext,
                "SpawnContext",
                "<SpawnContext>k__BackingField");
            List<string> selectionSpawnIds = CollectExplicitSpawnContextTemplateIds(spawnContext).ToList();
            foreach (string branchTemplateId in selectionSpawnIds)
            {
                AddBranchSummary(
                    option,
                    branchTemplateIds,
                    branchTemplateId,
                    "selection_context_spawn");
            }

            int abilitySpawnIdCount = 0;
            foreach (object ability in EnumerateValues(ReadFirstMember(
                template,
                "Abilities",
                "<Abilities>k__BackingField")))
            {
                object action = ReadFirstMember(ability, "Action", "<Action>k__BackingField");
                object actionSpawnContext = ReadFirstMember(
                    action,
                    "SpawnContext",
                    "<SpawnContext>k__BackingField");
                List<string> abilitySpawnIds = CollectExplicitSpawnContextTemplateIds(actionSpawnContext).ToList();
                abilitySpawnIdCount += abilitySpawnIds.Count;
                foreach (string branchTemplateId in abilitySpawnIds)
                {
                    AddBranchSummary(
                        option,
                        branchTemplateIds,
                        branchTemplateId,
                        "ability_spawn_context");
                }
            }

            if (option.branches.Count == 0 && !branchSummaryDiagnosticLogged)
            {
                branchSummaryDiagnosticLogged = true;
                sharedLogger?.LogInfo(
                    "Event branch diagnostic template_id="
                    + option.template_id
                    + " templateResolved="
                    + (template != null)
                    + " staticCacheCount="
                    + (staticCardTemplatesById == null ? "null" : staticCardTemplatesById.Count.ToString())
                    + " selectionContext="
                    + SafeTypeName(selectionContext)
                    + " spawnContext="
                    + SafeTypeName(spawnContext)
                    + " nextEncounterIds="
                    + nextEncounterBranchCount
                    + " selectionSpawnIds="
                    + selectionSpawnIds.Count
                    + " abilitySpawnIds="
                    + abilitySpawnIdCount);
            }
        }

        private static void AddBranchSummary(
            EventOptionSnapshot option,
            HashSet<string> branchTemplateIds,
            string branchTemplateId,
            string source)
        {
            if (option == null || string.IsNullOrEmpty(branchTemplateId))
            {
                return;
            }

            if (string.Equals(branchTemplateId, option.template_id, StringComparison.OrdinalIgnoreCase)
                || !branchTemplateIds.Add(branchTemplateId))
            {
                return;
            }

            object branchTemplate = TryResolveStaticCardTemplate(branchTemplateId);
            option.branches.Add(new EventOptionBranchSnapshot
            {
                template_id = branchTemplateId,
                name = ReadTemplateDisplayName(branchTemplate),
                card_type = ReadTemplateCardType(branchTemplate),
                kind = EventKindFromTemplate(branchTemplateId, branchTemplate),
                source = source,
            });
        }

        private static IEnumerable<string> CollectExplicitSpawnContextTemplateIds(object spawnContext)
        {
            if (spawnContext == null)
            {
                yield break;
            }

            HashSet<string> ids = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string[] memberNames =
            {
                "Id",
                "Ids",
                "TemplateId",
                "TemplateIds",
                "CardId",
                "CardIds",
                "CardTemplateId",
                "CardTemplateIds",
                "EncounterId",
                "EncounterIds",
                "<Ids>k__BackingField",
                "<TemplateIds>k__BackingField",
                "<CardIds>k__BackingField",
                "<CardTemplateIds>k__BackingField",
            };

            foreach (string memberName in memberNames)
            {
                foreach (object child in EnumerateValues(ReadMember(spawnContext, memberName)))
                {
                    string id = StringValue(child);
                    if (!string.IsNullOrEmpty(id) && ids.Add(id))
                    {
                        yield return id;
                    }
                }
            }
        }

        private static object TryResolveStaticCardTemplate(string templateId)
        {
            if (string.IsNullOrEmpty(templateId))
            {
                return null;
            }

            if (staticCardTemplatesById == null)
            {
                staticCardTemplatesById = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            }

            object cached;
            if (staticCardTemplatesById.TryGetValue(templateId, out cached))
            {
                return cached;
            }

            object manager = TryGetStaticGameDataManager();
            if (manager == null)
            {
                return null;
            }

            Guid guid;
            object template = null;
            if (Guid.TryParse(templateId, out guid))
            {
                template = InvokeMemberWithArgument(manager, "GetCardById", guid);
            }
            if (template == null)
            {
                template = InvokeMemberWithArgument(manager, "GetCardById", templateId);
            }

            template = UnwrapTemplate(template);
            if (template != null)
            {
                staticCardTemplatesById[templateId] = template;
            }
            return template;
        }

        private static object TryGetStaticGameDataManager()
        {
            Type dataType = FindLoadedType("TheBazaar.Data");
            if (dataType == null)
            {
                return null;
            }

            object isManagerCreated = InvokeParameterlessMember(dataType, "IsManagerCreated");
            if (isManagerCreated is bool && !(bool)isManagerCreated)
            {
                if (!staticDataUnavailableLogged)
                {
                    staticDataUnavailableLogged = true;
                    sharedLogger?.LogInfo("TheBazaar.Data static manager is not ready; event branch summaries will wait.");
                }
                return null;
            }

            object manager = InvokeParameterlessMember(dataType, "GetStatic");
            if (manager == null && !(isManagerCreated is bool))
            {
                manager = ReadStaticMember(dataType, "Static")
                    ?? ReadStaticMember(dataType, "GameData")
                    ?? ReadStaticMember(dataType, "Manager");
            }

            if (manager == null && !staticDataUnavailableLogged)
            {
                staticDataUnavailableLogged = true;
                sharedLogger?.LogInfo("TheBazaar.Data static manager was unavailable; event branch summaries skipped for now.");
            }
            return manager;
        }

        private static object InvokeMemberWithArgument(object target, string name, object argument)
        {
            if (target == null || string.IsNullOrEmpty(name))
            {
                return null;
            }

            Type type = target as Type ?? target.GetType();
            object instance = target is Type ? null : target;
            BindingFlags flags = BindingFlags.Instance
                | BindingFlags.Static
                | BindingFlags.Public
                | BindingFlags.NonPublic;
            foreach (MethodInfo method in type.GetMethods(flags))
            {
                if (!string.Equals(method.Name, name, StringComparison.Ordinal)
                    || method.GetParameters().Length != 1)
                {
                    continue;
                }

                try
                {
                    return method.Invoke(instance, new[] { argument });
                }
                catch
                {
                    try
                    {
                        object converted = Convert.ChangeType(
                            argument,
                            method.GetParameters()[0].ParameterType);
                        return method.Invoke(instance, new[] { converted });
                    }
                    catch
                    {
                    }
                }
            }
            return null;
        }

        private static object UnwrapTemplate(object value)
        {
            if (value == null)
            {
                return null;
            }

            object nested = ReadMember(value, "Value");
            if (nested != null)
            {
                value = nested;
            }

            nested = ReadMember(value, "CardTemplate");
            if (nested != null)
            {
                return nested;
            }

            nested = ReadMember(value, "Template");
            return nested ?? value;
        }

        private static IEnumerable<string> ReadTemplateIds(object template)
        {
            object attributes = ReadMember(template, "Attributes") ?? ReadMember(template, "Data");
            string[] names = { "TemplateId", "TemplateID", "Id", "SourceId", "SourceID" };
            foreach (string name in names)
            {
                string value = StringValue(ReadMember(template, name));
                if (!string.IsNullOrEmpty(value))
                {
                    yield return value;
                }

                value = StringValue(ReadMember(attributes, name));
                if (!string.IsNullOrEmpty(value))
                {
                    yield return value;
                }
            }
        }

        private static string ReadTemplateDisplayName(object template)
        {
            if (template == null)
            {
                return null;
            }

            object attributes = ReadMember(template, "Attributes") ?? ReadMember(template, "Data");
            string value = ReadLocalizedText(ReadMember(template, "Title"));
            if (!string.IsNullOrEmpty(value))
            {
                return value;
            }

            value = ReadLocalizedText(ReadMember(attributes, "Title"));
            if (!string.IsNullOrEmpty(value))
            {
                return value;
            }

            string[] names = { "DisplayName", "Name", "LocalizedName", "InternalName", "CardName" };
            foreach (string name in names)
            {
                value = StringValue(ReadMember(template, name));
                if (!string.IsNullOrEmpty(value))
                {
                    return value;
                }

                value = StringValue(ReadMember(attributes, name));
                if (!string.IsNullOrEmpty(value))
                {
                    return value;
                }
            }

            return null;
        }

        private static string ReadTemplateCardType(object template)
        {
            object attributes = ReadMember(template, "Attributes") ?? ReadMember(template, "Data");
            string value = FirstStringFromValue(ReadMember(template, "Types"))
                ?? FirstStringFromValue(ReadMember(attributes, "Types"))
                ?? StringValue(ReadMember(template, "Type"))
                ?? StringValue(ReadMember(attributes, "Type"))
                ?? StringValue(ReadMember(template, "CardType"))
                ?? StringValue(ReadMember(attributes, "CardType"));
            return value;
        }

        private static string EventKindFromTemplate(string templateId, object template)
        {
            string cardType = ReadTemplateCardType(template) ?? "";
            if (cardType.IndexOf("Event", StringComparison.OrdinalIgnoreCase) >= 0
                || cardType.IndexOf("Encounter", StringComparison.OrdinalIgnoreCase) >= 0
                || (templateId ?? "").StartsWith("enc_", StringComparison.OrdinalIgnoreCase))
            {
                return "encounter";
            }
            if ((templateId ?? "").StartsWith("ste_", StringComparison.OrdinalIgnoreCase)
                || cardType.IndexOf("Step", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "step";
            }
            if ((templateId ?? "").StartsWith("com_", StringComparison.OrdinalIgnoreCase)
                || cardType.IndexOf("Combat", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return "combat";
            }
            if ((templateId ?? "").StartsWith("pvp_", StringComparison.OrdinalIgnoreCase))
            {
                return "pvp";
            }
            return "unknown";
        }

        private static string FirstStringFromValue(object value)
        {
            foreach (object item in EnumerateValues(value))
            {
                string text = StringValue(item);
                if (!string.IsNullOrEmpty(text))
                {
                    return text;
                }
            }
            return null;
        }

        private static string ReadLocalizedText(object value)
        {
            if (value == null)
            {
                return null;
            }

            string direct = value as string;
            if (!string.IsNullOrEmpty(direct))
            {
                return direct;
            }

            string[] names = { "Text", "Value", "English", "En", "LocalizedText" };
            foreach (string name in names)
            {
                string text = StringValue(ReadMember(value, name));
                if (!string.IsNullOrEmpty(text))
                {
                    return text;
                }
            }

            return null;
        }

        private static IEnumerable<object> EnumerateValues(object value)
        {
            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null || value is string)
            {
                if (value != null)
                {
                    yield return value;
                }
                yield break;
            }

            foreach (object item in enumerable)
            {
                object unwrapped = UnwrapEnumerableItemValue(item);
                if (unwrapped != null)
                {
                    yield return unwrapped;
                }
            }
        }

        private static object UnwrapEnumerableItemValue(object item)
        {
            if (item == null)
            {
                return null;
            }

            if (item is DictionaryEntry)
            {
                return ((DictionaryEntry)item).Value;
            }

            Type type = item.GetType();
            if (type.FullName != null
                && type.FullName.StartsWith(
                    "System.Collections.Generic.KeyValuePair",
                    StringComparison.Ordinal))
            {
                object key = ReadMember(item, "Key");
                object value = ReadMember(item, "Value");
                return value ?? key ?? item;
            }

            return item;
        }

        private static object ReadStaticMember(Type type, string name)
        {
            if (type == null || string.IsNullOrEmpty(name))
            {
                return null;
            }

            BindingFlags flags = BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;
            FieldInfo field = type.GetField(name, flags);
            if (field != null)
            {
                try
                {
                    return field.GetValue(null);
                }
                catch
                {
                    return null;
                }
            }

            PropertyInfo property = type.GetProperty(name, flags);
            if (property == null)
            {
                return null;
            }

            try
            {
                return property.GetValue(null, null);
            }
            catch
            {
                return null;
            }
        }

        private static object ReadMember(object target, string name)
        {
            if (target == null || string.IsNullOrEmpty(name))
            {
                return null;
            }

            Type type = target.GetType();
            BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            FieldInfo field = type.GetField(name, flags);
            if (field != null)
            {
                try
                {
                    return field.GetValue(target);
                }
                catch
                {
                    return null;
                }
            }

            PropertyInfo property = type.GetProperty(name, flags);
            if (property == null)
            {
                return null;
            }

            try
            {
                return property.GetValue(target, null);
            }
            catch
            {
                return null;
            }
        }

        private static object ReadFirstMember(object target, params string[] names)
        {
            if (target == null || names == null)
            {
                return null;
            }

            foreach (string name in names)
            {
                object value = ReadMember(target, name);
                if (value != null)
                {
                    return value;
                }
            }

            return null;
        }

        private static string SafeTypeName(object value)
        {
            if (value == null)
            {
                return "null";
            }

            Type type = value as Type ?? value.GetType();
            return type.FullName ?? type.Name;
        }

        private static object InvokeParameterlessMember(object target, string name)
        {
            if (target == null || string.IsNullOrEmpty(name))
            {
                return null;
            }

            Type type = target as Type ?? target.GetType();
            object instance = target is Type ? null : target;
            MethodInfo method = type.GetMethod(
                name,
                BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic,
                null,
                Type.EmptyTypes,
                null);
            if (method == null)
            {
                return null;
            }

            try
            {
                return method.Invoke(instance, null);
            }
            catch
            {
                return null;
            }
        }

        private static Type FindLoadedType(string fullName)
        {
            foreach (Type type in FindLoadedTypes())
            {
                if (type != null && string.Equals(type.FullName, fullName, StringComparison.Ordinal))
                {
                    return type;
                }
            }
            return null;
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
                ui_context = card.ui_context,
                price = string.Equals(card.card_type, "Skill", StringComparison.OrdinalIgnoreCase)
                    ? null
                    : card.price,
                runtime_type = card.runtime_type,
            };
            clone.enchantments.AddRange(card.enchantments);
            clone.runtime_sources.AddRange(card.runtime_sources);
            CopyObjectDictionary(card.runtime_values, clone.runtime_values);
            CopyObjectDictionary(card.current_attributes, clone.current_attributes);
            CopyObjectDictionary(card.base_attributes, clone.base_attributes);
            CopyObjectDictionary(card.attribute_modifiers, clone.attribute_modifiers);
            return clone;
        }

        private static void CopyObjectDictionary(
            Dictionary<string, object> source,
            Dictionary<string, object> target)
        {
            if (source == null || target == null)
            {
                return;
            }

            foreach (KeyValuePair<string, object> item in source)
            {
                target[item.Key] = item.Value;
            }
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
                string templateId = StringValue(GetField(item, "TemplateId"));
                CardSnapshot card = new CardSnapshot
                {
                    id = StringValue(GetField(item, "InstanceId")),
                    template_id = templateId,
                    name = ResolveCardNameFromTemplateId(templateId),
                    rarity = NormalizeTier(StringValue(GetField(item, "Tier"))),
                    section = StringValue(GetField(item, "Section")),
                    card_type = StringValue(GetField(item, "Type")),
                    source = "game_state",
                };

                if (HasValue(enchantment))
                {
                    card.enchantments.Add(StringValue(enchantment));
                }

                RuntimeCardInstanceReader.AddRuntimeSnapshot(
                    card,
                    item,
                    "game_state_card");
                CardSnapshotPosition.Fill(card);

                yield return card;
            }
        }

        private static string ResolveCardNameFromTemplateId(string templateId)
        {
            if (string.IsNullOrEmpty(templateId))
            {
                return null;
            }

            try
            {
                return ReadTemplateDisplayName(TryResolveStaticCardTemplate(templateId));
            }
            catch (Exception)
            {
                return null;
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

        private static int? FindAttributeExact(
            Dictionary<string, int> attributes,
            params string[] names)
        {
            foreach (string name in names)
            {
                int value;
                if (attributes.TryGetValue(name, out value))
                {
                    return value;
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
