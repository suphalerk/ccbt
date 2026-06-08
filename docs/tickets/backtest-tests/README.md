# Backtest Engine — Test Plan (+ 2 honesty fixes)

> `backtest/engine.py` (1306 LOC) + `backtest/metrics.py` + the portfolio REPLAY have ZERO tests, yet they
> produce every deploy/no-deploy decision and historically carried the 10x-inflation bug class. This plan
> adds a real regression suite (OSS-informed) plus two behavior fixes that make backtests honest.
> Researched + reviewed 2026-06-08 (freqtrade, backtesting.py, vectorbt, nautilus_trader, backtrader, jesse,
> LEAN). Review verdict: **direction + all 3 decisions APPROVED**; this revision bakes in the must-fixes.

## ✅ Decisions (user, 2026-06-08)
1. **Funding = FIX** — deduct real funding while held + pin. (engine.py:214 merges fundingRate, charges nowhere.)
2. **7 orphan signals = PARITY TEST + ALLOWLIST** (do NOT wire to live). Verified EXACT: BT dispatches 38 keys,
   live 31, BT−live = the 7 (adx_di_cross, choppiness_ema, williams_r_adx, roc_momentum, price_channel_vol,
   ema_alligator, ribbon_rsi_vol), live−BT = ∅ (safe direction).
3. **Same-candle SL+TP tiebreak = SL-FIRST conservative** (replaces the optimistic candle-body guess at
   engine.py:1000-1017). Invalidates past backtest numbers → sequenced portfolio re-run (Downstream).

## 🚚 Split into 3 PRs (characterization-test discipline — review must-fix #6)
- **PR-A — harness + characterization (ZERO behavior change)**: build the harness, pin TODAY's behavior
  (candle-body tiebreak, funding-NOT-charged), capture the **golden snapshot of CURRENT numbers**. This
  proves the harness catches regressions and gives a clean "before" baseline. Land + verify first.
- **PR-B — SL-first flip (decision 3)**: change engine.py:1000-1017 to SL-first, intentionally regenerate the
  snapshot, trigger the portfolio re-run (SL-first leg).
- **PR-C — funding deduction (decision 1)**: add funding accumulation + deduction, own pin + snapshot regen,
  funding leg of the re-run.
Each PR is independently reviewed. Do NOT bundle behavior changes inside a "just tests" PR.

## 🧪 Test FILES (review must-fix #1 — the replay is in scope)
- `tests/_bt_fixtures.py` — deterministic OHLCV builder + config builder + ONE `open_position(engine, ...)`
  helper that builds the ~15-field `BacktestTrade` + seeds state (so a field rename touches one place).
- `tests/test_backtest_engine.py` — fill model / entry / exit / partial / pyramid / cooldown / regime.
- `tests/test_backtest_metrics.py` — drawdown, PF, Sharpe, R-multiple.
- **`tests/test_portfolio_replay.py`** — **the actual 10x-inflation site**: `research/portfolio_backtest_v2.py:348`
  `pnl_frac = t.pnl/initial_balance` then :396 `dollar_pnl = balance*pnl_frac` — correct ONLY under uniform-1%
  risk, breaks when bots differ (BTC 5% vs alt 1%). PIN-1 lives HERE, not in the engine.
CI gate: `.venv-dash/bin/python -m pytest tests/test_backtest_engine.py tests/test_backtest_metrics.py
tests/test_portfolio_replay.py tests/test_backtest_parity.py tests/test_backtest_snapshot.py -v`
— these files green in .venv-dash. (NOTE: the 43 pre-existing failures = 38 forex_exchange + 5 api_ws
env gaps, out of scope — "one green env" ≠ `pytest tests/` all green.)

## 🧰 Harness rules (review must-fix #5 + recommended)
- **White-box** (`_check_exit`/`_close_position` on hand-built Series) ONLY for fill MATH: PIN-6/7/8/10. Keep
  direct `_close_position` calls to <~6 cases via the `open_position()` helper.
