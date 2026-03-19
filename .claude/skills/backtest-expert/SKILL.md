---
name: backtest-expert
description: Validate and stress-test crypto trading strategies through systematic backtesting. Use when evaluating strategy performance, testing parameter sensitivity, checking for overfitting, or validating the bot's EMA crossover strategy against historical BTC data. Emphasizes finding strategies that break the least.
---

# Backtest Expert (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) for crypto perpetual futures.

## Philosophy

Find strategies that **break the least**, not those showing maximum paper profits. A robust strategy performs adequately across market regimes, parameter ranges, and realistic friction.

## Workflow

### Step 1: Hypothesis Definition

Articulate the trading edge in one sentence:
- "EMA(9/21) crossover with RSI confirmation captures trend continuation moves in BTC/USDT"
- Define what market behavior the strategy exploits
- State expected win rate and R:R ratio

### Step 2: Rule Codification

Define rules with **zero discretion**:

**Entry Rules** (from bot's strategy.py):
- EMA(9) crosses EMA(21) on 15m (direction determines side)
- RSI(14) in zone: Long 48-75, Short 25-52
- Volume > MA(20) × multiplier
- Price vs EMA(50) on 1h (trend filter)
- ATR >= minimum threshold

**Exit Rules**:
- Stop Loss: Entry ± (ATR × 1.5)
- Take Profit: Entry ± (ATR × 3.0)
- Trailing Stop: Entry ± (ATR × 1.8), ratchets in profit direction

**Position Sizing**:
- 1% risk per trade
- Dynamic adjustment by win rate and regime

**Filters**:
- Max 2 concurrent positions
- 5 consecutive losses → 1h cooldown
- 3% daily loss → halt

### Step 3: Initial Backtest

Run using `backtest/engine.py`:

```bash
# Ensure historical data covers 5+ years and multiple regimes
python -c "
from backtest.engine import BacktestEngine
from backtest.data_loader import load_historical_data
import json

config = json.load(open('config.json'))
df = load_historical_data('BTCUSDT', '15m', years=5)
engine = BacktestEngine(config)
results = engine.run(df)
"
```

**Required regime coverage**:
- Bull market (2020-2021)
- Bear market (2022)
- Ranging/consolidation periods
- High volatility events (COVID crash, FTX collapse)
- Low volatility periods

### Step 4: Stress Testing

**Parameter Sensitivity** (most critical):
```
EMA fast: [5, 7, 9, 12, 15]
EMA slow: [15, 18, 21, 25, 30]
RSI range: [40-70, 45-65, 48-62, 50-60]
ATR SL mult: [1.0, 1.2, 1.5, 1.8, 2.0]
ATR TP mult: [2.0, 2.5, 3.0, 3.5, 4.0]
```

**Look for stable plateaus**: Robust strategies work across parameter ranges, not just optimal values. If profit drops 50%+ with small param changes, it's curve-fitted.

**Execution Friction**:
- Commission: Test at 1.5-2× typical (0.08-0.11% instead of 0.055%)
- Slippage: Test at 2-3× typical (0.04-0.06% instead of 0.02%)
- Partial fills: Simulate 80% fill rate
- Latency: Add 1-3 candle delay to entries

**Regime-Specific Testing**:
- Run separately on bull/bear/ranging/volatile segments
- Strategy should be profitable in at least 2 of 4 regimes
- Acceptable to underperform in 1 regime if overall expectancy positive

### Step 5: Out-of-Sample Validation

**Walk-Forward Analysis**:
1. Split data: 70% in-sample, 30% out-of-sample
2. Optimize on in-sample
3. Test on out-of-sample
4. Roll forward and repeat
5. Aggregate OOS results

**Acceptance criteria**:
- OOS performance >= 60% of in-sample
- No regime where OOS is catastrophic
- Drawdown pattern similar IS vs OOS

### Step 6: Results Evaluation

**Deployment Criteria**:

| Metric | Minimum | Target |
|--------|---------|--------|
| Total trades | 100+ | 200+ |
| Win rate | 40% | 45-55% |
| Profit factor | 1.2 | 1.5+ |
| Sharpe ratio | 0.5 | 1.0+ |
| Max drawdown | < 20% | < 15% |
| Recovery factor | > 2.0 | > 3.0 |
| Avg R:R | > 1.5 | > 2.0 |

**Red Flags**:
- Win rate > 80% (likely overfitted or curve-fitted)
- Max drawdown < 5% (unrealistic)
- Profit factor > 3.0 (survivorship bias)
- Strategy only works in one regime
- Requires frequent parameter re-optimization

## Crypto-Specific Backtest Considerations

- **24/7 Market**: No gaps, but include weekend/holiday behavior
- **Funding Rates**: Include 8-hour funding rate costs for perpetuals
- **Liquidation Risk**: Model at leverage × position size
- **Exchange Fees**: Maker/taker fee asymmetry
- **Data Quality**: Check for gaps, spikes, exchange-specific anomalies

## Integration with Bot

```python
# Use backtest/engine.py directly
from backtest.engine import BacktestEngine
from backtest.metrics import calculate_metrics

engine = BacktestEngine(config)
results = engine.run(historical_df)
metrics = calculate_metrics(results)
```

Compare backtest results with live trading journal (trades.db) to identify:
- Execution slippage vs backtest assumptions
- AI advisor impact (trades with/without AI adjustments)
- Regime detection accuracy
