---
name: dashboard-rewrite-qa
description: Dashboard-rewrite (Vite SPA + FastAPI api/) Round 1 fix verification — 6 blockers, which truly fixed and which passing-but-wrong
metadata:
  type: project
---

Dashboard-rewrite branch (`dashboard-rewrite-impl`, base 5d0905c): old Streamlit dashboard replaced by `api/` (FastAPI) + `web/` (Vite React SPA). Live-bot files (bot/engine.py, main_multi.py, deploy/macos/start.sh) untouched.

**Round 1 fix verification (2026-06-07):**
- BLOCKER #1 (WS nested envelope) — FIXED. Producer `api/ws.py:_build_snapshot` emits `{type,ts,data:{portfolio,bots}}`; `web/.../useLiveSnapshot.ts:89` reads nested with flat fallback. Real-envelope test in `round1-frontend-fixes.test.tsx:99`.
- BLOCKER #2 (roster from start.sh not glob → 12 MIXED) — FIXED. `dashboard/queries.py:_build_symbol_config_count` parses AUDITED_CONFIGS+FORWARD_TEST_CONFIGS from start.sh. Verified 12 MIXED exactly (AXS=4, AAVE/DOT/FIL=3, +8 at 2). ENJ/TRUMP/ARC/ALICE/TON/ZEN correctly single.
- BLOCKER #3 (gate window == CLI) — **ONLY HALF-FIXED**. `get_trade_gate` gained a `since=` param, BUT the production endpoint `api/routers/metrics.py:103` calls it WITHOUT `since`. CLI (`forward_test_report.py`) uses `since=manifest['added']`. Manifest added=2026-06-07, all 277 trades predate it → CLI reports 0 trades/symbol, API reports all-time. Parity test `test_n13_parity.py:748` compares endpoint-vs-function (both unfiltered), NOT endpoint-vs-CLI → passing-but-wrong. Also metrics.py:114/119 read `config_count`/`real_r` keys the gate never emits (always 1/null).
- BLOCKER #4 (dow Mon=0) — FIXED. Producer `queries.py:1223` uses `dt.dayofweek` (Mon=0); `NewPanels.tsx:602 DOW_LABEL_MAP` maps 0→Mon.
- BLOCKER #5 (startup guard) — FIXED. `api/main.py:42-44` lifespan reads CCBT_DASH_HOST, calls `assert_startup_safety` (deps.py:122) which raises RuntimeError on non-loopback+no-token. start-dashboard-v2.sh aborts exit 1 + exports token.
- BLOCKER #6 (/ws auth) — FIXED. `/ws` + `/ws/logs` check `?token=` → error+close 4403 (main.py:155,215). nginx-ws-v2.conf /ws block has auth_basic.

