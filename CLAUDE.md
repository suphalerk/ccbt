# CCBT — Crypto & Gold Trading Bot with Claude AI

## Project Overview
Automated trading bot for BTC perpetual futures (Binance/Bybit via ccxt) and Gold XAU/USD (OANDA forex) with Claude AI advisor integration, Streamlit dashboard, and production deployment infrastructure.

**Language**: Python 3.11 | **Exchanges**: Binance/Bybit (ccxt) + OANDA (forex) | **AI**: Claude Sonnet via Anthropic SDK | **Dashboard**: Streamlit + Plotly | **DB**: SQLite (WAL mode)

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
├── ai_analyst.py        → Claude AI advisor (nuanced adjustments, not binary gate)
├── context_builder.py   → Market context assembly for AI prompts
├── news_fetcher.py      → RSS/CryptoPanic news integration
└── logger.py            → SQLite trade journal + AI calibration tracker
dashboard/
├── app.py               → Streamlit web UI (multi-bot portfolio + single-bot drill-down)
├── components.py        → Plotly chart components (per-bot PnL bar, portfolio table)
└── queries.py           → SQLite query helpers (symbol-filtered) + log file reader
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
├── auto_research.py     → One-command research pipeline (sweep→verify→deploy)
├── portfolio_backtest_v2.py → Shared wallet backtest with monthly/daily reports
├── discover_liquid_coins.py → Find liquid USDT perps on Binance ($10M+ volume)
├── download_altcoin_data.py → Download OHLCV + funding (--from-json, --funding, parallel)
├── mass_sweep.py        → Lightweight EMA+Ichimoku sweep across all coins
├── verify_winners.py    → Full BacktestEngine verification of sweep winners
├── sweep_new_strategies_1.py → Strategies 1-10 sweep (ADX, StochRSI, CCI, etc.)
├── sweep_new_strategies_2.py → Strategies 11-20 sweep (EMA+Ichi, VolExp+ST, etc.)
└── sweep_weak_bots.py   → 6-strategy sweep for underperforming bots
scripts/
├── generate_configs.py       → Auto-generate config + docker-compose from verified winners
├── generate_all_configs.py   → Batch config generator for all strategy types
├── start_all_bots.sh         → Start all 167 bots in a single process via main_multi.py
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
  config_doge.json       → DOGE EMA 15m (3% risk, RETIRED — PF 1.11, no edge)
  config_arb.json        → ARB EMA 15m (3% risk, RETIRED — replaced by Ichi 4H)
  config_wif.json        → WIF EMA 15m (3% risk, funding scorer ON)
  config_avax_ichi.json  → AVAX Ichimoku 1H (2% risk, SL2.0/TP5.0)
  config_near_ichi.json  → NEAR Ichimoku 1H (2% risk, SL2.5/TP5.0)
  config_sol_ichi.json   → SOL Ichimoku 1H (2% risk, RETIRED — PF 1.18, no edge)
  config_gold_forex.json → XAU/USD OANDA (H1 signal, H4 trend)
  config_*usdt_ichi.json → Auto-generated Ichimoku configs (1% risk, mass expansion)
  config_*usdt_ema.json  → Auto-generated EMA configs (1% risk, mass expansion)
  config_*usdt_ichi4h.json → Auto-generated 4H Ichimoku configs (1% risk, mass expansion v2)
  config_*usdt_ichi4htrail.json → 4H Ichimoku trailing exit configs (1% risk)
  config_*usdt_supertrend.json → Supertrend configs (1% risk)
  config_*_volexp.json     → Vol Expansion Breakout configs (1% risk)
  config_*usdt_dualthrust.json → Dual Thrust configs (1% risk, R10+R12)
  config_*usdt_dualst*.json → Dual Supertrend configs (1% risk, R8)
  config_*usdt_alligator*.json → Alligator configs (1% risk, R8)
  config_*usdt_emaichi4h.json → EMA+Ichimoku 4H configs (1% risk, R8)
  config_*usdt_emaribbon.json → EMA Ribbon configs (1% risk, R12)
  config_*usdt_ichist4h.json → Ichi+Supertrend 4H configs (1% risk, R10)
```

## Key Commands

```bash
# Run all 167 bots (single process, ~340 MB RAM)
bash scripts/start_all_bots.sh

# Stop all bots
bash scripts/start_all_bots.sh stop

# Run a named group
python main_multi.py --group ichimoku-1h

# Run specific configs
python main_multi.py --configs config_avax_ichi.json config_near_ichi.json

# Run by glob pattern
python main_multi.py --pattern "config_*ichi*.json"

# Run dashboard
streamlit run dashboard/app.py

# Run tests
pytest tests/ -v

# Run single bot (legacy)
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

