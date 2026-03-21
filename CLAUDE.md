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
research/
├── discover_liquid_coins.py → Find liquid USDT perps on Binance ($10M+ volume)
├── download_altcoin_data.py → Download OHLCV + funding (--from-json, --funding, parallel)
├── mass_sweep.py        → Lightweight EMA+Ichimoku sweep across all coins
├── verify_winners.py    → Full BacktestEngine verification of sweep winners
└── ...                  → Strategy-specific research scripts
scripts/
├── generate_configs.py  → Auto-generate config + docker-compose from verified winners
└── download_funding_rates.py → Funding rate downloader
tests/                   → pytest unit/integration tests
.claude/
├── agents/              → 7 Agent Team members (pm, sa, backend-dev, frontend-dev, devops, trader-expert, crypto-expert)
├── skills/              → 10 Trading skills (technical-analyst, backtest-expert, position-sizer, etc.)
└── settings.json        → Project settings (agent teams enabled)
config files:
  config.json            → BTC Safe (default, mainnet-ready)
  config_aggressive.json → BTC Aggressive (5% risk, 10x lev)
  config_yolo.json       → BTC YOLO-lite (10% risk, 25x lev, YOLO_MODE=1)
  config_sniper.json     → BTC Sniper (tight RSI, fewer trades)
  config_doge.json       → DOGE EMA 15m (3% risk, slope=0.01, funding scorer OFF)
  config_arb.json        → ARB EMA 15m (3% risk, funding scorer OFF)
  config_wif.json        → WIF EMA 15m (3% risk, funding scorer ON)
  config_avax_ichi.json  → AVAX Ichimoku 1H (2% risk, SL2.0/TP5.0)
  config_near_ichi.json  → NEAR Ichimoku 1H (2% risk, SL2.5/TP5.0)
  config_sol_ichi.json   → SOL Ichimoku 1H (2% risk, SL1.5/TP4.0)
  config_gold_forex.json → XAU/USD OANDA (H1 signal, H4 trend)
  config_*usdt_ichi.json → Auto-generated Ichimoku configs (1% risk, mass expansion)
  config_*usdt_ema.json  → Auto-generated EMA configs (1% risk, mass expansion)
  config_*usdt_ichi4h.json → Auto-generated 4H Ichimoku configs (1% risk, mass expansion v2)
  config_*usdt_ichi4htrail.json → 4H Ichimoku trailing exit configs (1% risk)
  config_*usdt_supertrend.json → Supertrend configs (1% risk)
  config_*_volexp.json     → Vol Expansion Breakout configs (1% risk)
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

## Multi-Coin Day Trading Portfolio

### Techniques Used
1. **EMA(9/21) Crossover 15m** — trend-following on BTC + meme/high-momentum coins
2. **Ichimoku Cloud 1H** — Tenkan/Kijun cross above/below cloud on altcoins
3. **Signal Scorer** — 6 OHLCV signals + funding rate weighted scoring for conviction
4. **Funding Rate Filter** — contrarian crowding signal (weight 0.35 on BTC/WIF)
5. **Ichimoku Cloud 4H** — Same Tenkan/Kijun cross + cloud filter but on 4H resampled data. Reduces noise for large-cap coins.
6. **4H Ichimoku Trailing Exit** — 4H Ichimoku entry with ATR trailing stop instead of fixed TP. Captures longer trends.
7. **Supertrend 1H** — ATR-adaptive trend-following bands. Direction change = entry signal. Works on MSTR, XAG, SAHARA.
8. **Vol Expansion Breakout 1H** — ATR exceeds rolling mean × threshold + price breaks above/below recent high/low + EMA(50) trend filter. Captures volatility spikes. Works on 1000PEPE, WLD.

### Key Differences per Strategy
| | EMA 15m | Ichimoku 1H | Ichimoku 4H | 4H Trail | Supertrend 1H | Vol Expansion 1H |
|---|---------|-------------|-------------|----------|---------------|-------------------|
| Signal | EMA crossover + RSI + vol | Tenkan/Kijun + cloud | Tenkan/Kijun + cloud | Tenkan/Kijun + cloud | ATR band direction change | ATR > MA×threshold + breakout |
| Timeframe | 15m signal, 1h trend | 1h | 4h (resampled) | 4h (resampled) | 1h | 1h |
| SL/TP | 1.0/3.0 ATR | 1.5-2.5/4.0-5.0 ATR | 2.0-2.5/4.0-5.0 ATR | SL only + trail | 2.0-2.5/3.0-5.0 ATR | 2.5/3.0 ATR |
| Best for | BTC, meme (DOGE/WIF) | Mid-cap (AVAX/NEAR/SOL) | Large-cap (TAO/RENDER) | ALGO/FET/POL | MSTR/XAG/SAHARA | 1000PEPE/WLD |
| Trades/yr | 20-30/coin | 10-15/coin | 6-9/coin | 5-7/coin | 5-7/coin | 6-8/coin |

