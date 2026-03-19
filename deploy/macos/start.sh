#!/usr/bin/env bash
# start.sh — Wrapper to launch the CCBT trading bot
# Loads .env, activates venv if present, then runs main.py

set -euo pipefail

PROJECT_DIR="/Users/iceai/Work/ccbt"
cd "$PROJECT_DIR"

# Load environment variables from .env if it exists
if [[ -f "$PROJECT_DIR/.env" ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.env"
    set +a
else
    echo "WARNING: .env file not found at $PROJECT_DIR/.env" >&2
    echo "Bot will start but API keys will be missing." >&2
fi

# Activate virtual environment if one exists
if [[ -f "$PROJECT_DIR/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.venv/bin/activate"
elif [[ -f "$PROJECT_DIR/venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/venv/bin/activate"
fi

# Resolve python — prefer venv python, fall back to homebrew, then system
PYTHON="python3"
if command -v python3 &>/dev/null; then
    PYTHON="$(command -v python3)"
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting CCBT trading bot"
echo "  Python:  $PYTHON"
echo "  Working: $PROJECT_DIR"

exec "$PYTHON" main.py
