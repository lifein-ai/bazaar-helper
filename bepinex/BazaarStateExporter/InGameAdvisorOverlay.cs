using System;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Threading;
using BepInEx.Configuration;
using BepInEx.Logging;
using UnityEngine;

namespace BazaarStateExporter
{
    public sealed class InGameAdvisorOverlay : MonoBehaviour
    {
        private ManualLogSource logger;
        private ConfigEntry<bool> enabledConfig;
        private ConfigEntry<string> helperBaseUrl;
        private ConfigEntry<float> pollIntervalSeconds;
        private ConfigEntry<int> topRecommendations;
        private ConfigEntry<bool> includeAi;
        private ConfigEntry<string> toggleKeyConfig;
        private KeyCode toggleKey = KeyCode.F8;
        private bool visible = true;
        private volatile bool requestInFlight;
        private float nextPollAt;
        private Rect windowRect = new Rect(24f, 70f, 540f, 640f);
        private Rect buildWindowRect = new Rect(576f, 70f, 380f, 640f);
        private bool windowsPlaced;
        private Vector2 scrollPosition;
        private Vector2 buildScrollPosition;
        private OverlayAnalysis latest = OverlayAnalysis.Waiting("正在等待 BazaarHelper...");
        private string selectedBuildOverride = "";
        private GUIStyle windowStyle;
        private GUIStyle titleStyle;
        private GUIStyle itemStyle;
        private GUIStyle reasonStyle;
        private GUIStyle badgeStyle;
        private GUIStyle mutedStyle;

        public void Initialize(
            ManualLogSource log,
            ConfigEntry<bool> enableOverlay,
            ConfigEntry<string> baseUrl,
            ConfigEntry<float> pollInterval,
            ConfigEntry<int> top,
            ConfigEntry<bool> requestAi,
            ConfigEntry<string> toggleKey)
        {
            logger = log;
            enabledConfig = enableOverlay;
            helperBaseUrl = baseUrl;
            pollIntervalSeconds = pollInterval;
            topRecommendations = top;
            includeAi = requestAi;
            toggleKeyConfig = toggleKey;
            ParseToggleKey();
            logger?.LogInfo(
                "In-game overlay initialized url="
                + (helperBaseUrl == null ? "" : helperBaseUrl.Value)
                + " poll="
                + (pollIntervalSeconds == null ? 0f : pollIntervalSeconds.Value));
        }

        private void Start()
        {
            logger?.LogInfo("In-game overlay started.");
            nextPollAt = 0f;
            if (enabledConfig != null && enabledConfig.Value && !requestInFlight)
            {
                StartAnalysisRequest();
            }
        }

        private void Update()
        {
            if (enabledConfig == null || !enabledConfig.Value)
            {
                return;
            }
            if (Time.unscaledTime < nextPollAt || requestInFlight)
            {
                return;
            }

            nextPollAt = Time.unscaledTime + Math.Max(0.5f, pollIntervalSeconds.Value);
            StartAnalysisRequest();
        }

        private void OnGUI()
        {
            if (enabledConfig == null || !enabledConfig.Value)
            {
                return;
            }

            HandleToggleEvent();
            if (!visible)
            {
                return;
            }

            EnsureStyles();
            PlaceAndClampWindows();
            windowRect = GUI.Window(
                90210,
                windowRect,
                DrawWindow,
                "BazaarHelper - 推荐",
                windowStyle);
            buildWindowRect = GUI.Window(
                90211,
                buildWindowRect,
                DrawBuildWindow,
                "BazaarHelper - 阵容",
                windowStyle);
            windowRect = ClampToScreen(windowRect);
            buildWindowRect = ClampToScreen(buildWindowRect);
        }

