---
name: project_dashboard_round4_frontend_fix
description: Round 4 dashboard-rewrite fixes: SPA catch-all blocker, R-multiple label, avg_pnl heatmap, server-only AI aggregate, sym_clean parity, test contamination
metadata:
  type: project
---

Round 4 (commits c5bb0fa, f2e5d32, 1b15713) landed all fixes. BLOCKER confirmed resolved.

**Status**: All fixes verified. 4 pre-existing inter-test contamination failures remain (not regressions).

## Confirmed Working
- SPA blocker: `GET /assets/index-LbfGwAlX.js` returns `text/javascript` (not `text/html`) — FileResponse with MIME from `mimetypes.guess_type`. Path traversal blocked via `.resolve().relative_to(_dist)`.
- Token injection: `_inject_token` uses `json.dumps(token).replace("</", "<\\/")` — correctly escapes `</script>` in token values.
- `reward_to_avgloss` renders as `"2.50R"` (not `"$2.50"`); column header is "Avg Win/Avg Loss (R)".
- `ExpectancyHeatmap` colours and cell values use `avg_pnl` (not `total_pnl`). `avg_pnl` flows: `get_hour_dow_stats()` → `HeatmapCell` (api/models.py:280) → router → frontend.
- `AIAnalyticsPage` deleted `computeAggStats()` TS fallback; cards show `'unavailable'` when server omits aggregate.
- `get_trade_gate` uses `pnls[pnls <= 0]` for PF loss count to match `_live_stats` parity (zero-pnl = loss in PF).
- `sym_clean` in `bot/mode.py`: no `.upper()` (mirrors `engine.py:410`); regex enforces uppercase so lowercase input raises `ValueError`.
- nginx `/ws` block: `access_log off` prevents `?token=` from appearing in access logs.
- nginx `/v2` rewrite strips prefix before FastAPI proxying (no Vite `base='/v2/'` rebuild needed).
- `.env` inline comment strip in `start-dashboard-v2.sh`: trailing space left on value (e.g. `"8601 "`), but benign: `int("8601 ")` works in Python; token with trailing space is self-consistent across injection→URL→comparison.

## Known Discrepancy (nit)
- `queries.py:1043` comment says "Uses <= 0 for losses" but line 1045 uses `pnls[pnls < 0]` for `strict_losses` (the `reward_to_avgloss` denominator). Intentional: zero-pnl has no loss magnitude for mean-loss calc. Comment is stale/misleading.

## Test Contamination (pre-existing, not introduced by R4)
- `test_spa_asset_serving._get_client()` does `importlib.reload(api.deps)` + `importlib.reload(api.main)`. Creates a new `get_db_path` function object. Routers hold a `Depends()` closure over the OLD object. When `test_trade_gate._get_client()` sets `app.dependency_overrides[new_get_db_path]`, key mismatch → FastAPI bypasses override → real DB used → 4 tests fail when run after spa tests.
- Fix: `test_spa_asset_serving` should mutate `api.deps.CCBT_DASH_TOKEN` directly (as `test_round1_fixes.py::TestSpaCatchAll` does) rather than reloading the module.
- 4 affected tests: all pass in isolation; only fail in combined pytest run ordered spa→trade-gate.

**Why:** The reload pattern was used to propagate env var to module-level constant. Direct mutation is safer.
**How to apply:** Future token/env tests must mutate `api.deps.CCBT_DASH_TOKEN` directly, never `importlib.reload(api.deps)`.
