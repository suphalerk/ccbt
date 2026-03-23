---
name: sweep_improve_weak 2026-03-22
description: 30 weak/negative-PnL coins × 10 strategies × 10 param sets × 2 TF sweep results
type: project
---

30 weak coins swept with 10 strategies × 10 param sets × 2 TF (1H+4H) = 5,940 combos.
Results: 682 combos passing PF>=1.2 trades>=8. 29/30 coins have at least one passing combo.
Script: `/Users/iceai/Work/ccbt/research/sweep_improve_weak.py`
JSON output: `/Users/iceai/Work/ccbt/data/sweep_improve_weak.json`

**Why:** These coins had negative PnL with their deployed strategies (DualThrust/Ichimoku/etc.)
and needed fresh strategy/parameter combinations to become profitable.

**Key findings:**
- zscore_meanrev 1H dominates: VVV PF 6.17, CRV PF 5.82, ZEC PF 4.75, UNI PF 4.75, FARTCOIN PF 4.46
- awesome_osc 4H strong for: ANIME PF 3.11, XAI PF 2.48, VVV PF 3.13, ALICE PF 1.52
- ichi_adx 4H strong for: DOT PF 2.21, SUI PF 1.75, TON PF 2.44, ADA PF 2.29
- ichimoku_cloud 4H trail_only strong for: PENGU PF 3.00, POL PF 2.49
- ema_ribbon 4H strong for: GALA PF 1.73, ATOM PF 2.44, KAS PF 2.23, AVAX PF 1.89
- supertrend 4H strong for: ZEN PF 1.82, XAI PF 2.48, AAVE PF 1.96

**IMPORTANT - lightweight sim only:** These are lightweight simulator numbers, NOT BacktestEngine.
All top candidates must be verified with BacktestEngine before deployment.
Historical pattern: ~30-40% of lightweight winners survive full engine verification.

BCH is the only coin with NO passing combo (BCH best is range_bounce PF 0.99).

**How to apply:** Next step is BacktestEngine verification of top candidates — prioritize
zscore_meanrev coins (VVV/CRV/ZEC/UNI/FARTCOIN/ICP) and awesome_osc 4H (ANIME/XAI).
