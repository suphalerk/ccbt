---
name: Round 12: Combo strategy verification results
description: 5 combo signals verified against full engine from sweep_mega100.json; 23/120 pass; DualThrust+ADX is the dominant winner
type: project
---

Verified 120 qualifying combo candidates (PF >= 1.3, trades >= 10, 365+ days data) from sweep_mega100.json using 5 combo signals implemented in the engine: ribbon_rsi_vol, dualthrust_adx, zscore_stoch, ichi_adx, ribbon_ao.

**Why:** Following rounds 10-11 which implemented these 5 combo signals in the engine, this round verifies which coins actually pass the full engine with realistic fees/slippage/risk management (vs the lightweight sweep sim).

**How to apply:** When deploying new combo bots, use combo_results.json passed list as the source of truth. Prefer DualThrust+ADX and Ichi+ADX over ZScore variants.

## Key findings

- 23/120 passed (19% pass rate — lower than round 10-11 single-signal strategies)
- DualThrust+ADX: dominant combo — 13 passes (AAVE PF 2.20, ANKR PF 2.23, AXS PF 2.07, FIL PF 2.28, ENJ PF 2.87, LINK PF 1.96, SAND PF 1.90, OP PF 1.59, WAXP PF 2.00, ATOM PF 1.50, BCH PF 1.47, APT PF 1.66, ZRO PF 1.38)
- Ichi+ADX: 5 passes (ENJ PF 3.56, LTC PF 2.56, SAND PF 1.65, APT PF 1.61, XMR PF 1.21)
- Ribbon+AO: 3 passes (DOT PF 1.44 47 trades, PIXEL PF 1.42 63 trades, SUI PF 1.25 46 trades)
- ZScore+BB and ZScore+Stoch: only XAUUSD passes (PF 1.30) — consistent with mean-reversion failing on crypto
- Ribbon+RSI+Vol: 0 passes — worst combo, all fail full engine despite good sweep numbers

## Portfolio replay (27 bots: 4 existing + 23 new)
- $200 → $410 (+105%) over ~2 years
- Note: this replay only covers the 4 bots from mega100_results['passed'] + 23 new combos, NOT the full 31-bot portfolio from all rounds

## Script
- `/Users/iceai/Work/ccbt/research/combo_verify_backtest.py`
- Outputs: `/Users/iceai/Work/ccbt/data/combo_results.json`

## Lessons learned
- BacktestMetrics has no `final_balance` attr — use `engine.state.balance` and `engine.state.trades` directly
- Engine prints verbose results to stdout per run — wrap with context manager to suppress
- Timezone handling: engine trade timestamps may be tz-aware; strip with `tz_convert("UTC").tz_localize(None)` before sorting
