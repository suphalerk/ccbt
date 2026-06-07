# N13 — Parity Verification & Cutover

**Phase D · est. M (1 day) · do last**

## Goal
Prove the Vite/React dashboard is CORRECT (not merely "equal to Streamlit") before retiring Streamlit.

## Scope
- **Correctness-anchored parity (must-fix recommended)** — Streamlit and the new API both consume
  `queries.py`, and pre-N1-fix that function was wrong, so "equals Streamlit" is NOT the oracle. Compare
  **API JSON vs the (fixed) `queries.py` functions in-process**, panel by panel: header metrics, bot grid
  + modes, bot overview, trade history summary, recent trades, equity curve, per-bot PnL, daily PnL,
  single-bot stats/log/risk, AI analytics, log viewer, + the 5 new panels (N10).
- Known, accepted degradations (note, NOT parity-blockers): `signal_source` dropped (absent from schema);
  exit markers placed at `timestamp+duration_seconds` (Streamlit's entry-time placement is wrong);
  candles from persisted data (no exchange call).
- **Playwright smokes** (reserve for what unit tests can't): portfolio loads + live data; drilldown loads;
  a mode change posts + reflects; PANIC confirm dialog blocks an accidental click; WS reconnect after a
  server bounce.
- **Soak**: run `com.ccbt.dashboard-v2` (distinct port, N12) in parallel with the old Streamlit on 8501;
  diff numbers over a short window. (No 8501 collision — that's why v2 uses its own port.)
- **Cutover (only after sign-off)**: atomically `launchctl unload` Streamlit / `load` v2; mark
  `dashboard/app.py` deprecated (KEEP `dashboard/queries.py` — now shared with the API — and the
  `components.py` colour tokens). Update CLAUDE.md (dashboard section), docs, and mark
  `docs/tickets/dashboard-upgrade/` superseded. Do NOT delete Streamlit for a grace period.

## Acceptance
- API-vs-queries.py diff is zero across all panels; Playwright smokes green; user sign-off; Streamlit
  retired (not deleted); `pytest tests/ -q` + `npm test` green; one dashboard service on the canonical port.

## Result
_(fill on completion)_
