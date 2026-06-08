---
name: project_dashboard_batch2_review_fixes
description: Batch 2 review fix: notional pin test added to TestPortfolioSummary; abs() mutation coverage; 90 python + 218 ts tests pass
metadata:
  type: project
---

Batch 2 (ui-batch2-livetrade) had one MAJOR review finding: no Python test exercised the `notional = SUM(abs(entry_price * size))` path in `api/routers/portfolio.py`. The vitest fixture used a hardcoded `notional:25000.0` so the `.abs()` call on negative-size short positions was untested.

**Fix applied:** Added two tests to `TestPortfolioSummary` in `tests/test_api_rest.py`:
- `test_notional_pin_long_and_short`: seeds BTC long (entry=30000, size=+0.5) + ETH short (entry=2000, size=-3.0); asserts `notional == 21000.0`. The short's negative size makes `sz.abs()` load-bearing — dropping it yields 9000.0 and the test fails.
- `test_notional_zero_when_no_open_trades`: asserts `notional == 0.0` for an empty DB.

**Why:** spec (batch-2-live-trade.md Constraint #5) required a summary notional pin test; mutation of dropping `.abs()` would silently pass the entire suite without this test.

**Results after fix:** 90 Python tests pass (test_markprice.py + test_api_rest.py); 218 vitest tests pass; `npm run build` clean. The 54 "unhandled errors" in vitest are pre-existing JSDOM environment issues (ResizeObserver/canvas not in jsdom) from lightweight-charts — not test failures.

**Commit:** `94bfe75` on branch `ui-batch2-livetrade`.
