---
name: reference-backtest-engine-testing
description: Concrete testing techniques used by major open-source backtest engines (freqtrade, backtesting.py, vectorbt, nautilus_trader, backtrader, jesse, LEAN) for simulator correctness: fill ordering, fees, lookahead, invariants, backtest-live parity
metadata:
  type: reference
---

# Backtest Engine Testing Patterns (June 2026 research)

## freqtrade
- tests/optimize/test_backtesting.py, tests/optimize/test_lookahead_analysis.py
- Fill priority (intrabar): stoploss > exit signal > ROI > trailing stop (from strategy/interface.py should_exit)
- ROI is evaluated before trailing_stop so profits are top-capped by ROI if both apply
- Lookahead detection: "lookahead_analysis" CLI command runs chained backtests, pokes strategy to provoke divergent indicators/entries vs full-backtest baseline
- Key test: test_data_with_fee — asserts backtesting.fee == 0.01234 (user config overrides exchange fee mock)
- Key test: test_backtest__enter_trade_futures — asserts pytest.approx(trade.liquidation_price) == 0.081767037
- Key test: test_backtest_dataprovider_analyzed_df — asserts candle_date == current_time to verify no future candle leaked
- Known trap: extremely tight trailing stops on long TFs let backtest "buy low and sell just below the high" — adversarial stance not taken by default on 1H+
- stoploss_on_exchange forced to False in backtesting (test: assert not backtesting.strategy.order_types["stoploss_on_exchange"])

## backtesting.py (kernc)
- tests/_test.py
- SL priority rule: "if you enter with a plain market order, SL has priority" (adversarial, not optimistic)
- Key test: test_sl_always_before_tp — asserts ExitPrice == SL value (105) not TP
- Key test: test_trade_enter_hit_sl_on_same_day — validates SL execution on entry day
- Key test: test_stop_price_between_sl_tp — UserWarning when entry stop between SL and TP
- Same-candle entry+SL/TP: defers to next matching bar, issues warning ("result somewhat dubious")
- Key test: test_trades_dates_match_prices — EURUSD.Close[trades['ExitTime']] == trades['ExitPrice']
- No explicit lookahead test — framework populates full dataframe; user must avoid iloc[-1] pattern

## vectorbt
- tests/test_portfolio.py
- Cash conservation: test_execute_order_nb — rejects when free_cash < 0, status=2
- Cash locking: test_lock_cash — with cash_sharing=True, validates locked reserves across grouped positions
- Slippage: test_slippage — long orders increase price, short decrease; directional correctness
- Fees: test_execute_order_nb — combined fees+fixed_fees+slippage produces specific OrderResult(price=11.0, fees=10.00...)
- Partial fills: test_allow_partial — allow_partial=False rejects; True permits partial; status_info=9 = rejected
- Multi-asset sequencing: test_call_seq — call_seq="auto" dynamically reorders to maximize fills under cash_sharing
- Known bug: Issue #187 — shorts were unlimited (no liquidation check), -256% losses possible. Fixed in later versions.
- Negative cash prevention: checked at order execution time, not position close

## nautilus_trader
- tests/unit_tests/backtest/ (30 test files)
- Monotonic invariant: test_backtest_monotonic.py — assert_monotonic() enforces all event timestamps are non-decreasing
- Commission model: test_commission_model.py — maker/taker fees as % of notional; inverse perpetuals use base currency
- Fill models: test_fill_models.py — 10+ fill model types tested: BestPrice (unlimited), OneTickSlippage, TwoTier, SizeAware (price impact on large orders), Probabilistic, LimitOrderPartial, ThreeTier, MarketHours, VolumeSensitive, CompetitionAware
- Contingency bracket: test_exchange_contingencies.py — whichever order triggers first fills and cancels the other (first-triggered-wins, not SL-first)
- Partial fill bracket: test_partial_fill_bracket_tp_updates_sl_order — TP partially fills, SL qty auto-updates to match remaining position
- Backtest-live parity: test_trade_id_parity.py — Python and Rust implementations produce identical trade IDs (FNV1a hash + counter, bounded to u64)
- Stop-limit: test_exchange_stop_limits.py — initial taker fill at ask/bid; subsequent fills MAKER after trigger
- Cash: test_exchange_cash.py — short selling rejection when exceeding holdings; exact balance assertions after each leg
- Trailing stops: test_exchange_bracket_trailing_stop_orders.py — first-triggered-wins between SL and TP

