# Bazaar State Exporter

Minimal BepInEx plugin shell for exporting The Bazaar runtime state to the Python helper.

The plugin should not run recommendations or AI. Its job is only to write structured game facts to:

```text
D:\bazzarhelp\runtime\game_state.json
```

## Build

Install a .NET SDK that can build `net472`, then pass your game paths:

```powershell
dotnet build .\bepinex\BazaarStateExporter\BazaarStateExporter.csproj `
  -p:GameRoot="C:\Path\To\The Bazaar" `
  -p:BepInExRoot="C:\Path\To\The Bazaar\BepInEx" `
  -p:GameManagedDir="C:\Path\To\The Bazaar\TheBazaar_Data\Managed"
```

Copy the built DLL to:

```text
<The Bazaar>\BepInEx\plugins\BazaarStateExporter\BazaarStateExporter.dll
```

After first launch, edit:

```text
<The Bazaar>\BepInEx\config\local.bazaar.stateexporter.cfg
```

Set:

```ini
[Export]
OutputPath = D:\bazzarhelp\runtime\game_state.json
PollIntervalSeconds = 1
```

For a smoke test before live probing is implemented:

```ini
[Debug]
WritePlaceholderWhenEmpty = true
EnableRuntimeInspection = true
```

## Next Hook Point

Implement live reading in `StateProbe.TryReadCurrentState()`. Keep all game-specific reflection or Harmony patches in that layer, then return a `GameStateSnapshot`.

When `EnableRuntimeInspection` is enabled, the plugin logs likely runtime objects and members once after startup. Use `BepInEx/LogOutput.log` to identify the object that owns the current run/session/shop state, then turn the option off.
