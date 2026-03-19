---
name: research
description: Iterative strategy optimization loop — agent team analyzes current performance, proposes improvements, implements changes, backtests, and repeats until target is met. Use when optimizing trading strategy parameters, testing new features, or hunting for higher returns.
user_invocable: true
---

# Strategy Research & Optimization Loop

Systematic iterative optimization methodology for the CCBT trading bot. Combines agent team expertise with automated backtesting to find optimal configurations.

## When to Use

- Optimizing strategy parameters (SL/TP/Trail, risk, leverage)
- Testing new signal types or features
- Hunting for higher returns or lower drawdown
- Validating changes after code modifications

## Methodology

### Phase 1: Baseline Measurement

Run the current config as baseline before any changes:

```python
from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv
import json

with open('config.json') as f:
    config = json.load(f)

signal_df = load_ohlcv('data/btcusdt_15m_2y.csv')
trend_df = load_ohlcv('data/btcusdt_1h_2y.csv')

engine = BacktestEngine(config, initial_balance=100.0)
result = engine.run(signal_df, trend_df)
```

Record: Trades, WR, PF, Sharpe, PnL, Max DD, per-signal-source breakdown.

### Phase 2: Agent Team Analysis

Spawn specialized agents in parallel:

1. **trader-expert**: Analyze current edge, identify weaknesses, propose parameter/strategy changes
2. **sa** (Solution Architect): Review code architecture, identify highest-impact code changes
3. **crypto-expert** (optional): Domain context if testing market-specific hypotheses

Key questions for agents:
- What's the bottleneck? (frequency? win rate? risk/reward? sizing?)
- What approaches have been tried and failed? (check conversation history and this skill doc)
- What's the highest-ROI change with lowest implementation risk?

### Phase 3: Hypothesis Testing (Parameter Sweep)

Test each change INDIVIDUALLY first, then in combination:

```python
import copy

tests = [
    ('Change A only', {param_a_overrides}),
    ('Change B only', {param_b_overrides}),
    ('A + B combined', {param_a_and_b}),
]

for name, overrides in tests:
    config = copy.deepcopy(base_config)
    config.update(overrides)
    engine = BacktestEngine(config, initial_balance=100.0)
    result = engine.run(signal_df, trend_df)
    # Compare vs baseline
```

Rules:
- **Isolate variables**: Test one change at a time to understand causality
- **No cherry-picking**: Use full 2-year dataset, not just favorable periods
- **Check per-source breakdown**: A combined PF can hide one good + one bad source
- **Watch trade count**: More trades with lower PF often = worse (fee drag)
- **DD matters**: High returns with high DD = fragile

### Phase 4: Implementation

If backtest shows improvement:
1. **backend-dev agent**: Implement code changes
2. Run `python3 -m pytest tests/ -v` — all tests must pass
3. Verify backtest with disabled feature = identical to baseline (no regression)
4. Verify backtest with enabled feature = improvement confirmed

### Phase 5: Iterate or Stop

**Stop criteria** (any of):
- Target return achieved
- Max rounds reached
- Diminishing returns (< 5% improvement per round)
- Overfitting risk (too many parameters tuned to same dataset)

**Continue criteria**:
- Target not met AND plausible hypotheses remain
- New approach fundamentally different from what was tried

### Phase 6: Deploy

Update config.json with best parameters, update CLAUDE.md and PRD.md.

## Key Lessons Learned

These findings from previous research rounds should guide future work:

### What Works on BTC 15m
- **Pyramiding into winners** is the single highest-impact feature (4x PnL improvement)
- **Compound pyramid adds from current balance** — NOT from original_size (5x more PnL vs fixed sizing)
- **MTD Accelerator** — scale up when winning month, scale down when losing (reduces DD 3%)
- **5 pyramid levels** better than 3 (adds at 2.8 and 3.5 ATR capture big runners)
- **Adaptive sizing** by signal quality — bet more on high-quality, skip low-quality
- **Higher leverage (10x)** is safe when combined with pyramiding SL ratcheting
- **EMA(9/21) + EMA(5/13) dual crossover** with RSI + volume confirmation
- **Regime-adaptive trailing** (wider in trending, tighter in ranging)
- **Trading hours filter** (skip 00-03 UTC dead hours)
- **TP extension HURTS** — extending TP on pyramided trades drops Sharpe from 2.01 to 0.30

### What Does NOT Work on BTC 15m
- **Mean reversion** (BB+RSI in ranging) — negative PF, bad on BTC trending markets
- **MACD momentum** — noisy, PF 0.71
- **EMA pullback** — structurally unprofitable
- **RSI divergence** — too few signals (2 in 2 years)
- **More trades via looser filters** — every added signal dilutes edge
- **Weekend trading** — negative edge on BTC
- **Higher risk alone** — circuit breakers trigger faster, net negative
- **Blended partial-TP R:R** — mathematically blocks most signals; use full TP distance

### Parameter Sensitivities
- SL multiplier: 1.2 ATR optimal (tested 1.0-2.0 range)
- TP multiplier: 3.0 ATR optimal (tested 2.0-4.0)
- Risk per trade: 2.5-3% optimal with adaptive sizing (half-Kelly ~2.1%)
- Cooldown: 4/8 candles optimal; reducing adds bad trades
- Volume threshold: 1.3x MA optimal; lower = noise

## Available Profiles

| Profile | Risk | Pyramid | MTD | 5yr Return | DD | Status |
|---------|------|---------|-----|-----------|-----|--------|
| **Deployed** | 3% | 5 adds, compound | On | +4,336% ($100→$4,436) | 9.5% | Active |
| Conservative | 2.5% | 3 adds | Off | ~165%/yr | 9.6% | Available |

### 5-Year Backtest (Deployed Config)
- $100 → $4,436 over 5 years (Apr 2021 - Mar 2026)
- 188 trades, WR 29.3%, PF 3.32, DD 9.45%
- Survives 2022 bear market (-1.5% for the year)
- Best year: 2025 (+199.7%), Best month: Feb 2026 (+105.3%)

## Invocation

User says `/research` with optional context:
- `/research` — full optimization loop from current baseline
- `/research "test lower SL multiplier"` — targeted hypothesis test
- `/research "compare profiles"` — backtest all 3 profiles side by side