## backtrader (mementum)
- tests/test_comminfo.py, tests/test_order.py
- Commission: stocks = size*price*commission; futures = size*commission (without price, with multiplier)
- Futures multiplier: pnl = size * (newprice - price) * mult; cash_adjustment = size * (newprice - price) * mult
- Fill mechanisms (fillers.py): FixedSize, FixedBarPerc (% of bar volume), BarPointPerc (proportional to price range)
- All fills constrained by bar volume as upper bound
- Multiframe: test_data_multiframe.py — validates SMA calculations with weekly + daily mixed; chkmin=151 for warmup
- cheat_on_open / cheat_on_close modes exist but no dedicated lookahead test — framework relies on bar boundary semantics

## jesse
- tests/test_simulator_parity.py, test_conflicting_orders.py, test_broker.py, test_completed_trade.py
- Simulator parity: assert fast == full — both simulators must feed strategy identical candles; caught regression where higher-TF candles were aggregated from original not synthetic candles
- Isolated backtest: test_isolated_backtest.py — daily_balance resets between runs (no state leakage), input candles not mutated
- Conflicting orders: average fill prices when multiple entries overlap; (1.1+1.11)/2 for two-fill entry
- Reduce-only orders: capped to remaining position qty (oversized reduce-only uses actual_filled_qty)
- Fee calculation: (1 * 50 + 0.7 * 80 + 0.3 * 40) * 0.002 — fees on each partial fill leg
- Exit price VWAP: (0.7*80 + 0.3*40) / (0.7+0.3) = 68 for multi-fill exits
- Reduce-only submission blocked when position is closed (raises OrderNotAllowed)
- No explicit SL-before-TP priority test found; jesse uses "first-triggered-wins" implicitly

## QuantConnect LEAN
- Tests/Engine/ (AlgorithmManagerTests.cs, PartialFillModel.cs), Brokerages/Backtesting/BacktestingBrokerage.cs
- Fill model architecture: pluggable via security.FillModel.Fill(context) — swap slippage/fee models per brokerage
- Margin pre-check: Portfolio.HasSufficientBuyingPowerForOrder(orders) before any fill execution
- Orders placed on current bar NOT filled until next bar (except market orders) — prevents same-bar lookahead
- Time frontier: algorithm UTC time read-only during backtest, sourced from data feed — prevents future access
- Stop/trailing stop: OnOrderUpdated() fires StopTriggered/StopPrice events; still subject to next-bar fill rule
- Regression suite: hundreds of full-algorithm tests in Algorithm.CSharp/RegressionTests/ — golden fixture results compared
- Reconciliation: live trading has explicit reconciliation vs backtest results (reconciliation docs)
- PartialFillModel: tracks remaining qty per orderId, sets OrderStatus.PartiallyFilled until complete

## Cross-Engine Patterns Summary
1. SL vs TP priority: freqtrade=SL-first; backtesting.py=SL-first (adversarial); nautilus/jesse=first-triggered-wins
2. Same-bar fill: LEAN=next-bar (except market); backtesting.py=next-bar for same-entry-bar SL/TP; freqtrade=current bar with detail TF
3. Lookahead: freqtrade has dedicated CLI tool; LEAN uses time frontier; others rely on bar boundary semantics
4. Cash invariant: vectorbt=pre-fill rejection; nautilus=explicit balance assertions; LEAN=margin pre-check
5. Parity test: nautilus has Python-Rust hash parity; jesse has fast-vs-full simulator parity
6. Fee model: all support pluggable fee functions; maker/taker distinction in nautilus; multiplier in backtrader futures
