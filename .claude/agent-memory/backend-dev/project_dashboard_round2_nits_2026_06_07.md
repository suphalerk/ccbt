---
name: project-dashboard-round2-nits-2026-06-07
description: Dashboard rewrite Round 2 NITs fixed on 2026-06-07 — segment-exact SPA guard, Cache-Control, WS parity tests
metadata:
  type: project
---

Round 2 NIT fixes applied on 2026-06-07 (branch: dashboard-rewrite-impl, commit cccac1e).

**NIT #1 fixed — segment-exact SPA guard** (`api/main.py:297`):
Old: `full_path.startswith("api") or full_path.startswith("ws")` wrongly caught SPA routes like `/apikeys` and `/wstools`, returning JSON 404 instead of index.html.
Fix: tightened to `== "api"`, `startswith("api/")`, `== "ws"`, `startswith("ws/")`.

**NIT #2 fixed — spa_catch_all return annotation** (`api/main.py:284`):
Changed `-> HTMLResponse` to `-> Response` (the function also returns JSONResponse and FileResponse).

**NIT #3 fixed — Cache-Control: no-store on injected index.html** (`api/main.py:317`):
Added `headers={"Cache-Control": "no-store"}` to the HTMLResponse for index.html. Without this, browsers cache the HTML (with embedded token) indefinitely — a token rotation leaves the cached page using a revoked token for WS connections.

**WS snapshot parity verified** (`api/ws.py _build_snapshot()`):
The `_build_snapshot()` was already fixed in the prior frontend commit to call `get_bot_health() + get_per_bot_summary() + get_open_trades()` and emit full BotRow objects. Parity tests added to lock this in.

**New tests** (`tests/test_round2_nits.py` — 13 tests, all pass):
- `TestSpaCatchAllSegmentGuard` — /apikeys → HTML (200), /wstools → HTML (200), /api/x → JSON 404, /ws/x → JSON 404
- `TestIndexHtmlCacheControl` — GET / and SPA deep-links have `Cache-Control: no-store`
- `TestWsSnapshotBotsParity` — _build_snapshot bots == REST bots for same DB, full shape, correct envelope, empty-DB safety

**Why:** TDD — tests written alongside the fixes. These are NITs from the Round 1 review but affect real browser behaviour (token caching + SPA routing).

**How to apply:** When adding new SPA routes that begin with "api" or "ws" (e.g. /api-keys, /ws-monitor), the guard will NOT block them — only `/api/` prefix and exact `/api` segment are gated. [[project-dashboard-round1-fixes-2026-06-07]]