**New issues found (not new regressions, pre-existing or minor):**
- SPA catch-all `GET /{full_path:path}` (main.py:258) returns index.html (HTTP 200 text/html) for UNKNOWN /api/* GET paths instead of 404 JSON. Verified: `GET /api/does-not-exist` → 200 html. Should exclude /api/ and /ws prefixes.
- Roster divergence: `api/deps.py:get_roster` (disk-glob, 85 symbols) vs gate (start.sh, 42 deployed). 43 phantom non-deployed symbols mode-controllable via control API. Two duplicate `_SKIP_CONFIGS` (deps.py + queries.py). Pre-existing (get_roster from N8), not unified by BLOCKER #2.

Tests: 17 round1 + 210 core API + 84 misc all pass on .venv-dash. No regression vs prior passing set.

**Round 2 fix verification (2026-06-07):**
- BLOCKER #3 (since= parity) — **NOW GENUINELY FIXED**. `api/routers/metrics.py:108-118` loads cohort manifest, passes `since=manifest['added']` to `get_trade_gate`. `dashboard/queries.py:980-981` applies `closed[closed["timestamp"] >= since]`. Verified byte-for-byte parity with CLI `_live_stats` (forward_test_report.py:36 `timestamp >= ?`): same orphan exclusion (get_closed_trades:136), same PF math, same lexicographic TEXT compare (DB ts "%Y-%m-%d %H:%M:%S" vs bare-date since; boundary included identically). Parity tests now use since-filtered oracle (no longer passing-but-wrong).
- config_count (BUG 2) FIXED — queries.py:1031 emits key; metrics.py:129 reads it. Verified 12 MIXED from real start.sh roster (AXS=4, AAVE/DOT/FIL=3, +8 at 2).
- real_r FIXED — queries.py:1041 aliases reward_to_avgloss; non-null tests genuine.
- 354 dashboard tests pass, no regression.

**STILL-OPEN issues carried/new (NOT Round-2 regressions):**
- SPA catch-all (main.py:258) STILL leaks: unknown `/api/*` GET → 200 text/html (index.html) not 404 JSON. Carried from Round 1, unaddressed. Minor.
- NEW found Round 2: frontend WS hook `useLiveSnapshot.ts:40` builds `//${host}/ws` with NO `?token=`. Backend WS auth (main.py:155) closes 4403 when CCBT_DASH_TOKEN set (mandatory non-localhost per startup guard). → live snapshot + /ws/logs silently fail in any tokened/prod deploy. Inverse of BLOCKER #6 (backend gate added, frontend never wired to satisfy it). test_api_ws.py has ZERO token refs — auth path untested. Major (prod live dashboard dead) but not a backtest/PnL risk.

**Round 3 fix verification (2026-06-07):**
- WS token chain — **GENUINELY FIXED end-to-end (verified non-mocked in subprocess).** (1) buildWsUrl (useLiveSnapshot.ts) appends `?token=encodeURIComponent(window.__CCBT_TOKEN__)`; real frontend test renders hook + asserts MockWebSocket URL. (2) Backend `spa_catch_all` (main.py:283) switched FileResponse→HTMLResponse, `_inject_token` (main.py:264) injects `<script>window.__CCBT_TOKEN__=json.dumps(token)</script>` before `</head>` on ALL html routes (/, /portfolio, /index.html confirmed). StaticFiles mount at line 301 is SHADOWED by the catch-all (registered first), so injection is real. (3) WS gate `_check_ws_token` (main.py:126) re-imports CCBT_DASH_TOKEN at call time → no-token=accept+error-envelope+close 4403, wrong-token=4403, good-token=accept (subprocess-verified). start-dashboard-v2.sh:129 exports CCBT_DASH_TOKEN to uvicorn; nginx-ws-v2.conf two-gate (auth_basic outer + ?token= inner, named-upstream proxy_pass preserves query). devops "still_open" token-wiring note is OVER-CONSERVATIVE — runtime injection resolves it.
- BLOCKER #3 since= regression (panel emptied when all trades predate manifest) — FIXED via cohort-scoping. queries.py:996-1005 OR-mask: cohort rows filtered by since=, non-cohort keep all. metrics.py:120-134 extracts cohort_symbols from manifest candidates[].coin. n_total>0 verified. BUT parity test (test_n13_parity.py:791) now passes SAME (since=,cohort_symbols=) to both endpoint AND oracle → tests endpoint==function, NOT function==CLI. Original BLOCKER #3 intent (gate==CLI) no longer asserted; by design dashboard now also shows non-cohort all-history symbols the CLI never reports.
- 12-MIXED: real start.sh roster path (_build_symbol_config_count) STILL yields exactly 12 (AXS=4,AAVE/DOT/FIL=3,+8). BUT the round-3 `test_mixed_symbols_present_in_pre_manifest_db` MONKEYPATCHES _build_symbol_config_count with a synthetic 12-symbol dict — the "==12" assertion there is synthetic, not real-roster. Real-roster 12 verified separately by me.
- BLOCKER #4 dow=0→Mon — still FIXED (queries.py:1256 dt.dayofweek, NewPanels.tsx:612 DOW_LABEL_MAP 0→Mon). MonthlyCalendar's separate DOW_LABELS Sun=0 is JS getDay() grid, unrelated.
- BLOCKER #5 startup guard — still FIXED (deps.py:122 raises on non-loopback+no-token; lifespan main.py:44 fires it — subprocess test on host=0.0.0.0+no-token raised RuntimeError; start script exit 1).
- bot/mode.py CHANGED (+37, sym_clean helper, commit be8f159 N8). ADD-ONLY, NOT called by live engine (engine.py imports only BotMode/read_bot_mode). Only api/ (deps.py, control.py) calls it; control.py:70 catches ValueError→400. No live-bot regression. (test report's "bot/engine.py untouched" claim true but it OMITTED checking bot/mode.py.)
- SPA catch-all leak ESCALATED: unknown `/api/*` and `/ws/*` GET → 200 text/html (index.html) not 404 JSON. Now ALSO embeds the token (HTMLResponse change). Token already in GET / behind same nginx gate, so no NEW auth bypass, but API-contract bug persists (mistyped /api returns HTML 200, no JSON 404). Carried Round 1→2→3, still unaddressed. Major for API correctness.
- Tests: 554 dashboard-scope pass (0 fail), 158 frontend pass. No regression vs prior set. 9 collection errors + forex/playwright fails all pre-existing env (ccxt/anthropic/oandapyV20 missing, no live server).
