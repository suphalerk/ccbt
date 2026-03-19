---
name: position-sizer
description: Calculate risk-based position sizes for crypto perpetual futures trades. Use when calculating how much to risk, position sizing with leverage, ATR-based stop distances, Kelly criterion optimization, or validating the bot's risk.py calculations. Supports fixed fractional, ATR-based, and Kelly methods.
---

# Position Sizer (Crypto Futures Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) for crypto perpetual futures with leverage.

## Overview

Calculate optimal position size for crypto perpetual futures based on risk management principles. Three methods supported:

- **Fixed Fractional**: Risk fixed % of equity per trade (bot default: 1%)
- **ATR-Based**: Volatility-adjusted stop distances determine size
- **Kelly Criterion**: Mathematically optimal allocation from win/loss stats

## When to Use

- Validating bot's position sizing in `bot/risk.py`
- Comparing sizing methods for optimization
- Checking if positions fit within risk limits
- Auditing sizing calculations after trades

## Workflow

### Step 1: Gather Parameters

**Required**:
- Account equity (USDT balance)
- Entry price
- Stop loss price (or ATR for ATR-based)
- Leverage setting (bot default: 3x)

**From bot config**:
- `risk_per_trade`: 0.01 (1%)
- `max_daily_loss`: 0.03 (3%)
- `max_positions`: 2
- `leverage`: 3
- `commission_rate`: 0.00055
- `slippage_rate`: 0.0002

### Step 2: Calculate Position Size

#### Method A: Fixed Fractional (Bot Default)

```
risk_amount = equity × risk_per_trade
stop_distance_pct = abs(entry - stop_loss) / entry
position_size_usdt = risk_amount / stop_distance_pct
actual_qty = position_size_usdt / entry

# With leverage check
max_position = equity × leverage
position_size_usdt = min(position_size_usdt, max_position)
```

**Example**:
- Equity: $10,000, Risk: 1%, Entry: $65,000, SL: $64,025 (ATR×1.5)
- Risk amount: $100
- SL distance: 1.5%
- Position: $6,667 (~0.1026 BTC)
- With 3x leverage: max $30,000 → $6,667 OK

#### Method B: ATR-Based

```
atr_stop = ATR × atr_sl_mult  (default 1.5)
stop_distance_pct = atr_stop / entry
risk_amount = equity × risk_per_trade
position_size_usdt = risk_amount / stop_distance_pct
```

#### Method C: Kelly Criterion

```
kelly_pct = (win_rate × avg_win - (1 - win_rate) × avg_loss) / avg_win
half_kelly = kelly_pct / 2  # Always use half Kelly in practice

position_size_usdt = equity × half_kelly
```

**Half Kelly** captures 75% of growth with far less drawdown risk.

### Step 3: Apply Constraints

Check against all risk limits:

```
1. position_size ≤ equity × leverage (leverage limit)
2. position_size ≤ equity × max_position_pct (concentration limit)
3. total_open_risk ≤ equity × max_daily_loss (daily risk budget)
4. open_positions < max_positions (position count limit)
5. Strictest constraint wins
```

### Step 4: Apply Dynamic Adjustments

From bot's `risk.py`:

**Win Rate Factor**:
- Win rate > 48%: 1.0x (normal)
- Win rate < 48%: reduced (dynamic risk factor)

**Regime Factor**:
- Trending: 1.0x
- Ranging: 0.7x
- Volatile: 0.5x

**AI Advisor Modifier**:
- AI position_size_modifier: 0.5-1.5x
- Calibration influence multiplier applied

**Final size**:
```
final_size = base_size × regime_factor × dynamic_risk_factor × ai_modifier × calibration_influence
```

### Step 5: Fee-Adjusted Validation

Verify R:R after round-trip fees:
```
round_trip_fee = 2 × (commission_rate + slippage_rate)
                = 2 × (0.00055 + 0.0002) = 0.0015 (0.15%)

net_profit = tp_distance - round_trip_fee × entry
net_loss = sl_distance + round_trip_fee × entry
net_rr = net_profit / net_loss

# Must be >= min_rr_ratio (1.5)
```

## Output Format

```
Position Sizing Report
═══════════════════════
Account Equity:    $10,000 USDT
Entry Price:       $65,000
Stop Loss:         $64,025 (1.50%)
Take Profit:       $66,950 (3.00%)
Leverage:          3x

Method: Fixed Fractional (1% risk)
──────────────────────────────────
Risk Amount:       $100.00
Position Size:     $6,666.67 (0.1026 BTC)
Margin Required:   $2,222.22 (at 3x)

Constraints Check:
  Leverage limit:     $30,000 → PASS
  Max positions:      1/2     → PASS
  Daily loss budget:  $300    → PASS ($100 used)

Adjustments:
  Regime (trending):  × 1.0
  Win rate (52%):     × 1.0
  AI modifier:        × 1.1
  Calibration:        × 1.0

Final Position:    $7,333.33 (0.1128 BTC)
Final Risk:        $110.00 (1.1% of equity)

R:R Validation:
  Gross R:R:         2.00
  Fee cost:          $10.00 round-trip
  Net R:R:           1.87 → PASS (≥1.5)
```

## Key Principles

1. **Survival first**: Sizing is about surviving losing streaks
2. **1% rule**: Default 1%, never exceed 2% without exceptional reason
3. **Round down**: Always round position down (never up)
4. **Strictest constraint wins**: Multiple limits → tightest one applies
5. **Half Kelly**: Never use full Kelly; half Kelly = 75% of growth, much less risk
6. **Portfolio heat**: Total open risk ≤ 3% of equity (max_daily_loss)
7. **Asymmetry**: 50% loss needs 100% gain to recover
8. **Leverage awareness**: Low leverage (3x) is key risk control
