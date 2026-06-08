---
name: backtest-tests-pra-review-2026-06-08
description: Re-review of PR-A backtest characterization suite — pins mostly sound (mutation-verified), but PIN-4 regime look-ahead pin is tautological and partial-TP cash-conservation is unpinned
metadata:
  type: project
---

Adversarial re-review of branch `backtest-tests-pr-a` (tests-only, 6 files ~3150 LOC) on 2026-06-08.

**Verified sound via mutation testing:** PIN-7 (candle-body tiebreak — fails on always-SL), PIN-1 replay :396 (fails on 10x), TestPIN1RunBotLine348 :348 (fails on 10x — monkeypatch stubs only engine/loader, real pnl_frac path intact), PIN-2 ema + body_dominance (fail when _check_entry uses exec candle), PIN-3 trend-filter (bear run = 2 short / 0 long, genuinely differential), gap-fill KNOWN GAP (fails on gap-fill "fix"), exit-slippage (TestSlippage catches dropped slippage), parity PIN-11 (regex resolves all live sites incl standalone supertrend/ichimoku → exactly the 7 orphans, live-bt empty).

**Real gaps found:**
- **PIN-4 (`test_pin4_regime_per_row_early_candles_are_ranging`) is TAUTOLOGICAL.** The 30-candle fixture produces 0 trades even with `regime_filter` OFF (EMA9/21 can't cross in 30 bars). Mutating regime to global/future computation → test STILL passes. Provides zero protection against the look-ahead it claims to guard. Fix: add control assertion (filter-OFF must yield ≥1 trade) or use a longer fixture where the gate is the only thing suppressing entries.
- **No partial-TP cash-conservation at run() level.** Only `test_cash_conservation_no_partial` (partials OFF) + snapshot (OFF). README §49-50 flagged the exact partial_pnl double-count hazard (booked at engine.py:1048 AND inside trade.pnl at :1273); never reconciled end-to-end with a partial trade.
- **Parity `_extract_live_keys` reads to EOF**, capturing 8 helper fns defined after generate_signal. Lands at 31 today but a future helper referencing signals_config.get("orphan") would falsely mark it live-wired. README wanted a SUPPORTED_SIGNALS registry.

**Why:** characterization baseline must let PR-B/PR-C changes be caught; a tautological pin or unpinned account path lets a fill-model regression slip through silently.
**How to apply:** when PR-A revisions or PR-B/PR-C land, require the PIN-4 control assertion and a partials-ON run() reconciliation before sign-off. See [[backtest-engine-audit-2026-06-08]].
