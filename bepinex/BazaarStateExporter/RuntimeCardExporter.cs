using BepInEx.Logging;
using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using UnityEngine;

namespace BazaarStateExporter
{
    public static class RuntimeCardExporter
    {
        public static RuntimeCardExportResult TryExportLatestCards(string outputPath, ManualLogSource logger)
        {
            RuntimeCardExportResult result = new RuntimeCardExportResult();
            string liveCardsPath = ResolveLiveCardsPath(outputPath);
            string liveMonstersPath = ResolveLiveMonstersPath(outputPath);
            string diagnosticsPath = ResolveDiagnosticsPath(outputPath);
            result.OutputPath = liveCardsPath;
            result.MonstersOutputPath = liveMonstersPath;
            result.DiagnosticsPath = diagnosticsPath;

            // Keep the one-shot export light. Full diagnostics scan every loaded type and
            // Unity object, which is useful for discovery but too expensive during play.
            result.ScannedAssemblyCount = 0;
            result.CandidateTypeCount = 0;
            result.CandidateObjectCount = 0;

            try
            {
                List<Dictionary<string, object>> cards = new List<Dictionary<string, object>>();
                List<Dictionary<string, object>> monsters = new List<Dictionary<string, object>>();
                object clientCacheType = FindLoadedType("TheBazaar.ClientCache");
                result.FoundClientCache = clientCacheType != null;

                object runConfig = null;
                if (clientCacheType is Type)
                {
                    TryGetStaticMemberValue((Type)clientCacheType, "RunConfig", out runConfig);
                    if (runConfig == null)
                    {
                        object fallbackValue;
                        if (TryGetStaticMemberValue((Type)clientCacheType, "runConfig", out fallbackValue))
                        {
                            runConfig = fallbackValue;
                        }
                    }
                }

                result.FoundRunConfigurationCache = runConfig != null;
                result.FoundCardMap = TryCollectCardsFromRunConfig(runConfig, cards, result, logger);

                if (cards.Count == 0)
                {
                    TryCollectCardsFromBppStaticDataAccess(cards, result, logger);
                }

                result.FoundCardMap = cards.Count > 0;
                result.ExportedCardCount = cards.Count;
                if (cards.Count > 0)
                {
                    WriteJsonAtomic(liveCardsPath, cards);
                }

                object staticDataManager = TryGetStaticDataManager();
                result.FoundStaticDataManager = staticDataManager != null;
                if (TryCollectMonstersFromStaticData(staticDataManager, monsters, result, logger))
                {
                    result.ExportedMonsterCount = monsters.Count;
                    WriteJsonAtomic(liveMonstersPath, monsters);
                }

                if (logger != null)
                {
                    logger.LogInfo("Runtime card export: found TheBazaar.ClientCache=" + result.FoundClientCache);
                    logger.LogInfo("Runtime card export: found RunConfig=" + result.FoundRunConfigurationCache);
                    logger.LogInfo("Runtime card export: RunConfig is null=" + (runConfig == null));
                    logger.LogInfo("Runtime card export: RunConfig type=" + SafeTypeName(runConfig));
                    logger.LogInfo("Runtime card export: RunConfig candidate members count=" + CountCandidateMembers(runConfig));
                    logger.LogInfo("Runtime card export: found BazaarPlusPlus fallback=" + result.FoundBazaarPlusPlusFallback);
                    logger.LogInfo("Runtime card export: BPP ready manager type=" + result.BppReadyManagerType);
                    logger.LogInfo("Runtime card export: LoadCardMap result type=" + result.LoadCardMapResultType);
                    logger.LogInfo("Runtime card export: LoadCardMap count=" + result.LoadCardMapCount);
                    logger.LogInfo("Runtime card export: found CardMap=" + result.FoundCardMap);
                    logger.LogInfo("Runtime card export: exported card count=" + result.ExportedCardCount);
                    logger.LogInfo("Runtime card export: found StaticDataManager=" + result.FoundStaticDataManager);
                    logger.LogInfo("Runtime card export: MonsterMap count=" + result.MonsterMapCount);
                    logger.LogInfo("Runtime card export: exported monster count=" + result.ExportedMonsterCount);
                    logger.LogInfo("Runtime card export: found Karnok=" + result.FoundKarnok);
                    logger.LogInfo("Runtime card export: live_cards_raw.json write path=" + liveCardsPath);
                    logger.LogInfo("Runtime card export: live_monsters_raw.json write path=" + liveMonstersPath);
                    logger.LogInfo("Runtime card export: cache diagnostics skipped for performance.");
                    if (cards.Count == 0)
                    {
                        logger.LogInfo("Runtime card export: exported card count is 0, so live_cards_raw.json was not overwritten.");
                    }
                }
            }
            catch (Exception ex)
            {
                if (logger != null)
                {
                    logger.LogWarning("Runtime card export failed: " + ex);
                }
            }

            return result;
        }

        private static CacheDiagnostics BuildCacheDiagnostics()
        {
            CacheDiagnostics diagnostics = new CacheDiagnostics();
            HashSet<string> keywords = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            {
                "Cache",
                "Card",
                "Template",
                "RunConfiguration",
                "Collection",
                "Catalog",
                "Item",
                "Skill",
                "Ability",
                "Abilities",
                "Aura",
                "Auras",
                "Effect",
                "Effects",
                "Action",
                "Actions",
                "Trigger",
                "Triggers",
                "Condition",
                "Conditions",
                "Value",
                "Values",
                "Spawn",
                "Spawning",
            };

            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (assembly == null)
                {
                    continue;
                }

                diagnostics.AddAssembly(assembly);
            }

            foreach (Type type in FindLoadedTypes())
            {
                if (type == null)
                {
                    continue;
                }

                ScanTypeForDiagnostics(type, keywords, diagnostics);
            }

            foreach (UnityEngine.Object unityObject in FindLoadedUnityObjects())
            {
                if (unityObject == null)
                {
                    continue;
                }

                ScanUnityObjectForDiagnostics(unityObject, keywords, diagnostics);
            }

            return diagnostics;
        }

        private static bool TryCollectCardsFromRunConfig(object runConfigRoot, List<Dictionary<string, object>> cards, RuntimeCardExportResult result, ManualLogSource logger)
        {
            result.LoadCardMapResultType = null;
            if (runConfigRoot == null || cards == null)
            {
                return false;
            }

            Type runtimeType = runConfigRoot.GetType();
            if (runtimeType == null)
            {
                return false;
            }

            object workingRoot = runConfigRoot;
            object nestedValue;
            if (TryGetMemberValue(runConfigRoot, "Value", out nestedValue) && nestedValue != null)
            {
                workingRoot = nestedValue;
            }

            List<string> priorityNames = new List<string>
            {
                "GetCardMap",
                "CardMap",
                "Cards",
                "CardTemplates",
                "StaticCards",
                "StaticCardTemplates",
                "Items",
                "Skills",
                "GetCardTemplate",
                "HasStaticCardTemplate",
                "loadedCards",
                "_loadedCards",
                "_cardMap",
                "cardMap",
                "_cards",
                "_cardTemplates",
            };

            if (TryCollectCardsFromMemberNames(workingRoot, priorityNames, cards, result))
            {
                return cards.Count > 0;
            }

            HashSet<string> keywords = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            {
                "Card",
                "Cards",
                "Template",
                "Templates",
                "Map",
                "Static",
                "Item",
                "Skill",
                "Loaded",
            };

            return TryCollectCardsFromMatchingMembers(workingRoot, keywords, cards, result);
        }

        private static bool TryCollectCardsFromBppStaticDataAccess(List<Dictionary<string, object>> cards, RuntimeCardExportResult result, ManualLogSource logger)
        {
            Type bppType = FindLoadedType("BazaarPlusPlus.GameInterop.StaticCards.BppStaticDataAccess");
            result.FoundBazaarPlusPlusFallback = bppType != null;
            if (bppType == null)
            {
                return false;
            }

            object manager;
            if (!TryInvokeParameterlessMember(bppType, "TryGetReadyManagerObject", out manager) || manager == null)
            {
                return false;
            }

            result.BppReadyManagerType = SafeTypeName(manager);

            object map;
            if (!TryInvokeMember(bppType, "LoadCardMap", new[] { manager }, out map) || map == null)
            {
                return false;
            }

            result.LoadCardMapResultType = SafeTypeName(map);
            int mapCount;
            if (TryGetCollectionCount(map, out mapCount))
            {
                result.LoadCardMapCount = mapCount;
            }

            AppendCardsFromValue(map, cards, result);
            return cards.Count > 0;
        }

        private static object TryGetStaticDataManager()
        {
            Type dataType = FindLoadedType("TheBazaar.Data");
            object manager;
            if (dataType != null && TryInvokeMember(dataType, "GetStatic", new object[0], out manager) && manager != null)
            {
                return manager;
            }

            Type bppType = FindLoadedType("BazaarPlusPlus.GameInterop.StaticCards.BppStaticDataAccess");
            if (bppType != null && TryInvokeParameterlessMember(bppType, "TryGetReadyManagerObject", out manager) && manager != null)
            {
                return manager;
            }

            return null;
        }

        private static bool TryCollectMonstersFromStaticData(
            object staticDataManager,
            List<Dictionary<string, object>> monsters,
            RuntimeCardExportResult result,
            ManualLogSource logger)
        {
            if (staticDataManager == null || monsters == null)
            {
                return false;
            }

            object monsterMap = FindMemberValue(staticDataManager, "_monsters", "Monsters", "MonsterMap");
            if (monsterMap == null)
            {
                return false;
            }

            int monsterMapCount;
            if (TryGetCollectionCount(monsterMap, out monsterMapCount))
            {
                result.MonsterMapCount = monsterMapCount;
            }

            object cardMap = null;
            TryInvokeMember(staticDataManager, "GetCardMap", new object[0], out cardMap);
            Dictionary<string, List<Dictionary<string, object>>> encountersByMonsterId =
                BuildCombatEncounterLinks(cardMap);

            foreach (object monster in EnumerateDictionaryValues(monsterMap))
            {
                Dictionary<string, object> record =
                    BuildMonsterRecord(monster, staticDataManager, encountersByMonsterId);
                if (record != null)
                {
                    monsters.Add(record);
                }
            }

            return monsters.Count > 0;
        }

        private static Dictionary<string, List<Dictionary<string, object>>> BuildCombatEncounterLinks(object cardMap)
        {
            Dictionary<string, List<Dictionary<string, object>>> result =
                new Dictionary<string, List<Dictionary<string, object>>>(StringComparer.OrdinalIgnoreCase);

            foreach (object template in EnumerateDictionaryValues(cardMap))
            {
                if (template == null)
                {
                    continue;
                }

                object combatant = FindMemberValue(template, "CombatantType", "<CombatantType>k__BackingField");
                string monsterTemplateId = ReadStringFromSources(
                    combatant,
                    null,
                    "MonsterTemplateId",
                    "<MonsterTemplateId>k__BackingField");
                if (string.IsNullOrEmpty(monsterTemplateId))
                {
                    continue;
                }

                Dictionary<string, object> card = BuildCardRecord(template);
                if (card == null)
                {
                    continue;
                }

                card["monster_template_id"] = monsterTemplateId;
                string level = ReadStringFromSources(combatant, null, "Level", "<Level>k__BackingField");
                if (!string.IsNullOrEmpty(level))
                {
                    card["monster_level"] = level;
                }

                List<Dictionary<string, object>> links;
                if (!result.TryGetValue(monsterTemplateId, out links))
                {
                    links = new List<Dictionary<string, object>>();
                    result[monsterTemplateId] = links;
                }
                links.Add(card);
            }

            return result;
        }

