using System;
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
        public const string PluginVersion = "0.8.9";

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
        private ConfigEntry<bool> overlayIncludeAi;
        private ConfigEntry<string> overlayToggleKey;
        private StateProbe probe;
        private Harmony harmony;
        private float nextPollAt;
        private float runtimeCardExportAt;
        private bool runtimeCardExportAttempted;
        private InGameAdvisorOverlay overlay;

        private void Awake()
        {
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
            overlayIncludeAi = Config.Bind(
                "Overlay",
                "IncludeAi",
                false,
                "Request AI analysis for the in-game overlay. Disabled by default to avoid repeated model calls.");
            overlayToggleKey = Config.Bind(
                "Overlay",
                "ToggleKey",
                "F7",
                "Keyboard key used to show or hide the in-game overlay.");
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
                overlayIncludeAi,
                overlayToggleKey);
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

            if (Time.unscaledTime < nextPollAt)
            {
                return;
            }

            nextPollAt = Time.unscaledTime + Math.Max(0.2f, pollIntervalSeconds.Value);

            try
            {
                if (enableVisibleCardScanning.Value)
                {
                    probe.ScanVisibleUiCards();
                }
                if (enableHudResourceScanning.Value || enableUnsafeUiScanning.Value)
                {
                    probe.ScanUiResources();
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
                    return;
                }

                WriteSnapshot(snapshot);
            }
            catch (Exception ex)
            {
                Logger.LogWarning("Failed to export Bazaar state: " + ex);
            }

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

        public static void RequestEventExport()
        {
            EventDrivenExporter.TryExport();
        }

        private void WriteSnapshot(GameStateSnapshot snapshot)
        {
            if (string.IsNullOrEmpty(snapshot.source))
            {
                snapshot.source = "bepinex";
            }
            snapshot.status = null;
            snapshot.message = null;
            snapshot.updated_at_utc = DateTime.UtcNow.ToString("o");
            JsonStateWriter.WriteAtomic(outputPath.Value, snapshot);
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

    internal static class EventDrivenExporter
    {
        private static readonly object SyncRoot = new object();
        private static StateProbe probe;
        private static string outputPath;
        private static ManualLogSource logger;
        private static bool exporting;
        private static int exportCount;
        private static float lastExportAt;
        private const float MinExportIntervalSeconds = 0.2f;

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
                exportCount = 0;
                lastExportAt = 0f;
            }
        }

        public static void TryExport()
        {
            StateProbe currentProbe;
            string currentOutputPath;
            ManualLogSource currentLogger;
            lock (SyncRoot)
            {
                if (exporting || probe == null || string.IsNullOrEmpty(outputPath))
                {
                    return;
                }
                if (Time.unscaledTime - lastExportAt < MinExportIntervalSeconds)
                {
                    return;
                }

                exporting = true;
                lastExportAt = Time.unscaledTime;
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
                JsonStateWriter.WriteAtomic(currentOutputPath, snapshot);
                exportCount++;
                currentLogger?.LogInfo(
                    "Event-driven state export #"
                    + exportCount
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
    }
}
