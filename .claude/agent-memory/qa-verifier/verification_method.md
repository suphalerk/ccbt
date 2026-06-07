---
name: verification-method
description: Repeatable QA harness for verifying a proposed per-coin strategy fix against the 2yr engine
metadata:
  type: project
---

QA-verify a proposed fix by backtesting on `data/{sym}_1h_2y.csv` (or 15m) via `BacktestEngine`.

**Why:** Deployed configs (config_{coin}_*.json) sometimes ship with too-tight SL or n<15 trade samples; live -PnL is often regime variance, not a broken signal. Must distinguish overfit tuning from genuine improvement before DEPLOY.

**How to apply:**
- Run engine: `eng=BacktestEngine(cfg); m=eng.run(df.copy(),df.copy())`; pull per-trade list from `eng.state.trades` (`vars(t)` → side, pnl, close_reason, entry_time).
- Always run BASELINE (current deployed config) on the SAME data first, then the fix.
- Walk-forward = split df in half by time; OOS PF must be >= 60% of IS PF.
- Adversarial extras that caught real signal here: (1) quartile split — exposes which regime the live loss came from (baseline Q4 often <1.0 = the live bleeder); (2) 2x fee stress; (3) long/short PF split — a fix that only helps one side is regime-luck, one that's monotonic across a param sweep (SL 2.0→2.5→3.0) is structural.
- DEPLOY requires: full PF up, walk-forward pass, trades >= 15, survives fee stress, improvement is monotonic / well-motivated (not a lucky single param).
- 'retire' verdict: confirm baseline has no edge at ANY filter setting — relax regime_filter/trading_hours to free the sample; if PF stays <1.2 (or collapses), retire is correct. See [[verification_method]].
- python3 not python on this Mac. `logging.disable(logging.CRITICAL)` to silence engine logs.
