# T6 — Enrich close_reason Taxonomy (BACKEND — separate approval)

**Phase 3 · est. M–L (1–2 days) · BACKEND, higher blast radius — get explicit go-ahead before starting**

## Why
T1 can only show `sl / tp / graceful_shutdown` because the engine collapses every stop exit into
`sl`. Our bots trail heavily, so we cannot currently tell a **trailing-stop exit that locked profit**
from a **hard SL that took the full loss** — exactly the insight the external dashboard surfaces
(TRAIL vs SL vs BE). Enriching the close_reason at the source unlocks T1's real value and feeds T5.

## Scope
Distinguish, at the point of close in `bot/engine.py` (and any other close path), among:
- `tp` — take-profit hit
- `stop_loss` — hard SL hit while position still at a loss / at/under entry
- `trail_stop` — stop hit after it had ratcheted into profit (exit price better than entry for the
  side) → was a *winning* trailing exit
- `breakeven` — stop moved to ~entry (TP_ONLY mode / BE logic) and hit near entry
- `manual` / `panic` / `graceful_shutdown` — mode-driven closes (keep existing)

Source of truth must be **how the stop was actually placed/ratcheted**, not a guess from PnL sign
alone — but PnL-vs-entry can disambiguate trail vs hard-SL when the close path can't.

## ⚠️ Risk — this touches the live close path
- Must NOT change *when/whether* a position closes or an SL/TP is placed — only the **label**.
- Existing close-reason consumers must still work: `orphan_reconcile` exclusion everywhere,
  `graceful_shutdown`, dashboard queries, `forward_test_report` (filters orphan only — OK).
- Binance algo-order cleanup (`cancel_all_orders` after any close) must be unchanged.
- Backward compat: old rows keep `sl`/`tp`; new labels are additive. T1's donut already auto-adopts
  new buckets, so no dashboard change needed.

## Files
- `bot/engine.py` — set the richer reason where positions close (SL/TP/trail/BE/mode paths).
- Possibly `bot/logger.py` if reason normalization lives there.

## Tests (write first) — `tests/test_close_reason_taxonomy.py`
- a ratcheted stop that exits in profit → `trail_stop` (not `sl`).
- a hard SL at the original level (loss) → `stop_loss`.
- BE stop hit near entry → `breakeven`.
- TP hit → `tp`. panic/graceful unchanged.
- **No behavioral change**: a regression test asserting the close *decision* (close or not, size,
  SL/TP placement) is identical before/after — only the string label differs.

## Acceptance
- `pytest tests/ -q` green incl. existing engine/close tests.
- After deploy, new closed trades show `trail_stop`/`stop_loss`/`breakeven`; T1 donut splits them
  automatically with zero dashboard edits.

## Result
_(fill on completion)_