        private void PlaceAndClampWindows()
        {
            float margin = 16f;
            float top = Math.Max(48f, margin);
            float availableWidth = Math.Max(320f, Screen.width - margin * 2f);
            float availableHeight = Math.Max(280f, Screen.height - top - margin);
            float gap = 12f;

            if (!windowsPlaced)
            {
                if (availableWidth >= 900f)
                {
                    float buildWidth = Math.Min(400f, availableWidth * 0.38f);
                    float recommendationWidth = availableWidth - buildWidth - gap;
                    windowRect = new Rect(margin, top, recommendationWidth, availableHeight);
                    buildWindowRect = new Rect(
                        margin + recommendationWidth + gap,
                        top,
                        buildWidth,
                        availableHeight);
                }
                else
                {
                    float recommendationHeight = Math.Max(260f, availableHeight * 0.6f);
                    float buildHeight = Math.Max(180f, availableHeight - recommendationHeight - gap);
                    windowRect = new Rect(margin, top, availableWidth, recommendationHeight);
                    buildWindowRect = new Rect(
                        margin,
                        top + recommendationHeight + gap,
                        availableWidth,
                        buildHeight);
                }
                windowsPlaced = true;
            }

            windowRect.width = Math.Min(windowRect.width, availableWidth);
            windowRect.height = Math.Min(windowRect.height, availableHeight);
            buildWindowRect.width = Math.Min(buildWindowRect.width, availableWidth);
            buildWindowRect.height = Math.Min(buildWindowRect.height, availableHeight);
        }

        private static Rect ClampToScreen(Rect rect)
        {
            const float margin = 8f;
            rect.width = Math.Max(280f, Math.Min(rect.width, Screen.width - margin * 2f));
            rect.height = Math.Max(180f, Math.Min(rect.height, Screen.height - margin * 2f));
            rect.x = Mathf.Clamp(rect.x, margin, Math.Max(margin, Screen.width - rect.width - margin));
            rect.y = Mathf.Clamp(rect.y, margin, Math.Max(margin, Screen.height - rect.height - margin));
            return rect;
        }

        private void HandleToggleEvent()
        {
            if (toggleKeyConfig != null)
            {
                ParseToggleKey();
            }
            Event current = Event.current;
            if (current == null || current.type != EventType.KeyDown || current.keyCode != toggleKey)
            {
                return;
            }

            visible = !visible;
            current.Use();
        }

        private void DrawWindow(int windowId)
        {
            GUILayout.BeginVertical();
            GUILayout.Label("当前推荐", titleStyle);
            if (!string.IsNullOrEmpty(latest.Status))
            {
                GUILayout.Label(latest.Status, mutedStyle);
            }

            if (!string.IsNullOrEmpty(latest.ShopAction))
            {
                GUILayout.BeginVertical(itemStyle);
                GUILayout.BeginHorizontal();
                GUILayout.Label("商店操作", titleStyle);
                GUILayout.FlexibleSpace();
                GUILayout.Label(latest.ShopAction, badgeStyle);
                GUILayout.EndHorizontal();
                if (!string.IsNullOrEmpty(latest.ShopReason))
                {
                    GUILayout.Label(latest.ShopReason, reasonStyle);
                }
                GUILayout.EndVertical();
            }

            scrollPosition = GUILayout.BeginScrollView(scrollPosition, false, true);
            if (latest.Items.Count == 0 && string.IsNullOrEmpty(latest.ShopAction))
            {
                GUILayout.Label("暂时没有可执行建议。", mutedStyle);
            }
            foreach (OverlayRecommendation item in latest.Items)
            {
                GUILayout.BeginVertical(itemStyle);
                GUILayout.BeginHorizontal();
                GUILayout.Label(item.Name, titleStyle);
                GUILayout.FlexibleSpace();
                GUILayout.Label(item.Label, badgeStyle);
                GUILayout.EndHorizontal();
                if (!string.IsNullOrEmpty(item.Notes))
                {
                    GUILayout.Label(item.Notes, mutedStyle);
                }
                if (!string.IsNullOrEmpty(item.PoolSummary))
                {
                    GUILayout.Label(item.PoolSummary, mutedStyle);
                }
                DrawInlineList("关键卡", item.PriorityCards, true);
                DrawInlineList("已拥有命中", item.OwnedHits, true);
                DrawInlineList("可能后续", item.ChildOptions, false);
                DrawInlineList("原因", item.Reasons, true);
                if (item.AltCoreCardCount > 0)
                {
                    GUILayout.Label(
                        "转型/备选阵容",
                        mutedStyle);
                    GUILayout.Label(
                        "其他阵容核心命中 " + item.AltCoreCardCount + " 张，可作为转型或备选阵容参考。",
                        mutedStyle);
                    DrawInlineList("", item.AltCoreHits, false);
                }
                GUILayout.EndVertical();
            }
            GUILayout.EndScrollView();
            GUILayout.Label(toggleKey + " 隐藏 / 显示", mutedStyle);
            GUILayout.EndVertical();
            GUI.DragWindow(new Rect(0f, 0f, 10000f, 28f));
        }

