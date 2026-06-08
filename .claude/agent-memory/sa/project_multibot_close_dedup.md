---
name: multibot-close-dedup
description: Architecture of the shared recently_closed registry for multi-bot netted-position close dedup, and its known structural limits
metadata:
  type: project
---

Multi-bot close-dedup: when N config-bots share ONE netted exchange position per symbol (AXS=7 bots, POL=3 bots), each bot independently detects position absence and would journal+alert separately. Fixed via a shared `recently_closed` dict (keyed by normalised symbol, value=close timestamp, 60s TTL) owned by `PortfolioManager.recently_closed` and threaded into every `TradingEngine` → `check_closed_positions(recently_closed=...)`. First bot to reach the journal write is primary; siblings within TTL skip journal+alert but still remove their local trade_id and call `register_close` (idempotent `set.discard`).

**Why:** before the fix, all 7 AXS bots journaled the same netted close → 7 duplicate DB rows + 7 Telegram alerts + 7x inflation of the portfolio-wide `get_daily_pnl()` (which is NOT symbol-filtered — it `SUM(pnl)` across all trades). This corrupted circuit-breaker accounting at restart since each bot's RiskManager seeds `daily_pnl`/`consecutive_losses` from that global query.

**How to apply:**
- The dedup is lock-free and that is CORRECT: all bots run as asyncio tasks in ONE event loop (`main_multi.py` `asyncio.create_task`/`gather`), and `check_closed_positions` is fully synchronous (no `await` between the `if key in recently_closed` check and the `recently_closed[key]=...` mark) → the check-then-set is atomic under cooperative scheduling. Do NOT add an async lock; do NOT introduce an `await` inside that critical section or the dedup races.
- Registry mark MUST stay deferred to AFTER both `log_trade_close` and `record_trade_result` succeed (commit b22260b). Marking before the journal write poisons the key on SQLite-locked failure: the retry/sibling sees primary=False, skips the journal, and the loss never reaches circuit breakers. Regression: `test_registry_not_poisoned_on_journal_failure`.
- Registry is in-memory, reset to `{}` on restart; `_restore_positions` never touches it and never journals closes → no leak across restart.

**Known STRUCTURAL limit (pre-existing, NOT solved by dedup — do not mistake for a regression):** the 7 same-symbol bots each track DIFFERENT entry/sl/tp (different strategies) but only one netted position exists. Whichever bot wins the dedup race journals ITS OWN entry/sl/tp/PnL — non-deterministic which strategy's numbers get recorded. The dedup only guarantees ONE row per netted close, not that it's the "right" strategy's row. Rooted in exchange netting; see [[backtest-pnl-r-multiple]] for why portfolio PnL attribution is already approximate.

**Exit-price sanity guard** (same fix): `check_closed_positions` rejects any `get_closed_pnl` fill whose price is outside `[entry/5, entry*5]` (`_MAX_EXIT_RATIO=5.0`), falling back to SL/TP estimate. Backstop against cross-symbol fill contamination (POL entry 0.09 matched to an AXS 2.5 fill = garbage -2685% PnL/alert). Note: this is a price backstop, NOT a true symbol filter — matching still trusts ccxt `fetch_my_trades(symbol)` server-side filtering and breaks on the FIRST side+price match in `reversed(recent_trades)`, so same-symbol re-entry/re-close within the window can still mis-match (pre-existing).
