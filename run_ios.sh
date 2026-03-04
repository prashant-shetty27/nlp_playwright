#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT_DIR/.venv/bin/python"
CAPS_FILE="suites/ios_suite.json"
APPIUM_PORT="4723"
KEEP_RUNNING="false"
SKIP_TUNNEL="false"

usage() {
  cat <<'EOF'
Run iOS suite end-to-end (readiness -> tunnel -> Appium -> runner)

Usage:
  ./run_ios.sh [--caps suites/ios_suite.json] [--port 4723] [--keep-running] [--skip-tunnel]

Options:
  --caps          Path to iOS suite/caps JSON (default: suites/ios_suite.json)
  --port          Appium port (default: 4723)
  --keep-running  Do not stop tunnel/Appium when runner exits
  --skip-tunnel   Skip tunnel startup (use only if already running)
  -h, --help      Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --caps) CAPS_FILE="$2"; shift 2 ;;
    --port) APPIUM_PORT="$2"; shift 2 ;;
    --keep-running) KEEP_RUNNING="true"; shift ;;
    --skip-tunnel) SKIP_TUNNEL="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

if [[ ! -x "$PY" ]]; then
  echo "❌ Missing virtualenv Python: $PY"
  echo "Run: ./setup.sh"
  exit 1
fi

if [[ ! -f "$ROOT_DIR/$CAPS_FILE" ]]; then
  echo "❌ Caps file not found: $ROOT_DIR/$CAPS_FILE"
  exit 1
fi

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "❌ Required command not found: $1"
    exit 1
  fi
}

require_cmd appium
require_cmd xcrun
require_cmd curl
require_cmd grep

LOG_DIR="$ROOT_DIR/data/logs"
mkdir -p "$LOG_DIR"

APPIUM_PID=""
TUNNEL_PID=""

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [[ "$KEEP_RUNNING" != "true" ]]; then
    if [[ -n "$APPIUM_PID" ]]; then
      kill "$APPIUM_PID" >/dev/null 2>&1 || true
    fi
    if [[ -n "$TUNNEL_PID" ]]; then
      kill "$TUNNEL_PID" >/dev/null 2>&1 || true
    fi
  fi
  exit "$code"
}
trap cleanup EXIT INT TERM

echo "🧪 iOS readiness check..."
"$PY" "$ROOT_DIR/execution/ios_readiness.py" --caps "$ROOT_DIR/$CAPS_FILE"

UDID="$("$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); c=d.get("desired_capabilities",d); print((c.get("appium:udid") or c.get("udid") or "").strip())' "$ROOT_DIR/$CAPS_FILE")"
if [[ -z "$UDID" ]]; then
  echo "❌ No appium:udid found in $CAPS_FILE"
  exit 1
fi

if ! xcrun xctrace list devices | grep -q "$UDID"; then
  echo "❌ UDID not visible to Xcode tooling: $UDID"
  echo "Unlock iPhone, accept trust prompt, reconnect cable, then retry."
  exit 1
fi

if [[ "$SKIP_TUNNEL" != "true" ]]; then
  echo "🔌 Starting XCUITest tunnel..."
  if ! sudo -n true 2>/dev/null; then
    echo "🔐 sudo access is required for tunnel creation."
    sudo -v
  fi

  nohup sudo appium driver run xcuitest tunnel-creation \
    > "$LOG_DIR/tunnel.log" 2>&1 &
  TUNNEL_PID=$!
  echo "$TUNNEL_PID" > /tmp/nlp_ios_tunnel.pid

  tunnel_ready="false"
  for _ in $(seq 1 30); do
    if curl -sf "http://localhost:42314/remotexpc/tunnels" > /tmp/nlp_tunnels.json; then
      if grep -q "$UDID" /tmp/nlp_tunnels.json; then
        tunnel_ready="true"
        break
      fi
    fi
    sleep 1
  done

  if [[ "$tunnel_ready" != "true" ]]; then
    echo "❌ Tunnel did not become ready for UDID $UDID"
    echo "Check log: $LOG_DIR/tunnel.log"
    exit 1
  fi
fi

if lsof -ti tcp:"$APPIUM_PORT" >/dev/null 2>&1; then
  echo "♻️  Stopping existing Appium on port $APPIUM_PORT..."
  lsof -ti tcp:"$APPIUM_PORT" | xargs kill -9 >/dev/null 2>&1 || true
  sleep 1
fi

echo "🤖 Starting Appium on port $APPIUM_PORT..."
nohup appium --port "$APPIUM_PORT" --log-level info \
  > "$LOG_DIR/appium.log" 2>&1 &
APPIUM_PID=$!
echo "$APPIUM_PID" > /tmp/nlp_ios_appium.pid

appium_ready="false"
for _ in $(seq 1 30); do
  if curl -sf "http://localhost:${APPIUM_PORT}/status" | grep -q '"ready":true'; then
    appium_ready="true"
    break
  fi
  sleep 1
done

if [[ "$appium_ready" != "true" ]]; then
  echo "❌ Appium failed to become ready on port $APPIUM_PORT"
  echo "Check log: $LOG_DIR/appium.log"
  exit 1
fi

echo "🚀 Running iOS suite..."
"$PY" "$ROOT_DIR/runner_appium.py" "$ROOT_DIR/$CAPS_FILE"
