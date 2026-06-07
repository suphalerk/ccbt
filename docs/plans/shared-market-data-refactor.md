# Plan v3 — Shared Market-Data Layer (kill the per-bot API fan-out)

Status: GATED-GO. Passed adversarial review (sa/qa/backend) + an 8-scenario dry-trace
of the real code paths. v1 blocker (caching positions) removed in v2; v2 blocker
(gating on `can_open`) removed in v3. **Safe to build once the must-fix items below are
honoured.** Touches the live trading core — feature-flagged + reversible.

## Problem
~62 bots each fetch their own data every tick: `get_balance()` ×3,
`get_positions()` (per-symbol), `get_ohlcv()` ×~4 — ~600 calls/tick vs a true need of
~50. Triggers Binance -1003/418 (per-IP limit; all bots share ONE proxy IP). Blocks mainnet.

## Design (cache balance + OHLCV; positions never cached, only gated)

### `SharedMarketData` (in `bot/shared_exchange_pool.py`) — **synchronous, `threading.Lock`**
ccxt is sync called inside the async loop, on a single event-loop thread → a sync cache
is the correct primitive (asyncio.Lock/single-flight were incoherent — dropped).
- **balance**: one float, TTL 30s. `get_balance(fresh=False)`. Serves all bots.
- **ohlcv**: keyed `(symbol, timeframe)` → store `(df, last_closed_open_time, served_limit)`.
  Positions: **not cached** (no positions in this object).

**Lock discipline (MAJOR-6):** non-reentrant lock held only to read/decide-miss, then
RELEASED; the blocking ccxt fetch runs UNLOCKED; re-acquire only to write-through. Never
hold the lock across a fetch (`_retry` can `time.sleep` backoff → would self-deadlock).
Never nest SharedMarketData calls.

### OHLCV invalidation — **wall-clock candle boundary (MAJOR-3, must-fix)**
A cache entry is FRESH iff:
`served_limit >= requested` **AND** `floor(now_utc, tf) == stored_last_closed_open_time`.
Invalidation is wall-clock driven, NOT df-index-only — otherwise a 4H bot reuses a stale
entry across a rollover and `iloc[-2]` reads a candle up to 4h old (missed signal).
`fetch_ohlcv` returns the forming bar as `index[-1]`; we store/compare on the last CLOSED
candle (`index[-2]`) so a forming candle can never reach `iloc[-2]` (look-ahead impossible).
Write-through keeps `served_limit = max(old, new)` (MINOR-7) so a small-limit fetch never
shrinks a larger cached frame.

### Positions — never cached; cut volume by gating on OWN tracked trades (BLOCKER-1/2 fix)
**Do NOT gate on `PortfolioManager.can_open()`** — it returns False for every holder (it
includes a duplicate-coin clause and every holder registered its own symbol), so gating on
it would make holders skip their own position fetch → missed SL/TP close (BLOCKER-1) or a
false-close cascade (BLOCKER-2).

Correct gate for the routine fetch at `engine.py:563`:
```
if (not self._tracked_trades) and (portfolio_manager.open_count >= max_positions):
    positions = []          # bot holds nothing AND global cap full → can't act, skip fetch
else:
    positions = self._client.get_positions()   # fresh, per-symbol — ALWAYS if holding a trade
```
Evaluate `not self._tracked_trades` FIRST (cheap, sync). Any bot with a tracked trade
ALWAYS fetches. Safety/monitor reads stay fresh & per-symbol everywhere: 522, 535, 608,
1278/1347 (Bybit only — Binance SL/TP verify uses `_get_binance_algo_orders`, not
get_positions), 1633 (shutdown).

### Startup (the real -1003/418 trigger)
- **Warm caches BEFORE launching tasks**: balance once; OHLCV per unique `(symbol, tf)` at
  `limit = max(max(100, ema_trend*3) over that group)` (MAJOR-4 — uniform 100 would miss
  every trend fetch and re-burst). Warming runs through ccxt `enableRateLimit` (paced).
- **Stagger** `create_task` ~200ms apart.

### Feature flag + parity (MAJOR-5, must-fix)
`CCBT_SHARED_MARKETDATA=1` (default OFF in code; ON in `start.sh`). When OFF: no cache, the
563 gate is INACTIVE (unconditional `get_positions()`), no stagger — byte-for-byte today.
The gate AND stagger are both behind the flag. `main.py` single-bot unaffected.
Cache-hit early-returns as the FIRST statement of `get_balance`/`get_ohlcv`, BEFORE
`_retry`/`_rate_limit`, and does NOT advance `_last_request_time` (MINOR-10).

## Exact file changes
1. `bot/shared_exchange_pool.py` — `SharedMarketData` (threading.Lock; balance + ohlcv;
   tiny lock scope; wall-clock invalidation; served_limit monotonic).
2. `bot/exchange.py` — `BybitClient(..., market_data=None)`; `get_balance`/`get_ohlcv`
   early-return from cache before `_rate_limit` when `market_data` set; `get_positions`
   unchanged. Optional `fresh=True` plumbed for daily-reset(678) + check_leverage(1054).
3. `bot/engine.py` — thread `market_data`; add the `_tracked_trades`+`open_count` gate at
   563 (flag-guarded); optional `fresh=True` at 678 + 1054.
4. `main_multi.py` — build + warm `SharedMarketData`; stagger launches (flag-guarded);
   expose `open_count`/`max_positions` to the engine for the gate.
5. `deploy/macos/start.sh` — `export CCBT_SHARED_MARKETDATA=1`.
6. `tests/` — balance TTL hit/miss/expiry; OHLCV wall-clock boundary for 1h AND 4h
   (warm mid-candle, cross boundary, assert exactly one refetch); served_limit monotonic;
   gate truth table (holder always fetches; only no-trade+cap-full skips); flag-OFF parity;
   no nested cache call (deadlock guard).

## Residual staleness
| read | cached? | risk |
|------|---------|------|
| balance | yes 30s | sizing drift ≤30s; NO circuit breaker reads balance (verified) — safe |
| OHLCV | yes, wall-clock candle boundary | none (closed-candle key; iloc[-2]) |
| positions | **no** | none — always fresh per-symbol; holders always fetch |

## Dry-trace outcome (8 scenarios)
PASSED: balance TTL safety (no breaker reads balance), sync lock model (single thread, no
deadlock if scope tiny), look-ahead impossible, served_limit on reads, flag-OFF construction.
FIXED in v3: BLOCKER-1/2 (gate predicate), MAJOR-3 (wall-clock invalidation), MAJOR-4
(warm limit), MAJOR-5 (flag parity), MAJOR-6 (lock scope), MINOR-7/8/9/10.

## Rollout
1. Unit tests green (incl. flag-OFF parity + gate truth table + candle-boundary).
2. One bot via `main.py`, flag ON — identical vs OFF.
3. 5-bot testnet — call-volume counter drops; no missed closes; SL/TP verify fresh.
4. Full 62-bot testnet 24h — -1003/418 warnings vanish.
5. Rollback = unset flag + restart.

## Effort ~6-8h (incl. tests). Can land phased behind the one flag: balance cache →
OHLCV cache → positions gate + startup warm/stagger.
