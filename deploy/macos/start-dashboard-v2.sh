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
# CCBT_DASH_TOKEN: required when the dashboard is reachable from non-localhost
# peers (i.e. CCBT_DASH_HOST != 127.0.0.1).  On a VPS the token is the real
# auth layer forwarded by nginx to FastAPI; on a local Mac Mini it is optional
# (loopback-only, nginx basic-auth provides the outer gate).
#
# Note: the allowlist .env parsing below runs AFTER this check.  If the token
# is set in .env rather than the launchd environment, set it via:
#   launchctl setenv CCBT_DASH_TOKEN "$(openssl rand -hex 32)"
# IMPORTANT: 'launchctl setenv' updates the launchd session environment but
# uvicorn (and therefore FastAPI) reads CCBT_DASH_TOKEN once at import time.
# A simple 'launchctl stop/start' does NOT pick up the new value because the
# parent launchd session re-exports the FROZEN old value.  Always use:
#   launchctl kickstart -k gui/$(id -u)/com.ccbt.dashboard-v2
# ('kickstart -k' kills the running job and starts a fresh one in the updated
# launchd environment, so the new token is visible in os.environ at startup.)
# ---------------------------------------------------------------------------
if [[ "${CCBT_DASH_HOST:-127.0.0.1}" != "127.0.0.1" && "${CCBT_DASH_HOST:-127.0.0.1}" != "localhost" ]]; then
    if [[ -z "${CCBT_DASH_TOKEN:-}" ]]; then
        echo "ERROR: CCBT_DASH_TOKEN must be set when CCBT_DASH_HOST is not 127.0.0.1." >&2
        echo "  Generate: openssl rand -hex 32" >&2
        echo "  Inject:   launchctl setenv CCBT_DASH_TOKEN <value>" >&2
        exit 1
    fi
elif [[ -z "${CCBT_DASH_TOKEN:-}" ]]; then
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
            # Strip inline comments: ' # ...' or ' #...' (space then hash)
            # e.g.  CCBT_DASH_PORT=8601  # dashboard port  -> 8601
            _value="${_value%%[[:space:]]#*}"
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
# Export only the safe allowlisted subset to FastAPI.
# CCBT_DASH_TOKEN is intentionally included: FastAPI reads it from os.environ
# for per-request token validation.  Without this export the variable lives
# only in the parent shell and uvicorn inherits an empty string, causing every
# token check to silently pass.
# ---------------------------------------------------------------------------
export CCBT_DASH_HOST
export CCBT_DASH_PORT
export CCBT_DASH_POLL_S
export CCBT_DASH_TOKEN
# trades.db lives at the PROJECT ROOT (the bot's start.sh does not set BOT_DATA_DIR,
# so it defaults to '.' = project root). Point the dashboard at the same root, NOT
# $PROJECT_DIR/data (which holds a stale 28KB trades.db and made /api/* read empty).
export BOT_DATA_DIR="${BOT_DATA_DIR:-$PROJECT_DIR}"
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
    --log-level info \
    --no-access-log
