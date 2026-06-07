---
name: dashboard-rewrite-review-2026-06-07
description: Round-3 dashboard-rewrite metric-correctness review — SPA asset-serving blocker, ratio-shown-as-dollars, total-vs-expectancy heatmap mislabel
metadata:
  type: project
---

Round-3 review of dashboard-rewrite (branch dashboard-rewrite-impl) under the METRIC-CORRECTNESS lens. The 6 prior blockers (trade-gate since= cohort scoping, WS ?token=, token injection) are genuinely fixed in code and verified.

**Why:** New React/FastAPI dashboard replacing Streamlit; traders read these panels to judge live bot health, so misleading numbers are high-stakes.

**How to apply:** When reviewing this dashboard, re-check these on any future change:

1. BLOCKER (live, pre-existing but perpetuated this round): `api/main.py` `spa_catch_all` at `@app.get("/{full_path:path}")` is registered BEFORE the StaticFiles mount, so it shadows ALL static assets. `GET /assets/index-*.js` returns injected index.html (content-type text/html) instead of the JS bundle — verified live with TestClient when `web/dist` exists. nginx `/v2` proxies assets to FastAPI (does not serve dist directly), so the SPA is broken end-to-end in the documented deploy. Fix: catch-all must serve the real file when `(_dist/full_path).is_file()`, else fall back to injected index.html. Existed since commit 8fb8ea6 (FileResponse), commit 90aae68 changed it to HTMLResponse but kept the shadowing.

2. MAJOR: `reward_to_avgloss` / `real_r` is a dimensionless ratio (avg_pnl / mean|loss|, an R-multiple) but the trade-gate table renders it as `$X.XX` (NewPanels.tsx ~line 298) under header "Reward/$Avg-Loss". A trader reads $1.50 as dollars. Drop the `$`, label it as a ratio/R.

3. MAJOR: Panel titled "Expectancy Heatmap" but `/api/heatmap` maps cell `pnl` to **total_pnl** (metrics.py line 291), not avg_pnl. Expectancy = per-trade avg. Backend computes avg_pnl but discards it. Either use avg_pnl or rename to "Total PnL Heatmap." Color-by-sign is unaffected (same sign), but the displayed magnitude conflates frequency with edge.

4. MINOR: CloseReasonDonut segments sized by trade COUNT, colored by PnL sign; legend shows count/pct only, not the dollar total_pnl (it's in the payload). A big green TP slice + small red SL slice can hide that the red slice lost more dollars. Surface total_pnl per reason.

5. NIT: `pnl_positive = total_pnl >= 0` → an exactly-$0 break-even bucket renders green.

6. NIT (pre-existing convention): MonthlyCalendar/get_calendar_pnl group PnL by `DATE(timestamp)` = ENTRY date, so a multi-day hold (common on 4H bots) attributes realized PnL to the open day, not close day. Documented as matching get_daily_pnl.

Verified-honest: RiskAtStakeHeader (max_sl_loss sums only protected positions, unprotected flagged red, no fake % gauge, *50 hack avoided); heatmap DOW labels (pandas Mon=0..Sun=6 matches backend dayofweek) + <20-sample masking + diagnostic disclaimer; trade-gate MIXED verdict + config_count badge + ∞ PF handling; WS token wiring end-to-end; trade-gate since= parity with forward_test_report CLI (`timestamp >= since` on entry ts, same column).