        private void DrawBuildWindow(int windowId)
        {
            GUILayout.BeginVertical();
            string buildName = FirstNonEmpty(latest.CurrentBuildName, latest.CurrentBuildId);
            GUILayout.Label(string.IsNullOrEmpty(buildName) ? "当前阵容" : buildName, titleStyle);

            buildScrollPosition = GUILayout.BeginScrollView(buildScrollPosition, false, true);
            if (latest.BuildOptions.Count > 0)
            {
                GUILayout.Label("选择阵容", mutedStyle);
                for (int i = 0; i < latest.BuildOptions.Count; i++)
                {
                    OverlayBuildOption option = latest.BuildOptions[i];
                    bool active = string.Equals(
                        option.Id,
                        string.IsNullOrEmpty(selectedBuildOverride)
                            ? latest.CurrentBuildId
                            : selectedBuildOverride,
                        StringComparison.Ordinal);
                    string label = active ? "✓  " + option.Name : option.Name;
                    GUI.enabled = !active;
                    if (GUILayout.Button(label, GUILayout.ExpandWidth(true), GUILayout.MinHeight(30f)))
                    {
                        selectedBuildOverride = option.Id;
                        latest.Status = "正在切换阵容...";
                        nextPollAt = 0f;
                    }
                    GUI.enabled = true;
                }
            }

            GUILayout.Space(8f);
            DrawCardSection("核心卡", latest.BuildDetail.CoreCards);
            DrawCardSection("过渡卡", latest.BuildDetail.TransitionCards);
            DrawCardSection("可选卡", latest.BuildDetail.OptionalCards);
            GUILayout.EndScrollView();
            GUILayout.Label(toggleKey + " 隐藏 / 显示", mutedStyle);
            GUILayout.EndVertical();
            GUI.DragWindow(new Rect(0f, 0f, 10000f, 28f));
        }

        private void DrawCardSection(string title, List<string> cards)
        {
            GUILayout.BeginVertical(itemStyle);
            GUILayout.Label(title, titleStyle);
            if (cards.Count == 0)
            {
                GUILayout.Label("暂无", mutedStyle);
            }
            for (int i = 0; i < cards.Count; i++)
            {
                GUILayout.Label(cards[i], reasonStyle);
            }
            GUILayout.EndVertical();
        }

        private void DrawInlineList(string title, List<string> values, bool showEmpty)
        {
            if (!string.IsNullOrEmpty(title))
            {
                GUILayout.Label(title, mutedStyle);
            }
            if (values.Count == 0)
            {
                if (showEmpty)
                {
                    GUILayout.Label("- 暂无", reasonStyle);
                }
                return;
            }

            for (int i = 0; i < values.Count; i++)
            {
                GUILayout.Label("- " + values[i], reasonStyle);
            }
        }

