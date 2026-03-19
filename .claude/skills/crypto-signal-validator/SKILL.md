---
name: crypto-signal-validator
description: Validate the bot's trading signals against multiple confirmation layers. Use to audit signal quality, verify R:R calculations, assess AI advisor recommendations, or debug why signals were generated/missed. Works both as automated pre-trade validation and post-trade audit tool.
---

# Crypto Signal Validator

Custom skill for CCBT — validates bot signals for quality assurance.

## Overview

A multi-layer validation system that cross-references the bot's generated signals against technical, fundamental, and risk criteria. Use for automated pre-trade quality checks, post-trade auditing, or debugging signal generation logic.

## When to Use

- Auditing signal quality and accuracy after trades
- Debugging why a signal was generated or missed
- Verifying AI advisor recommendations are reasonable
- Validating strategy changes before deployment

## Validation Checklist

### Layer 1: Technical Signal Validation

```
Signal: [LONG/SHORT] [SYMBOL] at [PRICE]

EMA Crossover:
  [ ] EMA(9) vs EMA(21) crossover confirmed on closed candle (iloc[-2])
  [ ] Not using forming candle (iloc[-1])
  [ ] EMA warmup sufficient (at least 50 bars for EMA(50))

Trend Filter:
  [ ] Price vs EMA(50) on 1h aligns with signal direction
  [ ] Long: price > EMA(50), Short: price < EMA(50)

RSI:
  [ ] RSI(14) value: ___
  [ ] Long: 48-75, Short: 25-52
  [ ] No extreme divergence with price

Volume:
  [ ] Current volume vs MA(20): ___ ratio
  [ ] Above multiplier threshold (1.0x)

ATR:
  [ ] ATR(14) value: ___
  [ ] Above minimum threshold (0.001)
  [ ] Not extreme (>3x normal = volatile regime)
```

### Layer 2: Risk Validation

```
Position Sizing:
  [ ] Risk amount = balance × 0.01 = $___
  [ ] SL distance = ATR × 1.5 = ___
  [ ] Position size = risk / SL_pct = $___
  [ ] Within leverage limit (equity × 3)

R:R Validation:
  [ ] Gross R:R = TP_distance / SL_distance = ___
  [ ] Round-trip fees = 2 × (0.055% + 0.02%) = 0.15%
  [ ] Net R:R = (TP - fees) / (SL + fees) = ___
  [ ] Net R:R >= 1.5 ✓/✗

Risk State:
  [ ] Daily PnL: ___% (limit: 3%)
  [ ] Consecutive losses: ___ (limit: 5)
  [ ] Open positions: ___ (limit: 2)
  [ ] Not in cooldown
  [ ] Not halted
```

### Layer 3: AI Advisor Validation

```
AI Decision:
  [ ] Confidence: ___ (threshold: 0.65)
  [ ] Position size modifier: ___ (range: 0.5-1.5)
  [ ] SL adjustment: ___
  [ ] TP adjustment: ___
  [ ] Risk flags: ___
  [ ] Reasoning makes sense: ___

Calibration:
  [ ] AI rolling accuracy: ___%
  [ ] Influence multiplier: ___
  [ ] Applied modifiers reasonable: ___
```

### Layer 4: Market Context

```
Regime:
  [ ] Detected regime: [trending/ranging/volatile]
  [ ] Regime factor: [1.0/0.7/0.5]
  [ ] Regime aligns with strategy type

Funding Rate:
  [ ] Current rate: ___
  [ ] Direction supports trade: ___

News:
  [ ] Any major events in last 2h: ___
  [ ] No imminent high-impact events: ___

Macro:
  [ ] DXY direction: ___
  [ ] Equity market sentiment: ___
```

### Layer 5: Final Decision

```
All Layers Passed: [YES/NO]

If NO, which layer failed:
  - Layer ___: [reason]

Recommendation:
  [ ] EXECUTE as-is
  [ ] EXECUTE with modifications: ___
  [ ] SKIP — reason: ___
  [ ] WAIT — recheck in: ___
```

## Usage

```
/crypto-signal-validator

# Or with specific signal:
/crypto-signal-validator LONG BTC 65500
```

The validator reads current bot state from `trades.db` and `config.json` to auto-fill known values.
