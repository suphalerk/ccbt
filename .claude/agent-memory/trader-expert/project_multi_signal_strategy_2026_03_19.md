---
name: Multi-Signal Strategy Design
description: Complete 4-signal strategy spec targeting 200-300% annual — EMA crossover + EMA pullback + RSI divergence + BB squeeze breakout. Includes exact entry/exit conditions, per-signal configs, risk rules, implementation plan for 7 files, and 5-phase validation plan.
type: project
---

Designed 4-signal architecture for CCBT to scale from 4-6 trades/month to 20-31 trades/month.

**Why:** Single EMA crossover maxes at ~50%/yr (PF 1.10, 87 trades/2yr). Need diverse signal sources exploiting different market structures to increase frequency while maintaining edge.

**4 Signal Types:**
1. EMA Crossover (existing, proven) — 4-6/mo, trend transitions
2. EMA Pullback to EMA21 support — 8-12/mo, trend continuation, tighter SL (1.2x ATR)
3. RSI Divergence (swing-based SL) — 3-5/mo, exhaustion reversals, counter-trend with lower risk (1%)
4. BB Squeeze Breakout — 5-8/mo, volatility expansion, structure-based SL (BB middle)

**Key design decisions:**
- Priority order: Divergence > BB > Pullback > Crossover (rarest = highest conviction)
- Per-signal exit parameters (different trail/TP/SL per signal type)
- Confluence bonus: +25% size per agreeing signal (cap 1.5x)
- Cross-source cooldown reduced (1-2 candles vs 3-6 same-source)
- Max positions increased to 3, daily loss limit to 6%
- Each signal independently disable-able via config flag

**Performance target:** 25-30 trades/mo, 47-53% WR, 1.6 R:R, PF 1.25-1.45, 200-300% annual

**How to apply:** Implementation touches 7 files: data.py (BB indicators, swing detection), strategy.py (3 new signal generators + SignalSource enum), engine.py (per-signal exits), risk.py (per-source risk), config.json (nested signals block), metrics.py (per-signal breakdown), tests. 5-phase validation: individual backtests -> combined -> stress test -> testnet 2wk -> live gradual.
