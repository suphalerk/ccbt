---
name: backtest-engine-audit-2026-06-08
description: Strategy/research soundness review of backtest engine — what's rigorous, what's missing (funding cost, taker fees, data-snooping correction)
metadata:
  type: project
---

Holistic review of CCBT backtest methodology & engine (lens: strategy/research soundness), done 2026-06-08.

**Strengths (verified in code, not assumed):**
- Look-ahead defense is genuinely double-protected: indicators use `.shift(1)` (`bot/data.py` dual_thrust L396/L421, vol_break L305), AND 1H trend features are `.shift(1)`'d then merged with `merge_asof(direction="backward")` (`bot/data.py:1261-1268`). Well-commented.
- R-multiple shared-wallet replay is correctly implemented: `r_mult = pnl/(10000×actual_risk_pct)` per-bot, then `dollar_pnl = balance×0.01×r_mult` flat 1% (`research/final_backtest.py:809`, `r13_combined_backtest.py:900`). Fixes the documented 10x risk-mismatch bug.
- Walk-forward 50/50 with OOS>=60% IS gate (`research/audit_btc_wif.py:345,517`); deploy gate trades>=15.
- Drawdown denominator correctly uses initial_balance+peak equity (`backtest/metrics.py:86-91`).
- SL+TP same-candle tiebreaker uses candle direction (`backtest/engine.py:1000`).

**Gaps found (real, file:line):**
- **Funding cost never charged in backtest.** `add_funding_rate` merges the column (`engine.py:214`) but `_close_position` only deducts commission+slippage. Funding is consumed ONLY as a signal-scorer feature (`bot/signal_scorer.py:432`). For 4H bots holding multi-day positions across 8h funding windows this omits a real holding cost. Skill doc requires it.
- **Fee assumption optimistic.** Backtests use commission_rate=0.0004 (0.04% maker) but SL/TP + market entries are TAKER fills (0.06% per CLAUDE.md). Under-charges ~33%/side. Slippage 0.00015 < documented 0.02%.
- **Data-snooping uncorrected.** 30k+ backtests across rounds; only a one-line caveat in `docs/backtest-methodology.md:32`. No Bonferroni/deflated-Sharpe/multiple-testing adjustment. Per-coin best-of selection inflates expected PF.
- **Profile return claims internally inconsistent.** research-history "Final +918%/yr (audited, R-fixed)" vs portfolio.md profiles +2,099%/+3,339%. The high numbers come from higher leverage (25x) + 0.9% risk + 10 concurrent compounding, not new alpha — magnitude not credible as sustainable.
- range_bounce support/resistance NOT shifted (`bot/data.py:441-442`) unlike dual_thrust — includes current (closed) bar; not a future leak but inconsistent and mildly self-confirming.

**Why:** user explicitly emphasizes statistical honesty and has been burned by inflation bugs (see [[feedback-backtest-bugs]]).
**How to apply:** when proposing new strategies or trusting a profile return, charge taker fees + funding, and discount best-of-N PFs.
