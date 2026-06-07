# CCBT — Crypto & Gold Trading Bot with Claude AI

## Project Overview
Automated trading bot for BTC perpetual futures (Binance/Bybit via ccxt) and Gold XAU/USD (OANDA forex) with Claude AI advisor integration, Streamlit dashboard, and production deployment infrastructure.

**Language**: Python 3.11 | **Exchanges**: Binance/Bybit (ccxt) + OANDA (forex) | **AI**: Claude Sonnet via Anthropic SDK | **Dashboard**: Streamlit + Plotly | **DB**: SQLite (WAL mode)

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
├── telegram_commands.py → Interactive Telegram commands (/status /pnl /panic etc.)
└── logger.py            → SQLite trade journal + AI calibration tracker + bot_health table
dashboard/
├── app.py               → Streamlit web UI (multi-bot portfolio + single-bot drill-down)
├── components.py        → Plotly chart components (per-bot PnL bar, portfolio table)
└── queries.py           → SQLite query helpers (symbol-filtered) + log file reader
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

# Dashboard
streamlit run dashboard/app.py

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
```

**Key selection is automatic**: `use_testnet: true` → testnet keys, `use_testnet: false` → mainnet keys. Bot raises `ValueError` if mainnet keys are missing.

## Database Schema (trades.db)
- `trades` — Full trade lifecycle (open → close with PnL, AI decision, close reason)
- `ai_calibration` — AI decision outcomes for accuracy tracking
- `bot_health` — Per-bot live status (position, errors, loop count, mode, PnL)

## Deployment
- **All bots in a single process** via `main_multi.py` (shared ccxt exchange pool); ~59 active configs as of 2026-06-07 (roster lives in `deploy/macos/start.sh`)
- **RAM**: ~340 MB total (vs ~25 GB if running each bot as a separate Docker container)
- **Start/stop**: `bash scripts/start_all_bots.sh` / `bash scripts/start_all_bots.sh stop`
- **Dashboard**: Streamlit local on port 8501 (`streamlit run dashboard/app.py`)
- **Per-bot mode control**: mode files in `data/mode_{symbol}.json` (read each loop tick)
- **Exchange**: Binance testnet (set `use_testnet: false` in configs to go live)
- Docker deployment still supported for VPS: `docker compose up -d --build`
- Nginx reverse proxy (HTTPS, basic auth, rate limiting) for VPS dashboard
- Monthly cost: ~$7-17 (Hetzner VPS + Claude API)
- **Fixed-IP proxy for Binance**: exchange traffic can route through a SOCKS5 tunnel to a DigitalOcean droplet so Binance sees a whitelistable egress IP. Enable with env `CCBT_SOCKS_PROXY=socks5h://127.0.0.1:1080` (read in `bot/exchange.py` + `bot/shared_exchange_pool.py`); `deploy/macos/start.sh` runs `deploy/do-proxy/check-proxy.sh` pre-flight and refuses to start on IP mismatch. Setup + ops: [deploy/do-proxy/README.md](deploy/do-proxy/README.md). Binance API is IPv4-only, so egress can't leak over IPv6.
- **Shared market-data cache** (`CCBT_SHARED_MARKETDATA=1`): `SharedMarketData` (`bot/shared_exchange_pool.py`) caches **balance** (30s) + **OHLCV** (wall-clock candle-boundary) shared across all bots, cutting the per-bot API fan-out that caused Binance -1003/418 rate-limit bursts. **Positions are never cached** (absence-based close detection); routine fetches are gated on the bot's own `_tracked_trades`. Flag-OFF = byte-for-byte original behaviour. Plan + design: [docs/plans/shared-market-data-refactor.md](docs/plans/shared-market-data-refactor.md).
- macOS: managed by launchd (`com.ccbt.trading-bot` → `deploy/macos/start.sh`); restart with `launchctl kickstart -k gui/$(id -u)/com.ccbt.trading-bot`.
- Deployment profiles (testnet → real-test → real-safe → real-grow → real-full): [docs/portfolio.md](docs/portfolio.md)

## API Testing
- `tests/test_api_binance_testnet.py` — 17-endpoint test suite for Binance testnet
- Tests all API calls: markets, leverage, balance, OHLCV, ticker, funding, OI, orderbook, long/short round-trip, SL/TP, trade history, order cleanup
- Run: `python tests/test_api_binance_testnet.py`