- **End-to-end `run_backtest()`** (hand-built DataFrame, config-driven fees, no network/keys) for everything
  behavioral, ESPECIALLY all look-ahead pins (PIN-2/3/4/9) — a structural `signal_row is prev_row` assertion
  PASSES while wrong if a var is renamed; the pin must use an **asymmetric two-candle fixture** (signal-candle
  fails the condition, exec-candle would pass) and assert NO position opens.
- **Golden snapshot**: pin the SIMPLEST deterministic config (single ema_crossover; partial/pyramid/MTD/
  adaptive/scorer/regime-adaptive-exit all OFF) so it churns only on core fill/PnL changes. `rel=1e-6`.
- Invariants: **cash-conservation reconciled against `self.state.balance`** (review missing-test) — NOT a
  naive `Σ trade.pnl` (partial PnL is booked separately at engine.py:1048 while trade.pnl stores the COMBINED
  total at 1273/1280, so summing double-counts).

## 📋 Test categories (each tied to the real fill model; cite file:line in tests)
1. **Intrabar SL/TP** — long `low<=SL`/`high>=TP`; same-candle both-touched ⇒ **SL-first** (PR-B); positive
   run() pin that no trade exits on the candle it entered (entry checks exit at top of candle i, enters at
   close → entry candle's own h/l never evaluated, engine.py:271,377). *(backtesting.py.)*
2. **Gap-through-SL fill PRICE** *(review missing-test)* — candle OPENS beyond SL → assert exit fills at the
   WORSE gap-open, not the literal `pos.stop_loss` (engine.py:1007). Decide: adopt gap-fill, or pin current
   optimistic behavior as a documented KNOWN LIMITATION.
3. **Liquidation at 25-30x** *(review missing-test — biggest YOLO divergence)* — a position whose adverse
   excursion crosses maintenance margin before its ATR-SL loses ~100% of margin, not 1R. Either model it
   (close at liq price / full-margin loss) OR pin the current no-liq behavior as a KNOWN GAP so high-leverage
   backtests aren't trusted as realistic.
4. **Slippage** — direction (long entry UP / short DOWN) + time-of-day factor (weekend 2.0/Asia 1.0/EU 0.8/
   US 0.7, engine.py:159-184) on entry+partial+full. Note: fixed-fraction, size-independent (optimistic for
   large YOLO size) — pin so it isn't mistaken for depth-aware. *(vectorbt.)*
5. **Commission** — entry, full exit, partial TP1, every pyramid add. *(backtrader.)*
6. **Funding (PR-C)** — formula below; multi-settlement, size-changing, both-sides hand-computed pin.
7. **Gold/forex funding no-op** *(review missing-test)* — XAUUSD has no funding file → deduction is exactly 0,
   no raise; a mismatched-but-present file (xagusdt) is never auto-applied to another instrument.
8. **Dispatch parity (decision 2)** — see "Parity" below.
9. **Metrics** — drawdown ÷ peak EQUITY (initial+peak_cum_pnl, metrics.py:84-91), PF `inf` no-losses,
   **Sharpe = PER-TRADE R-Sharpe** (returns=`trades['pnl_pct']`, ×√(trades/yr), metrics.py:66-77) pinned
   against an INDEPENDENTLY hand-computed value on a 3-4 trade fixture (NOT by calling the same code).
   *(Calendar-daily Sharpe is a possible future engine-change #4, out of scope here.)*
10. **Partial + pyramid + SL INTERACTION** *(review missing-test)* — one combined-sequence fixture: enter →
    partial-TP1 (size halves, SL→BE+buffer at 1056-1064) → pyramid add (weighted-avg entry on reduced size,
    ~1147, SL ratchets up-only) → SL; assert final `total_pnl` == independently hand-computed (each leg with
    own commission+slippage). Plus pyramid-SL-never-loosens.
11. **Sim-time cooldown** — candle-count (after_sl vs after_close) + risk-mgr time cooldown fed the SIMULATED
    timestamp (engine.py:846); **run-twice determinism with `flexible_cooldown.enabled=true` near-threshold**
    (review missing-test — a wall-clock/RNG leak can hide in the re-score override 319-330/818-843).
12. **force_close** *(review missing-test)* — engine.py:1291 end-of-backtest cleanup every run hits: closes at
    `row['close']` with commission+slippage(+funding), only on an open position.

## 📌 Regression pins (1 per documented historical bug; behavioral unless noted)
PIN-1 **replay R-multiple** `pnl/(engine_bal×ACTUAL_risk)×1%` ≠ `pnl/initial_balance` (test_portfolio_replay) ·
PIN-2 no entry from exec candle (asymmetric fixture; + a dedicated body_dominance Round-3 fixture) · PIN-3
trend_filter `shift(1)` (data.py:1254, behavioral) · PIN-4 regime per-row no-future · PIN-5 drawdown ÷ peak
equity · PIN-6 partial commission + SL→BE (white-box) · PIN-7 SL+TP **SL-first** (PR-B) · PIN-8 pyramid SL
up-only (white-box) · PIN-9 sim-time run-twice incl. flexible-cooldown · PIN-10 funding charged = hand-computed
multi-settlement size-changing both-sides (PR-C) · PIN-11 dispatch parity exact.

## 💸 Funding formula (review must-fix #3 — write before TDD)
`funding = pos.size(NOTIONAL) × Σ(settled_rate at each 8h boundary crossed in (entry, exit]) × (+1 long / −1 short)`.
- The merged column is **ffill per candle** (data.py:1200) → a `.sum()` over 15m rows over-charges ~32x. Tally
  ONLY at 8h-settlement candles, on the **then-current** size (size changes at partial-TP 1053 & pyramid 1147).
- Accumulate each settlement-candle inside `_check_exit`, store on the trade (new field), subtract in
  `_close_position` (which today receives no rate/accumulator). Long pays when rate>0, short receives.
- **No-op** when the column is absent / instrument is forex; never auto-apply a different instrument's file.

## 🔑 Parity test (review must-fix #4)
Derive the live key-set from a **SINGLE source of truth** across ALL THREE live dispatch sites (legacy/row-only
block + `new_signals` list strategy.py:2522 + the late standalone `rsi_divergence` strategy.py:2615 &
`squeeze_release` :2656). A naive `new_signals`-only scan falsely flags those two as orphans → a wrong 9-item
allowlist. **Best fix**: expose one `SUPPORTED_SIGNALS` registry both live + backtest import. Assert
`backtest_keys − live_keys == frozenset(the 7)` EXACTLY and `live_keys − backtest_keys == ∅`. Allowlist may
only SHRINK (members removed by wiring live), never grow. Cross-reference `scripts/check_config_timeframes.py`.
The 7 are live-DEAD: enabling one in a deployed config = silent zero-trades.

## 🔁 Downstream — sequenced re-run (review must-fix #7)
After PR-B/PR-C: re-run `research/portfolio_backtest_v2.py`/`final_backtest.py` as **baseline → +SL-first only →
+funding only → both**, regenerating ALL per-bot trade R-series **from the patched engine (NO cache reuse**, or
fill models silently mix). Record the three deltas in docs/portfolio.md. SL-first is **non-uniform pessimism**
(hits wide-TP/4H bots near the PF~1.2 floor hardest) → add a per-bot same-candle-both-touched frequency
diagnostic and **re-audit any bot whose pass/fail flips**. (Separately, from the project review: taker fees +
higher slippage re-baseline + multiple-testing discount — not this ticket.)

## Effort
PR-A ~1 day (harness + characterization + snapshot + replay pin). PR-B ~0.5 day + re-run. PR-C ~1 day
(funding is the fiddly one) + re-run. ~45-60 tests total.

## Open (defaulted unless you say otherwise)
- Property tests: plain loops (no `hypothesis`). Snapshot tol `rel=1e-6`. CI: local pytest (no runner wired).
- Gap-fill (cat 2) & liquidation (cat 3): default = **pin current behavior as a KNOWN GAP** + document, rather
  than re-model the engine now (re-modeling is a bigger, separate change). Say if you want them modeled.
