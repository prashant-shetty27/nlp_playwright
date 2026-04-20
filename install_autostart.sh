#!/usr/bin/env bash
# install_autostart.sh — Install / uninstall the NLP Playwright LaunchAgent
#
# After install, the control panel starts automatically at login
# and is always available at http://localhost:8000
#
# Usage:
#   ./install_autostart.sh          # install & start now
#   ./install_autostart.sh stop     # stop the running service
#   ./install_autostart.sh uninstall # remove autostart

PLIST_NAME="com.nlpplaywright.controlpanel"
PLIST_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$PLIST_NAME.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"

case "${1:-install}" in

  install)
    echo "Installing NLP Playwright Control Panel as a login service…"
    mkdir -p "$HOME/Library/LaunchAgents"
    cp "$PLIST_SRC" "$PLIST_DEST"
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    launchctl load -w "$PLIST_DEST"
    echo ""
    echo "✅  Done! The control panel will now:"
    echo "    • Start automatically every time you log in"
    echo "    • Restart automatically if it crashes"
    echo "    • Always be available at http://localhost:8000"
    echo ""
    echo "    Opening http://localhost:8000 in 3 seconds…"
    sleep 3
    open "http://localhost:8000"
    ;;

  stop)
    echo "Stopping NLP Playwright Control Panel…"
    launchctl stop "$PLIST_NAME" 2>/dev/null || true
    echo "✅  Stopped. Run './install_autostart.sh' to start again."
    ;;

  uninstall)
    echo "Removing NLP Playwright Control Panel from login items…"
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "✅  Uninstalled."
    ;;

  *)
    echo "Usage: $0 [install|stop|uninstall]"
    exit 1
    ;;
esac
