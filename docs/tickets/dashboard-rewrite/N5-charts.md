# N5 — Charts (equity / per-bot PnL / daily PnL)

**Phase C · est. M (1 day)**

## Goal
Port the portfolio + single-bot charts from Plotly to recharts.

## Chart-library decision (review-approved)
- **recharts only** for bar/area/line (equity curve, per-bot PnL bars, daily PnL bars).
- Candlesticks → lightweight-charts (N6). **nivo is dropped** — the calendar/heatmap (N10) use custom
  CSS grids instead (avoids a ~600KB dep for two simple grids). Justify any new dep in the ADR.

## Scope
- `EquityCurve` (area, cumulative PnL, zero baseline) — `/api/equity`.
- `PerBotPnl` (horizontal bars, green/red by sign) — portfolio summary.
- `DailyPnl` (bars, green/red) — `/api/daily-pnl`.
- Match the dark theme tokens (N3); green/red semantics identical to current.
- Live-update on WS snapshot.

## Tests (write first) — vitest + RTL
- each chart renders with mock data and with empty data (no crash, shows empty state);
- color mapping: positive→profit, negative→loss;
- equity curve plots cumulative series in order.

## Acceptance
- Visual + numeric parity with the Streamlit Plotly charts.

## Result
Done. Commit `662b94b` on `dashboard-rewrite-impl`.

Files added/changed:
- `web/src/components/Charts.tsx` — EquityCurve (area), PerBotPnl (horizontal bar), DailyPnl (vertical bar); all recharts; dark-theme palette tokens from N3; empty states; zero-baseline reference line on EquityCurve; color-semantic `data-*` attributes for tests.
- `web/src/__tests__/charts.test.tsx` — 17 vitest+RTL tests written before implementation (TDD); covers render-with-data, empty-state (no crash), color mapping (positive→profit / negative→loss), cumulative series order.
- `web/src/pages/PortfolioPage.tsx` — integrated EquityCurve, DailyPnl, PerBotPnl sections with TanStack Query fetching `/api/equity` and `/api/daily-pnl`; updates on WS snapshot via cache hydration.

All 75 tests pass. Build clean (TypeScript strict). recharts `ResizeObserver`/width=0 warnings in jsdom are expected and tests still pass.