- Controlled via dashboard buttons or by writing `data/mode_{SYMBOL_CLEAN}.json` directly
- Implemented in `bot/mode.py`; read by `bot/engine.py` each tick

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
7. **Supertrend 1H** — ATR-adaptive trend-following bands. Direction change = entry signal. Works on MSTR, XAG.
8. **Vol Expansion Breakout 1H** — ATR exceeds rolling mean × threshold + price breaks above/below recent high/low + EMA(50) trend filter. Captures volatility spikes. Works on 1000PEPE, WLD.
9. **Dual Supertrend 4H** — Fast(7,2.0) + Slow(14,3.0) Supertrend bands. Entry when both agree. Reduces whipsaw. Best hit rate (52%). Works on XRP, ANKR, PAXG, DOGE, FIL.
10. **VolExp+Supertrend 1H** — Vol Expansion breakout confirmed by Supertrend direction. Tighter filter = higher WR. Works on ATOM, 1000PEPE, MYX, PENGU, AAVE.
11. **Alligator 4H** — Bill Williams 3 smoothed MAs (Jaw=13, Teeth=8, Lips=5). Lips crosses Teeth in Jaw direction. Works on LIGHT, LINK, SUI, QNT.
12. **EMA+Ichimoku 4H** — EMA(9/21) crossover for timing + Ichimoku cloud as direction filter. Works on VVV, ADA, APT, ZEC.
13. **Ichi+Supertrend 4H** — Ichimoku cloud for direction + Supertrend flip for entry. Works on PIPPIN, XAI, LTC, W.
14. **Dual Thrust** — Range breakout using previous-day high/low range × multiplier. Mean between high/low as dynamic support/resistance. Works on GALA, PHA, ZEC, DOT, FIL.
15. **Range Bounce** — RSI reversal within Bollinger Band range when Bollinger %B < 0.2 (oversold) or > 0.8 (overbought). Mean reversion only when range is confirmed. Works on AXS, FIL.
16. **Awesome Oscillator** — Bill Williams AO zero-cross + Ichimoku cloud direction filter on 4H. Works on VVV, SAND, ALICE.
17. **Z-Score Mean Reversion** — Statistical z-score of price vs rolling mean + EMA(50) trend filter to avoid counter-trend entries. Works on AKT, CRV, VVV.
18. **Stoch MTF (Multi-Timeframe Stochastic)** — Stochastic crossover on signal timeframe confirmed by higher timeframe Stochastic direction. Works on AXS.
19. **EMA Ribbon** — Fan of 6 EMAs (8/13/21/34/55/89) must all align in same direction. Entry when fastest crosses slowest. Works on IP, OP, AVAX, KAS, BERA.
20. **Ichi+ADX** — Ichimoku Tenkan/Kijun cross with ADX > 20 trend strength filter. Fewer but higher-conviction trades.
21. **Ribbon+AO** — EMA Ribbon alignment confirmed by Awesome Oscillator momentum direction.

### Key Differences per Strategy
| | EMA 15m | Ichimoku 1H | Ichimoku 4H | 4H Trail | Supertrend 1H | Vol Expansion 1H |
|---|---------|-------------|-------------|----------|---------------|-------------------|
| Signal | EMA crossover + RSI + vol | Tenkan/Kijun + cloud | Tenkan/Kijun + cloud | Tenkan/Kijun + cloud | ATR band direction change | ATR > MA×threshold + breakout |
| Timeframe | 15m signal, 1h trend | 1h | 4h (resampled) | 4h (resampled) | 1h | 1h |
| SL/TP | 1.0/3.0 ATR | 1.0-2.5/0-5.0 ATR | 2.0-3.0/4.0-5.0 ATR | SL only + trail | 2.0-2.5/3.0-5.0 ATR | 2.5/3.0 ATR |
| Best for | BTC, WIF | Mid-cap (AVAX/NEAR/POL) | Large-cap (TAO/RENDER/ARB/1000SHIB) | ALGO/FET/TRX/SAHARA/XLM | MSTR/XAG | 1000PEPE/WLD |
| Trades/yr | 20-30/coin | 10-15/coin | 4-9/coin | 4-7/coin | 5-7/coin | 4-8/coin |

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

### Round 6 Research — Weak Bot Optimization (March 2026)
- **1yr backtest all 31 bots**: 30 profitable, 1 losing (POL PF 0.37)
- **6-strategy sweep on 8 weak bots** (PF < 1.3): tested EMA/Ichi1H/Ichi4H/4HTrail/Supertrend/VolExp
- **6 replacements found**:
  - POL: Ichi4H Trail → Ichi 1H Trail (PF 0.37→6.79, SL1.0/Trail3.0)
  - TRX: Ichi 1H → 4H Trail (PF 1.01→5.53, SL1.5/Trail4.0)
  - ARB: EMA 15m → Ichi 4H (PF 1.03→4.38, SL3.0/TP4.0)
  - 1000SHIB: Ichi 1H → Ichi 4H (PF 1.06→18.43, SL3.0/TP5.0)
  - SAHARA: Supertrend → 4H Trail (PF 1.27→2.44, SL3.0/Trail4.0)
  - XLM: Ichi 1H → 4H Trail (PF 1.31→2.88, SL1.0/Trail4.0)
- **2 bots RETIRED** (no strategy PF > 1.2): DOGE (best 1.11), SOL (best 1.17)
- **Dashboard overhauled**: multi-bot portfolio view, per-bot drill-down, auto-config discovery
- **Binance leverage fix**: auto-halving retry on -4028 rejection
- **6 rounds of research**: 7→19→22→26→29→31→29 bots (net -2), portfolio PF improved

### Round 7 Research — 20 New Strategies Mega Sweep (March 2026)
- **20 brand new strategies** designed and swept across **133 coins × 2 timeframes (1H + 4H)**
- **3,935 backtests** completed (2yr data), **760 winners** (PF >= 1.3, trades >= 6)
- **80 new coin candidates** found (not already deployed, PF >= 1.5, data > 1yr)
- **5 new strategy types proven on crypto:**
  1. **Dual Supertrend 4H** — fast(7,2.0) + slow(14,3.0) agreement → 15 new coins, 52% hit rate
  2. **VolExp+Supertrend 1H** — vol expansion + supertrend direction confluence → 10 new coins, avg PF 1.47
  3. **Alligator 4H** — Bill Williams 3-SMMA (jaw=13,teeth=8,lips=5) → 9 new coins, LIGHT PF 4.99
  4. **EMA+Ichimoku 4H** — EMA(9/21) cross + cloud direction filter → 7 new coins, VVV PF 5.12
  5. **Ichi+Supertrend 4H** — Ichimoku cloud + Supertrend entry → 6 new coins, PIPPIN PF 7.09
- **Top new coin×strategy candidates (pending engine verification):**
  - PIPPIN Ichi+ST 4H PF 7.09, LYN Dual ST 1H PF 5.79, ATOM VolExp+ST PF 5.38
  - VVV EMA+Ichi 4H PF 5.12, LIGHT Alligator 1H PF 4.99, XRP Dual ST 4H PF 3.13
