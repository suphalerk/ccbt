---
name: project_full_portfolio_74_2026_03_22
description: Full 147-bot combined portfolio backtest across all rounds — $200 shared wallet, R-multiple, max 5 concurrent
type: project
---

Full combined portfolio backtest run on 2026-03-22 using `research/full_portfolio_74.py`.

Results: $200 → $1,789.58 (+794.8%) over 1 year.

**Why:** Unified all bots from every research round (R1-R12) into a single shared-wallet replay to measure true combined edge.

**How to apply:** When user asks about full portfolio performance, reference these numbers. The script at `research/full_portfolio_74.py` is the authoritative source.

Key stats:
- 154 bots built, 147 ran (7 skipped: no data or < 8 trades)
- 1541 total trades across 136 unique bots
- Max drawdown: 23.0% ($411.87 from peak)
- Best months: Nov 2025 (+$616.59), Mar 2026 (+$342.77)
- Worst months: Dec 2025 (-$187.83), Jun 2025 (-$38.08)

Top wallet PnL bots:
- BTC EMA 15m: +$471.86 (10 trades, 50% WR)
- BERA ema_ribbon R11: +$305.50 (49 trades, 57% WR)
- DEGO dual_thr R10: +$77.29

R12 combo bots contribution:
- AAVE dualthrust_adx: +$55.22 (6 trades, 67% WR)
- ENJ ichi_adx contributed positively
- SUI/DOT ribbon_ao dragged slightly negative

Output saved to `data/full_portfolio_74.json`.

Skipped bots:
- GUN Ichi 1H (no data)
- ATH Ichi 1H (no data)
- ZEC range_bo R10 (4 trades only)
- DASH dual_thr R10 (5 trades only)
- ALICE range_bo R10 (5 trades only)
- LINK dualthrust R12 (5 trades only)
- BCH dualthrust R12 (5 trades only)
