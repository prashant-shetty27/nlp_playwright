#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT_DIR/.venv/bin/python"

usage() {
  cat <<'EOF'
Simple recorder launcher

Usage:
  ./start_recorder.sh web [--port 8080] [--force]
  ./start_recorder.sh app recorder [--port 8090] [--force] [--with-appium]
  ./start_recorder.sh app android [--caps suites/android_suite.json] [--port 8090] [--force] [--with-appium]
  ./start_recorder.sh app ios     [--caps suites/ios_suite.json]     [--port 8090] [--force] [--with-appium] [--skip-ios-preflight]
  ./start_recorder.sh app-recorder [--port 8090] [--force] [--with-appium]

Examples:
  ./start_recorder.sh web
  ./start_recorder.sh app recorder
  ./start_recorder.sh web --port 8081 --force
  ./start_recorder.sh app android
  ./start_recorder.sh app android --caps suites/android_suite.json --with-appium
  ./start_recorder.sh app ios --caps suites/ios_suite.json --port 8091
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

MODE="$1"; shift || true
PORT=""
CAPS=""
FORCE="false"
WITH_APPIUM="false"
SKIP_IOS_PREFLIGHT="false"
PLATFORM=""
DEFAULT_APP_PLATFORM="${DEFAULT_APP_PLATFORM:-ios}"

if [[ "$MODE" == "web" ]]; then
  PORT="8080"
elif [[ "$MODE" == "app" || "$MODE" == "app-recorder" ]]; then
  # Simple mode: "app recorder" -> uses DEFAULT_APP_PLATFORM (ios by default)
  if [[ "$MODE" == "app" && "${1:-}" == "recorder" ]]; then
    PLATFORM="$DEFAULT_APP_PLATFORM"
    shift || true
  elif [[ "$MODE" == "app-recorder" || $# -lt 1 || "${1:-}" == --* ]]; then
    PLATFORM="$DEFAULT_APP_PLATFORM"
  else
    PLATFORM="$1"; shift || true
  fi

  if [[ "$PLATFORM" != "android" && "$PLATFORM" != "ios" ]]; then
    echo "❌ Invalid platform: $PLATFORM (use android|ios or 'recorder')"
    exit 1
  fi

  PORT="8090"
  CAPS="suites/${PLATFORM}_suite.json"
else
  echo "❌ Invalid mode: $MODE"
  usage
  exit 1
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)
      PORT="$2"; shift 2 ;;
    --caps)
      CAPS="$2"; shift 2 ;;
    --force)
      FORCE="true"; shift ;;
    --with-appium)
      WITH_APPIUM="true"; shift ;;
    --skip-ios-preflight)
      SKIP_IOS_PREFLIGHT="true"; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "❌ Unknown option: $1"
      usage
      exit 1 ;;
  esac
done

if [[ ! -x "$PY" ]]; then
  echo "❌ Python venv not found: $PY"
  echo "Run setup first to create .venv"
  exit 1
fi

kill_port_if_needed() {
  local p="$1"
  local pids
  pids="$(lsof -ti tcp:"$p" || true)"
  if [[ -n "$pids" ]]; then
    if [[ "$FORCE" == "true" ]]; then
      echo "⚠️ Port $p in use. Killing: $pids"
      echo "$pids" | xargs kill -9 || true
      sleep 1
    else
      echo "❌ Port $p already in use by PID(s): $pids"
      echo "Use --force to kill and restart."
      exit 1
    fi
  fi
}

mkdir -p "$ROOT_DIR/data/logs"

if [[ "$MODE" == "web" ]]; then
  kill_port_if_needed "$PORT"
  echo "🚀 Starting WEB recorder on http://localhost:$PORT"
  nohup "$PY" "$ROOT_DIR/ui_builder.py" > "$ROOT_DIR/data/logs/ui_builder.log" 2>&1 &
  echo $! > /tmp/nlp_web_recorder.pid
  echo "✅ Started (PID $(cat /tmp/nlp_web_recorder.pid))"
  echo "📄 Log: $ROOT_DIR/data/logs/ui_builder.log"
  exit 0
fi

# app mode
if [[ "$WITH_APPIUM" == "true" ]]; then
  echo "🤖 Starting Appium first..."
  "$ROOT_DIR/appium_start.sh" --"$PLATFORM"
fi

if [[ ! -f "$ROOT_DIR/$CAPS" ]]; then
  echo "⚠️ Caps file not found: $ROOT_DIR/$CAPS"
  echo "Continuing without --caps (recorder_ui defaults will be used)."
  CAPS=""
fi

if [[ "$PLATFORM" == "ios" && "$SKIP_IOS_PREFLIGHT" != "true" ]]; then
  if [[ -n "$CAPS" ]]; then
    echo "🧪 Running iOS readiness preflight..."
    if ! "$PY" "$ROOT_DIR/execution/ios_readiness.py" --caps "$ROOT_DIR/$CAPS"; then
      echo "❌ iOS readiness failed. Fix the errors above, then retry."
      echo "   Use --skip-ios-preflight only if you intentionally want to bypass this gate."
      exit 1
    fi
  else
    echo "⚠️  iOS preflight skipped because no caps file was provided."
  fi
fi

kill_port_if_needed "$PORT"

echo "🚀 Starting APP recorder ($PLATFORM) on http://localhost:$PORT"
if [[ -n "$CAPS" ]]; then
  nohup "$PY" "$ROOT_DIR/recorder_ui.py" --platform "$PLATFORM" --caps "$ROOT_DIR/$CAPS" --port "$PORT" > "$ROOT_DIR/data/logs/recorder_ui.log" 2>&1 &
else
  nohup "$PY" "$ROOT_DIR/recorder_ui.py" --platform "$PLATFORM" --port "$PORT" > "$ROOT_DIR/data/logs/recorder_ui.log" 2>&1 &
fi

echo $! > /tmp/nlp_app_recorder.pid
echo "✅ Started (PID $(cat /tmp/nlp_app_recorder.pid))"
echo "📄 Log: $ROOT_DIR/data/logs/recorder_ui.log"