- **Strategies that FAILED on crypto:**
  - RSI 50-Cross 1H (PF 0.94), PSAR Flip 1H (PF 0.95), StochRSI 1H (PF 0.95)
  - BTC Cross-Coin Leader (PF 0.96), Regime Transition 1H (PF 0.88)
  - Elder Impulse 1H (PF 0.97), CCI 1H (PF 0.97)
- **Key insight**: 4H consistently outperforms 1H for all new strategies (noise reduction)
- Scripts: `research/sweep_new_strategies_1.py` (S1-S10), `research/sweep_new_strategies_2.py` (S11-S20)
- Results: `data/sweep_new_strats_1.json`, `data/sweep_new_strats_2.json`

### Round 8 Research — Engine Verification + Deploy (March 2026)
- **Full BacktestEngine verification** of 80 sweep winners: Tier A+B 14/46 pass (30%), Tier C+D 7/34 pass (21%)
- **VolExp+Supertrend FAILS full engine** — 0 trades on every coin (signal conditions too strict vs lightweight sweep)
- **18 new coins deployed**: APT, LTC, LIGHT, QNT, LYN, SIGN, ENJ, TIA, XPL, IP, ONDO, LINK, XRP, SUI, AKT, H, HUMA, AXS
- **4 existing coins upgraded**: NEAR/ZETA → EMA+Ichi 4H, HBAR/ARC → Alligator 4H
- **9 deployed coins tested × 5 new strategies**: BTC Dual ST 4H PF 1.71 (+21% vs EMA), but kept EMA as flagship
- **Portfolio backtest ($200 shared wallet, 1yr)**: 528 trades, PF 1.75, $200→$1,794 (+797%), 12/13 months profitable
- **5 new signal types implemented in engine**: dual_supertrend, alligator, ema_ichimoku_hybrid, ichi_supertrend, volexp_supertrend
- **Auto-research script**: `research/auto_research.py` — one-command pipeline (sweep→verify→deploy)
- **8 rounds of research**: 7→19→22→26→29→31→29→47 bots, $200→$1,794 in 1yr

### Round 9 Research — 20 More New Strategies (March 2026)
- **20 brand new strategies** swept across 129 coins × 2 timeframes = **5,160 backtests**
- **799 winners** (PF >= 1.3, trades >= 6), **78 new coin candidates**
- **Top new strategy types:**
  1. **ADX+DI Crossover** — DI+/DI- cross with ADX rising, avg PF 1.58, 43% hit rate, **12 new coins**
  2. **Choppiness Index + EMA** — CI < 38.2 (trending) + EMA cross, avg PF 1.44, 40% hit rate, **9 new coins**
  3. **Williams %R + ADX** — %R extremes in trending market, **8 new coins** (ATOM PF 2.73, BCH PF 2.15)
  4. **Stochastic + Supertrend** — Stoch timing + ST direction, **6 new coins** (PENGU PF 3.02)
  5. **ROC Momentum** — ROC zero-cross + trend, **7 new coins** (RIVER PF 2.55, TON PF 1.73)
- **Deployed coin upgrades found:**
  - XAG: Supertrend → Donchian+ADX 4H (PF 2.09→15.58)
  - MSTR: Supertrend → Keltner+ADX 1H (PF 2.66→7.45)
  - ANIME: Ichi 1H → ADX+DI Cross 1H (PF 1.51→6.16)
  - AXS: Ichi 4H → Choppiness+EMA 4H (PF 1.31→4.08)
- **Strategies that work**: ADX+DI, Choppiness+EMA, EMA+Alligator, Supertrend+Volume, PriceChannel+Vol
- **Strategies mediocre**: Hull MA Cross (PF 0.98), KAMA Cross (PF 1.01), OBV Breakout (PF 1.04)
- **Engine verification**: 14/78 PASS (18%), 19 skipped (strategy not in engine), 44 FAIL
- **8 new signal types implemented**: adx_di_cross, choppiness_ema, williams_r_adx, roc_momentum, stoch_supertrend, price_channel_vol, ema_alligator, supertrend_volume
- **14 new coins engine-verified**: ZRO, ARIA, SAND, CRCL, ZEC, DEGO, TSLA, KAS, RIVER, TON, DASH, DOT, APR, XMR
- **Backtest audit found critical bugs**: double compounding (pnl/initial instead of pnl/current), no concurrent limit, loose data filters
- **Corrected portfolio (14 verified bots, $200 shared, max 5 concurrent)**: 320 trades, $200→$497 (+149%/yr), DD 8.1%
- **47/61 bots filtered out**: data < 12mo or trades < 10 — configs ready but need more data before live
- **9 rounds of research**: 7→19→22→26→29→31→29→47→61 bots (14 verified), $200→$497 realistic
- Script: `research/sweep_round9.py`, `research/round9_verify_and_backtest.py`

### Round 10 Research — GitHub-Inspired Strategies (March 2026)
- **5 strategies from top GitHub trading bots** (Freqtrade, je-suis-tm, OctoBot, NostalgiaForInfinity)
- Swept 54 coins × parameter grids (108 combos/coin) = thousands of backtests
- **ALL 5 strategies PASSED** — first time every strategy in a round has winners:
  1. **Dual Thrust** (je-suis-tm 4.5K stars) — range breakout, ZEN 4H PF 8.34, DOT PF 7.77
  2. **Kalman Filter Trend** (QuantConnect) — adaptive MA crossover, less lag than EMA
  3. **Awesome Oscillator** (je-suis-tm) — AO zero-cross, VVV 4H PF 2.81, 28 winners
  4. **Range Bounce** (OctoBot 4K stars) — ranging market bounces, AXS PF 13.46 (92% WR!), **45 winners**
  5. **Multi-Indicator Confluence** (NostalgiaForInfinity 2.9K stars) — 3-4/5 indicators agree, IP 4H PF 2.11
