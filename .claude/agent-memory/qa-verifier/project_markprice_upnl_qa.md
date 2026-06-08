---
name: markprice-upnl-qa
description: QA of realtime uPnL markPrice WS (branch dashboard-realtime-upnl) — R1 resubscribe dead code; R2 prod fix REAL but test still vacuous + monotonic-throttle flake
metadata:
  type: project
---

## Round 2 re-verify (2026-06-08) — PASS-WITH-WARNINGS

Round-1 blocker (symbol-change resubscribe never fires) is **TRULY FIXED in production code**. New design: `_symbol_poll_loop` no longer calls `_reload_positions()`; it read-only queries the DB symbol set, compares to `self._open_symbols` (captured at connect in `_run_once`), and sets `self._needs_reconnect=True`; the `async for` body checks the flag and breaks. I drove the REAL path (live FakeWS + real DB insert) and `_run_once` broke on its own with `_needs_reconnect=True` — verified working.

**Two round-2 findings:**
1. (MAJOR, env-dependent) `test_long_upnl_positive_move` FAILS on this Mac (1 failed / 15 passed). Root cause is a real prod nit: `_maybe_broadcast` throttles via `time.monotonic()` vs initial `_last_broadcast_ts=0.0`. On macOS `monotonic()` is process-relative (starts ~0), so for the FIRST ~1s of the WS client's life every broadcast is suppressed (`now-0.0 < 1.0`). Prod impact ~1s startup suppression (minor); test impact = deterministic fail on macOS (the Mac Mini deploy target). On long-running Linux `monotonic()` is boot-relative/large so the test passes there — hence be's "16/16 pass". Fix: init `_last_broadcast_ts = -BROADCAST_MIN_INTERVAL_S` (or `float("-inf")`), or special-case first broadcast.
2. (MAJOR) The resubscribe test `TestSymbolSetChange` is STILL VACUOUS. It passes even when I gut `_symbol_poll_loop` to a no-op or set `_needs_reconnect=False` — because session-0 FakeWS ends via `StopAsyncIteration` (not the `_needs_reconnect` break) and the test then HAND-CALLS `_run_once()` a 2nd time, which reloads positions regardless. So the test does not actually exercise the fix it claims to. The fix is real (verified manually) but the regression guard is not. Future re-verify: a real guard must let the WS keep yielding and assert `_run_once` RETURNS on its own (no manual 2nd call) driven only by the poll flag.

Mutation-verified clean: long math (500/-1000), short direction sign (flipping → 4 fails), total_upnl sum. Symbol mapping (DB upper ↔ stream lower ↔ msg `s` upper) correct. Real `trades` schema has entry_price/size/side('buy'/'sell')/status('open') — query matches. No bot/ or backtest/ SOURCE touched (diff includes unrelated NEW `tests/test_backtest_*` + `docs/tickets/` from a different base — scope noise, not a violation). snapshot_registry.broadcast shared with ChangeDetector but async-sequential + throttled — upnl does not block the snapshot WS. Web build clean (dist rebuilt), 15/15 upnl vitest pass, upnl is render-only useState (no TanStack cache write). 9 `test_api_ws.py` failures are PRE-EXISTING on base branch (token-injection/env), not this PR.

Round-1 minors still open (non-blocking): docstring line 34 says env `CCBT_USE_TESTNET` (code reads `CCBT_MARKPRICE_TESTNET`); `data.get("p") or data.get("P")` dead `P` fallback.

---

Realtime unrealized-PnL feature (`api/markprice.py`, branch `dashboard-realtime-upnl`, round 1) — CORRECTNESS lens QA.

**Verdict: FAIL (one functional blocker).** uPnL math, public-stream-only (no API key / no REST / no account), graceful degrade, throttle, no bot/backtest touch all verified clean. Web build clean, 178/178 vitest pass, 16/16 pytest pass.

**Blocker — symbol-change resubscribe never fires in production.** `_symbol_poll_loop` calls `_reload_positions()` which mutates BOTH `self._positions` AND sets `self._open_symbols` to the new set. The only reconnect trigger in `_run_once` is `if self._open_symbols != set(self._positions.keys())` — after the poll loop both are equal, so it's always False. Reproduced directly. Effect: a newly-opened position is never subscribed (combined-stream URL is built once at connect) and never appears in the uPnL feed until an UNRELATED socket drop reconnects. Removed positions DO disappear (dropped from `_positions`), so only the add path is broken.
**Why:** the unit test `TestSymbolSetChange` (test_markprice.py:218-248) never calls `_run_once`/`_symbol_poll_loop` — it hand-sets `client._open_symbols = initial_symbols` to fabricate the mismatch, so it passes against a bug the real loop can't reach.
**How to apply:** on re-verify, require a test that drives the real poll→reconnect path (e.g. assert the WS URL is rebuilt / connect is re-invoked after a DB insert). Fix is likely: poll loop should set `_open_symbols` but NOT also re-point `_positions` to the new set before the break check (or compare against a saved `_subscribed_symbols` captured at connect time).

Minor (non-blocking): docstring line 34 says env `CCBT_USE_TESTNET` but code reads `CCBT_MARKPRICE_TESTNET`. `data.get("p") or data.get("P")` fallback to settle-price `P` is unreachable dead code (`p` always present & truthy). Pre-existing 54 jsdom uncaught errors from lightweight-charts in bot-detail.test.tsx are unrelated.
