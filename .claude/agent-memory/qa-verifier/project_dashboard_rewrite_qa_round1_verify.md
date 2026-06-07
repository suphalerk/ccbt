---
name: dashboard-rewrite-qa-round1-verify
description: Round-1 post-fix re-verify of dashboard-rewrite — residual #1 (WS clobber) PASS; pre-existing conditional-hook in BotGrid; parity test only compares symbol sets
metadata:
  type: project
---

Dashboard-rewrite branch, round-1 post-fix re-verification (2026-06-07).

**Residual #1 (portfolio bot-grid showed 0 vs sidebar 40) — TRULY FIXED.**
- Root cause was frontend: `useLiveSnapshot` wrote a raw `BotRow[]` into the `['bots']` TanStack key, so every consumer reading `data?.bots` got undefined.
- Fix (useLiveSnapshot.ts:116): wraps as `{ bots: [...] }` matching `BotListResponse`. Backend `api/ws.py _build_snapshot()` now emits the full BotRow shape via get_per_bot_summary + get_bot_health.
- Both `PortfolioPage` and `Sidebar` read the SAME `['bots']` key + shape, so the fix keeps them in sync (pre-fix, BOTH were broken — the report's "sidebar showed 40" claim is the only inconsistency, smoke confirms both work now).
- Initial-snapshot path (api/main.py:167) reuses the same `_build_snapshot()`; the no-detector fallback sends `{data:{}}` which the FE handles gracefully (no clobber).
- Non-mocked vitest test asserts cache stays 40 after WS snapshot. 15 round1 fe tests + 24 portfolio/App tests + 73 dashboard python tests all green in `.venv-dash`.

**Why:** verifying the QA lens that the WS snapshot bots payload matches /api/bots and no longer clobbers the cache.
**How to apply:** residual #1 is closed. Two leftover items below are NOT blockers for it.

Leftover (not regressions from this work):
1. `BotGrid` (PortfolioPage.tsx:167) has an early `return` BEFORE its `useMemo` (line 179) — conditional hook / Rules-of-Hooks violation. PRE-EXISTING since N4 (commit 810cfe3), not introduced by round-1 fixes. Latent: a 0->N bots transition changes hook count.
2. Python parity test `test_ws_snapshot_bots_equal_rest_bots_for_same_db` only compares symbol SETS + key-presence, NOT field VALUES — weaker than the be report's "matches GET /api/bots exactly" claim. Smoke covered the value match live.

Tooling: dashboard python tests need `.venv-dash` (fastapi not in system python3). Frontend: `cd web && npx vitest run`.
