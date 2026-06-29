using System;
using System.IO;
using BepInEx;
using BepInEx.Configuration;
using HarmonyLib;
using UnityEngine;

namespace BazaarStateExporter
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class Plugin : BaseUnityPlugin
    {
        public const string PluginGuid = "local.bazaar.stateexporter";
        public const string PluginName = "Bazaar State Exporter";
        public const string PluginVersion = "0.1.0";

        private ConfigEntry<string> outputPath;
        private ConfigEntry<float> pollIntervalSeconds;
        private ConfigEntry<bool> writePlaceholderWhenEmpty;
        private ConfigEntry<bool> enableRuntimeInspection;
        private StateProbe probe;
        private Harmony harmony;
        private float nextPollAt;
        private float nextUiScanAt;
        private float nextCardExportAt;
        private float inspectAt;
        private bool inspected;
        private bool runtimeCardsExported;

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
            enableRuntimeInspection = Config.Bind(
                "Debug",
                "EnableRuntimeInspection",
                true,
                "Log likely The Bazaar runtime objects once after startup. Turn off after StateProbe is wired.");

            probe = new StateProbe(Logger);
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
            inspectAt = Time.unscaledTime + 8.0f;
            nextCardExportAt = Time.unscaledTime + 12.0f;
            Logger.LogInfo(PluginName + " loaded. OutputPath=" + outputPath.Value);
        }

        private void OnDestroy()
        {
            if (harmony != null)
            {
                harmony.UnpatchSelf();
                harmony = null;
            }
        }

        private void Update()
        {
            if (enableRuntimeInspection.Value && !inspected && Time.unscaledTime >= inspectAt)
            {
                inspected = true;
                probe.LogRuntimeHints();
            }

            if (Time.unscaledTime < nextPollAt)
            {
                return;
            }

            nextPollAt = Time.unscaledTime + Math.Max(0.2f, pollIntervalSeconds.Value);
            if (Time.unscaledTime >= nextUiScanAt)
            {
                nextUiScanAt = Time.unscaledTime + 0.5f;
                probe.ScanVisibleUiCards();
            }

            try
            {
                GameStateSnapshot snapshot = probe.TryReadCurrentState();
                if (snapshot == null && writePlaceholderWhenEmpty.Value)
                {
                    snapshot = GameStateSnapshot.CreatePlaceholder();
                }

                if (snapshot == null)
                {
                    return;
                }

                if (string.IsNullOrEmpty(snapshot.source))
                {
                    snapshot.source = "bepinex";
                }
                snapshot.updated_at_utc = DateTime.UtcNow.ToString("o");
                JsonStateWriter.WriteAtomic(outputPath.Value, snapshot);
            }
            catch (Exception ex)
            {
                Logger.LogWarning("Failed to export Bazaar state: " + ex);
            }

            if (!runtimeCardsExported && Time.unscaledTime >= nextCardExportAt)
            {
                nextCardExportAt = Time.unscaledTime + 60.0f;
                try
                {
                    RuntimeCardExportResult result =
                        RuntimeCardExporter.TryExportLatestCards(outputPath.Value, Logger);
                    runtimeCardsExported = result != null && result.ExportedCardCount > 0;
                    if (runtimeCardsExported)
                    {
                        Logger.LogInfo(
                            "Runtime card library exported successfully; no further exports will run this session.");
                    }
                }
                catch (Exception ex)
                {
                    Logger.LogWarning("Failed to export runtime cards: " + ex);
                }
            }
        }
    }
}
