#!/usr/bin/env bash
# scripts/dev_dashboard.sh — start the CCBT Dashboard v2 dev servers
#
# Usage: bash scripts/dev_dashboard.sh
#
# Starts:
#   - FastAPI (uvicorn --reload) on port 8502 (does not conflict with Streamlit on 8501)
#   - Vite dev server on port 5173 (proxies /api and /ws to FastAPI)
#
# Requirements:
#   - .venv-dash created: /opt/homebrew/bin/python3.12 -m venv .venv-dash
#   - deps installed:     .venv-dash/bin/pip install -r requirements.txt
#   - npm deps installed: npm --prefix web install

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Allowlist-only env exports — never expose mainnet/exchange keys to the dashboard
export BOT_DATA_DIR="${BOT_DATA_DIR:-}"
export CCBT_DASH_TOKEN="${CCBT_DASH_TOKEN:-}"

# Explicitly unset exchange / secrets keys
unset MAINNET_API_KEY MAINNET_SECRET_KEY API_KEY API_SECRET
unset ANTHROPIC_API_KEY TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID
unset OANDA_API_TOKEN OANDA_ACCOUNT_ID CRYPTOPANIC_TOKEN
unset CCBT_SOCKS_PROXY

VENV="$REPO/.venv-dash"
if [[ ! -f "$VENV/bin/uvicorn" ]]; then
  echo "ERROR: .venv-dash not found or uvicorn not installed."
  echo "  Run: /opt/homebrew/bin/python3.12 -m venv .venv-dash"
  echo "       .venv-dash/bin/pip install -r requirements.txt"
  exit 1
fi

WEB="$REPO/web"
if [[ ! -d "$WEB/node_modules" ]]; then
  echo "ERROR: web/node_modules not found."
  echo "  Run: npm --prefix web install"
  exit 1
fi

# Fail fast if port 8502 is already bound
if lsof -iTCP:8502 -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "ERROR: Port 8502 is already in use. Cannot start FastAPI."
  exit 1
fi

echo "Starting FastAPI on port 8502..."
"$VENV/bin/uvicorn" api.main:app --reload --port 8502 --host 127.0.0.1 &
UVICORN_PID=$!

echo "Starting Vite on port 5173..."
npm --prefix "$WEB" run dev &
VITE_PID=$!

trap "kill $UVICORN_PID $VITE_PID 2>/dev/null; exit 0" INT TERM

echo ""
echo "Dashboard v2 running:"
echo "  API:       http://localhost:8502/api/health"
echo "  API docs:  http://localhost:8502/api/docs"
echo "  Frontend:  http://localhost:5173"
echo ""
echo "Press Ctrl-C to stop both servers."
wait
