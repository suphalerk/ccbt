---
name: test-suite-qa-2026-06-08
description: Holistic test-quality review of CCBT suite (998 tests) — env split, backtest engine untested, dispatch-parity gap, order-dependent WS failures
metadata:
  type: project
---

Holistic TEST QUALITY review, 2026-06-08 (branch claude/crypto-trading-bot-1xlt7).

**Why:** parent orchestrator asked for a project-wide test-quality lens, not a single PR.
**How to apply:** use as the baseline next time the suite is touched; re-check the items below before trusting a green run.

Key facts found:
- 48 test files, 21.7k LOC, **998 tests collected** (no collection errors in .venv-dash).
- **No single environment runs the whole suite.** `.venv-dash` (py3.12) has ccxt/fastapi/playwright but NOT oandapyV20 → 38 forex tests ModuleNotFound. System py3.9 has oanda but NOT fastapi/playwright. Full-suite run in .venv-dash = **43 failed / 955 passed**: 38 forex (env) + 5 WS.
- **The 5 WS failures are ORDER-DEPENDENT, not real bugs.** `tests/test_api_ws.py` passes 25/25 alone; the coalescing/dead-client tests use `asyncio.get_event_loop().run_until_complete()` (the ONLY file doing this, line ~411) and inherit a closed/stale loop after earlier async tests run. Fix = convert to pytest-asyncio. The coalescing asserts are actually correctly bounded (>=1 AND <=1) — earlier worry unfounded.
- **backtest/engine.py has ZERO pytest coverage.** No test imports BacktestEngine; it's only used by research/ scripts (not collected). This is the engine that produces every deploy decision and historically had the 10x-inflation bugs. Biggest gap in the suite.
- Backtest engine itself is methodologically SOUND on read: regime computed per-row with warmup guard (lines 219-229), entry uses prev_row signal + current_row close fill (273-377), slippage+commission both sides, simulated-time cooldown. Intrabar SL+TP-same-candle tiebreaker uses close-vs-open direction (lines 997-1020) — optimistic but documented (Item 10).
- **DISPATCH-PARITY GAP (HIGH):** 7 signal types — adx_di_cross, choppiness_ema, williams_r_adx, roc_momentum, price_channel_vol, ema_alligator, ribbon_rsi_vol — have check_*_conditions defined AND dispatched in backtest/engine.py but NO new_signals.append / special-case in live generate_signal() (verified lines 2302-2700). A config enabling these backtests fine but produces ZERO live signals (the exact CLAUDE.md "most commonly missed step"). Severity HIGH not CRITICAL: **0 configs currently enable any of them, 0 in start.sh roster** — latent landmine, no live outage. No test guards this invariant.
- Live-path tests that ARE strong: test_strategy.py (1100 LOC — trailing ratchet, net R:R w/ fees, RSI/vol gates, detect_regime trending/ranging/volatile, generate_signal uses iloc[-2]); test_risk.py (392 LOC — sizing, daily/consec loss, circuit breakers, dynamic risk, **simulated-time cooldown**); test_strategy_meta.py (drift guard).
- **Test LOC imbalance:** dashboard/API ~9,573 LOC vs live-trading path ~7,759. The read-only dashboard is more tested than the autonomous money-path.
- Recurring "passes-but-wrong" patterns mostly TAMED this round: n13_parity now compares VALUES per symbol (not just symbol sets) and self-documents the old bug; busy_timeout test pairs source-grep with a real PRAGMA query (line 271); ~30 source-text asserts remain across suite but most are paired w/ behavioral checks. mock_ccxt returns real ccxt shapes.
- No multi-writer WAL concurrency test (59 bots write trades.db); WAL is only set on tmp DBs, never stress-tested for "database is locked".
- No regression test pinning the Forming-Candle Bug (inject different iloc[-1] forming candle → signal must not change).
- live_sl_trigger_binance.py correctly has NO test_ prefix → not auto-collected (manual network script).

Related: [[verification_method]], [[engine_followups_round2_qa]] (no-single-green-run noted before).
