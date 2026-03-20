# CCBT — Crypto & Gold Trading Bot with Claude AI

## Project Overview
Automated trading bot for BTC perpetual futures (Binance/Bybit via ccxt) and Gold XAU/USD (OANDA forex) with Claude AI advisor integration, Streamlit dashboard, and production deployment infrastructure.

**Language**: Python 3.11 | **Exchanges**: Binance/Bybit (ccxt) + OANDA (forex) | **AI**: Claude Sonnet via Anthropic SDK | **Dashboard**: Streamlit + Plotly | **DB**: SQLite (WAL mode)

## Architecture

```
main.py                  → Main async trading loop — BTC (entry point)
main_gold.py             → Gold XAU/USD trading loop — OANDA forex
bot/
├── exchange.py          → Bybit/Binance ccxt wrapper (rate limiting, retries)
├── forex_exchange.py    → OANDA forex wrapper (XAU/USD, same interface as exchange.py)
├── strategy.py          → Signal generation (EMA crossover + RSI + ATR + trend filter)
├── data.py              → Technical indicators (EMA, RSI, ATR, volume MA, regime detection)
├── risk.py              → Risk management (position sizing, circuit breakers, cooldowns)
├── ai_analyst.py        → Claude AI advisor (nuanced adjustments, not binary gate)
├── context_builder.py   → Market context assembly for AI prompts
├── news_fetcher.py      → RSS/CryptoPanic news integration
└── logger.py            → SQLite trade journal + AI calibration tracker
dashboard/
├── app.py               → Streamlit web UI (auto-refresh 30s, live log viewer)
├── components.py        → Plotly chart components
└── queries.py           → SQLite query helpers + log file reader
backtest/
├── engine.py            → Event-driven backtesting simulator
├── data_loader.py       → Historical data loading
└── metrics.py           → Performance metrics (Sharpe, drawdown, profit factor)
deploy/
├── setup.sh             → Full VPS setup (Docker, nginx, SSL, firewall)
├── monitoring.py        → Health checks + Telegram alerts
├── backup.sh            → SQLite backup with retention
└── nginx.conf           → Reverse proxy config
tests/                   → pytest unit/integration tests
.claude/
├── agents/              → 7 Agent Team members (pm, sa, backend-dev, frontend-dev, devops, trader-expert, crypto-expert)
├── skills/              → 10 Trading skills (technical-analyst, backtest-expert, position-sizer, etc.)
└── settings.json        → Project settings (agent teams enabled)
```

## Key Commands

```bash
# Run bot (testnet by default, safe config)
python main.py

# Run bot with YOLO config (testnet only)
YOLO_MODE=1 python main.py --config config_yolo.json

# Run dashboard
streamlit run dashboard/app.py

# Run tests
pytest tests/ -v

# Docker deployment (safe)
docker compose up -d --build

# Docker deployment (YOLO, testnet only)
CONFIG_FILE=config_yolo.json YOLO_MODE=1 docker compose up -d --build

# VPS setup (Ubuntu 22.04)
sudo bash deploy/setup.sh dashboard.yourdomain.com
```

## Trading Strategy (Champion v2 — verified, no look-ahead bias)
- **Signal**: EMA(9)/EMA(21) crossover + EMA(5/13) fast crossover on 15m + EMA(50) trend filter on 1h
- **Confirmation**: RSI(14) directional ranges (long 45-65, short 35-55), volume > 1.3×MA(20), ATR >= minimum, EMA slope >= 0.02%
- **Entries**: Uses iloc[-2] (last closed candle, not forming candle)
- **SL/TP**: ATR-based (SL=1.0×ATR, TP=3.0×ATR, R:R=3:1), trailing stop 2.0×ATR
- **Pyramiding**: Disabled | **Partial TP**: Disabled | **Cooldown**: None
- **MTD Accelerator**: OFF by default (best for trending markets). Enable via `config_yolo.json` for bear markets. Moderate tiers: +15%→2.0x, +5%→1.5x, flat→1.0x, -20%→0.7x, worse→0.5x.
- **Trading Hours**: 03:00-20:00 UTC | **Weekend**: Off | **Regime**: Skip ranging
- **Body Dominance / Squeeze Release**: Implemented but disabled — showed PF 2.06 but was look-ahead bias (1H candle not yet closed). With proper lag, PF drops to 0.90. Code retained for future use if a non-biased version is found.

