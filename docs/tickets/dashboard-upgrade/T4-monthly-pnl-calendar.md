# T4 — Monthly PnL Calendar

**Tier 2 · Phase 2 · est. M (1 day) · DIAGNOSTIC (display only, never a trade gate)**

## Why
At-a-glance daily PnL + WR in a month grid (like the external dashboard's June calendar). Good for
spotting streaks and "one bad day" clusters. Pure display — no new edge, no overfit risk because it
gates nothing.

## Scope
A month grid (weeks × days) where each cell shows date, net PnL (color), and WR%. Reuse the existing
`get_daily_pnl()` and add per-day WR. Month selector defaults to current month.

## Files
- `dashboard/queries.py` → `get_calendar_pnl(db_path=None, symbol=None) -> pd.DataFrame`
  - columns: `date (YYYY-MM-DD), daily_pnl, trades, wins, win_rate` over closed excl. orphan.
  - (extends existing get_daily_pnl with wins/win_rate; keep get_daily_pnl untouched).
- `dashboard/components.py` → `monthly_calendar(calendar_df, year, month, height=320) -> go.Figure`
  - a 7-col heatmap/annotated grid; cell color by sign of daily_pnl; annotation `"$+x.xx\nWR n%"`.
  - empty / no-data month → titled empty figure.
- `dashboard/app.py` → "Monthly Calendar" section (portfolio + single-bot). Month `st.selectbox`
  from available months in data.

## Tests (write first) — `tests/test_dashboard_calendar.py`
- `get_calendar_pnl` aggregates by UTC date correctly across the ISO+tz timestamps (a trade at
  `...T23:30:00+00:00` lands on that UTC date, not local); WR per day correct; orphan excluded.
- `monthly_calendar` returns a Figure for a populated month and for an empty month.

## Acceptance
- Calendar renders for current month; switching months works; labeled "diagnostic".

## Result
_(fill on completion)_
