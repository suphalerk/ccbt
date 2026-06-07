---
name: project_dashboard_round6_frontend_fix
description: Round 6 frontend fix: WS snapshot bots cache shape mismatch — portfolio grid showed 0 while sidebar showed 40
metadata:
  type: project
---

Round 6 fix: RESIDUAL #1 — portfolio bot-grid shows 0 while sidebar shows 40.

**Root cause**: `useLiveSnapshot` called `queryClient.setQueryData(['bots'], rawBotsArray)` — setting the cache to a raw `BotRow[]` array. But `PortfolioPage` and `Sidebar` both read `data?.bots` (expecting `BotListResponse` shape `{bots:[...]}`) — `.bots` on a raw array is `undefined`, so `bots = []` → grid showed 0. The Sidebar showed 40 because it had been hydrated by the REST call before the WS snapshot arrived, making the bug appear inconsistent.

**Fix (frontend, `web/src/hooks/useLiveSnapshot.ts`)**: Wrap WS bots in `{ bots: [...] }` when calling `setQueryData` for `QUERY_KEYS.bots`, matching `BotListResponse` shape. Imported `BotListResponse` from `../api/client`.

**Fix (backend, `api/ws.py` `_build_snapshot`)**: Now calls `get_bot_health()` + `get_per_bot_summary()` + `get_open_trades()` and builds full `BotRow` objects (strategy, status, mode, position_side etc.), matching the shape from `GET /api/bots`. Previously only sent 4-field objects which degraded the grid display even if the shape bug was fixed.

**Test (non-mocked vitest)**: Added in `web/src/__tests__/round1-frontend-fixes.test.tsx` — seeds `['bots']` with 40 REST-shaped bots, delivers the real py-shaped WS envelope, asserts `data?.bots.length === 40` after the snapshot (not 0).

**Why:** The TanStack Query cache key `['bots']` is shared by `api.listBots()` (returns `{bots:[...]}`) and the WS hydration path. The hook must always write the same shape the REST call writes — any divergence silently breaks `.bots` access.

**How to apply:** When `useLiveSnapshot` (or any future WS handler) writes to a TanStack Query cache key, the written value must exactly match the shape of the corresponding REST response. Use type-safe `setQueryData<BotListResponse>` to enforce this at compile time.
