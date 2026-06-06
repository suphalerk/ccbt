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
