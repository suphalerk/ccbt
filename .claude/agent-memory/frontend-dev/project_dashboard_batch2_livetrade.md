---
name: project_dashboard_batch2_livetrade
description: Batch 2 live-trade context: SL/TP/R:R in uPnL panel (#3) + Today's PnL/Notional header cards (#5)
metadata:
  type: project
---

Batch 2 dashboard UI shipped on commit a29e364 (branch ui-batch2-livetrade).

**Why:** Live-trade operators need to see R:R remaining and dist-to-stop on open positions without opening Binance, and Today's PnL without digging into the daily-pnl bar chart.

**How to apply:** All financial math is server-side (no TS math rule). When touching markprice or portfolio summary, keep these computed fields up to date.

## #3 — SL/TP + R:R + dist-to-stop in uPnL panel

- `api/markprice.py`: `_query_open_positions` now SELECTs `stop_loss, take_profit` (was just symbol/side/entry/size)
- `PositionMark` has `stop_loss`, `take_profit` slots; `_compute_derived(mark)` computes:
  - `dist_to_stop_pct = abs(mark - SL) / mark * 100` (null when SL None/0)
  - `rr_remaining = abs(TP - mark) / abs(mark - SL)` (null when SL or TP None/0, or denom=0)
- `to_dict()` emits `stop_loss`, `take_profit`, `dist_to_stop_pct`, `rr_remaining`
- `web/src/ws-types.ts`: `PositionMark` extended with 4 nullable fields
- `web/src/components/UpnlPanel.tsx`: 4 new columns (SL, TP, Dist%, R:R), '—' for null
- `tests/test_markprice.py`: 4 new pin tests (long, short, SL=0, SL=null) — class `TestDistAndRR`

## #5 — Today's PnL + Open/Notional header cards

- `api/models.py`: `PortfolioSummaryResponse` gains `notional: float = 0.0`
- `api/routers/portfolio.py`: `portfolio_summary` computes `notional = Σ abs(entry*size)` for open trades
- `web/src/api-types.d.ts`: `PortfolioSummaryResponse.notional?: number` added
- `web/src/pages/PortfolioPage.tsx`: `PortfolioHeader` now accepts `dailyDays` prop and summary.notional; renders `header-today-pnl` (sign-colored) and `header-open-notional` cards; grid expanded to 6 cols

## Tests
- Python: 22 passed (test_markprice.py) with `.venv-dash`
- Vitest: 218 passed (13 files) including new `batch2-livetrade.test.tsx` (14 tests)
- Build: clean (tsc + vite)
