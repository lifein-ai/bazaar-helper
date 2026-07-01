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
        public const string PluginVersion = "0.7.9";

        private ConfigEntry<string> outputPath;
        private ConfigEntry<float> pollIntervalSeconds;
        private ConfigEntry<bool> writePlaceholderWhenEmpty;
        private ConfigEntry<bool> enableVisibleCardScanning;
        private ConfigEntry<bool> enableUnsafeUiScanning;
        private StateProbe probe;
        private Harmony harmony;
        private float nextPollAt;

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
            enableUnsafeUiScanning = Config.Bind(
                "Debug",
                "EnableUnsafeUiScanning",
                false,
                "Enable extra global HUD resource scans. Disabled by default because these scans can destabilize the game.");
            probe = new StateProbe(Logger);
            EventDrivenExporter.Initialize(probe, resolvedOutputPath, Logger);
            RuntimeStateCache.Logger = Logger;
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
                if (enableUnsafeUiScanning.Value)
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
                    return;
                }

                WriteSnapshot(snapshot);
            }
            catch (Exception ex)
            {
                Logger.LogWarning("Failed to export Bazaar state: " + ex);
            }

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
            snapshot.updated_at_utc = DateTime.UtcNow.ToString("o");
            JsonStateWriter.WriteAtomic(outputPath.Value, snapshot);
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
