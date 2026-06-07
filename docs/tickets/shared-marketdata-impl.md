# Shared Market-Data Layer — Implementation Handoff Report

**Plan ref**: `docs/plans/shared-market-data-refactor.md` (v3)
**Branch**: `claude/crypto-trading-bot-1xlt7`
**Final test count**: **416 passed, 0 failed, 1 warning** (urllib3/ssl, harmless)

---

## 1. Ticket Summary

| ID | Label | Status | Commit SHA | Tests Added | Files Changed |
|----|-------|--------|-----------|-------------|---------------|
| T1 | SharedMarketData cache core | DONE | `e9d0e1f` | 15 | `bot/shared_exchange_pool.py`, `tests/test_shared_marketdata.py` |
| T2 | Wire cache into BybitClient | DONE | `fd9d4dd` | 18 | `bot/exchange.py`, `tests/test_exchange_marketdata.py` |
| T3 | Positions gate in engine (flag-guarded) | DONE | `6c7d36a` | 15 | `bot/engine.py`, `tests/test_engine_gate.py` |
| T4 | Startup warm + stagger (flag-guarded) | DONE | `0483c86` | 18 | `main_multi.py`, `bot/engine.py`, `deploy/macos/start.sh`, `tests/test_startup_warm.py` |
| T5 | Flag wiring + start.sh + full parity | DONE | `cb21ec9` | 21 | `tests/test_t5_parity.py` |
| FIX | Review-driven fixes (5 findings) | DONE | `e7d7244` | +28 | `bot/shared_exchange_pool.py`, `tests/test_shared_marketdata.py` |

Total new tests added: **115** (T1:15, T2:18, T3:15, T4:18, T5:21, FIX:+28)
Total suite before feature: ~301 passing
Total suite after all tickets + fix: **416 passing**

---

## 2. What Was Built

### SharedMarketData (bot/shared_exchange_pool.py)
- Balance cache: one USDT float, 30s TTL. `get_balance(fresh=False)`.
- OHLCV cache: keyed `(symbol, timeframe)`. Entry is FRESH iff `served_limit >= requested` AND `floor(now_utc, tf) == stored_last_closed_open_time`. The **forming candle is kept at `iloc[-1]`** (identical to live BybitClient shape); the last-closed candle (`index[-2]`) is used only as the internal freshness key.
- Lock discipline: non-reentrant `threading.Lock` held only to read/decide-miss; released before the blocking ccxt fetch; re-acquired for write-through. No lock held across any I/O.
- Raw ccxt format (list-of-lists from `fetch_ohlcv`, nested dict from `fetch_balance`) is accepted and converted internally using the same logic as `BybitClient.get_ohlcv()`.

### BybitClient (bot/exchange.py)
- `__init__` gains `market_data=None`. When set and `CCBT_SHARED_MARKETDATA=1`, `get_balance()` and `get_ohlcv()` early-return from cache before `_retry`/`_rate_limit` — `_last_request_time` does not advance on a cache hit.
- `get_positions()` is **unchanged** (never cached).
- Flag-off or `market_data=None`: byte-for-byte original behaviour.

### engine.py positions gate (T3)
At the routine positions fetch (engine loop), the gate:
```python
if flag_on and (not self._tracked_trades) and (portfolio_manager.open_count >= max_positions):
    positions = []   # skip — bot holds nothing AND global cap is full
else:
    positions = self._client.get_positions()
```
All non-routine calls (safety/monitor/shutdown paths: lines 563, 576, 649, 1319, 1388, 1674) are untouched.

### Startup warm + stagger (T4, main_multi.py)
When flag is ON:
1. `compute_warm_plan(bots)` produces `{(symbol, tf): limit}` where `limit = max(100, ema_trend*3)` across all bots sharing the same `(symbol, tf)`.
2. Balance warmed once before task creation.
3. OHLCV warmed per unique `(symbol, tf)` pair.
4. `create_task` calls staggered 200ms apart (instead of immediate fan-out).

`main.py` single-bot path is completely unaffected (`market_data=None`).

### deploy/macos/start.sh
`export CCBT_SHARED_MARKETDATA=1` is present (added in T4 commit). The feature is **armed for the next manual restart**.

---

## 3. Review Findings — Status

### Dimension: Correctness & Safety

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| B-1 | BLOCKER | `get_ohlcv` slow path called `.iloc` on a ccxt list-of-lists → `AttributeError` on every real cache miss | **FIXED** in `e7d7244` — list→DataFrame conversion added matching `exchange.py` logic |
| B-2 | BLOCKER (same root) | Cache `get_balance` read `raw['free']['USDT']` vs flag-off `raw['USDT']['free']` | **FIXED** — balance slow path now handles both ccxt nested dict and test-fake flat format |
| M-1 | MINOR | `get_ohlcv` stripped forming candle before returning — `iloc[-2]` in strategy.py would read second-to-last closed candle, stale by one period | **FIXED** — forming candle kept at `iloc[-1]`; only the freshness key uses `index[-2]` |
| M-2 | MINOR | Warm-up symbol normalisation used naive string replace; live read uses `_normalize_symbol()` | **Accepted / low-risk** — for the standard `XYZUSDTformat` the naive transform is equivalent; a canonical helper should be extracted in a follow-on ticket |

