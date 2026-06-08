# CCBT — Crypto & Gold Trading Bot with Claude AI

## Project Overview
Automated trading bot for BTC perpetual futures (Binance/Bybit via ccxt) and Gold XAU/USD (OANDA forex) with Claude AI advisor integration, a Vite + React + FastAPI dashboard, and production deployment infrastructure.

**Language**: Python 3.11 | **Exchanges**: Binance/Bybit (ccxt) + OANDA (forex) | **AI**: Claude Sonnet via Anthropic SDK | **Dashboard**: Vite + React SPA + FastAPI + WebSocket (`api/` + `web/`) | **DB**: SQLite (WAL mode)

## 📚 Documentation Index
Detailed reference lives in `docs/` — read the relevant file when working on that area (keeps this file lean and loaded every session):

| Doc | When to read |
|-----|--------------|
| [docs/strategies.md](docs/strategies.md) | Working on signal logic, BTC/Gold strategy details, the 21 multi-coin techniques, AI advisor, signal scorer |
| [docs/portfolio.md](docs/portfolio.md) | Need the deployed bot roster (which coin runs which strategy/config), deployment profiles, per-profile backtest results |
| [docs/research-history.md](docs/research-history.md) | Reviewing what was tested in Rounds 1-12 / core retest / weak-bot opt (archival — what worked & what failed) |
| [docs/backtest-methodology.md](docs/backtest-methodology.md) | ⚠️ Writing or trusting ANY portfolio backtest — R-multiple rules + 6-point audit checklist |
| [docs/research-pipeline.md](docs/research-pipeline.md) | Running a new research round (`auto_research.py`, `/research` loop) |
| [docs/telegram.md](docs/telegram.md) | Working on Telegram alerts or interactive commands |
| [docs/agents-and-skills.md](docs/agents-and-skills.md) | Using the 11-agent team or the 10 trading skills |

## Architecture

```
main.py                  → Main async trading loop — BTC (entry point, legacy single-bot)
main_multi.py            → Multi-bot single-process runner (shared exchange, 167 bots, ~340 MB RAM)
main_gold.py             → Gold XAU/USD trading loop — OANDA forex
bot/
├── exchange.py          → Bybit/Binance ccxt wrapper (rate limiting, retries)
├── async_exchange.py    → Async ccxt wrapper used by multi-bot runner
├── shared_exchange_pool.py → Shared ccxt exchange instance pool (load_markets() once)
├── mode.py              → Per-bot mode control (NORMAL/GRACEFUL_STOP/TP_ONLY/PANIC)
├── forex_exchange.py    → OANDA forex wrapper (XAU/USD, same interface as exchange.py)
├── strategy.py          → Signal generation (EMA crossover + RSI + ATR + trend filter)
├── data.py              → Technical indicators (EMA, RSI, ATR, volume MA, regime detection)
├── risk.py              → Risk management (position sizing, circuit breakers, cooldowns)
├── engine.py            → Per-bot trading loop (mode reads, signal→order, SL/TP, restore)
├── ai_analyst.py        → Claude AI advisor (nuanced adjustments, not binary gate)
├── context_builder.py   → Market context assembly for AI prompts
├── news_fetcher.py      → RSS/CryptoPanic news integration
├── telegram.py          → Lightweight Telegram alert sender (stdlib only)
├── telegram_commands.py → Interactive Telegram commands (/status /pnl /positions /upnl /panic etc.); /upnl = live uPnL via a thread-isolated ccxt clone (not the shared trading instance)
├── user_data_stream.py  → Realtime TP/SL alerts (CCBT_USERDATA_WS=1): ONE account-wide Binance user-data WS as a TRIGGER (set-only) → wakes the owning bot to verify via check_closed_positions + bounded retry → alert in seconds (vs candle-paced). Dedicated ccxt for listenKey; fail-closed SOCKS (python-socks; socks5h→socks5). Flag-OFF == candle-paced (today). See docs/tickets/realtime-close-alerts/
└── logger.py            → SQLite trade journal + AI calibration tracker + bot_health table
dashboard/
└── queries.py           → SQLite query helpers (symbol-filtered) + log file reader — SINGLE SOURCE OF TRUTH for all dashboard financial math; imported by the FastAPI `api/`. (The old Streamlit app.py + Plotly components.py were retired 2026-06-08.)
api/                     → FastAPI + WebSocket dashboard service; reuses dashboard/queries.py, serves the Vite SPA on one port. Plan/history: docs/tickets/dashboard-rewrite/
web/                     → Vite + React + Tailwind SPA (builds to web/dist, served by FastAPI)
backtest/
├── engine.py            → Event-driven backtesting simulator (own signal dispatch — see strategy checklist)
├── data_loader.py       → Historical data loading
└── metrics.py           → Performance metrics (Sharpe, drawdown, profit factor)
deploy/                  → VPS setup.sh, monitoring.py, backup.sh, nginx.conf, macos/ (launchd), do-proxy/ (SOCKS5 fixed-IP proxy)
research/                → Sweep + verify + portfolio backtest scripts (see docs/research-pipeline.md)
scripts/                 → generate_configs.py, start_all_bots.sh, download_funding_rates.py
tests/                   → pytest unit/integration tests
.claude/
├── agents/              → 11 Agent Team members (see docs/agents-and-skills.md)
├── skills/              → 10 Trading skills
└── settings.json        → Project settings (agent teams enabled)
```

