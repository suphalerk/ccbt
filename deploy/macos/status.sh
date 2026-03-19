#!/usr/bin/env bash
# status.sh — Show the status of CCBT launchd services

set -euo pipefail

LOG_DIR="$HOME/Library/Logs/ccbt"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

echo "=== CCBT Service Status ==="
echo ""

# Helper: check a single service
check_service() {
    local label="$1"
    local plist_file="$LAUNCH_AGENTS_DIR/${label}.plist"

    # Check if plist is installed
    if [[ ! -f "$plist_file" ]]; then
        printf "  %-35s  NOT INSTALLED\n" "$label"
        return
    fi

    # launchctl list output: PID  LastExitCode  Label
    local info
    info="$(launchctl list "$label" 2>/dev/null || echo "NOT_LOADED")"

    if [[ "$info" == "NOT_LOADED" ]]; then
        printf "  %-35s  STOPPED (plist installed, not loaded)\n" "$label"
        return
    fi

    local pid last_exit
    pid="$(echo "$info" | awk '/\"PID\"/ {gsub(/[^0-9]/,"",$NF); print $NF}')"
    last_exit="$(echo "$info" | awk '/\"LastExitStatus\"/ {gsub(/[^0-9-]/,"",$NF); print $NF}')"

    if [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]]; then
        printf "  %-35s  RUNNING  (PID %s)\n" "$label" "$pid"
    else
        printf "  %-35s  STOPPED  (last exit: %s)\n" "$label" "${last_exit:-unknown}"
    fi
}

check_service "com.ccbt.trading-bot"
check_service "com.ccbt.dashboard"

echo ""
echo "=== Recent Log Tails ==="
echo ""

for log in bot.log dashboard.log; do
    log_path="$LOG_DIR/$log"
    if [[ -f "$log_path" ]]; then
        echo "-- $log (last 5 lines) --"
        tail -5 "$log_path"
        echo ""
    else
        echo "-- $log: not found --"
        echo ""
    fi
done

echo "Dashboard URL: http://localhost:8501"
echo ""
echo "Commands:"
echo "  Start bot:       launchctl start com.ccbt.trading-bot"
echo "  Stop bot:        launchctl stop com.ccbt.trading-bot"
echo "  Start dashboard: launchctl start com.ccbt.dashboard"
echo "  Stop dashboard:  launchctl stop com.ccbt.dashboard"
echo "  Follow bot log:  tail -f $LOG_DIR/bot.log"
