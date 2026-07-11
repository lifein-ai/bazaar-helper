# BazaarHelper Manual Quark Update

BazaarHelper only checks a small remote JSON manifest when the local helper
starts. It does not download the full package from GitHub or any direct file
host. Users open the Quark page, download the zip manually, then choose that zip
in the in-game overlay.

User data lives outside the app folder:

```text
%LOCALAPPDATA%\BazaarHelper\runtime
```

Replacing the app folder keeps runtime state, API keys, game directory records,
logs, update logs, backups, and update downloads under `%LOCALAPPDATA%\BazaarHelper`.

## Release Flow

1. Update the root `VERSION` file.
2. Build the normal release folder:

   ```powershell
   .\package_release.ps1
   ```

3. Compress `release\BazaarHelper` yourself into one zip file, for example:

   ```text
   BazaarHelper-2.3.0.zip
   ```

4. Upload that zip to Quark and copy the Quark share link.
5. Create a manifest template:

   ```powershell
   .\scripts\make_update_package.ps1 -Changelog "新增功能","优化性能","修复问题"
   ```

6. Edit `releases\latest.template.json`: paste the Quark link into
   `download_url`, optionally fill `sha256`, then publish it as `latest.json` to
   the configured remote JSON URL.

Clients read the manifest from either:

```text
BAZAAR_HELPER_UPDATE_MANIFEST_URL=https://example.com/latest.json
```

or an `update_url.txt` file next to `BazaarHelper.exe`.

## Manifest Format

```json
{
  "name": "BazaarHelper",
  "version": "2.3.0",
  "download_url": "https://pan.quark.cn/s/example",
  "sha256": "",
  "changelog": [
    "新增功能",
    "优化性能",
    "修复问题"
  ],
  "force_update": false,
  "published_at": "2026-07-11T00:00:00Z"
}
```

`version` and `download_url` are required. `sha256` is optional but recommended:
when present, the selected zip must match it before installation.

## Install Behavior

The in-game overlay checks update status once when it starts. Closing the prompt
hides it for this helper process. Clicking the download button only opens the
Quark page. Clicking the package button asks the local helper process to open a
Windows file picker.

Before installing, the user should close The Bazaar because the BepInEx plugin
DLL may be locked while the game is running.

Before installation, the helper validates that the zip:

- is a valid zip file;
- has no absolute paths or `..` path traversal entries;
- contains the expected BazaarHelper release files;
- includes a valid `VERSION` or `version.json`;
- is newer than the installed version;
- matches the remote manifest version and sha256 when provided.

The PowerShell updater runs outside `BazaarHelper.exe`, stops the running helper
process, extracts the package to a temporary update directory, backs up the
current app folder, replaces shipped program directories, syncs the BepInEx
plugin if possible, and starts the updated helper.

If the game is still running and the plugin DLL is locked, the main helper update
can still complete. Plugin sync failure is written to the update log; close the
game and run `install_plugin.bat` or rerun the manual updater if needed.

## Manual Repair

The release folder includes:

```text
install_update.bat
update_helper.ps1
```

If an update fails or the overlay cannot complete installation, close the game,
double-click `install_update.bat`, choose the same update zip, and let it
overwrite the program files again. Details are written to:

```text
%LOCALAPPDATA%\BazaarHelper\update.log
```
