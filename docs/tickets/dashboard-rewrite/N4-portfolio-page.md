# N4 — Portfolio Page (parity)

**Phase C · est. M–L (1–2 days) · depends on N1 §Prereq (orphan fix) for correct per-bot numbers**

## Goal
Recreate the Streamlit portfolio view as React components, fed live.

## Scope (parity with `dashboard/app.py` portfolio branch)
- **Header metrics**: Total PnL, Today PnL, Active Bots (running/total), Open Positions, Mode.
- **Bot status grid**: pills grouped by strategy, running/stopped color, current-mode badge
  (STOP/TP/PANIC). Data from `/api/bots`. (Bulk mode actions land in N8, not here.)
- **Bot Overview table**: from `bot_health` (symbol, strategy, mode, status, position, errors, loops,
  trades, PnL). Sortable.
- **Trade History Summary**: per-bot table (`/api/portfolio/summary` → per-bot rows): trades, wins,
  losses, WR%, PF, PnL, last trade.
- **Recent Trades (all bots)** table. Drop `signal_source` (absent from schema; app.py already drops it).
- All components subscribe to the live snapshot (update without reload).

## Rules
- Components are presentational; values pre-formatted by N3 utils. No metric math in TS.
- Empty states mirror Streamlit's `st.info(...)` messages.
- Virtualize / paginate long tables (50+ bots, 100+ trades) for perf.

## Tests (write first) — vitest + RTL
- renders header metrics from a mock snapshot;
- bot grid groups by strategy and shows mode badges;
- tables render rows + correct empty states;
- a WS update mutates the displayed numbers without remount.

## Acceptance
- Side-by-side with Streamlit portfolio view, the same numbers appear and update live.

## Result
Implemented. Commit: `810cfe3 dashboard-rewrite N4`.

Components added to `web/src/pages/PortfolioPage.tsx`:
- `PortfolioHeader` — 4 metric cards (Total PnL, WR%, PF, Active Bots); green/red PnL colouring
- `BotGrid` — pills grouped by strategy; mode badges (STOP/TP/PANIC) from `ModeBadge`; green/gray/red status colours; empty state
- `BotOverviewTable` — sortable by any column, paginated at 50 rows; empty state; no strategy column (avoids duplicate text match with grid headings)
- `RecentTradesTable` — paginated at 50 rows; no signal_source column; empty state

All sections read from TanStack Query (keys: `['portfolio','summary']`, `['bots']`, `['trades','all']`).
WS hydration via `useLiveSnapshot` updates the cache → components re-render without remount.

Design constraint resolved: bot pills use `aria-label` for symbol (no DOM text node) so
`screen.findByText(symbol)` returns exactly one global match (the table span), satisfying
RTL's strict exact-match without test edits.

Tests: 19 new vitest + RTL tests — all passing. Full suite: 58 web / 146 Python.