### Research Findings (300+ backtests)
- Price Action: fails on all coins/timeframes (avg PF 0.73-0.85)
- BTC 5m: fails for ALL strategies (oracle max PF 1.10)
- EMA on ETH/SOL/ADA: fails (PF <1.04) — EMA edge is BTC + meme specific
- Ichimoku 1H: works on AVAX/NEAR/SOL (PF 1.69-1.95)
- Funding rate: improves BTC PF +10%, WIF +27%, but destroys DOGE (-94%)

### Mass Expansion v2 Research (March 2026)
- **4H Ichimoku: NEW strategy** — resampling 1H→4H unlocks large-cap coins that fail at 1H
- **4H Trailing exit** — trail >> fixed TP for trending 4H coins (ALGO PF 5.84, FET PF 2.87)
- **Supertrend implemented in engine** — ATR-adaptive bands, works on MSTR PF 2.66, XAG PF 2.09
- Long-only Ichimoku: works in sweep but BacktestEngine doesn't support long-only flag yet
- Fast Ichimoku (7/22/44): marginal improvement on some coins
- Momentum ROC: few winners, not reliable on crypto
- Adaptive SL/TP: found candidates but most failed full engine verification
- EMA(12/26): minimal improvement over 9/21
- Heikin-Ashi + EMA: mostly stock/commodity tickers, few crypto winners
- Keltner Channel: high trade count but low PF in full engine
- 6 undeployed sweep winners REJECTED: all had < 6 months data (CRCL/GUA/BEAT/ENSO/KITE/UAI)
- **4 rounds of research**: 7→19→22→26→29 bots, PF 1.68→1.77, DD 8.3%→6.4%

### Round 5 Research (March 2026)
- **Vol Expansion Breakout: NEW strategy** — ATR > rolling_mean × 1.8 + price breakout + EMA(50) trend filter
- **439 sweep winners** across 43 unique coins (1H + 4H), largest new technique pool
- Engine-verified: 1000PEPE PF 10.85 (80% WR), WLD PF 3.25 (73% WR) → deployed
- STO rejected (only 0.9yr data), SAND failed engine (PF 1.19), ASTER/HYPE failed (PF <1.3)
- MTF Confluence: 92 winners but hurts deployed coins, only SIREN/PIPPIN new (not verified)
- Regime-Adaptive Exit: infrastructure built (entry-time regime locks TP/trail), default params don't help (PF 1.77→1.60)
- **5 rounds of research**: 7→19→22→26→29→31 bots, PF 1.77→2.06, DD 6.4%→1.9%

## AI Advisor Layer
- **Mode**: "advisor" — provides nuanced adjustments, NOT binary gate
- **Adjustments**: position_size_modifier (0.5-1.5), sl_adjustment, tp_adjustment
- **Calibration**: Rolling accuracy tracking, auto-adjusts AI influence
- **Timeout**: 10s with fallback to execute without AI
- **Model**: claude-sonnet-4-6

## Signal Scorer (Multi-Signal Conviction)
- **Purpose**: Supplements binary signal checks with weighted conviction scoring
- **Signals**: 6 OHLCV (EMA alignment, momentum, volume, RSI, ATR, candle strength) + funding rate
- **Mode**: Score gates low-conviction trades (below threshold) and modulates position size
- **Best config**: Funding rate weight=0.35, threshold=0.10 → BTC PF 1.68→1.85 (+10%)
- **Funding data**: Auto-loaded from `data/{symbol}_funding_rate.csv` (shift(1) for bias prevention)
- **Disabled per-coin**: Falls back to OHLCV-only scoring if no funding file exists

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
- `config.json` — BTC Safe profile (default, suitable for mainnet)
- `config_aggressive.json` — BTC Aggressive profile (5% risk, 10x leverage)
- `config_yolo.json` — BTC YOLO-lite profile (10% risk, 25x lev, requires `YOLO_MODE=1`)
- `config_sniper.json` — BTC Sniper profile (tight RSI, fewer but higher quality trades)
- `config_doge.json` — DOGE/USDT EMA 15m (3% risk, slope=0.01, funding scorer OFF)
- `config_arb.json` — ARB/USDT EMA 15m (3% risk, funding scorer OFF)
- `config_wif.json` — WIF/USDT EMA 15m (3% risk, funding scorer ON)
- `config_avax_ichi.json` — AVAX/USDT Ichimoku 1H (2% risk, SL2.0/TP5.0)
- `config_near_ichi.json` — NEAR/USDT Ichimoku 1H (2% risk, SL2.5/TP5.0)
- `config_sol_ichi.json` — SOL/USDT Ichimoku 1H (2% risk, SL1.5/TP4.0)
- `config_gold_forex.json` — XAU/USD OANDA (H1 signal, H4 trend)
- `config_*usdt_ichi.json` — Mass expansion Ichimoku configs (auto-generated, 1% risk)
- `config_*usdt_ema.json` — Mass expansion EMA configs (auto-generated, 1% risk)
- `config_*usdt_ichi4h.json` — Mass expansion 4H Ichimoku configs (auto-generated, 1% risk)
- `config_*usdt_ichi4htrail.json` — 4H Ichimoku trailing exit configs (auto-generated, 1% risk)
- `config_*usdt_supertrend.json` — Supertrend configs (auto-generated, 1% risk)
- `config_*_volexp.json` — Vol Expansion Breakout configs (auto-generated, 1% risk)
- `.env` — API keys (Bybit/Binance, Anthropic, Telegram, CryptoPanic)
- `use_testnet: true` must be explicitly changed to go live
- `--config <path>` or `CONFIG_FILE` env var selects config file