- **Key surprise**: Range Bounce (mean reversion variant) WORKS when gated by range detection + RSI
- **High-frequency strategies FAIL**: 10 scalping strategies (15m) tested, all PF 0.32-0.71 — fees kill edge
- Script: `research/sweep_github_inspired.py`, `research/sweep_highfreq.py`
- Results: `data/sweep_github.json`, `data/sweep_highfreq.json`
- **Engine verification**: 76/180 PASS. 3 strategies implemented (Dual Thrust, Awesome Oscillator, Range Bounce)
- **Top verified new coins**: GALA DualThrust PF 7.84, PHA DualThrust PF 4.39, ZEC DualThrust PF 3.41, FIL RangeBounce PF 3.25, SAND AwesomeOsc PF 2.88
- **Realistic backtest (62 bots, $200 shared, R-multiple, max 5 concurrent)**: $200→$1,106 (+453%), PF 1.20, DD 34%, 1,452 trades, 10/13 months profitable
- **10 rounds of research**: 7→19→22→26→29→31→29→47→61→62 bots

### Round 11 Research — FMZQuant/TradingView/Academic Strategies (March 2026)
- **20 strategies** from FMZQuant, TradingView, Connors, academic papers — 2,811 backtests, 78 coins
- **Top performers**: Stoch MTF (AXS PF 5.86), Z-Score MeanRev (AKT PF 2.94), EMA Ribbon (IP PF 2.45)
- **3 new strategies implemented in engine**: stoch_mtf, zscore_meanrev, ema_ribbon
- **Engine verification**: 36/87 PASS — zscore_meanrev 63% pass rate (best)
- **Turtle Trading FAILS** on crypto (avg PF 0.85)
- **Z-Score Mean Reversion WORKS** — first statistical mean reversion success with EMA50 filter
- **Realistic backtest (85 bots, $200 shared, R-multiple, max 5 concurrent)**:
  - **$200 → $1,579 (+689%)** | DD 26.1% | 1,465 trades | 11/13 months profitable
  - Best months: Nov +$358, Jan +$384, Mar +$198
  - Worst: Dec -$185
- **11 rounds total**: 7→19→22→26→29→31→29→47→61→62→85 bots
- Scripts: `research/sweep_round11.py`, `research/round11_verify_backtest.py`

### Round 12 Research — 100-Strategy Mega Sweep + Combo Functions (March 2026)
- **100 strategy COMBINATIONS** (2-3 indicators each) swept across 104 coins = **6,376 backtests**
- **744 winners** (PF >= 1.3, trades >= 10)
- **Top combos**: Ribbon+RSI+Vol (avgPF 1.44), DualThrust+ADX (32 winners), ZScore+Stoch (avgPF 1.28)
- **5 combo check functions implemented in engine**: ribbon_rsi_vol, dualthrust_adx, zscore_stoch, ichi_adx, ribbon_ao
- **Combo verification**: 23/120 PASS — DualThrust+ADX dominant (13 passes), Ichi+ADX (5), Ribbon+AO (3)
- **Key finding**: combo strategies (2 indicators AND) outperform single signals — ADX confirmation is the #1 edge booster
- **Pattern strategies FAIL**: Hammer, Engulfing, InsideBar all PF < 1.0 — candle patterns don't work on crypto
- **Multi-confluence (4-5 indicators) doesn't help**: too selective, fewer trades = worse compounding
- **Full combined portfolio (136 bots, $200 shared, R-multiple, max 5 concurrent)**:
  - **$200 → $1,790 (+795%)** | DD 23.0% | 1,541 trades | 10/13 months profitable
  - Best month: Nov +$617 | Worst: Dec -$188
  - Top contributors: BTC EMA +$471, BERA ema_ribbon +$306
- **12 rounds of research**: 7→19→22→26→29→31→29→47→61→62→85→136 bots, $200→$1,790 realistic

### Core Coins Retested + Audited (March 2026)
- **224 backtests**: 7 core coins × 32 strategies + OP/ZRO × 32 = 288 total
- **Confirmed upgrades (walk-forward stable, trades >= 18)**:
  - AVAX: Ichi 1H (PF 1.95) → **Dual Thrust 1H (PF 3.23, 31 trades)** — most reliable
  - ARC: EMA 15m (PF 1.89) → **Dual Thrust 1H (PF 2.50, 18 trades)**
  - OP: DualThrust → **EMA Ribbon 1H (PF 1.95, 24 trades)**
- **REJECTED after deep audit (walk-forward fail / small sample)**:
  - BTC Stoch MTF 1H: PF 2.96 but only 14 trades, walk-forward degrades 49% → **KEEP EMA 15m**
  - WIF DualThrust+ADX 1H: PF 2.45 but only 8 trades, last 3 trades all losses → **KEEP EMA 15m**
  - ZRO ADX+DI 1H: PF 21.22 but only 6 trades → **KEEP Dual ST 1H**
- **Keep current (confirmed best)**: BTC (EMA 15m), WIF (EMA 15m), GUN (Ichi PF 4.00), ATH (Ichi PF 2.51), 1000PEPE (VolExp PF 10.85), ZRO (Dual ST 1H)
- **Audit methodology**: signal verification (every trade), look-ahead check, fee check, walk-forward (first/second half), baseline comparison
- Scripts: `research/core7_new_strategies.py`, `research/audit_btc_wif.py`, `research/sweep_op_zro.py`

### Weak Bot Optimization — Parameter Sweep (March 2026)
- **5,940 backtests**: 30 weak coins × 10 strategies × 10 param sets × 2 TF
- **29/30 found improvements** in lightweight sweep (only BCH had no solution)
- **Engine verification**: 8/29 PASS — Z-Score 1H collapsed (overfitting), 4H strategies won
- **6 deploy-grade upgrades**:
  - PENGU: Dual Thrust → Ichi 4H Trail (PF 6.01, 10 trades)
  - POL: Ichi Trail → Ichi 4H Trail (PF 3.57, 13 trades)
  - AVAX: Dual Thrust → EMA Ribbon 4H (PF 2.10, 30 trades)
  - ALICE: Dual Thrust → Awesome Osc 4H (PF 1.97, 16 trades)
  - DASH: Dual Thrust → Ichi 4H (PF 1.96, 14 trades)
  - KAS: Range Bounce → EMA Ribbon 4H (PF 1.95, 28 trades)
