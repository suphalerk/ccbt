---
name: Known bugs from 2026-03-18 code review
description: Critical and high bugs identified in full backend code review, prioritized for fixing
type: project
---

Code review completed 2026-03-18, exchange.py re-reviewed 2026-03-19.

**Critical:**
- main.py ~727: If SL verification fails AND close_all_positions() also fails (bare `except: pass`), bot has live unprotected position and continues. Must halt + break.
- exchange.py:523-527 — `_modify_sl_binance` calls `fapiPrivateDeleteAlgoOpenOrders` which cancels ALL algo orders (SL + TP). After any trailing stop update, the TP order is silently deleted and never recreated.
- exchange.py:547-558 — `create_order` in `_modify_sl_binance` passes `amount=None`. ccxt may reject before sending to exchange. If step 1 (cancel) already ran, position has no SL.
- exchange.py:523-558 — Race window in `_modify_sl_binance`: if step 2 fails after step 1, position permanently has no SL. Outer except returns False; caller does not halt.

**High (financial loss risk):**
- exchange.py:187 — `free or total` fallback NOTE: verified 2026-03-19 the current code uses `is not None` check which is correct. This entry is STALE — not a bug in current code.
- main.py:188-196 — Two tracked trades on same side (e.g., 2 longs) both detected as closed simultaneously. Double-records PnL loss, false daily-loss halt.
- main.py:507-691 — balance and num_positions fetched at top of loop but used ~30 seconds later after AI context build. Stale by execution time.
- risk.py:315 — `reset_daily()` zeros consecutive_losses. Conflicts with restart state restoration (losses from prev session wiped on midnight reset). Should NOT reset consecutive_losses on daily reset.
- exchange.py:404-405 — `close_all_positions` symbol normalization strips `:USDT` only; non-`:USDT` collateral symbols never match. Position not closed during shutdown.
- exchange.py:609-612 — Bybit `modify_sl` fallback branch calls `self.exchange.privatePostV5PositionTradingStop` directly after `getattr` already returned None. Guaranteed `AttributeError` → SL modification silently fails.
- exchange.py:458 — `get_ticker_price` returns `0.0` if `last` key absent (no warning); raises `TypeError` if `last` is `None`. Both cause downstream sizing errors.
- ai_analyst.py:539 — `response` variable referenced in except handler but only assigned inside try block. NameError if _call_api raises before response is set.
- data.py:218-222 — `add_trend_filter` calls reset_index() 3 times redundantly; merge key lookup fragile for non-standard index names; silently disables trend filter.

**Medium:**
- exchange.py:595 — Bybit `modify_sl` sends `stopLoss` as `str(new_sl)` without applying `_safe_precision` first. Excess decimal places may cause exchange rejection.
- exchange.py:273-275 — `get_ohlcv` does not normalize the `symbol` parameter; callers passing raw symbol (e.g. 'BTCUSDT') get `BadSymbol` crash.
- exchange.py:242-244 — `ccxt.ExchangeError` base class caught and re-raised immediately; transient subclasses (InvalidNonce, RequestTimeout) not retried.
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
**How to apply:** When touching any of these areas, fix the bug as part of the change. Prioritize the three CRITICAL exchange.py bugs (TP deletion, amount=None, race window) before any live Binance trading.
