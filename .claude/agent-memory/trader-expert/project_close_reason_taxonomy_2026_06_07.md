---
name: close-reason-taxonomy-2026-06-07
description: dashboard-engine-followups close-reason taxonomy + cooldown gate — only gold is observably affected; crypto invariant
metadata:
  type: project
---

The `dashboard-engine-followups` branch replaced the 2-way close-reason taxonomy (`sl`/`tp`) with a 4-way one via `_infer_close_reason()` in `bot/engine.py` (stop_loss / tp / breakeven / trail_stop), and round-2 expanded the same-side cooldown `is_sl` set to `("sl","stop_loss","trail_stop","breakeven")` so all stop exits select `cooldown_candles_after_sl`.

**Why:** Pre-taxonomy all stops were labelled `sl` and always used `after_sl`. The new taxonomy split them, so without the set expansion, profit-side trailing/breakeven exits would have silently dropped to the shorter `after_close` cooldown — a behavior change. Round-2 restores parity.

**How to apply:**
- Cooldown selection is observably identical on EVERY config where `cooldown_candles_after_sl == cooldown_candles_after_close`. As of 2026-06-07 the ONLY config where they differ is `config_gold_forex.json` (after_sl=8, after_close=4). So gold is the sole bot whose entry timing this change can move. All crypto bots: decision-invariant.
- Gold runs `atr_tp_mult=0` → strategy sets a "very far" TP (strategy.py ~line 2474), so `_infer_close_reason`'s `dist_to_tp` branch never fires for gold; exits are always stop_loss (pnl<0) or trail_stop/breakeven (pnl>=0), all mapping to after_sl=8. Matches pre-taxonomy.
- One genuine (conservative) gold change from round-1's pnl<0 first-gate: a fee-driven near-TP loss that OLD code mislabelled `tp` (after_close=4) is now `stop_loss` (after_sl=8) = longer cooldown = fewer entries. No money-risk increase.
- The `closed.append` reorder (now before telemetry) and per-block telemetry try/except are pure double-count hardening: on the happy path final trade-state is byte-identical; on a telemetry raise the OLD code would re-drive `record_trade_result` next loop (double risk accounting). Fix is correct.
- Related: [[project_alice_timeframe_mismatch_2026_06_06]] (gold/AO timeframe drift family), [[project_engine_followup_round2_2026_06_07]] (prior round-2 engine review — candle_secs .lower() + calibration guard).
