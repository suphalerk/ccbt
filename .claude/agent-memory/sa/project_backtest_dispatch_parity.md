---
name: project-backtest-dispatch-parity
description: 7 signals exist in backtest/engine.py dispatch but NOT in live strategy.py generate_signal() — backtest-passes-live-silent class
metadata:
  type: project
---

`backtest/engine.py` `_check_entry` dispatches 38 signal keys; `bot/strategy.py` `generate_signal()` dispatches 31. Backtest has 7 that live does NOT:
adx_di_cross, choppiness_ema, williams_r_adx, roc_momentum, price_channel_vol, ema_alligator, ribbon_rsi_vol. (Verified 2026-06-08 by diffing `signals_config.get("...")` keys in both files — `in LIVE not BT` is empty.)

**Why:** This is the INVERSE of the usual CLAUDE.md warning (which guards "live missing a signal backtest has"). Here a config enabling one of these 7 backtests fine but produces ZERO live signals. The backtest-tests plan (`docs/tickets/backtest-tests/`) adds a parity test asserting `bt_keys - live_keys == {these 7}` as an allowlist to block NEW drift; the 7 stay latent, not wired to live (user decision 2, 2026-06-08).

**How to apply:** When reviewing a new-signal PR or this parity test, the allowlist is a freeze, not a fix — flag if anyone tries to shrink it by wiring orphans into live without re-validating timeframe (`research/strategy_meta.json`). The parity test's value is regression-locking, not closing the real gap.
