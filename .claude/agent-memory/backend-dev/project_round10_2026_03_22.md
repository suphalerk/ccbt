---
name: Round 10 GitHub Strategies
description: Round 10 results — 3 new strategies implemented, 76/180 verified, portfolio $200→$1,106 (+453%)
type: project
---

## Round 10: GitHub Strategy Implementation + Verification (2026-03-22)

**Why:** Sweep run against GitHub trading strategy repos produced 1,821 entries; 3 strategies implementable in engine.

**How to apply:** Use these bots as candidates for next portfolio expansion; check for deployment-ready configs.

### Strategies Implemented

| Signal Key | Engine Signal | Description |
|---|---|---|
| `dual_thrust` | G1_DualThrust | Price breaks above/below close ± k × N-bar range; EMA(50) trend filter |
| `awesome_oscillator` | G3_AwesomeOscillator | AO (SMA5-SMA34 of midpoints) zero-cross; EMA(50) trend filter |
| `range_bounce` | G4_RangeBounce | Mean-reversion near rolling support/resistance in narrow range; NOT gated by trend_signals_gated |

Not implemented: G2_KalmanTrend (stateful Kalman filter too complex), G5_Confluence (composite score, no direct engine mapping).

### Files Changed

- `bot/data.py` — Added `compute_dual_thrust()`, `compute_awesome_oscillator()`, plus indicator blocks in `add_indicators()` for all 3 (each computes `ema50` for trend filter if not already present)
- `bot/strategy.py` — Added `check_dual_thrust_conditions()`, `check_awesome_oscillator_conditions()`, `check_range_bounce_conditions()`
- `backtest/engine.py` — Added 3 new imports + signal queue entries; `range_bounce` placed OUTSIDE `trend_signals_gated` block

### Verification Results (180 candidates, last-year data, PASS = PF≥1.2 AND trades≥8)

**Passed: 76/180**

Top verified winners (deployed-coin-free):

| Coin | Strategy | TF | Engine PF | Trades | WR% | DD% |
|------|----------|----|-----------|--------|-----|-----|
| GALA | DualThrust | 1H | 7.84 | 10 | 80% | 1.2% |
| PHA | DualThrust | 4H | 4.39 | 29 | 62% | 2.0% |
| ZEC | DualThrust | 1H | 3.41 | 10 | 60% | 1.0% |
| PENGU | DualThrust | 1H | 3.29 | 10 | 70% | 3.0% |
| FIL | RangeBounce | 1H | 3.25 | 11 | 64% | 2.1% |
| PIXEL | DualThrust | 1H | 3.05 | 8 | 62% | 2.0% |
| ICP | DualThrust | 1H | 2.97 | 8 | 50% | 1.8% |
| SAND | AwesomeOscillator | 4H | 2.88 | 32 | 62% | 3.1% |
| VVV | DualThrust | 4H | 2.85 | 15 | 60% | 2.9% |
| DEGO | DualThrust | 4H | 2.54 | 23 | 65% | 2.0% |

### Portfolio Backtest Results ($200, max 5 concurrent, R-multiple)

62 bots total (14 existing + 48 new verified not already deployed).

| Month | Trades | PnL$ | Balance$ |
|-------|--------|------|---------|
| 2025-03 | 33 | +19.89 | 219.89 |
| 2025-04 | 108 | -30.32 | 189.57 |
| 2025-05 | 118 | +73.00 | 262.57 |
| 2025-06 | 95 | +63.02 | 325.58 |
| 2025-07 | 130 | +77.37 | 402.95 |
| 2025-08 | 127 | -48.71 | 354.24 |
| 2025-09 | 120 | +105.26 | 459.50 |
| 2025-10 | 145 | +37.02 | 496.52 |
| 2025-11 | 117 | +256.46 | 752.99 |
| 2025-12 | 118 | -126.57 | 626.42 |
| 2026-01 | 134 | +201.19 | 827.61 |
| 2026-02 | 131 | +118.18 | 945.79 |
| 2026-03 | 76 | +159.76 | 1105.55 |

**Final: $200 → $1,106 (+453%) | PF=1.20 | WR=39% | MaxDD=34% | 1,452 trades | 62 bots**

### Key Findings

- DualThrust works well on altcoin 4H (DOT, DEGO, VVV, PIXEL); lower-count 4H bots pass with high PF but fewer trades (risk: small sample)
- RangeBounce has unique "ranging gate" (rb_is_ranging via rolling percentile), correctly excluded from trend regime gate
- AwesomeOscillator 4H on SAND, ADA, PENGU passes with solid trade count (17-32 trades)
- MaxDD 34% is driven by large number of bots and concurrent exposure — portfolio risk management still acceptable for $200 starting wallet
- Range Bounce (mean-reversion) adds diversification — opposite regime from Ichimoku/EMA trend bots

### Config params to set for each strategy type

**dual_thrust:** `dual_thrust_lookback=20`, `dual_thrust_k=0.5-0.7`, `atr_sl_mult=1.5`, `atr_tp_mult=3.0`
**awesome_oscillator:** `atr_sl_mult=1.5-2.0`, `atr_tp_mult=3.0-4.0`
**range_bounce:** `range_bounce_lookback=20-30`, `range_bounce_rsi_lo=30-40`, `range_bounce_rsi_hi=60-70`, `range_bounce_atr_sl_mult=1.0-1.5`, `range_bounce_atr_tp_mult=1.5-2.0`
