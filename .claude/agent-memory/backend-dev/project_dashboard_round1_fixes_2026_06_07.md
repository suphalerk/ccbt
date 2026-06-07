---
name: project-dashboard-round1-fixes-2026-06-07
description: Dashboard rewrite Round 1 fixes — 4 blockers + follow-ups fixed on 2026-06-07
metadata:
  type: project
---

Round 1 of dashboard-rewrite blockers fixed on 2026-06-07 (branch: dashboard-rewrite-impl).

**BLOCKER #2 fixed**: `_build_symbol_config_count` now reads `AUDITED_CONFIGS + FORWARD_TEST_CONFIGS` from `deploy/macos/start.sh` instead of disk-globbing. Exact MIXED set = 12 coins (AXS=4, AAVE/DOT/FIL=3, ATOM/BERA/INJ/IP/ONDO/PIPPIN/PIXEL/SUI=2). Falls back to disk-glob when start.sh absent (test fixtures). ENJ/TRUMP/ARC/ALICE/TON/ZEN correctly single-config.

**BLOCKER #3 fixed**: `get_trade_gate()` now accepts `since: Optional[str]` param; filters `timestamp >= since` before grouping — parity with `forward_test_report._live_stats(symbol, since=...)`. Also moved to `api.classify` (canonical import) instead of `research.forward_test_report.classify`.

**BLOCKER #5 fixed**: `assert_startup_safety()` is now called in the FastAPI lifespan startup (reads `CCBT_DASH_HOST` env, defaults to `127.0.0.1`). Will raise `RuntimeError` if non-loopback + no token.

**BLOCKER #6 fixed**: `/ws` and `/ws/logs` handlers check `?token=` query param against `CCBT_DASH_TOKEN`. Wrong/missing token sends `{type: "error"}` then closes with code 4403.

Follow-ups also applied:
- `control.py` bulk_mode: `sym_clean()` applied to each item before roster check
- `ws.py`: dead `asyncio.Lock` removed from `ConnectionRegistry`
- `candles.py`: `PRAGMA query_only=1` added
- `portfolio.py`: dead equity cumsum fallback dropped (uses `cumulative_pnl` column only)
- `metrics.py`: `risk_pct` → `stop_distance_pct`; `/api/risk` emits `notional + max_sl_loss`
- `portfolio.py`: `ai_calibration` emits `AICalibrationAggregate` (uses `get_calibration_stats()`)
- `main.py`: SPA deep-link catch-all `GET /{full_path:path}` returns `index.html`

**Why:** TDD — all 17 new tests in `tests/test_round1_fixes.py` written first, failed, then fixed. 292 dashboard tests pass after fixes.

**How to apply:** The roster for MIXED attribution is now always sourced from start.sh. If the deployed config list changes, update start.sh — `_build_symbol_config_count` will automatically pick up changes. Do NOT add to `_SKIP_CONFIGS` for new retired coins; simply remove them from start.sh.
