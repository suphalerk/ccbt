---
name: external-close-pnl-qa
description: QA of fix-external-close-pnl branch (check_closed_positions sanity guard + multi-bot dedup) — found dedup-key-poisoning blocker
metadata:
  type: project
---

Branch `fix-external-close-pnl` (commits 08a6622/ee0e8bf/fb35aeb) fixes two real bugs in `bot/engine.py check_closed_positions()`: (1) exit-price ratio guard `_MAX_EXIT_RATIO=5.0` rejecting cross-symbol contaminated fills (POL 0.09 matched vs AXS 2.5); (2) multi-bot dedup via shared `recently_closed` dict on `PortfolioManager` (one journal+alert per netted close).

**Why:** several config-bots share ONE netted exchange position per symbol (AXS=7 bots) — each independently detected absence and journaled/alerted separately; and get_closed_pnl occasionally returns other-symbol fills causing -2685% garbage PnL.

**BLOCKER found (not caught by their TDD suite):** dedup key is marked at `recently_closed[norm_symbol]=time()` BEFORE the journal write. If journal raises (SQLite locked — the exact prod scenario the pre-existing crash-safe retry guards), primary `continue`s (trade kept open, good) but key is now POISONED. Sibling bots + the primary's own retry then see the key → `is_primary_closer=False` → skip journal AND `risk_mgr.record_trade_result` entirely → position closes with ZERO risk accounting (consecutive-loss / daily-loss circuit breaker silently loses the trade). Regresses the crash-safe contract. Fix: move the `recently_closed[key]=...` marking to AFTER successful journal+risk accounting.

**How to apply:** when re-verifying, demand a test that sets `journal.log_trade_close.side_effect=lock` WITH a shared `recently_closed` dict and asserts a sibling bot OR a retry still journals+records exactly once. Confirmed via repro: total `record_trade_result`=0 across the netted close when first writer hits a transient lock.

Verified clean: ratio guard never false-rejects legit ATR-based SL/TP (always <<5x entry); dedup is GIL-atomic (no await between check-and-set); restart-safe (`recently_closed` is a fresh empty dict per process, restore unaffected); decision-invariant surfaces (SL/TP placement, cancel_all_orders, _restore_positions, register_open) untouched; cooldown `last_trade_close` set for both primary+secondary. 9 new tests pass (7 fail pre-fix as claimed); 172 engine/close/risk tests green. Only suite failure is env-only fastapi import (pre-exists). See [[verification_method]].

**ROUND 2 (commit b22260b) — BLOCKER TRULY FIXED, PASS.** The premature mark in the else-branch is removed; `recently_closed[key]=time()` now runs only AFTER both `log_trade_close` and `record_trade_result` succeed (engine.py ~375). Reproduced the original poisoning by reintroducing the early mark — regression test `test_registry_not_poisoned_on_journal_failure` then FAILS (`recently_closed` left with `AXSUSDT` after a locked journal); reverting the fix makes it pass. The regression test is genuine and behavioral. Implausible-guard rejection path verified: a 5x+ fill is skipped (`continue`), `exit_price` stays None, code falls to the per-symbol ticker fallback (engine.py ~274) which journals a BOUNDED SL/TP PnL — no garbage. All 61 close-path tests green (test_close_phantom_guard + fallback + taxonomy + external_close). 

**Architectural note (PRE-EXISTING, out of scope, do NOT block this PR):** `_restore_positions` reads `journal.get_open_trades()` which is NOT symbol-filtered, and the restore loop matches only on `trade_side` vs exchange position side — no `db_trade["symbol"]==config["symbol"]` check (engine.py ~899-905). On restart this can restore the same/cross-symbol DB trade into multiple same-coin bots, which is precisely what generates the multi-bot detection + cross-symbol contamination the dedup and ratio-guard defend against. The duplicate-coin gate (`can_open`: `symbol in _open_coins`) prevents two bots being open on one coin live, so the contamination is restart-driven. Worth a follow-up: filter restore by symbol.
