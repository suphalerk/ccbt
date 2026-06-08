#!/usr/bin/env bash
# rotate-logs.sh — Rotate CCBT log files without root
# Keeps last 7 rotated copies, compresses with gzip, max 10 MB before rotating
#
# Install as a daily launchd timer via:
#   install.sh already handles this — or run manually:
#   bash deploy/macos/rotate-logs.sh

set -euo pipefail

LOG_DIR="$HOME/Library/Logs/ccbt"
MAX_SIZE_KB=10240  # 10 MB
KEEP_COPIES=7

rotate_log() {
    local log_file="$1"

    [[ -f "$log_file" ]] || return 0

    local size_kb
    size_kb=$(du -k "$log_file" | cut -f1)

    if (( size_kb < MAX_SIZE_KB )); then
        return 0
    fi

    echo "$(date '+%Y-%m-%d %H:%M:%S') Rotating: $log_file (${size_kb}KB)"

    # Shift existing rotated files: .6.gz -> .7.gz, etc.
    for i in $(seq $((KEEP_COPIES - 1)) -1 1); do
        local old="$log_file.$i.gz"
        local new="$log_file.$((i + 1)).gz"
        [[ -f "$old" ]] && mv "$old" "$new"
    done

    # Compress current log to .1.gz
    gzip -c "$log_file" > "$log_file.1.gz"

    # Truncate (not delete) the live file so the running process keeps its fd
    : > "$log_file"

    # Remove copies beyond KEEP_COPIES
    for i in $(seq $((KEEP_COPIES + 1)) $((KEEP_COPIES + 10))); do
        local old="$log_file.$i.gz"
        [[ -f "$old" ]] && rm "$old"
    done
}

mkdir -p "$LOG_DIR"

rotate_log "$LOG_DIR/bot.log"
rotate_log "$LOG_DIR/bot-error.log"
# Dashboard v2 (Vite+FastAPI) replaced Streamlit — rotate its logs, not the old dashboard.log
rotate_log "$LOG_DIR/dashboard-v2.log"
rotate_log "$LOG_DIR/dashboard-v2-error.log"
rotate_log "$LOG_DIR/checkpoint.log"