## Gold Trading Strategy (XAU/USD — research complete, pending implementation)

Three strategies validated on 2.4yr XAU/USD 1H data (simple simulator):

### 1. Ichimoku Cloud + Trailing (BEST — PF 2.02, +46%/yr, DD 8.8%)
- **Signal**: Tenkan(9) crosses Kijun(26) above Cloud = long, below = short
- **Exit**: Trailing stop 3.0×ATR (no fixed TP — gold trends run far)
- **SL**: 2.5×ATR (wider than BTC — gold has wider intraday swings)
- **Hours**: 08:00-20:00 UTC only (London+NY, skip Asian noise)
- **Long-only variant**: PF 2.01, +56%/yr, DD 12%

### 2. Momentum Long-Only (PF 2.70, +62%/yr, DD 14%)
- **Signal**: ROC(10) > 1.2% + price above EMA21 = long only
- **Exit**: SL 2.5×ATR, TP 5.0×ATR
- 29 trades/yr, highest PF but lowest frequency

### 3. EMA(12/26) + Volume + Trail (PF 1.52, +18%/yr, DD 24%)
- Most similar to BTC strategy, easiest to implement

### Key differences Gold vs BTC
| Gold | BTC |
|------|-----|
| Trail stop >> fixed TP | Fixed TP better |
| Long-only bias works (+136% in 2.4yr) | Both sides work |
| SL 2.5 ATR (wider) | SL 1.0 ATR (tighter) |
| Ichimoku = best indicator | EMA crossover = best |
| Hours 8-20 UTC critical | Hours 3-20 UTC |
| Mean reversion fails completely | Same |

### Forex Integration
- `bot/forex_exchange.py` — OANDA wrapper (same interface as BybitClient)
- `main_gold.py` — separate entry point for gold
- `config_gold_forex.json` — OANDA config (H1 signal, H4 trend, XAU_USD)
- Requires: OANDA_API_TOKEN + OANDA_ACCOUNT_ID in .env

## AI Advisor Layer
- **Mode**: "advisor" — provides nuanced adjustments, NOT binary gate
- **Adjustments**: position_size_modifier (0.5-1.5), sl_adjustment, tp_adjustment
- **Calibration**: Rolling accuracy tracking, auto-adjusts AI influence
- **Timeout**: 10s with fallback to execute without AI
- **Model**: claude-sonnet-4-6

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
- `config.json` — Safe profile (default, suitable for mainnet)
- `config_aggressive.json` — Aggressive profile (5% risk, 10x leverage)
- `config_yolo.json` — YOLO-lite profile (10% risk, 20x lev, requires `YOLO_MODE=1`)
- `config_sniper.json` — Sniper profile (tight RSI, fewer but higher quality trades)
- `config_doge.json` — DOGE/USDT (3% risk, slope=0.01, atr_min=0)
- `config_arb.json` — ARB/USDT (3% risk, atr_min=0)
- `config_wif.json` — WIF/USDT (3% risk, atr_min=0)
- `.env` — API keys (Bybit/Binance, Anthropic, Telegram, CryptoPanic)
- `use_testnet: true` must be explicitly changed to go live
- `--config <path>` or `CONFIG_FILE` env var selects config file

## Profiles (5yr backtest, $1,000 start, verified no look-ahead bias)

| Profile | File | Risk | Lev | 5yr | /yr | PF | DD | Trades |
|---------|------|------|-----|-----|-----|-----|-----|--------|
| **Safe** | `config.json` | 2% | 7x | +72% | ~11% | 1.62 | 17% | 103 |
| **Aggressive** | `config_aggressive.json` | 5% | 10x | +158% | ~21% | 1.53 | 15% | 103 |
| **YOLO (bear mode)** | `config_yolo.json` | 10% | 25x | +78%/yr | 1.41 | 32% | MTD ON |
| **MAX** | `config_max.json` | 15% | 25x | +33%/yr | 1.29 | 36% | MTD OFF |
| **Sniper** | `config_sniper.json` | 2% | 7x | +12%/yr | 1.62 | 17% | MTD OFF |

