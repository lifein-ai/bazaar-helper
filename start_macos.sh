#!/bin/sh
# Build/install the BepInEx plugin and start the local helper API.
set -eu

usage() {
    cat <<'EOF'
Usage: ./start_macos.sh [--launch-game]

Environment overrides:
  BAZAAR_GAME_ROOT        The Bazaar Steam directory.
  BAZAAR_HELPER_DATA_DIR  Directory containing game_state.json and helper logs.
  BAZAAR_PYTHON           Python 3.10+ executable.
  DOTNET_BIN              dotnet executable used to build the BepInEx plugin.

By default, the script prepares the plugin and API service without opening the game.
--launch-game additionally opens The Bazaar through the BepInEx launcher.
EOF
}

launch_game=0
case "${1:-}" in
    "")
        ;;
    --launch-game)
        launch_game=1
        ;;
    -h|--help)
        usage
        exit 0
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
GAME_ROOT=${BAZAAR_GAME_ROOT:-"$HOME/Library/Application Support/Steam/steamapps/common/The Bazaar"}
GAME_APP="$GAME_ROOT/TheBazaar.app"
BEPINEX_ROOT="$GAME_ROOT/BepInEx"
MANAGED_DIR="$GAME_APP/Contents/Resources/Data/Managed"
PLUGIN_PROJECT="$SCRIPT_DIR/bepinex/BazaarStateExporter/BazaarStateExporter.csproj"
PLUGIN_OUTPUT="$SCRIPT_DIR/bepinex/BazaarStateExporter/bin/Release/net472/BazaarStateExporter.dll"
PLUGIN_DIR="$BEPINEX_ROOT/plugins/BazaarStateExporter"
PLUGIN_CONFIG="$BEPINEX_ROOT/config/local.bazaar.stateexporter.cfg"
RUNTIME_DIR=${BAZAAR_HELPER_DATA_DIR:-"$HOME/.bazaar_helper/runtime"}
STATE_PATH="$RUNTIME_DIR/game_state.json"
HELPER_LOG="$RUNTIME_DIR/bazaarhelper.log"
HELPER_PID="$RUNTIME_DIR/bazaarhelper.pid"
HOST=127.0.0.1
PORT=${BAZAAR_HELPER_PORT:-8765}

fail() {
    printf '%s\n' "start_macos.sh: $*" >&2
    exit 1
}

find_python() {
    if [ -n "${BAZAAR_PYTHON:-}" ]; then
        printf '%s\n' "$BAZAAR_PYTHON"
        return
    fi
    for candidate in python3.11 python3.12 python3.13 python3.14 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s\n' "$(command -v "$candidate")"
            return
        fi
    done
    return 1
}

PYTHON_BIN=$(find_python) || fail "Python 3.10+ is required. Install it with: brew install python@3.11"
"$PYTHON_BIN" - <<'PY' || fail "Python 3.10+ is required. Set BAZAAR_PYTHON to a compatible executable."
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY

[ -d "$GAME_APP" ] || fail "Game app not found: $GAME_APP"
[ -d "$BEPINEX_ROOT" ] || fail "BepInEx not found: $BEPINEX_ROOT"
[ -d "$MANAGED_DIR" ] || fail "Unity Managed directory not found: $MANAGED_DIR"
[ -f "$GAME_ROOT/run_bepinex.sh" ] || fail "BepInEx launcher not found: $GAME_ROOT/run_bepinex.sh"

if [ -n "${DOTNET_BIN:-}" ]; then
    dotnet_bin=$DOTNET_BIN
elif command -v dotnet >/dev/null 2>&1; then
    dotnet_bin=$(command -v dotnet)
elif [ -x /opt/homebrew/opt/dotnet@8/bin/dotnet ]; then
    dotnet_bin=/opt/homebrew/opt/dotnet@8/bin/dotnet
elif command -v brew >/dev/null 2>&1; then
    printf '%s\n' "Installing .NET 8 SDK with Homebrew (first run only)..."
    brew install dotnet@8
    [ -x /opt/homebrew/opt/dotnet@8/bin/dotnet ] \
        || fail "Homebrew installed dotnet@8 but its executable was not found."
    dotnet_bin=/opt/homebrew/opt/dotnet@8/bin/dotnet
else
    fail "dotnet 8 is required to build the plugin. Install Homebrew, then re-run this script."
fi

mkdir -p "$RUNTIME_DIR" "$PLUGIN_DIR" "$(dirname "$PLUGIN_CONFIG")"
export BAZAAR_HELPER_DATA_DIR="$RUNTIME_DIR"