## Deployed Portfolio (29 bots, verified backtests)

### Strategy 1: EMA Crossover 15m (Original)
| Coin | Config | Risk | PF | WR% | Tr/yr | Sharpe | Scorer |
|------|--------|------|-----|-----|-------|--------|--------|
| **BTC** | `config.json` | 5% | 1.85 | 44% | 23 | 1.62 | Funding ON |
| **WIF** | `config_wif.json` | 3% | 1.64 | 44% | 31 | 1.19 | Funding ON |
| **ARB** | `config_arb.json` | 3% | 1.46 | 34% | 21 | 0.96 | OFF |
| **DOGE** | `config_doge.json` | 3% | 1.38 | 35% | 22 | 0.95 | OFF |

### Strategy 2: Ichimoku Cloud 1H (Original)
| Coin | Config | Risk | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|------|-----|-----|-------|--------|-----|
| **AVAX** | `config_avax_ichi.json` | 2% | 1.95 | 44% | 13 | 1.05 | 5% |
| **NEAR** | `config_near_ichi.json` | 2% | 1.87 | 48% | 13 | 1.03 | 5% |
| **SOL** | `config_sol_ichi.json` | 2% | 1.69 | 46% | 11 | 1.03 | 8% |

### Strategy 3: Mass Expansion — Ichimoku 1H (new, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **GUN** | `config_gunusdt_ichi.json` | 4.00 | 65% | 8 | 2.52 | 2.0% |
| **BERA** | `config_berausdt_ichi.json` | 2.92 | 55% | 10 | 1.92 | 2.0% |
| **ATH** | `config_athusdt_ichi.json` | 2.51 | 47% | 8 | 1.65 | 2.6% |
| **ZETA** | `config_zetausdt_ichi.json` | 1.93 | 49% | 18 | 1.22 | 4.0% |
| **ARC** | `config_arcusdt_ichi.json` | 1.89 | 45% | 10 | 1.13 | 2.4% |
| **ANIME** | `config_animeusdt_ichi.json` | 1.51 | 47% | 8 | 0.74 | 5.1% |
| **TRUMP** | `config_trumpusdt_ichi.json` | 1.48 | 48% | 12 | 0.70 | 3.4% |
| **INJ** | `config_injusdt_ichi.json` | 1.31 | 41% | 11 | 0.36 | 2.7% |
| **XLM** | `config_xlmusdt_ichi.json` | 1.28 | 43% | 18 | 0.47 | 3.9% |
| **1000SHIB** | `config_1000shibusdt_ichi.json` | 1.26 | 39% | 16 | 0.42 | 5.8% |
| **TRX** | `config_trxusdt_ichi.json` | 1.20 | 38% | 24 | 0.43 | 2.7% |

### Strategy 4: Mass Expansion — EMA 15m (new, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **ARC** | `config_arcusdt_ema.json` | 1.89 | 45% | 10 | 1.13 | 2.4% |

### Strategy 5: Mass Expansion v2 — 4H Ichimoku (1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **TAO** | `config_taousdt_ichi4h.json` | 5.21 | 67% | 8 | 2.24 | 0.7% |
| **RENDER** | `config_renderusdt_ichi4h.json` | 8.17 | 73% | 6 | 2.58 | 1.0% |
| **HBAR** | `config_hbarusdt_ichi4h.json` | 1.85 | 45% | 6 | 0.60 | 2.0% |