Default = R10%/L25x NO MTD (+115%/yr recent, +40%/yr 5yr average).
YOLO config = same params but MTD ON — switch to this during bear/sideways markets.

All profiles share: EMA(9/21)+EMA(5/13), SL=1.0 ATR, TP=3.0 ATR, RSI 45-65/35-55, no pyramiding, hours 3-20 UTC.

```bash
# Deploy profiles (BTC)
python main.py                                           # BTC Safe (default)
python main.py --config config_aggressive.json           # BTC Aggressive
YOLO_MODE=1 python main.py --config config_yolo.json     # BTC YOLO (testnet only)
python main.py --config config_sniper.json               # BTC Sniper

# Deploy Gold bot (runs alongside BTC)
YOLO_MODE=1 python main.py --config config_gold.json     # XAU/USDT

# Deploy altcoin bots (multi-coin day trading portfolio)
YOLO_MODE=1 python main.py --config config_doge.json     # DOGE (3% risk)
YOLO_MODE=1 python main.py --config config_arb.json      # ARB (3% risk)
YOLO_MODE=1 python main.py --config config_wif.json      # WIF (3% risk)

# Run all 4 crypto bots simultaneously (day trading portfolio)
YOLO_MODE=1 python main.py --config config.json &          # BTC (5% risk)
YOLO_MODE=1 python main.py --config config_doge.json &     # DOGE (3% risk)
YOLO_MODE=1 python main.py --config config_arb.json &      # ARB (3% risk)
YOLO_MODE=1 python main.py --config config_wif.json &      # WIF (3% risk)
```

## Multi-Coin Day Trading Portfolio (verified 2yr backtest)

| Coin | Config | Risk | PF | Sharpe | Tr/yr | Notes |
|------|--------|------|-----|--------|-------|-------|
| **BTC** | `config.json` | 5% | 1.51 | 1.77 | 38 | Anchor, highest PF |
| **DOGE** | `config_doge.json` | 3% | 1.50 | 1.54 | 29 | slope=0.01, atr_min=0 |
| **ARB** | `config_arb.json` | 3% | 1.27 | 0.96 | 21 | Fragile edge, don't tune |
| **WIF** | `config_wif.json` | 3% | 1.21 | 0.86 | 33 | Meme coin momentum |

Total: ~121 trades/yr across 4 coins. Max simultaneous exposure: 14%.

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

## Environment Variables
```
API_KEY, API_SECRET          — Bybit/Binance API credentials
ANTHROPIC_API_KEY            — Claude AI access
CRYPTOPANIC_TOKEN            — News API (optional)
TELEGRAM_BOT_TOKEN/CHAT_ID  — Monitoring alerts (optional)
CONFIG_FILE                  — Config file path (default: config.json)
YOLO_MODE                    — Set to "1" to enable YOLO validation limits
```

## Database Schema (trades.db)
- `trades` — Full trade lifecycle (open → close with PnL, AI decision, close reason)
- `ai_calibration` — AI decision outcomes for accuracy tracking

## Deployment
- Docker containers (bot + dashboard) with shared volume
- Nginx reverse proxy (HTTPS, basic auth, rate limiting)
- Systemd timers for monitoring (5min) and backup (daily)
- fail2ban + UFW firewall
- Monthly cost: ~$7-17 (Hetzner VPS + Claude API)

## Agent Team

7 specialized agents configured as teammates that can collaborate via shared task lists and direct messaging. Enable via `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (already set in `.claude/settings.json`).

### Team Structure
```
              ┌──────────┐
              │    PM     │ Product Owner — roadmap, priorities, features
              │  sonnet   │
              └─────┬─────┘
         ┌──────────┼──────────┐
         ▼          ▼          ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │    SA    │ │  Trader  │ │  Crypto  │
   │   opus   │ │  Expert  │ │  Expert  │
   │ Arch.    │ │ Strategy │ │ Domain   │
   └────┬─────┘ └──────────┘ └──────────┘
   ┌────┼──────────┬──────────┐
   ▼    ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Backend  │ │ Frontend │ │  DevOps  │
