# T3 — Risk-at-Stake Header

**Tier 1 · Phase 1 · est. S (½ day)**

## Why
The external dashboard's single best risk widget: "Max SL Loss -$10.03 (-3.19%)" = if every open
SL hits right now, this is the damage. One number that answers "how exposed am I this second?" —
more honest than open-position count alone.

## Scope
From currently-open trades compute:
- `open_count`
- `notional` = Σ |entry_price × size|
- `max_sl_loss` = Σ per-open loss-if-SL-hits =
  for each open trade with a stop_loss: `abs(entry_price - stop_loss) * size` (skip rows w/ null SL,
  and **count them** as `unprotected` — surface that, an open position without a stored SL is a flag).
- `max_sl_loss_pct` = max_sl_loss / balance — balance source: prefer latest `bot_health.total_pnl`
  rollup is NOT balance; if no reliable balance available, show `$` only and omit `%` (do NOT fabricate
  a balance — the existing single-bot `risk_gauge` already fakes one via `*50`; do not copy that hack).

## Files
- `dashboard/queries.py` → `get_open_risk(db_path=None, symbol=None) -> dict`
  - keys: `open_count, notional, max_sl_loss, unprotected_count`. Pure, empty-safe (all zeros).
- `dashboard/app.py` → extend the portfolio header metrics row (and single-bot header) with:
  "Risk at Stake" = `$max_sl_loss` and a caption `N open · M unprotected · $notional notional`.
  Keep existing metrics; just add columns/a sub-row.

## Tests (write first) — `tests/test_dashboard_open_risk.py`
- empty DB → all zeros, no crash.
- seeded open trades (long & short, mixed) → max_sl_loss is sum of abs(entry-sl)*size;
  short position SL above entry handled (abs); a row with null stop_loss increments `unprotected_count`
  and is excluded from the loss sum; notional correct.

## Acceptance
- Header shows a live $-at-risk number; `unprotected_count > 0` is visibly flagged (e.g. red caption).
- No fabricated balance; `%` only shown if a real balance is available.

## Result
_(fill on completion)_
