#!/usr/bin/env bash
# uninstall.sh — Remove CCBT launchd services from macOS

set -euo pipefail

LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/ccbt"

PLISTS=(
    "com.ccbt.trading-bot.plist"
    "com.ccbt.dashboard.plist"
    "com.ccbt.rotate-logs.plist"
)

echo "=== CCBT macOS Service Uninstaller ==="
echo ""

# Unload services
echo "Unloading services..."
for plist in "${PLISTS[@]}"; do
    label="${plist%.plist}"
    if launchctl unload "$LAUNCH_AGENTS_DIR/$plist" 2>/dev/null; then
        echo "  Unloaded: $label"
    else
        echo "  Not loaded (skipped): $label"
    fi
done

# Remove plist files
echo ""
echo "Removing plist files from $LAUNCH_AGENTS_DIR..."
for plist in "${PLISTS[@]}"; do
    target="$LAUNCH_AGENTS_DIR/$plist"
    if [[ -f "$target" ]]; then
        rm "$target"
        echo "  Removed: $target"
    else
        echo "  Not found (skipped): $target"
    fi
done

echo ""
echo "=== Services uninstalled ==="
echo ""
echo "NOTE: Log files remain at $LOG_DIR"
echo "      Remove manually if no longer needed:"
echo "        rm -rf $LOG_DIR"
