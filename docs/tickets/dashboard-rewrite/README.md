# Dashboard Rewrite — Vite + React SPA + FastAPI + WebSocket (Ticket Series)

> Replaces the Streamlit dashboard (`dashboard/app.py`) with a **Vite + React SPA** fed by a thin
> **FastAPI + WebSocket** service. Goal: maintainable component model + real-time updates.
> Supersedes `docs/tickets/dashboard-upgrade/` (Streamlit). Reviewed by a 5-lens workflow
> (architect/frontend/trader/qa/devops) on 2026-06-07 — verdict **GO-with-changes**; all must-fixes
> below are baked into the tickets.

## 🏗️ Build status (2026-06-07 — ✅ MERGED to main `claude/crypto-trading-bot-1xlt7` via `c89330a`; NOT yet cutover)
- **Implemented**: all 13 tickets coded TDD + committed (N11 deferred). `api/` (FastAPI+WS) + `web/`
  (Vite SPA, builds to `web/dist`) created; `.venv-dash` on Homebrew python3.12; `dashboard/queries.py`
  shared by Streamlit + the API. **Live bot untouched** (0-byte diff to engine/strategy/risk/main_multi/
  start.sh — verified every round).
- **Healed**: 5 fix→test→re-review rounds. The original 6 post-build blockers (WS hydration no-op,
  trade-gate attribution/window, dow labels, dead startup guard, unauth /ws) + the SPA asset-shadow
  blocker are fixed and **runtime-verified (not mocked)**.
- **Screenshot-verified** (run against the real `trades.db`): Portfolio, Analytics Panels, Bot Detail,
  Live Logs, AI Analytics all render with real data; must-fixes visibly working (PnL-sign donut,
  "N of M meet 15-trade min", R-multiple column, candles graceful-degrade to "unavailable").
- **Found by the real-server run (tests missed it)**: `/api/bots` 500 — pandas Series truth-value in
  `list_bots` health lookup — FIXED (commit `8def892`).
- **Remaining residuals (small, non-engine — being closed via a fix workflow)**:
  1. Portfolio bot-grid shows 0 while the sidebar shows 40 (WS initial snapshot likely clobbers the
     `['bots']` query cache on first load).
  2. `nginx-ws-v2.conf` has no root-level `location /api/` block (SPA REST calls 404 behind the /v2 proxy).
  3. `start-dashboard-v2.sh` uvicorn line lacks `--no-access-log` → the WS `?token=` is logged to disk.
  + nits: over-broad `startswith('api')` SPA guard, `-> HTMLResponse` annotation, token Cache-Control.
- **Deferred (need explicit go-ahead — touch the live engine)**: N11 close_reason enrichment; the
  `bot_ohlcv` candle producer (consumer ships `available=false` + chart hidden).
- **Run locally**: `.venv-dash/bin/python -m uvicorn api.main:app --host 127.0.0.1 --port 8610` then open
  `http://127.0.0.1:8610/`.
- **Merged 2026-06-07** (merge commit `c89330a`, 37 commits, 296 dashboard pytest + 163 web tests green,
  live bot 0-byte diff). Cutover (N12/N13: load `com.ccbt.dashboard-v2`, soak, retire Streamlit) is still
  pending and gated. Deferred engine items (N11, bot_ohlcv producer) still need explicit go-ahead.

## ⚖️ Honest cost note (architect lens — read before starting)
This is a single-user, localhost-bound dashboard reading **242 closed trades** on a Mac Mini. The
rewrite buys **maintainability + UX (real component model, real-time WS)** — it does **not** buy any
trading edge; every analytical metric is computed in Python (`queries.py`) regardless of frontend.
The 30s-refresh pain alone was solvable in Streamlit (`st.fragment(run_every=)`). The user has chosen
the full rewrite with eyes open. The highest-ROI, frontend-agnostic work (N1 orphan fix, N9 metrics,
N11 close_reason) is sequenced **first** so value lands even if the React phase slips.

