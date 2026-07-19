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
        private const int AnalysisTimeoutMilliseconds = 8000;
        private const int AiAnalysisTimeoutMilliseconds = 120000;

        private ManualLogSource logger;
        private ConfigEntry<bool> enabledConfig;
        private ConfigEntry<string> helperBaseUrl;
        private ConfigEntry<bool> autoStartHelper;
        private ConfigEntry<string> helperExecutablePath;
        private ConfigEntry<float> pollIntervalSeconds;
        private ConfigEntry<int> topRecommendations;
        private ConfigEntry<bool> manualAnalyzeConfig;
        private ConfigEntry<bool> includeAi;
        private ConfigEntry<bool> autoAnalyzeConfig;
        private ConfigEntry<float> fontScaleConfig;
        private ConfigEntry<string> toggleKeyConfig;
        private ConfigEntry<string> lockToggleKeyConfig;
        private ConfigEntry<string> manualAnalysisKeyConfig;
        private ConfigEntry<string> aiAnalysisKeyConfig;
        private ConfigEntry<bool> lockedConfig;
        private ConfigEntry<float> recommendationXConfig;
        private ConfigEntry<float> recommendationYConfig;
        private ConfigEntry<float> recommendationWidthConfig;
        private ConfigEntry<float> recommendationHeightConfig;
        private ConfigEntry<float> buildXConfig;
        private ConfigEntry<float> buildYConfig;
        private ConfigEntry<float> buildWidthConfig;
        private ConfigEntry<float> buildHeightConfig;
        private ConfigFile configFile;
        private KeyCode toggleKey = KeyCode.F7;
        private KeyCode lockToggleKey = KeyCode.F6;
        private KeyCode manualAnalysisKey = KeyCode.F8;
        private KeyCode aiAnalysisKey = KeyCode.F5;
        private string parsedToggleKeyName = "";
        private string parsedLockToggleKeyName = "";
        private string parsedManualAnalysisKeyName = "";
        private string parsedAiAnalysisKeyName = "";
        private bool visible = true;
        private bool layoutLocked = true;
        private bool settingsVisible;
        private bool nextRequestIncludeAi;
        private volatile bool requestInFlight;
        private volatile bool simulationRequestInFlight;
        private volatile bool updateRequestInFlight;
        private float nextAutoAnalyzeAt;
        private DateTime lastSuccessfulAnalysisUtc = DateTime.MinValue;
        private DateTime lastHelperStartAttemptUtc = DateTime.MinValue;
        private DateTime lastFailureLogUtc = DateTime.MinValue;
        private DateTime lastUpdateFailureLogUtc = DateTime.MinValue;
        private string lastSuccessfulAnalysisKey = "";
        private string cachedBuildOptionsHero = "";
        private Process helperProcessStartedByOverlay;
        private readonly List<OverlayBuildOption> cachedBuildOptions = new List<OverlayBuildOption>();
        private Rect windowRect = new Rect(16f, 56f, 500f, 620f);
        private Rect buildWindowRect = new Rect(532f, 56f, 400f, 620f);
        private bool windowsPlaced;
        private int resizingWindowId;
        private bool layoutDirty;
        private float saveLayoutAt;
        private Vector2 scrollPosition;
        private Vector2 buildScrollPosition;
        private OverlayAnalysis latest = OverlayAnalysis.Waiting("正在等待 BazaarHelper...");
        private OverlayCombatSimulation latestCombat = OverlayCombatSimulation.Waiting("");
        private OverlayUpdateState updateState = new OverlayUpdateState();
        private string selectedBuildOverride = "";
        private GUIStyle windowStyle;
        private GUIStyle titleStyle;
        private GUIStyle itemStyle;
        private GUIStyle reasonStyle;
        private GUIStyle badgeStyle;
        private GUIStyle mutedStyle;
        private GUIStyle resizeHandleStyle;

        public void Initialize(
            ManualLogSource log,
            ConfigEntry<bool> enableOverlay,
            ConfigEntry<string> baseUrl,
            ConfigEntry<bool> autoStart,
            ConfigEntry<string> executablePath,
            ConfigEntry<float> pollInterval,
            ConfigEntry<int> top,
            ConfigEntry<bool> manualAnalyze,
            ConfigEntry<bool> requestAi,
            ConfigEntry<bool> autoAnalyze,
            ConfigEntry<float> fontScale,
            ConfigEntry<string> toggleKey,
            ConfigEntry<string> lockToggleKey,
            ConfigEntry<string> manualKey,
            ConfigEntry<string> aiKey,
            ConfigEntry<bool> locked,
            ConfigEntry<float> recommendationX,
            ConfigEntry<float> recommendationY,
            ConfigEntry<float> recommendationWidth,
            ConfigEntry<float> recommendationHeight,
            ConfigEntry<float> buildX,
            ConfigEntry<float> buildY,
            ConfigEntry<float> buildWidth,
            ConfigEntry<float> buildHeight,
            ConfigFile config)
        {
            logger = log;
            enabledConfig = enableOverlay;
            helperBaseUrl = baseUrl;
            autoStartHelper = autoStart;
            helperExecutablePath = executablePath;
            pollIntervalSeconds = pollInterval;
            topRecommendations = top;
            manualAnalyzeConfig = manualAnalyze;
            includeAi = requestAi;
            autoAnalyzeConfig = autoAnalyze;
            fontScaleConfig = fontScale;
            toggleKeyConfig = toggleKey;
            lockToggleKeyConfig = lockToggleKey;
            manualAnalysisKeyConfig = manualKey;
            aiAnalysisKeyConfig = aiKey;
            lockedConfig = locked;
            recommendationXConfig = recommendationX;
            recommendationYConfig = recommendationY;
            recommendationWidthConfig = recommendationWidth;
            recommendationHeightConfig = recommendationHeight;
            buildXConfig = buildX;
            buildYConfig = buildY;
            buildWidthConfig = buildWidth;
            buildHeightConfig = buildHeight;
            configFile = config;
            layoutLocked = lockedConfig == null || lockedConfig.Value;
            LoadConfiguredLayout();
            ParseToggleKey();
            ParseLockToggleKey();
            ParseManualAnalysisKey();
            ParseAiAnalysisKey();
            logger?.LogInfo(
                "In-game overlay initialized url="
                + (helperBaseUrl == null ? "" : helperBaseUrl.Value)
                + " poll="
                + (pollIntervalSeconds == null ? 0f : pollIntervalSeconds.Value));
        }

        private void Start()
        {
            logger?.LogInfo("In-game overlay started.");
            latest = OverlayAnalysis.Waiting(ModeStatusText());
            RequestUpdateStatus(false);
        }

        private void OnApplicationQuit()
        {
            StopHelperStartedByOverlay();
        }

        private void OnDestroy()
        {
            StopHelperStartedByOverlay();
        }

        private void Update()
        {
            if (enabledConfig == null || !enabledConfig.Value || !AutoAnalyzeEnabled())
            {
                return;
            }
            if (requestInFlight || Time.unscaledTime < nextAutoAnalyzeAt)
            {
                return;
            }

            nextAutoAnalyzeAt = Time.unscaledTime + AutoAnalyzeInterval();
            RequestAnalysis(false, "自动分析中...", true);
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
            Rect previousWindowRect = windowRect;
            Rect previousBuildWindowRect = buildWindowRect;
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
            if (!layoutLocked
                && (!RectsNearlyEqual(previousWindowRect, windowRect)
                    || !RectsNearlyEqual(previousBuildWindowRect, buildWindowRect)))
            {
                MarkLayoutDirty();
            }
            HandleResizeGrip(90210, ref windowRect);
            HandleResizeGrip(90211, ref buildWindowRect);
            SaveLayoutIfNeeded();
        }

        private void LoadConfiguredLayout()
        {
            windowRect = new Rect(
                ConfigFloat(recommendationXConfig, 16f),
                ConfigFloat(recommendationYConfig, 56f),
                ConfigFloat(recommendationWidthConfig, 500f),
                ConfigFloat(recommendationHeightConfig, 620f));
            buildWindowRect = new Rect(
                ConfigFloat(buildXConfig, 532f),
                ConfigFloat(buildYConfig, 56f),
                ConfigFloat(buildWidthConfig, 400f),
                ConfigFloat(buildHeightConfig, 620f));
            windowsPlaced = true;
        }

        private static float ConfigFloat(ConfigEntry<float> entry, float fallback)
        {
            return entry == null ? fallback : entry.Value;
        }

        private static bool RectsNearlyEqual(Rect left, Rect right)
        {
            return Math.Abs(left.x - right.x) < 0.5f
                && Math.Abs(left.y - right.y) < 0.5f
                && Math.Abs(left.width - right.width) < 0.5f
                && Math.Abs(left.height - right.height) < 0.5f;
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
            if (lockToggleKeyConfig != null)
            {
                ParseLockToggleKey();
            }
            if (manualAnalysisKeyConfig != null)
            {
                ParseManualAnalysisKey();
            }
            if (aiAnalysisKeyConfig != null)
            {
                ParseAiAnalysisKey();
            }
            Event current = Event.current;
            if (current == null || current.type != EventType.KeyDown)
            {
                return;
            }

            if (current.keyCode == toggleKey)
            {
                visible = !visible;
                current.Use();
                return;
            }

            if (current.keyCode == lockToggleKey)
            {
                layoutLocked = !layoutLocked;
                if (lockedConfig != null)
                {
                    lockedConfig.Value = layoutLocked;
                    configFile?.Save();
                }
                current.Use();
                return;
            }

            if (current.keyCode == manualAnalysisKey && ManualAnalyzeEnabled())
            {
                RequestAnalysis(false, "正在扫描当前局面...", false);
                current.Use();
                return;
            }

            if (current.keyCode == aiAnalysisKey && ManualAnalyzeEnabled())
            {
                RequestAnalysis(true, "正在请求智能阵容分析...", false);
                current.Use();
            }
        }

        private void HandleResizeGrip(int windowId, ref Rect rect)
        {
            if (layoutLocked)
            {
                return;
            }

            const float handleSize = 18f;
            Rect handle = new Rect(
                rect.xMax - handleSize - 3f,
                rect.yMax - handleSize - 3f,
                handleSize,
                handleSize);
            GUI.Box(handle, "↘", resizeHandleStyle);

            Event current = Event.current;
            if (current == null)
            {
                return;
            }

            if (current.type == EventType.MouseDown && current.button == 0 && handle.Contains(current.mousePosition))
            {
                resizingWindowId = windowId;
                current.Use();
            }
            else if (current.type == EventType.MouseDrag && resizingWindowId == windowId)
            {
                rect.width = Mathf.Clamp(current.mousePosition.x - rect.x + 8f, 280f, Screen.width - rect.x - 8f);
                rect.height = Mathf.Clamp(current.mousePosition.y - rect.y + 8f, 180f, Screen.height - rect.y - 8f);
                MarkLayoutDirty();
                current.Use();
            }
            else if (current.type == EventType.MouseUp && resizingWindowId == windowId)
            {
                resizingWindowId = 0;
                MarkLayoutDirty();
                SaveLayoutNow();
                current.Use();
            }
        }

        private void MarkLayoutDirty()
        {
            layoutDirty = true;
            saveLayoutAt = Time.unscaledTime + 0.5f;
        }

        private void SaveLayoutIfNeeded()
        {
            if (!layoutDirty || Time.unscaledTime < saveLayoutAt)
            {
                return;
            }

            SaveLayoutNow();
        }

        private void SaveLayoutNow()
        {
            if (!layoutDirty)
            {
                return;
            }

            SetConfig(recommendationXConfig, windowRect.x);
            SetConfig(recommendationYConfig, windowRect.y);
            SetConfig(recommendationWidthConfig, windowRect.width);
            SetConfig(recommendationHeightConfig, windowRect.height);
            SetConfig(buildXConfig, buildWindowRect.x);
            SetConfig(buildYConfig, buildWindowRect.y);
            SetConfig(buildWidthConfig, buildWindowRect.width);
            SetConfig(buildHeightConfig, buildWindowRect.height);
            configFile?.Save();
            layoutDirty = false;
        }

        private static void SetConfig(ConfigEntry<float> entry, float value)
        {
            if (entry != null && Math.Abs(entry.Value - value) > 0.001f)
            {
                entry.Value = value;
            }
        }

        private void ResetStyles()
        {
            windowStyle = null;
            titleStyle = null;
            itemStyle = null;
            reasonStyle = null;
            badgeStyle = null;
            mutedStyle = null;
            resizeHandleStyle = null;
        }

        private void DrawWindow(int windowId)
        {
            GUILayout.BeginVertical();
            GUILayout.Label("当前推荐", titleStyle);
            if (!string.IsNullOrEmpty(latest.Status))
            {
                GUILayout.Label(latest.Status, mutedStyle);
            }
            GUILayout.BeginHorizontal();
            GUI.enabled = !requestInFlight;
            if (ManualAnalyzeEnabled() && GUILayout.Button("扫描分析 (" + manualAnalysisKey + ")", GUILayout.MinHeight(28f)))
            {
                RequestAnalysis(false, "正在扫描当前局面...", false);
            }
            if (ManualAnalyzeEnabled() && GUILayout.Button("智能分析 (" + aiAnalysisKey + ")", GUILayout.MinHeight(28f)))
            {
                RequestAnalysis(true, "正在请求智能阵容分析...", false);
            }
            GUI.enabled = true;
            if (GUILayout.Button(settingsVisible ? "收起设置" : "设置", GUILayout.Width(92f), GUILayout.MinHeight(28f)))
            {
                settingsVisible = !settingsVisible;
            }
            GUILayout.EndHorizontal();
            if (settingsVisible)
            {
                DrawSettingsPanel();
            }

            DrawUpdatePanel();

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
                DrawInlineList("适配阵容", candidate.BuildHits, true);
                DrawInlineList("原因", candidate.Reasons, true);
                DrawInlineList("风险与不确定性", candidate.Risks, false);
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
            GUILayout.Label(toggleKey + " 隐藏 / 显示 · " + lockToggleKey + (layoutLocked ? " 解锁布局" : " 锁定布局"), mutedStyle);
            GUILayout.EndVertical();
            if (!layoutLocked)
            {
                Rect before = windowRect;
                GUI.DragWindow(new Rect(0f, 0f, 10000f, 28f));
                if (before.x != windowRect.x || before.y != windowRect.y)
                {
                    MarkLayoutDirty();
                }
            }
        }

        private void DrawBuildWindow(int windowId)
        {
            GUILayout.BeginVertical();
            string buildName = FirstNonEmpty(latest.CurrentBuildName, latest.CurrentBuildId);
            GUILayout.Label(string.IsNullOrEmpty(buildName) ? "当前阵容" : buildName, titleStyle);

            GUI.enabled = !simulationRequestInFlight;
            if (GUILayout.Button(simulationRequestInFlight ? "正在模拟..." : "模拟当前阵容", GUILayout.MinHeight(28f)))
            {
                RequestCombatSimulation();
            }
            GUI.enabled = true;

            buildScrollPosition = GUILayout.BeginScrollView(buildScrollPosition, false, true);
            DrawAiAnalysisSection();
            GUILayout.Space(6f);
            DrawCombatSimulationSection();
            GUILayout.Space(6f);
            if (false && latest.BuildOptions.Count > 0)
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
                        RequestAnalysis(false, "正在切换阵容并扫描...", false);
                    }
                    GUI.enabled = true;
                }
            }

            GUILayout.Space(8f);
            if (false && !string.IsNullOrEmpty(latest.AiAnalysis))
            {
                GUILayout.BeginVertical(itemStyle);
                GUILayout.Label("智能阵容分析", titleStyle);
                GUILayout.Label(latest.AiAnalysis, reasonStyle);
                GUILayout.EndVertical();
            }
            else if (false && !string.IsNullOrEmpty(latest.AiError))
            {
                GUILayout.BeginVertical(itemStyle);
                GUILayout.Label("智能阵容分析", titleStyle);
                GUILayout.Label(latest.AiError, mutedStyle);
                GUILayout.EndVertical();
            }
            GUILayout.Space(6f);
            DrawBuildMatchSection();
            GUILayout.Space(6f);
            DrawCardSection("核心卡", latest.BuildDetail.CoreCards);
            DrawCardSection("可选卡", latest.BuildDetail.OptionalCards);
            GUILayout.EndScrollView();
            GUILayout.Label(toggleKey + " 隐藏 / 显示 · " + lockToggleKey + (layoutLocked ? " 解锁布局" : " 锁定布局") + " · " + manualAnalysisKey + " 扫描分析 · " + aiAnalysisKey + " 智能分析", mutedStyle);
            GUILayout.EndVertical();
            if (!layoutLocked)
            {
                Rect before = buildWindowRect;
                GUI.DragWindow(new Rect(0f, 0f, 10000f, 28f));
                if (before.x != buildWindowRect.x || before.y != buildWindowRect.y)
                {
                    MarkLayoutDirty();
                }
            }
        }

        private void DrawAiAnalysisSection()
        {
            GUILayout.BeginVertical(itemStyle);
            GUILayout.Label("智能阵容分析", titleStyle);
            if (!string.IsNullOrEmpty(latest.AiAnalysis))
            {
                GUILayout.Label(latest.AiAnalysis, reasonStyle);
            }
            else if (!string.IsNullOrEmpty(latest.AiError))
            {
                GUILayout.Label(latest.AiError, mutedStyle);
            }
            else
            {
                GUILayout.Label("点击智能分析，或在设置中开启自动带 AI。", mutedStyle);
            }
            GUILayout.EndVertical();
        }

        private void DrawSettingsPanel()
        {
            GUILayout.BeginVertical(itemStyle);
            GUILayout.Label("设置", titleStyle);
            DrawBoolSetting("自动分析", AutoAnalyzeEnabled(), ToggleAutoAnalyze);
            DrawBoolSetting("显示手动分析按钮", ManualAnalyzeEnabled(), ToggleManualAnalyze);

            GUI.enabled = !updateRequestInFlight;
            if (GUILayout.Button("检查更新", GUILayout.MinHeight(26f)))
            {
                RequestUpdateStatus(true);
            }
            GUI.enabled = true;
            if (updateRequestInFlight)
            {
                GUILayout.Label("正在检查更新...", mutedStyle);
            }
            else if (updateState != null
                && !updateState.UpdateAvailable
                && !string.IsNullOrEmpty(updateState.Message))
            {
                GUILayout.Label(updateState.Message, mutedStyle);
            }

            GUILayout.Space(4f);
            GUILayout.Label("字号", mutedStyle);
            GUILayout.BeginHorizontal();
            DrawFontScaleButton("标准", 1.0f);
            DrawFontScaleButton("大", 1.15f);
            DrawFontScaleButton("更大", 1.3f);
            DrawFontScaleButton("最大", 1.5f);
            GUILayout.EndHorizontal();

            DrawCompactBuildSelection();
            GUILayout.EndVertical();
        }

        private void DrawBoolSetting(string label, bool value, Action toggle)
        {
            GUILayout.BeginHorizontal();
            GUILayout.Label(label, reasonStyle);
            GUILayout.FlexibleSpace();
            if (GUILayout.Button(value ? "开" : "关", GUILayout.Width(64f), GUILayout.MinHeight(26f)))
            {
                toggle();
            }
            GUILayout.EndHorizontal();
        }

        private void DrawFontScaleButton(string label, float scale)
        {
            bool active = Math.Abs(FontScale() - scale) < 0.01f;
            GUI.enabled = !active;
            if (GUILayout.Button(active ? "✓ " + label : label, GUILayout.MinHeight(26f)))
            {
                SetConfig(fontScaleConfig, scale);
                configFile?.Save();
                ResetStyles();
            }
            GUI.enabled = true;
        }

        private void DrawCompactBuildSelection()
        {
            if (latest.BuildOptions.Count == 0)
            {
                return;
            }

            GUILayout.Space(6f);
            GUILayout.Label("阵容匹配", mutedStyle);
            for (int i = 0; i < latest.BuildOptions.Count; i++)
            {
                OverlayBuildOption option = latest.BuildOptions[i];
                bool active = string.Equals(
                    option.Id,
                    string.IsNullOrEmpty(selectedBuildOverride)
                        ? latest.CurrentBuildId
                        : selectedBuildOverride,
                    StringComparison.Ordinal);
                GUI.enabled = !active;
                if (GUILayout.Button(active ? "✓ " + option.Name : option.Name, GUILayout.MinHeight(24f)))
                {
                    selectedBuildOverride = option.Id;
                    RequestAnalysis(false, "正在切换阵容并扫描...", false);
                }
                GUI.enabled = true;
            }
        }

        private void DrawUpdatePanel()
        {
            OverlayUpdateState state = updateState;
            if (state == null || !state.UpdateAvailable || state.Dismissed)
            {
                return;
            }

            GUILayout.BeginVertical(itemStyle);
            GUILayout.Label("发现新版本 " + state.Version, titleStyle);
            DrawInlineList("更新内容", state.Changelog, false);
            if (!string.IsNullOrEmpty(state.Message))
            {
                GUILayout.Label(state.Message, mutedStyle);
            }
            if (!string.IsNullOrEmpty(state.PackageVersion))
            {
                GUILayout.Label("已识别新版更新包 " + state.PackageVersion, reasonStyle);
                GUILayout.Label("安装更新时程序将自动关闭，更新完成后会重新启动。", mutedStyle);
                GUILayout.Label("请先关闭 The Bazaar，避免游戏占用插件 DLL 导致覆盖失败。", mutedStyle);
                GUILayout.BeginHorizontal();
                GUI.enabled = !updateRequestInFlight;
                if (GUILayout.Button("立即安装", GUILayout.MinHeight(28f)))
                {
                    RequestInstallUpdate();
                }
                if (GUILayout.Button("取消", GUILayout.MinHeight(28f)))
                {
                    state.PackagePath = "";
                    state.PackageVersion = "";
                    state.Message = "";
                }
                GUI.enabled = true;
                GUILayout.EndHorizontal();
            }
            else
            {
                GUILayout.BeginHorizontal();
                GUI.enabled = !updateRequestInFlight;
                if (GUILayout.Button("前往夸克下载", GUILayout.MinHeight(28f)))
                {
                    RequestOpenUpdateDownload();
                }
                if (GUILayout.Button("选择更新包", GUILayout.MinHeight(28f)))
                {
                    RequestSelectUpdatePackage();
                }
                GUI.enabled = true;
                GUILayout.EndHorizontal();
                if (GUILayout.Button("暂不更新", GUILayout.MinHeight(24f)))
                {
                    RequestDismissUpdate();
                }
            }
            GUILayout.EndVertical();
        }

        private void DrawCombatSimulationSection()
        {
            OverlayCombatSimulation combat = latestCombat;
            if (combat == null || (string.IsNullOrEmpty(combat.Status) && !combat.HasResult))
            {
                return;
            }

            GUILayout.BeginVertical(itemStyle);
            GUILayout.Label("当前阵容战斗模拟", titleStyle);
            if (!string.IsNullOrEmpty(combat.Status))
            {
                GUILayout.Label(combat.Status, combat.HasResult ? mutedStyle : reasonStyle);
            }
            if (combat.HasResult)
            {
                GUILayout.Label("总伤害：" + combat.TotalDamage + "  DPS：" + combat.DamagePerSecond, reasonStyle);
                GUILayout.Label("直接伤害：" + combat.DirectDamage + "  护盾：" + combat.TotalShield, mutedStyle);
                GUILayout.Label("燃烧跳伤：" + combat.BurnTickDamage + "  毒跳伤：" + combat.PoisonTickDamage, mutedStyle);
                GUILayout.Label("击杀时间：" + combat.KillTime + "  模拟卡数：" + combat.SimulatedCardCount, mutedStyle);
                if (combat.SkippedCardCount > 0)
                {
                    GUILayout.Label("有 " + combat.SkippedCardCount + " 张卡暂未参与模拟。", mutedStyle);
                }
            }
            GUILayout.EndVertical();
        }

        private void DrawBuildMatchSection()
        {
            GUILayout.BeginVertical(itemStyle);
            GUILayout.Label("路线匹配", titleStyle);
            if (latest.BuildMatches.Count == 0)
            {
                GUILayout.Label("暂无足够已拥有卡牌判断更接近的阵容", mutedStyle);
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
                    GUILayout.Label("已命中可选：" + string.Join("、", match.OwnedOptional.ToArray()), reasonStyle);
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
            if (string.Equals(value, "high", StringComparison.Ordinal)) return "高";
            if (string.Equals(value, "medium", StringComparison.Ordinal)) return "中";
            if (string.Equals(value, "low", StringComparison.Ordinal)) return "低";
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

        private void RequestUpdateStatus(bool refresh)
        {
            if (updateRequestInFlight)
            {
                return;
            }
            OverlayUpdateState existing = updateState;
            if (!refresh
                && existing != null
                && (!string.IsNullOrEmpty(existing.CurrentVersion) || existing.UpdateAvailable))
            {
                return;
            }
            updateRequestInFlight = true;
            if (refresh)
            {
                lock (this)
                {
                    updateState.Message = "正在检查更新...";
                }
            }
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    EnsureHelperServiceStarted();
                    string url = GetHelperBaseUrl().TrimEnd('/') + "/api/update/status";
                    if (refresh)
                    {
                        url += "?force=1";
                    }
                    using (TimeoutWebClient client = new TimeoutWebClient(4000))
                    {
                        client.Encoding = Encoding.UTF8;
                        string json = client.DownloadString(url);
                        OverlayUpdateState parsed = OverlayAnalysisParser.ParseUpdateStatus(json);
                        lock (this)
                        {
                            if (refresh)
                            {
                                if (!string.IsNullOrEmpty(parsed.Error))
                                {
                                    parsed.Message = "更新检查失败：" + parsed.Error;
                                }
                                else if (parsed.UpdateAvailable)
                                {
                                    parsed.Message = "发现新版本 " + parsed.Version;
                                }
                                else
                                {
                                    parsed.Message = string.IsNullOrEmpty(parsed.CurrentVersion)
                                        ? "当前已是最新版本。"
                                        : "当前已是最新版本 " + parsed.CurrentVersion + "。";
                                }
                            }
                            if (!string.IsNullOrEmpty(updateState.PackagePath)
                                && string.Equals(updateState.Version, parsed.Version, StringComparison.Ordinal))
                            {
                                parsed.PackagePath = updateState.PackagePath;
                                parsed.PackageVersion = updateState.PackageVersion;
                                parsed.Message = updateState.Message;
                            }
                            updateState = parsed;
                        }
                    }
                }
                catch (Exception ex)
                {
                    if (refresh)
                    {
                        lock (this)
                        {
                            updateState.Message = "更新检查失败：" + ex.Message;
                        }
                    }
                    LogUpdateFailure(ex);
                }
                finally
                {
                    updateRequestInFlight = false;
                }
            });
        }

        private void RequestOpenUpdateDownload()
        {
            PostUpdateCommand("/api/update/open-download", "已打开夸克下载页。");
        }

        private void RequestDismissUpdate()
        {
            PostUpdateCommand("/api/update/dismiss", "");
        }

        private void RequestSelectUpdatePackage()
        {
            updateRequestInFlight = true;
            updateState.Message = "正在等待选择更新包...";
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    EnsureHelperServiceStarted();
                    using (TimeoutWebClient client = new TimeoutWebClient(600000))
                    {
                        client.Encoding = Encoding.UTF8;
                        client.Headers[HttpRequestHeader.ContentType] = "application/json; charset=utf-8";
                        string json = client.UploadString(
                            GetHelperBaseUrl().TrimEnd('/') + "/api/update/select-package",
                            "POST",
                            "{}");
                        OverlayUpdatePackage package = OverlayAnalysisParser.ParseUpdatePackage(json);
                        lock (this)
                        {
                            updateState.PackagePath = package.Path;
                            updateState.PackageVersion = package.Version;
                            updateState.Message = string.IsNullOrEmpty(package.Version)
                                ? "已识别新版更新包。"
                                : "已识别新版更新包 " + package.Version + "。";
                        }
                    }
                }
                catch (Exception ex)
                {
                    lock (this)
                    {
                        updateState.Message = "更新包识别失败：" + ex.Message;
                    }
                    LogUpdateFailure(ex);
                }
                finally
                {
                    updateRequestInFlight = false;
                }
            });
        }

        private void RequestInstallUpdate()
        {
            string path = updateState == null ? "" : updateState.PackagePath;
            if (string.IsNullOrEmpty(path))
            {
                return;
            }

            updateRequestInFlight = true;
            updateState.Message = "正在启动更新器...";
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    EnsureHelperServiceStarted();
                    using (TimeoutWebClient client = new TimeoutWebClient(10000))
                    {
                        client.Encoding = Encoding.UTF8;
                        client.Headers[HttpRequestHeader.ContentType] = "application/json; charset=utf-8";
                        client.UploadString(
                            GetHelperBaseUrl().TrimEnd('/') + "/api/update/install",
                            "POST",
                            "{\"path\":\"" + EscapeJson(path) + "\"}");
                    }
                    lock (this)
                    {
                        updateState.Message = "更新器已启动，助手会关闭并安装新版。";
                    }
                }
                catch (Exception ex)
                {
                    lock (this)
                    {
                        updateState.Message = "启动更新失败：" + ex.Message;
                    }
                    LogUpdateFailure(ex);
                }
                finally
                {
                    updateRequestInFlight = false;
                }
            });
        }

        private void PostUpdateCommand(string endpoint, string successMessage)
        {
            updateRequestInFlight = true;
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    EnsureHelperServiceStarted();
                    using (TimeoutWebClient client = new TimeoutWebClient(10000))
                    {
                        client.Encoding = Encoding.UTF8;
                        client.Headers[HttpRequestHeader.ContentType] = "application/json; charset=utf-8";
                        string json = client.UploadString(
                            GetHelperBaseUrl().TrimEnd('/') + endpoint,
                            "POST",
                            "{}");
                        OverlayUpdateState parsed = OverlayAnalysisParser.ParseUpdateStatus(json);
                        lock (this)
                        {
                            if (!string.IsNullOrEmpty(parsed.Version) || parsed.Dismissed)
                            {
                                updateState = parsed;
                            }
                            if (!string.IsNullOrEmpty(successMessage))
                            {
                                updateState.Message = successMessage;
                            }
                        }
                    }
                }
                catch (Exception ex)
                {
                    lock (this)
                    {
                        updateState.Message = "更新操作失败：" + ex.Message;
                    }
                    LogUpdateFailure(ex);
                }
                finally
                {
                    updateRequestInFlight = false;
                }
            });
        }

        private void RequestAnalysis(bool includeAiForNextRequest, string status)
        {
            RequestAnalysis(includeAiForNextRequest, status, false);
        }

        private void RequestCombatSimulation()
        {
            if (simulationRequestInFlight)
            {
                latestCombat = OverlayCombatSimulation.Waiting("正在模拟，请稍候...");
                return;
            }

            simulationRequestInFlight = true;
            latestCombat = OverlayCombatSimulation.Waiting("正在导出当前阵容并估算伤害...");
            ManualExportResult exportResult = Plugin.RequestManualExport();
            if (exportResult == null || !exportResult.SnapshotAvailable)
            {
                latestCombat = OverlayCombatSimulation.Waiting("暂时没有可模拟的实时阵容。");
                simulationRequestInFlight = false;
                return;
            }

            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    EnsureHelperServiceStarted();
                    string url = GetHelperBaseUrl().TrimEnd('/') + "/api/combat-simulation?duration=60&trials=1";
                    using (TimeoutWebClient client = new TimeoutWebClient(8000))
                    {
                        client.Encoding = Encoding.UTF8;
                        string json = client.DownloadString(url);
                        OverlayCombatSimulation parsed = OverlayAnalysisParser.ParseCombatSimulation(json);
                        lock (this)
                        {
                            latestCombat = parsed;
                        }
                    }
                }
                catch (Exception ex)
                {
                    lock (this)
                    {
                        latestCombat = OverlayCombatSimulation.Waiting("模拟失败：" + ex.Message);
                    }
                    LogAnalysisFailure(ex);
                }
                finally
                {
                    simulationRequestInFlight = false;
                }
            });
        }

        private void RequestAnalysis(bool includeAiForNextRequest, string status, bool automatic)
        {
            if (requestInFlight)
            {
                if (!automatic)
                {
                    latest.Status = "正在分析，请稍候...";
                }
                return;
            }

            if (!automatic)
            {
                latest.Status = status;
            }
            bool includeAiForRequest = includeAiForNextRequest;
            ManualExportResult exportResult = automatic
                ? Plugin.RequestAutomaticExport()
                : Plugin.RequestManualExport();
            string analysisKey = BuildAnalysisRequestKey(
                exportResult == null ? "" : exportResult.StateSignature,
                includeAiForRequest);
            if (automatic
                && exportResult != null
                && exportResult.SnapshotAvailable
                && !exportResult.StateChanged
                && !string.IsNullOrEmpty(analysisKey)
                && string.Equals(analysisKey, lastSuccessfulAnalysisKey, StringComparison.Ordinal))
            {
                logger?.LogDebug("Skipped in-game overlay analysis because state signature did not change.");
                return;
            }

            nextRequestIncludeAi = includeAiForRequest;
            StartAnalysisRequest(analysisKey, automatic);
        }

        private bool AutoAnalyzeEnabled()
        {
            return autoAnalyzeConfig != null && autoAnalyzeConfig.Value;
        }

        private bool ManualAnalyzeEnabled()
        {
            return manualAnalyzeConfig == null || manualAnalyzeConfig.Value;
        }

        private float AutoAnalyzeInterval()
        {
            return Math.Max(0.5f, pollIntervalSeconds == null ? 2.0f : pollIntervalSeconds.Value);
        }

        private float FontScale()
        {
            return Mathf.Clamp(fontScaleConfig == null ? 1.15f : fontScaleConfig.Value, 1.0f, 1.5f);
        }

        private string ModeStatusText()
        {
            return AutoAnalyzeEnabled()
                ? "自动模式：会按间隔扫描分析，也可以点击“扫描分析”立即更新。"
                : "手动模式：点击“扫描分析”或按 " + manualAnalysisKey + " 更新。";
        }

        private void ToggleAutoAnalyze()
        {
            if (autoAnalyzeConfig == null)
            {
                return;
            }

            autoAnalyzeConfig.Value = !autoAnalyzeConfig.Value;
            configFile?.Save();
            nextAutoAnalyzeAt = 0f;
            latest.Status = ModeStatusText();
        }

        private void ToggleManualAnalyze()
        {
            if (manualAnalyzeConfig == null)
            {
                return;
            }

            manualAnalyzeConfig.Value = !manualAnalyzeConfig.Value;
            configFile?.Save();
        }

        private void StartAnalysisRequest(string analysisKey, bool silentStatus)
        {
            requestInFlight = true;
            bool includeAiForRequest = nextRequestIncludeAi;
            nextRequestIncludeAi = false;
            string url = BuildAnalysisUrl(includeAiForRequest);
            int timeoutMs = includeAiForRequest ? AiAnalysisTimeoutMilliseconds : AnalysisTimeoutMilliseconds;
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try
                {
                    EnsureHelperServiceStarted();
                    using (TimeoutWebClient client = new TimeoutWebClient(timeoutMs))
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
                            string previousStatus = latest.Status;
                            if (!includeAiForRequest)
                            {
                                parsed.AiAnalysis = latest.AiAnalysis;
                                parsed.AiError = latest.AiError;
                            }
                            if (silentStatus)
                            {
                                parsed.Status = previousStatus;
                            }
                            latest = parsed;
                            lastSuccessfulAnalysisUtc = DateTime.UtcNow;
                            lastSuccessfulAnalysisKey = analysisKey ?? "";
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
                        if (!silentStatus)
                        {
                            latest.Status = AnalysisFailureStatus(ex, includeAiForRequest);
                        }
                    }
                    LogAnalysisFailure(ex);
                }
                finally
                {
                    requestInFlight = false;
                }
            });
        }

        private string AnalysisFailureStatus(Exception ex, bool includeAiForRequest)
        {
            WebException webException = ex as WebException;
            if (webException != null)
            {
                if (webException.Status == WebExceptionStatus.Timeout)
                {
                    return includeAiForRequest
                        ? "智能分析超时，请稍后再试；扫描分析仍可继续。"
                        : "扫描分析超时，请稍后再试。";
                }
                if (webException.Status == WebExceptionStatus.ConnectFailure
                    || webException.Status == WebExceptionStatus.NameResolutionFailure
                    || webException.Status == WebExceptionStatus.ProxyNameResolutionFailure)
                {
                    return "未连接到 BazaarHelper，请先启动助手。";
                }
            }

            if (webException != null)
            {
                string serverMessage = ReadServerErrorMessage(webException);
                if (!string.IsNullOrEmpty(serverMessage))
                {
                    return includeAiForRequest
                        ? "智能分析失败：" + serverMessage
                        : "扫描分析失败：" + serverMessage;
                }
            }

            return includeAiForRequest
                ? "智能分析失败：" + ex.Message
                : "扫描分析失败：" + ex.Message;
        }

        private static string ReadServerErrorMessage(WebException webException)
        {
            try
            {
                HttpWebResponse response = webException.Response as HttpWebResponse;
                if (response == null)
                {
                    return "";
                }
                using (Stream stream = response.GetResponseStream())
                {
                    if (stream == null)
                    {
                        return "";
                    }
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                    {
                        string body = reader.ReadToEnd();
                        string parsed = OverlayAnalysisParser.ParseErrorMessage(body);
                        return string.IsNullOrEmpty(parsed) ? body : parsed;
                    }
                }
            }
            catch
            {
                return "";
            }
        }

        private string BuildAnalysisUrl(bool includeAiForRequest)
        {
            string baseUrl = GetHelperBaseUrl().TrimEnd('/');
            int top = Math.Max(1, topRecommendations == null ? 3 : topRecommendations.Value);
            string ai = includeAiForRequest ? "1" : "0";
            string url = baseUrl + "/api/analysis?top=" + top + "&ai=" + ai;
            if (!string.IsNullOrEmpty(selectedBuildOverride))
            {
                url += "&build=" + Uri.EscapeDataString(selectedBuildOverride);
            }
            return url;
        }

        private string BuildAnalysisRequestKey(string stateSignature, bool includeAiForRequest)
        {
            if (string.IsNullOrEmpty(stateSignature))
            {
                return "";
            }

            int top = Math.Max(1, topRecommendations == null ? 3 : topRecommendations.Value);
            return stateSignature
                + "|build="
                + (selectedBuildOverride ?? "")
                + "|top="
                + top
                + "|ai="
                + (includeAiForRequest ? "1" : "0");
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

            string executablePath = ResolveHelperExecutablePath();
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
                helperProcessStartedByOverlay = Process.Start(startInfo);
                logger?.LogInfo("In-game overlay auto-started BazaarHelper: " + executablePath);
                Thread.Sleep(500);
            }
            catch (Exception ex)
            {
                logger?.LogInfo("In-game overlay failed to auto-start BazaarHelper: " + ex.Message);
            }
        }

        private string ResolveHelperExecutablePath()
        {
            string configuredPath = helperExecutablePath == null ? "" : helperExecutablePath.Value.Trim();
            if (!string.IsNullOrEmpty(configuredPath) && File.Exists(configuredPath))
            {
                return configuredPath;
            }

            string rememberedPath = ReadRememberedHelperExecutablePath();
            if (!string.IsNullOrEmpty(rememberedPath) && File.Exists(rememberedPath))
            {
                logger?.LogInfo(
                    "In-game overlay using remembered BazaarHelper path because configured path is missing: "
                    + rememberedPath);
                return rememberedPath;
            }

            return configuredPath;
        }

        private static string ReadRememberedHelperExecutablePath()
        {
            try
            {
                string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                if (string.IsNullOrEmpty(localAppData))
                {
                    return "";
                }

                string pathFile = Path.Combine(localAppData, "BazaarHelper", "runtime", "helper_path.txt");
                if (!File.Exists(pathFile))
                {
                    return "";
                }

                return File.ReadAllText(pathFile, Encoding.UTF8).Trim();
            }
            catch
            {
                return "";
            }
        }

        private void StopHelperStartedByOverlay()
        {
            Process process = helperProcessStartedByOverlay;
            helperProcessStartedByOverlay = null;
            if (process == null)
            {
                return;
            }

            try
            {
                if (process.HasExited)
                {
                    process.Dispose();
                    return;
                }

                process.Kill();
                process.WaitForExit(2000);
                process.Dispose();
                logger?.LogInfo("In-game overlay stopped BazaarHelper process it auto-started.");
            }
            catch (Exception ex)
            {
                logger?.LogInfo("In-game overlay could not stop auto-started BazaarHelper: " + ex.Message);
                try
                {
                    process.Dispose();
                }
                catch
                {
                    // Ignore cleanup failures during game shutdown.
                }
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

        private void LogUpdateFailure(Exception ex)
        {
            DateTime now = DateTime.UtcNow;
            if ((now - lastUpdateFailureLogUtc).TotalSeconds >= 60)
            {
                lastUpdateFailureLogUtc = now;
                logger?.LogInfo("In-game overlay update request failed: " + ex.Message);
            }
            else
            {
                logger?.LogDebug("In-game overlay update request failed: " + ex.Message);
            }
        }

        private static string EscapeJson(string value)
        {
            if (string.IsNullOrEmpty(value))
            {
                return "";
            }
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"");
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

        private void ParseLockToggleKey()
        {
            string keyName = lockToggleKeyConfig == null ? "F6" : lockToggleKeyConfig.Value;
            if (string.Equals(keyName, parsedLockToggleKeyName, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            parsedLockToggleKeyName = keyName;
            KeyCode parsed;
            if (Enum.TryParse(keyName, true, out parsed))
            {
                lockToggleKey = parsed;
            }
        }

        private void ParseManualAnalysisKey()
        {
            string keyName = manualAnalysisKeyConfig == null ? "F8" : manualAnalysisKeyConfig.Value;
            if (string.Equals(keyName, parsedManualAnalysisKeyName, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            parsedManualAnalysisKeyName = keyName;
            KeyCode parsed;
            if (Enum.TryParse(keyName, true, out parsed))
            {
                manualAnalysisKey = parsed;
            }
        }

        private void ParseAiAnalysisKey()
        {
            string keyName = aiAnalysisKeyConfig == null ? "F5" : aiAnalysisKeyConfig.Value;
            if (string.Equals(keyName, parsedAiAnalysisKeyName, StringComparison.OrdinalIgnoreCase))
            {
                return;
            }

            parsedAiAnalysisKeyName = keyName;
            KeyCode parsed;
            if (Enum.TryParse(keyName, true, out parsed))
            {
                aiAnalysisKey = parsed;
            }
        }

        private void EnsureStyles()
        {
            if (windowStyle != null)
            {
                return;
            }

            float uiScale = Mathf.Clamp(Screen.height / 1080f, 1f, 1.35f) * FontScale();
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
            resizeHandleStyle = new GUIStyle(GUI.skin.box)
            {
                fontSize = mutedFontSize,
                alignment = TextAnchor.MiddleCenter,
                normal = { textColor = Color.white },
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
        public string AiAnalysis;
        public string AiError;
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

    internal sealed class OverlayUpdateState
    {
        public bool UpdateAvailable;
        public bool Dismissed;
        public string Status;
        public string Version;
        public string CurrentVersion;
        public string DownloadUrl;
        public string Error;
        public string Message;
        public string PackagePath;
        public string PackageVersion;
        public readonly List<string> Changelog = new List<string>();
    }

    internal sealed class OverlayCombatSimulation
    {
        public bool HasResult;
        public string Status;
        public string TotalDamage;
        public string DamagePerSecond;
        public string DirectDamage;
        public string TotalShield;
        public string BurnTickDamage;
        public string PoisonTickDamage;
        public string KillTime;
        public int SimulatedCardCount;
        public int SkippedCardCount;

        public static OverlayCombatSimulation Waiting(string status)
        {
            return new OverlayCombatSimulation { Status = status };
        }
    }

    internal sealed class OverlayUpdatePackage
    {
        public string Path;
        public string Version;
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
        public static string ParseErrorMessage(string json)
        {
            return FirstNonEmpty(
                FindStringProperty(json, "ai_error"),
                FindStringProperty(json, "error"));
        }

        public static OverlayCombatSimulation ParseCombatSimulation(string json)
        {
            string error = FindStringProperty(json, "error");
            if (!string.IsNullOrEmpty(error))
            {
                return OverlayCombatSimulation.Waiting(error);
            }

            string combatObject = FindObjectProperty(json, "combat");
            if (string.IsNullOrEmpty(combatObject))
            {
                return OverlayCombatSimulation.Waiting("当前没有可模拟的阵容。");
            }

            OverlayCombatSimulation result = new OverlayCombatSimulation();
            result.HasResult = true;
            result.Status = "估算完成。结果只覆盖当前已支持的伤害、燃烧、毒和触发规则。";
            result.TotalDamage = FormatNumber(FindNumberProperty(combatObject, "total_damage"));
            result.DamagePerSecond = FormatNumber(FindNumberProperty(combatObject, "damage_per_second"));
            result.DirectDamage = FormatNumber(FindNumberProperty(combatObject, "direct_damage"));
            result.TotalShield = FormatNumber(FindNumberProperty(combatObject, "total_shield"));
            result.BurnTickDamage = FormatNumber(FindNumberProperty(combatObject, "total_burn_tick_damage"));
            result.PoisonTickDamage = FormatNumber(FindNumberProperty(combatObject, "total_poison_tick_damage"));
            string killTime = FindNumberProperty(combatObject, "kill_time_sec");
            result.KillTime = string.IsNullOrEmpty(killTime) ? "未击杀" : FormatNumber(killTime) + " 秒";
            result.SimulatedCardCount = ParseInt(FindNumberProperty(combatObject, "simulated_card_count"));
            result.SkippedCardCount = CountJsonObjects(FindArrayProperty(combatObject, "skipped_cards"));
            return result;
        }

        public static OverlayAnalysis Parse(string json)
        {
            OverlayAnalysis result = new OverlayAnalysis();
            result.AiAnalysis = FindStringProperty(json, "ai_analysis");
            result.AiError = FindStringProperty(json, "ai_error");
            string shopObject = FindObjectProperty(json, "build_analysis");
            if (!string.IsNullOrEmpty(shopObject))
            {
                result.ShopAction = FirstNonEmpty(
                    FindStringProperty(shopObject, "shop_action_label"),
                    ShopActionLabel(FindStringProperty(shopObject, "shop_action")));
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
                    RecommendationLabel(FindStringProperty(itemObject, "recommendation")));
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

        public static OverlayUpdateState ParseUpdateStatus(string json)
        {
            OverlayUpdateState result = new OverlayUpdateState();
            result.Status = FindStringProperty(json, "status");
            result.CurrentVersion = FindStringProperty(json, "current_version");
            result.UpdateAvailable = FindBoolProperty(json, "update_available");
            result.Dismissed = FindBoolProperty(json, "dismissed");
            result.Error = FindStringProperty(json, "error");
            string update = FindObjectProperty(json, "update");
            if (!string.IsNullOrEmpty(update))
            {
                result.Version = FindStringProperty(update, "version");
                result.DownloadUrl = FindStringProperty(update, "download_url");
                result.Changelog.AddRange(FindStringArrayProperty(update, "changelog"));
            }
            return result;
        }

        public static OverlayUpdatePackage ParseUpdatePackage(string json)
        {
            OverlayUpdatePackage result = new OverlayUpdatePackage();
            string package = FindObjectProperty(json, "package");
            if (!string.IsNullOrEmpty(package))
            {
                result.Path = FindStringProperty(package, "path");
                result.Version = FindStringProperty(package, "version");
            }
            return result;
        }

        private static string FirstNonEmpty(string first, string second)
        {
            return string.IsNullOrEmpty(first) ? second : first;
        }

        private static List<string> FirstNonEmptyList(List<string> first, List<string> second)
        {
            return first != null && first.Count > 0 ? first : second;
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
                    System.Globalization.CultureInfo.InvariantCulture) + " 金币";
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

                string recommendation = FirstNonEmpty(
                    FindStringProperty(itemObject, "recommendation_type_label"),
                    ShopRecommendationLabel(FindStringProperty(itemObject, "recommendation_type")));
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
                    Importance = FirstNonEmpty(
                        FindStringProperty(itemObject, "importance_label"),
                        ImportanceLabel(FindStringProperty(itemObject, "importance"))),
                    Summary = JoinNonEmpty(
                        " · ",
                        recommendation,
                        JoinNonEmpty(" · ", string.IsNullOrEmpty(price) ? "" : price + " 金币", affordable)),
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
                string buildName = FirstNonEmpty(
                    FindStringProperty(itemObject, "build_display_name"),
                    FindStringProperty(itemObject, "build_name"));
                if (string.IsNullOrEmpty(buildName))
                {
                    continue;
                }

                string phase = FirstNonEmpty(
                    FindStringProperty(itemObject, "build_phase_label"),
                    BuildPhaseLabel(FindStringProperty(itemObject, "build_phase")));
                string role = FirstNonEmpty(
                    FindStringProperty(itemObject, "role_label"),
                    BuildRoleLabel(FindStringProperty(itemObject, "role")));
                string relation = FirstNonEmpty(
                    FindStringProperty(itemObject, "relation_label"),
                    RelationLabel(FindStringProperty(itemObject, "relation")));
                values.Add(
                    buildName
                    + " · "
                    + phase
                    + " · "
                    + role
                    + " · "
                    + relation);
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

        private static string ImportanceLabel(string value)
        {
            if (string.Equals(value, "critical", StringComparison.Ordinal)) return "关键";
            if (string.Equals(value, "high", StringComparison.Ordinal)) return "高";
            if (string.Equals(value, "medium", StringComparison.Ordinal)) return "中";
            if (string.Equals(value, "low", StringComparison.Ordinal)) return "低";
            if (string.Equals(value, "ignored", StringComparison.Ordinal)) return "忽略";
            if (string.Equals(value, "unknown", StringComparison.Ordinal)) return "未知";
            return string.IsNullOrEmpty(value) ? "未知" : value;
        }

        private static string ShopRecommendationLabel(string value)
        {
            if (string.Equals(value, "buy_now", StringComparison.Ordinal)) return "建议购买";
            if (string.Equals(value, "tempo_upgrade", StringComparison.Ordinal)) return "节奏补强";
            if (string.Equals(value, "stash_future", StringComparison.Ordinal)) return "留作后期";
            if (string.Equals(value, "observe", StringComparison.Ordinal)) return "观察";
            if (string.Equals(value, "skip", StringComparison.Ordinal)) return "跳过";
            if (string.Equals(value, "consider_buying_together", StringComparison.Ordinal)) return "可成组购买";
            if (string.Equals(value, "prioritize_best_core", StringComparison.Ordinal)) return "优先最强核心";
            if (string.Equals(value, "unknown", StringComparison.Ordinal)) return "待判断";
            return string.IsNullOrEmpty(value) ? "" : value;
        }

        private static string ShopActionLabel(string value)
        {
            if (string.Equals(value, "buy_visible", StringComparison.Ordinal)) return "购买可见目标";
            if (string.Equals(value, "consider_bundle", StringComparison.Ordinal)) return "考虑组合购买";
            if (string.Equals(value, "skip", StringComparison.Ordinal)) return "跳过刷新";
            if (string.Equals(value, "unknown", StringComparison.Ordinal)) return "暂不强推";
            return string.IsNullOrEmpty(value) ? "" : value;
        }

        private static string RecommendationLabel(string value)
        {
            if (string.Equals(value, "High Value", StringComparison.Ordinal)) return "优先选择";
            if (string.Equals(value, "Medium Value", StringComparison.Ordinal)) return "可以考虑";
            if (string.Equals(value, "Low Value", StringComparison.Ordinal)) return "优先级低";
            return string.IsNullOrEmpty(value) ? "" : value;
        }

        private static string BuildRoleLabel(string value)
        {
            if (string.Equals(value, "core", StringComparison.Ordinal)) return "核心";
            if (string.Equals(value, "optional", StringComparison.Ordinal)) return "可选";
            if (string.Equals(value, "transition", StringComparison.Ordinal)) return "可选";
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
                match.OwnedCore.AddRange(FirstNonEmptyList(
                    FindStringArrayProperty(itemObject, "owned_core_display"),
                    FindStringArrayProperty(itemObject, "owned_core")));
                match.MissingCore.AddRange(FirstNonEmptyList(
                    FindStringArrayProperty(itemObject, "missing_core_display"),
                    FindStringArrayProperty(itemObject, "missing_core")));
                match.OwnedOptional.AddRange(FirstNonEmptyList(
                    FindStringArrayProperty(itemObject, "owned_optional_display"),
                    FindStringArrayProperty(itemObject, "owned_optional")));
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

        private static int CountJsonObjects(string array)
        {
            int count = 0;
            foreach (string ignored in SplitTopLevelObjects(array))
            {
                count++;
            }
            return count;
        }

        private static string FormatNumber(string value)
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
            return Math.Round(parsed, 1).ToString(
                "0.#",
                System.Globalization.CultureInfo.InvariantCulture);
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
