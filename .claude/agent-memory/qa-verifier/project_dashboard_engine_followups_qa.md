---
name: dashboard-engine-followups-qa
description: QA of N11 close-reason taxonomy + N6 candle producer + 3 ws/nginx nits on dashboard-engine-followups branch (live engine edits)
metadata:
  type: project
---

QA round 1 of branch `dashboard-engine-followups` (commits 51328d9 N11, 450f84e N6, 07f0168 nits), reviewed 2026-06-07.

**Verdict: PASS WITH WARNINGS.** No trading-safety blocker. Close decision genuinely invariant; candle persist is non-fatal (tight try/except, signal-gen before it, `return False` after it).

Key findings (no code bug that changes a trade decision):
- N11 `_infer_close_reason()` docstring claims it uses `initial_sl` to avoid the ratcheted SL — but the code reads `info["sl"]` (ratcheted) and `initial_sl` is DEAD (written at open+restore, never read). Labels still come out correct because exit≈ratcheted-sl on a trail exit. Dead-field + wrong-docstring defect, not a label bug.
- N11 silently changes the SAME-SIDE ENTRY COOLDOWN: `is_sl = lc_reason in ("sl","stop_loss")` (engine.py ~1103) now routes `trail_stop`/`breakeven` to `cooldown_candles_after_close` instead of the old uniform `"sl"`. Behavior change, NOT pure relabel as the report claims. Blast radius tiny: 190/191 configs have after_sl==after_close; ONLY config_gold_forex.json differs (after_sl=8 vs after_close=4). No test covers this branch with new labels.
- Cooldown/circuit-breakers are PnL-sign-driven (`risk.record_trade_result`), NOT label-driven — so the relabel can't affect consecutive-loss halts. That's why severity is low.

**Test-environment split (real CI gap):** No single venv runs all 53 new tests green.
- `.venv-dash` has fastapi+pandas, NO anthropic/ccxt → N11 (test_close_reason_taxonomy) FAILS TO COLLECT (`import bot.engine` → anthropic).
- system python 3.9 has anthropic+ccxt, NO fastapi → the 3 N6 API-integration tests FAIL.
- Verified green where deps exist: N11 31/31 (system py), N6 22/22 (.venv-dash), ws 25/25 (.venv-dash), engine_gate 15/15.

Strong parts: N6 producer tests assert exact schema columns AND value-by-value column mapping (ema_fast→ema9 etc.) AND /api/candles available=true via TestClient, for BOTH timestamp-column and live DatetimeIndex shapes. Symbol parity holds (producer + trades + dashboard URL param all use clean `config["symbol"]` e.g. BTCUSDT). bot_ohlcv create is idempotent + table created at journal init; rows bounded per (symbol,tf) at 200. ws first-tick no-client guard is safe (on-connect snapshot sent separately in api/main.py:163).

Weak parts: N6 engine-hook tests (test_n6_engine_persist TestEnginePersistHook) are SOURCE-GREP only, not behavior — the non-fatal try/except is never exercised by a test. "decision byte-identical incl SL/TP placement" is overstated: no placement test exists (placement code untouched, so not a regression).

See [[verification_method]].