- **Key lesson**: Z-Score MeanRev sweep PFs (4-6) were overfitting — engine gets 0-5 trades. Always verify.
- Scripts: `research/sweep_improve_weak.py`, `research/verify_weak_improvements.py`

## Auto-Research Pipeline
One-command research script replaces manual iterative process:
```bash
# Sweep a single strategy across all coins
python3 research/auto_research.py --strategy dual_supertrend --timeframe 4h

# Sweep ALL strategies
python3 research/auto_research.py --all-strategies

# Compare against deployed portfolio
python3 research/auto_research.py --strategy alligator --compare-deployed

# Generate config files for verified winners
python3 research/auto_research.py --generate-configs --input data/sweep_results.json

# Full portfolio backtest ($200 shared wallet)
python3 research/portfolio_backtest_v2.py
```

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

## Deployed Portfolio (47 bots, engine-verified backtests)

### Strategy 1: EMA Crossover 15m (2 bots)
| Coin | Config | Risk | PF | WR% | Tr/yr | Sharpe | Scorer |
|------|--------|------|-----|-----|-------|--------|--------|
| **BTC** | `config.json` | 5% | 1.41 | 42% | 24 | 0.91 | Funding ON |
| **WIF** | `config_wif.json` | 3% | 1.71 | 42% | 36 | 1.46 | Funding ON |

### Strategy 2: Ichimoku Cloud 1H (8 bots)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% | Notes |
|------|--------|-----|-----|-------|--------|-----|-------|
| **AVAX** | `config_avax_ichi.json` | 2.49 | 45% | 11 | 1.28 | 3.6% | |
| **POL** | `config_polusdt_ichi.json` | 6.79 | 50% | 14 | 2.13 | 2.4% | SL1.0/Trail3.0 |
| **GUN** | `config_gunusdt_ichi.json` | 4.00 | 65% | 17 | 2.52 | 2.0% | |
| **BERA** | `config_berausdt_ichi.json` | 2.92 | 55% | 20 | 1.92 | 2.0% | |
| **ATH** | `config_athusdt_ichi.json` | 2.51 | 47% | 17 | 1.65 | 2.6% | |
| **INJ** | `config_injusdt_ichi.json` | 2.28 | 44% | 9 | 0.93 | 2.6% | |
| **TRUMP** | `config_trumpusdt_ichi.json` | 1.66 | 53% | 19 | 0.87 | 3.5% | |
| **ANIME** | `config_animeusdt_ichi.json` | 1.51 | 47% | 17 | 0.74 | 5.1% | |

### Strategy 3: EMA 15m (mass expansion, 1% risk)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **ARC** | `config_arcusdt_ema.json` | 1.59 | 47% | 32 | 1.06 | 4.1% |

### Strategy 4: 4H Ichimoku Fixed TP (5 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **1000SHIB** | `config_1000shibusdt_ichi4h.json` | 18.43 | 75% | 4 | 2.66 | 0.2% |
| **TAO** | `config_taousdt_ichi4h.json` | 6.13 | 67% | 9 | 2.77 | 0.7% |
| **RENDER** | `config_renderusdt_ichi4h.json` | 4.71 | 57% | 7 | 1.86 | 1.0% |
| **ARB** | `config_arbusdt_ichi4h.json` | 4.38 | 50% | 4 | 1.72 | 0.2% |

### Strategy 5: 4H Ichimoku Trailing Exit (6 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **ALGO** | `config_algousdt_ichi4htrail.json` | 7.21 | 50% | 4 | 1.32 | 2.0% |
| **TRX** | `config_trxusdt_ichi4htrail.json` | 5.53 | 50% | 4 | 1.31 | 1.6% |
| **POLYX** | `config_polyxusdt_ichi4htrail.json` | 5.13 | 75% | 8 | 1.78 | 1.2% |
| **FET** | `config_fetusdt_ichi4htrail.json` | 3.03 | 33% | 6 | 0.93 | 1.4% |
| **XLM** | `config_xlmusdt_ichi4htrail.json` | 2.88 | 25% | 4 | 0.76 | 2.1% |
| **SAHARA** | `config_saharausdt_ichi4htrail.json` | 2.44 | 60% | 5 | 0.95 | 0.7% |

### Strategy 6: Supertrend 1H (2 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **MSTR** | `config_mstrusdt_supertrend.json` | 2.66 | 64% | 11 | 4.63 | 1.9% |
| **XAG** | `config_xagusdt_supertrend.json` | 2.09 | 44% | 9 | 2.47 | 2.0% |

### Strategy 7: Vol Expansion Breakout 1H (2 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **1000PEPE** | `config_1000pepeusdt_volexp.json` | 7.23 | 70% | 10 | 2.92 | 0.6% |
| **WLD** | `config_wldusdt_volexp.json` | 3.45 | 75% | 4 | 1.40 | 1.0% |

### Strategy 8: EMA+Ichimoku Hybrid 4H (3 bots, 1% risk each)
Signal: EMA crossover confirmation + Ichimoku cloud filter on 4H. SL 1.5 ATR, TP 4.0 ATR.
| Coin | Config | PF | Notes |
|------|--------|----|-------|
| **APT** | `config_aptusdt_emaichi4h.json` | 8.17 | |
| **NEAR** | `config_near_ichi.json` | 4.06 | upgraded from Ichi 1H |
| **ZETA** | `config_zetausdt_ichi.json` | 3.96 | upgraded from Ichi 1H |

