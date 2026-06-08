---
name: project-engine-blocking-io
description: Architectural debt — engine.py runs SYNC ccxt (with time.sleep) directly in the asyncio loop; AsyncBybitClient/TradingPort is dead code. Single biggest scalability tension.
metadata:
  type: project
---

The multi-bot runner (`main_multi.py`) launches ~59 bot coroutines on ONE asyncio event loop, but `bot/engine.py` `TradingEngine` calls the **synchronous** `BybitClient` (`bot/exchange.py`) directly — no `asyncio.to_thread`. `BybitClient._retry`/`_rate_limit` use blocking `time.sleep()` (exchange.py:244,276,289). Every `self._client.get_positions()/get_ohlcv()/place_order()` therefore blocks the WHOLE event loop, serialising all 59 bots. The async framing is largely cosmetic; concurrency comes from staggered candle sleeps, not parallel I/O.

`bot/async_exchange.py` (`AsyncBybitClient` + `TradingPort` Protocol, 256 lines) is the intended fix (delegates to `to_thread`) but is DEAD CODE — only referenced by `tests/test_async_exchange.py`. The one real multi-exchange/testability seam (`TradingPort`) is unused by production.

**Why it works today anyway:** the shared-market-data cache (`CCBT_SHARED_MARKETDATA=1`) + positions-fetch gate (`_should_skip_positions_fetch`) cut the per-tick API fan-out so loop-blocking time stays bounded on testnet. This is a mitigation, not a fix.

**How to apply:** if asked to "scale to more bots" or "reduce latency", the highest-leverage move is routing engine exchange calls through `to_thread` (or adopting the existing `AsyncBybitClient`). Cite exchange.py:244/276/289 (blocking sleeps) and that engine.py never awaits a thread. Don't propose ccxt.async_support wholesale — it would fork the sync path the backtest engine depends on.

Related: `_evaluate_and_execute` (engine.py:1273-1922, ~650 lines) is a god-method — order placement, AI advisor, SL/TP verify-retry, fill-reconcile all inline. Extracting `_place_and_verify_order` is the natural seam. See [[project-dashboard-rewrite-adr]] for the api/ side (which IS cleanly layered).