Config files follow naming conventions: `config.json` (BTC Safe default), `config_aggressive/yolo/sniper.json` (BTC variants), `config_gold_forex.json` (XAU/USD OANDA), and auto-generated `config_{coin}usdt_{strategy}.json` (1% risk each). Full roster + per-coin params: [docs/portfolio.md](docs/portfolio.md).

## Key Commands

```bash
# Run all bots (single process, ~340 MB RAM) / stop them
bash scripts/start_all_bots.sh
bash scripts/start_all_bots.sh stop

# Run a named group / specific configs / glob pattern
python main_multi.py --group ichimoku-1h
python main_multi.py --configs config_avax_ichi.json config_near_ichi.json
python main_multi.py --pattern "config_*ichi*.json"

# Dashboard (v2 — FastAPI serves the Vite SPA + API + WS on one port)
.venv-dash/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8501
# (managed in prod by launchd com.ccbt.dashboard-v2; web bundle: npm --prefix web run build)

# Tests
pytest tests/ -v

# Check for timeframe drift (strategy on a TF it wasn't validated on)
python scripts/check_config_timeframes.py --deployed-only

# Track the 4H forward-test cohort (candidates accumulating live trades → 15)
python research/forward_test_report.py

# Single bot (legacy)
python main.py --config config.json
YOLO_MODE=1 python main.py --config config_yolo.json

# VPS setup (Ubuntu 22.04)
sudo bash deploy/setup.sh dashboard.yourdomain.com
```

## Bot Mode Control

Each bot reads a mode file (`data/mode_{symbol}.json`) every loop iteration. The dashboard writes these files; bots react without a restart.

| Mode | Behavior |
|------|----------|
| `NORMAL` | Full trading — entries and exits as configured |
| `GRACEFUL_STOP` | No new entries; close positions at TP or SL as they hit |
| `TP_ONLY` | No new entries; move SL to break-even, let TP close positions |
| `PANIC` | No new entries; close all open positions immediately at market |

- Controlled via dashboard buttons, Telegram commands, or `data/mode_{SYMBOL_CLEAN}.json` files
- Implemented in `bot/mode.py`; read by `bot/engine.py` each tick

## Risk Management (Autonomous Safety Net)
Bot runs fully autonomous — risk management is the primary safety layer:
- 2% base risk per trade (default profile), 9% max daily loss
- 7x max leverage (default), effective leverage ~2-3x typical
- Max 2 concurrent positions, max 5 consecutive losses
- Circuit breakers: daily loss halt, API error halt
- SL verification with 3 retries on exchange (Binance: cancel-recreate pattern)
- Graceful shutdown on SIGINT/SIGTERM (closes all positions)
- Telegram alerts for critical events (informational, no action required)

