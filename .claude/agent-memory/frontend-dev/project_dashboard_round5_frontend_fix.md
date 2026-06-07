---
name: project_dashboard_round5_frontend_fix
description: Round 5 post-fix review: SPA asset MIME confirmed correct, honesty/security verified, minor findings logged
metadata:
  type: project
---

Both Round 5 blockers resolved in commit 300104c. Post-fix review completed (round 5 code review pass).

**Blocker 1 — importlib.reload() poisoning Depends() identity — VERIFIED:**
- `tests/test_spa_asset_serving.py` uses `_token_ctx()` context manager with direct `api.deps.CCBT_DASH_TOKEN` mutation (never `importlib.reload()`).
- `TestZNoReloadPoisonGuard` verifies token is None after all SPA tests AND `POST /api/bots/*/mode` returns 200 in combined run.
- Combined `test_spa_asset_serving + test_api_control` = 51 passed, 0 failed.
- `verify_token` in `api/deps.py` reads via `LOAD_GLOBAL` from module dict — mutation propagates correctly (verified with Python introspection).

**Blocker 2 — nginx /v2 blank SPA (root-absolute Vite asset URLs 404) — VERIFIED:**
- `deploy/nginx-ws-v2.conf` has `location /assets/`, `location = /favicon.svg`, `location = /icons.svg` with `auth_basic` + `proxy_pass http://ccbt_v2_backend`.
- `mimetypes.guess_type('.js')` returns `text/javascript` — GET /assets/*.js served correctly.
- `TestNginxDeploymentTopology` (7 tests, all pass). `zone=dashboard` referenced in snippet is declared in base `nginx.conf` — not a bug.
- 12/12 SPA tests pass, 44/44 nginx topology tests pass.

**Honesty follow-ups VERIFIED:**
- `reward_to_avgloss` renders as `"2.50R"` (not `"$2.50"`) — `NewPanels.tsx:298`.
- Heatmap colours by `avg_pnl` not `total_pnl` — `NewPanels.tsx:655-660`.
- `AIAnalyticsPage` uses `data.aggregate` from server only.
- 162/162 vitest tests pass.

**Minor findings (not blockers):**
- `api/main.py:284` — `spa_catch_all` annotated `-> HTMLResponse` but returns `JSONResponse` on two paths (lines 298, 325). Type lie.
- `_get_client()` in test file has convention-only safety (no-cleanup when token=None); `TestZNoReloadPoisonGuard` is the backstop, not a preventative guard.
- `index.html` read from disk on every SPA navigation request — negligible for dashboard traffic.

**Why:** Vite default `base="/"` emits root-absolute URLs; browser fetches `/assets/...` at root bypassing `/v2` rewrite.
**How to apply:** Any future Vite `base=` change must be cross-checked against nginx location blocks. New dimensionless ratio metrics must use `R` or `%` suffix, never `$`.
