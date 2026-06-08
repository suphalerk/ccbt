#!/usr/bin/env bash
# install.sh — Install CCBT launchd services on macOS
# Copies plists to ~/Library/LaunchAgents and loads them.
# Services started: trading bot, Streamlit dashboard, daily log rotation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$HOME/Library/Logs/ccbt"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

PLISTS=(
    "com.ccbt.trading-bot.plist"
    "com.ccbt.dashboard-v2.plist"
    "com.ccbt.rotate-logs.plist"
)

echo "=== CCBT macOS Service Installer ==="
echo ""

# 1. Create log directory
echo "Creating log directory: $LOG_DIR"
mkdir -p "$LOG_DIR"

# 2. Make all scripts executable
echo "Making scripts executable..."
chmod +x \
    "$SCRIPT_DIR/start.sh" \
    "$SCRIPT_DIR/start-dashboard.sh" \
    "$SCRIPT_DIR/rotate-logs.sh" \
    "$SCRIPT_DIR/status.sh" \
    "$SCRIPT_DIR/uninstall.sh"

# 3. Warn if .env is missing (don't block install)
if [[ ! -f "/Users/iceai/Work/ccbt/.env" ]]; then
    echo ""
    echo "WARNING: /Users/iceai/Work/ccbt/.env not found."
    echo "  Copy .env.example to .env and fill in your API keys"
    echo "  before the bot can connect to any exchange or AI service."
    echo ""
fi

# 4. Unload any existing versions (silent if not loaded)
echo "Unloading any existing services..."
for plist in "${PLISTS[@]}"; do
    label="${plist%.plist}"
    launchctl unload "$LAUNCH_AGENTS_DIR/$plist" 2>/dev/null && \
        echo "  Unloaded: $label" || true
done

# 5. Copy plists to LaunchAgents
echo ""
echo "Copying plists to $LAUNCH_AGENTS_DIR..."
for plist in "${PLISTS[@]}"; do
    cp "$SCRIPT_DIR/$plist" "$LAUNCH_AGENTS_DIR/$plist"
    echo "  Copied: $plist"
done

# 6. Load all services
echo ""
echo "Loading services..."
for plist in "${PLISTS[@]}"; do
    label="${plist%.plist}"
    launchctl load "$LAUNCH_AGENTS_DIR/$plist"
    echo "  Loaded: $label"
done

echo ""
echo "=== Installation complete ==="
echo ""

# 7. Show status
"$SCRIPT_DIR/status.sh"

echo ""
echo "Log directory: $LOG_DIR"
echo "  tail -f $LOG_DIR/bot.log"
echo "  tail -f $LOG_DIR/bot-error.log"
echo "  tail -f $LOG_DIR/dashboard.log"
echo ""
echo "Dashboard:  http://localhost:8501"
echo "Uninstall:  bash $SCRIPT_DIR/uninstall.sh"