## Configuration
- `use_testnet: true` must be explicitly changed to go live (selects testnet vs mainnet keys automatically)
- `--config <path>` or `CONFIG_FILE` env var selects config file
- `.env` holds all API keys (Bybit/Binance, Anthropic, Telegram, CryptoPanic, OANDA)
- Full config roster, profiles, and per-coin parameters: [docs/portfolio.md](docs/portfolio.md)

## Development Rules
- **Symbol format**: Always normalize BTCUSDT → BTC/USDT:USDT for ccxt
- **Candle data**: Use iloc[-2] for signals (last closed candle)
- **EMA warmup**: Ensure sufficient bars before generating signals
- **Fees**: Always include commission + slippage in R:R calculations (use full TP distance, not blended partial TP)
- **Risk-first**: Never bypass circuit breakers or skip SL placement
- **AI layer**: Advisor adjustments are multiplied by calibration influence factor
- **Trailing stops**: Must only ratchet in profit direction (up for longs, down for shorts); use wider trail after TP1
- **Graceful shutdown**: Must close all positions and cancel orders on SIGINT/SIGTERM
- **Backtest time**: Always pass simulated time to `can_trade(current_time=...)` — never use wall-clock `time.time()` in backtest
- **Drawdown calc**: Max drawdown denominator must include `initial_balance + peak_cumulative_pnl`, not just peak PnL
- **Flexible cooldown**: `compute_signal_quality_score()` bounds are [0,1]; `min_quality_score` clamped to [0.5,1.0]; consecutive loss cooldown in `risk.py` must never be overridden
- **Adaptive sizing**: `get_tiered_risk()` returns (0,0) for D-grade signals (must skip trade); when disabled, returns (1.0, 1.0) for backward compatibility
- **Pyramiding**: Only add to winning positions when trend aligned (EMA9 vs EMA21); SL must ratchet up (never lower) on pyramid adds; pyramid adds charge commission on the added size; levels 1-7 use explicit config keys (`add_N_atr_mult`, `add_N_size_pct`), levels 8+ use dynamic formula
- **Regime propagation**: `detect_regime()` must be computed per row in backtest (rolling); backtest stores regime in DataFrame for signal-level gating
- **Leverage auto-reduction**: `set_leverage()` returns `int` (actual leverage set); halves on Binance -4028 rejection; `engine.py` captures actual value and updates config for risk manager
- **Position restore**: `engine._restore_positions()` (async) must `await portfolio_manager.register_open(symbol)` for every restored position — otherwise the global position cap and duplicate-coin gate undercount after a restart and the bot opens beyond `--max-positions` / re-opens the same coin
- **Portfolio cap counts by unique coin**: `PortfolioManager.open_count` = `len(_open_coins)` (a set), NOT a separate counter. Several strategy-bots share one netted exchange position per symbol, so `register_open` must be idempotent per coin — a per-call counter double-counts on restart and freezes all entries
- **Shared market-data cache**: NEVER cache positions (close detection is absence-based — a stale snapshot misses SL/TP closes, up to a full candle on 4H bots). Only balance + OHLCV are cached. OHLCV invalidation is WALL-CLOCK (`floor(now,tf)`), keyed on the last closed candle; the forming candle stays at `iloc[-1]`. `SharedMarketData` uses a non-reentrant `threading.Lock` with TINY scope (never held across a fetch). Unit-test fakes must return the real ccxt shape (`fetch_ohlcv` → `list[list]`, not a DataFrame) — add a real-interface smoke test
- **Backtest correctness**: Portfolio backtests must use the R-multiple method — see [docs/backtest-methodology.md](docs/backtest-methodology.md) (past results were inflated 10x by a risk-mismatch bug)

### Adding a New Strategy — Checklist
When adding a new signal type (e.g. `my_new_signal`), ALL of these must be done or live bots will silently produce zero signals:

1. **`bot/data.py`**: Add indicator computation in `add_indicators()` gated by `config.get("signals", {}).get("my_new_signal", {}).get("enabled", False)`
2. **`bot/strategy.py`**: Create `check_my_new_signal_conditions(row, prev_row, config, signal_type)` function
   - If the function does NOT need `prev_row`, add the signal key to the `_no_prev_row` set in `generate_signal()` dispatch block
