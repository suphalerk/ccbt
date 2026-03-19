---
name: strategy-pivot-designer
description: Diagnose strategy stagnation and propose fundamentally different trading approaches. Use when backtest results plateau, live performance stalls, or when the current EMA crossover strategy needs alternatives. Generates restructured strategy architectures.
disable-model-invocation: true
---

# Strategy Pivot Designer (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills).

## Overview

Identify when the current trading strategy has hit a performance ceiling and propose fundamentally different approaches rather than continuing parameter tweaks.

## When to Use

- Backtest performance plateaued across parameter ranges
- Live trading underperforming backtest expectations
- Market regime has shifted and strategy no longer fits
- Want to explore alternative strategies for Phase 8

## Stagnation Triggers

| Trigger | Detection |
|---------|-----------|
| Performance Plateau | Sharpe/profit factor unchanged across 3+ parameter sweeps |
| Overfitting Pattern | IS performance >> OOS performance (>40% degradation) |
| Fee Sensitivity | Small fee increase eliminates edge |
| Drawdown Excess | Max drawdown > 2× historical average |
| Regime Dependence | Strategy only profitable in 1 of 4 market regimes |
| Win Rate Decay | Win rate declining over rolling 30-trade windows |

## Pivot Types

### Type 1: Assumption Inversion

Flip a core assumption of the current strategy:

**Current**: Trend-following (EMA crossover)
**Pivot**: Mean reversion (Bollinger Band bounce)

**Current**: Momentum entry (RSI 48-75)
**Pivot**: Exhaustion entry (RSI < 30 or > 70)

**Current**: ATR-based fixed stops
**Pivot**: Structure-based stops (swing high/low)

### Type 2: Archetype Switching

Switch to entirely different strategy archetype:

| From | To | Rationale |
|------|-----|-----------|
| EMA Crossover | Breakout | Works in different regime |
| Trend Following | Mean Reversion | Captures range-bound profits |
| Single Timeframe | Multi-TF Confluence | Reduces false signals |
| Indicator-Based | Price Action | Less lag, more adaptable |
| Momentum | Volatility Expansion | Profits from regime change |

### Type 3: Objective Reframing

Change what the strategy optimizes for:

- Max Sharpe → Min Drawdown
- Win Rate → R:R Ratio
- Total Profit → Consistency (low variance)
- Signal Frequency → Signal Quality

## Workflow

### Step 1: Diagnose Current Strategy

Analyze backtest results and live performance:
```python
# From backtest/metrics.py
metrics = {
    'win_rate': 0.48,
    'profit_factor': 1.3,
    'sharpe': 0.7,
    'max_drawdown': -12%,
    'avg_rr': 1.8,
    'trades_per_month': 15,
    'regime_performance': {
        'trending': +8%,
        'ranging': -2%,
        'volatile': -5%,
    }
}
```

### Step 2: Identify Stagnation

Check each trigger against thresholds. Document which triggers are active.

### Step 3: Generate Pivot Proposals

For each active trigger, propose 2-3 pivots:

```yaml
pivot_proposal:
  name: "Mean Reversion with Bollinger Bands"
  type: assumption_inversion
  trigger: regime_dependence  # current strategy fails in ranging

  hypothesis: "BTC/USDT spends 60%+ of time in ranges.
               Mean reversion captures this neglected regime."

  entry_rules:
    - Price touches lower Bollinger Band (2σ)
    - RSI < 30 (oversold)
    - Volume declining (exhaustion, not panic)

  exit_rules:
    - Target: Middle Bollinger Band (20 SMA)
    - Stop: 1.5 × ATR below entry
    - Time stop: 24 hours max

  expected_improvement:
    regime_coverage: "Profitable in ranging + volatile"
    win_rate: "60-65% (higher but smaller wins)"
    risk: "Lower avg profit per trade"

  implementation_effort: medium
  backtest_priority: high
```

### Step 4: Rank and Recommend

Rank proposals by:
1. Expected regime coverage improvement
2. Complementarity with current strategy
3. Implementation effort
4. Backtest priority

### Step 5: Export

Save to `reports/strategy_pivot_<date>.md`

Include:
- Diagnosis summary
- Active stagnation triggers
- Ranked pivot proposals
- Implementation roadmap
- Backtest plan for top proposal