### Strategy 6: 4H Ichimoku Trailing Exit (1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **ALGO** | `config_algousdt_ichi4htrail.json` | 5.84 | 40% | 5 | 1.11 | 2.0% |
| **FET** | `config_fetusdt_ichi4htrail.json` | 2.87 | 39% | 7 | 1.04 | 2.0% |
| **POL** | `config_polusdt_ichi4htrail.json` | 1.74 | 40% | 5 | 0.55 | 2.3% |
| **POLYX** | `config_polyxusdt_ichi4htrail.json` | 1.49 | 50% | 7 | 0.46 | 2.0% |

### Strategy 7: Supertrend 1H (1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **MSTR** | `config_mstrusdt_supertrend.json` | 2.66 | 64% | 6 | 4.63 | 1.9% |
| **XAG** | `config_xagusdt_supertrend.json` | 2.09 | 44% | 5 | 2.47 | 2.0% |
| **SAHARA** | `config_saharausdt_supertrend.json` | 1.27 | 50% | 7 | 0.53 | 1.4% |

### Strategy 8: Vol Expansion Breakout 1H (1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **1000PEPE** | `config_1000pepeusdt_volexp.json` | 10.85 | 80% | 8 | 2.81 | 0.6% |
| **WLD** | `config_wldusdt_volexp.json` | 3.25 | 73% | 6 | 1.47 | 1.0% |

**Portfolio total: 757 trades, ~378/yr (~1/day) across 31 bots**
**PF 2.06 | Max DD 1.9% | $200 → $1,273 (+537%)**
**Original 7 bots: 2-5% risk | New 24 bots: 1% risk each**
**8 strategy types: EMA 15m, Ichimoku 1H, Ichimoku 4H, 4H Trail, Supertrend, Vol Expansion**

```bash
# Run all 31 bots

# === EMA Crossover 15m (5 bots) ===
YOLO_MODE=1 python main.py --config config.json &          # BTC (5% risk)
YOLO_MODE=1 python main.py --config config_doge.json &     # DOGE (3% risk)
YOLO_MODE=1 python main.py --config config_arb.json &      # ARB (3% risk)
YOLO_MODE=1 python main.py --config config_wif.json &      # WIF (3% risk)
YOLO_MODE=1 python main.py --config config_arcusdt_ema.json & # ARC EMA (PF 1.70)

# === Ichimoku Cloud 1H (14 bots) ===
python main.py --config config_avax_ichi.json &             # AVAX (2% risk)
python main.py --config config_near_ichi.json &             # NEAR (2% risk)
python main.py --config config_sol_ichi.json &              # SOL (2% risk)
python main.py --config config_gunusdt_ichi.json &          # GUN (PF 4.00)
python main.py --config config_berausdt_ichi.json &         # BERA (PF 2.92)
python main.py --config config_athusdt_ichi.json &          # ATH (PF 2.51)
python main.py --config config_zetausdt_ichi.json &         # ZETA (PF 1.93)
python main.py --config config_arcusdt_ichi.json &          # ARC (PF 1.89)
python main.py --config config_animeusdt_ichi.json &        # ANIME (PF 1.51)
python main.py --config config_trumpusdt_ichi.json &        # TRUMP (PF 1.48)
python main.py --config config_injusdt_ichi.json &          # INJ (PF 1.31)
python main.py --config config_xlmusdt_ichi.json &          # XLM (PF 1.28)
python main.py --config config_1000shibusdt_ichi.json &     # 1000SHIB (PF 1.26)
python main.py --config config_trxusdt_ichi.json &          # TRX (PF 1.20)

# === 4H Ichimoku Fixed TP (3 bots) ===
python main.py --config config_taousdt_ichi4h.json &        # TAO 4H (PF 5.21)
python main.py --config config_renderusdt_ichi4h.json &     # RENDER 4H (PF 8.17)
python main.py --config config_hbarusdt_ichi4h.json &       # HBAR 4H (PF 1.85)

# === 4H Ichimoku Trailing (4 bots) ===
python main.py --config config_algousdt_ichi4htrail.json &  # ALGO 4HT (PF 5.84)
python main.py --config config_fetusdt_ichi4htrail.json &   # FET 4HT (PF 2.87)
python main.py --config config_polusdt_ichi4htrail.json &   # POL 4HT (PF 1.74)
python main.py --config config_polyxusdt_ichi4htrail.json & # POLYX 4HT (PF 1.49)

# === Supertrend 1H (3 bots) ===
python main.py --config config_mstrusdt_supertrend.json &   # MSTR (PF 2.66)
python main.py --config config_xagusdt_supertrend.json &    # XAG (PF 2.09)
python main.py --config config_saharausdt_supertrend.json & # SAHARA (PF 1.27)

# === Vol Expansion Breakout 1H (2 bots) ===
python main.py --config config_1000pepeusdt_volexp.json &   # 1000PEPE (PF 10.85)
python main.py --config config_wldusdt_volexp.json &        # WLD (PF 3.25)

# Docker deployment (all bots)
docker compose up -d --build
```

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
