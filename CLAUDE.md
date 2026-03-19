# CCBT — Crypto Trading Bot with Claude AI

## Project Overview
Automated cryptocurrency perpetual futures trading bot for Bybit exchange with Claude AI advisor integration, Streamlit dashboard, and production deployment infrastructure.

**Language**: Python 3.11 | **Exchange**: Bybit (ccxt) | **AI**: Claude Sonnet via Anthropic SDK | **Dashboard**: Streamlit + Plotly | **DB**: SQLite (WAL mode)

## Architecture

```
main.py                  → Main async trading loop (entry point)
bot/
├── exchange.py          → Bybit ccxt wrapper (rate limiting, retries, symbol normalization)
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
# Run bot (testnet by default)
python main.py

# Run dashboard
streamlit run dashboard/app.py

# Run tests
pytest tests/ -v

# Docker deployment
docker compose up -d --build

# VPS setup (Ubuntu 22.04)
sudo bash deploy/setup.sh dashboard.yourdomain.com
```

## Trading Strategy
- **Signal**: EMA(9)/EMA(21) crossover + EMA(5/13) fast crossover on 15m + EMA(50) trend filter on 1h
- **Confirmation**: RSI(14) directional ranges (long 48-68, short 30-52), volume > MA(20), ATR >= minimum, EMA slope filter
- **Entries**: Uses iloc[-2] (last closed candle, not forming candle)
- **SL/TP**: ATR-based (SL=1.2×ATR, TP=3.0×ATR, Trail=2.0×ATR, post-TP1 trail=3.0×ATR)
- **Partial TP**: 30% closed at TP1 (2×ATR), SL moves to breakeven + 0.5×ATR buffer, remaining 70% runs to full TP
- **R:R**: Minimum 1.0 net R:R after commission (0.04%) + slippage (0.015%)
- **Adaptive Sizing**: Signal quality score (0-1) determines position size tier — A-grade (>=0.75) gets 2x risk + 1.5x leverage, B-grade (>=0.5) normal, C-grade (>=0.3) half risk, D-grade skipped
- **Pyramiding**: Up to 5 adds into winning positions — +100% at 0.8×ATR, +75% at 1.5×ATR, +50% at 2.2×ATR, +25% at 2.8×ATR, +25% at 3.5×ATR. SL ratchets up on each add (breakeven→+0.5→+1.0→+1.5→+2.0 ATR). Pyramid adds sized from current balance (compounds). Requires trend still aligned (EMA 9>21)
- **MTD Accelerator**: Position size scales with month-to-date performance — up 20%+ → 1.5x size, up 10%+ → 1.3x, flat → 1.0x, down 5% → 0.8x, down more → 0.6x
- **Cooldown**: 4 candles after close, 8 candles after stop loss
- **Flexible Cooldown**: High-quality signals (score >= 0.7) can override cooldown at 50% reduction. Score = avg(R:R, RSI optimality, volume ratio, regime). Consecutive loss circuit breaker is never overridden.
- **Trading Hours**: Skip 00:00-02:59 UTC (low-edge dead hours)
- **Regime Filter**: Skip ranging markets for trend-following signals
- **Weekend**: Disabled (weekend trades have negative edge on BTC)

## AI Advisor Layer
- **Mode**: "advisor" — provides nuanced adjustments, NOT binary gate
- **Adjustments**: position_size_modifier (0.5-1.5), sl_adjustment, tp_adjustment
- **Calibration**: Rolling accuracy tracking, auto-adjusts AI influence
- **Timeout**: 10s with fallback to execute without AI
- **Model**: claude-sonnet-4-6

## Risk Management (Autonomous Safety Net)
Bot runs fully autonomous — risk management is the primary safety layer:
- 3% base risk per trade (tiered by signal quality), 9% max daily loss
- 10x max leverage (effective leverage varies by tier and regime)
- Max 2 concurrent positions, max 5 consecutive losses
- Dynamic sizing based on win rate, market regime, and signal quality score
- Circuit breakers: daily loss halt, API error halt, cooldown timer
- SL verification with 3 retries on exchange
- Graceful shutdown on SIGINT/SIGTERM (closes all positions)
- AI calibration auto-adjusts influence (no human tuning needed)
- Telegram alerts for critical events (informational, no action required)

## Configuration
- `config.json` — All trading parameters (validated on load with safety limits)
- `.env` — API keys (Bybit, Anthropic, Telegram, CryptoPanic)
- `use_testnet: true` must be explicitly changed to go live

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
- **Pyramiding**: Only add to winning positions when trend aligned (EMA9 vs EMA21); SL must ratchet up (never lower) on pyramid adds; pyramid adds charge commission on the added size
- **Regime propagation**: `detect_regime()` must be computed per row in backtest (rolling); backtest stores regime in DataFrame for signal-level gating

## Environment Variables
```
API_KEY, API_SECRET          — Bybit API credentials
ANTHROPIC_API_KEY            — Claude AI access
CRYPTOPANIC_TOKEN            — News API (optional)
TELEGRAM_BOT_TOKEN/CHAT_ID  — Monitoring alerts (optional)
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
