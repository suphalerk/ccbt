#!/usr/bin/env bash
# start-dashboard.sh — Wrapper to launch the CCBT Streamlit dashboard
# Loads .env, activates venv if present, then runs streamlit

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
fi

# Activate virtual environment if one exists
if [[ -f "$PROJECT_DIR/.venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/.venv/bin/activate"
elif [[ -f "$PROJECT_DIR/venv/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$PROJECT_DIR/venv/bin/activate"
fi

# Resolve streamlit binary
STREAMLIT=""
if command -v streamlit &>/dev/null; then
    STREAMLIT="$(command -v streamlit)"
elif [[ -f "/Users/iceai/Library/Python/3.9/bin/streamlit" ]]; then
    STREAMLIT="/Users/iceai/Library/Python/3.9/bin/streamlit"
elif [[ -f "$PROJECT_DIR/.venv/bin/streamlit" ]]; then
    STREAMLIT="$PROJECT_DIR/.venv/bin/streamlit"
elif [[ -f "$PROJECT_DIR/venv/bin/streamlit" ]]; then
    STREAMLIT="$PROJECT_DIR/venv/bin/streamlit"
fi

if [[ -z "$STREAMLIT" ]]; then
    echo "ERROR: streamlit not found. Install it with: pip install streamlit" >&2
    exit 1
fi

export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting CCBT dashboard"
echo "  Streamlit: $STREAMLIT"
echo "  Working:   $PROJECT_DIR"

exec "$STREAMLIT" run dashboard/app.py \
    --server.port 8501 \
    --server.address 0.0.0.0 \
    --server.headless true \
    --browser.gatherUsageStats false
