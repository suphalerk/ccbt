# N1 — FastAPI REST (parity data layer)

**Phase B · est. M–L (1–2 days) · §prereq runs in Phase A**

## 🔴 §Prereq (must-fix #2 — do FIRST, benefits Streamlit too)
- **Fix `get_per_bot_summary` orphan leak** (queries.py:727): add
  `AND COALESCE(close_reason,'') != 'orphan_reconcile'` (currently only `status='closed'`). 35 orphan
  rows (pnl=0) are leaking as losses across ~24 symbols, deflating WR/PF on the CURRENT dashboard.
- **Make `_get_connection` read-only**: open with `sqlite3.connect('file:...?mode=ro', uri=True)` +
  `PRAGMA query_only=1` so the API process can never write/lock against the bot.
- TDD: a test seeding an orphan row asserts it's excluded from `get_per_bot_summary`; a cross-function
  invariant test asserts per-symbol `get_per_bot_summary.total_pnl == Σ get_closed_trades(symbol).pnl`.

## Goal
Expose all data the current Streamlit dashboard shows, via REST, **by reusing the (now-fixed)
`dashboard/queries.py`** (zero re-implementation of metrics).

## Scope — endpoints → existing query functions
- `/api/portfolio/summary` → `get_trade_stats`, `get_today_pnl`, `get_per_bot_summary`, open count.
- `/api/bots` → `get_bot_statuses(PROJECT_ROOT)` + `get_bot_health`.
- `/api/bots/{symbol}` → per-symbol stats (`get_trade_stats(symbol=...)`, `get_consecutive_losses`).
- `/api/trades?symbol=&limit=` → `get_recent_trades`.
- `/api/equity?symbol=` → `get_equity_curve`.
- `/api/daily-pnl?symbol=` → `get_daily_pnl`.
- `/api/ai/calibration` → `get_calibration_data` + `get_calibration_stats`.
- `/api/logs?...` → `get_recent_logs` (reads `trading_bot.log`).
- DataFrames → `df.to_dict(orient='records')` mapped into Pydantic models (NaN → null; round money).

## Rules
- Read-only. Open a fresh sqlite connection per request (or a small pool) in **read-only / WAL** mode;
  never block the bot's writes. `PRAGMA query_only=1`.
- All money/pct rounding done server-side so TS never computes.
- Bind `127.0.0.1` by default; host/port from env (`CCBT_DASH_HOST`, `CCBT_DASH_PORT`).
- Graceful when DB missing/empty → 200 with empty arrays (mirror queries.py guards), never 500.

## Tests (write first) — `tests/test_api_rest.py` (FastAPI TestClient)
- each endpoint returns 200 with the contracted shape against a seeded temp DB;
- empty DB → 200 + empty arrays (no 500);
- symbol filter narrows results;
- NaN/inf serialized as null/"inf" not a crash;
- **parity is correctness-anchored**: assert API JSON == the (fixed) `queries.py` function output. Do NOT
  treat "equals the old function" as the oracle — the old `get_per_bot_summary` was wrong (see §Prereq).

## Acceptance
- All Streamlit data reachable via REST; responses validate against the Pydantic models;
  `get_per_bot_summary` numbers reconcile with `get_closed_trades` per symbol (no orphan leak).

## Result
_(fill on completion)_
