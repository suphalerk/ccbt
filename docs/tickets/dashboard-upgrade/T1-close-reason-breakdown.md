# T1 — Close-Reason Breakdown

**Tier 1 · Phase 1 · est. S (½ day)**

## Why
We trail heavily. Knowing the exit mix (how trades actually close) tells us whether stops are doing
their job or cutting winners. The external dashboard shows `TP / TRAIL / BE / OTHER %` — we can show
the slice our data supports **today**, and T6 later enriches it to full TRAIL-vs-SL granularity.

## Scope (with current taxonomy)
Render the distribution of `close_reason` over closed trades (excluding `orphan_reconcile`), with
per-bucket count, %, total PnL, and avg PnL/trade. Buckets present today: `sl`, `tp`,
`graceful_shutdown`. Code must be taxonomy-agnostic (auto-pick up `trail`/`breakeven` once T6 lands).

## Files
- `dashboard/queries.py` → `get_close_reason_breakdown(db_path=None, symbol=None) -> pd.DataFrame`
  - columns: `close_reason, count, pct, total_pnl, avg_pnl` ; ordered by count desc.
  - WHERE `status='closed' AND COALESCE(close_reason,'')!='orphan_reconcile'` + optional symbol.
  - `pct` = count / total_closed * 100 (0 when no trades).
- `dashboard/components.py` → `close_reason_donut(breakdown_df, height=300) -> go.Figure`
  - donut (hole=0.4), green for `tp`, red for `sl`, grey others; empty-df → titled "no data" fig
    (match existing empty-guard pattern). Label shows `reason · n · pct%`.
- `dashboard/app.py` → add an "Exit Breakdown" sub-section in BOTH portfolio view (symbol=None) and
  single-bot view (symbol=selected). Use a `@st.cache_data(ttl=30)` loader.

## Tests (write first) — `tests/test_dashboard_close_reason.py`
- empty DB → returns empty df, donut returns a Figure (no exception).
- seeded in-memory/temp DB with known sl/tp/graceful/orphan rows →
  - orphan excluded from counts;
  - pct sums to ~100 over included buckets;
  - total_pnl & avg_pnl per bucket correct;
  - symbol filter narrows correctly.

## Acceptance
- Panel shows in both views; no crash on empty/partial data.
- Comment in code notes that `trail`/`breakeven` appear automatically after T6.

## Result
_(fill on completion)_
