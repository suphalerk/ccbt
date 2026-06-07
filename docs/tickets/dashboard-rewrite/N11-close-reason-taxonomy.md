# N11 — Enrich close_reason Taxonomy (BACKEND — separate approval)

**Phase A (ideally) · est. M–L (1–2 days) · BACKEND, higher blast radius — explicit go-ahead before the engine edit**

> Stack-agnostic. Unlocks the full TRAIL-vs-SL value of N9/N10's CloseReasonDonut with zero front-end
> changes (the donut is taxonomy-agnostic). Ship in Phase A with N9 if approved — then N10's donut is
> honest from day one and the must-fix #6 PnL-sign workaround is only an interim safety net.

## Why (VERIFIED)
The engine infers close_reason by **proximity** (`engine.py:126`: `'tp' if dist_to_tp < dist_to_sl else 'sl'`)
and trailing **mutates `info['sl']` in place**, so a stop that ratcheted into profit is still labelled
`sl`. Confirmed: **39 of 192 `sl` rows are profitable**. We can't distinguish a winning trail exit from a
hard-SL loss — the exact insight we want. Enrich at the source.

## Scope — labels at the close point in `bot/engine.py` (+ any other close path)
- `tp` — take-profit hit.
- `stop_loss` — hard SL hit at/under entry (a real loss).
- `trail_stop` — stop hit after ratcheting into profit (exit better than entry for the side) → winning trail.
- `breakeven` — stop moved to ~entry (TP_ONLY/BE) and hit near entry.
- `manual` / `panic` / `graceful_shutdown` — mode-driven (keep existing).

## ⚠️ Risk — touches the live close path
- Must change only the **label**, never whether/when a position closes or an SL/TP is placed.
- **Correction (must-fix #6)**: "byte-identical decision, only the label changes" UNDERSTATES it —
  emitting `trail_stop` requires reading the original SL vs the ratcheted SL (or the pnl sign), i.e. real
  logic, not a pure rename. So tests must assert label **correctness**, not only decision-invariance.
- Preserve all consumers: `orphan_reconcile` exclusion everywhere; Binance algo-order cleanup
  (`cancel_all_orders` after close) unchanged; `forward_test_report` (filters orphan only) unaffected.
- Backward compat: old rows keep sl/tp; new labels additive. N9 donut adopts them automatically.

## Tests (write first) — `tests/test_close_reason_taxonomy.py`
- ratcheted stop exiting in profit → `trail_stop` (NOT `sl`); hard SL loss → `stop_loss`; BE near entry
  → `breakeven`; TP → `tp`; panic/graceful unchanged.
- **Label correctness invariant**: a stop exit with **positive pnl is NEVER labelled `stop_loss`** (the
  property the current proximity inference violates 39/192 times).
- **Regression**: the close *decision* (close-or-not, size, SL/TP placement, algo-order cleanup) is
  unchanged before/after — only the reason string differs.

## ⛔ Unattended-runner note
This edits the LIVE engine close path and requires explicit human go-ahead. An unattended Phase-A
workflow must **SKIP N11** and rely on N10's interim PnL-sign donut (the sanctioned safety net); resume
N11 only on go-ahead.

## Acceptance
- `pytest tests/ -q` green incl. existing engine/close tests; new trades show enriched reasons; donut
  splits TRAIL vs SL with no UI edit.

## Result
_(fill on completion)_
