---
name: backtest-engine-map
description: Backtest engine responsibilities, fill model, historical bugs to pin, untested paths, and the live-vs-backtest divergences (7 orphan signals + regime gate is backtest-only)
metadata:
  type: project
---

Map of `backtest/engine.py` (1307 lines) for the regression test plan.

**Fill model** (engine.py): market-on-close entry at `current_row["close"]` (engine.py:377) with slippage applied AFTER sizing (engine.py:934-940); SL/TP filled intrabar via high/low touch (engine.py:998-1020). Same-candle SL+TP tiebreak = candle DIRECTION (bullish close>open -> TP first for long; bearish -> SL first) (engine.py:1000-1017) — this is OPTIMISTIC, real fill is open->first-touch. Slippage is time-of-day/weekend multiplied (engine.py:159-184). Commission both sides (947, 1266) + on partial (1046) + on pyramid add (1142). Funding merged via add_funding_rate but NOT charged in PnL (engine.py:214 — column exists, never deducted). Cooldown = candle-count based on sim index (engine.py:312-330), risk.py cooldown uses sim timestamp (engine.py:846). Regime computed per-row rolling (engine.py:223-229).

**Look-ahead handling**: entry uses `signal_row`=prev_row (iloc i-1) for the signal, executes at current_row (iloc i) close (engine.py:275). Live uses iloc[-2] for signal, iloc[-1] is forming. Multi-prev signals slice `_df.iloc[candle_idx-2]`.

**KEY DIVERGENCES (live vs backtest)**:
1. 7 orphan signals dispatched in backtest but NOT in live generate_signal(): adx_di_cross, choppiness_ema, ema_alligator, price_channel_vol, ribbon_rsi_vol, roc_momentum, williams_r_adx. check_*_conditions funcs exist; live just never calls them. Currently 0 configs enable them (latent). Same class as the AO-on-1h checklist-step-3 miss.
2. Regime gate (regime_filter/skip_ranging/skip_volatile + trend_signals_gated per-signal gating) is BACKTEST-ONLY (engine.py:356-375, 386-390). Live generate_signal() computes regime only for sizing, never skips an entry on it. A backtest with skip_ranging=true trades fewer than live -> backtest looks cleaner than live behaves.

**Untested**: backtest/engine.py and backtest/metrics.py have ZERO test references (grep tests/). No regression pin for any historical bug.
