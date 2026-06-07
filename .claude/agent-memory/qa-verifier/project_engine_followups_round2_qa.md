---
name: engine-followups-round2-qa
description: Round-2 verify of dashboard-engine-followups BLOCKER fixes (Gold H1 candle_secs, CalibrationTracker busy_timeout + double-count guard)
metadata:
  type: project
---

Round-2 QA of branch `dashboard-engine-followups` (commits ad36445, 43bf8c2) vs base `dashboard-rewrite-impl`. Verdict: PASS-WITH-WARNINGS. Both BLOCKERs genuinely fixed; concerns are test-quality + a latent parser trap + a live behavior change not surfaced.

**Why:** This edits the LIVE engine close path + shared trades.db journal under a ~59-bot write burst. Trading-safety + concurrency lens.

**How to apply:** When re-verifying or extending this area:

- BLOCKER 1 (Gold H1) TRULY FIXED: both candle_secs parsers (engine.py ~1126 cooldown, ~1691 _sleep_until_next_candle) now `tf.lower()` before 'h'/'m'. `'h1'`->3600. Only deployed TFs are 15m/1h/4h/H1 — no `1d`/`w`. LATENT TRAP: the new `else` fallback is 15min — a `1d`/`w` config would silently mis-size cooldown (no crash). Dormant today.
- BLOCKER 2 TRULY FIXED: (a) `_apply_pragmas()` shared helper sets busy_timeout=5000+WAL+synchronous=NORMAL on all 3 writers (TradeJournal._conn, CalibrationTracker._conn, upsert_candles per-call conn). Both persistent conns behaviorally verified (query PRAGMA busy_timeout >=1000). (b) calibration block wrapped in try/except + `continue` on log_trade_close failure — behaviorally tested: record_trade_result called exactly once even when record_outcome raises (no double-count → no spurious consecutive-loss circuit-breaker trip).
- busy_timeout: `journal_mode=WAL` is DB-level; busy_timeout+synchronous are PER-CONNECTION, so every conn must call _apply_pragmas — confirmed.
- WRITE-AMP NOT ADDRESSED: `upsert_candles` opens a NEW sqlite conn EVERY signal tick (not reusing persistent journal conn, no new-candle dedup) and rewrites ~200 rows + correlated-subquery prune. busy_timeout-guarded so it won't crash, but the producer should reuse the journal conn AND only write on a closed-candle boundary. Same note as round1 FIX1-6.
- LIVE BEHAVIOR CHANGE not surfaced in report: new close taxonomy drives cooldown. OLD: any non-TP exit labelled "sl" → after_sl (longer) cooldown. NEW: trail_stop/breakeven → after_close (SHORTER) cooldown; only genuine loss (pnl<0) → stop_loss → after_sl. Intentional & documented in test docstring, but report omitted it.
- TEST QUALITY: many are source-grep (read_text + `assert 'x' in source`), not behavioral. PASSING-BUT-WRONG: `test_H1_cooldown_selects_after_sl_candles` is labelled "Behavioral" but RE-IMPLEMENTS the parser inline in the test — does not call engine; a revert would not fail it (only the grep test guards the real code). Engine's non-fatal candle-persist guard has NO behavioral test (grep-only). Genuinely-behavioral & solid: crash-safety (journal-raise leaves trade in open_ids, record_trade_result not called), double-count guard, both persistent-conn busy_timeout.
- `_infer_close_reason(trade_side=...)` takes trade_side but NEVER uses it (dead param). Logic is symmetric via abs() so it works for long+short, but the param + docstring are misleading.
- ONE-COMMAND-ALL-GREEN: FALSE. Full `tests/` = 43 failed (38 forex ModuleNotFoundError oandapyV20 env-only; 5 test_api_ws TestCoalescing fail ONLY under full-suite ordering due to deprecated asyncio.get_event_loop() event-loop pollution after forex import errors — ws passes 25/25 in isolation). Report's "unified" cmd (3 files=55) OMITS test_close_reason_taxonomy.py + test_n6_candle_producer.py. All 5 engine-relevant files together = 100 passed.
- CRASH-SAFE TRADEOFF: when log_trade_close raises, `continue` skips closed.append → cancel_all_orders gated on `if closed` does NOT run that tick → orphaned algo SL/TP persist until next loop retries. Acceptable (rare w/ busy_timeout; next loop + next-open cleanup covers it) but worth noting.
