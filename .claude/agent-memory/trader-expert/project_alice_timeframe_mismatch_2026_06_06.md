---
name: alice-timeframe-mismatch-2026-06-06
description: ALICEUSDT deployed config runs Awesome Osc on 1H (PF 0.77, broken) but was validated on 4H (PF 2.87) — timeframe field mismatch bug
metadata:
  type: project
---

ALICEUSDT (config_aliceusdt_awesome.json) loses live: 12 trades, 8.3% WR, -$196.97, 11/12 SL.

Root cause: config has `timeframe_signal: "1h"` and `timeframe_trend: "1h"`, but CLAUDE.md / R10 research validated Awesome Oscillator on **4H** (claimed PF 1.97).
- Backtest of deployed 1H config: PF 0.77, WR 25%, Sharpe -0.32, OOS PF 0.55 → BROKEN, never had edge.
- Backtest of same AO params on 4H data: PF 2.87, WR 60%, OOS PF 2.86 → HEALTHY but only 10 trades (< 15 min).
- Live avg holding 10.6h (matches 1H), not ~3.8 days (4H) → confirms bot really runs the broken 1H variant.

**Why:** Auto-generated/edited config never had timeframe switched to 4h after AO was researched on 4H. AO zero-cross on 1H whipsaws in chop (3 entries on a single day 2026-04-07).

**How to apply:** When diagnosing any "awesome"/AO config, FIRST check timeframe_signal matches the 4H research basis. Likely affects other R10 AO bots (VVV, SAND). Fix = switch to 4h; but even fixed, ALICE 4H has only 10 trades/2yr → recommend retire or pair with a higher-freq strategy. Check whole portfolio for timeframe field drift vs documented research TF.
