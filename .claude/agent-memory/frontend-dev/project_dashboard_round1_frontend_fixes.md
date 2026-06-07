---
name: project-dashboard-round1-frontend-fixes
description: Round 1 frontend fixes for dashboard rewrite — WS envelope, DOW labels, server value consumption
metadata:
  type: project
---

Round 1 frontend fixes committed 2026-06-07 on branch `dashboard-rewrite-impl`.

**WS envelope shape (BLOCKER #1):** `api/ws.py` sends `{type, ts, data:{portfolio, bots}}` — NOT flat.
`useLiveSnapshot.ts` updated to read `snap.data.portfolio` / `snap.data.bots` / `snap.ts`.
`ws-types.ts` updated with nested `WSSnapshotData` interface. SSR fallback port is 8501.

**DOW labels (BLOCKER #4):** `DOW_LABEL_MAP` in `NewPanels.tsx` corrected to pandas convention:
Mon=0, Tue=1, Wed=2, Thu=3, Fri=4, Sat=5, Sun=6. (Was JS convention Sun=0.)

**Server value consumption:** RiskAtStakeHeader now reads `max_sl_loss`/`notional` from server.
AIAnalyticsPage reads `aggregate.weighted_accuracy_pct`/`avg_influence_factor` from server.

**MonthlyCalendar WR label:** Shows `win_rate_pct%` not `tradeCount`. Added `win_rate_pct` field
to `CalendarCell` model and router. openapi.json + api-types.d.ts regenerated.

**formatMoney guard:** Added `!isFinite` check — returns '—' for Infinity/NaN.

**Why:** Backend fixes were committed separately; frontend was left with the old flat WS shape,
wrong DOW mapping, and TS-computed financial values that should come from server.

**How to apply:** When adding WS consumers, always check `api/ws.py` for the actual payload shape.
When adding metric displays, always check if server aggregate is available first.
