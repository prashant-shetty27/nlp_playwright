#!/usr/bin/env bash
# appium_start.sh — Start Appium with required drivers
#
# Usage:
#   ./appium_start.sh              # start with all drivers
#   ./appium_start.sh --android    # uiautomator2 only
#   ./appium_start.sh --ios        # xcuitest only
#   ./appium_start.sh --port 4724  # custom port

set -uo pipefail

PORT=4723
PLATFORM="all"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --android) PLATFORM="android" ;;
    --ios)     PLATFORM="ios"     ;;
    --port)    PORT="$2"; shift   ;;
    *)         echo "Unknown option: $1"; exit 1 ;;
  esac
  shift
done

echo "═══════════════════════════════════════════════════════"
echo "  🤖  Appium Launcher"
echo "  Port     : $PORT"
echo "  Platform : $PLATFORM"
echo "═══════════════════════════════════════════════════════"

# ── Check Appium is installed ─────────────────────────────
if ! command -v appium &>/dev/null; then
  echo "❌  Appium not found. Install with:"
  echo "    npm install -g appium"
  exit 1
fi

echo "✅  Appium version: $(appium --version)"

# ── Install drivers if missing ────────────────────────────
install_if_missing() {
  local driver="$1"
  if ! appium driver list --installed 2>/dev/null | grep -q "$driver"; then
    echo "📦  Installing driver: $driver ..."
    appium driver install "$driver"
  else
    echo "✅  Driver already installed: $driver"
  fi
}

if [[ "$PLATFORM" == "all" || "$PLATFORM" == "android" ]]; then
  install_if_missing uiautomator2
fi

if [[ "$PLATFORM" == "all" || "$PLATFORM" == "ios" ]]; then
  install_if_missing xcuitest
fi

# ── Set ANDROID_HOME if not already set ──────────────────
if [[ -z "${ANDROID_HOME:-}" ]]; then
  # Detect brew-installed platform-tools (macOS)
  BREW_PT=$(ls /usr/local/Caskroom/android-platform-tools/ 2>/dev/null | sort -V | tail -1)
  if [[ -n "$BREW_PT" ]]; then
    export ANDROID_HOME="/usr/local/Caskroom/android-platform-tools/${BREW_PT}"
    export ANDROID_SDK_ROOT="$ANDROID_HOME"
    echo "✅  ANDROID_HOME auto-set: $ANDROID_HOME"
  else
    echo "⚠️   ANDROID_HOME not set. Export it before running this script."
  fi
else
  echo "✅  ANDROID_HOME: $ANDROID_HOME"
fi

# ── Set JAVA_HOME if not already set ─────────────────────
if [[ -z "${JAVA_HOME:-}" ]]; then
  DETECTED_JAVA=$(/usr/libexec/java_home 2>/dev/null || true)
  if [[ -n "$DETECTED_JAVA" ]]; then
    export JAVA_HOME="$DETECTED_JAVA"
    echo "✅  JAVA_HOME auto-set: $JAVA_HOME"
  else
    echo "⚠️   JAVA_HOME not set and java_home not found."
  fi
else
  echo "✅  JAVA_HOME: $JAVA_HOME"
fi

# ── Android env check ─────────────────────────────────────
if [[ "$PLATFORM" == "all" || "$PLATFORM" == "android" ]]; then
  if command -v adb &>/dev/null; then
    echo ""
    echo "📱  Connected Android devices:"
    adb devices
  else
    echo "⚠️   ADB not found. Install Android SDK platform-tools."
  fi
fi

# ── iOS env check ─────────────────────────────────────────
if [[ "$PLATFORM" == "all" || "$PLATFORM" == "ios" ]]; then
  if command -v xcrun &>/dev/null; then
    echo ""
    echo "📱  Available iOS simulators (first 10):"
    xcrun simctl list devices available | grep -E "iPhone|iPad" | head -10
  else
    echo "⚠️   xcrun not found. Install Xcode from the App Store."
  fi
fi

echo ""
echo "🚀  Starting Appium on port $PORT (background, log: data/logs/appium.log)"
echo "    Stop with: kill \$(cat /tmp/appium.pid)"
echo "═══════════════════════════════════════════════════════"

mkdir -p "$(dirname "$0")/data/logs"
nohup appium --port "$PORT" --log-level info \
  > "$(dirname "$0")/data/logs/appium.log" 2>&1 &
echo $! > /tmp/appium.pid
echo "✅  Appium started — PID: $(cat /tmp/appium.pid)"
echo "    Waiting for server to be ready..."
sleep 5
curl -s "http://localhost:${PORT}/status" | grep -o '"ready":[a-z]*' \
  && echo "✅  Appium is ready!" \
  || echo "⚠️  Appium may still be starting — check data/logs/appium.log"
