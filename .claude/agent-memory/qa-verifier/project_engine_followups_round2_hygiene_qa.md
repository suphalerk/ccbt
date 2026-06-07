---
name: engine-followups-round2-hygiene-qa
description: Re-verify of dashboard-engine-followups hygiene round2 (cb4b062 H1 cooldown set + 682c00c _candle_seconds extract + 8fb71ea TDD) — LENS on #2/#3/#5 idempotency/persist/helper
metadata:
  type: project
---

QA re-verify of `dashboard-engine-followups` hygiene round 2 (2026-06-07), commits cb4b062 (H1: trail_stop/breakeven into is_sl set), 682c00c (extract `_candle_seconds`, dedupe both parser sites), 8fb71ea (TDD tests). Verdict: PASS-WITH-WARNINGS. All 118 engine-relevant tests green.

**Why:** LIVE engine close path + shared trades.db under ~59-bot write burst. Trading-safety + concurrency lens.

**How to apply:** When re-verifying or extending this area:

- LENS #2 (idempotent telemetry) TRULY FIXED structurally: engine.py:281 `closed.append(trade_id)` runs immediately after record_trade_result (275), BEFORE both telemetry try-blocks (calibration 291, logger/alert 317). A raise in telemetry can't re-drive record_trade_result next loop. Crash-safe `continue` at 273 (log_trade_close raise → trade stays in open_ids, no double-count). Genuinely behaviorally tested.
- LENS #3 (persistent conn + dedup): SPLIT VERDICT. Persistent conn TRULY FIXED — engine.py:1105 `self._journal.upsert_candles` → `_upsert_candles_conn(conn=self._conn)`, the per-tick `sqlite3.connect` I flagged R1/R2 is GONE. But dedup-per-closed-candle NOT addressed: write fires EVERY signal tick (grep `upsert_candles` bot/engine.py = ONE call site, no `_last_persisted`/new-candle gate). "Dedup" is only INSERT-OR-REPLACE idempotency (safe), NOT skip-if-no-new-candle. Still rewrites ~200 rows + correlated-subquery DELETE prune per tick on shared db. Same write-amp note as R1/R2 — now THREE rounds open.
- LENS #5 (`_candle_seconds` both sites + warning) TRULY FIXED: helper at engine.py:99 with `tf.lower()` + `logger.warning("candle_seconds_unknown_tf", fallback 900)`. Used at BOTH 1155 (cooldown) and 1719 (sleep). Duplicated parser eliminated. Latent: checks "h" before "m" so "1h30m" mis-parses; `1d`/`w` → warn+900s. Dormant (not deployed).
- LENS H1 (cooldown set) LANDED + DECISION-INVARIANT: engine.py:1163 `is_sl = lc_reason in ("sl","stop_loss","trail_stop","breakeven")`. VERIFIED scoping claim: config_gold_forex.json is the SOLE asymmetric config (after_sl=8/after_close=4) of 191 scanned; other 190 equal → zero observable effect. Gold flex_cooldown.enabled=False (override does NOT mask), trailing + move_sl_to_be_after_tp1=True active → trail_stop/breakeven reachable. Net: restores pre-`_infer_close_reason` 8-candle cooldown for profitable Gold stop exits. NOTE: the R1 N11 taxonomy commit (51328d9) temporarily REGRESSED Gold (profitable trail/BE dropped to 4-candle); cb4b062 fixes it — whole branch is now invariant vs pre-taxonomy baseline.
- TEST-QUALITY (repeat smell): the 6 "Behavioral" parametrized tests in test_engine_hygiene_round2.py (lines 121-218) RE-IMPLEMENT `is_sl = ... ("sl","stop_loss","trail_stop","breakeven")` inline (142,163,186,208) — they do NOT call the engine gate. PROVEN: I reverted engine.py:1163 to the old 2-element set → only the 2 source-grep tests (test_trail_stop_in_is_sl_set, test_breakeven_in_is_sl_set) FAIL; all 6 "behavioral" tests still PASS on the broken engine. So the H1 fix is guarded ONLY by grep. Same passing-but-wrong as test_H1_cooldown_selects_after_sl_candles (test_engine_followup_fixes.py:692) which STILL inlines both parser + is_sl (lines 701-721) — unchanged this round. The round-2 tests DO correctly import+call `_candle_seconds` (152,178,200) — that part is real.
- "corrected 2 contradicting FIX-6 tests" (report claim) VERIFIED: test_engine_followup_fixes.py:560/578 now assert trail_stop/breakeven MUST be in is_sl (were "must NOT"). But still source-grep (read_text + assert in line), same weak guard.
- ONE-COMMAND-ALL-GREEN: 6 engine files together (.venv-dash) = 118 passed. Full `tests/` still has the pre-existing 43 fails (forex oandapyV20 env + ws ordering) — unchanged, not a regression.

See [[engine-followups-round2-qa]], [[engine-followups-round1-qa]], [[verification_method]].
