---
name: Round 1 Backtest Diagnosis
description: Detailed diagnosis of why 1042-trade backtest loses 36.5% — trailing stop too tight (R:R 1.22 vs 2.0 target), EMA crossover whipsaw in ranging markets (WR 40.9%), regime bug in backtest
type: project
---

Round 1 backtest (2024-03 to 2026-03, $100 initial): 1042 trades, WR 40.9%, PF 0.84, R:R 1.22, Max DD 36.9%, final balance $63.47.

**Why:** Three root causes identified:

1. **Trailing stop too tight (1.8x ATR)** — clips winners at +1.5 ATR instead of letting them reach 3x ATR TP. Combined with breakeven buffer of only 0.2x ATR after TP1, most winners close at ~1.0-1.3x ATR profit. This is the #1 R:R killer.

2. **EMA crossover whipsaw in ranging markets** — no EMA slope filter, RSI ranges overlap (45-55 dead zone), regime detection exists but is never propagated to backtest DataFrame rows (BUG: all trades use trending trail multiplier).

3. **SL at 1.5x ATR too tight for 15m** — normal noise clips trades. Widening to 1.8x ATR reduces gross R:R from 2.0 to 1.67 but improves realized R:R because fewer premature stop-outs.

**How to apply:** Five priority groups:
- P1: Widen trails (2.2 base, 2.5 trending, 3.0 post-TP1), breakeven buffer to 0.5 ATR
- P2: EMA slope filter (reject flat crossovers), tighten RSI (50-68 long, 30-48 short)
- P3: Fix regime bug (propagate to DataFrame), regime-adaptive entries
- P4: Widen SL to 1.8x ATR, adjust min_rr to 1.3
- P5: Reduce cooldowns slightly

Expected outcome: WR 45-50%, R:R 1.5-1.8, PF 1.2-1.5, 550-750 trades, 80-150% annual return.
