---
name: backtest-tests-pra-round1-2026-06-08
description: PR-A round-1 follow-up re-review (branch backtest-tests-pr-a, commit 56aa129) — all 5 verification points confirmed by independent mutation; flakiness scare was self-inflicted (concurrent pytest on shared engine.py)
metadata:
  type: project
---

PR-A round-1 follow-ups (branch `backtest-tests-pr-a`, commit 56aa129) re-reviewed 2026-06-08. Diff is TESTS-ONLY + `docs/tickets/backtest-tests/README.md` (zero production code). 91/91 CI-gate tests pass.

**Why:** This suite pins the backtest engine before PR-B (SL-first tiebreak) and PR-C (funding) land. The pins must have real teeth or the regressions slip through. Builds on [[backtest-tests-pra-review-2026-06-08]].

**How to apply:** All 5 confirmation points independently verified — trust this suite as a PR-B/PR-C tripwire:
- (a) PIN-4 regime: mutated engine.py:222-229 to global/whole-df regime → PIN-4 FAILS (1 trade opens at crossover candle 31, which is <warmup 34). Crossover empirically at positions 31+32 (3-candle band inside the floor). Reverted clean.
- (b) Loss snapshot: mutated same-candle tiebreak (engine.py:998-1016) to always-SL → loss-snapshot value pin + same-candle pin + optimistic tiebreak chars all FAIL (win_rate 0.80→drops). Reverted clean. Fixture verified: 5 trades, 4W/1L, LONG→SL + SHORT→TP both-touched bars from injected candles 64/115.
- (c) Partial-TP cash-conservation: reconciles `engine.state.balance` (authoritative ledger) vs `initial - entry_comm + trade.pnl`; engine.py:1049 books partial_pnl once, 1270 books remaining, 1273 stores total — no double-count. Correct.
- (d) Parity LIVE slice bounded to generate_signal body (next top-level def). NOTE: backtest slice (`_extract_backtest_keys`) still reads to EOF (unbounded) — latent gap, but mitigated by hard count pins (test_backtest_key_count==38, test_live_key_count==31).
- (e) git diff TESTS+README only — confirmed.
- PIN-1 run_bot:348 (`pnl_frac = t.pnl / initial_balance`) guarded by stub_call_count[0]>=1 — pins the historical 10x shared-wallet inflation bug. Load-bearing.

**Methodology lesson (self):** I saw phantom flakiness (28 "failures") because I ran mutation-test pytest against the shared `backtest/engine.py` WHILE a background flaky-loop job ran pytest on the same file. Clean SERIAL re-run = 80/80 pass, zero flakiness. ALWAYS kill concurrent pytest before mutation testing a shared source file — concurrent runs read a half-mutated file. The suite is deterministic (no RNG, sim-time passed to risk_mgr).
