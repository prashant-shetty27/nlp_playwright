#!/usr/bin/env bash
# appium_start.sh — Start Appium with required drivers
#
# Usage:
#   ./appium_start.sh              # start with all drivers
#   ./appium_start.sh --android    # uiautomator2 only
#   ./appium_start.sh --ios        # xcuitest only
#   ./appium_start.sh --port 4724  # custom port

set -euo pipefail

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
echo "🚀  Starting Appium on port $PORT ..."
echo "    Stop with Ctrl+C"
echo "═══════════════════════════════════════════════════════"

appium --port "$PORT" --log-level info
