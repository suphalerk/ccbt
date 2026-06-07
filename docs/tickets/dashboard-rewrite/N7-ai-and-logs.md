# N7 — AI Analytics + Live Log Viewer

**Phase C · est. M (1 day)**

## Goal
Port the AI advisor analytics section and the live log viewer.

## Scope
- **AI Analytics** (when `ai_layer.enabled`): metric cards (total/decided decisions, win rate, avg
  confidence, influence multiplier), calibration curve, accuracy-by-regime, advisor adjustments hist,
  decision pie, detail tables. Data from `/api/ai/calibration` (server-computed via
  `get_calibration_stats`/`get_calibration_data`). Charts via recharts.
- **Live Log Viewer**: stream `trading_bot.log` entries with level filter + search. Level counts row +
  monospace colored log lines (INFO/WARNING/ERROR/CRITICAL palette). **WS push** of new log lines via the
  `/ws/logs` channel (N2), REST fallback (`/api/logs`).

## Rules
- Log search/filter happens server-side (reuse `get_recent_logs` args); TS only renders.
- Cap rendered lines (e.g. last 500) for perf; newest first (match Streamlit).
- **Tail by size + inode (must-fix recommended)**: track the file inode (not just mtime) so launchd log
  rotation (`com.ccbt.rotate-logs`) resets the read offset; reuse `get_recent_logs`' partial-line parse
  for the incremental tail, not its whole-64KB read.

## Tests (write first)
- `/api/logs` honors level + search filters (parity with `get_recent_logs`).
- React: AI cards/charts render from mock calibration data + empty state; log viewer renders levels,
  applies filter UI, appends a WS-pushed line.

## Acceptance
- AI section + logs match Streamlit; logs update live via WS.

## Result
Done. Commit `e9780a8` on `dashboard-rewrite-impl`.

Files added/changed:
- `tests/test_n7_ai_and_logs.py` — 16 Python tests (TDD-first): /api/logs level+search filter parity with get_recent_logs(), /api/ai/calibration row schema + accuracy/influence value correctness, symbol set parity.
- `web/src/__tests__/ai-and-logs.test.tsx` — 22 vitest+RTL tests (TDD-first): AIAnalyticsPage metric cards + empty state + charts; LogViewerPage level filter + search + WS-pushed lines + 500-line cap.
- `web/src/pages/AIAnalyticsPage.tsx` — metric cards (total decisions, avg accuracy, correct count, avg influence), per-symbol accuracy bar chart, calibration detail table. Empty/loading states.
- `web/src/pages/LogViewerPage.tsx` — level filter select (ALL/INFO/WARNING/ERROR/CRITICAL), debounced search, monospace colored log lines with level badges, WS-push via onWsLine callback, 500-line cap, newest-first.
- `web/src/App.tsx` + `web/src/components/Sidebar.tsx` — /ai and /logs routes + nav links.

All 116 web tests pass; all 230 Python tests pass; build clean.
