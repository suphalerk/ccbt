---
name: feedback-dashboard-parity-testing
description: Key findings from N13 parity testing — queries.py semantics that differ from naive assumptions
metadata:
  type: feedback
---

When writing parity tests comparing API JSON vs queries.py, these semantics matter:

1. `get_recent_trades()` does NOT exclude orphans — it's the log-viewer function, returns all statuses.
   Orphan exclusion applies only to analytics functions: `get_closed_trades`, `get_trade_stats`, `get_per_bot_summary`.

2. `get_per_bot_summary()` returns `win_rate` already as a PERCENTAGE (e.g. 50.0, not 0.50).
   Do NOT multiply by 100 when comparing to API's `win_rate_pct`.

3. `get_daily_pnl()` column is named `daily_pnl`, not `pnl`.

4. API `/api/trades` default limit is 100; queries.py `get_recent_trades` default is 50.
   Always call with matching limit for accurate row-count comparison.

5. FastAPI `StaticFiles(html=True)` does NOT serve `index.html` for paths with URL-encoded slashes
   (%2F decoded as '/'). Bot symbols like `BTC/USDT:USDT` in SPA routes (`/bots/BTC%2FUSDT%3AUSDT`)
   cause 404. Fix before cutover: add a catch-all FastAPI route returning index.html for non-/api/ paths.

**Why:** These were discovered writing N13 parity tests. N1 fixed orphan exclusion in analytics
functions but intentionally left `get_recent_trades` inclusive (log-viewer shows all entries).

**How to apply:** When cross-checking API vs queries.py, always check which function the endpoint calls
and verify its exact semantics before asserting row counts or column names.