        private void StartAnalysisRequest()
        {
            requestInFlight = true;
            string url = BuildAnalysisUrl();
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    using (TimeoutWebClient client = new TimeoutWebClient(4000))
                    {
                        client.Encoding = Encoding.UTF8;
                        string json = client.DownloadString(url);
                        OverlayAnalysis parsed = OverlayAnalysisParser.Parse(json);
                        if (parsed.BuildOptions.Count == 0 && !string.IsNullOrEmpty(parsed.Hero))
                        {
                            string optionsUrl = BuildOptionsUrl(parsed.Hero);
                            string optionsJson = client.DownloadString(optionsUrl);
                            parsed.BuildOptions.AddRange(
                                OverlayAnalysisParser.ParseBuildOptions(optionsJson));
                        }
                        lock (this)
                        {
                            latest = parsed;
                        }
                        logger?.LogDebug(
                            "In-game overlay refreshed recommendations="
                            + parsed.Items.Count
                            + " build="
                            + parsed.CurrentBuildId
                            + " core="
                            + parsed.BuildDetail.CoreCards.Count);
                        logger?.LogInfo(
                            "In-game overlay refreshed recommendations="
                            + parsed.Items.Count
                            + " build="
                            + parsed.CurrentBuildId
                            + " core="
                            + parsed.BuildDetail.CoreCards.Count);
                    }
                }
                catch (Exception ex)
                {
                    lock (this)
                    {
                        latest = OverlayAnalysis.Waiting("未连接到 BazaarHelper，请先启动助手。");
                    }
                    logger?.LogInfo("In-game overlay analysis request failed: " + ex.Message);
                }
                finally
                {
                    requestInFlight = false;
                }
            });
        }

        private string BuildAnalysisUrl()
        {
            string baseUrl = (helperBaseUrl == null ? "" : helperBaseUrl.Value).Trim();
            if (string.IsNullOrEmpty(baseUrl))
            {
                baseUrl = "http://127.0.0.1:8765";
            }
            baseUrl = baseUrl.TrimEnd('/');
            int top = Math.Max(1, topRecommendations == null ? 3 : topRecommendations.Value);
            string ai = includeAi != null && includeAi.Value ? "1" : "0";
            string url = baseUrl + "/api/analysis?top=" + top + "&ai=" + ai;
            if (!string.IsNullOrEmpty(selectedBuildOverride))
            {
                url += "&build=" + Uri.EscapeDataString(selectedBuildOverride);
            }
            return url;
        }

        private string BuildOptionsUrl(string hero)
        {
            string baseUrl = (helperBaseUrl == null ? "" : helperBaseUrl.Value).Trim();
            if (string.IsNullOrEmpty(baseUrl))
            {
                baseUrl = "http://127.0.0.1:8765";
            }
            baseUrl = baseUrl.TrimEnd('/');
            return baseUrl + "/api/options?hero=" + Uri.EscapeDataString(hero);
        }

        private static string FirstNonEmpty(string first, string second)
        {
            return string.IsNullOrEmpty(first) ? second : first;
        }

        private void ParseToggleKey()
        {
            string keyName = toggleKeyConfig == null ? "F8" : toggleKeyConfig.Value;
            KeyCode parsed;
            if (Enum.TryParse(keyName, true, out parsed))
            {
                toggleKey = parsed;
            }
        }

        private void EnsureStyles()
        {
            if (windowStyle != null)
            {
                return;
            }

            float uiScale = Mathf.Clamp(Screen.height / 1080f, 1f, 1.35f);
            int bodyFontSize = Mathf.RoundToInt(13f * uiScale);
            int titleFontSize = Mathf.RoundToInt(16f * uiScale);
            int mutedFontSize = Mathf.RoundToInt(12f * uiScale);

            windowStyle = new GUIStyle(GUI.skin.window)
            {
                fontSize = bodyFontSize,
                padding = new RectOffset(14, 14, 32, 12),
            };
            titleStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = titleFontSize,
                fontStyle = FontStyle.Bold,
                wordWrap = true,
                normal = { textColor = Color.white },
            };
            itemStyle = new GUIStyle(GUI.skin.box)
            {
                padding = new RectOffset(10, 10, 8, 8),
                margin = new RectOffset(0, 0, 0, 8),
            };
            reasonStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = bodyFontSize,
                wordWrap = true,
                normal = { textColor = new Color(0.92f, 0.92f, 0.86f) },
            };
            badgeStyle = new GUIStyle(GUI.skin.box)
            {
                fontSize = bodyFontSize,
                fontStyle = FontStyle.Bold,
                alignment = TextAnchor.MiddleCenter,
                padding = new RectOffset(8, 8, 3, 3),
                normal = { textColor = Color.white },
            };
            mutedStyle = new GUIStyle(GUI.skin.label)
            {
                fontSize = mutedFontSize,
                wordWrap = true,
                normal = { textColor = new Color(0.72f, 0.74f, 0.78f) },
            };
        }
    }

    internal sealed class OverlayAnalysis
    {
        public string Status;
        public string ShopAction;
        public string ShopReason;
        public string Hero;
        public string CurrentBuildId;
        public string CurrentBuildName;
        public readonly OverlayBuildDetail BuildDetail = new OverlayBuildDetail();
        public readonly List<OverlayBuildOption> BuildOptions = new List<OverlayBuildOption>();
        public readonly List<OverlayRecommendation> Items = new List<OverlayRecommendation>();

        public static OverlayAnalysis Waiting(string status)
        {
            return new OverlayAnalysis { Status = status };
        }
    }

    internal sealed class OverlayRecommendation
    {
        public string Name;
        public string Label;
        public string Notes;
        public string PoolSummary;
        public int AltCoreCardCount;
        public readonly List<string> Reasons = new List<string>();
        public readonly List<string> PriorityCards = new List<string>();
        public readonly List<string> OwnedHits = new List<string>();
        public readonly List<string> ChildOptions = new List<string>();
        public readonly List<string> AltCoreHits = new List<string>();
    }

    internal sealed class OverlayBuildDetail
    {
        public readonly List<string> CoreCards = new List<string>();
        public readonly List<string> TransitionCards = new List<string>();
        public readonly List<string> OptionalCards = new List<string>();
    }

    internal sealed class OverlayBuildOption
    {
        public string Id;
        public string Name;
    }

    internal static class OverlayAnalysisParser
    {
        public static OverlayAnalysis Parse(string json)
        {
            OverlayAnalysis result = new OverlayAnalysis();
            string shopObject = FindObjectProperty(json, "build_analysis");
            if (!string.IsNullOrEmpty(shopObject))
            {
                result.ShopAction = FindStringProperty(shopObject, "shop_action");
                result.ShopReason = FindStringProperty(shopObject, "refresh_reason");
            }

            string stateObject = FindObjectProperty(json, "state");
            if (!string.IsNullOrEmpty(stateObject))
            {
                result.Hero = FindStringProperty(stateObject, "hero");
                result.CurrentBuildId = FindStringProperty(stateObject, "build");
                result.CurrentBuildName = FirstNonEmpty(
                    FindStringProperty(stateObject, "build_display_name"),
                    result.CurrentBuildId);
                string buildDetail = FindObjectProperty(stateObject, "build_detail");
                if (!string.IsNullOrEmpty(buildDetail))
                {
                    result.BuildDetail.CoreCards.AddRange(
                        FindCardNameArrayProperty(buildDetail, "core_cards"));
                    result.BuildDetail.TransitionCards.AddRange(
                        FindCardNameArrayProperty(buildDetail, "transition_cards"));
                    result.BuildDetail.OptionalCards.AddRange(
                        FindCardNameArrayProperty(buildDetail, "optional_cards"));
                }

                string buildOptions = FindArrayProperty(stateObject, "build_options");
                foreach (string optionObject in SplitTopLevelObjects(buildOptions))
                {
                    string id = FindStringProperty(optionObject, "id");
                    if (string.IsNullOrEmpty(id))
                    {
                        continue;
                    }
                    result.BuildOptions.Add(new OverlayBuildOption
                    {
                        Id = id,
                        Name = FirstNonEmpty(FindStringProperty(optionObject, "name"), id),
                    });
                }
            }

            string recommendations = FindArrayProperty(json, "recommendations");
            foreach (string itemObject in SplitTopLevelObjects(recommendations))
            {
                OverlayRecommendation item = new OverlayRecommendation();
                item.Name = FirstNonEmpty(
                    FindStringProperty(itemObject, "event_display_name"),
                    FindStringProperty(itemObject, "event_name"));
                item.Label = FirstNonEmpty(
                    FindStringProperty(itemObject, "recommendation_label"),
                    FindStringProperty(itemObject, "recommendation"));
                item.Notes = FindStringProperty(itemObject, "notes");
                item.Reasons.AddRange(FindStringArrayProperty(itemObject, "reasons"));
                if (string.IsNullOrEmpty(item.Name))
                {
                    continue;
                }
                if (string.IsNullOrEmpty(item.Label))
                {
                    item.Label = "-";
                }
                item.PoolSummary = BuildPoolSummary(itemObject);
                item.PriorityCards.AddRange(FindNamedObjectArrayProperty(
                    itemObject,
                    "priority_cards",
                    "role_label_zh",
                    "tier"));
                item.OwnedHits.AddRange(FindNamedObjectArrayProperty(
                    itemObject,
                    "owned_target_hits",
                    "role_label_zh",
                    "tier"));
                item.ChildOptions.AddRange(FindChildOptionArrayProperty(itemObject));
                item.AltCoreCardCount = ParseInt(FindNumberProperty(itemObject, "alt_core_card_count"));
                item.AltCoreHits.AddRange(FindAltCoreHitArrayProperty(itemObject));
                result.Items.Add(item);
            }

            result.Status = result.Items.Count == 0 && string.IsNullOrEmpty(result.ShopAction)
                ? "当前没有可执行建议。"
                : "";
            return result;
        }

        public static List<OverlayBuildOption> ParseBuildOptions(string json)
        {
            List<OverlayBuildOption> result = new List<OverlayBuildOption>();
            string buildOptions = FindArrayProperty(json, "build_options");
            foreach (string optionObject in SplitTopLevelObjects(buildOptions))
            {
                string id = FindStringProperty(optionObject, "id");
                if (string.IsNullOrEmpty(id))
                {
                    continue;
                }
                result.Add(new OverlayBuildOption
                {
                    Id = id,
                    Name = FirstNonEmpty(FindStringProperty(optionObject, "name"), id),
                });
            }
            return result;
        }

        private static string FirstNonEmpty(string first, string second)
        {
            return string.IsNullOrEmpty(first) ? second : first;
        }

        private static string FindObjectProperty(string json, string name)
        {
            int valueStart = FindPropertyValueStart(json, name);
            if (valueStart < 0 || valueStart >= json.Length || json[valueStart] != '{')
            {
                return "";
            }
            int end = FindMatching(json, valueStart, '{', '}');
            return end < 0 ? "" : json.Substring(valueStart, end - valueStart + 1);
        }

        private static string FindArrayProperty(string json, string name)
        {
            int valueStart = FindPropertyValueStart(json, name);
            if (valueStart < 0 || valueStart >= json.Length || json[valueStart] != '[')
            {
                return "";
            }
            int end = FindMatching(json, valueStart, '[', ']');
            return end < 0 ? "" : json.Substring(valueStart, end - valueStart + 1);
        }

        private static string FindStringProperty(string json, string name)
        {
            int valueStart = FindPropertyValueStart(json, name);
            if (valueStart < 0 || valueStart >= json.Length || json[valueStart] != '"')
            {
                return "";
            }
            int index = valueStart;
            return ReadJsonString(json, ref index);
        }

        private static List<string> FindStringArrayProperty(string json, string name)
        {
            List<string> values = new List<string>();
            string array = FindArrayProperty(json, name);
            for (int i = 0; i < array.Length; i++)
            {
                if (array[i] != '"')
                {
                    continue;
                }
                values.Add(ReadJsonString(array, ref i));
            }
            return values;
        }

        private static string BuildPoolSummary(string json)
        {
            string pool = FindObjectProperty(json, "pool_stats");
            if (string.IsNullOrEmpty(pool))
            {
                return "";
            }

            string expected = FindNumberProperty(pool, "expected_relevant_in_shop");
            string relevantProb = FindNumberProperty(pool, "prob_relevant_in_shop");
            string coreProb = FindNumberProperty(pool, "prob_core_in_shop");
            string sellGold = FindNumberProperty(pool, "expected_sell_gold");
            if (string.IsNullOrEmpty(expected)
                && string.IsNullOrEmpty(relevantProb)
                && string.IsNullOrEmpty(coreProb))
            {
                return "";
            }

            string sellText = "";
            double sellParsed;
            if (double.TryParse(
                sellGold,
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture,
                out sellParsed)
                && sellParsed > 0.0)
            {
                sellText = " · 卖价 +" + sellParsed.ToString(
                    "0.0",
                    System.Globalization.CultureInfo.InvariantCulture) + "g";
            }

            return "命中率 "
                + FormatProbability(relevantProb)
                + " · 核心 "
                + FormatProbability(coreProb)
                + " · 期望 "
                + (string.IsNullOrEmpty(expected) ? "-" : expected)
                + sellText;
        }

        private static List<string> FindNamedObjectArrayProperty(
            string json,
            string name,
            string detailField,
            string extraField)
        {
            List<string> values = new List<string>();
            string array = FindArrayProperty(json, name);
            foreach (string itemObject in SplitTopLevelObjects(array))
            {
                string displayName = FirstNonEmpty(
                    FindStringProperty(itemObject, "display_name"),
                    FindStringProperty(itemObject, "name"));
                if (string.IsNullOrEmpty(displayName))
                {
                    continue;
                }

                string detail = FindStringProperty(itemObject, detailField);
                string extra = FindStringProperty(itemObject, extraField);
                string suffix = JoinNonEmpty(" ", extra, detail);
                if (FindBoolProperty(itemObject, "can_upgrade"))
                {
                    suffix = JoinNonEmpty(" · ", suffix, "可升级");
                }
                List<string> enchantments = FindStringArrayProperty(itemObject, "enchantments");
                if (enchantments.Count > 0)
                {
                    suffix = JoinNonEmpty(" · ", suffix, string.Join(", ", enchantments.ToArray()));
                }
                values.Add(string.IsNullOrEmpty(suffix) ? displayName : displayName + " " + suffix);
            }
            return values;
        }

        private static List<string> FindChildOptionArrayProperty(string json)
        {
            List<string> values = new List<string>();
            string array = FindArrayProperty(json, "child_options");
            foreach (string itemObject in SplitTopLevelObjects(array))
            {
                string name = FindStringProperty(itemObject, "name");
                if (string.IsNullOrEmpty(name))
                {
                    name = "未知子选项";
                }

                string reward = FindStringProperty(itemObject, "reward_text");
                string description = FindStringProperty(itemObject, "description");
                string detail = !string.IsNullOrEmpty(reward)
                    ? reward
                    : description;
                string unresolved = FindBoolProperty(itemObject, "unresolved")
                    ? "未完全解析"
                    : "";
                values.Add(JoinNonEmpty(" ", JoinNonEmpty(" ", name, detail), unresolved));
            }
            return values;
        }

        private static List<string> FindAltCoreHitArrayProperty(string json)
        {
            List<string> values = new List<string>();
            string array = FindArrayProperty(json, "alt_core_build_hits");
            foreach (string itemObject in SplitTopLevelObjects(array))
            {
                string name = FirstNonEmpty(
                    FindStringProperty(itemObject, "card_display_name"),
                    FindStringProperty(itemObject, "card_name"));
                if (!string.IsNullOrEmpty(name))
                {
                    string builds = FindBuildNames(itemObject);
                    values.Add(name + "：" + (string.IsNullOrEmpty(builds) ? "其他阵容" : builds) + "核心卡");
                }
            }
            return values;
        }

        private static string FindBuildNames(string json)
        {
            List<string> names = new List<string>();
            string builds = FindArrayProperty(json, "builds");
            foreach (string buildObject in SplitTopLevelObjects(builds))
            {
                string name = FirstNonEmpty(
                    FindStringProperty(buildObject, "display_name"),
                    FindStringProperty(buildObject, "build_name"));
                if (!string.IsNullOrEmpty(name))
                {
                    names.Add(name);
                }
            }
            return string.Join("、", names.ToArray());
        }

        private static List<string> FindCardNameArrayProperty(string json, string name)
        {
            List<string> values = new List<string>();
            string array = FindArrayProperty(json, name);
            foreach (string itemObject in SplitTopLevelObjects(array))
            {
                string displayName = FirstNonEmpty(
                    FindStringProperty(itemObject, "display_name"),
                    FindStringProperty(itemObject, "name"));
                if (!string.IsNullOrEmpty(displayName))
                {
                    values.Add(displayName);
                }
            }
            return values;
        }

        private static int FindPropertyValueStart(string json, string name)
        {
            if (string.IsNullOrEmpty(json) || json[0] != '{')
            {
                return -1;
            }

            int objectDepth = 0;
            int arrayDepth = 0;
            for (int index = 0; index < json.Length; index++)
            {
                char current = json[index];
                if (current == '"')
                {
                    int stringStart = index;
                    string key = ReadJsonString(json, ref index);
                    if (objectDepth != 1 || arrayDepth != 0 || !string.Equals(key, name, StringComparison.Ordinal))
                    {
                        continue;
                    }

                    int colon = index;
                    while (colon < json.Length && char.IsWhiteSpace(json[colon]))
                    {
                        colon++;
                    }
                    if (colon >= json.Length || json[colon] != ':')
                    {
                        index = stringStart;
                        continue;
                    }

                    int valueStart = colon + 1;
                    while (valueStart < json.Length && char.IsWhiteSpace(json[valueStart]))
                    {
                        valueStart++;
                    }
                    return valueStart;
                }

                if (current == '{')
                {
                    objectDepth++;
                }
                else if (current == '}')
                {
                    objectDepth--;
                }
                else if (current == '[')
                {
                    arrayDepth++;
                }
                else if (current == ']')
                {
                    arrayDepth--;
                }
            }
            return -1;
        }

        private static string FindNumberProperty(string json, string name)
        {
            int valueStart = FindPropertyValueStart(json, name);
            if (valueStart < 0 || valueStart >= json.Length)
            {
                return "";
            }

            int index = valueStart;
            while (index < json.Length
                && "-+.0123456789eE".IndexOf(json[index]) >= 0)
            {
                index++;
            }
            return index == valueStart ? "" : json.Substring(valueStart, index - valueStart);
        }

        private static bool FindBoolProperty(string json, string name)
        {
            int valueStart = FindPropertyValueStart(json, name);
            if (valueStart < 0 || valueStart >= json.Length)
            {
                return false;
            }
            return json.IndexOf("true", valueStart, StringComparison.OrdinalIgnoreCase) == valueStart;
        }

        private static int ParseInt(string value)
        {
            int parsed;
            return int.TryParse(value, out parsed) ? parsed : 0;
        }

        private static string FormatProbability(string value)
        {
            double parsed;
            if (!double.TryParse(
                value,
                System.Globalization.NumberStyles.Float,
                System.Globalization.CultureInfo.InvariantCulture,
                out parsed))
            {
                return "-";
            }
            return Math.Round(parsed * 100.0, 1).ToString(
                "0.#",
                System.Globalization.CultureInfo.InvariantCulture) + "%";
        }

        private static string JoinNonEmpty(string separator, string first, string second)
        {
            if (string.IsNullOrEmpty(first))
            {
                return second ?? "";
            }
            if (string.IsNullOrEmpty(second))
            {
                return first;
            }
            return first + separator + second;
        }

        private static int FindMatching(string text, int start, char open, char close)
        {
            int depth = 0;
            bool inString = false;
            bool escaped = false;
            for (int i = start; i < text.Length; i++)
            {
                char c = text[i];
                if (inString)
                {
                    if (escaped)
                    {
                        escaped = false;
                    }
                    else if (c == '\\')
                    {
                        escaped = true;
                    }
                    else if (c == '"')
                    {
                        inString = false;
                    }
                    continue;
                }
                if (c == '"')
                {
                    inString = true;
                    continue;
                }
                if (c == open)
                {
                    depth++;
                }
                else if (c == close)
                {
                    depth--;
                    if (depth == 0)
                    {
                        return i;
                    }
                }
            }
            return -1;
        }

        private static IEnumerable<string> SplitTopLevelObjects(string array)
        {
            int index = 0;
            while (index < array.Length)
            {
                int start = array.IndexOf('{', index);
                if (start < 0)
                {
                    yield break;
                }
                int end = FindMatching(array, start, '{', '}');
                if (end < 0)
                {
                    yield break;
                }
                yield return array.Substring(start, end - start + 1);
                index = end + 1;
            }
        }

        private static string ReadJsonString(string text, ref int index)
        {
            if (index >= text.Length || text[index] != '"')
            {
                return "";
            }
            index++;
            StringBuilder builder = new StringBuilder();
            while (index < text.Length)
            {
                char c = text[index++];
                if (c == '"')
                {
                    return builder.ToString();
                }
                if (c != '\\' || index >= text.Length)
                {
                    builder.Append(c);
                    continue;
                }
                char escaped = text[index++];
                switch (escaped)
                {
                    case '"':
                    case '\\':
                    case '/':
                        builder.Append(escaped);
                        break;
                    case 'b':
                        builder.Append('\b');
                        break;
                    case 'f':
                        builder.Append('\f');
                        break;
                    case 'n':
                        builder.Append('\n');
                        break;
                    case 'r':
                        builder.Append('\r');
                        break;
                    case 't':
                        builder.Append('\t');
                        break;
                    case 'u':
                        if (index + 4 <= text.Length)
                        {
                            string hex = text.Substring(index, 4);
                            ushort value;
                            if (ushort.TryParse(
                                hex,
                                System.Globalization.NumberStyles.HexNumber,
                                null,
                                out value))
                            {
                                builder.Append((char)value);
                            }
                            index += 4;
                        }
                        break;
                    default:
                        builder.Append(escaped);
                        break;
                }
            }
            return builder.ToString();
        }
    }

    internal sealed class TimeoutWebClient : WebClient
    {
        private readonly int timeoutMilliseconds;

        public TimeoutWebClient(int timeoutMs)
        {
            timeoutMilliseconds = timeoutMs;
        }

        protected override WebRequest GetWebRequest(Uri address)
        {
            WebRequest request = base.GetWebRequest(address);
            if (request != null)
            {
                request.Timeout = timeoutMilliseconds;
                HttpWebRequest httpRequest = request as HttpWebRequest;
                if (httpRequest != null)
                {
                    httpRequest.ReadWriteTimeout = timeoutMilliseconds;
                }
            }
            return request;
        }
    }
}
