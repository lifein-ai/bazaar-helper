using System;
using System.Collections.Generic;
using System.Text;
using System.IO;
using BepInEx;
using BepInEx.Configuration;
using BepInEx.Logging;
using HarmonyLib;
using UnityEngine;

namespace BazaarStateExporter
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class Plugin : BaseUnityPlugin
    {
        public const string PluginGuid = "local.bazaar.stateexporter";
        public const string PluginName = "Bazaar State Exporter";
        public const string PluginVersion = "0.9.0";

        private ConfigEntry<string> outputPath;
        private ConfigEntry<float> pollIntervalSeconds;
        private ConfigEntry<bool> writePlaceholderWhenEmpty;
        private ConfigEntry<bool> enableVisibleCardScanning;
        private ConfigEntry<bool> enableHudResourceScanning;
        private ConfigEntry<bool> enableUnsafeUiScanning;
        private ConfigEntry<bool> enableRuntimeCardExport;
        private ConfigEntry<float> runtimeCardExportDelaySeconds;
        private ConfigEntry<bool> enableInGameOverlay;
        private ConfigEntry<string> helperBaseUrl;
        private ConfigEntry<bool> autoStartHelper;
        private ConfigEntry<string> helperExecutablePath;
        private ConfigEntry<float> overlayPollIntervalSeconds;
        private ConfigEntry<int> overlayTopRecommendations;
        private ConfigEntry<bool> overlayManualAnalyze;
        private ConfigEntry<bool> overlayIncludeAi;
        private ConfigEntry<bool> overlayAutoAnalyze;
        private ConfigEntry<float> overlayFontScale;
        private ConfigEntry<string> overlayToggleKey;
        private ConfigEntry<string> overlayLockToggleKey;
        private ConfigEntry<string> overlayManualAnalysisKey;
        private ConfigEntry<string> overlayAiAnalysisKey;
        private ConfigEntry<bool> overlayLocked;
        private ConfigEntry<float> overlayX;
        private ConfigEntry<float> overlayY;
        private ConfigEntry<float> overlayWidth;
        private ConfigEntry<float> overlayHeight;
        private ConfigEntry<float> buildOverlayX;
        private ConfigEntry<float> buildOverlayY;
        private ConfigEntry<float> buildOverlayWidth;
        private ConfigEntry<float> buildOverlayHeight;
        private StateProbe probe;
        private Harmony harmony;
        private float runtimeCardExportAt;
        private bool runtimeCardExportAttempted;
        private InGameAdvisorOverlay overlay;
        private int manualExportCount;
        private string lastManualStateSignature;
        private float lastManualWriteAt;
        private float lastVisibleCardScanAt = -999f;
        private float lastUiResourceScanAt = -999f;
        private static Plugin instance;
        private const float AutoVisibleCardFullScanIntervalSeconds = 6.0f;
        private const float AutoUiResourceScanIntervalSeconds = 1.5f;
        private const float AutoUnchangedHeartbeatSeconds = 10.0f;

        private void Awake()
        {
            instance = this;
            string defaultOutputPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "BazaarHelper",
                "runtime",
                "game_state.json");

            outputPath = Config.Bind(
                "Export",
                "OutputPath",
                defaultOutputPath,
                "Absolute path to the shared JSON file consumed by BazaarHelper.");
            string resolvedOutputPath = ResolveOutputPath(outputPath.Value, defaultOutputPath);
            if (!string.Equals(outputPath.Value, resolvedOutputPath, StringComparison.Ordinal))
            {
                outputPath.Value = resolvedOutputPath;
                Config.Save();
            }
            pollIntervalSeconds = Config.Bind(
                "Export",
                "PollIntervalSeconds",
                1.0f,
                "How often to scan game state and write JSON.");
            writePlaceholderWhenEmpty = Config.Bind(
                "Debug",
                "WritePlaceholderWhenEmpty",
                false,
                "Write a sample Vanessa state if the live probe has not been implemented or cannot find game objects.");
            enableVisibleCardScanning = Config.Bind(
                "Export",
                "EnableVisibleCardScanning",
                true,
                "Automatically scan visible CardController objects so event/shop screens update without mouse hover.");
            enableHudResourceScanning = Config.Bind(
                "Export",
                "EnableHudResourceScanning",
                true,
                "Read visible HUD resources such as gold and health so purchases update even when cached game state lags.");
            enableUnsafeUiScanning = Config.Bind(
                "Debug",
                "EnableUnsafeUiScanning",
                false,
                "Enable extra global HUD resource scans. Disabled by default because these scans can destabilize the game.");
            enableRuntimeCardExport = Config.Bind(
                "Debug",
                "EnableRuntimeCardExport",
                false,
                "Export the game's loaded card and encounter catalog once to live_cards_raw.json for data refreshes.");
            runtimeCardExportDelaySeconds = Config.Bind(
                "Debug",
                "RuntimeCardExportDelaySeconds",
                8.0f,
                "Seconds to wait after plugin startup before attempting the one-shot runtime card export.");
            runtimeCardExportAt = Time.unscaledTime + Math.Max(0.5f, runtimeCardExportDelaySeconds.Value);
            enableInGameOverlay = Config.Bind(
                "Overlay",
                "EnableInGameOverlay",
                true,
                "Show a small in-game recommendation overlay fed by the local BazaarHelper web service.");
            helperBaseUrl = Config.Bind(
                "Overlay",
                "HelperBaseUrl",
                "http://127.0.0.1:8765",
                "Base URL of the local BazaarHelper web service.");
            autoStartHelper = Config.Bind(
                "Overlay",
                "AutoStartHelper",
                true,
                "Automatically start BazaarHelper.exe when the in-game overlay cannot reach the local service.");
            helperExecutablePath = Config.Bind(
                "Overlay",
                "HelperExecutablePath",
                "",
                "Absolute path to BazaarHelper.exe. The installer writes this so the game can start the helper automatically.");
            overlayPollIntervalSeconds = Config.Bind(
                "Overlay",
                "PollIntervalSeconds",
                2.0f,
                "How often the in-game overlay refreshes recommendations.");
            overlayTopRecommendations = Config.Bind(
                "Overlay",
                "TopRecommendations",
                3,
                "How many event recommendations the in-game overlay requests.");
            overlayManualAnalyze = Config.Bind(
                "Overlay",
                "ManualAnalyze",
                true,
                "Show manual scan and AI analysis controls in the in-game overlay.");
            overlayIncludeAi = Config.Bind(
                "Overlay",
                "IncludeAi",
                false,
                "Request AI analysis for the in-game overlay. Disabled by default to avoid repeated model calls.");
            overlayAutoAnalyze = Config.Bind(
                "Overlay",
                "AutoAnalyze",
                false,
                "Automatically scan game state and refresh in-game recommendations at Overlay.PollIntervalSeconds. Disabled by default for lower overhead.");
            overlayFontScale = Config.Bind(
                "Overlay",
                "FontScale",
                1.15f,
                "Font scale for the in-game overlay. Common values: 1.0, 1.15, 1.3, 1.5.");
            overlayToggleKey = Config.Bind(
                "Overlay",
                "ToggleKey",
                "F7",
                "Keyboard key used to show or hide the in-game overlay.");
            overlayLockToggleKey = Config.Bind(
                "Overlay",
                "LockToggleKey",
                "F6",
                "Keyboard key used to lock or unlock overlay movement and resizing.");
            overlayManualAnalysisKey = Config.Bind(
                "Overlay",
                "ManualAnalysisKey",
                "F8",
                "Keyboard key used to manually scan game state and refresh in-game recommendations.");
            overlayAiAnalysisKey = Config.Bind(
                "Overlay",
                "AiAnalysisKey",
                "F5",
                "Keyboard key used to request one AI build analysis in the in-game overlay.");
            overlayLocked = Config.Bind(
                "Overlay",
                "Locked",
                true,
                "When false, overlay windows can be dragged and resized in game.");
            overlayX = Config.Bind(
                "Overlay",
                "RecommendationX",
                16f,
                "Recommendation window left position in screen pixels.");
            overlayY = Config.Bind(
                "Overlay",
                "RecommendationY",
                56f,
                "Recommendation window top position in screen pixels.");
            overlayWidth = Config.Bind(
                "Overlay",
                "RecommendationWidth",
                500f,
                "Recommendation window width in screen pixels.");
            overlayHeight = Config.Bind(
                "Overlay",
                "RecommendationHeight",
                620f,
                "Recommendation window height in screen pixels.");
            buildOverlayX = Config.Bind(
                "Overlay",
                "BuildX",
                532f,
                "Build window left position in screen pixels.");
            buildOverlayY = Config.Bind(
                "Overlay",
                "BuildY",
                56f,
                "Build window top position in screen pixels.");
            buildOverlayWidth = Config.Bind(
                "Overlay",
                "BuildWidth",
                400f,
                "Build window width in screen pixels.");
            buildOverlayHeight = Config.Bind(
                "Overlay",
                "BuildHeight",
                620f,
                "Build window height in screen pixels.");
            if (string.Equals(overlayToggleKey.Value, "F8", StringComparison.OrdinalIgnoreCase))
            {
                overlayToggleKey.Value = "F7";
                Config.Save();
            }
            probe = new StateProbe(Logger);
            EventDrivenExporter.Initialize(probe, resolvedOutputPath, Logger);
            RuntimeStateCache.Logger = Logger;
            GameObject overlayObject = new GameObject("BazaarHelperInGameOverlay");
            DontDestroyOnLoad(overlayObject);
            overlay = overlayObject.AddComponent<InGameAdvisorOverlay>();
            overlay.Initialize(
                Logger,
                enableInGameOverlay,
                helperBaseUrl,
                autoStartHelper,
                helperExecutablePath,
                overlayPollIntervalSeconds,
                overlayTopRecommendations,
                overlayManualAnalyze,
                overlayIncludeAi,
                overlayAutoAnalyze,
                overlayFontScale,
                overlayToggleKey,
                overlayLockToggleKey,
                overlayManualAnalysisKey,
                overlayAiAnalysisKey,
                overlayLocked,
                overlayX,
                overlayY,
                overlayWidth,
                overlayHeight,
                buildOverlayX,
                buildOverlayY,
                buildOverlayWidth,
                buildOverlayHeight,
                Config);
            try
            {
                harmony = new Harmony(PluginGuid);
                harmony.PatchAll(typeof(Plugin).Assembly);
                Logger.LogInfo("Harmony patches applied.");
            }
            catch (Exception ex)
            {
                Logger.LogWarning("Failed to apply Harmony patches: " + ex);
            }
            Logger.LogInfo(
                PluginName
                + " "
                + PluginVersion
                + " loaded with event-driven export. OutputPath="
                + resolvedOutputPath);
            WriteStatusSnapshot(
                "waiting_for_game_state",
                "Plugin loaded and output path is writable. Waiting for live run state.");
        }

        private string ResolveOutputPath(string configuredPath, string defaultOutputPath)
        {
            string candidate = string.IsNullOrWhiteSpace(configuredPath)
                ? defaultOutputPath
                : configuredPath;
            try
            {
                string fullPath = Path.GetFullPath(
                    Environment.ExpandEnvironmentVariables(candidate));
                string directory = Path.GetDirectoryName(fullPath);
                if (string.IsNullOrEmpty(directory))
                {
                    throw new IOException("OutputPath has no parent directory.");
                }
                Directory.CreateDirectory(directory);
                return fullPath;
            }
            catch (Exception ex)
            {
                Logger.LogWarning(
                    "Configured OutputPath is invalid or unavailable: "
                    + candidate
                    + ". Falling back to "
                    + defaultOutputPath
                    + ". Error: "
                    + ex.Message);
                Directory.CreateDirectory(Path.GetDirectoryName(defaultOutputPath));
                return defaultOutputPath;
            }
        }

        private void OnDestroy()
        {
            if (ReferenceEquals(instance, this))
            {
                instance = null;
            }
            Logger.LogWarning("Exporter Unity component was destroyed; Harmony event export remains available.");
        }

        private void Update()
        {
            try
            {
                UpdateExporter();
            }
            catch (Exception ex)
            {
                // No optional probe operation may permanently stop the Unity update loop.
                Logger.LogWarning("Unexpected exporter update failure: " + ex);
            }
        }

        private void UpdateExporter()
        {
            TryRuntimeCardExportOnce();
            EventDrivenExporter.FlushIfDue();
        }

        private void TryRuntimeCardExportOnce()
        {
            if (runtimeCardExportAttempted || !enableRuntimeCardExport.Value)
            {
                return;
            }
            if (Time.unscaledTime < runtimeCardExportAt)
            {
                return;
            }

            runtimeCardExportAttempted = true;
            RuntimeCardExportResult result = RuntimeCardExporter.TryExportLatestCards(outputPath.Value, Logger);
            Logger.LogInfo(
                "Runtime card export attempt completed. exported="
                + result.ExportedCardCount
                + " output="
                + result.OutputPath);
        }

        public static void RequestEventExport(string reason = "runtime_event")
        {
            EventDrivenExporter.MarkDirty(reason);
        }

        public static ManualExportResult RequestManualExport()
        {
            Plugin current = instance;
            if (current == null)
            {
                return ManualExportResult.NotAvailable("plugin_not_loaded");
            }
            return current.ExportCurrentStateOnce("manual_overlay", false);
        }

        public static ManualExportResult RequestAutomaticExport()
        {
            Plugin current = instance;
            if (current == null)
            {
                return ManualExportResult.NotAvailable("plugin_not_loaded");
            }
            return current.ExportCurrentStateOnce("auto_overlay", true);
        }

        private ManualExportResult ExportCurrentStateOnce(string reason, bool automatic)
        {
            try
            {
                float now = Time.unscaledTime;
                bool scanVisibleCards = enableVisibleCardScanning.Value
                    && (!automatic
                        || now - lastVisibleCardScanAt >= AutoVisibleCardFullScanIntervalSeconds);
                if (scanVisibleCards)
                {
                    probe.ScanVisibleUiCards();
                    lastVisibleCardScanAt = now;
                }

                bool scanUiResources =
                    (enableHudResourceScanning.Value || enableUnsafeUiScanning.Value)
                    && (!automatic
                        || now - lastUiResourceScanAt >= AutoUiResourceScanIntervalSeconds);
                if (scanUiResources)
                {
                    probe.ScanUiResources();
                    lastUiResourceScanAt = now;
                }

                GameStateSnapshot snapshot = probe.TryReadCurrentState();
                if (snapshot == null && writePlaceholderWhenEmpty.Value)
                {
                    snapshot = GameStateSnapshot.CreatePlaceholder();
                }

                if (snapshot == null)
                {
                    WriteStatusSnapshot(
                        "waiting_for_game_state",
                        "Plugin is loaded, but no NetMessageGameStateSync has been captured yet.");
                    return ManualExportResult.NotAvailable("waiting_for_game_state");
                }

                PrepareSnapshotForExport(snapshot, reason, manualExportCount + 1);
                bool stateChanged = !string.Equals(
                    snapshot.state_signature,
                    lastManualStateSignature,
                    StringComparison.Ordinal);
                bool heartbeatDue =
                    now - lastManualWriteAt >= AutoUnchangedHeartbeatSeconds;
                bool shouldWrite = !automatic || stateChanged || heartbeatDue;
                if (shouldWrite)
                {
                    manualExportCount++;
                    SnapshotExportMetadata.Apply(snapshot, reason, manualExportCount);
                    JsonStateWriter.WriteAtomic(outputPath.Value, snapshot);
                    lastManualStateSignature = snapshot.state_signature;
                    lastManualWriteAt = now;
                    Logger.LogInfo(
                        (automatic ? "Automatic" : "Manual")
                        + " Bazaar state export completed. changed="
                        + stateChanged);
                }
                else
                {
                    Logger.LogDebug(
                        "Skipped automatic overlay export because state signature did not change.");
                }

                return new ManualExportResult
                {
                    SnapshotAvailable = true,
                    StateChanged = stateChanged,
                    WroteSnapshot = shouldWrite,
                    StateSignature = snapshot.state_signature,
                    Message = stateChanged ? "changed" : "unchanged",
                };
            }
            catch (Exception ex)
            {
                Logger.LogWarning("Manual Bazaar state export failed: " + ex);
                return ManualExportResult.NotAvailable("export_failed");
            }
        }

        private void WriteSnapshot(GameStateSnapshot snapshot, string reason)
        {
            PrepareSnapshotForExport(snapshot, reason, manualExportCount + 1);
            manualExportCount++;
            SnapshotExportMetadata.Apply(snapshot, reason, manualExportCount);
            JsonStateWriter.WriteAtomic(outputPath.Value, snapshot);
            lastManualStateSignature = snapshot.state_signature;
            lastManualWriteAt = Time.unscaledTime;
        }

        private void PrepareSnapshotForExport(
            GameStateSnapshot snapshot,
            string reason,
            int exportCount)
        {
            if (string.IsNullOrEmpty(snapshot.source))
            {
                snapshot.source = "bepinex";
            }
            snapshot.status = null;
            snapshot.message = null;
            snapshot.updated_at_utc = DateTime.UtcNow.ToString("o");
            SnapshotExportMetadata.Apply(snapshot, reason, exportCount);
        }

        private void WriteStatusSnapshot(string status, string message)
        {
            try
            {
                GameStateSnapshot snapshot = GameStateSnapshot.CreateWaitingForGameState();
                snapshot.status = status;
                snapshot.message = message;
                snapshot.updated_at_utc = DateTime.UtcNow.ToString("o");
                JsonStateWriter.WriteAtomic(outputPath.Value, snapshot);
            }
            catch (Exception ex)
            {
                Logger.LogWarning("Failed to write exporter status snapshot: " + ex);
            }
        }
    }

    public sealed class ManualExportResult
    {
        public bool SnapshotAvailable;
        public bool StateChanged;
        public bool WroteSnapshot;
        public string StateSignature;
        public string Message;

        public static ManualExportResult NotAvailable(string message)
        {
            return new ManualExportResult
            {
                SnapshotAvailable = false,
                StateChanged = false,
                WroteSnapshot = false,
                StateSignature = "",
                Message = message,
            };
        }
    }

    internal static class SnapshotExportMetadata
    {
        public static void Apply(GameStateSnapshot snapshot, string reason, int exportCount)
        {
            ExportDebugSnapshot previousDebug = snapshot.debug;
            UiScanDebugSnapshot uiScanDebug = RuntimeStateCache.GetLastUiScanDebug();
            snapshot.last_export_reason = string.IsNullOrEmpty(reason)
                ? "unknown"
                : reason;
            snapshot.state_signature = BuildStateSignature(snapshot);
            snapshot.debug = new ExportDebugSnapshot
            {
                export_count = exportCount,
                screen_mode = RuntimeStateCache.CurrentScreenMode,
                event_option_count = snapshot.event_option_ids == null ? 0 : snapshot.event_option_ids.Count,
                visible_card_count = snapshot.visible_cards == null ? 0 : snapshot.visible_cards.Count,
                owned_card_count = snapshot.owned_cards == null ? 0 : snapshot.owned_cards.Count,
                shop_item_count = snapshot.current_shop == null || snapshot.current_shop.visible_items == null
                    ? 0
                    : snapshot.current_shop.visible_items.Count,
                reward_option_count = snapshot.current_reward_options == null
                    ? 0
                    : snapshot.current_reward_options.Count,
                dto_source = RuntimeStateCache.LatestGameStateSource,
                dto_summary = RuntimeStateCache.LatestGameStateSummary,
                card_controller_total = uiScanDebug.card_controller_total,
                active_card_controller_count = uiScanDebug.active_card_controller_count,
                ui_snapshot_success_count = uiScanDebug.ui_snapshot_success_count,
                ui_snapshot_failed_count = uiScanDebug.ui_snapshot_failed_count,
                captured_cards = uiScanDebug.captured_cards,
                dto_day = previousDebug == null ? null : previousDebug.dto_day,
                ui_day = previousDebug == null ? null : previousDebug.ui_day,
                dto_selection_count = previousDebug == null ? 0 : previousDebug.dto_selection_count,
                event_source = previousDebug == null ? null : previousDebug.event_source,
                scene_guess = previousDebug == null ? null : previousDebug.scene_guess,
            };
        }

        public static string BuildStateSignature(GameStateSnapshot snapshot)
        {
            StringBuilder builder = new StringBuilder();
            AppendPart(builder, "v3");
            AppendPart(builder, snapshot.hero);
            AppendPart(builder, snapshot.day.ToString());
            AppendPart(builder, RuntimeStateCache.CurrentScreenMode);
            AppendPart(builder, snapshot.gold.HasValue ? snapshot.gold.Value.ToString() : "");
            AppendPart(builder, snapshot.health.HasValue ? snapshot.health.Value.ToString() : "");
            AppendPart(builder, snapshot.monster_health.HasValue ? snapshot.monster_health.Value.ToString() : "");
            AppendStrings(builder, snapshot.event_option_ids);
            AppendStrings(builder, snapshot.event_option_template_ids);
            AppendEventOptions(builder, snapshot.event_options_detailed);
            AppendEventOptions(builder, snapshot.current_events);
            AppendCards(builder, snapshot.owned_cards);
            AppendCards(builder, snapshot.monster_items);
            AppendCards(builder, snapshot.monster_skills);
            AppendCards(builder, snapshot.visible_cards);
            AppendCards(
                builder,
                snapshot.current_shop == null ? null : snapshot.current_shop.visible_items);
            AppendCards(builder, snapshot.current_reward_options);
            if (snapshot.current_shop != null)
            {
                AppendPart(
                    builder,
                    snapshot.current_shop.refresh_available.HasValue
                        ? snapshot.current_shop.refresh_available.Value.ToString()
                        : "");
                AppendPart(
                    builder,
                    snapshot.current_shop.refresh_cost.HasValue
                        ? snapshot.current_shop.refresh_cost.Value.ToString()
                        : "");
                AppendPart(
                    builder,
                    snapshot.current_shop.refreshes_remaining.HasValue
                        ? snapshot.current_shop.refreshes_remaining.Value.ToString()
                        : "");
            }
            return builder.ToString();
        }

        private static void AppendStrings(StringBuilder builder, List<string> values)
        {
            if (values == null)
            {
                AppendPart(builder, "");
                return;
            }

            for (int i = 0; i < values.Count; i++)
            {
                AppendPart(builder, values[i]);
            }
        }

        private static void AppendCards(StringBuilder builder, List<CardSnapshot> cards)
        {
            if (cards == null)
            {
                AppendPart(builder, "");
                return;
            }

            for (int i = 0; i < cards.Count; i++)
            {
                CardSnapshot card = cards[i];
                if (card == null)
                {
                    AppendPart(builder, "");
                    continue;
                }

                AppendPart(
                    builder,
                    (card.id ?? "")
                    + ":"
                    + (card.template_id ?? "")
                    + ":"
                    + (card.name ?? "")
                    + ":"
                    + (card.rarity ?? "")
                    + ":"
                    + (card.section ?? "")
                    + ":"
                    + (card.price.HasValue ? card.price.Value.ToString() : ""));
            }
        }

        private static void AppendEventOptions(StringBuilder builder, List<EventOptionSnapshot> options)
        {
            if (options == null)
            {
                AppendPart(builder, "");
                return;
            }

            for (int i = 0; i < options.Count; i++)
            {
                EventOptionSnapshot option = options[i];
                if (option == null)
                {
                    AppendPart(builder, "");
                    continue;
                }

                AppendPart(
                    builder,
                    (option.id ?? "")
                    + ":"
                    + (option.template_id ?? "")
                    + ":"
                    + (option.kind ?? "")
                    + ":"
                    + (option.card_type ?? ""));

                if (option.branches == null)
                {
                    continue;
                }

                for (int branchIndex = 0; branchIndex < option.branches.Count; branchIndex++)
                {
                    EventOptionBranchSnapshot branch = option.branches[branchIndex];
                    if (branch == null)
                    {
                        AppendPart(builder, "");
                        continue;
                    }

                    AppendPart(
                        builder,
                        (branch.template_id ?? "")
                        + ":"
                        + (branch.kind ?? "")
                        + ":"
                        + (branch.card_type ?? ""));
                }
            }
        }

        private static void AppendPart(StringBuilder builder, string value)
        {
            builder.Append('|');
            if (value == null)
            {
                return;
            }
            builder.Append(value.Replace("|", "%7C"));
        }
    }

    internal static class EventDrivenExporter
    {
        private static readonly object SyncRoot = new object();
        private static StateProbe probe;
        private static string outputPath;
        private static ManualLogSource logger;
        private static bool exporting;
        private static bool dirty;
        private static string dirtyReason;
        private static float exportDueAt;
        private static string lastStateSignature;
        private static int exportCount;
        private static float lastExportAt;
        private const float DebounceSeconds = 0.35f;
        private const float MinExportIntervalSeconds = 0.5f;

        public static void Initialize(
            StateProbe stateProbe,
            string stateOutputPath,
            ManualLogSource log)
        {
            lock (SyncRoot)
            {
                probe = stateProbe;
                outputPath = stateOutputPath;
                logger = log;
                exporting = false;
                dirty = false;
                dirtyReason = null;
                exportDueAt = 0f;
                lastStateSignature = null;
                exportCount = 0;
                lastExportAt = 0f;
            }
        }

        public static void MarkDirty(string reason)
        {
            lock (SyncRoot)
            {
                dirty = true;
                dirtyReason = CombineReasons(dirtyReason, reason);
                exportDueAt = Time.unscaledTime + DebounceSeconds;
            }
        }

        public static void FlushIfDue()
        {
            StateProbe currentProbe;
            string currentOutputPath;
            ManualLogSource currentLogger;
            string currentReason;
            float now = Time.unscaledTime;
            lock (SyncRoot)
            {
                if (!dirty || exporting || probe == null || string.IsNullOrEmpty(outputPath))
                {
                    return;
                }
                if (now < exportDueAt)
                {
                    return;
                }
                if (now - lastExportAt < MinExportIntervalSeconds)
                {
                    exportDueAt = lastExportAt + MinExportIntervalSeconds;
                    return;
                }

                exporting = true;
                dirty = false;
                currentReason = dirtyReason ?? "runtime_event";
                dirtyReason = null;
                lastExportAt = now;
                currentProbe = probe;
                currentOutputPath = outputPath;
                currentLogger = logger;
            }

            try
            {
                GameStateSnapshot snapshot = currentProbe.TryReadCachedState();
                if (snapshot == null)
                {
                    return;
                }

                snapshot.source = "bepinex";
                snapshot.updated_at_utc = DateTime.UtcNow.ToString("o");
                SnapshotExportMetadata.Apply(snapshot, currentReason, exportCount + 1);
                if (string.Equals(
                    snapshot.state_signature,
                    lastStateSignature,
                    StringComparison.Ordinal))
                {
                    currentLogger?.LogDebug(
                        "Skipped event-driven export because state signature did not change. reason="
                        + currentReason);
                    return;
                }

                JsonStateWriter.WriteAtomic(currentOutputPath, snapshot);
                exportCount++;
                lastStateSignature = snapshot.state_signature;
                currentLogger?.LogInfo(
                    "Event-driven state export #"
                    + exportCount
                    + " reason="
                    + currentReason
                    + " day="
                    + snapshot.day
                    + " options="
                    + snapshot.event_option_ids.Count
                    + " owned="
                    + snapshot.owned_cards.Count);
            }
            catch (Exception ex)
            {
                currentLogger?.LogWarning("Event-driven state export failed: " + ex);
            }
            finally
            {
                lock (SyncRoot)
                {
                    exporting = false;
                }
            }
        }

        private static string CombineReasons(string existing, string incoming)
        {
            if (string.IsNullOrEmpty(incoming))
            {
                return string.IsNullOrEmpty(existing) ? "runtime_event" : existing;
            }
            if (string.IsNullOrEmpty(existing))
            {
                return incoming;
            }
            if (existing.IndexOf(incoming, StringComparison.OrdinalIgnoreCase) >= 0)
            {
                return existing;
            }
            return existing + "," + incoming;
        }
    }
}
