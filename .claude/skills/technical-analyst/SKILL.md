---
name: technical-analyst
description: Analyze cryptocurrency price charts for technical analysis. Use when analyzing BTC/USDT or other crypto charts, identifying trends, support/resistance levels, EMA crossovers, RSI divergences, ATR volatility, and developing probabilistic scenarios. Pure chart analysis without fundamentals.
---

# Technical Analyst (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) for crypto perpetual futures trading.

## Overview

Comprehensive technical analysis of cryptocurrency charts focusing on the indicators used by this bot: EMA(9/21/50), RSI(14), ATR(14), volume analysis, and market regime detection.

## Core Principles

1. **Pure Chart Analysis**: Base conclusions exclusively on technical data
2. **Systematic Approach**: Follow structured methodology for each analysis
3. **Probabilistic Scenarios**: Express possibilities as probability-weighted scenarios
4. **Bot-Aligned Indicators**: Focus on EMA crossover, RSI zones, ATR-based levels

## Analysis Workflow

### Step 1: Identify Context

- Symbol being analyzed (default: BTC/USDT perpetual)
- Timeframes: 15m (signal) and 1h (trend)
- Current market regime (trending/ranging/volatile)

### Step 2: Trend Analysis

- **EMA Structure**: EMA(9) vs EMA(21) position and crossover status
- **Trend Filter**: Price vs EMA(50) on 1h timeframe
- **Higher Highs/Lows**: Directional structure analysis
- **Trend Strength**: Assess momentum and potential exhaustion

### Step 3: Support & Resistance

- Mark significant horizontal S/R levels
- Identify EMA dynamic support/resistance
- Note confluence zones
- Assess role reversals

### Step 4: Indicator Analysis

#### RSI(14)
- Current reading and zone (oversold < 30, neutral 30-70, overbought > 70)
- Bot signal zones: Long 48-75, Short 25-52
- Divergences with price
- Momentum direction

#### ATR(14)
- Current volatility level
- ATR ratio vs 20-period SMA (regime detection)
- Implications for SL/TP distances:
  - SL = entry +/- (ATR x 1.5)
  - TP = entry +/- (ATR x 3.0)
  - Trail = entry +/- (ATR x 1.8)
- Minimum ATR threshold check

#### Volume
- Volume vs MA(20) ratio
- Volume confirmation of price moves
- Climax or exhaustion patterns

### Step 5: Market Regime Classification

Based on bot's `detect_regime()` logic:
- **Trending**: ATR ratio 0.8-1.5 + consistent HH/HL or LH/LL (full position size)
- **Ranging**: ATR ratio < 0.8 or no direction (70% size)
- **Volatile**: ATR ratio > 1.5 (50% size)

### Step 6: Signal Assessment

Evaluate current conditions against bot entry criteria:

**LONG Signal Checklist**:
- [ ] EMA(9) > EMA(21) crossover on 15m
- [ ] RSI(14) between 48-75
- [ ] Volume > MA(20) x multiplier
- [ ] Price > EMA(50) on 1h
- [ ] ATR >= minimum threshold
- [ ] Net R:R >= 1.5 after fees

**SHORT Signal Checklist**:
- [ ] EMA(9) < EMA(21) crossover on 15m
- [ ] RSI(14) between 25-52
- [ ] Volume > MA(20) x multiplier
- [ ] Price < EMA(50) on 1h
- [ ] ATR >= minimum threshold
- [ ] Net R:R >= 1.5 after fees

### Step 7: Develop Scenarios

Create 2-4 probabilistic scenarios:

1. **Base Case (40-60%)**: Most likely based on current structure
2. **Bull Case (20-35%)**: Upside breakout scenario
3. **Bear Case (20-35%)**: Downside breakdown scenario
4. **Black Swan (5-10%)**: Low probability, high impact

Each scenario includes:
- Description and trigger conditions
- Target price levels
- Invalidation level
- Implications for bot (signal type, entry/exit levels)

### Step 8: Generate Report

Output structured analysis:
- Current regime and trend assessment
- Key levels (S/R, entry, SL, TP)
- Signal status (LONG/SHORT/NO_SIGNAL)
- Risk assessment
- Scenario probabilities

## Crypto-Specific Considerations

- **Funding Rate**: Positive = longs pay shorts (bearish pressure), Negative = shorts pay longs
- **Open Interest**: Rising OI + rising price = new longs, Rising OI + falling price = new shorts
- **Liquidation Levels**: Major liquidation clusters can act as magnets
- **24/7 Market**: No opening/closing gaps, but weekly candle close matters
- **Correlation**: BTC dominance, ETH/BTC ratio, DXY inverse correlation

## Integration with Bot

This analysis complements the bot's automated signal generation:
- Use to validate signals before increasing AI advisor confidence
- Identify regime changes that may require config adjustments
- Spot divergences the bot's indicators might miss
- Provide context for AI analyst prompts
