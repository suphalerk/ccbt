---
name: feedback-no-financial-math-in-ts
description: Never compute financial/trading values in TypeScript — always consume pre-computed server fields
metadata:
  type: feedback
---

Never compute financial values (notional, max SL loss, accuracy %, influence factor) in TypeScript.
All such values must come from the Python backend.

**Why:** Dashboard was computing `maxSlLoss` and `notional` from raw rows in TS (Round 1 fix). The server already provides these as `max_sl_loss` and `notional` on `OpenRiskResponse`. Similarly, `AIAnalyticsPage` was computing `weighted_accuracy_pct` from rows instead of using server's `AICalibrationAggregate`.

**How to apply:** When adding/reviewing dashboard components:
- Check if the server response already has an aggregate field before writing TS math
- Prefer `data.max_sl_loss` over loop-summing `rows`
- Prefer `data.aggregate.weighted_accuracy_pct` over weighted average in TS
- `formatMoney` / `formatPct` etc. are display-only — no business logic allowed there
