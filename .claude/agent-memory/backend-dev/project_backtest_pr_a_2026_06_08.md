---
name: project-backtest-pr-a-2026-06-08
description: PR-A backtest harness: 77 characterization tests built for backtest/engine.py + metrics + portfolio replay + parity (zero behavior change)
metadata:
  type: project
---

PR-A backtest test harness landed 2026-06-08 (branch: backtest-tests-pr-a).

**Why:** backtest/engine.py (1306 LOC) had zero tests, yet produces every deploy/no-deploy decision. Historically carried the 10x inflation bug. Ticket: docs/tickets/backtest-tests/README.md.

**Files created (tests-only, zero production change):**
- `tests/_bt_fixtures.py` — deterministic OHLCV builder, base_config(), open_position() helper
- `tests/test_backtest_engine.py` — 39 tests: SL/TP, same-candle tiebreak (CURRENT optimistic), gap/liq KNOWN GAPs, slippage direction+time-of-day, commission, funding NOT charged KNOWN GAP, partial+pyramid+SL, sim-time cooldown, force_close, cash conservation
- `tests/test_backtest_metrics.py` — PIN-5 drawdown peak-equity denominator, Sharpe hand-computed (uses .days integer NOT fractional), PF=inf
- `tests/test_portfolio_replay.py` — PIN-1 replay R-multiple compounding, anti-regression 10x inflation guard
- `tests/test_backtest_parity.py` — PIN-11 dispatch parity: BT=38 keys, live=31; orphan allowlist = {adx_di_cross, choppiness_ema, williams_r_adx, roc_momentum, price_channel_vol, ema_alligator, ribbon_rsi_vol}
- `tests/test_backtest_snapshot.py` — golden snapshot on 300-candle alternating bull/bear fixture, all features OFF

**Round 1 result:** 77/77 pass. **Round 2 result (2026-06-08):** Fixed 3 review blockers → 71/71 pass on the 3 core files.

**How to apply:** When writing future backtest tests, use `open_position()` from `_bt_fixtures.py` (NOT raw BacktestTrade construction). Cash conservation: `final = initial - sum(entry_commissions) + sum(trade.pnl)` — NOT naive `initial + sum(pnl)` (entry commissions are charged separately at engine.py:947). For end-to-end run() tests that need real trades, use `make_trading_ohlcv()` + `trading_config()` (relaxed RSI/volume/slope filters). CRITICAL: `above_trend = signal.close > ema_trend_1h` — for LONG entries to be allowed, signal prices must be ABOVE the 1H trend EMA (trend close << signal prices), NOT vice versa.

**Round 2 additions (blockers fixed):**
- `make_trading_ohlcv()` / `trading_config()` in `_bt_fixtures.py` — fixture that produces real EMA crossovers
- 6 vacuous engine tests replaced with non-vacuous versions using new fixture + `len(trades)>=2` guards
- PIN-1 guard: `TestPIN1RunBotPnlFracComputation` — static guard (all BOTS use 1% risk via `build_config`) + behavioral demo (5%-risk pnl_frac = 5x of 1%-risk for same 1R trade → $30 vs $6 on $200 wallet)
- PIN-2/3/4 look-ahead pins: `TestLookAheadPins` — 4 asymmetric `run_backtest()` fixtures

**Next:** PR-B (SL-first tiebreak, engine.py:1000-1017) then PR-C (funding deduction). Both will update EXPECTED_SNAPSHOT.

**KNOWN GAPS documented in tests:**
- Gap-through-SL fills at SL price, not gap-open (optimistic)
- No liquidation modeling at high leverage
- Funding never deducted (PR-C will fix)
- Same-candle tiebreak uses candle body direction (optimistic, PR-B will fix)
