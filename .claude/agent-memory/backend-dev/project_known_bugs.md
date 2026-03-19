---
name: Known bugs from 2026-03-18 code review
description: Critical and high bugs identified in full backend code review, prioritized for fixing
type: project
---

Code review completed 2026-03-18. 21 issues found. Key ones:

**Critical:**
- main.py ~727: If SL verification fails AND close_all_positions() also fails (bare `except: pass`), bot has live unprotected position and continues. Must halt + break.

**High (financial loss risk):**
- exchange.py:187 — `free or total` fallback: when free=0.0 (all margin locked), bot uses total balance for sizing, causing oversized orders that fail at placement.
- main.py:188-196 — Two tracked trades on same side (e.g., 2 longs) both detected as closed simultaneously. Double-records PnL loss, false daily-loss halt.
- main.py:507-691 — balance and num_positions fetched at top of loop but used ~30 seconds later after AI context build. Stale by execution time.
- risk.py:315 — `reset_daily()` zeros consecutive_losses. Conflicts with restart state restoration (losses from prev session wiped on midnight reset). Should NOT reset consecutive_losses on daily reset.
- exchange.py:319-321 — `close_all_positions` symbol normalization can silently fail for non-standard symbol formats; position never closed during shutdown.
- ai_analyst.py:539 — `response` variable referenced in except handler but only assigned inside try block. NameError if _call_api raises before response is set.
- data.py:218-222 — `add_trend_filter` calls reset_index() 3 times redundantly; merge key lookup fragile for non-standard index names; silently disables trend filter.

**Medium:**
- All exchange methods use time.sleep() inside async trading_loop (blocking event loop).
- strategy.py:65-70 — RSI directional offsets (+3, +10, -20, -13) are magic numbers relative to config rsi_min/rsi_max. Not configurable.
- main.py:799 — Timeframe parsing fails for "4h" (gives 4 min, not 240 min).
- main.py:247-276 — Current price fallback for SL/TP close reason is unreliable (price already moved).
- metrics.py:67 — Sharpe annualization uses sqrt(365) but should use sqrt(365 * candles_per_day).

**No test coverage for:**
- add_trend_filter
- ContextBuilder.build
- check_closed_positions

**Why:** This is a financial bot where bugs directly cause money loss. These issues need fixes before going live.
**How to apply:** When touching any of these areas, fix the bug as part of the change. Prioritize Issues 4, 13, 9 first.