### Strategy 9: Ichi+Supertrend Hybrid 4H (1 bot, 1% risk)
Signal: Ichimoku Tenkan/Kijun cross + Supertrend direction agreement on 4H. SL 2.0 ATR, TP 5.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **LTC** | `config_ltcusdt_ichist4h.json` | 6.90 |

### Strategy 10: Alligator 1H (1 bot, 1% risk)
Signal: Williams Alligator jaw/teeth/lips alignment + price above/below all three lines. SL 2.0 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **LIGHT** | `config_lightusdt_alligator.json` | 5.16 |

### Strategy 11: Alligator 4H (5 bots, 1% risk each)
Signal: Williams Alligator on 4H. SL 2.0 ATR, TP 4.0 ATR.
| Coin | Config | PF | Notes |
|------|--------|----|-------|
| **HBAR** | `config_hbarusdt_ichi4h.json` | 4.23 | upgraded from Ichi 4H |
| **QNT** | `config_qntusdt_alligator4h.json` | 3.49 | |
| **ARC** | `config_arcusdt_ichi.json` | 3.15 | upgraded from Ichi 1H |
| **LINK** | `config_linkusdt_alligator4h.json` | 1.66 | |
| **SUI** | `config_suiusdt_alligator4h.json` | 1.58 | |

### Strategy 12: Dual Supertrend 1H (2 bots, 1% risk each)
Signal: Two Supertrend indicators (different ATR multipliers) must agree on direction. SL 2.5 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **LYN** | `config_lynusdt_dualst.json` | 3.11 |
| **HUMA** | `config_humausdt_dualst.json` | 1.49 |

### Strategy 13: Dual Supertrend 4H (4 bots, 1% risk each)
Signal: Two Supertrend indicators agree on 4H. SL 2.5 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **ENJ** | `config_enjusdt_dualst4h.json` | 2.35 |
| **XPL** | `config_xplusdt_dualst4h.json` | 1.90 |
| **XRP** | `config_xrpusdt_dualst4h.json` | 1.59 |
| **AKT** | `config_aktusdt_dualst4h.json` | 1.56 |

### Strategy 14: Ichimoku Cloud 4H — new coins (6 bots, 1% risk each)
Signal: Ichimoku Tenkan/Kijun cross above/below cloud on 4H. SL 2.0 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **SIGN** | `config_signusdt_ichi4h.json` | 2.43 |
| **TIA** | `config_tiausdt_ichi4h.json` | 2.08 |
| **ONDO** | `config_ondousdt_ichi4h.json` | 1.77 |
| **H** | `config_husdt_ichi4h.json` | 1.51 |
| **AXS** | `config_axsusdt_ichi4h.json` | 1.31 |

### Strategy 15: Ichimoku Cloud 1H — new coins (2 bots, 1% risk each)
| Coin | Config | PF |
|------|--------|----|
| **IP** | `config_ipusdt_ichi.json` | 1.84 |

### Strategy 16: ADX+DI Crossover 1H (Round 9, 1% risk)
Signal: DI+/DI- cross + ADX > 20 rising. SL 2.0 ATR, TP 4.0 ATR.
| Coin | PF | WR% | Trades |
|------|----|-----|--------|
| **ZRO** | 24.61 | 83% | 6 |
| **ZEC** | 2.95 | 75% | 4 |

### Strategy 17: Choppiness+EMA 1H (Round 9, 1% risk)
Signal: CI < 38.2 (trending) + EMA(9/21) crossover. SL 2.0 ATR, TP 4.0 ATR.
| Coin | PF | WR% | Trades |
|------|----|-----|--------|
| **ARIA** | 13.83 | 83% | 6 |
| **SAND** | 6.79 | 75% | 4 |
| **DEGO** | 2.82 | 57% | 7 |

### Strategy 18: ROC Momentum 1H/4H (Round 9, 1% risk)
Signal: ROC(10) zero-cross + EMA(50) trend. SL 2.0 ATR, TP 4.0 ATR.
| Coin | TF | PF | WR% | Trades |
|------|----|----|-----|--------|
| **TSLA** | 1H | 2.72 | 65% | 17 |
| **RIVER** | 1H | 1.78 | 44% | 34 |
| **TON** | 4H | 1.52 | 57% | 42 |
| **XMR** | 4H | 1.20 | 42% | 31 |

### Strategy 19: Other Round 9 strategies (1% risk each)
| Coin | Strategy | TF | PF | WR% | Trades |
|------|----------|----|-----|-----|--------|
| **APR** | PriceChannel+Vol | 4H | 98.10 | 75% | 4 |
| **CRCL** | PriceChannel+Vol | 1H | 3.89 | 56% | 9 |
| **KAS** | Williams %R+ADX | 4H | 2.09 | 44% | 9 |
| **DASH** | Supertrend+Vol | 4H | 1.56 | 25% | 4 |
| **DOT** | Supertrend+Vol | 4H | 1.51 | 33% | 6 |

### Strategy 20: Dual Thrust (34 bots, 1% risk each, R10+R12)
Signal: Previous-day high/low range × multiplier sets breakout levels above/below open. Entry on breakout. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: GALA (PF 7.84), PHA (PF 4.39), ZEC (PF 3.41), DOT (PF 7.77), AVAX (PF 3.23), ARC (PF 2.50)

### Strategy 21: Range Bounce (19 bots, 1% risk each, R10)
Signal: Bollinger Band %B < 0.2 (oversold) or > 0.8 (overbought) + RSI reversal + range detection filter (CI > 50). SL 2.0 ATR, TP 3.0 ATR.
Top verified coins: AXS (PF 13.46, 92% WR), FIL (PF 3.25)

### Strategy 22: Awesome Oscillator (18 bots, 1% risk each, R10)
Signal: AO zero-cross + Ichimoku cloud direction filter on 4H. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: VVV (PF 2.81), SAND (PF 2.88), ALICE (PF 1.97)

