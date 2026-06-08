---
name: external-close-pnl-review-2026-06-08
description: Round-2 money-safety review of fix-external-close-pnl branch — registry-poisoning fix correct; sanity bound is price-ratio not PnL; dedup semantics correct for shared netted positions
metadata:
  type: project
---

Reviewed branch `fix-external-close-pnl` (commit b22260b) — `check_closed_positions` in bot/engine.py. LENS = money/reporting safety.

**Verdict:** Fix is correct on all primary axes. APPROVED with minor follow-ups. No blockers.

Key facts established (verify before relying on later — code may drift):
- Sanity guard is `_MAX_EXIT_RATIO=5.0` on **price ratio** (exit/entry), engine.py ~246. Correctly leverage-independent: a real high-lev move to SL/TP keeps price ratio near 1.0. Rejected fill → fallback at ~274 journals sl_pnl/tp_pnl from bot's OWN configured SL/TP → inherently bounded → -2685%-type garbage impossible.
- Registry-poisoning fix: `recently_closed[key]` marked ONLY at ~375-376, after BOTH log_trade_close AND record_trade_result succeed. Journal failure → continue at ~365 with registry unmarked → retry/sibling reclaims primary. Regression test test_external_close_pnl.py:412 reproduces-before/passes-after.
- Dedup keyed on norm_symbol. Secondary bots remove trade_id then `continue` at ~392 before telemetry → no dup DB row, no dup alert. Skips record_trade_result too.
- **Why single record_trade_result is CORRECT (not a regression):** config-bots sharing a netted position are ONE position (PortfolioManager.open_count counts by unique coin). Pre-fix N bots each recording full netted PnL was N-fold OVER-count on shared wallet — the dedup fixes that. Recording once is right.
- recently_closed lives on PortfolioManager (main_multi.py), passed to every TradingEngine. RiskManagers are per-bot (engine.py:660), NOT shared.

Residual (minor/nit, recorded as findings):
1. 60s TTL could drop a REAL second close if same symbol round-trips <60s — but shortest deployed TF is 15m (loop sleeps to candle boundary), so theoretical only. Becomes real if a sub-1m config is ever deployed.
2. 5x ratio bound has a blind spot: cross-symbol contamination <5x apart passes and journals real-but-wrong PnL. No independent PnL-magnitude clamp as defense-in-depth.
3. Secondary bots' own consecutive_losses/daily_pnl don't tick — acceptable because portfolio cap gates by coin and primary's halt stops new entries on that coin.
