---
name: 5m BTC EMA Crossover Research
description: Backtest results for EMA crossover on 5m BTC data — strategy does NOT work on 5m
type: project
---

All 14 configurations tested on 2yr 5m BTC data (207,400 candles). Every single config produced PF < 1.0.

**Verdict: The EMA crossover edge does NOT exist on 5m.** The 15m edge is the proven timeframe.

**Best result:** EMA(15/35) Slow — PF 0.85, Sharpe -0.33, 49 trades/yr, DD 56%
**Best session result:** US Session Only (13-21 UTC) — PF 0.91, Sharpe -0.06, 33 trades/yr, DD 36% — closest to breakeven but still losing

**Key findings:**
- Fast EMAs (3/8, 5/13) make things worse — more trades, more fee drag, lower PF
- Tighter RSI makes things worse (PF drops from 0.39 → 0.32)
- Kill Zones filter destroys edge (PF 0.27)
- Scalping tight SL/TP does not work (PF 0.36)
- ema_fast_crossover signal is nearly useless on 5m (PF ~0.02-0.05 across all tests)
- ema_crossover alone performs better than combined on 5m but still loses

**Why:** Fee drag is fatal at 5m — at 25x leverage, round-trip costs eat into every small move. The 15m candle gives enough price movement to overcome fees; 5m does not.

**Why:** Saved 2026-03-20 after full backtest sweep.
**How to apply:** Do not attempt to port the 15m strategy to 5m. If day trading is desired, investigate a fundamentally different approach (volume-weighted, order flow, microstructure) rather than indicator-based crossovers.
