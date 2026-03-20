---
name: BTC Price Action + Day Trading Research
description: Research task to add Price Action patterns and day trading capability to BTC strategy (2026-03-20)
type: project
---

BTC Price Action research delegated to team on 2026-03-20.

**Why:** Current EMA crossover strategy generates only ~20 trades/yr. User wants more trades per day (day trading) and wants Price Action patterns tested — never tried before despite being a proven category in trend markets.

**Baseline:** EMA(9/21)+EMA(5/13) on 15m, R10%/L25x, PF 1.42, ~40%/yr avg, ~103 trades in 5 years.

**What has failed already:** Mean reversion, MACD, EMA pullback, RSI divergence, Donchian, BB Squeeze, VWAP, EMA Fan, Momentum Breakout, Body Dominance + Squeeze Release (look-ahead bias).

**Key hypotheses to test:**
1. Price Action patterns as standalone signal (pin bar, engulfing, inside bar)
2. PA as confirmation filter on existing EMA crossover
3. PA on 5m timeframe for day trading (more trades)
4. Combined best PA patterns with existing strategy

**How to apply:** When reviewing results, focus on: (1) no look-ahead bias — all signals must use iloc[-2], (2) more trades is only better if PF stays above 1.3, (3) day trading on 5m needs fee drag analysis, (4) quantitative definitions for PA patterns are critical.
