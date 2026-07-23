# Bazaar State Exporter

Minimal BepInEx plugin shell for exporting The Bazaar runtime state to the Python helper.

The plugin should not run recommendations or AI. Its job is only to write structured game facts to:

```text
%LOCALAPPDATA%\BazaarHelper\runtime\game_state.json
```

## Build

Install a .NET SDK that can build `net472`, then pass your game paths:

```powershell
dotnet build .\bepinex\BazaarStateExporter\BazaarStateExporter.csproj `
  -p:GameRoot="C:\Path\To\The Bazaar" `
  -p:BepInExRoot="C:\Path\To\The Bazaar\BepInEx" `
  -p:GameManagedDir="C:\Path\To\The Bazaar\TheBazaar_Data\Managed"
```

On macOS, the repository root includes a launcher that passes the correct
paths, installs the built DLL, and starts the local API:

```bash
./start_macos.sh
```

Pass `--launch-game` to additionally launch The Bazaar through BepInEx.

The equivalent macOS paths are:

```text
GameRoot=/Users/<user>/Library/Application Support/Steam/steamapps/common/The Bazaar
BepInExRoot=<GameRoot>/BepInEx
GameManagedDir=<GameRoot>/TheBazaar.app/Contents/Resources/Data/Managed
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
OutputPath = C:\Users\<user>\AppData\Local\BazaarHelper\runtime\game_state.json
PollIntervalSeconds = 1
```

The in-game overlay can start the local helper automatically when installed from
the release package:

```ini
[Overlay]
EnableInGameOverlay = true
HelperBaseUrl = http://127.0.0.1:8765
AutoStartHelper = true
HelperExecutablePath = C:\Path\To\BazaarHelper.exe
```

For a smoke test only (never enable this for normal use):

```ini
[Debug]
WritePlaceholderWhenEmpty = true
```

For normal use, keep `WritePlaceholderWhenEmpty = false`; otherwise a temporary
probe failure continuously writes sample data that looks like a frozen screen.

## Next Hook Point

Implement live reading in `StateProbe.TryReadCurrentState()`. Keep all game-specific reflection or Harmony patches in that layer, then return a `GameStateSnapshot`.

The live state hook reads `NetMessageProcessor.ReceiveOrQueue(INetMessage)` so
fresh `NetMessageGameStateSync` DTOs are captured before processor history
caches can go stale.