        private static Dictionary<string, object> BuildMonsterRecord(
            object monster,
            object staticDataManager,
            Dictionary<string, List<Dictionary<string, object>>> encountersByMonsterId)
        {
            if (monster == null)
            {
                return null;
            }

            string id = ReadStringFromSources(monster, null, "Id", "<Id>k__BackingField");
            string internalName = ReadStringFromSources(
                monster,
                null,
                "InternalName",
                "<InternalName>k__BackingField");
            object player = FindMemberValue(monster, "Player", "<Player>k__BackingField");

            Dictionary<string, object> record =
                new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            record["id"] = EmptyToNull(id);
            record["template_id"] = EmptyToNull(id);
            record["internal_name"] = EmptyToNull(internalName);
            record["name"] = EmptyToNull(internalName);
            record["health"] = TryReadPlayerHealth(player);
            record["attributes"] = SerializeRawEffectValue(
                FindMemberValue(player, "Attributes", "<Attributes>k__BackingField"),
                0,
                new HashSet<object>(ReferenceEqualityComparer.Instance));
            record["items"] = BuildMonsterItemInstances(player, staticDataManager);
            record["skills"] = BuildMonsterSkillInstances(player, staticDataManager);

            List<Dictionary<string, object>> encounters;
            if (!string.IsNullOrEmpty(id)
                && encountersByMonsterId != null
                && encountersByMonsterId.TryGetValue(id, out encounters))
            {
                record["encounters"] = encounters;
            }
            else
            {
                record["encounters"] = new List<Dictionary<string, object>>();
            }

            return record;
        }

        private static List<Dictionary<string, object>> BuildMonsterItemInstances(
            object player,
            object staticDataManager)
        {
            List<Dictionary<string, object>> result = new List<Dictionary<string, object>>();
            object hand = FindMemberValue(player, "Hand", "<Hand>k__BackingField");
            AppendCardInstances(result, FindMemberValue(hand, "Items", "<Items>k__BackingField"), "Hand", staticDataManager);
            object stash = FindMemberValue(player, "Stash", "<Stash>k__BackingField");
            AppendCardInstances(result, FindMemberValue(stash, "Items", "<Items>k__BackingField"), "Stash", staticDataManager);
            return result;
        }

        private static List<Dictionary<string, object>> BuildMonsterSkillInstances(
            object player,
            object staticDataManager)
        {
            List<Dictionary<string, object>> result = new List<Dictionary<string, object>>();
            AppendCardInstances(
                result,
                FindMemberValue(player, "Skills", "<Skills>k__BackingField"),
                "Skill",
                staticDataManager);
            return result;
        }

        private static void AppendCardInstances(
            List<Dictionary<string, object>> result,
            object instances,
            string section,
            object staticDataManager)
        {
            if (result == null || instances == null)
            {
                return;
            }

            foreach (object instance in EnumerateDictionaryValues(instances))
            {
                Dictionary<string, object> record =
                    BuildCardInstanceRecord(instance, section, staticDataManager);
                if (record != null)
                {
                    result.Add(record);
                }
            }
        }

        private static Dictionary<string, object> BuildCardInstanceRecord(
            object instance,
            string section,
            object staticDataManager)
        {
            if (instance == null)
            {
                return null;
            }

            string templateId = ReadStringFromSources(
                instance,
                null,
                "TemplateId",
                "<TemplateId>k__BackingField");
            Dictionary<string, object> record =
                new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            record["id"] = EmptyToNull(ReadStringFromSources(
                instance,
                null,
                "InstanceId",
                "<InstanceId>k__BackingField"));
            record["template_id"] = EmptyToNull(templateId);
            record["section"] = section;
            record["rarity"] = EmptyToNull(ReadStringFromSources(instance, null, "Tier", "<Tier>k__BackingField"));
            record["socket_id"] = EmptyToNull(ReadStringFromSources(instance, null, "SocketId", "<SocketId>k__BackingField"));
            record["enchantment"] = EmptyToNull(ReadStringFromSources(
                instance,
                null,
                "EnchantmentType",
                "<EnchantmentType>k__BackingField"));
            record["attributes"] = SerializeRawEffectValue(
                FindMemberValue(instance, "Attributes", "<Attributes>k__BackingField"),
                0,
                new HashSet<object>(ReferenceEqualityComparer.Instance));

            object template = ResolveStaticCardTemplate(staticDataManager, templateId);
            Dictionary<string, object> templateRecord = BuildCardRecord(template);
            if (templateRecord != null)
            {
                CopyIfPresent(templateRecord, record, "name");
                CopyIfPresent(templateRecord, record, "internal_name");
                CopyIfPresent(templateRecord, record, "card_type");
                CopyIfPresent(templateRecord, record, "size");
                CopyIfPresent(templateRecord, record, "tags");
                CopyIfPresent(templateRecord, record, "hidden_tags");
                CopyIfPresent(templateRecord, record, "description");
            }

            return record;
        }

        private static object ResolveStaticCardTemplate(object staticDataManager, string templateId)
        {
            if (staticDataManager == null || string.IsNullOrEmpty(templateId))
            {
                return null;
            }

            Guid parsed;
            if (!Guid.TryParse(templateId, out parsed))
            {
                return null;
            }

            object template;
            return TryInvokeMember(staticDataManager, "GetCardById", new object[] { parsed }, out template)
                ? template
                : null;
        }

        private static object TryReadPlayerHealth(object player)
        {
            object attributes = FindMemberValue(player, "Attributes", "<Attributes>k__BackingField");
            int health;
            if (TryReadAttributeInt(attributes, "Health", out health))
            {
                return health;
            }
            if (TryReadAttributeInt(attributes, "HealthMax", out health))
            {
                return health;
            }
            return null;
        }

