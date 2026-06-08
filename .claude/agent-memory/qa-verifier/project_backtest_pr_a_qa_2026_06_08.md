---
name: backtest-pr-a-qa-2026-06-08
description: QA sign-off of PR-A (first backtest-engine test suite, branch backtest-tests-pr-a) — verdict + the 3 real weaknesses found
metadata:
  type: project
---

PR-A (`backtest-tests-pr-a`) = TESTS-ONLY (6 files, ~3150 lines, diff touches ONLY tests/, zero engine/metrics/replay/live edit — confirmed via `git diff --name-only base..branch | grep -v ^tests/` = empty). 85 tests, all pass in `.venv-dash` when files listed explicitly.

**Verdict: PASS WITH WARNINGS / APPROVED.** Mutation-verified independently:
- PIN-7 tiebreak FAILS on always-SL flip (`test_bullish_candle_picks_tp_for_long`). Real power.
- PIN-1 FAILS on 10x at replay:396 (`TestPIN1ReplayFormula`, 5 tests) AND at run_bot:348 (`TestPIN1RunBotLine348`, 2 tests). Both inflation sites pinned.
- PIN-2 (ema + body_dominance) FAILS on signal_row→exec_row look-ahead mutation. Behavioral, not vacuous.
- Cash-conservation + snapshot FAIL on dropped entry-commission. Reconciles vs state.balance (not naive Σpnl), independent entry-comm recompute.
- Parity reproduces EXACTLY: live=31, bt=38, bt−live=the 7 orphans, live−bt=∅.

**Why (3 real weaknesses, none blocking):**
1. PIN-4 (`test_pin4_regime_per_row_early_candles_are_ranging`) is VACUOUS — its n=30 fixture produces 0 trades even with regime_filter OFF (EMA9/21 first crossover doesn't occur in 30 bars). `assert len(trades)==0` is true regardless of regime logic; passed even after I removed the ranging-warmup floor. Zero regression power for its stated no-future-leak claim.
2. Golden snapshot is all-TP / all-win / no-loss / no-drawdown / no same-candle-both-touched. Cannot detect SL-pricing or tiebreak regressions, and will NOT visibly move when PR-B flips to SL-first — partially undermining its role as PR-B's "before" baseline. White-box engine tests cover SL/tiebreak separately, so acceptable for PR-A.
3. Parity key-extraction slices `source[def generate_signal:]` to EOF and greps `signals_config.get(...)`. generate_signal is NOT the last function in strategy.py (many check_* defs follow). Clean TODAY (no signals_config after :2656) but fragile: any future `signals_config.get("x")` in a trailing helper silently corrupts the live set.

**How to apply:** these are PR-A-acceptable (characterization discipline holds); flag #1 + #2 as follow-ups before/with PR-B. First-run-only transient seen (TestPIN1RunBotLine348 showed 10x-style 1.0-vs-0.1 once, then 10/10 clean) — treat as harness cold-start artifact, not a defect, but note order/state sensitivity. CI gate runs only 3 of the 5 files (engine+metrics+replay); parity+snapshot not in the README's gate command. See [[project_backtest_engine_map_2026_06_08]] and [[verification_method]].
