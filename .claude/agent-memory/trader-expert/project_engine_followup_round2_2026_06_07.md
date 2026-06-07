---
name: project-engine-followup-round2-2026-06-07
description: Round-2 trading-safety review of dashboard-engine-followups — Gold H1 candle_secs fix + calibration double-count guard, both correct; pre-fix severity was understated
metadata:
  type: project
---

Round-2 review of branch `dashboard-engine-followups` (commits ad36445, 43bf8c2), live engine + shared trade journal. APPROVED — both fixes correct, decision-invariant for crypto.

**BLOCKER 1 (Gold H1 candle_secs)**: `bot/engine.py:1126` + `:1691` now `.lower()` the tf before h/m parse. `config_gold_forex.json` genuinely uses `timeframe_signal:"H1"` (OANDA native; `forex_exchange._map_timeframe` accepts both H1 and 1h, so OHLCV fetches were always fine — only the two manual string parsers broke).
- Severity was UNDERSTATED in fix report: the pre-fix ValueError was NOT swallowed inside `_evaluate_and_execute` (no inner try around the cooldown block). It propagated to the loop's `except Exception` → `_handle_error` → `record_api_error()` + 30s sleep EVERY tick once a prior close existed. 5+ reps would trip the API-error halt and force-close all positions. Active error path, not a silent no-op.
- Full chain verified: `_infer_close_reason` returns `stop_loss` for pnl<0 (hard loss never breakeven, gated at engine.py:82) → stored in last_trade_close → cooldown gate maps stop_loss→is_sl→cooldown_candles_after_sl(8) × candle_secs(3600).

**BLOCKER 2 (calibration double-count seam)**: try/except wraps engine.py:260-290. Ordering verified: `record_trade_result` (251, money state) is OUTSIDE the try; `closed.append` (311) and orphan `cancel_all_orders` (317) now always run even on calibration error. `record_outcome` is idempotent UPDATE...WHERE id (logger.py:795) so no double-write risk. `_apply_pragmas` covers all 3 sqlite connect sites (logger.py 154/547/669) — no drifting connection.

**How to apply**: This timeframe-case bug class affects ANY manual tf string parse. The OANDA/gold path uses uppercase OANDA granularity everywhere — audit any new code that does `int(tf.replace(...))` for `.lower()` first. Crypto configs (15m/1h lowercase) are byte-for-byte unchanged.