helper_healthy() {
    "$PYTHON_BIN" - "$HOST" "$PORT" <<'PY'
import json
import sys
from urllib.request import urlopen

try:
    with urlopen(f"http://{sys.argv[1]}:{sys.argv[2]}/", timeout=0.5) as response:
        payload = json.load(response)
    raise SystemExit(0 if payload.get("ok") and payload.get("mode") == "api-only" else 1)
except Exception:
    raise SystemExit(1)
PY
}

port_is_bound() {
    "$PYTHON_BIN" - "$HOST" "$PORT" <<'PY'
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.5)
    raise SystemExit(0 if sock.connect_ex((sys.argv[1], int(sys.argv[2]))) == 0 else 1)
PY
}

find_available_port() {
    "$PYTHON_BIN" - "$HOST" "$1" <<'PY'
import socket
import sys

host = sys.argv[1]
start = int(sys.argv[2])
for port in range(start, start + 20):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            continue
    print(port)
    raise SystemExit(0)
raise SystemExit("No available local port found.")
PY
}

helper_already_running=0
if helper_healthy; then
    helper_already_running=1
elif port_is_bound; then
    if [ -n "${BAZAAR_HELPER_PORT:-}" ]; then
        fail "Configured BAZAAR_HELPER_PORT=$PORT is occupied by a different service."
    fi
    PORT=$(find_available_port "$((PORT + 1))") \
        || fail "Port $PORT is occupied and no fallback port from $((PORT + 1)) to $((PORT + 20)) is available."
    printf '%s\n' "Port 8765 is occupied by a different service; using http://$HOST:$PORT instead."
fi

printf '%s\n' "Building BazaarStateExporter..."
"$dotnet_bin" build "$PLUGIN_PROJECT" -c Release \
    -p:GameRoot="$GAME_ROOT" \
    -p:BepInExRoot="$BEPINEX_ROOT" \
    -p:GameManagedDir="$MANAGED_DIR"
[ -f "$PLUGIN_OUTPUT" ] || fail "Plugin build completed without producing: $PLUGIN_OUTPUT"
install -m 644 "$PLUGIN_OUTPUT" "$PLUGIN_DIR/BazaarStateExporter.dll"

"$PYTHON_BIN" - "$PLUGIN_CONFIG" "$STATE_PATH" "http://$HOST:$PORT" <<'PY'
from pathlib import Path
import sys

config_path = Path(sys.argv[1])
state_path = sys.argv[2]
helper_base_url = sys.argv[3]
desired = {
    "Export": {"OutputPath": state_path},
    "Overlay": {
        "AutoStartHelper": "false",
        "HelperBaseUrl": helper_base_url,
    },
}

lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
output = []
current_section = None
seen = {section: set() for section in desired}

def append_missing(section: str) -> None:
    if section not in desired:
        return
    for key, value in desired[section].items():
        if key not in seen[section]:
            output.append(f"{key} = {value}")
            seen[section].add(key)

for line in lines:
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        append_missing(current_section)
        current_section = stripped[1:-1]
        output.append(line)
        continue
    if current_section in desired and "=" in line:
        key = line.split("=", 1)[0].strip()
        if key in desired[current_section]:
            output.append(f"{key} = {desired[current_section][key]}")
            seen[current_section].add(key)
            continue
    output.append(line)

append_missing(current_section)
for section, values in desired.items():
    if section not in {line.strip()[1:-1] for line in lines if line.strip().startswith("[") and line.strip().endswith("]")}:
        if output and output[-1] != "":
            output.append("")
        output.append(f"[{section}]")
        for key, value in values.items():
            output.append(f"{key} = {value}")

config_path.write_text("\n".join(output) + "\n", encoding="utf-8")
PY

if [ "$helper_already_running" -eq 1 ]; then
    printf '%s\n' "BazaarHelper API is already running at http://$HOST:$PORT"
else
    printf '%s\n' "Starting BazaarHelper API at http://$HOST:$PORT"
    nohup "$PYTHON_BIN" "$SCRIPT_DIR/src/web_app.py" --host "$HOST" --port "$PORT" --api-only \
        </dev/null >"$HELPER_LOG" 2>&1 &
    printf '%s\n' "$!" >"$HELPER_PID"
    attempt=0
    while [ "$attempt" -lt 20 ]; do
        if helper_healthy; then
            break
        fi
        attempt=$((attempt + 1))
        sleep 0.25
    done
    helper_healthy || {
        tail -40 "$HELPER_LOG" >&2 || true
        fail "BazaarHelper API did not become healthy. Log: $HELPER_LOG"
    }
fi

printf '%s\n' "State file: $STATE_PATH"
printf '%s\n' "Plugin: $PLUGIN_DIR/BazaarStateExporter.dll"
printf '%s\n' "BepInEx log: $BEPINEX_ROOT/LogOutput.log"

if [ "$launch_game" -eq 0 ]; then
    exit 0
fi

cd "$GAME_ROOT"
exec "$GAME_ROOT/run_bepinex.sh"
