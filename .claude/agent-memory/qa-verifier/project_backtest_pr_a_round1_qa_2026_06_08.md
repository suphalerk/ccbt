---
name: backtest-pr-a-round1-qa-2026-06-08
description: PR-A round-1 follow-ups (commit 56aa129, branch backtest-tests-pr-a) independent mutation re-verify — APPROVED, all 5 teeth real
metadata:
  type: project
---

PR-A round-1 follow-ups (commit 56aa129, branch backtest-tests-pr-a) — independently mutation-verified, all teeth confirmed real. Supersedes the "still-open" gaps in [[project_backtest_pr_a_qa_2026_06_08]].

**Why:** Round-0 PR-A had 3 substantive gaps I flagged (PIN-4 vacuous n=30 → 0 trades regardless of regime; golden snapshot all-win = no SL/tiebreak coverage; parity slice ran to EOF). Round 1 claimed to fix all + 4 nits. I re-ran every mutation independently rather than trust the fix report.

**What I verified by independent mutation (engine.py mutated, then `git checkout backtest/engine.py` each time — all reverted clean):**
- (a) PIN-4: real code PASSES (0 trades, regime='ranging' for i<34 warmup floor). Mutated regime to global whole-df detect_regime → 1 trade fires → test FAILS. CONTROL (regime_filter=OFF) yields the trade → proves the 0 is gate-caused not absent-signal. test_backtest_engine.py:1513.
- (b) Loss snapshot: real code PASSES (win_rate=0.80, PF=8.44). Mutated BOTH same-candle tiebreak branches (engine.py:1000-1004 long, 1012-1016 short) to always-SL → win_rate→0.60, PF→2.40, snapshot value test + win-rate test FAIL. Numbers match fix report exactly. test_backtest_snapshot.py:449.
- (c) Partial-TP cash conservation: expected balance hand-computed from primitives (10597.80), reconciled vs engine.state.balance. Mutated engine to double-add partial_pnl (engine.py:1270) → balance off by exactly 149.45 (the partial_pnl) → test FAILS. test_backtest_engine.py:1108.
- (d) Parity slice: was `source[fn_match.start():]` (EOF) in round-0, now bounded to next top-level `def` (test_backtest_parity.py:58-71). Confirmed generate_signal spans strategy.py:2302-2697, next def at 2697, fn_end=97016 < EOF=114168 → ~17KB trailing helpers excluded. NOTE: bounded vs EOF produce IDENTICAL 31 keys today (no signals_config.get in trailing helpers) → this is defensive hardening, not a present-bug fix; parity test passes the same with or without the change today.
- (e) Diff is TESTS-ONLY: `git diff base..branch` on backtest/engine.py, metrics.py, bot/strategy.py, research/portfolio_backtest_v2.py = 0 lines. Whole branch touches only tests/ + docs/tickets/backtest-tests/README.md.

**Nits found (non-blocking):** loss-snapshot value test docstring says "rel=1e-6" but asserts rel<1e-4 (test_backtest_snapshot.py:498 vs 514) — intentional for sharpe rounding (EXPECTED sharpe only 4 decimals) but doc is stale; same stale "rel=1e-6" line in README. Tolerance still far tighter than mutation deltas (0.25 win_rate move) so no masking.

**How to apply:** This PR is the first real test coverage for the previously-ZERO-covered backtest/engine.py (see [[project_backtest_engine_map_2026_06_08]]). 91/91 CI-gate tests pass in .venv-dash. The 43 pre-existing full-suite failures (38 forex_exchange env + 5 api_ws ordering) are untouched and out of scope. Approved for merge.
