# Batch 2 — Live-trade context

> From [README.md](README.md) ADD_NOW #3, #5. Branch: `ui-batch2-livetrade`. TDD + review.
> No new exchange call. Backend changes are small + server-side (no math in TS).

## #3 — SL + TP + R:R / dist-to-stop% columns in UpnlPanel
- Add `stop_loss, take_profit` to the SELECT in `api/markprice.py` (open-positions query ~line 166) so each
  uPnL position carries SL/TP.
- Compute **R:R remaining** and **dist-to-stop %** SERVER-SIDE (Python — no math in TS) and include them in
  the per-position uPnL payload. Update `web/src/ws-types.ts` (WSUpnlPayload) + `UpnlPanel.tsx` columns.
- Pin the math with a markprice test (hand-computed R:R for a long and a short).

## #5 — 'Today's PnL' + 'Open positions / notional' header cards
- Today's PnL = last row of `/api/daily-pnl` (already available). Notional = Σ(size·entry) over open trades —
  add a `notional` field to the portfolio/summary endpoint (server-side sum, no TS math).
- Add two cards to the PortfolioPage header (`portfolio-header` grid) reusing the existing card component.

## Constraints
- No math in TS; never-interfere; keep markprice broadcast ≤1/sec throttle intact.
- Tests: markprice R:R/dist pin (hand-computed), summary notional pin, UpnlPanel renders new columns,
  header cards render today's PnL + notional. Rebuild bundle.
