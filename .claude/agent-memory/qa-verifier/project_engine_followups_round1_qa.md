---
name: engine-followups-round1-qa
description: QA of dashboard-engine-followups fix round 1 (FIX1-6 on engine.py/logger.py) — close-reason taxonomy, busy_timeout, crash-safe close, N6 candle producer
metadata:
  type: project
---

QA of branch `dashboard-engine-followups` fix round 1 (2026-06-07). Verifies FIX1-6 in bot/engine.py + bot/logger.py.

**MAJOR finding (blocks the FIX1 live claim):** The fix report's headline — "Gold H1 with atr_sl_mult=2.5 will now correctly use cooldown_candles_after_sl (8)" — is FALSE in live. The cooldown candle-seconds parse at engine.py ~1115 is `int(tf.replace("h",""))*3600 if "h" in tf else int(tf.replace("m",""))*60` with NO else fallback. Gold config uses `timeframe_signal: "H1"` (capital H). `"h" in "H1"` is False → `int("H1")` → **ValueError**, caught by the loop's outer `except Exception` → `_handle_error` (increments API-error counter, skips entry). So `_infer_close_reason`'s correct `stop_loss` label never reaches a working `is_sl` branch for gold. The sibling parse at ~1679 has an else-default (15min) so it doesn't crash but silently mis-times. Fix: case-insensitive tf parse (`tf.lower()`) in BOTH spots.
- Reproduce: `python3 -c "tf='H1'; int(tf.replace('m',''))" ` → ValueError.
- Gold uses `TradingEngine` (main_gold.py monkey-patches OandaClient), so it hits this code.

**What IS solid:**
- FIX1 `_infer_close_reason` pnl-sign gate before proximity: correct, pnl<0 → stop_loss always. Wired end-to-end: check_closed_positions writes `last_trade_close[side]={reason}`, cooldown reads `lc_reason in ("sl","stop_loss")`.
- FIX3 crash-safe close: `continue` on log_trade_close exception is correctly placed in the `for trade_id,info` loop, skips record_trade_result+closed.append → no double-count of consecutive losses. log_trade_close is an idempotent UPDATE WHERE id=X so retry is safe. SQLite locked surfaces at commit() (atomic) → clean rollback.
- FIX4 initial_sl: fully removed from all production code (grep bot/ dashboard/ api/ main* = clean). Function no longer references it.
- FIX2 busy_timeout=5000 on BOTH the persistent journal conn (_init_db) and the per-tick upsert_candles fresh conn.
- N6 candle producer DatetimeIndex live path IS tested (test_n6_engine_persist.py TestProducerWithDatetimeIndex). get_ohlcv does set_index("timestamp") so live df has no timestamp column → index branch runs, matching the test.

**Concurrency / efficiency concern (minor):** upsert_candles runs EVERY loop iteration (not gated on new candle close), opening a FRESH sqlite connection each time, rewriting ~200 rows (INSERT OR REPLACE) + DELETE-prune subquery, against the SHARED trades.db that all ~59 bots use. Only the forming candle changes per tick → heavy write amplification at candle boundary. Consider gating on new-candle-detected and/or reusing the persistent journal conn.

**Test-quality smells (TDD):**
- Many "tests" are source-greps on engine.read_text() (is_sl set membership, busy_timeout presence, initial_sl absence, candle_persist_failed string). They pass even if the runtime path is dead/broken — exactly why the gold H1 crash slipped through (no test exercises the real "H1" candle_secs parse).
- test_close_reason_taxonomy parametrizes on `initial_sl` but _infer_close_reason IGNORES it — decorative param, false impression that ratchet-distance is tested. Backward-compat tests include trivially-true asserts (isinstance("sl",str)).
- WS test uses asyncio.get_event_loop().run_until_complete → DeprecationWarning now, breaks on py3.12+.

**One-command-green:** `pytest tests/` is NOT green — 43 failures in test_forex_exchange.py (missing `oandapyV20`, pre-existing env gap, NOT a regression). The documented cherry-picked command (5 files) = 88 pass in .venv-dash (anthropic+ccxt confirmed present, no skips). The "53 tests" in the prompt doesn't match; actual is 88.

See [[verification_method]].
