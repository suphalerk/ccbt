---
name: project-dashboard-rewrite-adr
description: Dashboard v2 (api/ + web/) ADR — Python is single source of truth for all financial math; TS formats only. Known adherence gaps from post-build review.
metadata:
  type: project
---

The dashboard rewrite (branch `dashboard-rewrite-impl`, tickets in `docs/tickets/dashboard-rewrite/`) replaces Streamlit with FastAPI (`api/`) + Vite/React SPA (`web/`). ADR in `docs/tickets/dashboard-rewrite/README.md`.

**Why:** Streamlit had a 30s-refresh + maintainability pain; the rewrite buys a real component model + WS live updates. It buys NO trading edge — every metric is computed in Python. The user accepted the full rewrite cost knowingly.

**ADR Decision 1 (load-bearing):** ALL financial/metric math stays in `dashboard/queries.py`. FastAPI routers are thin wrappers; React does ZERO business math. Reason: queries.py had a documented 10x PnL-inflation bug; a TS re-impl = second source of truth = that bug class returns. See [[feedback-backtest-bugs]].

**How to apply (architecture lens for any future dashboard PR):**
- Any aggregation/ratio/cumsum/threshold-ladder in `web/src/*.tsx` is a violation — push it into queries.py and expose via a Pydantic model.
- Routers (`api/routers/*.py`) must call a queries.py function for any number, not re-derive it.
- DB access must be read-only (`mode=ro`, ideally `+ PRAGMA query_only=1`); never call the exchange; WS detector must stay autocommit (no long-lived read txn — pins WAL, starves checkpoints with ~60 writers).

**Adherence gaps — status after fix round 2 (2026-06-07):**
1. [PARTIAL] `api/routers/portfolio.py:340-348` — portfolio AGGREGATE now from `get_calibration_stats` (canonical), but per-symbol influence ladder STILL recomputed inline in the router. Push the per-symbol ladder into a queries.py fn.
2. [MITIGATED] `AIAnalyticsPage.tsx` now prefers server `aggregate` (live path); `computeAggStats` (TS weighted-avg, lines 85-106) kept as fallback + `correctCount` always TS-derived (line 231). Strict ADR still wants the fallback gone.
3. [OPEN] `web/src/components/NewPanels.tsx:84,152` still computes close-reason pct (count/total*100) in TS; `CloseReasonItem` model (api/models.py:201-205) still has no `pct`. Low risk (a percentage, not PnL) but a TS-math leak.
4. [FIXED] `assert_startup_safety` now called at startup (api/main.py:44); `verify_token` attached to write endpoints (control.py:107,150); start-dashboard-v2.sh aborts (exit 1) on non-localhost w/o token.
5. [STILL OPEN — MAJOR] `bot/mode.py:sym_clean` added + adopted on API side (deps.py re-exports it), but engine inline derivations NOT migrated: engine.py:410 (read path via _symbol_clean→read_bot_mode:520) + engine.py:1593 (heartbeat) still `.replace('/','').replace(':','')` with NO `.upper()`. `write_bot_mode`/`read_bot_mode`/`_mode_path` use the arg verbatim → lowercase config: dashboard writes `mode_BTCUSDT.json`, engine reads `mode_btcusdt.json` → PANIC fails open. Engine already imports from bot.mode; fix = use sym_clean at :410 and :1593. Also queries.py:860,1021 inline-derive (internally consistent, no upper, fine).
6. [FIXED] SPA catch-all `/{full_path:path}` at main.py:258 returns index.html before StaticFiles mount. Minor: catch-all also swallows unknown `/api/*` → returns HTML 200 not 404 JSON; add `if full_path.startswith(('api/','ws'))` guard.
7. [DEFERRED-ACCEPTED] `bot_ohlcv` producer — endpoint ships available=false, chart hidden. Per HARD RULES (live engine edit).

**Verified fixed in code on real DB (trades.db, 277 closed trades):** BLOCKER #3 since= parity (queries.py:980 pandas filter == CLI SQL `timestamp>=since`; ISO-8601 lexicographic order makes date-only `since` correct) — endpoint vs CLI: 0 PF/trade-count mismatches across 40 symbols. config_count propagation (queries.py:1031) correct (AAVE=3/AXS=4 → MIXED). real_r (queries.py:1041) = alias of reward_to_avgloss, non-null when losses exist. WS detector RO+autocommit+query_only+in_transaction self-heal (ws.py:160-207). No DB writes anywhere in api/.

Bot/Streamlit untouched (additive constraint OK). N11 (engine close_reason taxonomy) intentionally skipped pending human go-ahead; donut colours by pnl-sign as interim.
