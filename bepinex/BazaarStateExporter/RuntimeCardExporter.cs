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
            string diagnosticsPath = ResolveDiagnosticsPath(outputPath);
            result.OutputPath = liveCardsPath;
            result.DiagnosticsPath = diagnosticsPath;

            try
            {
                CacheDiagnostics diagnostics = BuildCacheDiagnostics();
                result.ScannedAssemblyCount = diagnostics.ScannedAssemblyCount;
                result.CandidateTypeCount = diagnostics.CandidateTypeCount;
                result.CandidateObjectCount = diagnostics.CandidateObjectCount;
                WriteJsonAtomic(diagnosticsPath, diagnostics.ToSerializable());
            }
            catch (Exception ex)
            {
                if (logger != null)
                {
                    logger.LogWarning("Failed to write cache diagnostics: " + ex);
                }
            }

            try
            {
                List<Dictionary<string, object>> cards = new List<Dictionary<string, object>>();
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
                    logger.LogInfo("Runtime card export: found Karnok=" + result.FoundKarnok);
                    logger.LogInfo("Runtime card export: live_cards_raw.json write path=" + liveCardsPath);
                    logger.LogInfo("Runtime card export: cache_diagnostics.json path=" + diagnosticsPath);
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
        public bool FoundBazaarPlusPlusFallback;
        public int ExportedCardCount;
        public bool FoundKarnok;
        public string OutputPath;
        public string DiagnosticsPath;
        public string BppReadyManagerType;
        public string LoadCardMapResultType;
        public int? LoadCardMapCount;
        public int ScannedAssemblyCount;
        public int CandidateTypeCount;
        public int CandidateObjectCount;
    }
}
