using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
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
        private ConfigEntry<bool> autoStartHelper;
        private ConfigEntry<string> helperExecutablePath;
        private ConfigEntry<float> pollIntervalSeconds;
        private ConfigEntry<int> topRecommendations;
        private ConfigEntry<bool> includeAi;
        private ConfigEntry<string> toggleKeyConfig;
        private KeyCode toggleKey = KeyCode.F7;
        private string parsedToggleKeyName = "";
        private bool visible = true;
        private volatile bool requestInFlight;
        private float nextPollAt;
        private DateTime lastSuccessfulAnalysisUtc = DateTime.MinValue;
        private DateTime lastHelperStartAttemptUtc = DateTime.MinValue;
        private DateTime lastFailureLogUtc = DateTime.MinValue;
        private string cachedBuildOptionsHero = "";
        private readonly List<OverlayBuildOption> cachedBuildOptions = new List<OverlayBuildOption>();
        private Rect windowRect = new Rect(16f, 56f, 500f, 620f);
        private Rect buildWindowRect = new Rect(16f, 56f, 400f, 620f);
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
            ConfigEntry<bool> autoStart,
            ConfigEntry<string> executablePath,
            ConfigEntry<float> pollInterval,
            ConfigEntry<int> top,
            ConfigEntry<bool> requestAi,
            ConfigEntry<string> toggleKey)
        {
            logger = log;
            enabledConfig = enableOverlay;
            helperBaseUrl = baseUrl;
            autoStartHelper = autoStart;
            helperExecutablePath = executablePath;
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
            float availableHeight = Math.Max(240f, Screen.height - top - margin);
            float gap = 12f;
            float recommendationWidth = Math.Min(500f, Math.Max(360f, availableWidth * 0.32f));
            float panelHeight = Math.Min(availableHeight, Math.Max(360f, availableHeight * 0.66f));
            float buildWidth = Math.Min(400f, Math.Max(340f, availableWidth * 0.28f));

            if (!windowsPlaced)
            {
                if (availableWidth >= recommendationWidth + buildWidth + gap)
                {
                    windowRect = new Rect(margin, top, recommendationWidth, panelHeight);
                    buildWindowRect = new Rect(
                        Screen.width - margin - buildWidth,
                        top,
                        buildWidth,
                        panelHeight);
                }
                else
                {
                    float stackedWidth = Math.Min(availableWidth, 500f);
                    float stackedBuildHeight = Math.Min(
                        panelHeight,
                        Math.Max(180f, availableHeight - panelHeight - gap));
                    windowRect = new Rect(margin, top, stackedWidth, panelHeight);
                    buildWindowRect = new Rect(
                        margin,
                        top + panelHeight + gap,
                        stackedWidth,
                        stackedBuildHeight);
                }
                windowsPlaced = true;
            }

            windowRect.width = Math.Min(windowRect.width, availableWidth);
            windowRect.height = Math.Min(windowRect.height, panelHeight);
            buildWindowRect.width = Math.Min(buildWindowRect.width, availableWidth);
            buildWindowRect.height = Math.Min(buildWindowRect.height, panelHeight);
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
            if (latest.Items.Count == 0
                && latest.ShopVisibleItems.Count == 0
                && latest.ShopCandidates.Count == 0
                && string.IsNullOrEmpty(latest.ShopAction))
            {
                GUILayout.Label("暂时没有可执行建议。", mutedStyle);
            }
            if (latest.ShopVisibleItems.Count > 0)
            {
                GUILayout.BeginVertical(itemStyle);
                GUILayout.Label("当前商店物品", titleStyle);
                DrawInlineList("", latest.ShopVisibleItems, false);
                GUILayout.EndVertical();
            }

            foreach (OverlayShopCandidate candidate in latest.ShopCandidates)
            {
                GUILayout.BeginVertical(itemStyle);
                GUILayout.BeginHorizontal();
                GUILayout.Label(candidate.Name, titleStyle);
                GUILayout.FlexibleSpace();
                GUILayout.Label(candidate.Importance, badgeStyle);
                GUILayout.EndHorizontal();
                GUILayout.Label(candidate.Summary, mutedStyle);
                DrawInlineList("适配 Build", candidate.BuildHits, true);
                DrawInlineList("原因", candidate.Reasons, true);
                DrawInlineList("风险 / 不确定性", candidate.Risks, false);
                GUILayout.EndVertical();
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
            DrawBuildMatchSection();
            GUILayout.Space(6f);
            DrawCardSection("核心卡", latest.BuildDetail.CoreCards);
            DrawCardSection("过渡卡", latest.BuildDetail.TransitionCards);
            DrawCardSection("可选卡", latest.BuildDetail.OptionalCards);
            GUILayout.EndScrollView();
            GUILayout.Label(toggleKey + " 隐藏 / 显示", mutedStyle);
            GUILayout.EndVertical();
            GUI.DragWindow(new Rect(0f, 0f, 10000f, 28f));
        }

        private void DrawBuildMatchSection()
        {
            GUILayout.BeginVertical(itemStyle);
            GUILayout.Label("路线匹配", titleStyle);
            if (latest.BuildMatches.Count == 0)
            {
                GUILayout.Label("暂无足够已拥有卡牌判断更接近的 Build", mutedStyle);
                GUILayout.EndVertical();
                return;
            }

            int count = Math.Min(3, latest.BuildMatches.Count);
            for (int i = 0; i < count; i++)
            {
                OverlayBuildMatch match = latest.BuildMatches[i];
                string name = FirstNonEmpty(match.Name, match.BuildId);
                int totalCore = match.OwnedCore.Count + match.MissingCore.Count;
                string coreText = totalCore > 0
                    ? "核心 " + match.OwnedCore.Count + "/" + totalCore
                    : "核心未配置";
                GUILayout.Label((i == 0 ? "最接近：" : "候选：") + name, reasonStyle);
                GUILayout.Label(
                    MatchBandLabel(match.MatchBand)
                    + " · "
                    + coreText
                    + " · "
                    + RelationLabel(match.Relation)
                    + " · "
                    + ImportanceLabel(match.Importance),
                    mutedStyle);
                if (match.OwnedCore.Count > 0)
                {
                    GUILayout.Label("已命中：" + string.Join("、", match.OwnedCore.ToArray()), reasonStyle);
                }
                else if (match.OwnedOptional.Count > 0)
                {
                    GUILayout.Label("已命中过渡/可选：" + string.Join("、", match.OwnedOptional.ToArray()), reasonStyle);
                }
                else
                {
                    GUILayout.Label("暂无核心命中", mutedStyle);
                }

                if (match.MissingCore.Count > 0)
                {
                    List<string> missing = new List<string>();
                    int missingCount = Math.Min(4, match.MissingCore.Count);
                    for (int j = 0; j < missingCount; j++)
                    {
                        missing.Add(match.MissingCore[j]);
                    }
                    string suffix = match.MissingCore.Count > missingCount ? " 等" : "";
                    GUILayout.Label("缺核心：" + string.Join("、", missing.ToArray()) + suffix, mutedStyle);
                }
            }
            GUILayout.EndVertical();
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

        private static string MatchBandLabel(string value)
        {
            if (string.Equals(value, "locked", StringComparison.Ordinal)) return "已成型";
            if (string.Equals(value, "close", StringComparison.Ordinal)) return "接近成型";
            if (string.Equals(value, "developing", StringComparison.Ordinal)) return "发展中";
            if (string.Equals(value, "seed", StringComparison.Ordinal)) return "有苗头";
            if (string.Equals(value, "none", StringComparison.Ordinal)) return "未成型";
            return string.IsNullOrEmpty(value) ? "未知匹配" : value;
        }

        private static string RelationLabel(string value)
        {
            if (string.Equals(value, "current_build", StringComparison.Ordinal)) return "当前阶段";
            if (string.Equals(value, "future_build", StringComparison.Ordinal)) return "下一阶段";
            if (string.Equals(value, "late_build", StringComparison.Ordinal)) return "后期方向";
            if (string.Equals(value, "past_build", StringComparison.Ordinal)) return "已过期";
            return string.IsNullOrEmpty(value) ? "阶段未知" : value;
        }

        private static string ImportanceLabel(string value)
        {
            if (string.Equals(value, "critical", StringComparison.Ordinal)) return "关键";
            if (string.Equals(value, "high", StringComparison.Ordinal)) return "高匹配";
            if (string.Equals(value, "medium", StringComparison.Ordinal)) return "中匹配";
            if (string.Equals(value, "low", StringComparison.Ordinal)) return "低匹配";
            if (string.Equals(value, "ignored", StringComparison.Ordinal)) return "忽略";
            return string.IsNullOrEmpty(value) ? "匹配未知" : value;
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
                    EnsureHelperServiceStarted();
                    using (TimeoutWebClient client = new TimeoutWebClient(4000))
                    {
                        client.Encoding = Encoding.UTF8;
                        string json = client.DownloadString(url);
                        OverlayAnalysis parsed = OverlayAnalysisParser.Parse(json);
                        if (parsed.BuildOptions.Count == 0 && !string.IsNullOrEmpty(parsed.Hero))
                        {
                            FillBuildOptions(parsed, client);
                        }
                        lock (this)
                        {
                            latest = parsed;
                            lastSuccessfulAnalysisUtc = DateTime.UtcNow;
                        }
                        logger?.LogDebug(
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
                    LogAnalysisFailure(ex);
                }
                finally
                {
                    requestInFlight = false;
                }
            });
        }

        private string BuildAnalysisUrl()
        {
            string baseUrl = GetHelperBaseUrl().TrimEnd('/');
            int top = Math.Max(1, topRecommendations == null ? 3 : topRecommendations.Value);
            string ai = includeAi != null && includeAi.Value ? "1" : "0";
            string url = baseUrl + "/api/analysis?top=" + top + "&ai=" + ai;
            if (!string.IsNullOrEmpty(selectedBuildOverride))
            {
                url += "&build=" + Uri.EscapeDataString(selectedBuildOverride);
            }
            return url;
        }

        private void FillBuildOptions(OverlayAnalysis parsed, WebClient client)
        {
            if (string.Equals(parsed.Hero, cachedBuildOptionsHero, StringComparison.Ordinal)
                && cachedBuildOptions.Count > 0)
            {
                AddBuildOptions(parsed.BuildOptions, cachedBuildOptions);
                return;
            }

            string optionsUrl = BuildOptionsUrl(parsed.Hero);
            string optionsJson = client.DownloadString(optionsUrl);
            List<OverlayBuildOption> options = OverlayAnalysisParser.ParseBuildOptions(optionsJson);
            cachedBuildOptionsHero = parsed.Hero;
            cachedBuildOptions.Clear();
            AddBuildOptions(cachedBuildOptions, options);
            AddBuildOptions(parsed.BuildOptions, options);
        }

        private static void AddBuildOptions(
            List<OverlayBuildOption> target,
            List<OverlayBuildOption> source)
        {
            for (int i = 0; i < source.Count; i++)
            {
                target.Add(new OverlayBuildOption
                {
                    Id = source[i].Id,
                    Name = source[i].Name,
                });
            }
        }

        private void EnsureHelperServiceStarted()
        {
            if ((DateTime.UtcNow - lastSuccessfulAnalysisUtc).TotalSeconds < 15)
            {
                return;
            }

            if (autoStartHelper == null || !autoStartHelper.Value || IsHelperServiceReachable())
            {
                return;
            }

            DateTime now = DateTime.UtcNow;
            if ((now - lastHelperStartAttemptUtc).TotalSeconds < 30)
            {
                return;
            }
            lastHelperStartAttemptUtc = now;

            string executablePath = helperExecutablePath == null ? "" : helperExecutablePath.Value.Trim();
            if (string.IsNullOrEmpty(executablePath) || !File.Exists(executablePath))
            {
                logger?.LogInfo(
                    "In-game overlay could not auto-start BazaarHelper because HelperExecutablePath is not set or missing: "
                    + executablePath);
                return;
            }

            try
            {
                int port = GetHelperPort();
                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = executablePath,
                    Arguments = "--port " + port + " --api-only",
                    WorkingDirectory = Path.GetDirectoryName(executablePath),
                    UseShellExecute = false,
                    CreateNoWindow = true
                };
                Process.Start(startInfo);
                logger?.LogInfo("In-game overlay auto-started BazaarHelper: " + executablePath);
                Thread.Sleep(500);
            }
            catch (Exception ex)
            {
                logger?.LogInfo("In-game overlay failed to auto-start BazaarHelper: " + ex.Message);
            }
        }

        private bool IsHelperServiceReachable()
        {
            try
            {
                Uri uri = new Uri(GetHelperBaseUrl());
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult result = client.BeginConnect(uri.Host, uri.Port, null, null);
                    bool connected = result.AsyncWaitHandle.WaitOne(TimeSpan.FromMilliseconds(250));
                    if (!connected)
                    {
                        return false;
                    }
                    client.EndConnect(result);
                    return true;
                }
            }
            catch
            {
                return false;
            }
        }

        private int GetHelperPort()
        {
            try
            {
                return new Uri(GetHelperBaseUrl()).Port;
            }
            catch
            {
                return 8765;
            }
        }

        private string GetHelperBaseUrl()
        {
            string baseUrl = (helperBaseUrl == null ? "" : helperBaseUrl.Value).Trim();
            return string.IsNullOrEmpty(baseUrl) ? "http://127.0.0.1:8765" : baseUrl;
        }

        private string BuildOptionsUrl(string hero)
        {
            string baseUrl = GetHelperBaseUrl().TrimEnd('/');
            return baseUrl + "/api/options?hero=" + Uri.EscapeDataString(hero);
        }

        private void LogAnalysisFailure(Exception ex)
        {
            DateTime now = DateTime.UtcNow;
            if ((now - lastFailureLogUtc).TotalSeconds >= 30)
            {
                lastFailureLogUtc = now;
                logger?.LogInfo("In-game overlay analysis request failed: " + ex.Message);
            }
            else
            {
                logger?.LogDebug("In-game overlay analysis request failed: " + ex.Message);
            }
        }

        private static string FirstNonEmpty(string first, string second)
        {
            return string.IsNullOrEmpty(first) ? second : first;
        }

        private void ParseToggleKey()
        {
            string keyName = toggleKeyConfig == null ? "F7" : toggleKeyConfig.Value;
            if (string.Equals(keyName, parsedToggleKeyName, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            parsedToggleKeyName = keyName;
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
        public readonly List<OverlayBuildMatch> BuildMatches = new List<OverlayBuildMatch>();
        public readonly List<string> ShopVisibleItems = new List<string>();
        public readonly List<OverlayShopCandidate> ShopCandidates = new List<OverlayShopCandidate>();
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

    internal sealed class OverlayShopCandidate
    {
        public string Name;
        public string Importance;
        public string Summary;
        public readonly List<string> BuildHits = new List<string>();
        public readonly List<string> Reasons = new List<string>();
        public readonly List<string> Risks = new List<string>();
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

    internal sealed class OverlayBuildMatch
    {
        public string BuildId;
        public string Name;
        public string Phase;
        public string MatchBand;
        public string Importance;
        public string Relation;
        public readonly List<string> OwnedCore = new List<string>();
        public readonly List<string> MissingCore = new List<string>();
        public readonly List<string> OwnedOptional = new List<string>();
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
                result.BuildMatches.AddRange(FindBuildMatchArrayProperty(shopObject));
                result.ShopCandidates.AddRange(FindShopCandidateArrayProperty(shopObject));
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

                string currentShop = FindObjectProperty(stateObject, "current_shop");
                if (!string.IsNullOrEmpty(currentShop))
                {
                    result.ShopVisibleItems.AddRange(
                        FindNamedObjectArrayProperty(
                            currentShop,
                            "visible_items",
                            "rarity",
                            "price"));
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
                string extra = FirstNonEmpty(
                    FindStringProperty(itemObject, extraField),
                    FindNumberProperty(itemObject, extraField));
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

        private static List<OverlayShopCandidate> FindShopCandidateArrayProperty(string json)
        {
            List<OverlayShopCandidate> values = new List<OverlayShopCandidate>();
            string array = FindArrayProperty(json, "candidate_cards");
            foreach (string itemObject in SplitTopLevelObjects(array))
            {
                string name = FirstNonEmpty(
                    FindStringProperty(itemObject, "card_display_name"),
                    FindStringProperty(itemObject, "card_name"));
                if (string.IsNullOrEmpty(name))
                {
                    continue;
                }

                string recommendation = FindStringProperty(itemObject, "recommendation_type");
                string price = FindNumberProperty(itemObject, "price");
                string affordable = "";
                if (FindBoolProperty(itemObject, "affordable"))
                {
                    affordable = "买得起";
                }
                else if (FindExplicitFalseProperty(itemObject, "affordable"))
                {
                    affordable = "金币不足";
                }

                OverlayShopCandidate candidate = new OverlayShopCandidate
                {
                    Name = name,
                    Importance = FirstNonEmpty(FindStringProperty(itemObject, "importance"), "-"),
                    Summary = JoinNonEmpty(
                        " · ",
                        recommendation,
                        JoinNonEmpty(" · ", string.IsNullOrEmpty(price) ? "" : price + "g", affordable)),
                };
                candidate.BuildHits.AddRange(FindBuildHitArrayProperty(itemObject));
                candidate.Reasons.AddRange(FindStringArrayProperty(itemObject, "reasons"));
                candidate.Risks.AddRange(FindStringArrayProperty(itemObject, "risks"));
                values.Add(candidate);
            }
            return values;
        }

        private static List<string> FindBuildHitArrayProperty(string json)
        {
            List<string> values = new List<string>();
            string array = FindArrayProperty(json, "build_hits");
            foreach (string itemObject in SplitTopLevelObjects(array))
            {
                string buildName = FindStringProperty(itemObject, "build_name");
                if (string.IsNullOrEmpty(buildName))
                {
                    continue;
                }

                string phase = FindStringProperty(itemObject, "build_phase");
                string role = FindStringProperty(itemObject, "role");
                string relation = FindStringProperty(itemObject, "relation");
                values.Add(
                    buildName
                    + " · "
                    + BuildPhaseLabel(phase)
                    + " · "
                    + BuildRoleLabel(role)
                    + " · "
                    + RelationLabel(relation));
            }
            return values;
        }

        private static string BuildPhaseLabel(string value)
        {
            if (string.Equals(value, "early", StringComparison.Ordinal)) return "前期";
            if (string.Equals(value, "mid", StringComparison.Ordinal)) return "中期";
            if (string.Equals(value, "late", StringComparison.Ordinal)) return "后期";
            return string.IsNullOrEmpty(value) ? "阶段未知" : value;
        }

        private static string BuildRoleLabel(string value)
        {
            if (string.Equals(value, "core", StringComparison.Ordinal)) return "核心";
            if (string.Equals(value, "optional", StringComparison.Ordinal)) return "可选";
            if (string.Equals(value, "transition", StringComparison.Ordinal)) return "过渡";
            return string.IsNullOrEmpty(value) ? "定位未知" : value;
        }

        private static string RelationLabel(string value)
        {
            if (string.Equals(value, "current_build", StringComparison.Ordinal)) return "当前阶段";
            if (string.Equals(value, "future_build", StringComparison.Ordinal)) return "下一阶段";
            if (string.Equals(value, "late_build", StringComparison.Ordinal)) return "后期方向";
            if (string.Equals(value, "past_build", StringComparison.Ordinal)) return "已过期";
            return string.IsNullOrEmpty(value) ? "阶段未知" : value;
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

        private static List<OverlayBuildMatch> FindBuildMatchArrayProperty(string json)
        {
            List<OverlayBuildMatch> values = new List<OverlayBuildMatch>();
            string array = FindArrayProperty(json, "best_matching_builds");
            foreach (string itemObject in SplitTopLevelObjects(array))
            {
                OverlayBuildMatch match = new OverlayBuildMatch();
                match.BuildId = FindStringProperty(itemObject, "build_id");
                match.Name = FirstNonEmpty(
                    FindStringProperty(itemObject, "name"),
                    match.BuildId);
                match.Phase = FindStringProperty(itemObject, "phase");
                match.MatchBand = FindStringProperty(itemObject, "match_band");
                match.Importance = FindStringProperty(itemObject, "importance");
                match.Relation = FindStringProperty(itemObject, "relation");
                match.OwnedCore.AddRange(FindStringArrayProperty(itemObject, "owned_core"));
                match.MissingCore.AddRange(FindStringArrayProperty(itemObject, "missing_core"));
                match.OwnedOptional.AddRange(FindStringArrayProperty(itemObject, "owned_optional"));
                if (!string.IsNullOrEmpty(match.BuildId) || !string.IsNullOrEmpty(match.Name))
                {
                    values.Add(match);
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

        private static bool FindExplicitFalseProperty(string json, string name)
        {
            int valueStart = FindPropertyValueStart(json, name);
            if (valueStart < 0 || valueStart >= json.Length)
            {
                return false;
            }
            return json.IndexOf("false", valueStart, StringComparison.OrdinalIgnoreCase) == valueStart;
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
