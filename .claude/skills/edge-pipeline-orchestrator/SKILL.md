---
name: edge-pipeline-orchestrator
description: Orchestrate the full edge research pipeline from market observation through strategy design and validation. Use when developing new trading strategies end-to-end, from initial hypothesis to backtested and reviewed strategy draft.
disable-model-invocation: true
---

# Edge Pipeline Orchestrator (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills).

## Overview

Coordinate a complete edge research workflow that transforms market observations into validated trading strategies for the bot.

## Pipeline Stages

```
1. Observation → 2. Hypothesis → 3. Strategy Design → 4. Backtest → 5. Review → 6. Export
                                                           ↑                |
                                                           └── REVISE ──────┘
```

### Stage 1: Observation Collection

Gather market observations that might indicate a tradeable edge:

- Recurring price patterns in BTC/USDT
- Indicator divergences not captured by current strategy
- Time-of-day or day-of-week patterns
- Correlation breakdowns between assets
- On-chain metrics showing predictive value
- Funding rate extremes preceding reversals

### Stage 2: Hypothesis Formation

Convert observation into testable hypothesis:

```yaml
hypothesis:
  id: EDGE_001
  observation: "BTC funding rate > 0.03% often precedes 2-5% pullback within 24h"
  hypothesis: "Extreme positive funding creates overcrowded long positions
               that unwind as funding cost accumulates"
  testable_prediction: "Short when funding > 0.03% with 2% TP, 3% SL"
  required_data: ["funding_rate_history", "15m_ohlcv", "oi_data"]
  expected_win_rate: "55-60%"
  expected_rr: "0.67 (lower R:R but higher win rate)"
```

### Stage 3: Strategy Design

Design concrete trading rules:

```yaml
strategy:
  name: "Funding Rate Mean Reversion"

  entry:
    condition: "funding_rate > 0.03%"
    direction: short
    confirmation: "RSI(14) > 65 on 1h"
    timeframe: 1h check, 15m execution

  exit:
    take_profit: "2% from entry"
    stop_loss: "3% from entry"
    time_stop: "24 hours"
    trailing: "1.5% after 1% profit"

  sizing:
    method: "fixed_fractional"
    risk: "0.5%"  # Lower than main strategy
    max_concurrent: 1

  filters:
    - "Not during FOMC announcement ±2h"
    - "BTC volume > 20-day average"
    - "Not already in main strategy position"
```

### Stage 4: Backtest

Use `backtest/engine.py` or build custom backtest:

```python
# Minimum requirements:
# - 2+ years of data including bull and bear markets
# - Realistic fees (0.055% commission + 0.02% slippage)
# - Funding rate costs included
# - Walk-forward validation (70/30 split)
```

**Acceptance Criteria**:
- 50+ trades in sample
- Profit factor > 1.2
- Max drawdown < 15%
- Works in at least 2 market regimes
- OOS performance > 60% of IS

### Stage 5: Review

Score strategy on 8 criteria:

| Criteria | Weight | Score (1-10) |
|----------|--------|-------------|
| Edge clarity | 15% | Does the edge have logical basis? |
| Sample size | 15% | Enough trades for confidence? |
| Regime robustness | 15% | Works across market conditions? |
| Parameter stability | 10% | Plateau vs cliff in param space? |
| Fee sensitivity | 10% | Survives 2× fee assumption? |
| Drawdown profile | 10% | Max DD and recovery time? |
| Complementarity | 15% | Does it add value to existing strategy? |
| Implementation | 10% | Feasible to implement in bot? |

**Verdicts**:
- **PASS** (score ≥ 7.0): Ready for implementation
- **REVISE** (score 5.0-6.9): Fix identified issues, re-review (max 2 iterations)
- **REJECT** (score < 5.0): Fundamental flaw, archive and move on

### Stage 6: Export

For PASS strategies, generate:

```
strategies/
├── funding_rate_reversion/
│   ├── strategy.yaml          # Complete rule set
│   ├── backtest_results.json  # Performance data
│   ├── review_report.md       # Review scores and notes
│   └── implementation.md      # Integration guide for bot
```

## Integration with Bot

Approved strategies can be integrated into the bot by:
1. Adding to `bot/strategy.py` as new signal generator
2. Creating config section in `config.json`
3. Adding regime-based strategy switching logic
4. Including in AI advisor context for cross-strategy awareness

## Usage

```
/edge-pipeline-orchestrator

# With specific starting point:
/edge-pipeline-orchestrator "funding rate extreme reversal pattern"
/edge-pipeline-orchestrator --from-hypothesis EDGE_001
/edge-pipeline-orchestrator --from-draft strategies/draft_001.yaml
```