3. **`bot/strategy.py` dispatch**: Add to `generate_signal()` `new_signals` list:
   ```python
   if signals_config.get("my_new_signal", {}).get("enabled", False):
       new_signals.append(("my_new_signal", check_my_new_signal_conditions))
   ```
   **⚠️ THIS IS THE MOST COMMONLY MISSED STEP** — backtest engine has its own dispatch in `backtest/engine.py` so backtests pass but live bots never trade
4. **`backtest/engine.py`**: Add dispatch in the backtest signal loop (for backtesting)
5. **Config JSON**: Create config with `"signals": {"my_new_signal": {"enabled": true}}` and all required parameters
6. **`research/strategy_meta.json`**: Register the signal type with the timeframe(s) it was research-validated on (`"my_new_signal": {"validated_tf": ["4h"], ...}`). Configs whose `timeframe_signal` isn't in this set are flagged as drift — this is what caught the Awesome Oscillator shipping on 1h when it was only validated on 4h.
7. **Test**: Run one bot with the new config and verify signal generation in logs:
   ```bash
   python main.py --config config_test_newsignal.json
   # Look for: "signal_generated" with "source": "my_new_signal" in logs
   ```

**Timeframe-drift guard**: `python scripts/check_config_timeframes.py [--deployed-only]` scans every config's enabled signal vs its `validated_tf` in `research/strategy_meta.json`. `start.sh` runs `--deployed-only` as a non-blocking pre-flight. Generators can import `validate_config()` to reject a bad config before writing.

### Binance Algo Orders — Rules
- SL/TP are **algo conditional orders** (NOT regular orders) — they live in a separate order book
- Query via `fapiPrivateGetOpenAlgoOrders` (NOT `fetch_open_orders`)
- Cancel via `fapiPrivateDeleteAlgoOrder` with `algoId` (NOT `cancel_order` with `orderId`)
- Response format is `{"orders": [...]}` dict (NOT raw list) — always handle both
- After ANY position close (SL/TP trigger, manual close, panic, shutdown): **must call `cancel_all_orders()`** to clean up orphaned algo orders
- SL/TP size must use `filled_size` (actual fill) not requested size
- SL placement failure must prevent TP placement (never have TP without SL)
- Emergency SL must use `_retry` wrapper (network flicker could leave position unprotected)
- **`close_all_positions()` reduceOnly + fallback**: tries a `reduceOnly` market close first, then **falls back to a plain market order of the EXACT `info['positionAmt']`** (precision-TRUNCATED, so no over-shoot/side-flip) when Binance rejects with **-2022 "ReduceOnly Order is rejected"**. This account rejects reduceOnly market closes, so without the fallback **PANIC + graceful shutdown silently fail to close** (was logging `graceful_shutdown_failed -2022`). Only `-2022`/`reduceonly` errors trigger the fallback; other errors (margin/-2021/network) re-raise. **Must close to flat or RAISE** — the not-flat & hedge-mode (`positionSide != BOTH`) branches raise so PANIC/`_shutdown` callers never record a *phantom* close (book stays in sync with the exchange). Manual one-off close that works on this testnet: plain market order, exact `positionAmt`, one-way mode.

### API Key Safety
- **NEVER** put API keys in code, config files, or chat messages
- Testnet keys: `API_KEY` / `API_SECRET` in `.env`
- Mainnet keys: `MAINNET_API_KEY` / `MAINNET_SECRET_KEY` in `.env`
- Key selection is automatic based on `use_testnet` in config
- If mainnet keys are missing when `use_testnet=false`, bot raises `ValueError` immediately