### Strategy 23: Z-Score Mean Reversion (19 bots, 1% risk each, R11)
Signal: Statistical z-score of price vs rolling 20-period mean crosses ±1.5σ threshold + EMA(50) filter prevents counter-trend trades. SL 2.0 ATR, TP 3.0 ATR.
Top verified coins: AKT (PF 2.94), CRV (PF 5.82), VVV (PF 6.17), PENGU (PF 6.01), POL (PF 3.57)

### Strategy 24: Stoch MTF (8 bots, 1% risk each, R11)
Signal: Stochastic crossover on signal timeframe confirmed by same direction on higher timeframe. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: AXS (PF 5.86)

### Strategy 25: EMA Ribbon (11 bots, 1% risk each, R11+R12)
Signal: Fan of 6 EMAs (8/13/21/34/55/89) must all align in same direction; entry when fast EMA crosses slow EMA. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: IP (PF 2.45), OP (PF 1.95), AVAX (PF 2.10), KAS (PF 1.95), BERA (PF 2.06)

### Strategy 26: Ichi+ADX (5 bots, 1% risk each, R12)
Signal: Ichimoku Tenkan/Kijun cross with ADX > 20 confirmation for trend strength. SL 2.0 ATR, TP 4.0 ATR.

### Strategy 27: Ribbon+AO (3 bots, 1% risk each, R12)
Signal: EMA Ribbon alignment + Awesome Oscillator momentum direction agreement. SL 2.0 ATR, TP 4.0 ATR.

### Retired (no edge found in 1yr backtest)
| Coin | Was | Best PF | Reason |
|------|-----|---------|--------|
| **DOGE** | EMA 15m | 1.11 | All strategies PF < 1.2 |
| **SOL** | Ichi 1H | 1.17 | All strategies PF < 1.2 |

**Portfolio total: 167 bots across 27 strategy types**
**$200 shared wallet → $2,036 (+918%) AUDITED backtest (R-multiple fixed, max 5 concurrent, 1yr)**
**1,449 trades | DD 10.3% | 11/13 months profitable**
**Deployed: single process via main_multi.py, ~340 MB RAM total**
**6 weak-bot upgrades: PENGU/POL→Ichi4H Trail, AVAX/KAS→EMA Ribbon 4H, ALICE→AO 4H, DASH→Ichi4H**

```bash
# Run all 167 bots (single process, ~340 MB RAM)
bash scripts/start_all_bots.sh

# Or run by group
python main_multi.py --group ichimoku-1h
python main_multi.py --group ichimoku-4h
python main_multi.py --group dualthrust
python main_multi.py --group ema-ribbon

# Dashboard (local)
streamlit run dashboard/app.py
```

## Portfolio Backtest Methodology (Corrected)

Shared wallet backtest must follow these rules to avoid inflated results:

### R-Multiple Method (correct)
```python
# For each bot: track engine balance separately
engine_balance = 10000.0
for trade in bot_trades:
    engine_risk = engine_balance * risk_per_trade  # what engine risked
    r_multiple = trade.pnl / engine_risk           # actual R won/lost
    engine_balance += trade.pnl                    # engine compounds

# Shared wallet replay:
shared_risk = shared_balance * risk_per_trade      # 1% of shared balance
dollar_pnl = shared_risk * r_multiple              # apply R to shared wallet
shared_balance += dollar_pnl
```

### Common Bugs to Avoid
- **Double compounding**: NEVER do `pnl_frac = pnl / initial_balance` then `balance * pnl_frac` — this inflates 30-100% because engine compounds internally
- **R-multiple risk mismatch**: When computing R-multiple, use ACTUAL engine risk (`config['risk_per_trade']`), not fixed 1%. BTC uses 5% risk → PnL 5x larger → if you divide by 1% you get R 5x too high. Fix: `r = pnl / (engine_bal × actual_risk)`, shared wallet always applies 1%
- **No concurrent limit**: Must cap simultaneous open positions (max 5) — 61 bots on $200 can't all trade at once
- **Loose filters**: Require data >= 12 months AND trades >= 10 per bot — otherwise statistically meaningless

### Validation Filters
- **Data minimum**: 12 months of hourly data (coin must have been listed >= 1yr)
- **Trade minimum**: 10 trades in backtest period (statistical significance)
- **Concurrent limit**: Max 5 open positions at any time
- **Data snooping**: Be aware that selecting best from 8,700+ backtests inflates results

### Audit Checklist (before deploying any new strategy)
1. **Signal verification** — print every trade, verify indicator values at entry match conditions
2. **Look-ahead check** — signal uses iloc[-2] (closed candle), entry at iloc[-1] close
3. **Fee check** — verify PnL includes commission + slippage correctly
4. **Walk-forward test** — split data in half, run each separately. OOS PF should be >= 60% of IS PF
5. **Trade count** — minimum 15 trades for deployment. Under 15 = high variance / luck
6. **Baseline comparison** — compare vs current strategy on SAME data period
- If walk-forward degrades > 40% OR trades < 15: **REJECT** regardless of PF
- Script template: `research/audit_btc_wif.py`

### Realistic Results History
| Round | Bots | Return | DD | Trades | Method | Status |
|-------|------|--------|-----|--------|--------|--------|
| R8 | 14 | +149% | 8.1% | 320 | Conservative | ✅ Audited |
| **Final** | **125** | **+918%** | **10.3%** | **1,449** | **All rounds + 6 upgrades** | **✅ AUDITED (R-multiple fixed)** |

**⚠️ Previous results were inflated by R-multiple risk bug:**
- R10 (+453%), R11 (+689%), R12 (+795%), R12 Full (+1,611%) — ALL had bug where BTC (5% risk) and WIF (3% risk) got 3-5x inflated PnL in shared wallet
- Bug: `dollar_pnl = balance × bot_risk_pct × R` instead of `balance × 0.01 × R`
- Scripts affected: full_portfolio_74.py, round10/11_verify_backtest.py (NOT portfolio_backtest_realistic.py)
- Only `final_backtest.py` (fixed) and `portfolio_backtest_realistic.py` produce correct results

