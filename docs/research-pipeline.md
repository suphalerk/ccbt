# Research Pipeline & Methodology

> How to run new research. See [research-history.md](research-history.md) for past rounds and [backtest-methodology.md](backtest-methodology.md) for the correctness rules.

## Auto-Research Pipeline
One-command research script replaces manual iterative process:
```bash
# Sweep a single strategy across all coins
python3 research/auto_research.py --strategy dual_supertrend --timeframe 4h

# Sweep ALL strategies
python3 research/auto_research.py --all-strategies

# Compare against deployed portfolio
python3 research/auto_research.py --strategy alligator --compare-deployed

# Generate config files for verified winners
python3 research/auto_research.py --generate-configs --input data/sweep_results.json

# Full portfolio backtest ($200 shared wallet)
python3 research/portfolio_backtest_v2.py
```

## 4H Forward-Test Cohort
Strategies that show an edge in backtest but have **< 15 trades** can't be deployed (audit gate), and backtest data can't produce more trades — they must accumulate live. The forward-test cohort runs such candidates on testnet to gather ≥ 15 attributable trades, then re-audits.

- **Manifest**: `research/forward_test_cohort.json` — each candidate's coin, strategy, tf, backtest baseline (PF/trades), status (`testing` / `blocked_slot`).
- **Clean attribution**: candidates must have a **FREE symbol** (not traded by any main bot) so their per-symbol `trades.db` rows are wholly theirs. A symbol already in `AUDITED_CONFIGS` is `blocked_slot` (duplicate-coin gate would prevent it trading) — free the slot first.
- **Deploy**: `FORWARD_TEST_CONFIGS` in `deploy/macos/start.sh` (separate from `AUDITED_CONFIGS`, same process so the PortfolioManager coordinates).
- **Track**: `python research/forward_test_report.py` → per candidate: live trades since added, live PF/WR, and a verdict (`KEEP_TESTING` < 15 tr / `READY_TO_AUDIT` ≥ 15 & PF ≥ 1.3 / `MARGINAL` / `DROP` PF < 1).
- **Graduate**: when `READY_TO_AUDIT`, run the full backtest audit on the now-larger sample before promoting into `AUDITED_CONFIGS`.

First cohort (2026-06-07, AO 4H from the timeframe audit): ZEN, DASH, ALICE, TON, PENGU (testing); AXS, PIXEL, SAND, ENJ (blocked_slot — symbol busy in main).

## Research Methodology (`/research`)

Iterative optimization loop proven to improve strategy from 10%/yr to 523%/yr:

### Loop
1. **Baseline** — backtest current config, record metrics
2. **Agent team analysis** — spawn trader-expert + SA in parallel to identify bottlenecks and propose changes
3. **Isolate & test** — sweep each change individually, then combine winners
4. **Implement** — backend-dev agent codes changes, run tests
5. **Verify** — disabled = no regression, enabled = improvement confirmed
6. **Iterate or stop** — repeat until target met or diminishing returns

### Rules
- Test one variable at a time before combining
- Full 2-year dataset, no cherry-picking periods
- Check per-signal-source breakdown (combined PF can hide bad sources)
- More trades with lower PF = worse (fee drag eats edge)
- Always compare vs baseline, not vs previous round

### Proven Findings (BTC 15m)
**Works**: Pyramiding (4x PnL), adaptive sizing, 10x leverage + SL ratcheting, dual EMA crossover, regime-adaptive trail, trading hours filter
**Doesn't work**: Mean reversion, MACD, EMA pullback, RSI divergence, weekend trading, looser filters, higher risk alone
**YOLO-specific**: Adaptive sizing OFF at high leverage (5x better), lower volume threshold 0.7x (80% more trades), wider SL 1.65 ATR, TP 4.0 + trail 5.0
