---
name: ton-diagnosis-2026-06-06
description: TONUSDT net-loser diagnosis — both deployed strategies (Awesome Osc, Range Bounce) fail full 2yr backtest; long bias + downtrend kills it
metadata:
  type: project
---

TONUSDT is a net-losing testnet coin. Diagnosed 2026-06-06.

**Live (trades.db, ex 2026-03-23 glitch):** 13 trades, 15.4% WR, net -$201.48, 100% closed at SL. Longs 0/7 (-$136), shorts 2/6 (-$65). Long side is the primary bleeder.

**Backtests on 2yr 1h data (TON fell -67.6% over the window — sustained downtrend):**
- Awesome Osc 1H (config_tonusdt_awesome.json, SL2.0/TP4.0): full PF 1.01, WR 32%, 44 tr. WF IS 0.91 / OOS 0.91. No edge ever. LONG -$316 (23% WR) vs SHORT +$332 (44% WR). Verdict WEAK→BROKEN; longs are the bleeder.
- Range Bounce 1H (config_tonusdt_rangebounce.json, SL1.5/TP2.0): full PF 0.79, WR 38%, 48 tr, NEG Sharpe -0.54. WF IS 0.68. BROKEN. SHORT -$687 (32% WR) — mean-reversion shorting bounces in a one-way downtrend.

**Root cause:** R10 deploy PFs were data-snooped; neither strategy holds on full 2yr. TON has no trend-following or mean-reversion edge. Both fail walk-forward / baseline.

**Why:** TON is in a structural downtrend; Awesome Osc keeps taking failed longs, Range Bounce keeps shorting bounces that snap back.

**How to apply:** Recommend retiring both. If kept, restrict Awesome Osc to SHORT-ONLY (was profitable side) but PF still <1.2 so prefer retire. Cross-check any future TON config against full-2yr + walk-forward before redeploy.