│   Dev    │ │   Dev    │ │          │
│  sonnet  │ │  sonnet  │ │  sonnet  │
└──────────┘ └──────────┘ └──────────┘
```

### Agent Files: `.claude/agents/`
| Agent | Model | Role | Key Skills |
|-------|-------|------|------------|
| `pm` | sonnet | Product Manager | scenario-analyzer, trader-memory-core |
| `sa` | opus | Solution Architect | backtest-expert, edge-pipeline-orchestrator |
| `backend-dev` | sonnet | Backend Developer | crypto-signal-validator, position-sizer, backtest-expert |
| `frontend-dev` | sonnet | Frontend Developer | technical-analyst |
| `devops` | sonnet | DevOps Engineer | (infra focused) |
| `trader-expert` | opus | Trader Expert | 7 skills (TA, backtest, sizing, regime, pivot, validator, memory) |
| `crypto-expert` | opus | Crypto Expert | macro-regime-detector, market-news-analyst, scenario-analyzer, technical-analyst |

### Usage
```bash
# Create a team for a task
"Create an agent team with pm, sa, backend-dev to implement Phase 7"

# @mention specific agent
@trader-expert "analyze trailing stop performance"

# Run whole session as an agent
claude --agent trader-expert
```

## Skills (`.claude/skills/`)

10 installed skills adapted for crypto perpetual futures:

### Auto-Invoked (Claude uses when relevant)
| Skill | Command | Purpose |
|-------|---------|---------|
| technical-analyst | `/technical-analyst` | Chart analysis with EMA/RSI/ATR |
| backtest-expert | `/backtest-expert` | Strategy validation + stress testing |
| position-sizer | `/position-sizer` | Position sizing for futures + leverage |
| macro-regime-detector | `/macro-regime-detector` | Cross-asset macro regime detection |
| market-news-analyst | `/market-news-analyst` | Crypto news impact analysis |
| trader-memory-core | `/trader-memory-core` | Thesis lifecycle tracking |
| crypto-signal-validator | `/crypto-signal-validator` | Multi-layer signal validation (custom) |

### Manual-Only (invoke with /command)
| Skill | Command | Purpose |
|-------|---------|---------|
| research | `/research` | Iterative strategy optimization loop (agent team + backtest) |
| scenario-analyzer | `/scenario-analyzer "event"` | 18-month scenario projections |
| strategy-pivot-designer | `/strategy-pivot-designer` | Strategy stagnation diagnosis + pivots |
| edge-pipeline-orchestrator | `/edge-pipeline-orchestrator` | Full strategy development pipeline |

Sources: [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills), [SkillsMP](https://skillsmp.com/)

## Research Methodology (`/research`)

Iterative optimization loop proven to improve strategy from 10%/yr to 523%/yr:

### Loop
1. **Baseline** — backtest current config, record metrics
2. **Agent team analysis** — spawn trader-expert + SA in parallel to identify bottlenecks and propose changes
3. **Isolate & test** — sweep each change individually, then combine winners
4. **Implement** — backend-dev agent codes changes, run tests
5. **Verify** — disabled = no regression, enabled = improvement confirmed
6. **Iterate or stop** — repeat until target met or diminishing returns

### Rules
- Test one variable at a time before combining
- Full 2-year dataset, no cherry-picking periods
- Check per-signal-source breakdown (combined PF can hide bad sources)
- More trades with lower PF = worse (fee drag eats edge)
- Always compare vs baseline, not vs previous round

### Proven Findings (BTC 15m)
**Works**: Pyramiding (4x PnL), adaptive sizing, 10x leverage + SL ratcheting, dual EMA crossover, regime-adaptive trail, trading hours filter
**Doesn't work**: Mean reversion, MACD, EMA pullback, RSI divergence, weekend trading, looser filters, higher risk alone
**YOLO-specific**: Adaptive sizing OFF at high leverage (5x better), lower volume threshold 0.7x (80% more trades), wider SL 1.65 ATR, TP 4.0 + trail 5.0

## API Testing
- `tests/test_api_binance_testnet.py` — 17-endpoint test suite for Binance testnet
- Tests all API calls: markets, leverage, balance, OHLCV, ticker, funding, OI, orderbook, long/short round-trip, SL/TP, trade history, order cleanup
- Run: `python tests/test_api_binance_testnet.py`
