# T5 — Hour × Day-of-Week Expectancy Heatmap

**Tier 2 · Phase 2 · est. M (1 day) · DIAGNOSTIC ONLY — must NOT become an auto-trade gate**

## Why
The external dashboard's "DAYS / 4H slot grid". We already proved a **trading-hours filter works**
(research findings). A weekday × hour-bucket expectancy map helps spot consistently-bad slots.

## ⚠️ Overfitting guard (the whole point of this ticket's care)
With ~277 closed trades spread over a 7×N grid, most cells have a tiny sample. This view is for
**human diagnosis only**:
- **Mask** any cell with `count < min_samples` (default 5) — render it blank/grey, never colored.
- Show the `count` in every cell so sparsity is obvious.
- Section header must literally say "Diagnostic — not a trading rule. Low-sample cells are masked."
- Do **not** write any code that consumes this to filter live trades.

## Scope
Group closed trades (excl. orphan) by `strftime('%w', timestamp)` (weekday) ×
`hour bucket` (configurable bucket size; default 4h → 6 buckets, matching most bots' TF). Cell metric:
mean PnL (expectancy proxy) + count; optionally WR.

## Files
- `dashboard/queries.py` → `get_hour_dow_stats(db_path=None, symbol=None, bucket_hours=4) -> pd.DataFrame`
  - columns: `dow (0-6), hour_bucket, trades, avg_pnl, win_rate, total_pnl`. UTC-based via strftime.
- `dashboard/components.py` → `expectancy_heatmap(stats_df, min_samples=5, height=360) -> go.Figure`
  - 7 rows (Sun..Sat) × buckets; color = avg_pnl diverging (red/green) but **only** where
    `trades >= min_samples`; masked cells grey with just the count; hovertext shows n/WR/avg.
- `dashboard/app.py` → "Expectancy by Time (diagnostic)" section with the warning caption + a
  `bucket_hours` selector (4h default; 1h/2h options).

## Tests (write first) — `tests/test_dashboard_hour_dow.py`
- `get_hour_dow_stats`: weekday/hour bucketing from ISO+tz timestamps correct (UTC); a known trade at
  `2026-06-07T13:00:00+00:00` (Sunday) lands in dow=0, bucket=12-16h; orphan excluded.
- `expectancy_heatmap`: cells with count < min_samples are not colored (assert their z is masked/None);
  returns Figure on empty df.

## Acceptance
- Heatmap renders; low-sample cells visibly masked; warning text present; nothing consumes it as a gate.

## Result
_(fill on completion)_
