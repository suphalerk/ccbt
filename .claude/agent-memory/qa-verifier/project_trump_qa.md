---
name: project-trump-qa
description: QA verification of TRUMPUSDT fixes (June 2026) — short-only edge real but sub-15 trades; all live trades were out-of-sample
metadata:
  type: project
---

QA-verified TRUMPUSDT proposed fixes on 2026-06-06. TRUMP 1h data: Jan 18 2025 – Mar 21 2026 (~14mo, 10,240 bars). All 16 live trades (Mar 26 – Jun 3 2026) were 100% OUT-OF-SAMPLE — backtest data ends Mar 21, so no fix can be validated against the actual live failure window.

**Why:** Live TRUMP lost -$183 over 16 trades (25% WR, 13/16 SL). Diagnosis proposed retiring Stoch MTF + ZScore, switching Ichi to short-only, keeping EMA Ribbon.

**How to apply (verdicts):**
- EMA Ribbon (config_trumpusdt_emaribbon.json): PF 1.88, 39 trades, WF OOS/IS 0.74 — only clean DEPLOY. Short-skewed (shorts PF 2.32 vs longs 1.39).
- Stoch MTF + ZScore: confirmed no edge (6 / 1 trades) → retire = correct.
- Ichi short-only: PF 4.78 / 13 trades — edge is REAL (PF 3.67 without top trade, not an outlier; trades never overlap so post-hoc short filter is exact) but 13 < 15 trade gate, WF halves only 8/4 trades → NEEDS_MORE_DATA, NOT deploy.
- "short_only"/"long_only" is NOT a config flag in backtest/engine.py — short-only Ichi and Ribbon trend-filter proposals require code changes before they're deployable.

Verification scripts (throwaway): /tmp/qa_trump.py, qa_trump2.py, qa_trump3.py. Method: ran each config via BacktestEngine, accessed engine.state.trades for long/short split + time-split walk-forward. See [[feedback-audit-checklist]].