## Architecture Decision Record (ADR)
**Decision 1 — keep ALL data/financial logic in Python. FastAPI is the single source of truth; React does ZERO business math.**
Reason: R-multiple/PF/orphan-exclusion/drawdown is tested in `dashboard/queries.py` and had a
documented 10x inflation bug. A TS re-impl = a second source of truth = that bug class returns.

**Decision 2 — frontend = Vite + React SPA (NOT Next.js).**
Reason (unanimous across reviewers): the prod artifact is a static CSR bundle served by FastAPI either
way. Next.js `output:'export'` disables every Next.js advantage (SSR/RSC/route handlers/middleware) and
adds App-Router static-export footguns (`/bots/:symbol` can't pre-render an open symbol set). Vite gives
the identical artifact with smaller deps, faster builds, and client routing with no footguns.

**Decision 3 — WS live updates via SQLite `PRAGMA data_version` polling (verified safe).**
A FastAPI background task on a process-lifetime **read-only / query_only** connection polls
`data_version` (+ heartbeat/mode mtimes + log size·inode) every `CCBT_DASH_POLL_S` (default 3s) and
broadcasts deltas. Verified: `data_version` increments cross-connection on every committed write under
WAL (live `journal_mode=wal`); `mode=ro/query_only=1` reject writes → **zero blast radius on the bot**.
The connection must stay in **autocommit between ticks** (never hold an open read txn — a long-lived
reader pins the WAL and starves checkpoints with ~60 writers).

```
trades.db (SQLite WAL)  +  data/mode_*.json  +  data/heartbeat_*  +  trading_bot.log
        │  (read-only: mode=ro, query_only=1)            │ (mtime/size+inode watch)
        ▼
   FastAPI  (api/ — imports dashboard/queries.py + a shared classify util)
     ├─ REST  /api/...          snapshot JSON (Pydantic models; money rounded server-side)
     ├─ WS    /ws (snapshots) + /ws/logs (high-freq tail, backpressured)
     └─ POST  /api/bots/{symbol}/mode   bot control (auth + roster allowlist; PANIC debounce)
        │  HTTP + WS (JSON)
        ▼
   Vite + React SPA  (web/ — TanStack Query + openapi-typescript client + useLiveSnapshot WS hook + Tailwind)
        │  npm run build → web/dist  (static)
        ▼  served by FastAPI StaticFiles on ONE port
```

## 🔴 Must-fix list (from review — embedded in the named tickets)
1. **N8 (security)** — `bot/mode.py:_mode_path` has zero sanitization (path traversal). Factor a single
   canonical `sym_clean(symbol)` into `bot/mode.py`; **engine AND api both call it** (engine currently
   uses `config['symbol'].replace('/','').replace(':','')` at engine.py:410/1593). Validate `{symbol}`
   against an exact-match roster allowlist (`^[A-Z0-9]{2,20}$`) BEFORE building any path. Do NOT port
   app.py's bulk derivation (`.replace('USDT','')+'USDT'`, app.py:410-429) — it's a divergent string.
   *(Note: configs store plain `1000BONKUSDT`, so it doesn't misfire today — but it's a latent trap and
   the API's `{symbol}` param makes sanitization mandatory.)*
2. **N1 (correctness)** — `get_per_bot_summary` (queries.py:727) lacks the orphan exclusion every other
   function has → 35 orphan rows (pnl=0) leak as losses, deflating WR/PF **on the current dashboard too**.
   Fix the function + add `read_only/query_only` to `_get_connection`. TDD: orphan-exclusion test +
   cross-function invariant (per-bot total == Σ get_closed_trades per symbol).
3. **N9 trade-gate (attribution)** — `classify()` requires a FREE symbol (one strategy per symbol).
   **12 deployed coins run multiple configs on one netted position** (axs=4; aave/dot/fil=3; +8 run 2:
   atom,bera,inj,ip,ondo,pippin,pixel,sui). Gate-verdict ONLY symbols owned by exactly one deployed
   config; multi-config → `MIXED`. **Count must be COMPUTED from config files / the start.sh roster at
   runtime — NOT from `get_bot_statuses` (it dedupes to one row per symbol_clean) and never hardcoded.**
4. **N9 sample size** — only **3 of 48 symbols clear 15 trades**; **242** closed (not 277). Lead the gate
   panel with "N of M meet the 15-trade min", grey sub-threshold rows; heatmap colour threshold ≥20 (or
   coarsen to day-of-week only) and always print per-cell counts.
5. **N6 candles (never-interfere)** — do NOT call the exchange from the dashboard (rate-limit budget,
   fixed-IP SOCKS proxy, sync-ccxt blocks the event loop). Read **persisted** OHLCV (have the bot write
   what it already fetches) or drop live candles. Same persistence gives the % risk gauge a real balance.
6. **N10/N11 (mislabel)** — `close_reason` is proximity-inferred (engine.py:126) and trailing mutates
   `info['sl']` in place → **39/192 `sl` rows are actually profitable trail exits**. Until N11 lands,
   colour the donut by **total_pnl sign per reason**, not by the reason string. N11 tests assert label
   CORRECTNESS (a positive-pnl stop is NEVER `stop_loss`), not just decision-invariance.
7. **N2 (WAL safety)** — persistent RO connection must stay autocommit between ticks; explicit complete
   watched-source list {data_version, heartbeat mtimes, mode mtimes, log size+inode}; graceful retry if
   DB not yet in WAL at startup.
8. **N0/N12 (deps/runtime)** — `requirements.txt` has only streamlit; add `fastapi`, `uvicorn[standard]`,
   `pydantic>=2`, `httpx`. System Python is **3.9.6** (Dockerfile uses 3.11) — pin the venv to 3.10+ or
   avoid `X|None` syntax. Commit a static `openapi.json` via an export script for reproducible codegen.
9. **N12 (service collision)** — `com.ccbt.dashboard.plist` + `start-dashboard.sh` **already exist on
   port 8501** (Streamlit). Use `com.ccbt.dashboard-v2` on a **distinct port** during the soak; atomic
   unload-old/load-new only at N13 cutover; fail-fast if the port is bound.
10. **N8 (secrets/exposure)** — `start-dashboard.sh` sources `.env` wholesale (`set -a`), exposing
    MAINNET/ANTHROPIC/OANDA/Telegram keys to a read-only dashboard. Allowlist-export only `CCBT_DASH_*`
    + `BOT_DATA_DIR`; explicitly **unset `CCBT_SOCKS_PROXY`** (dashboard must never egress to the
    exchange). Require `CCBT_DASH_TOKEN`; startup assertion: refuse to start if host≠127.0.0.1 and token
    unset (behind nginx the localhost peer-check is vacuous). Server-side PANIC debounce (rapid 2nd → 429).

## 🟡 Recommended (also folded in)
- Lift `classify()` into a shared util imported by both `research/forward_test_report.py` and the API
  (kills the path-fragile cross-package import; add an import smoke test in N0).
- Introduce `reward_to_avgloss` (a $-ratio, NOT a risk-normalized R given variable AI sizing 0.09–709) —
  there is no existing `expectancy_r` to rename. OPTIONAL: a real per-trade R column
  `mean(pnl/(abs(entry-stop_loss)*size))` (stop_loss populated on all 242 closed rows, 0 NULLs). Return
  null/"—" for zero-loss symbols; suppress for symbols with <10 trades.
- Persist last-known balance + recent OHLCV from the bot to a small DB table → real % risk gauge + N6 candles.
- UTC bucketing: use `strftime('%w','utc',ts)` / `DATE(ts,'utc')` (SQLite strips offset otherwise); add a
  NON-UTC fixture (e.g. `T02:00+07:00`) — guards the OANDA/gold path. Today all rows are +00:00 so the
  +00:00-only test gives false confidence.
- WS: separate `/ws` (low-freq snapshots) from `/ws/logs` (high-freq tail) or add per-client backpressure
  so a slow log consumer can't stall trade snapshots. Define the WS envelope as Pydantic models and
  generate WS TS types too (OpenAPI doesn't cover WS).
- nginx: `deploy/nginx.conf` already has `Upgrade/Connection` headers — the work is a dedicated `/ws`
  location with `proxy_read_timeout 0` + its own rate-limit zone (not "lacks WS upgrade").
- Drop `signal_source` from N4/N6 column lists (absent from schema). Place exit markers at
  `timestamp + duration_seconds` (both columns exist), not Streamlit's known-wrong entry-time placement.

## Hard constraints (every ticket)
- **TDD**: failing test first; never edit a correct test to make code pass (project rule).
  Python: pytest + FastAPI `TestClient`. Web: vitest + RTL; Playwright smokes in N13.
- **No business/financial math in TypeScript** — TS only formats & renders.
- **Data hygiene**: exclude `close_reason='orphan_reconcile'`; closed-only for performance; ISO-8601 tz
  timestamps bucketed by UTC; div-by-zero guarded; PF `inf`-safe; R-multiple per-symbol attribution.
- **Never interfere with the bot**: DB access is `mode=ro, query_only=1`; never call the exchange; never
  hold a write lock or a long-lived read txn.
- **Security**: state-changing POSTs auth-gated + localhost-bound; PANIC behind a confirm dialog + debounce.
- **Additive**: new top-level `api/` and `web/`; do NOT break the bot or the existing Streamlit app until
  the N13 cutover. Keep both dashboards running in parallel during the soak.

## Execution order (workflow picks up sequentially — review-approved order)
**Phase A — frontend-agnostic value FIRST (lands even if React slips):**
1. `N1-fastapi-rest.md` **§prereq**: fix `get_per_bot_summary` orphan exclusion + `read_only` connections (queries.py).
2. `N9-new-metrics-data.md`: metric functions in queries.py (attribution guards + sample masks + real R).
3. `N11-close-reason-taxonomy.md`: engine close_reason enrichment (explicit go-ahead before the engine edit;
   an unattended runner SKIPS this — see N11).
   *(N1's orphan fix and N11's enrichment land in the existing Streamlit dashboard automatically. N9's new
   functions would each need a new Streamlit panel that no ticket scopes — so N9 ships with the React UI
   (N10), not "for free" in Streamlit.)*

**Phase B — backend service:**
4. `N0-scaffold-and-contract.md` (deps, pinned Python, shared classify util, committed openapi.json).
5. `N1-fastapi-rest.md` (REST over the now-fixed queries.py; correctness-anchored parity tests).
6. `N2-websocket-live.md` (change-detector; autocommit-between-ticks; complete source list).
7. `N8-bot-control-auth.md` (EARLY — security-critical; N12 depends on its env model).

**Phase C — Vite SPA:**
8. `N3-vite-scaffold.md` → 9. `N4-portfolio-page.md` + `N5-charts.md` → 10. `N6-single-bot-page.md`
   → 11. `N7-ai-and-logs.md` → 12. `N10-new-panels-ui.md`.

**Phase D — deploy & cutover:**
13. `N12-deploy.md` (dashboard-v2 on a distinct port) → 14. `N13-parity-cutover.md`
   (correctness-anchored parity + Playwright smokes; atomic cutover; retire Streamlit).

## Definition of Done (per ticket)
- Tests added + passing (`pytest tests/ -q`; `npm test`/vitest). Existing suites stay green.
- Endpoint/page renders against empty DB (no crash) and real DB.
- `## Result` note appended; commit references ticket id (e.g. `dashboard-rewrite N1: REST + orphan fix`).
