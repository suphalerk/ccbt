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
_(fill on completion)_
