# api/ — FastAPI Dashboard Backend (v2)

FastAPI service that replaces the Streamlit dashboard with a REST+WebSocket API.
Frontend: `web/` (Vite + React + Tailwind).

## Python requirement

**Python 3.10+** — uses pydantic>=2. System Python is 3.9.6; use `.venv-dash`:

```bash
# Create venv (one time)
/opt/homebrew/bin/python3.12 -m venv .venv-dash
.venv-dash/bin/pip install -r requirements.txt

# Run the API (dev, with reload)
.venv-dash/bin/uvicorn api.main:app --reload --port 8502
```

## Endpoint inventory

### REST (GET)

| Path | Description |
|------|-------------|
| `/api/health` | Liveness probe |
| `/api/portfolio/summary` | Portfolio-level aggregates |
| `/api/bots` | All bots with summary stats |
| `/api/bots/{symbol}` | Single bot detail + recent trades |
| `/api/trades?symbol=&limit=` | Closed trades list |
| `/api/equity?symbol=` | Equity curve points |
| `/api/daily-pnl?symbol=` | Daily PnL by UTC date |
| `/api/ai/calibration` | AI advisor accuracy stats |
| `/api/logs?level=&search=&limit=` | Log tail |
| `/api/close-reasons?symbol=` | Close-reason breakdown (N9) |
| `/api/trade-gate` | Attribution gate per symbol (N9) |
| `/api/risk?symbol=` | Open position risk (N9) |
| `/api/calendar?symbol=&year=&month=` | Calendar PnL heatmap (N9) |
| `/api/heatmap?symbol=&bucket_hours=` | Hour×DOW heatmap (N9) |

### POST (auth-gated)

| Path | Description |
|------|-------------|
| `/api/bots/{symbol}/mode` | Set bot mode (N8) |
| `/api/bots/mode/bulk` | Bulk mode change (N8) |

### WebSocket

| Path | Description |
|------|-------------|
| `/ws` | Low-freq snapshots (portfolio + bots, ~3s, N2) |
| `/ws/logs` | High-freq log tail (N2/N7) |

## OpenAPI contract

The committed `openapi.json` in the repo root is the source of truth for TS codegen:

```bash
# Re-generate after changing models/routes
.venv-dash/bin/python scripts/export_openapi.py
```

Frontend generates TS types via:
```bash
npm --prefix web run generate-types
```

## Architecture notes

- ALL financial math stays in Python (`dashboard/queries.py`). TypeScript only formats/renders.
- DB access is read-only (`mode=ro, query_only=1`). The API never writes to trades.db.
- Bot mode writes go to `data/mode_{SYMBOL}.json` (N8), never to the DB.
- The API never calls the exchange (no ccxt, no SOCKS proxy).
