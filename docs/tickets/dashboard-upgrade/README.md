# Dashboard Upgrade — Ticket Series

> ⚠️ **SUPERSEDED (2026-06-07)** by `../dashboard-rewrite/` (Vite + React SPA + FastAPI + WS rewrite).
> The metric definitions & overfitting guards here were absorbed into that series (N9/N10); T6 → N11.
> Kept for history only — do not implement these Streamlit tickets.


> Source: analysis of an external multi-bot dashboard (crypto.dobot.trade). Goal = adopt the
> **genuinely useful, non-fancy** panels that our data already supports, without overfitting and
> without breaking the existing Streamlit app. See the parent chat for the full feature triage.

## Why these (and not the rest)
Adopted = high decision value **and** backed by existing `trades.db` data **and** low overfit risk.
Rejected (fancy / data-limited): gainers ticker, long/short ratio gauges, CPI countdown, AI-team
chat theater, STATE gauges — these add screen candy, not decisions.

## Hard constraints (apply to EVERY ticket)
- **TDD**: write the failing test first, then the code. **Never edit a test to make code pass** if
  the test was correct to begin with (project rule).
- **Additive only**: new pure functions in `dashboard/queries.py` + `dashboard/components.py`, wired
  into `dashboard/app.py`. Do not alter existing function signatures or remove panels.
- **Data hygiene** (match existing code):
  - Always exclude `close_reason = 'orphan_reconcile'` from performance stats.
  - Closed-only for performance: `status = 'closed'`.
  - `timestamp` is ISO-8601 with tz (`2026-06-05T16:01:01.232670+00:00`) — use SQLite
    `strftime(...)` / pandas `to_datetime(utc=True)`, never naive string slicing for hour/weekday.
  - Guard divide-by-zero; PF with zero losses → `inf` (render as `"inf"`/`"N/A"`, never crash).
- **R-multiple awareness**: any expectancy/PF shown is per-symbol (clean attribution); do NOT sum raw
  `pnl/initial_balance` across the shared wallet (project rule — past 10x inflation bug).
- **No new heavy deps**: stdlib + pandas + plotly (already used). No new pip packages.
- **Caching**: wrap every new `get_*` loader in app.py with `@st.cache_data(ttl=30)` like the rest.
- **Schema reality** (verified 2026-06-07):
  - `trades` columns: id, timestamp, symbol, side, entry_price, exit_price, size, stop_loss,
    take_profit, pnl, pnl_pct, status, close_reason, duration_seconds, ai_* . **No `signal_source`**.
  - `close_reason` distinct values today: `sl`, `tp`, `orphan_reconcile`, `graceful_shutdown`
    (NO `trail` / `breakeven` — see T6).

## Execution order (workflow picks up sequentially)
**Phase 1 — Tier 1 (do first, data ready, no overfit):**
1. `T1-close-reason-breakdown.md`
2. `T2-trade-gate-table.md`
3. `T3-risk-at-stake-header.md`

**Phase 2 — Tier 2 (diagnostic, mark explicitly non-gating):**
4. `T4-monthly-pnl-calendar.md`
5. `T5-hour-dow-expectancy-heatmap.md`

**Phase 3 — backend unlock (separate approval, bigger blast radius):**
6. `T6-close-reason-taxonomy.md` (enriches engine close_reason → unlocks T1's full TRAIL-vs-SL value)

## Definition of Done (per ticket)
- New tests added under `tests/` and **passing**; full `pytest tests/ -q` stays green.
- Panel renders in `streamlit run dashboard/app.py` with empty DB (no crash) and with real DB.
- One-line note appended to the ticket's `## Result` section (what shipped, any deviation).
- Commit message references the ticket id (e.g. `dashboard T2: trade-gate table`).