## Environment Variables
```
# Testnet API keys (used when use_testnet=true)
API_KEY                      — Binance/Bybit testnet API key
API_SECRET                   — Binance/Bybit testnet API secret

# Mainnet API keys (used when use_testnet=false)
MAINNET_API_KEY              — Binance mainnet API key (required for live trading)
MAINNET_SECRET_KEY           — Binance mainnet API secret (required for live trading)

# Services
ANTHROPIC_API_KEY            — Claude AI access
CRYPTOPANIC_TOKEN            — News API (optional)
TELEGRAM_BOT_TOKEN           — Telegram bot token (alerts + commands)
TELEGRAM_CHAT_ID             — Telegram chat ID to send/receive
OANDA_API_TOKEN              — OANDA forex (gold bot)
OANDA_ACCOUNT_ID             — OANDA account ID (gold bot)

# Runtime
CONFIG_FILE                  — Config file path (default: config.json)
YOLO_MODE                    — Set to "1" to enable YOLO validation limits
BOT_DATA_DIR                 — Data directory (default: ./data in Docker, . locally)

# Portfolio Kill-Switch (Tier 1, realized-only) — bot/portfolio_killswitch.py
CCBT_KILL_SWITCH             — Set to "1" to enable the portfolio kill-switch monitor.
                               Default OFF == byte-for-byte today (no monitor task, no
                               mode-file writes, can_open() unchanged).
CCBT_KILL_HALT_PCT           — Realized loss % threshold for Tier 1 halt (default: 6.0,
                               meaning -6% of start-of-day equity). NOTE: -6% is NOT
                               validated against CCBT's equity-curve drawdown distribution.
                               Set conservatively (8-10%) until the equity-curve replay
                               (Open Decision 1) is completed. NEVER use the default live
                               without that measurement.
CCBT_KILL_CONFIRM_M          — M in M-of-N hysteresis: how many of the last N samples must
                               breach the threshold before tripping (default: 3).
CCBT_KILL_CONFIRM_N          — N in M-of-N hysteresis: sliding window size (default: 4).
                               Loop period is 15s; with N=4, the window covers ~60s.

# Kill-Switch Design Notes
# - Tier 1 ONLY in this build: in-process is_halted gate, NO mode-file writes.
#   Writing GRACEFUL_STOP to a flat bot permanently kills its coroutine (engine.py:961
#   breaks when _tracked_trades is empty). The entry gate (can_open()→False) is the
#   only Tier 1 action.
# - Realized-only: metric = get_today_realized_by_close() bucketed by CLOSE time
#   (close_timestamp column), not open time. Trades opened yesterday but closed today
#   at a loss correctly count on today's Bangkok day.
# - Equity denominator = totalWalletBalance (realized equity incl. locked margin) from
#   Binance USDT-M fetch_balance()["info"]["totalWalletBalance"]. NOT free balance.
# - Bangkok day boundary (GMT+7): matches dashboard and get_today_realized_by_close().
#   Per-bot reset_daily uses UTC — 7h divergence window. Documented gap; sized via
#   Open Decision 1.
# - Restart persistence: data/portfolio_halt.json (bangkok_date keyed). Same-day
#   restart restores is_halted=True for Tier 1. start_of_day_equity back-calculated
#   as current_equity - realized_today_by_close() on mid-day restart.
# - No Tier 2 (FLATTEN), no PANIC mode files, no unrealized metric in this build.
```

**Key selection is automatic**: `use_testnet: true` → testnet keys, `use_testnet: false` → mainnet keys. Bot raises `ValueError` if mainnet keys are missing.

## Database Schema (trades.db)
- `trades` — Full trade lifecycle (open → close with PnL, AI decision, close reason). `close_reason` is
  enriched (dashboard-engine-followups): `tp` / `stop_loss` (hard SL, loss) / `trail_stop` (stop that
  ratcheted into profit — a winning exit) / `breakeven` (pnl≥0 near entry) / `panic` / `graceful_shutdown`
  / `orphan_reconcile`. Classification is by **pnl sign first** (a loss is never `trail_stop`/`breakeven`).
  `close_timestamp` (TEXT, UTC ISO) — populated by `log_trade_close()`; NULL for pre-migration rows (fallback
  to open-time `timestamp`). Used by `get_today_realized_by_close()` to bucket by CLOSE time in Bangkok tz.
- `ai_calibration` — AI decision outcomes for accuracy tracking
- `bot_health` — Per-bot live status (position, errors, loop count, mode, PnL)
- `bot_ohlcv` — Recent OHLCV+indicators (ema9/ema21/rsi14) per symbol/timeframe, written by the bot from
  candles it already fetched (NO extra exchange call); read by the v2 `/api/candles` endpoint. Bounded/upserted.