        private static bool TryReadAttributeInt(object attributes, string attributeName, out int value)
        {
            value = 0;
            IDictionary dictionary = attributes as IDictionary;
            if (dictionary == null || string.IsNullOrEmpty(attributeName))
            {
                return false;
            }

            foreach (DictionaryEntry entry in dictionary)
            {
                string key = entry.Key == null ? "" : entry.Key.ToString();
                if (!string.Equals(key, attributeName, StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }

                return TryConvertToInt(entry.Value, out value);
            }

            return false;
        }

        private static bool TryConvertToInt(object raw, out int value)
        {
            value = 0;
            if (raw == null)
            {
                return false;
            }

            if (raw is int)
            {
                value = (int)raw;
                return true;
            }

            if (raw is uint)
            {
                uint uintValue = (uint)raw;
                if (uintValue <= int.MaxValue)
                {
                    value = (int)uintValue;
                    return true;
                }
                return false;
            }

            double parsedDouble;
            if (double.TryParse(
                    raw.ToString(),
                    NumberStyles.Any,
                    CultureInfo.InvariantCulture,
                    out parsedDouble))
            {
                value = (int)Math.Round(parsedDouble);
                return true;
            }

            return false;
        }

        private static IEnumerable<object> EnumerateDictionaryValues(object value)
        {
            if (value == null)
            {
                yield break;
            }

            IDictionary dictionary = value as IDictionary;
            if (dictionary != null)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (entry.Value != null)
                    {
                        yield return entry.Value;
                    }
                }
                yield break;
            }

            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null || value is string)
            {
                yield return value;
                yield break;
            }

            foreach (object item in enumerable)
            {
                if (item == null)
                {
                    continue;
                }

                object itemValue;
                if (TryGetMemberValue(item, "Value", out itemValue) && itemValue != null)
                {
                    yield return itemValue;
                }
                else
                {
                    yield return item;
                }
            }
        }

        private static void CopyIfPresent(
            Dictionary<string, object> source,
            Dictionary<string, object> target,
            string key)
        {
            object value;
            if (source != null
                && target != null
                && !string.IsNullOrEmpty(key)
                && source.TryGetValue(key, out value)
                && value != null)
            {
                target[key] = value;
            }
        }

        private static bool TryCollectCardsFromMemberNames(object target, IEnumerable<string> memberNames, List<Dictionary<string, object>> cards, RuntimeCardExportResult result)
        {
            if (target == null || memberNames == null)
            {
                return false;
            }

            bool foundAny = false;
            foreach (string memberName in memberNames)
            {
                object value;
                if (!TryGetMemberValue(target, memberName, out value) || value == null)
                {
                    if (!TryInvokeParameterlessMember(target, memberName, out value) || value == null)
                    {
                        continue;
                    }
                }

                foundAny = true;
                if (result.LoadCardMapResultType == null)
                {
                    result.LoadCardMapResultType = SafeTypeName(value);
                }

                AppendCardsFromValue(value, cards, result);
            }

            return foundAny;
        }

        private static bool TryCollectCardsFromMatchingMembers(object target, HashSet<string> keywords, List<Dictionary<string, object>> cards, RuntimeCardExportResult result)
        {
            if (target == null || keywords == null)
            {
                return false;
            }

            Type type = target is Type ? (Type)target : target.GetType();
            if (type == null)
            {
                return false;
            }

            bool foundAny = false;
            BindingFlags flags = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

            FieldInfo[] fields = null;
            try
            {
                fields = type.GetFields(flags);
            }
            catch
            {
            }

            if (fields != null)
            {
                foreach (FieldInfo field in fields)
                {
                    if (field == null || !NameMatchesAnyKeyword(field.Name, keywords))
                    {
                        continue;
                    }

                    object value = SafeGetFieldValue(field, target is Type ? null : target);
                    if (value == null)
                    {
                        continue;
                    }

                    foundAny = true;
                    if (result.LoadCardMapResultType == null)
                    {
                        result.LoadCardMapResultType = SafeTypeName(value);
                    }

                    AppendCardsFromValue(value, cards, result);
                }
            }

            PropertyInfo[] properties = null;
            try
            {
                properties = type.GetProperties(flags);
            }
            catch
            {
            }

            if (properties != null)
            {
                foreach (PropertyInfo property in properties)
                {
                    if (property == null || !NameMatchesAnyKeyword(property.Name, keywords))
                    {
                        continue;
                    }

                    object value = SafeGetPropertyValue(property, target is Type ? null : target);
                    if (value == null)
                    {
                        continue;
                    }

                    foundAny = true;
                    if (result.LoadCardMapResultType == null)
                    {
                        result.LoadCardMapResultType = SafeTypeName(value);
                    }

                    AppendCardsFromValue(value, cards, result);
                }
            }

            MethodInfo[] methods = null;
            try
            {
                methods = type.GetMethods(flags);
            }
            catch
            {
            }

            if (methods != null)
            {
                foreach (MethodInfo method in methods)
                {
                    if (method == null || method.IsSpecialName || !NameMatchesAnyKeyword(method.Name, keywords) || method.GetParameters().Length != 0)
                    {
                        continue;
                    }

                    object value = InvokeMethod(method, target is Type ? null : target);
                    if (value == null)
                    {
                        continue;
                    }

                    foundAny = true;
                    if (result.LoadCardMapResultType == null)
                    {
                        result.LoadCardMapResultType = SafeTypeName(value);
                    }

                    AppendCardsFromValue(value, cards, result);
                }
            }

            return foundAny;
        }

        private static void AppendCardsFromValue(object value, List<Dictionary<string, object>> cards, RuntimeCardExportResult result)
        {
            if (value == null || cards == null)
            {
                return;
            }

            foreach (object template in EnumerateCandidateCardValues(value))
            {
                Dictionary<string, object> card = BuildCardRecord(template);
                if (card == null)
                {
                    continue;
                }

                cards.Add(card);
                if (!result.FoundKarnok && CardLooksLikeKarnok(card))
                {
                    result.FoundKarnok = true;
                }
            }
        }

        private static IEnumerable<object> EnumerateCandidateCardValues(object value)
        {
            if (value == null)
            {
                yield break;
            }

            IDictionary dictionary = value as IDictionary;
            if (dictionary != null)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (entry.Value == null)
                    {
                        continue;
                    }

                    foreach (object nested in EnumerateCandidateCardValues(entry.Value))
                    {
                        yield return nested;
                    }
                }

                yield break;
            }

            IEnumerable enumerable = value as IEnumerable;
            if (enumerable != null && !(value is string))
            {
                foreach (object item in enumerable)
                {
                    if (item == null)
                    {
                        continue;
                    }

                    foreach (object nested in EnumerateCandidateCardValues(item))
                    {
                        yield return nested;
                    }
                }

                yield break;
            }

            object nestedValue;
            string[] nestedMemberNames = new string[]
            {
                "ShopItems",
                "BoardItems",
                "StashItems",
                "Items",
                "Cards",
                "CardTemplates",
                "Templates",
                "Values",
                "Value",
                "Snapshots",
                "Snapshot",
                "Entries",
                "Map",
                "CardMap",
                "CollectionItems",
            };

            bool expanded = false;
            foreach (string memberName in nestedMemberNames)
            {
                if (TryGetMemberValue(value, memberName, out nestedValue) && nestedValue != null)
                {
                    expanded = true;
                    foreach (object nested in EnumerateCandidateCardValues(nestedValue))
                    {
                        yield return nested;
                    }
                }
            }

            if (!expanded)
            {
                yield return value;
            }
        }

        private static bool TryInvokeParameterlessMember(object target, string memberName, out object value)
        {
            value = null;
            if (target == null || string.IsNullOrEmpty(memberName))
            {
                return false;
            }

            Type type = target is Type ? (Type)target : target.GetType();
            BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;

            MethodInfo[] methods = null;
            try
            {
                methods = type.GetMethods(flags);
            }
            catch
            {
            }

            if (methods == null)
            {
                return false;
            }

            foreach (MethodInfo method in methods)
            {
                if (method == null || method.IsSpecialName || !string.Equals(method.Name, memberName, StringComparison.OrdinalIgnoreCase) || method.GetParameters().Length != 0)
                {
                    continue;
                }

                value = InvokeMethod(method, target is Type ? null : target);
                return true;
            }

            return false;
        }

        private static bool TryInvokeMember(object target, string memberName, object[] arguments, out object value)
        {
            value = null;
            if (target == null || string.IsNullOrEmpty(memberName))
            {
                return false;
            }

            object[] safeArguments = arguments ?? new object[0];
            Type type = target is Type ? (Type)target : target.GetType();
            BindingFlags flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance;

            MethodInfo[] methods = null;
            try
            {
                methods = type.GetMethods(flags);
            }
            catch
            {
            }

            if (methods == null)
            {
                return false;
            }

            foreach (MethodInfo method in methods)
            {
                try
                {
                    if (method == null || method.IsSpecialName ||
                        !string.Equals(method.Name, memberName, StringComparison.OrdinalIgnoreCase) ||
                        method.GetParameters().Length != safeArguments.Length)
                    {
                        continue;
                    }

                    value = method.Invoke(target is Type ? null : target, safeArguments);
                    return true;
                }
                catch
                {
                }
            }

            return false;
        }

        private static object InvokeMethod(MethodInfo method, object target)
        {
            if (method == null)
            {
                return null;
            }

            try
            {
                return method.Invoke(target, null);
            }
            catch
            {
                return null;
            }
        }

        private static Type FindLoadedType(string fullName)
        {
            if (string.IsNullOrEmpty(fullName))
            {
                return null;
            }

            foreach (Type type in FindLoadedTypes())
            {
                if (type == null)
                {
                    continue;
                }

                string typeFullName = type.FullName ?? string.Empty;
                if (string.Equals(typeFullName, fullName, StringComparison.OrdinalIgnoreCase) || typeFullName.IndexOf(fullName, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return type;
                }
            }

            return null;
        }

        private static string SafeTypeName(object value)
        {
            if (value == null)
            {
                return null;
            }

            try
            {
                Type type = value is Type ? (Type)value : value.GetType();
                return type == null ? null : type.FullName ?? type.Name;
            }
            catch
            {
                return null;
            }
        }

        private static int CountCandidateMembers(object target)
        {
            if (target == null)
            {
                return 0;
            }

            Type type = target.GetType();
            if (type == null)
            {
                return 0;
            }

            HashSet<string> keywords = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            {
                "Card",
                "Cards",
                "Template",
                "Templates",
                "Map",
                "Static",
                "Item",
                "Skill",
                "Loaded",
            };

            int count = 0;
            BindingFlags flags = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

            FieldInfo[] fields = null;
            try
            {
                fields = type.GetFields(flags);
            }
            catch
            {
            }

            if (fields != null)
            {
                foreach (FieldInfo field in fields)
                {
                    if (field != null && NameMatchesAnyKeyword(field.Name, keywords))
                    {
                        count++;
                    }
                }
            }

            PropertyInfo[] properties = null;
            try
            {
                properties = type.GetProperties(flags);
            }
            catch
            {
            }

            if (properties != null)
            {
                foreach (PropertyInfo property in properties)
                {
                    if (property != null && NameMatchesAnyKeyword(property.Name, keywords))
                    {
                        count++;
                    }
                }
            }

            MethodInfo[] methods = null;
            try
            {
                methods = type.GetMethods(flags);
            }
            catch
            {
            }

            if (methods != null)
            {
                foreach (MethodInfo method in methods)
                {
                    if (method != null && !method.IsSpecialName && NameMatchesAnyKeyword(method.Name, keywords))
                    {
                        count++;
                    }
                }
            }

            return count;
        }

        private static void ScanTypeForDiagnostics(Type type, HashSet<string> keywords, CacheDiagnostics diagnostics)
        {
            TypeDiagnostics typeEntry = null;
            List<string> matchedKeywords = new List<string>();

            if (TypeMatchesAnyKeyword(type, keywords, out matchedKeywords))
            {
                typeEntry = diagnostics.GetOrAddType(type, matchedKeywords);
            }

            if (typeEntry == null)
            {
                return;
            }

            BindingFlags flags = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

            FieldInfo[] fields = null;
            try
            {
                fields = type.GetFields(flags);
            }
            catch
            {
            }

            if (fields != null)
            {
                foreach (FieldInfo field in fields)
                {
                    if (field == null)
                    {
                        continue;
                    }

                    typeEntry.AddField(field.Name, field.FieldType, field.IsStatic ? "static" : "instance");
                    if (field.IsStatic)
                    {
                        TryRecordMemberValueDiagnostics(typeEntry, field.Name, field.FieldType, "static", SafeGetFieldValue(field, null), true);
                    }
                }
            }

            PropertyInfo[] properties = null;
            try
            {
                properties = type.GetProperties(flags);
            }
            catch
            {
            }

            if (properties != null)
            {
                foreach (PropertyInfo property in properties)
                {
                    if (property == null)
                    {
                        continue;
                    }

                    typeEntry.AddProperty(property.Name, property.PropertyType, GetPropertyScope(property));
                    if (property.GetGetMethod(true) != null && property.GetGetMethod(true).IsStatic)
                    {
                        TryRecordMemberValueDiagnostics(typeEntry, property.Name, property.PropertyType, "static", SafeGetPropertyValue(property, null), false);
                    }
                }
            }

            MethodInfo[] methods = null;
            try
            {
                methods = type.GetMethods(flags);
            }
            catch
            {
            }

            if (methods != null)
            {
                foreach (MethodInfo method in methods)
                {
                    if (method == null || method.IsSpecialName)
                    {
                        continue;
                    }

                    typeEntry.AddMethod(method.Name);
                }
            }
        }

        private static void ScanUnityObjectForDiagnostics(UnityEngine.Object unityObject, HashSet<string> keywords, CacheDiagnostics diagnostics)
        {
            Type type = unityObject.GetType();
            List<string> matchedKeywords;
            if (!TypeMatchesAnyKeyword(type, keywords, out matchedKeywords) && !ObjectHasMatchingMember(type, unityObject, keywords))
            {
                return;
            }

            ObjectDiagnostics objectEntry = diagnostics.AddObject("unity_object", unityObject, type);

            BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            FieldInfo[] fields = null;
            try
            {
                fields = type.GetFields(flags);
            }
            catch
            {
            }

            if (fields != null)
            {
                foreach (FieldInfo field in fields)
                {
                    if (field == null)
                    {
                        continue;
                    }

                    object value = SafeGetFieldValue(field, unityObject);
                    objectEntry.AddField(field.Name, field.FieldType, "instance", value);
                }
            }

            PropertyInfo[] properties = null;
            try
            {
                properties = type.GetProperties(flags);
            }
            catch
            {
            }

            if (properties != null)
            {
                foreach (PropertyInfo property in properties)
                {
                    if (property == null)
                    {
                        continue;
                    }

                    object value = SafeGetPropertyValue(property, unityObject);
                    objectEntry.AddProperty(property.Name, property.PropertyType, GetPropertyScope(property), value);
                }
            }
        }

        private static bool ObjectHasMatchingMember(Type type, object target, HashSet<string> keywords)
        {
            if (type == null || target == null)
            {
                return false;
            }

            BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

            FieldInfo[] fields = null;
            try
            {
                fields = type.GetFields(flags);
            }
            catch
            {
            }

            if (fields != null)
            {
                foreach (FieldInfo field in fields)
                {
                    if (field != null && NameMatchesAnyKeyword(field.Name, keywords))
                    {
                        return true;
                    }
                }
            }

            PropertyInfo[] properties = null;
            try
            {
                properties = type.GetProperties(flags);
            }
            catch
            {
            }

            if (properties != null)
            {
                foreach (PropertyInfo property in properties)
                {
                    if (property != null && NameMatchesAnyKeyword(property.Name, keywords))
                    {
                        return true;
                    }
                }
            }

            return false;
        }

        private static bool TypeMatchesAnyKeyword(Type type, HashSet<string> keywords, out List<string> matchedKeywords)
        {
            matchedKeywords = new List<string>();
            if (type == null)
            {
                return false;
            }

            if (NameMatchesAnyKeyword(type.FullName, keywords, matchedKeywords) || NameMatchesAnyKeyword(type.Name, keywords, matchedKeywords))
            {
                return matchedKeywords.Count > 0;
            }

            BindingFlags flags = BindingFlags.Instance | BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic;

            FieldInfo[] fields = null;
            try
            {
                fields = type.GetFields(flags);
            }
            catch
            {
            }

            if (fields != null)
            {
                foreach (FieldInfo field in fields)
                {
                    if (field != null && NameMatchesAnyKeyword(field.Name, keywords, matchedKeywords))
                    {
                        return true;
                    }
                }
            }

            PropertyInfo[] properties = null;
            try
            {
                properties = type.GetProperties(flags);
            }
            catch
            {
            }

            if (properties != null)
            {
                foreach (PropertyInfo property in properties)
                {
                    if (property != null && NameMatchesAnyKeyword(property.Name, keywords, matchedKeywords))
                    {
                        return true;
                    }
                }
            }

            MethodInfo[] methods = null;
            try
            {
                methods = type.GetMethods(flags);
            }
            catch
            {
            }

            if (methods != null)
            {
                foreach (MethodInfo method in methods)
                {
                    if (method != null && !method.IsSpecialName && NameMatchesAnyKeyword(method.Name, keywords, matchedKeywords))
                    {
                        return true;
                    }
                }
            }

            return false;
        }

        private static bool NameMatchesAnyKeyword(string name, HashSet<string> keywords)
        {
            return NameMatchesAnyKeyword(name, keywords, null);
        }

        private static bool NameMatchesAnyKeyword(string name, HashSet<string> keywords, List<string> matchedKeywords)
        {
            if (string.IsNullOrEmpty(name) || keywords == null)
            {
                return false;
            }

            foreach (string keyword in keywords)
            {
                if (!string.IsNullOrEmpty(keyword) && name.IndexOf(keyword, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    if (matchedKeywords != null && !matchedKeywords.Any(item => string.Equals(item, keyword, StringComparison.OrdinalIgnoreCase)))
                    {
                        matchedKeywords.Add(keyword);
                    }

                    return true;
                }
            }

            return false;
        }

        private static string GetPropertyScope(PropertyInfo property)
        {
            try
            {
                MethodInfo getter = property.GetGetMethod(true);
                if (getter != null && getter.IsStatic)
                {
                    return "static";
                }
            }
            catch
            {
            }

            return "instance";
        }

        private static object SafeGetFieldValue(FieldInfo field, object target)
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

        private static object SafeGetPropertyValue(PropertyInfo property, object target)
        {
            try
            {
                MethodInfo getter = property.GetGetMethod(true);
                if (getter == null)
                {
                    return null;
                }

                return property.GetValue(target, null);
            }
            catch
            {
                return null;
            }
        }

        private static void TryRecordMemberValueDiagnostics(TypeDiagnostics typeEntry, string memberName, Type memberType, string scope, object value, bool isField)
        {
            if (typeEntry == null || string.IsNullOrEmpty(memberName))
            {
                return;
            }

            if (isField)
            {
                typeEntry.AddFieldValue(memberName, memberType, scope, value);
            }
            else
            {
                typeEntry.AddPropertyValue(memberName, memberType, scope, value);
            }
        }

        internal static bool TryGetCollectionCount(object value, out int count)
        {
            count = 0;
            if (value == null || value is string)
            {
                return false;
            }

            Array array = value as Array;
            if (array != null)
            {
                count = array.Length;
                return true;
            }

            IDictionary dictionary = value as IDictionary;
            if (dictionary != null)
            {
                count = dictionary.Count;
                return true;
            }

            ICollection collection = value as ICollection;
            if (collection != null)
            {
                count = collection.Count;
                return true;
            }

            try
            {
                PropertyInfo countProperty = value.GetType().GetProperty("Count", BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (countProperty != null)
                {
                    object raw = countProperty.GetValue(value, null);
                    if (raw != null)
                    {
                        count = Convert.ToInt32(raw, CultureInfo.InvariantCulture);
                        return true;
                    }
                }
            }
            catch
            {
            }

            return false;
        }

        private static string ResolveDiagnosticsPath(string outputPath)
        {
            string fullPath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(outputPath ?? string.Empty));
            string directory = Path.GetDirectoryName(fullPath);
            if (string.IsNullOrEmpty(directory))
            {
                directory = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "runtime");
            }

            return Path.Combine(directory, "cache_diagnostics.json");
        }

        private static IEnumerable<UnityEngine.Object> FindLoadedUnityObjects()
        {
            UnityEngine.Object[] objects = null;
            try
            {
                objects = UnityEngine.Object.FindObjectsByType<UnityEngine.Object>(
                    FindObjectsSortMode.None);
            }
            catch
            {
            }

            if (objects == null)
            {
                try
                {
                    objects = Resources.FindObjectsOfTypeAll<UnityEngine.Object>();
                }
                catch
                {
                    objects = new UnityEngine.Object[0];
                }
            }

            foreach (UnityEngine.Object unityObject in objects)
            {
                if (unityObject != null)
                {
                    yield return unityObject;
                }
            }
        }

        private static string ResolveLiveCardsPath(string outputPath)
        {
            string fullPath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(outputPath ?? string.Empty));
            string directory = Path.GetDirectoryName(fullPath);
            if (string.IsNullOrEmpty(directory))
            {
                directory = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "runtime");
            }

            return Path.Combine(directory, "live_cards_raw.json");
        }

        private static string ResolveLiveMonstersPath(string outputPath)
        {
            string fullPath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(outputPath ?? string.Empty));
            string directory = Path.GetDirectoryName(fullPath);
            if (string.IsNullOrEmpty(directory))
            {
                directory = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "runtime");
            }

            return Path.Combine(directory, "live_monsters_raw.json");
        }

        private static object FindCacheObject(string cacheName)
        {
            object direct = FindStaticSingleton(cacheName);
            if (direct != null)
            {
                return direct;
            }

            foreach (MonoBehaviour behaviour in FindLoadedMonoBehaviours())
            {
                if (behaviour == null)
                {
                    continue;
                }

                Type type = behaviour.GetType();
                string fullName = type.FullName ?? type.Name;
                if (fullName.IndexOf(cacheName, StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return behaviour;
                }
            }

            return null;
        }

        private static object FindStaticSingleton(string typeNameHint)
        {
            foreach (Type type in FindLoadedTypes())
            {
                if (!TypeMatchesHint(type, typeNameHint))
                {
                    continue;
                }

                object value;
                if (TryGetStaticMemberValue(type, "Instance", out value))
                {
                    return value;
                }
                if (TryGetStaticMemberValue(type, "Current", out value))
                {
                    return value;
                }
                if (TryGetStaticMemberValue(type, "Shared", out value))
                {
                    return value;
                }
                if (TryGetStaticMemberValue(type, typeNameHint, out value))
                {
                    return value;
                }
            }

            return null;
        }

        private static bool TypeMatchesHint(Type type, string hint)
        {
            if (type == null)
            {
                return false;
            }

            string fullName = type.FullName ?? string.Empty;
            string name = type.Name ?? string.Empty;
            return fullName.IndexOf(hint, StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf(hint, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        private static object FindGlobalMemberValue(string memberName)
        {
            foreach (Type type in FindLoadedTypes())
            {
                object value;
                if (TryGetStaticMemberValue(type, memberName, out value))
                {
                    return value;
                }
            }

            return null;
        }

        private static object FindMemberValue(object target, params string[] memberNames)
        {
            if (target == null)
            {
                return null;
            }

            foreach (string memberName in memberNames)
            {
                object value;
                if (TryGetMemberValue(target, memberName, out value))
                {
                    if (value != null)
                    {
                        return value;
                    }
                }
            }

            return null;
        }

        private static IEnumerable<object> EnumerateCardTemplates(object cardMap)
        {
            if (cardMap == null)
            {
                yield break;
            }

            IDictionary dictionary = cardMap as IDictionary;
            if (dictionary != null)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (entry.Value != null)
                    {
                        yield return entry.Value;
                    }
                }

                yield break;
            }

            IEnumerable enumerable = cardMap as IEnumerable;
            if (enumerable == null || cardMap is string)
            {
                yield return cardMap;
                yield break;
            }

            foreach (object item in enumerable)
            {
                if (item == null)
                {
                    continue;
                }

                object value;
                if (TryGetMemberValue(item, "Value", out value) && value != null)
                {
                    yield return value;
                    continue;
                }

                if (TryGetMemberValue(item, "CardTemplate", out value) && value != null)
                {
                    yield return value;
                    continue;
                }

                if (TryGetMemberValue(item, "Template", out value) && value != null)
                {
                    yield return value;
                    continue;
                }

                yield return item;
            }
        }

        private static Dictionary<string, object> BuildCardRecord(object template)
        {
            if (template == null)
            {
                return null;
            }

            object resolvedTemplate = template;
            object nestedTemplate;
            if (TryGetMemberValue(template, "CardTemplate", out nestedTemplate) && nestedTemplate != null)
            {
                resolvedTemplate = nestedTemplate;
            }
            else if (TryGetMemberValue(template, "Template", out nestedTemplate) && nestedTemplate != null)
            {
                resolvedTemplate = nestedTemplate;
            }

            object attributeSource = null;
            object nestedAttributes;
            if (TryGetMemberValue(resolvedTemplate, "Attributes", out nestedAttributes) && nestedAttributes != null)
            {
                attributeSource = nestedAttributes;
            }
            else if (TryGetMemberValue(resolvedTemplate, "Data", out nestedAttributes) && nestedAttributes != null)
            {
                attributeSource = nestedAttributes;
            }

            string sourceId = ReadStringFromSources(resolvedTemplate, attributeSource, "SourceId", "SourceID", "Id", "TemplateId", "TemplateID");
            string templateId = ReadStringFromSources(resolvedTemplate, attributeSource, "TemplateId", "TemplateID", "Id", "SourceId", "SourceID");
            string internalName = ReadStringFromSources(resolvedTemplate, attributeSource, "InternalName", "InternalID", "CardName", "Name");
            string name = ReadLocalizedTextFromSources(resolvedTemplate, attributeSource, "Title");
            if (string.IsNullOrEmpty(name))
            {
                name = ReadStringFromSources(resolvedTemplate, attributeSource, "Title", "Name", "DisplayName", "LocalizedName", "InternalName", "CardName");
            }

            string description = ReadLocalizedTextFromSources(resolvedTemplate, attributeSource, "Description");
            if (string.IsNullOrEmpty(description))
            {
                description = ReadStringFromSources(resolvedTemplate, attributeSource, "Description");
            }
            if (string.IsNullOrEmpty(description))
            {
                description = ReadTooltipText(resolvedTemplate);
                if (string.IsNullOrEmpty(description))
                {
                    description = ReadTooltipText(attributeSource);
                }
            }

            List<string> heroes = ReadStringListFromSources(resolvedTemplate, attributeSource, "Heroes", "Hero");
            List<string> tags = ReadStringListFromSources(resolvedTemplate, attributeSource, "Tags");
            List<string> hiddenTags = ReadStringListFromSources(resolvedTemplate, attributeSource, "HiddenTags");
            List<string> cardTypes = ReadStringListFromSources(resolvedTemplate, attributeSource, "Types", "Type", "CardType");
            string cardType = cardTypes.Count > 0 ? cardTypes[0] : null;
            string size = ReadStringFromSources(resolvedTemplate, attributeSource, "Size");
            List<string> tiers = ReadTierNamesFromSources(resolvedTemplate, attributeSource);
            string rarity = ReadStringFromSources(resolvedTemplate, attributeSource, "StartingTier", "Tier", "Rarity");
            if (string.IsNullOrEmpty(rarity) && tiers.Count > 0)
            {
                rarity = tiers[0];
            }

            OrderedDictionary buyPrices = new OrderedDictionary(StringComparer.OrdinalIgnoreCase);
            OrderedDictionary sellPrices = new OrderedDictionary(StringComparer.OrdinalIgnoreCase);
            PopulatePricesFromSources(resolvedTemplate, attributeSource, buyPrices, sellPrices);

            string hero = heroes.Count == 1 ? heroes[0] : null;
            string minRarity = tiers.Count > 0 ? tiers[0] : rarity;
            string maxRarity = tiers.Count > 0 ? tiers[tiers.Count - 1] : rarity;

            if (string.IsNullOrEmpty(name)
                && string.IsNullOrEmpty(internalName)
                && string.IsNullOrEmpty(sourceId)
                && string.IsNullOrEmpty(templateId))
            {
                return null;
            }

            Dictionary<string, object> record = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            record["source_id"] = EmptyToNull(sourceId);
            record["template_id"] = EmptyToNull(templateId);
            record["id"] = EmptyToNull(sourceId) ?? EmptyToNull(templateId) ?? EmptyToNull(internalName) ?? EmptyToNull(name);
            record["internal_name"] = EmptyToNull(internalName);
            record["name"] = EmptyToNull(name);
            record["description"] = EmptyToNull(description);
            record["hero"] = EmptyToNull(hero);
            record["heroes"] = heroes;
            record["tags"] = tags;
            record["hidden_tags"] = hiddenTags;
            record["card_type"] = EmptyToNull(cardType);
            record["size"] = EmptyToNull(size);
            record["tiers"] = tiers;
            record["rarity"] = EmptyToNull(rarity);
            record["min_rarity"] = EmptyToNull(minRarity);
            record["max_rarity"] = EmptyToNull(maxRarity);
            record["buy_prices"] = buyPrices;
            record["sell_prices"] = sellPrices;
            record["raw_type"] = SafeTypeName(resolvedTemplate);

            Dictionary<string, object> rawEffects =
                BuildRawEffectRecord(resolvedTemplate, attributeSource);
            if (rawEffects.Count > 0)
            {
                record["raw_effects"] = rawEffects;
                record["raw_effect_fields"] = rawEffects.Keys.ToList();
            }

            Dictionary<string, object> spawningFilter =
                BuildSpawningFilterRecord(resolvedTemplate, attributeSource);
            if (spawningFilter.Count > 0)
            {
                record["spawning_filter"] = spawningFilter;
            }

            object cardPackId;
            if ((TryGetMemberValue(resolvedTemplate, "CardPackId", out cardPackId) && cardPackId != null)
                || (attributeSource != null && TryGetMemberValue(attributeSource, "CardPackId", out cardPackId) && cardPackId != null))
            {
                record["card_pack_id"] = cardPackId.ToString();
            }

            object visibleTags;
            if (TryGetMemberValue(resolvedTemplate, "VisibleTags", out visibleTags))
            {
                record["visible_tags"] = ReadStringList(visibleTags);
            }
            else if (attributeSource != null && TryGetMemberValue(attributeSource, "VisibleTags", out visibleTags))
            {
                record["visible_tags"] = ReadStringList(visibleTags);
            }
            else
            {
                record["visible_tags"] = BuildVisibleTags(tags, hiddenTags);
            }

            return record;
        }

        private static Dictionary<string, object> BuildRawEffectRecord(
            object primary,
            object secondary)
        {
            Dictionary<string, object> result =
                new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);

            AddRawEffectMember(result, primary, secondary, "abilities", "Abilities", "Ability", "CardAbilities");
            AddRawEffectMember(result, primary, secondary, "auras", "Auras", "Aura", "CardAuras");
            AddRawEffectMember(result, primary, secondary, "effects", "Effects", "Effect", "CardEffects");
            AddRawEffectMember(result, primary, secondary, "actions", "Actions", "Action", "CardActions");
            AddRawEffectMember(result, primary, secondary, "triggers", "Triggers", "Trigger", "CardTriggers");
            AddRawEffectMember(result, primary, secondary, "conditions", "Conditions", "Condition", "Requirements", "SelectionRequirements");
            AddRawEffectMember(result, primary, secondary, "values", "Values", "Value");
            AddRawEffectMember(result, primary, secondary, "tiers_raw", "Tiers");
            AddRawEffectMember(result, primary, secondary, "attributes", "Attributes", "Data");
            AddRawEffectMember(result, primary, secondary, "spawn_context", "SpawnContext", "SpawningContext", "SelectionContext");
            AddRawEffectMember(result, primary, secondary, "spawn_contexts", "SpawnContexts", "SpawningContexts", "SelectionContexts");
            AddRawEffectMember(result, primary, secondary, "spawn_filter_raw", "SpawningFilter", "SpawnFilter", "CardSpawningFilter");
            AddRawEffectMember(result, primary, secondary, "spawn_groups", "SpawnGroups", "Groups");
            AddRawEffectMember(result, primary, secondary, "spawn_behaviors", "SpawnBehaviors", "Behaviors");
            AddRawEffectMember(result, primary, secondary, "selection_criteria", "SelectionCriteria");

            return result;
        }

        private static void AddRawEffectMember(
            Dictionary<string, object> target,
            object primary,
            object secondary,
            string outputName,
            params string[] memberNames)
        {
            if (target == null || string.IsNullOrEmpty(outputName))
            {
                return;
            }

            object value = ReadValueFromSources(primary, secondary, memberNames);
            if (value == null)
            {
                return;
            }

            object serialized = SerializeRawEffectValue(
                value,
                0,
                new HashSet<object>(ReferenceEqualityComparer.Instance));
            if (serialized != null)
            {
                target[outputName] = serialized;
            }
        }

        private static object SerializeRawEffectValue(
            object value,
            int depth,
            HashSet<object> visited)
        {
            if (value == null)
            {
                return null;
            }

            Type type = value.GetType();
            if (IsSimpleSerializableValue(type))
            {
                return SimpleSerializableValue(value, type);
            }

            if (IsUnsafeRawEffectObject(type))
            {
                return DescribeRawEffectValue(value, "skipped_runtime_object");
            }

            if (depth >= GetRawEffectDepthLimit(type))
            {
                return DescribeRawEffectValue(value, "max_depth");
            }

            if (!type.IsValueType)
            {
                if (visited.Contains(value))
                {
                    return DescribeRawEffectValue(value, "cycle");
                }
                visited.Add(value);
            }

            IDictionary dictionary = value as IDictionary;
            if (dictionary != null)
            {
                OrderedDictionary result = new OrderedDictionary(StringComparer.OrdinalIgnoreCase);
                int maxItems = GetRawEffectCollectionLimit(type);
                int count = 0;
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (count >= maxItems)
                    {
                        result["$truncated"] = true;
                        break;
                    }

                    string key = entry.Key == null ? string.Empty : entry.Key.ToString();
                    result[key] = SerializeRawEffectValue(entry.Value, depth + 1, visited);
                    count++;
                }
                return result;
            }

            IEnumerable enumerable = value as IEnumerable;
            if (enumerable != null && !(value is string))
            {
                List<object> result = new List<object>();
                int maxItems = GetRawEffectCollectionLimit(type);
                int count = 0;
                foreach (object item in enumerable)
                {
                    if (count >= maxItems)
                    {
                        result.Add(DescribeRawEffectValue(value, "truncated"));
                        break;
                    }

                    result.Add(SerializeRawEffectValue(item, depth + 1, visited));
                    count++;
                }
                return result;
            }

            OrderedDictionary record = new OrderedDictionary(StringComparer.OrdinalIgnoreCase);
            record["$type"] = SafeTypeName(value);
            AppendRawEffectMembers(record, value, type, depth, visited);
            if (record.Count == 1)
            {
                record["$value"] = SafeToString(value);
            }
            return record;
        }

        private static void AppendRawEffectMembers(
            OrderedDictionary record,
            object value,
            Type type,
            int depth,
            HashSet<object> visited)
        {
            BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            HashSet<string> added = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            int maxMembers = GetRawEffectMemberLimit(type);
            int count = 0;

            FieldInfo[] fields = null;
            try
            {
                fields = type.GetFields(flags);
            }
            catch
            {
            }

            if (fields != null)
            {
                foreach (FieldInfo field in fields)
                {
                    if (field == null || field.IsStatic || count >= maxMembers)
                    {
                        continue;
                    }

                    string name = CleanRawEffectMemberName(field.Name);
                    if (string.IsNullOrEmpty(name) || !ShouldIncludeRawEffectMember(name) || !added.Add(name))
                    {
                        continue;
                    }

                    object child = SafeGetFieldValue(field, value);
                    if (child == null)
                    {
                        continue;
                    }

                    record[name] = SerializeRawEffectValue(child, depth + 1, visited);
                    count++;
                }
            }

            PropertyInfo[] properties = null;
            try
            {
                properties = type.GetProperties(flags);
            }
            catch
            {
            }

            if (properties != null)
            {
                foreach (PropertyInfo property in properties)
                {
                    if (property == null || property.GetIndexParameters().Length != 0 || count >= maxMembers)
                    {
                        continue;
                    }

                    MethodInfo getter = null;
                    try
                    {
                        getter = property.GetGetMethod(true);
                    }
                    catch
                    {
                    }
                    if (getter == null || getter.IsStatic)
                    {
                        continue;
                    }

                    string name = CleanRawEffectMemberName(property.Name);
                    if (string.IsNullOrEmpty(name) || !ShouldIncludeRawEffectMember(name) || !added.Add(name))
                    {
                        continue;
                    }

                    object child = SafeGetPropertyValue(property, value);
                    if (child == null)
                    {
                        continue;
                    }

                    record[name] = SerializeRawEffectValue(child, depth + 1, visited);
                    count++;
                }
            }

            if (count >= maxMembers)
            {
                record["$members_truncated"] = true;
            }
        }

        private static int GetRawEffectDepthLimit(Type type)
        {
            return IsRawEffectDomainType(type) || IsRawEffectCollectionType(type) ? 16 : 5;
        }

        private static int GetRawEffectCollectionLimit(Type type)
        {
            return IsRawEffectDomainType(type) || IsRawEffectCollectionType(type) ? 160 : 80;
        }

        private static int GetRawEffectMemberLimit(Type type)
        {
            return IsRawEffectDomainType(type) ? 80 : 40;
        }

        private static bool IsRawEffectCollectionType(Type type)
        {
            if (type == null)
            {
                return false;
            }

            if (IsRawEffectDomainType(type))
            {
                return true;
            }

            try
            {
                if (type.IsArray)
                {
                    return IsRawEffectDomainType(type.GetElementType());
                }

                if (type.IsGenericType)
                {
                    foreach (Type argument in type.GetGenericArguments())
                    {
                        if (IsRawEffectDomainType(argument) || IsSimpleSerializableValue(argument))
                        {
                            return true;
                        }
                    }
                }
            }
            catch
            {
            }

            return false;
        }

        private static bool IsRawEffectDomainType(Type type)
        {
            if (type == null)
            {
                return false;
            }

            string fullName = type.FullName ?? type.Name ?? string.Empty;
            return fullName.StartsWith("BazaarGameShared.Domain.Effect.", StringComparison.Ordinal)
                || fullName.StartsWith("BazaarGameShared.Domain.Spawning.", StringComparison.Ordinal)
                || fullName.StartsWith("BazaarGameShared.Domain.Prerequisites.", StringComparison.Ordinal)
                || fullName.StartsWith("BazaarGameShared.Domain.Targeting.", StringComparison.Ordinal)
                || fullName.StartsWith("BazaarGameShared.Domain.Values.", StringComparison.Ordinal)
                || fullName.StartsWith("BazaarGameShared.Domain.Durations.", StringComparison.Ordinal)
                || fullName.StartsWith("BazaarGameShared.Domain.Core.Types.", StringComparison.Ordinal);
        }

        private static bool ShouldIncludeRawEffectMember(string name)
        {
            if (string.IsNullOrEmpty(name))
            {
                return false;
            }

            string lower = name.ToLowerInvariant();
            string[] keywords = new string[]
            {
                "id",
                "name",
                "type",
                "ability",
                "abilities",
                "aura",
                "auras",
                "effect",
                "effects",
                "action",
                "actions",
                "trigger",
                "triggers",
                "condition",
                "conditions",
                "constraint",
                "constraints",
                "requirement",
                "requirements",
                "target",
                "targets",
                "value",
                "values",
                "amount",
                "modifier",
                "mod",
                "attribute",
                "attributes",
                "tier",
                "tiers",
                "duration",
                "cooldown",
                "second",
                "seconds",
                "count",
                "chance",
                "probability",
                "operation",
                "operator",
                "comparison",
                "compare",
                "min",
                "max",
                "default",
                "modify",
                "mode",
                "round",
                "subject",
                "source",
                "origin",
                "include",
                "exclude",
                "ignore",
                "priority",
                "weight",
                "spawn",
                "spawning",
                "filter",
                "filters",
                "group",
                "groups",
                "behavior",
                "behaviors",
                "card",
                "cards",
                "item",
                "items",
                "skill",
                "skills",
                "hero",
                "heroes",
                "tag",
                "tags",
                "size",
                "sizes",
                "enchant",
                "enchantment",
                "rarity",
                "rarities",
                "quest",
                "xp",
            };

            foreach (string keyword in keywords)
            {
                if (lower.IndexOf(keyword, StringComparison.Ordinal) >= 0)
                {
                    return true;
                }
            }

            return false;
        }

        private static bool IsUnsafeRawEffectObject(Type type)
        {
            if (type == null)
            {
                return false;
            }

            try
            {
                if (typeof(UnityEngine.Object).IsAssignableFrom(type))
                {
                    return true;
                }
            }
            catch
            {
            }

            string fullName = type.FullName ?? type.Name ?? string.Empty;
            return fullName.StartsWith("System.Reflection.", StringComparison.Ordinal)
                || fullName.StartsWith("System.RuntimeType", StringComparison.Ordinal)
                || fullName.StartsWith("BepInEx.", StringComparison.Ordinal)
                || fullName.StartsWith("HarmonyLib.", StringComparison.Ordinal);
        }

        private static string CleanRawEffectMemberName(string name)
        {
            if (string.IsNullOrEmpty(name))
            {
                return null;
            }

            if (name.StartsWith("<", StringComparison.Ordinal)
                && name.IndexOf(">k__BackingField", StringComparison.Ordinal) > 1)
            {
                int end = name.IndexOf('>');
                if (end > 1)
                {
                    return name.Substring(1, end - 1);
                }
            }

            return name;
        }

        private static bool IsSimpleSerializableValue(Type type)
        {
            if (type == null)
            {
                return false;
            }

            return type.IsPrimitive
                || type.IsEnum
                || type == typeof(string)
                || type == typeof(decimal)
                || type == typeof(Guid)
                || type == typeof(DateTime)
                || type == typeof(TimeSpan);
        }

        private static object SimpleSerializableValue(object value, Type type)
        {
            if (value == null)
            {
                return null;
            }

            if (type.IsEnum || type == typeof(Guid) || type == typeof(DateTime) || type == typeof(TimeSpan))
            {
                return value.ToString();
            }

            return value;
        }

        private static OrderedDictionary DescribeRawEffectValue(object value, string reason)
        {
            OrderedDictionary result = new OrderedDictionary(StringComparer.OrdinalIgnoreCase);
            result["$type"] = SafeTypeName(value);
            result["$reason"] = reason;
            string text = SafeToString(value);
            if (!string.IsNullOrEmpty(text))
            {
                result["$value"] = text;
            }
            return result;
        }

        private static string SafeToString(object value)
        {
            if (value == null)
            {
                return null;
            }

            try
            {
                return value.ToString();
            }
            catch
            {
                return null;
            }
        }

        private static Dictionary<string, object> BuildSpawningFilterRecord(
            object primary,
            object secondary)
        {
            Dictionary<string, object> result =
                new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            object filter = ReadValueFromSources(
                primary,
                secondary,
                "SpawningFilter",
                "SpawnFilter",
                "CardSpawningFilter");
            if (filter == null)
            {
                filter = primary;
            }

            AddStringList(result, filter, "ItemTierFilters", "item_tier_filters");
            AddStringList(result, filter, "CardTypeFilters", "card_type_filters");
            AddStringList(result, filter, "MerchantHeroFilters", "merchant_hero_filters");
            AddStringList(result, filter, "EncounterHeroFilters", "encounter_hero_filters");
            AddStringList(result, filter, "CardSizeFilters", "card_size_filters");
            AddStringList(result, filter, "CardTagFilters", "card_tag_filters");
            AddStringList(result, filter, "EnchantmentFilters", "enchantment_filters");
            AddStringList(result, filter, "HiddenTagFilters", "hidden_tag_filters");
            AddInt(result, filter, "NumberCardsToSpawn", "number_cards_to_spawn");
            AddInt(result, filter, "GoldRewardAmount", "gold_reward_amount");

            object rerolls = ReadValue(filter, "Rerolls", "rerolls");
            List<Dictionary<string, object>> rerollRecords = BuildRerollRecords(rerolls);
            if (rerollRecords.Count > 0)
            {
                result["Rerolls"] = rerollRecords;
            }
            else
            {
                Dictionary<string, object> directReroll = BuildSingleRerollRecord(filter);
                if (directReroll.Count > 0)
                {
                    result["Rerolls"] = new List<Dictionary<string, object>> { directReroll };
                }
            }

            return result;
        }

        private static object ReadValueFromSources(
            object primary,
            object secondary,
            params string[] memberNames)
        {
            object value = ReadValue(primary, memberNames);
            if (value != null)
            {
                return value;
            }

            return secondary == null ? null : ReadValue(secondary, memberNames);
        }

        private static void AddStringList(
            Dictionary<string, object> target,
            object source,
            string memberName,
            string outputName)
        {
            object value = ReadValue(source, memberName);
            List<string> items = ReadStringList(value);
            if (items.Count > 0)
            {
                target[outputName] = items;
            }
        }

        private static void AddInt(
            Dictionary<string, object> target,
            object source,
            string memberName,
            string outputName)
        {
            object value = ReadValue(source, memberName);
            int parsed;
            if (TryParseInt(value, out parsed) && parsed >= 0)
            {
                target[outputName] = parsed;
            }
        }

        private static List<Dictionary<string, object>> BuildRerollRecords(object value)
        {
            List<Dictionary<string, object>> result = new List<Dictionary<string, object>>();
            if (value == null || value is string)
            {
                return result;
            }

            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null)
            {
                Dictionary<string, object> record = BuildSingleRerollRecord(value);
                if (record.Count > 0)
                {
                    result.Add(record);
                }
                return result;
            }

            foreach (object item in enumerable)
            {
                Dictionary<string, object> record = BuildSingleRerollRecord(item);
                if (record.Count > 0)
                {
                    result.Add(record);
                }
            }

            return result;
        }

        private static Dictionary<string, object> BuildSingleRerollRecord(object source)
        {
            Dictionary<string, object> record =
                new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            AddInt(record, source, "RerollCost", "reroll_cost");
            AddInt(record, source, "RerollScalar", "reroll_scalar");
            AddInt(record, source, "NumberOfRerolls", "number_of_rerolls");
            AddBool(record, source, "RerollEnabled", "reroll_enabled");
            AddBool(record, source, "RerollRepeats", "reroll_repeats");
            return record;
        }

        private static void AddBool(
            Dictionary<string, object> target,
            object source,
            string memberName,
            string outputName)
        {
            object value = ReadValue(source, memberName);
            bool parsed;
            if (TryParseBool(value, out parsed))
            {
                target[outputName] = parsed;
            }
        }

        private static bool TryParseInt(object value, out int result)
        {
            result = 0;
            if (value == null)
            {
                return false;
            }

            try
            {
                result = Convert.ToInt32(value);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static bool TryParseBool(object value, out bool result)
        {
            result = false;
            if (value == null)
            {
                return false;
            }

            try
            {
                result = Convert.ToBoolean(value);
                return true;
            }
            catch
            {
                return false;
            }
        }

        private static List<string> BuildVisibleTags(List<string> tags, List<string> hiddenTags)
        {
            HashSet<string> hidden = new HashSet<string>(hiddenTags.Select(NormalizeValue), StringComparer.OrdinalIgnoreCase);
            List<string> visible = new List<string>();
            foreach (string tag in tags)
            {
                if (string.IsNullOrEmpty(tag))
                {
                    continue;
                }

                if (!hidden.Contains(tag))
                {
                    visible.Add(tag);
                }
            }
            return visible;
        }

        private static void PopulatePrices(object template, OrderedDictionary buyPrices, OrderedDictionary sellPrices)
        {
            PopulatePricesFromSources(template, null, buyPrices, sellPrices);
        }

        private static void PopulatePricesFromSources(object primary, object secondary, OrderedDictionary buyPrices, OrderedDictionary sellPrices)
        {
            object buyPricesValue;
            if (TryGetMemberValue(primary, "BuyPrices", out buyPricesValue) && buyPricesValue != null)
            {
                AppendPriceValues(buyPricesValue, buyPrices);
            }
            else if (secondary != null && TryGetMemberValue(secondary, "BuyPrices", out buyPricesValue) && buyPricesValue != null)
            {
                AppendPriceValues(buyPricesValue, buyPrices);
            }

            object sellPricesValue;
            if (TryGetMemberValue(primary, "SellPrices", out sellPricesValue) && sellPricesValue != null)
            {
                AppendPriceValues(sellPricesValue, sellPrices);
            }
            else if (secondary != null && TryGetMemberValue(secondary, "SellPrices", out sellPricesValue) && sellPricesValue != null)
            {
                AppendPriceValues(sellPricesValue, sellPrices);
            }

            object tiersValue;
            if (!TryGetMemberValue(primary, "Tiers", out tiersValue) || tiersValue == null)
            {
                if (secondary == null || !TryGetMemberValue(secondary, "Tiers", out tiersValue) || tiersValue == null)
                {
                    return;
                }
            }

            IDictionary dictionary = tiersValue as IDictionary;
            if (dictionary != null)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    string tierName = entry.Key == null ? null : entry.Key.ToString();
                    if (string.IsNullOrEmpty(tierName))
                    {
                        continue;
                    }

                    object tierData = entry.Value;
                    object attributes;
                    if (!TryGetMemberValue(tierData, "Attributes", out attributes) || attributes == null)
                    {
                        attributes = tierData;
                    }

                    object buyPrice = ReadValue(attributes, "BuyPrice", "Buy");
                    object sellPrice = ReadValue(attributes, "SellPrice", "Sell");
                    if (buyPrice != null)
                    {
                        buyPrices[tierName] = buyPrice;
                    }
                    if (sellPrice != null)
                    {
                        sellPrices[tierName] = sellPrice;
                    }
                }
            }
        }

        private static void AppendPriceValues(object source, OrderedDictionary target)
        {
            if (source == null || target == null)
            {
                return;
            }

            IDictionary dictionary = source as IDictionary;
            if (dictionary != null)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (entry.Key != null)
                    {
                        target[entry.Key.ToString()] = entry.Value;
                    }
                }

                return;
            }

            IEnumerable enumerable = source as IEnumerable;
            if (enumerable == null || source is string)
            {
                return;
            }

            foreach (object item in enumerable)
            {
                if (item == null)
                {
                    continue;
                }

                object key;
                object value;
                if (TryGetMemberValue(item, "Key", out key) && TryGetMemberValue(item, "Value", out value) && key != null)
                {
                    target[key.ToString()] = value;
                }
            }
        }

        private static List<string> ReadTierNames(object target)
        {
            return ReadTierNamesFromSources(target, null);
        }

        private static List<string> ReadTierNamesFromSources(object primary, object secondary)
        {
            List<string> tiers = new List<string>();

            object tiersValue;
            if (!TryGetMemberValue(primary, "Tiers", out tiersValue) || tiersValue == null)
            {
                if (secondary == null || !TryGetMemberValue(secondary, "Tiers", out tiersValue) || tiersValue == null)
                {
                    return tiers;
                }
            }

            IDictionary dictionary = tiersValue as IDictionary;
            if (dictionary != null)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (entry.Key != null)
                    {
                        tiers.Add(entry.Key.ToString());
                    }
                }

                return tiers;
            }

            IEnumerable enumerable = tiersValue as IEnumerable;
            if (enumerable == null || tiersValue is string)
            {
                return ReadStringList(tiersValue);
            }

            foreach (object item in enumerable)
            {
                string text = NormalizeValue(item == null ? null : item.ToString());
                if (!string.IsNullOrEmpty(text))
                {
                    tiers.Add(text);
                }
            }

            return tiers;
        }

        private static string ReadLocalizedText(object target, string key)
        {
            return ReadLocalizedTextFromSources(target, null, key);
        }

        private static string ReadLocalizedTextFromSources(object primary, object secondary, string key)
        {
            object localization;
            if (!TryGetMemberValue(primary, "Localization", out localization) || localization == null)
            {
                if (secondary == null || !TryGetMemberValue(secondary, "Localization", out localization) || localization == null)
                {
                    return ReadString(primary, key);
                }
            }

            object localizedValue;
            if (!TryGetMemberValue(localization, key, out localizedValue) || localizedValue == null)
            {
                return null;
            }

            object text;
            if (TryGetMemberValue(localizedValue, "Text", out text) && text != null)
            {
                return text.ToString();
            }

            string fallback = NormalizeValue(localizedValue.ToString());
            if (!string.IsNullOrEmpty(fallback))
            {
                return fallback;
            }

            string primaryFallback = ReadString(primary, key);
            if (!string.IsNullOrEmpty(primaryFallback))
            {
                return primaryFallback;
            }

            return ReadString(secondary, key);
        }

        private static string ReadTooltipText(object target)
        {
            object localization;
            if (!TryGetMemberValue(target, "Localization", out localization) || localization == null)
            {
                return null;
            }

            object tooltipsValue;
            if (!TryGetMemberValue(localization, "Tooltips", out tooltipsValue) || tooltipsValue == null)
            {
                return null;
            }

            IEnumerable enumerable = tooltipsValue as IEnumerable;
            if (enumerable == null || tooltipsValue is string)
            {
                return null;
            }

            List<string> texts = new List<string>();
            foreach (object tooltip in enumerable)
            {
                if (tooltip == null)
                {
                    continue;
                }

                object content;
                if (!TryGetMemberValue(tooltip, "Content", out content) || content == null)
                {
                    continue;
                }

                object text;
                if (TryGetMemberValue(content, "Text", out text) && text != null)
                {
                    texts.Add(text.ToString());
                }
            }

            return texts.Count > 0 ? string.Join("\n", texts.ToArray()) : null;
        }

        private static List<string> ReadStringList(object target, params string[] memberNames)
        {
            object value = ReadValue(target, memberNames);
            return ReadStringList(value);
        }

        private static List<string> ReadStringListFromSources(object primary, object secondary, params string[] memberNames)
        {
            object value = ReadValue(primary, memberNames);
            if (value == null && secondary != null)
            {
                value = ReadValue(secondary, memberNames);
            }

            return ReadStringList(value);
        }

        private static string ReadStringFromSources(object primary, object secondary, params string[] memberNames)
        {
            string value = ReadString(primary, memberNames);
            if (!string.IsNullOrEmpty(value))
            {
                return value;
            }

            if (secondary != null)
            {
                value = ReadString(secondary, memberNames);
                if (!string.IsNullOrEmpty(value))
                {
                    return value;
                }
            }

            return null;
        }

        private static List<string> ReadStringList(object value)
        {
            List<string> result = new List<string>();
            if (value == null)
            {
                return result;
            }

            if (value is string)
            {
                string text = NormalizeValue(value.ToString());
                if (!string.IsNullOrEmpty(text))
                {
                    result.Add(text);
                }
                return result;
            }

            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null)
            {
                string text = NormalizeValue(value.ToString());
                if (!string.IsNullOrEmpty(text))
                {
                    result.Add(text);
                }
                return result;
            }

            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (object item in enumerable)
            {
                string text = NormalizeValue(item == null ? null : item.ToString());
                if (string.IsNullOrEmpty(text) || !seen.Add(text))
                {
                    continue;
                }

                result.Add(text);
            }

            return result;
        }

        private static string ReadString(object target, params string[] memberNames)
        {
            object value = ReadValue(target, memberNames);
            return value == null ? null : NormalizeValue(value.ToString());
        }

        private static object ReadValue(object target, params string[] memberNames)
        {
            if (target == null)
            {
                return null;
            }

            foreach (string memberName in memberNames)
            {
                object value;
                if (TryGetMemberValue(target, memberName, out value) && value != null)
                {
                    return value;
                }
            }

            return null;
        }

        private static bool TryGetStaticMemberValue(Type type, string name, out object value)
        {
            value = null;
            if (type == null)
            {
                return false;
            }

            try
            {
                FieldInfo field = type.GetField(name, BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                if (field != null)
                {
                    value = field.GetValue(null);
                    return true;
                }
            }
            catch
            {
            }

            try
            {
                PropertyInfo property = type.GetProperty(name, BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
                if (property != null)
                {
                    value = property.GetValue(null, null);
                    return true;
                }
            }
            catch
            {
            }

            return false;
        }

        private static bool TryGetMemberValue(object target, string name, out object value)
        {
            value = null;
            if (target == null || string.IsNullOrEmpty(name))
            {
                return false;
            }

            Type type = target.GetType();
            try
            {
                FieldInfo field = type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (field != null)
                {
                    value = field.GetValue(target);
                    return true;
                }
            }
            catch
            {
            }

            try
            {
                PropertyInfo property = type.GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
                if (property != null)
                {
                    value = property.GetValue(target, null);
                    return true;
                }
            }
            catch
            {
            }

            return false;
        }

        private static IEnumerable<Type> FindLoadedTypes()
        {
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] types = null;
                try
                {
                    types = assembly.GetTypes();
                }
                catch (ReflectionTypeLoadException ex)
                {
                    types = ex.Types;
                }
                catch
                {
                }

                if (types == null)
                {
                    continue;
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

        private static IEnumerable<MonoBehaviour> FindLoadedMonoBehaviours()
        {
            MonoBehaviour[] behaviours = null;
            try
            {
                behaviours = Resources.FindObjectsOfTypeAll<MonoBehaviour>();
            }
            catch
            {
                behaviours = new MonoBehaviour[0];
            }

            foreach (MonoBehaviour behaviour in behaviours)
            {
                if (behaviour != null)
                {
                    yield return behaviour;
                }
            }
        }

        private static bool CardLooksLikeKarnok(Dictionary<string, object> card)
        {
            if (card == null)
            {
                return false;
            }

            string[] fields = new string[]
            {
                GetString(card, "name"),
                GetString(card, "internal_name"),
                GetString(card, "source_id"),
                GetString(card, "template_id"),
                GetString(card, "hero"),
                string.Join(" ", GetStringList(card, "heroes").ToArray()),
                string.Join(" ", GetStringList(card, "tags").ToArray()),
                string.Join(" ", GetStringList(card, "hidden_tags").ToArray()),
            };

            foreach (string field in fields)
            {
                if (!string.IsNullOrEmpty(field) && field.IndexOf("karnok", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    return true;
                }
            }

            return false;
        }

        private static string GetString(Dictionary<string, object> card, string key)
        {
            object value;
            if (card.TryGetValue(key, out value) && value != null)
            {
                return value.ToString();
            }

            return null;
        }

        private static List<string> GetStringList(Dictionary<string, object> card, string key)
        {
            object value;
            if (!card.TryGetValue(key, out value) || value == null)
            {
                return new List<string>();
            }

            IEnumerable enumerable = value as IEnumerable;
            if (enumerable == null || value is string)
            {
                return new List<string> { value.ToString() };
            }

            List<string> result = new List<string>();
            foreach (object item in enumerable)
            {
                if (item != null)
                {
                    result.Add(item.ToString());
                }
            }

            return result;
        }

        private static string NormalizeValue(string value)
        {
            return string.IsNullOrEmpty(value) ? null : value.Trim();
        }

        private static object EmptyToNull(string value)
        {
            return string.IsNullOrEmpty(value) ? null : value;
        }

        private static void WriteJsonAtomic(string outputPath, object value)
        {
            string fullPath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(outputPath ?? string.Empty));
            string directory = Path.GetDirectoryName(fullPath);
            if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
            {
                Directory.CreateDirectory(directory);
            }

            string tempPath = fullPath + ".tmp";
            using (StreamWriter writer = new StreamWriter(tempPath, false, new UTF8Encoding(false)))
            {
                WriteJsonValue(writer, value);
                writer.WriteLine();
            }

            if (File.Exists(fullPath))
            {
                File.Replace(tempPath, fullPath, null);
            }
            else
            {
                File.Move(tempPath, fullPath);
            }
        }

        private static void WriteJsonValue(TextWriter writer, object value)
        {
            if (value == null)
            {
                writer.Write("null");
                return;
            }

            string text = value as string;
            if (text != null)
            {
                WriteJsonString(writer, text);
                return;
            }

            if (value is bool)
            {
                writer.Write(((bool)value) ? "true" : "false");
                return;
            }

            if (value is byte || value is sbyte || value is short || value is ushort || value is int || value is uint || value is long || value is ulong || value is float || value is double || value is decimal)
            {
                writer.Write(Convert.ToString(value, CultureInfo.InvariantCulture));
                return;
            }

            IDictionary dictionary = value as IDictionary;
            if (dictionary != null)
            {
                writer.Write('{');
                bool first = true;
                foreach (DictionaryEntry entry in dictionary)
                {
                    if (!first)
                    {
                        writer.Write(',');
                    }
                    first = false;

                    WriteJsonString(writer, entry.Key == null ? string.Empty : entry.Key.ToString());
                    writer.Write(':');
                    WriteJsonValue(writer, entry.Value);
                }
                writer.Write('}');
                return;
            }

            IEnumerable enumerable = value as IEnumerable;
            if (enumerable != null)
            {
                writer.Write('[');
                bool first = true;
                foreach (object item in enumerable)
                {
                    if (!first)
                    {
                        writer.Write(',');
                    }
                    first = false;
                    WriteJsonValue(writer, item);
                }
                writer.Write(']');
                return;
            }

            WriteJsonString(writer, value.ToString());
        }

        private static void WriteJsonString(TextWriter writer, string value)
        {
            if (value == null)
            {
                writer.Write("null");
                return;
            }

            writer.Write('"');
            foreach (char c in value)
            {
                switch (c)
                {
                    case '"':
                        writer.Write("\\\"");
                        break;
                    case '\\':
                        writer.Write("\\\\");
                        break;
                    case '\b':
                        writer.Write("\\b");
                        break;
                    case '\f':
                        writer.Write("\\f");
                        break;
                    case '\n':
                        writer.Write("\\n");
                        break;
                    case '\r':
                        writer.Write("\\r");
                        break;
                    case '\t':
                        writer.Write("\\t");
                        break;
                    default:
                        if (c < 32)
                        {
                            writer.Write("\\u");
                            writer.Write(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                        }
                        else
                        {
                            writer.Write(c);
                        }
                        break;
                }
            }
            writer.Write('"');
        }
    }

    public sealed class CacheDiagnostics
    {
        private readonly List<string> assemblies = new List<string>();
        private readonly Dictionary<string, TypeDiagnostics> candidateTypes = new Dictionary<string, TypeDiagnostics>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, ObjectDiagnostics> candidateObjects = new Dictionary<string, ObjectDiagnostics>(StringComparer.OrdinalIgnoreCase);

        public int ScannedAssemblyCount
        {
            get { return assemblies.Count; }
        }

        public int CandidateTypeCount
        {
            get { return candidateTypes.Count; }
        }

        public int CandidateObjectCount
        {
            get { return candidateObjects.Count; }
        }

        public void AddAssembly(Assembly assembly)
        {
            if (assembly == null)
            {
                return;
            }

            string displayName = SafeAssemblyName(assembly);
            if (!string.IsNullOrEmpty(displayName) && !assemblies.Any(existing => string.Equals(existing, displayName, StringComparison.OrdinalIgnoreCase)))
            {
                assemblies.Add(displayName);
            }
        }

        public TypeDiagnostics GetOrAddType(Type type, List<string> matchedKeywords)
        {
            if (type == null)
            {
                return null;
            }

            string key = type.FullName ?? type.Name ?? Guid.NewGuid().ToString("N");
            TypeDiagnostics existing;
            if (candidateTypes.TryGetValue(key, out existing))
            {
                existing.MergeMatchedKeywords(matchedKeywords);
                return existing;
            }

            TypeDiagnostics created = new TypeDiagnostics(type, matchedKeywords);
            candidateTypes[key] = created;
            return created;
        }

        public ObjectDiagnostics AddObject(string source, object value, Type ownerType)
        {
            if (value == null)
            {
                return null;
            }

            string key = BuildObjectKey(source, value);
            ObjectDiagnostics existing;
            if (candidateObjects.TryGetValue(key, out existing))
            {
                return existing;
            }

            ObjectDiagnostics created = new ObjectDiagnostics(source, value, ownerType);
            candidateObjects[key] = created;
            return created;
        }

        public Dictionary<string, object> ToSerializable()
        {
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            result["scanned_assemblies"] = new List<string>(assemblies);
            result["scanned_assembly_count"] = ScannedAssemblyCount;
            result["candidate_type_count"] = CandidateTypeCount;
            result["candidate_object_count"] = CandidateObjectCount;
            result["candidate_types"] = candidateTypes.Values.Select(item => item.ToSerializable()).ToList();
            result["candidate_objects"] = candidateObjects.Values.Select(item => item.ToSerializable()).ToList();
            return result;
        }

        private static string SafeAssemblyName(Assembly assembly)
        {
            if (assembly == null)
            {
                return null;
            }

            try
            {
                AssemblyName name = assembly.GetName();
                return name == null ? assembly.FullName : name.FullName;
            }
            catch
            {
                return assembly.FullName;
            }
        }

        private static string BuildObjectKey(string source, object value)
        {
            string typeName = value == null ? string.Empty : value.GetType().FullName ?? value.GetType().Name;
            int hash = 0;
            try
            {
                hash = RuntimeHelpers.GetHashCode(value);
            }
            catch
            {
            }

            return (source ?? string.Empty) + "|" + typeName + "|" + hash.ToString(CultureInfo.InvariantCulture);
        }
    }

    internal sealed class ReferenceEqualityComparer : IEqualityComparer<object>
    {
        public static readonly ReferenceEqualityComparer Instance = new ReferenceEqualityComparer();

        private ReferenceEqualityComparer()
        {
        }

        public new bool Equals(object x, object y)
        {
            return object.ReferenceEquals(x, y);
        }

        public int GetHashCode(object obj)
        {
            return obj == null ? 0 : RuntimeHelpers.GetHashCode(obj);
        }
    }

    public sealed class TypeDiagnostics
    {
        private readonly List<string> matchedKeywords = new List<string>();
        private readonly Dictionary<string, DiagnosticMember> fields = new Dictionary<string, DiagnosticMember>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<string, DiagnosticMember> properties = new Dictionary<string, DiagnosticMember>(StringComparer.OrdinalIgnoreCase);
        private readonly List<string> methods = new List<string>();

        public TypeDiagnostics(Type type, List<string> keywords)
        {
            FullName = type == null ? null : type.FullName ?? type.Name;
            AssemblyName = type == null || type.Assembly == null ? null : type.Assembly.GetName().FullName;
            MergeMatchedKeywords(keywords);
        }

        public string FullName { get; private set; }
        public string AssemblyName { get; private set; }

        public void MergeMatchedKeywords(List<string> keywords)
        {
            if (keywords == null)
            {
                return;
            }

            foreach (string keyword in keywords)
            {
                if (string.IsNullOrEmpty(keyword))
                {
                    continue;
                }

                if (!matchedKeywords.Any(existing => string.Equals(existing, keyword, StringComparison.OrdinalIgnoreCase)))
                {
                    matchedKeywords.Add(keyword);
                }
            }
        }

        public void AddField(string name, Type memberType, string scope)
        {
            AddMember(fields, name, memberType, scope, null);
        }

        public void AddProperty(string name, Type memberType, string scope)
        {
            AddMember(properties, name, memberType, scope, null);
        }

        public void AddFieldValue(string name, Type memberType, string scope, object value)
        {
            AddMember(fields, name, memberType, scope, value);
        }

        public void AddPropertyValue(string name, Type memberType, string scope, object value)
        {
            AddMember(properties, name, memberType, scope, value);
        }

        public void AddMethod(string name)
        {
            if (string.IsNullOrEmpty(name))
            {
                return;
            }

            if (!methods.Any(existing => string.Equals(existing, name, StringComparison.OrdinalIgnoreCase)))
            {
                methods.Add(name);
            }
        }

        public Dictionary<string, object> ToSerializable()
        {
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            result["full_name"] = FullName;
            result["assembly"] = AssemblyName;
            result["matched_keywords"] = new List<string>(matchedKeywords);
            result["fields"] = fields.Values.Select(item => item.ToSerializable()).ToList();
            result["properties"] = properties.Values.Select(item => item.ToSerializable()).ToList();
            result["methods"] = new List<string>(methods);
            return result;
        }

        private static void AddMember(Dictionary<string, DiagnosticMember> collection, string name, Type memberType, string scope, object value)
        {
            if (collection == null || string.IsNullOrEmpty(name))
            {
                return;
            }

            string key = (scope ?? string.Empty) + "|" + name;
            DiagnosticMember member;
            if (!collection.TryGetValue(key, out member))
            {
                member = new DiagnosticMember(name, scope, memberType);
                collection[key] = member;
            }

            member.UpdateValue(value);
        }
    }

    public sealed class ObjectDiagnostics
    {
        private readonly List<DiagnosticMember> fields = new List<DiagnosticMember>();
        private readonly List<DiagnosticMember> properties = new List<DiagnosticMember>();

        public ObjectDiagnostics(string source, object value, Type ownerType)
        {
            Source = source;
            OwnerType = ownerType == null ? null : ownerType.FullName ?? ownerType.Name;
            AssemblyName = ownerType == null || ownerType.Assembly == null ? null : ownerType.Assembly.GetName().FullName;
            ObjectType = value == null ? null : value.GetType().FullName ?? value.GetType().Name;
            ObjectAssembly = value == null || value.GetType().Assembly == null ? null : value.GetType().Assembly.GetName().FullName;
            ObjectName = SafeObjectName(value);
        }

        public string Source { get; private set; }
        public string OwnerType { get; private set; }
        public string AssemblyName { get; private set; }
        public string ObjectType { get; private set; }
        public string ObjectAssembly { get; private set; }
        public string ObjectName { get; private set; }

        public void AddField(string name, Type memberType, string scope, object value)
        {
            AddMember(fields, name, memberType, scope, value);
        }

        public void AddProperty(string name, Type memberType, string scope, object value)
        {
            AddMember(properties, name, memberType, scope, value);
        }

        public Dictionary<string, object> ToSerializable()
        {
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            result["source"] = Source;
            result["owner_type"] = OwnerType;
            result["assembly"] = AssemblyName;
            result["object_type"] = ObjectType;
            result["object_assembly"] = ObjectAssembly;
            result["object_name"] = ObjectName;
            result["fields"] = fields.Select(item => item.ToSerializable()).ToList();
            result["properties"] = properties.Select(item => item.ToSerializable()).ToList();
            return result;
        }

        private static void AddMember(List<DiagnosticMember> collection, string name, Type memberType, string scope, object value)
        {
            if (collection == null || string.IsNullOrEmpty(name))
            {
                return;
            }

            DiagnosticMember member = new DiagnosticMember(name, scope, memberType);
            member.UpdateValue(value);
            collection.Add(member);
        }

        private static string SafeObjectName(object value)
        {
            UnityEngine.Object unityObject = value as UnityEngine.Object;
            if (unityObject == null)
            {
                return null;
            }

            try
            {
                return unityObject.name;
            }
            catch
            {
                return null;
            }
        }
    }

    public sealed class DiagnosticMember
    {
        public DiagnosticMember(string name, string scope, Type declaredType)
        {
            Name = name;
            Scope = scope;
            DeclaredType = declaredType == null ? null : declaredType.FullName ?? declaredType.Name;
        }

        public string Name { get; private set; }
        public string Scope { get; private set; }
        public string DeclaredType { get; private set; }
        public string ValueType { get; private set; }
        public int? Count { get; private set; }

        public void UpdateValue(object value)
        {
            if (value != null)
            {
                try
                {
                    ValueType = value.GetType().FullName ?? value.GetType().Name;
                }
                catch
                {
                }
            }

            int count;
            if (RuntimeCardExporter.TryGetCollectionCount(value, out count))
            {
                Count = count;
            }
        }

        public Dictionary<string, object> ToSerializable()
        {
            Dictionary<string, object> result = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
            result["name"] = Name;
            result["scope"] = Scope;
            result["declared_type"] = DeclaredType;
            result["value_type"] = ValueType;
            result["count"] = Count;
            return result;
        }
    }

    public sealed class RuntimeCardExportResult
    {
        public bool FoundClientCache;
        public bool FoundRunConfigurationCache;
        public bool FoundCardMap;
        public bool FoundStaticDataManager;
        public bool FoundBazaarPlusPlusFallback;
        public int ExportedCardCount;
        public int ExportedMonsterCount;
        public int? MonsterMapCount;
        public bool FoundKarnok;
        public string OutputPath;
        public string MonstersOutputPath;
        public string DiagnosticsPath;
        public string BppReadyManagerType;
        public string LoadCardMapResultType;
        public int? LoadCardMapCount;
        public int ScannedAssemblyCount;
        public int CandidateTypeCount;
        public int CandidateObjectCount;
    }
}
