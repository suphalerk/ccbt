---
name: project_dashboard_round6_post_review
description: Post-fix review of Round 6 residuals — all 3 verified fixed, 163 vitest + 99 Python tests pass, build clean
metadata:
  type: project
---

Round 6 post-fix review completed on 2026-06-07. All 3 residuals confirmed fixed.

**RESIDUAL #1 (WS bots empty):** `useLiveSnapshot.ts` wraps WS bots in `{ bots: [...] }` (BotListResponse shape). `api/ws.py _build_snapshot` now builds full BotRow objects via `get_bot_health() + get_per_bot_summary() + get_open_trades()`. Both match the GET /api/bots shape. 40 bots show in grid after WS snapshot.

**RESIDUAL #2 (nginx /api/ block):** `deploy/nginx-ws-v2.conf` has `location /api/` block proxying to `ccbt_v2_backend` with auth_basic + no Cache-Control immutable. REST calls /api/bots, /api/health, /api/portfolio now route to FastAPI.

**RESIDUAL #3 (uvicorn token leak):** `deploy/macos/start-dashboard-v2.sh` exec line includes `--no-access-log` so ?token= never writes to launchd log.

**Test counts:** 163 vitest (10 files, 27 pre-existing jsdom/lightweight-charts exceptions unchanged). 99 Python tests (test_round2_nits + test_n12_deploy + test_api_ws + test_spa_asset_serving). Build: tsc -b + vite build clean, 0 TS errors.

**Nit found (not a regression):** `useLiveSnapshot.ts` writes portfolio cache typed as `PortfolioSummary` (ws-types.ts) while consumers read `PortfolioSummaryResponse` (api/client.ts openapi codegen). Shapes are structurally identical; tsc accepts it. Safe but worth aligning.

**win_rate parity confirmed:** `get_per_bot_summary` returns win_rate in [0,100] range — both `api/ws.py` and `api/routers/portfolio.py` use `round(wr, 1)` without `*100`. `get_trade_stats` returns win_rate in [0,1] — both multiply by 100 for portfolio summary. Correct parity.

**Why:** Fix was for the "0 bots in grid" production bug where WS snapshot clobbered the TanStack ['bots'] cache with a raw array instead of `{ bots: [...] }`.

**How to apply:** When writing WS snapshot data to TanStack cache, always wrap arrays in the same shape as their REST counterpart. `setQueryData<BotListResponse>(['bots'], { bots: [...] })` not `setQueryData(['bots'], [...])`.