- All `trades.db` writers set `PRAGMA busy_timeout=5000` (shared `_apply_pragmas`) — ~59 bots burst-write at
  the candle boundary, so contended writers wait rather than fail with "database is locked".

## Deployment
- **All bots in a single process** via `main_multi.py` (shared ccxt exchange pool); ~59 active configs as of 2026-06-07 (roster lives in `deploy/macos/start.sh`)
- **RAM**: ~340 MB total (vs ~25 GB if running each bot as a separate Docker container)
- **Start/stop**: `bash scripts/start_all_bots.sh` / `bash scripts/start_all_bots.sh stop`
- **Dashboard**: **Vite + React SPA + FastAPI + WebSocket** (`api/` serves the `web/dist` bundle + REST + WS on one port). Managed by launchd `com.ccbt.dashboard-v2` on **127.0.0.1:8501** (`deploy/macos/start-dashboard-v2.sh`, `.venv-dash` Python 3.12); restart with `launchctl kickstart -k gui/$(id -u)/com.ccbt.dashboard-v2`. The old Streamlit dashboard was **retired + deleted 2026-06-08** (cutover N12/N13 complete); `dashboard/queries.py` is kept as the shared data layer. Rebuild the web bundle after a `web/` change: `npm --prefix web run build`. Plan/history: [docs/tickets/dashboard-rewrite/README.md](docs/tickets/dashboard-rewrite/README.md).
- **Per-bot mode control**: mode files in `data/mode_{symbol}.json` (read each loop tick). The dashboard now
  has UI controls wired to the token-gated `api/routers/control.py` endpoints: per-bot mode buttons on
  BotDetailPage + global STOP-ALL/PANIC-ALL/RESUME-ALL on Portfolio (PANIC behind a confirm dialog). BotRow
  serves `mode` UPPERCASE + `error_count` (drives the portfolio alert banner). UI plan + per-batch status:
  [docs/tickets/dashboard-ui-improvements/README.md](docs/tickets/dashboard-ui-improvements/README.md).
- **Realtime uPnL** (`api/markprice.py`): public Binance markPrice WS (no API key) broadcasts `type:"upnl"`
  frames on the same `/ws` channel — per-position unrealized PnL + server-computed `dist_to_stop_pct` /
  `rr_remaining` (shown in UpnlPanel) + `feed_status` (live/stale/offline) + `today_realized` + `net_today`
  (= today_realized + total_upnl, drives the **Today Net PnL** header card; net is computed in Python, never
  re-summed in TS). A `_stale_rebroadcast_loop` re-emits the payload every ~STALE_AFTER_S/2 so a SILENT feed
  freeze (WS up, no frames) still flips the card to `feed_status:'stale'` → offline fallback (no frozen
  "live" net). never-interfere: the dashboard never calls the trading account.
- **Today's PnL is Bangkok (GMT+7)**: `dashboard.queries.get_today_pnl()` groups on
  `DATE(timestamp,'+7 hours') = DATE('now','+7 hours')` (timestamps are stored UTC). It feeds both
  `PortfolioSummaryResponse.today_pnl` (REST) and the markPrice `today_realized`. **The WS `_build_snapshot`
  portfolio payload (`api/ws.py`) MUST carry the full REST shape (incl. `today_pnl` + `notional`)** — it
  overwrites the `['portfolio','summary']` cache in `useLiveSnapshot`, so any field it omits flips to
  `—` a few seconds after load.
- **Realtime TP/SL alerts** (`CCBT_USERDATA_WS=1`, `bot/user_data_stream.py`): ON for testnet
  (`deploy/macos/start.sh`), **OFF for mainnet** until proxied-WS egress is validated. A single account-wide
  Binance user-data WS is a TRIGGER only (set-only; never alerts/DB/PnL); on an `ACCOUNT_UPDATE pa==0` /
  SL-TP `ORDER_TRADE_UPDATE FILLED` it wakes the owning bot, which VERIFIES via the unchanged
  `check_closed_positions` (absence in real position state) with a bounded retry, then alerts — within
  seconds instead of waiting for the next candle. Flag-OFF == candle-paced (the per-bot loop still detects
  closes once per candle as the backstop). Needs `python-socks` (in requirements) for the SOCKS-proxied WS;
  the WS fails CLOSED (disabled, candle backstop) if the proxy is set but unusable. Design + PR history:
  [docs/tickets/realtime-close-alerts/README.md](docs/tickets/realtime-close-alerts/README.md).
