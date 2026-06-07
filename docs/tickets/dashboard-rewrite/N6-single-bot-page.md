# N6 — Single-Bot Drilldown Page

**Phase C · est. L (2 days)**

## Goal
Per-symbol view: price/indicators, trade log, performance stats, risk monitor.

## 🔴 Candle source (must-fix #5 — never call the exchange from the dashboard)
The dashboard process must NEVER call the exchange: it adds to the rate-limit budget the shared cache
exists to protect (-1003/418 history), egresses from the wrong IP (start.sh enforces the fixed-IP SOCKS
proxy), and a sync ccxt call **blocks the async event loop** (stalls all WS/REST). Two allowed options:
- **(preferred) persisted OHLCV**: have the bot write the candles it already fetches to a small DB table
  (recommended in README); `/api/candles` reads that table read-only. Bonus: the same persistence gives
  the risk gauge a real balance (kills the `*50` hack — app.py:751).
- **(fallback) drop live candles** entirely (Streamlit already degrades gracefully to "chart unavailable").
- If live candles are ever truly required, it's a SEPARATE ticket needing explicit go-ahead:
  `run_in_executor` + route through the SOCKS proxy + a hard call cap — NOT a Phase-C 'L' item.

## Scope (parity with `dashboard/app.py` single-bot branch)
- **Price chart**: candlesticks + EMA9/EMA21 + trade entry/exit markers via **lightweight-charts**; RSI
  subchart below. OHLCV+indicators from `/api/candles` (persisted source above) — no TA in TS. Exit
  markers placed at `timestamp + duration_seconds` (both columns exist), NOT Streamlit's known-wrong
  entry-time placement (components.py:163, `x=[entry_time]` for the exit marker).
- **Trade Log** table (duration, ai_decision, close_reason). Drop `signal_source` (absent from schema).
- **Performance Stats**: total trades, WR, W/L, PF, avg win/loss, max DD, Sharpe, total PnL — all from
  `/api/bots/{symbol}` (server-computed).
- **Risk Monitor**: daily-loss gauge (only if a real balance is persisted; else show absolute $ and omit
  the % gauge), leverage/risk/RR readout, consecutive-losses progress, circuit-breaker dots.

## Tests (write first)
- `/api/candles` reads persisted OHLCV (mock table); returns `{available:false}` when none persisted;
  **a test asserts the candles endpoint never imports/constructs a ccxt client / never hits the network.**
- React: candle chart + markers render from mock; RSI renders; stats panel maps fields; risk dots map
  thresholds; empty/unavailable states covered.

## Acceptance
- Drilldown matches Streamlit single-bot view; chart updates live; no TA math in TS; **no exchange call
  from the dashboard process**.

## Result
_(fill on completion)_
