# Trading Strategies

> Strategy reference for CCBT. See [portfolio.md](portfolio.md) for which coins run which strategy, and [research-history.md](research-history.md) for how each was discovered.

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

## Multi-Coin Day Trading Portfolio — Techniques Used
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
