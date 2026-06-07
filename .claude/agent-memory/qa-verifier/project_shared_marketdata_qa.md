---
name: shared-marketdata-qa
description: QA review of the shared market-data cache refactor (bot/engine.py) — the safety blocker and which reads must stay fresh
metadata:
  type: project
---

QA review of `docs/plans/shared-market-data-refactor.md` (caches balance/positions/OHLCV across ~62 bots in `bot/engine.py`). Verdict: HAS_SAFETY_BLOCKER.

**Why:** plan tried to cache `get_positions` at the routine monitor site (engine.py:563) on a 10s TTL, justified by "60s tick reconciles." Both premises are false in the code.

**How to apply (load-bearing facts when reviewing any caching/staleness change to the live core):**
- Close detection is *absence-based*: `check_closed_positions` (engine.py ~75-84) infers an SL/TP close from a position being absent in the positions list. A STALE positions snapshot → ghost position, missed close, blocked re-entry (duplicate-side gate at ~948 keys off `_tracked_trades`), delayed daily-loss breaker, and PortfolioManager global-slot leak (PM only decrements via `register_close`, which is driven by this same detection).
- The loop tick is CANDLE-ALIGNED, not 60s — `_sleep_until_next_candle` (engine.py ~1513). On 1h/4h bots a stale-positions miss reconciles up to 1-4h later, not seconds.
- positions reads that MUST be fresh: 563 (monitor+num_positions→can_trade), 522 (GRACEFUL_STOP exit), 535 (TP_ONLY exit), 608 (startup restore → seeds PM global count). Plan only marked 1278/1347/1633.
- Safe default recommendation: do NOT cache positions at all (or invalidate per-tick); cache only balance (30s) + OHLCV (per candle boundary). OHLCV duplication is the bulk of the fan-out anyway.
- daily-loss breaker reads `risk_mgr.state.daily_pnl` (risk.py:159), NOT cached balance — so balance caching does not weaken the breaker (sizing drift only).
- iloc[-2] (last closed candle) verified correct at signal/trail/restore. OHLCV cache must invalidate on candle boundary, not fetched_at+duration (plan contradicts itself: line 40 vs line 92).

See [[verification_method]] for the general QA approach.
