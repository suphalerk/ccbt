# T2 — Trade-Gate Table (live audit gate per bot)

**Tier 1 · Phase 1 · est. M (1 day)**

## Why
This is the highest-value steal from the external dashboard: a per-strategy table with a PASS/FAIL
gate. For us, **symbol == strategy** (1 coin runs 1 deployed strategy). It operationalizes our
existing audit gate (trades ≥ 15, PF ≥ 1.3) **live on screen**, so we can see at a glance which bots
have earned their slot, which are marginal, and which should be cut — instead of running the CLI.

## Scope
A sortable table: one row per symbol with live closed-trade stats + a verdict column. Reuse the
classifier already proven in `research/forward_test_report.py::classify` (do NOT reinvent the
thresholds — import it) so the dashboard and the CLI agree.

## Files
- `dashboard/queries.py` → `get_trade_gate(db_path=None, min_trades=15, graduate_pf=1.3) -> pd.DataFrame`
  - per symbol over `status='closed'` excl. `orphan_reconcile`:
    `symbol, trades, win_rate, profit_factor, total_pnl, avg_pnl, expectancy_r`
    where `expectancy_r` = mean(pnl) / mean(abs(pnl of losers)) (None/NaN-safe; the per-symbol
    R-multiple proxy — clean attribution, never the shared-wallet ratio).
  - add `verdict` via `from research.forward_test_report import classify`
    (`classify(trades, pf, min_trades, graduate_pf)` → KEEP_TESTING / READY_TO_AUDIT / MARGINAL / DROP).
  - sorted: DROP first (needs attention), then MARGINAL, then by total_pnl desc.
- `dashboard/components.py` → `trade_gate_table(gate_df) -> pd.DataFrame` (format: WR%, PF (inf-safe),
  PnL `$+/-`, verdict). Color/emoji per verdict is fine via a text column (Streamlit dataframe).
- `dashboard/app.py` → new "Trade Gate" section in portfolio view, above/near "Trade History
  Summary". Add a small legend (gate = ≥15 trades & PF≥1.3).

## Tests (write first) — `tests/test_dashboard_trade_gate.py`
- `get_trade_gate` on seeded DB: counts/WR/PF/expectancy correct; orphan excluded; PF-inf handled.
- verdict column matches `classify(...)` for crafted rows (under-gate → KEEP_TESTING; ≥15 & PF1.5 →
  READY_TO_AUDIT; ≥15 & PF0.8 → DROP; ≥15 & PF1.1 → MARGINAL).
- `import` of `classify` works from dashboard context (no circular import / path issue).

## Acceptance
- Dashboard verdicts are identical to `python research/forward_test_report.py` for the same symbols.
- Renders with empty DB (empty table, no crash).

## Notes / risk
- `research/` import from `dashboard/` — verify sys.path (run via `streamlit run` from repo root).
  If fragile, lift `classify` into a shared util both import, rather than duplicating thresholds.

## Result
_(fill on completion)_
