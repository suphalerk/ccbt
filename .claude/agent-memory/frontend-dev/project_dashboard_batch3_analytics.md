---
name: project_dashboard_batch3_analytics
description: Batch 3 analytics features — underwater chart, pill labels, table filters — shipped on branch ui-batch3-analytics
metadata:
  type: project
---

Batch 3 analytics features shipped on branch `ui-batch3-analytics` (commit db3f0de).

**#6 Underwater drawdown sub-chart:**
- `get_equity_curve()` in `dashboard/queries.py` now computes `underwater` column server-side: `cumulative_pnl − running_max(cumulative_pnl)` (always ≤ 0).
- `api/models.py` `EquityPoint` gains `underwater: float = 0.0`.
- Router `api/routers/portfolio.py` passes it through.
- `web/src/api-types.d.ts` `EquityPoint` updated with `underwater: number`.
- `Charts.tsx` `EquityPoint` interface gains optional `underwater?: number`; new `UnderwaterChart` recharts bar component exported.
- `PortfolioPage.tsx` renders `UnderwaterChart` below `EquityCurve` in the equity column.

**Why:** HARD RULE: financial math server-side only. The underwater series is the running-max drawdown visualization.

**#7 BotPill symbol label:**
- `pillLabel(symbol)` helper strips USDT suffixes and truncates to 6 chars max.
- `BotPill` now shows `<span data-testid="bot-pill-label-{symbol}">` with the short label.
- `aria-label` on the Link still holds the full symbol.

**#8 BotOverviewTable filters:**
- `searchText`, `statusFilter`, `strategyFilter` state added.
- `filtered` useMemo composing before sort.
- Filter bar UI: text input, status select, strategy select, count badge, clear button.
- Empty-filter state: `data-testid="bot-overview-no-match"`.

**Tests:** 6 Python pin tests in `tests/test_batch3_underwater.py` (hand-computed [0,-5,0,-8] series). 23 vitest tests in `web/src/__tests__/batch3-analytics.test.tsx` + 1 in portfolio.test.tsx updated. Build clean. 242 TS tests pass.

**Review fixes applied (commit 2c5382f):**
- MAJOR 1: `BotPill` and table status cell now branch on `status==='running'` (what `bot/engine.py` actually emits). Error styling driven by `error_count > 0`, not `status==='error'`. All test fixtures updated to use `'running'`/`'stopped'`.
- MAJOR 2: Two new exact-match vs substring tests added — `'ema'` must not match `'ema_crossover'`; `'run'` must not match `'running'`. Pins `===` semantics in filter so a `.includes()` mutation fails.
- MAJOR 3: `UnderwaterChart` exposes `data-max-depth` attribute; test asserts it equals `-8` for the `[0,-5,0,-8]` fixture, catching any magnitude corruption.

**How to apply:** Future analytics additions follow the same pattern: server-side math → EquityPoint (or new model) → api-types.d.ts → Charts.tsx → PortfolioPage.tsx.
