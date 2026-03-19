#!/usr/bin/env bash
# ngrok_dashboard.sh — Expose the CCBT Streamlit dashboard via ngrok tunnel
#
# Usage:
#   bash deploy/macos/ngrok_dashboard.sh
#
# What it does:
#   1. Starts the Streamlit dashboard (port 8501) if not already running
#   2. Opens an ngrok HTTPS tunnel to port 8501
#   3. Prints the public URL
#
# Requirements:
#   - ngrok installed (brew install ngrok) and authenticated
#   - Streamlit + bot dependencies installed in venv or system Python

set -euo pipefail

PROJECT_DIR="/Users/iceai/Work/ccbt"
DASHBOARD_PORT=8501
LOG_DIR="$PROJECT_DIR/logs"
DASHBOARD_LOG="$LOG_DIR/dashboard.log"
NGROK_LOG="$LOG_DIR/ngrok.log"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

ensure_log_dir() {
    mkdir -p "$LOG_DIR"
}

dashboard_running() {
    # True if something is already listening on the dashboard port
    lsof -i tcp:"$DASHBOARD_PORT" -sTCP:LISTEN -t &>/dev/null
}

ngrok_running() {
    pgrep -x ngrok &>/dev/null
}

start_dashboard() {
    log "Dashboard not running — starting Streamlit on port $DASHBOARD_PORT..."
    # Run start-dashboard.sh in the background, redirecting output to log
    nohup bash "$PROJECT_DIR/deploy/macos/start-dashboard.sh" \
        >> "$DASHBOARD_LOG" 2>&1 &

    # Wait up to 15 seconds for the port to open
    local waited=0
    while ! dashboard_running; do
        if (( waited >= 15 )); then
            log "ERROR: Streamlit did not start within 15 seconds."
            log "       Check $DASHBOARD_LOG for details."
            exit 1
        fi
        sleep 1
        (( waited++ ))
    done
    log "Dashboard is up (took ${waited}s)."
}

stop_ngrok() {
    if ngrok_running; then
        log "Stopping existing ngrok process..."
        pkill -x ngrok || true
        sleep 1
    fi
}

get_public_url() {
    # Poll the ngrok local API for the public URL (up to 10 seconds)
    local waited=0
    while (( waited < 10 )); do
        local url
        url=$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null \
              | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    tunnels = data.get('tunnels', [])
    for t in tunnels:
        if t.get('proto') == 'https':
            print(t['public_url'])
            break
except Exception:
    pass
" 2>/dev/null || true)
        if [[ -n "$url" ]]; then
            echo "$url"
            return 0
        fi
        sleep 1
        (( waited++ ))
    done
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ensure_log_dir

# 1. Dashboard
if dashboard_running; then
    log "Streamlit is already running on port $DASHBOARD_PORT."
else
    start_dashboard
fi

# 2. ngrok — kill any stale tunnel first so we get a fresh URL
stop_ngrok

log "Starting ngrok tunnel to localhost:$DASHBOARD_PORT..."
nohup ngrok http "$DASHBOARD_PORT" \
    --log stdout \
    --log-format json \
    >> "$NGROK_LOG" 2>&1 &

# 3. Fetch and display public URL
PUBLIC_URL=$(get_public_url)

echo ""
echo "========================================"
if [[ -n "$PUBLIC_URL" ]]; then
    echo "  CCBT Dashboard is publicly accessible"
    echo ""
    echo "  URL:  $PUBLIC_URL"
    echo ""
    echo "  ngrok inspector: http://127.0.0.1:4040"
    echo "  Logs: $NGROK_LOG"
else
    echo "  WARNING: Could not retrieve ngrok public URL."
    echo "  Check the ngrok inspector at http://127.0.0.1:4040"
    echo "  or the log at $NGROK_LOG"
fi
echo "========================================"
echo ""
log "To stop the tunnel:  pkill -x ngrok"
log "To stop dashboard:   pkill -f 'streamlit run dashboard/app.py'"