### Scripts
- `research/final_backtest.py` — **CURRENT** full portfolio (R-multiple FIXED, 125 bots)
- `research/portfolio_backtest_realistic.py` — corrected R-multiple (14 bots only)
- `research/full_portfolio_74.py` — DEPRECATED (R-multiple risk bug)
- `research/round10_verify_backtest.py` — DEPRECATED (R-multiple risk bug)
- `research/round11_verify_backtest.py` — DEPRECATED (R-multiple risk bug)
- `research/portfolio_backtest_v2.py` — DEPRECATED (double compounding bug)
- `research/portfolio_backtest_full.py` — DEPRECATED (per-bot $200, not shared wallet)

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
- **167 bots in a single process** via `main_multi.py` (shared ccxt exchange pool)
- **RAM**: ~340 MB total (vs ~25 GB if running 167 separate Docker containers)
- **Start/stop**: `bash scripts/start_all_bots.sh` / `bash scripts/start_all_bots.sh stop`
- **Dashboard**: Streamlit local on port 8501 (`streamlit run dashboard/app.py`)
- **Per-bot mode control**: mode files in `data/mode_{symbol}.json` (read each loop tick)
- **Exchange**: Binance testnet (set `use_testnet: false` in configs to go live)
- Docker deployment still supported for VPS: `docker compose up -d --build`
- Nginx reverse proxy (HTTPS, basic auth, rate limiting) for VPS dashboard
- Monthly cost: ~$7-17 (Hetzner VPS + Claude API)

## Agent Team

11 specialized agents configured as teammates that can collaborate via shared task lists and direct messaging. Enable via `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (already set in `.claude/settings.json`).

### Team Structure
```
                        ┌──────────────┐
                        │ ORCHESTRATOR │ Research loop coordinator
                        │    opus      │ Discover→Test→Verify→Deploy
                        └──────┬───────┘
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
         ┌──────────┐  ┌──────────┐  ┌──────────────┐
         │    PM    │  │ Strategy │  │    Quant      │
         │  sonnet  │  │  Scout   │  │  Researcher   │
         │ Planning │  │  Ideas   │  │  Statistics   │
         └────┬─────┘  └──────────┘  └──────────────┘
              │
    ┌─────────┼─────────┬─────────────┐
    ▼         ▼         ▼             ▼
┌────────┐┌────────┐┌──────────┐┌──────────┐
│   SA   ││ Trader ││  Crypto  ││    QA    │
│  opus  ││ Expert ││  Expert  ││ Verifier │
│ Arch.  ││Strategy││ Domain   ││ Bugs/Bias│
└───┬────┘└────────┘└──────────┘└──────────┘
    │
┌───┼─────────┬──────────┐
▼   ▼         ▼          ▼
┌────────┐┌────────┐┌────────┐
│Backend ││Frontend││ DevOps │
│  Dev   ││  Dev   ││        │
│ sonnet ││ sonnet ││ sonnet │
└────────┘└────────┘└────────┘
```

### Research Flow (agents communicate in this order)
```
1. DISCOVER:  orchestrator → [strategy-scout + crypto-expert + quant-researcher] (parallel)
2. DESIGN:    orchestrator → [quant-researcher + trader-expert] (parallel)
3. SWEEP:     orchestrator → backend-dev → quant-researcher (sequential)
4. VERIFY:    orchestrator → [qa-verifier + trader-expert] (parallel, NEVER skip)
5. IMPLEMENT: orchestrator → backend-dev → qa-verifier → sa (sequential)
6. DEPLOY:    orchestrator → devops → frontend-dev (parallel)
```

### Agent Files: `.claude/agents/`
| Agent | Model | Role | Skills (12 total) |
|-------|-------|------|-------------------|
| `orchestrator` | opus | Research Loop Coordinator | team-orchestrator, research, trader-memory-core, scenario-analyzer, backtest-expert |
| `pm` | sonnet | Product Manager | scenario-analyzer, trader-memory-core, team-orchestrator |
| `strategy-scout` | sonnet | Strategy Idea Discovery | strategy-pivot-designer, edge-pipeline-orchestrator, technical-analyst, market-news-analyst, scenario-analyzer |
| `quant-researcher` | opus | Experiment Design & Statistics | backtest-expert, position-sizer, technical-analyst, macro-regime-detector, crypto-signal-validator |
| `qa-verifier` | opus | Bug/Bias Detection & QA | backtest-expert, crypto-signal-validator, position-sizer, technical-analyst |
| `sa` | opus | Solution Architect | backtest-expert, edge-pipeline-orchestrator |
| `trader-expert` | opus | Trading Strategy Authority | technical-analyst, backtest-expert, position-sizer, macro-regime-detector, strategy-pivot-designer, crypto-signal-validator, trader-memory-core |
| `crypto-expert` | opus | Crypto Domain Authority | macro-regime-detector, market-news-analyst, scenario-analyzer, technical-analyst |
| `backend-dev` | sonnet | Backend Developer | crypto-signal-validator, position-sizer, backtest-expert |
| `frontend-dev` | sonnet | Frontend Developer | technical-analyst |
| `devops` | sonnet | DevOps Engineer | (infra focused) |

### Usage
```bash
# Run a full research round (orchestrator manages everything)
claude --agent orchestrator "Run research round 11: find 10 new strategies, sweep, verify, deploy"

# PM for non-research complex tasks
claude --agent pm "Plan the gold bot deployment to OANDA"

# Direct specialist access
claude --agent trader-expert "analyze trailing stop performance"
claude --agent strategy-scout "find 10 new strategy ideas from GitHub repos with >1K stars"
claude --agent quant-researcher "analyze portfolio correlation and find gaps"
claude --agent qa-verifier "verify round 10 backtest results for look-ahead bias"
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