- **Exchange**: Binance testnet (set `use_testnet: false` in configs to go live)
- Docker deployment still supported for VPS: `docker compose up -d --build`
- Nginx reverse proxy (HTTPS, basic auth, rate limiting) for VPS dashboard
- Monthly cost: ~$7-17 (Hetzner VPS + Claude API)
- **Fixed-IP proxy for Binance**: exchange traffic can route through a SOCKS5 tunnel to a DigitalOcean droplet so Binance sees a whitelistable egress IP. Enable with env `CCBT_SOCKS_PROXY=socks5h://127.0.0.1:1080` (read in `bot/exchange.py` + `bot/shared_exchange_pool.py`); `deploy/macos/start.sh` runs `deploy/do-proxy/check-proxy.sh` pre-flight and refuses to start on IP mismatch. Setup + ops: [deploy/do-proxy/README.md](deploy/do-proxy/README.md). Binance API is IPv4-only, so egress can't leak over IPv6.
- **Shared market-data cache** (`CCBT_SHARED_MARKETDATA=1`): `SharedMarketData` (`bot/shared_exchange_pool.py`) caches **balance** (30s) + **OHLCV** (wall-clock candle-boundary) shared across all bots, cutting the per-bot API fan-out that caused Binance -1003/418 rate-limit bursts. **Positions are never cached** (absence-based close detection); routine fetches are gated on the bot's own `_tracked_trades`. Flag-OFF = byte-for-byte original behaviour. Plan + design: [docs/plans/shared-market-data-refactor.md](docs/plans/shared-market-data-refactor.md).
- macOS: managed by launchd (`com.ccbt.trading-bot` → `deploy/macos/start.sh`); restart with `launchctl kickstart -k gui/$(id -u)/com.ccbt.trading-bot`.
  - **Restart behavior / gotcha**: `kickstart -k` sends SIGTERM → `engine._shutdown()` cancels orders + **closes all open positions at market**. But launchd's SIGTERM→SIGKILL window is short, so closing many positions + tearing down ~59 bots may not finish before SIGKILL → positions are **left open on the exchange** and re-adopted by the new process via `_restore_positions()`. So a restart is NOT a guaranteed flat-restart. To truly flatten first, close positions before restarting; to apply a code change WITHOUT disrupting positions, expect them to be restored. Engine code changes only take effect on restart (Python doesn't hot-reload).
  - If a position is closed on the exchange while the bot isn't tracking it (e.g. closed during a partial shutdown), its DB row can be left `open` (an orphan). The owning bot reconciles it via absence detection on its next candle if still tracking; otherwise reconcile manually (`UPDATE trades SET status='closed', close_reason='orphan_reconcile', pnl=COALESCE(pnl,0) WHERE …`) — dashboard/queries already exclude `orphan_reconcile`.
- Deployment profiles (testnet → real-test → real-safe → real-grow → real-full): [docs/portfolio.md](docs/portfolio.md)

## API Testing
- `tests/test_api_binance_testnet.py` — 17-endpoint test suite for Binance testnet
- Tests all API calls: markets, leverage, balance, OHLCV, ticker, funding, OI, orderbook, long/short round-trip, SL/TP, trade history, order cleanup
- Run: `python tests/test_api_binance_testnet.py`

## Test Safety
- **NEVER let tests send real Telegram.** Tests that drive the engine close path (`check_closed_positions`)
  call `bot.engine.send_alert`; if `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are in the env (e.g. the dev
  `.env`), fixture closes get delivered to the real chat (this happened — a flood of bogus
  "BUY closed (trail_stop/breakeven/...)" alerts at round fixture PnLs). `tests/conftest.py` has an
  **autouse** fixture that blanks the telegram creds + no-ops `send_alert` in `bot.telegram`/`bot.engine`/
  `main_multi` for every test. **Do not remove it**, and don't write a test that asserts a real send.
