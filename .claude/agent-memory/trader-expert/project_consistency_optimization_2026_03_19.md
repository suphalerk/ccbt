---
name: Consistency Optimization Analysis
description: Top 3 approaches to push from 43.6% avg monthly to consistent 25-100%. Baseline: 120 trades/2yr, PF 2.29, WR 34.2%, 10x lev, 3% risk. Core insight — frequency (5 trades/mo) is the bottleneck, not edge quality.
type: project
---

Baseline (2-year BT, $100 start): 120 trades, WR 34.2%, PF 2.29, Sharpe 1.56, DD 11.56%, +1046% total.
Monthly: avg +43.6%, 22% months with 25%+, 30% losing months.

**Why:** 5 trades/month creates wild monthly variance. Need 12-15+ trades/month for law of large numbers to smooth returns.

**Top 3 Approaches:**

1. **5m Sub-Signals** (HIGHEST PRIORITY): Use 5m EMA(5/13) crossover when 15m trend is aligned. Expected 12-18 trades/mo. PF may drop to ~1.8 but consistency dramatically improves. Months with 25%+ from 22% -> 50-60%. Losing months from 30% -> 15-20%.

2. **Monthly P&L Accelerator**: Risk scaling based on MTD P&L (+10% MTD -> 1.3x risk, +20% -> 1.5x, -5% -> 0.7x). Adds 10-15% to avg month. Low implementation effort (risk.py only).

3. **Extended Pyramiding + Dynamic TP**: Add levels 4-5 at 2.8/3.5 ATR with TP extension when pyramid >= 2 (TP goes from 3.0 to 4.0+ ATR). +5-10% on avg month, makes runners contribute 8-12% vs 5-7%.

**Proven failures (don't retry):** Mean reversion, MACD, EMA pullback, RSI divergence all negative PF. Weekend trading and looser filters dilute edge.

**Realistic targets with all 3 combined:**
- Months with 25%+: 60-70%
- Average monthly: 50-65%
- Months with 100%+: 10-15%
- Losing months: 10-15%

**How to apply:** Implement in order (5m signals -> accelerator -> pyramiding), backtest each incrementally, full combined BT, then 2-week testnet before live.
