---
name: project-dashboard-round3-frontend-fix
description: Round 3 frontend fix — WS ?token= wiring verified and trade-gate since= regression confirmed fixed
metadata:
  type: project
---

Round 3 frontend fixes verified and committed 2026-06-07 on branch `dashboard-rewrite-impl`.

**WS ?token= never sent (round 3 blocker):** `buildWsUrl()` in `useLiveSnapshot.ts` now reads
`window.__CCBT_TOKEN__` (injected at page load) and appends `?token=encodeURIComponent(token)`
to both `/ws` and `/ws/logs` URLs. Without this the backend gate closed with code 4403 on any
tokened (non-localhost) deployment. Two frontend tests added:
  - `appends ?token= to WS URL when CCBT_DASH_TOKEN is injected via window.__CCBT_TOKEN__`
  - `does NOT append ?token= when CCBT_DASH_TOKEN is not injected`

**Trade-gate since= global filter (round 3 blocker):** `/api/trade-gate` was passing
`since=manifest['added']` globally, emptying the panel when all portfolio trades predate the
manifest date (the normal state day 1 of any new forward-test cohort). Backend fix: added
`cohort_symbols=` param to `get_trade_gate()` — since= only applies to those cohort symbols;
non-cohort symbols always use full trade history. Added `TestLiveShapedDB` tests with a
live-shaped DB (all 240 trades before 2026-06-07 manifest) asserting `n_total > 0` and
12 MIXED rows are visible.

**All other round 1 items already completed in prior rounds:**
- WS envelope shape (nested `{type, ts, data:{portfolio, bots}}`) — round 1
- DOW labels pandas convention (Mon=0, Sun=6) — round 1
- Server value consumption (RiskAtStakeHeader, AIAnalyticsPage) — round 1
- MonthlyCalendar WR label shows win_rate_pct not tradeCount — round 1
- formatMoney !isFinite guard — round 1
- CandleSection hidden when available=false — round 1
- config_count rendered in TradeGateTable rows — round 2

**Test counts at end of round 3:** 158 frontend tests pass (0 fail), 147 backend dashboard tests
pass (0 fail). Build: clean (1 chunk-size warning only, not an error).

**Why:** Backend fixes were already committed; this round confirmed all frontend items were
wired correctly and added/verified all required tests per TDD discipline.
