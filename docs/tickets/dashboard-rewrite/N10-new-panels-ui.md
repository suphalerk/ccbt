# N10 — New Panels UI

**Phase C · est. M (1 day) · chart libs = recharts + custom CSS grids (defer nivo)**

## Goal
Render the N9 metrics as React panels.

## Scope
1. **CloseReasonDonut** — donut of exit mix (`/api/close-reasons`); label `reason · n · pct%`.
   **🔴 must-fix #6: colour by the SIGN of each reason's `total_pnl`, NOT by the reason string** — until
   N11, 39/192 `sl` rows are actually profitable trail exits, so an all-`sl`-red donut would lie ("80%
   stop-loss"). Green when a reason's net PnL is positive, red when negative. Both portfolio + single-bot.
2. **TradeGateTable** — `/api/trade-gate`; columns WR%, PF (inf-safe), PnL, `reward_to_avgloss` (labelled
   a $-ratio, not R), verdict. **Lead with "N of M symbols meet the 15-trade min"** and grey sub-threshold
   rows (must-fix #4). **Verdict vocabulary = the real `classify()` set** `{KEEP_TESTING, READY_TO_AUDIT,
   MARGINAL, DROP}` **+ `MIXED`** for multi-config symbols (must-fix #3 — never the made-up "KEEP/DROP").
   Sort DROP→MARGINAL→KEEP_TESTING→READY_TO_AUDIT→MIXED, then PnL desc. Legend: "gate (READY_TO_AUDIT) =
   ≥15 trades & PF≥graduate_pf & single-config symbol; MIXED = multiple configs share the netted position".
3. **RiskAtStakeHeader** — `/api/risk`; "$ max SL loss" big number + caption `N open · M unprotected ·
   $notional`; `unprotected>0` flagged red. **Shows absolute $ only (no % gauge) until the bot persists a
   real balance** (see N9 "Ships degraded") — never the `*50` hack.
4. **MonthlyCalendar** — `/api/calendar`; month grid (**custom CSS grid**, not nivo); cell colour by
   daily_pnl sign + `WR n%`; month selector. Labeled **Diagnostic**.
5. **ExpectancyHeatmap** — `/api/heatmap`; 7×buckets (**custom CSS grid**); colour by avg_pnl **only where
   trades ≥ 20** (must-fix #4 — at 242 trades min_samples=5 sits at the per-cell mean), else grey + show
   count; bucket-size selector (4h default). Header literally: "Diagnostic — not a trading rule.
   Low-sample cells are masked." Nothing consumes it as a gate.

## Rules
- Presentational only; all numbers from API. Masks/colours derived from per-row/per-cell counts the API
  already returns (N9). No business math in TS.
- Live-update on WS snapshot where cheap (donut/gate/risk); calendar/heatmap refetch on interval.

## Tests (write first) — vitest + RTL
- donut: a reason with net-positive PnL renders GREEN even if named `sl` (asserts must-fix #6);
- gate table: MIXED for a multi-config symbol; all five classify verdicts render; sub-threshold rows greyed;
  "N of M meet min" header; sort order DROP→MARGINAL→KEEP_TESTING→READY_TO_AUDIT→MIXED;
- risk header flags unprotected; `%` hidden when no balance;
- calendar: populated + empty month; heatmap: cells with count<20 are grey/uncolored + diagnostic text present.

## Acceptance
- Five panels render live; donut colours by PnL sign; gate shows MIXED + sample header; heatmap mask ≥20
  with per-cell counts + diagnostic label.

## Result
_(fill on completion)_
