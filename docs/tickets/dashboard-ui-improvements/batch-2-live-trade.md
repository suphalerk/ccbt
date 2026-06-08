# Batch 2 — Live-trade context

> ✅ **DONE + deployed 2026-06-08** (branch `ui-batch2-livetrade`, merged to main). All numbers computed
> server-side (no math in TS): markprice query adds `stop_loss,take_profit`; `PositionMark` emits
> `dist_to_stop_pct` + `rr_remaining` (null-guarded for missing/zero SL/TP); `PortfolioSummaryResponse` gains
> `notional` (Σ abs(entry)·abs(size), matching canonical `/api/risk`). Review caught a missing notional pin
> test (short with negative size → makes abs() load-bearing) — added. 22 markprice + 218 vitest pass.
>
> **Follow-ups (2026-06-08, post-merge):**
> - **Today's PnL → Bangkok GMT+7** — `get_today_pnl` now groups on `DATE(timestamp,'+7 hours')` (was UTC).
>   Fixed a pre-existing DUPLICATE `get_today_pnl` def (the old UTC one shadowed it) + a WS-clobber bug
>   (`_build_snapshot` omitted `today_pnl`/`notional` → cards flipped to `—` after the first WS snapshot).
> - **Today Net PnL card** (supersedes the plain "Today's PnL" card, testId `header-today-pnl` kept): shows
>   NET = realized(today) + unrealized(live) with a 2-line breakdown + ● live dot; `net_today` computed in
>   the markPrice payload (Python, TTL-cached 5s), read verbatim in TS. Offline fallback → realized-only +
>   "feed offline". Review caught silent-freeze (added `_stale_rebroadcast_loop`) + a toothless net test
>   (poisoned `net_today` to pin server-read). User-approved design "A". 248 vitest + 29 markprice pass.
>
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
