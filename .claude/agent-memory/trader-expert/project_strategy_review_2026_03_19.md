---
name: Strategy Deep-Dive Review March 2026
description: Comprehensive analysis of why the EMA crossover strategy produced only 64 trades in 4 months then zero for 20 months, with specific parameter fix recommendations
type: project
---

Root cause: Over-filtering. Six boolean AND gates stacked on a rare event (exact EMA crossover candle) creates near-zero signal frequency. The crossover candle only occurs 2-4 times/day, and requiring RSI + volume + trend filter + R:R all on that single candle drops probability to ~0.13% per candle.

**Why:** 2-year backtest (2024-03 to 2026-03) showed 64 trades in first 4 months, then ZERO for 20 months. Win rate 43.8%, PF 0.91 (losing money), avg R:R 1.16.

**How to apply:** Three priority tiers of fixes identified:

Priority 1 (config only): Widen RSI to 40-75 long / 25-55 short, drop volume_mult to 0.8, remove volume_max_mult, widen stops to ATR x 2.0 SL / 4.0 TP, reduce cooldowns, lower min_rr to 1.3.

Priority 2 (code required): Add ema_cross_lookback of 5 candles (highest single impact), add trend_filter_buffer_pct of 0.5% around EMA(50), add max_holding_candles of 48.

Priority 3 (structural): EMA slope quality scoring, 4h timeframe layer, regime-adaptive parameters.

Expected outcome: 400-700 trades over 2 years, 46-50% win rate, 1.3-1.6 PF, 1.5-2.0 R:R, 8-12% max DD.
