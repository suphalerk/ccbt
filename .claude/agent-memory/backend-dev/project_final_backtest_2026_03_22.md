---
name: project_final_backtest_2026_03_22
description: Final definitive portfolio backtest: all R1-R12 bots + 6 weak-coin upgrades, $200→$3,421 (+1610%)
type: project
---

Ran `research/final_backtest.py` on 2026-03-22.

**Result: $200 → $3,421.56 (+1610.8%), 1449 trades, 12.4% max DD**

Previous full_portfolio_74 baseline: $200 → $1,789.58 (+794.8%), 23% max DD

**Why:** Added 6 verified weak-coin upgrade bots and removed all losing versions of those coins.
Max drawdown improved from 23% → 12.4% (removal of losing bots helps).

**Upgrade coins removed:** ALICE, AVAX, DASH, KAS, PENGU, POL (all losing bot versions stripped)

**6 upgrade bots engine results (on $10K):**
- DASH Ichi4H: 21 trades, 62% WR, PF 3.54, +$1,945
- AVAX EMA Ribbon 4H: 22 trades, 50% WR, PF 1.73, +$1,301
- POL Ichi4H Trail: 17 trades, 59% WR, PF 2.89, +$993
- KAS EMA Ribbon 4H: 15 trades, 40% WR, PF 2.12, +$738
- PENGU Ichi4H Trail: 8 trades, 25% WR, PF 2.50, +$616
- ALICE AO 4H: 18 trades, 39% WR, PF 1.27, +$156

**Script:** `/Users/iceai/Work/ccbt/research/final_backtest.py`
**Output:** `/Users/iceai/Work/ccbt/data/final_backtest.json`

**Portfolio construction:** 142 bot definitions → 135 ran (7 skipped: GUN/ATH no data, TON/XMR/ZEC/LINK/BCH too few trades), 125 contributed trades.

**How to apply:** Use final_backtest.json as the new baseline for any subsequent round comparisons.
