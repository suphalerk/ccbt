---
name: Round 2 Scaling Analysis
description: Round 2 is profitable (PF 1.10, 87 trades/2yr) but needs 5-10x more trades. Filter-by-filter analysis shows volume (1.3x) and EMA slope (0.02%) are biggest bottlenecks, not ATR. Kelly optimal is 1.77% (half Kelly). Need 20-30 trades/mo at 1.5% risk for target returns.
type: project
---

Round 2 backtest: 87 trades, WR 39.1%, PF 1.10, R:R 1.72, Max DD 7.5%, +3.53% in 2 years.

**Why:** Edge exists (6.08 cents per dollar risked) but is barely captured at 3.6 trades/month. Need to scale frequency 5-10x while preserving or improving edge.

**Key findings from filter analysis:**
1. `atr_min: 50` is NOT the bottleneck (99.8% of candles pass it anyway, since BTC 15m ATR median is $252)
2. `volume_mult: 1.3` kills 68% of signals (only 32% pass)
3. `ema_slope_min: 0.02` kills another 29% of remaining signals
4. Weekend disabled + trading hours skip 00-03 removes another ~40%
5. Regime filter + cooldowns remove another ~45%
6. Net: 251 raw signals / 2 years = 10.5/month, ~87 trades after all filters

**Scenario projections (raw signals -> est trades over 2 years):**
- Current: 251 raw -> ~138 trades (5.8/mo)
- Vol 1.0 + slope 0.01: 359 -> 197 (8.2/mo) -- SAFE
- + weekends enabled: 534 -> 294 (12.2/mo) -- MODERATE
- + lookback 3: 1120 -> 616 (25.7/mo) -- AGGRESSIVE, needs backtest validation
- Remove vol+slope entirely: 969 -> 533 (22.2/mo) -- RISKY, reduces quality

**Kelly analysis:** Full Kelly = 3.55%, Half Kelly = 1.77%. Current 1% is quarter Kelly = very conservative.

**How to apply:** Prioritized 7-tier plan in Round 2 scaling recommendations. Must backtest each tier before combining.
