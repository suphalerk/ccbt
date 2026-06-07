#!/usr/bin/env bash
# start-dashboard-v2.sh — Launch the CCBT dashboard v2 (FastAPI + Vite SPA)
#
# Security model: does NOT source .env wholesale into the process environment.
# Only CCBT_DASH_* and BOT_DATA_DIR are allowlist-exported; all exchange,
# broker, AI, and Telegram secrets are excluded by not reading them at all.
# CCBT_SOCKS_PROXY is explicitly unset so the dashboard can never egress to
# the Binance SOCKS5 proxy.
#
# Port: 8601 (distinct from the legacy Streamlit on 8501 — both run in
# parallel during the N13 soak period so neither must collide).
#
# DO NOT load/unload launchd plists from this script. The atomic swap of the
# old Streamlit service for this one happens only at the N13 cutover.

set -euo pipefail

PROJECT_DIR="/Users/iceai/Work/ccbt"
VENV="$PROJECT_DIR/.venv-dash"

# ---------------------------------------------------------------------------
# Defaults — can be overridden by the env or the allowlisted .env values below
# ---------------------------------------------------------------------------
CCBT_DASH_HOST="${CCBT_DASH_HOST:-127.0.0.1}"
CCBT_DASH_PORT="${CCBT_DASH_PORT:-8601}"
CCBT_DASH_POLL_S="${CCBT_DASH_POLL_S:-3}"

# ---------------------------------------------------------------------------
# CCBT_DASH_TOKEN: required when running behind nginx (localhost peer-check
# is vacuous; the token is the real auth layer for the VPS path).
# ---------------------------------------------------------------------------
if [[ -z "${CCBT_DASH_TOKEN:-}" ]]; then
    echo "WARNING: CCBT_DASH_TOKEN is not set. Required when running behind nginx." >&2
fi

# ---------------------------------------------------------------------------
# Secret hygiene: remove the exchange proxy from the environment.
# The dashboard must never route traffic through the Binance SOCKS5 tunnel.
# ---------------------------------------------------------------------------
unset CCBT_SOCKS_PROXY

# ---------------------------------------------------------------------------
# Read only the allowlisted variables from .env (if present).
# We parse manually rather than sourcing the whole file so that exchange keys,
# broker tokens, AI keys, and Telegram secrets are never loaded into this
# process's environment — only CCBT_DASH_* and BOT_DATA_DIR are extracted.
# ---------------------------------------------------------------------------
ENV_FILE="$PROJECT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
    while IFS='=' read -r _key _value; do
        # Skip blank lines and full-comment lines
        [[ -z "$_key" || "$_key" == \#* ]] && continue
        _key="${_key%%[[:space:]]*}"   # trim trailing whitespace
        # Allowlist: only keys that start with CCBT_DASH_ or are BOT_DATA_DIR
        if [[ "$_key" == CCBT_DASH_* || "$_key" == "BOT_DATA_DIR" ]]; then
            # Strip surrounding quotes from value
            _value="${_value#\"}"
            _value="${_value%\"}"
            _value="${_value#\'}"
            _value="${_value%\'}"
            export "$_key"="$_value"
        fi
    done < "$ENV_FILE"
fi

# Re-apply defaults after .env parsing (allowlisted vars may have been set)
CCBT_DASH_HOST="${CCBT_DASH_HOST:-127.0.0.1}"
CCBT_DASH_PORT="${CCBT_DASH_PORT:-8601}"
CCBT_DASH_POLL_S="${CCBT_DASH_POLL_S:-3}"

# ---------------------------------------------------------------------------
# Pre-flight: assert Vite bundle was built before starting
# (The build step runs in CI / a Makefile target — not at launch time.)
# ---------------------------------------------------------------------------
DIST_INDEX="$PROJECT_DIR/web/dist/index.html"
if [[ ! -f "$DIST_INDEX" ]]; then
    echo "ERROR: web/dist/index.html not found (bundle not built)." >&2
    echo "  Build: npm --prefix $PROJECT_DIR/web run build" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Pre-flight: fail fast if port 8601 is already bound.
# Prevents collision with the legacy Streamlit (8501) or any other process.
# ---------------------------------------------------------------------------
if lsof -iTCP:"$CCBT_DASH_PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "ERROR: Port $CCBT_DASH_PORT is already in use." >&2
    echo "  Diagnose: lsof -iTCP:$CCBT_DASH_PORT -sTCP:LISTEN" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Activate the pinned venv (.venv-dash, Python 3.12 via N0)
# ---------------------------------------------------------------------------
if [[ ! -f "$VENV/bin/activate" ]]; then
    echo "ERROR: $VENV not found." >&2
    echo "  Create: python3 -m venv $VENV && $VENV/bin/pip install -r requirements.txt" >&2
    exit 1
fi

# shellcheck source=/dev/null
source "$VENV/bin/activate"

# ---------------------------------------------------------------------------
# Export only the safe allowlisted subset to FastAPI
# ---------------------------------------------------------------------------
export CCBT_DASH_HOST
export CCBT_DASH_PORT
export CCBT_DASH_POLL_S
export BOT_DATA_DIR="${BOT_DATA_DIR:-$PROJECT_DIR/data}"
export PYTHONPATH="$PROJECT_DIR:${PYTHONPATH:-}"

# ---------------------------------------------------------------------------
# Launch uvicorn — host 127.0.0.1 port 8601
# ---------------------------------------------------------------------------
cd "$PROJECT_DIR"

echo "$(date '+%Y-%m-%d %H:%M:%S') Starting CCBT dashboard v2"
echo "  Host:    $CCBT_DASH_HOST"
echo "  Port:    $CCBT_DASH_PORT"
echo "  Poll:    ${CCBT_DASH_POLL_S}s"
echo "  Bundle:  $DIST_INDEX"
echo "  Python:  $(python --version 2>&1)"

exec uvicorn api.main:app \
    --host "$CCBT_DASH_HOST" \
    --port "$CCBT_DASH_PORT" \
    --loop uvloop \
    --log-level info