### Dimension: Architecture & Flag Parity

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| A-1 | BLOCKER | Same as B-1 — ccxt→DataFrame conversion missing in SharedMarketData | **FIXED** |
| A-2 | BLOCKER | Forming candle stripped → iloc[-2] parity violation | **FIXED** |
| A-3 | MAJOR | End-to-end flag-ON test with real ccxt-shaped fake exchange missing | **FIXED** — `TestRawCcxtExchange` (6 tests) + `TestFlagOnFlagOffFrameParity` (3 tests) added |
| A-4 | MINOR | Docstring for `get_ohlcv` described diverging shapes between branches | **FIXED** as a side-effect of making both branches return identical shapes |

### Dimension: TDD Compliance & Test Quality

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| T-1 | MAJOR | Plan's mandatory deadlock-guard test category missing | **FIXED** — `TestDeadlockGuard` (3 tests): `LockSpyExchange` asserts `lock.locked() == False` inside fetch; timeout test under thread contention |
| T-2 | MINOR | `test_ohlcv_passes_symbol_timeframe_limit` did not assert normalised symbol | Acknowledged; T2 test still weak here — follow-on: tighten assertion to `args[0] == 'BTC/USDT:USDT'` |
| T-3 | MINOR | `test_pm_not_accessed_when_holder` uses plain MagicMock attribute — can't verify short-circuit | Accepted as documentation test; follow-on: use `SimpleNamespace` without `open_count` to prove short-circuit |
| T-4 | MINOR | `test_flag_off_no_shared_market_data_import_unused` tests compute_warm_plan, not async_main flag gate | Accepted; parity for async_main flag-OFF is implicitly covered by T5 parity tests |
| T-5 | MINOR (corrected test) | `test_ohlcv_forming_candle_never_served_as_closed` asserted stripped-frame shape — wrong at authoring | **FIXED** — renamed `test_ohlcv_forming_candle_at_iloc_minus1_closed_at_iloc_minus2`, corrected assertion. Coverage strengthened. |

---

## 4. What Is Left / Follow-On Tickets

These are hardening items, not blockers for the feature to go live:

### P1 — Symbol normalisation helper (MINOR, ~1h)
Extract `BybitClient._normalize_symbol` (or a standalone function) and use it in both the warm loop (`main_multi.py:535-537`) and any direct cache-key construction. Add a test asserting warm key equals engine read key for `1000PEPEUSDT`, `XAGUSDTand a plain `BTCUSDT`.

### P2 — Tighten T2 test assertion (~15min)
`tests/test_exchange_marketdata.py::TestGetOHLCVCacheHit::test_ohlcv_passes_symbol_timeframe_limit`: add `assert call_args[0][0] == 'BTC/USDT:USDT'` to lock in normalised-symbol pass-through.

### P3 — T3 short-circuit test hardening (~20min)
`tests/test_engine_gate.py::test_pm_not_accessed_when_holder`: replace `pm.open_count = 10` with a `SimpleNamespace` object that does NOT have `open_count`, then assert no `AttributeError` is raised — proving the short-circuit path never reads `open_count`.

### P4 — async_main flag-OFF integration test (~30min)
Add test that patches `SharedMarketData.__init__` with a call-counter and verifies it is never instantiated when `CCBT_SHARED_MARKETDATA` is unset.

### P5 — Validate in testnet (next manual restart)
Per plan rollout steps:
1. Restart bot with `start.sh` (flag is already `=1`).
2. Watch logs for `"cache_hit"` entries and absence of `-1003`/`-418` errors.
3. Confirm call volume in logs drops from ~600/tick toward ~50/tick.
4. Verify no missed SL/TP closes after 24h.
5. Rollback = `unset CCBT_SHARED_MARKETDATA` + restart.

---

## 5. Review Verdicts — Final

| Dimension | Original Verdict | Post-Fix Verdict |
|-----------|-----------------|------------------|
| Correctness & Safety | NEEDS_FIX (2 BLOCKERs) | **PASS** — both blockers fixed and regression-tested |
| Architecture & Flag Parity | FAIL (2 BLOCKERs) | **PASS** — parity verified end-to-end with ccxt-shaped fake |
| TDD Compliance & Test Quality | NEEDS_FIX (1 MAJOR) | **PASS** — deadlock guard implemented; 4 minor findings accepted with follow-on tickets |

---

## 6. Final Test Run

```
python3 -m pytest tests/ -q

416 passed, 1 warning in 3.93s
```

Warning: `urllib3 v2 only supports OpenSSL 1.1.1+` — system LibreSSL, harmless, pre-existing.

---

## 7. Activation Note

**The feature is NOT live until someone manually restarts the bot.**

`deploy/macos/start.sh` already contains `export CCBT_SHARED_MARKETDATA=1`.
On the next manual restart via `start.sh`, all 96 bots will use the shared cache.

Pre-restart checklist:
- [ ] `python3 -m pytest tests/ -q` — confirm 416 passed
- [ ] Confirm no open positions at restart time (or accept positions will re-fetch from exchange within first tick)
- [ ] Watch first 5 minutes of logs for `AttributeError` or `api_error` spikes
- [ ] After 1h: confirm `-1003`/`-418` warnings are gone from logs
- [ ] Rollback ready: `unset CCBT_SHARED_MARKETDATA` in `start.sh`, restart

There is no `check-proxy` script in the repo currently — the plan's "run check-proxy" rollout step refers to manually confirming API call volume in bot logs (search for `fetch_ohlcv` / `get_balance` log lines; should drop ~12x in frequency).
