---
name: expansion_research_plan_2026_03_21
description: 6-track strategy research plan to find 5-15 more tradeable coins from 90 failed sweep coins. Tracks: fast Ichimoku, 4H Ichimoku, long-only, SL/TP sweep, ROC momentum, EMA 12/26. CRCL/GUA/BEAT rejected (insufficient data).
type: project
---

## Context
- 90 coins failed the initial 5-variant sweep (EMA 9/21 + Ichimoku 9/26/52 fixed)
- Only 40 of 90 have full 2yr data suitable for backtesting
- CRCL (39d data), GUA (89d), BEAT (128d) all have insufficient history -- results are noise

## 6 Strategy Tracks (priority order)
1. **Long-Only Ichimoku 1H** (40 coins, ~80 runs) -- highest p(success) ~45%, but bull-window bias risk
2. **Ichi-Fast 7/22/44 on 1H** (40 coins, ~40 runs) -- p ~35%, addresses slow-cloud problem
3. **4H Ichimoku** (25 large-caps, ~50 runs) -- p ~30%, noise reduction for liquid coins
4. **SL/TP Sweep** (40 coins, ~480 runs) -- p ~25%, high overfit risk
5. **ROC Momentum** (25 large-caps, ~50 runs) -- p ~15%, works on gold, unclear on crypto
6. **EMA 12/26 on 1H** (25 large-caps, ~25 runs) -- p ~10%, already tested EMA on 1H with poor results

## Acceptance Criteria
- Min 500 days data, 40+ trades, PF >= 1.3, DD <= 35%, WR 30-55%
- Not a corner solution (neighbors must also work)
- Long-only requires regime gate (BTC > 200d SMA)

## Tier 1 Target Coins
BNB, LTC, TON, HBAR, FET, RENDER, KAS, TIA

**Why:** These are the most structured, highest-probability coins to unlock edge. Track 3 (long-only) is fastest to test and most likely to produce winners.

**How to apply:** Run tracks in priority order. Stop early if we hit 10+ winners before Track 4 (SL/TP sweep has highest overfit risk).
