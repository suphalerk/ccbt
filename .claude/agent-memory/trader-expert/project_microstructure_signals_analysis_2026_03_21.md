---
name: Microstructure Signals Analysis (Polymarket-Inspired)
description: Deep analysis of adapting Polymarket AI bot's order flow signals for BTC perp day trading. Taker buy/sell ratio = high potential (partially backtestable). Funding rate = moderate. OI change = moderate-high. Order book/liquidation = skip. Recommendation: Option B (filters + size modifiers on existing EMA crossover, NOT standalone signals).
type: project
---

Analyzed Polymarket AI trading bot architecture (6 signals, RAG, self-learning) for applicability to our BTC perpetual futures strategy.

**Why:** Our 250+ backtests ALL used OHLCV data only. Microstructure signals (taker ratio, OI, funding) measure participant behavior directly -- fundamentally different from price-derived indicators.

**Key findings:**

1. **Taker Buy/Sell Ratio** (HIGHEST PRIORITY): Independent from OHLCV, partially backtestable via Binance aggTrades API (isBuyerMaker flag). Aggregate into 15m candle-aligned ratios. Use as confirmation filter: taker_ratio > 1.2 for longs, < 0.8 skip. 1-2 years historical data available.

2. **Open Interest Change**: Fully backtestable via Binance OI API. OI divergence from price = powerful signal (price up + OI falling = short squeeze, fragile). Use as quality modifier, not hard filter.

3. **Funding Rate**: Fully backtestable but only 3 updates/day (8h cycle). Low frequency, heavily arbitraged. Use only as extreme crowding veto (> +0.03% skip longs).

4. **Order Book / Liquidations / Cross-exchange**: NOT backtestable, skip entirely.

**What NOT to copy from Polymarket:**
- Their 48.7% WR / 0.6% ROI is statistically indistinguishable from random (128 trades)
- Auto-adjusting signal weights requires 500+ trades per signal (we have 38/yr)
- RAG self-learning is premature -- our AI advisor calibration is already better designed
- Kelly on $1 bets vs 25x levered futures is completely different risk domain

**Architecture decision: Option B** -- microstructure as filters + size modifiers on proven EMA crossover.
- NEVER standalone entry signals (Polymarket proved this doesn't work)
- Size modifier preferred over hard filter (preserve trade count > 50/2yr)
- Disable in ranging regime

**Critical risk:** Over-filtering 38 trades/yr to 15-20 = destroys statistical significance. Must test as size modifier FIRST, hard filter only if evidence supports it.

**Success criteria:** PF 1.55+ (from 1.51), trade count > 50/2yr, DD < 20%.
**Blocker:** Taker ratio data collection (2 days Backend Dev).

**How to apply:** Phase 1 data collection -> Phase 2 backtest integration -> Phase 3 signal enhancement -> Phase 4 validation. Total ~12 days. Do not proceed past Phase 2 without backtest evidence.
