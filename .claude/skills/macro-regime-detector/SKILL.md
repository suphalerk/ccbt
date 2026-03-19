---
name: macro-regime-detector
description: Detect structural macro regime transitions affecting crypto markets using cross-asset analysis. Analyze DXY, yields, credit conditions, BTC dominance, ETH/BTC ratio, and risk appetite to identify regime shifts. Use when assessing macro environment for trading decisions or adjusting bot strategy parameters.
---

# Macro Regime Detector (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) for crypto market context.

## Overview

Detect structural macro regime transitions (1-3 month horizon for crypto) using cross-asset ratio analysis. Identifies regime shifts that should inform bot configuration and AI advisor context.

## When to Use

- Assessing current macro environment for crypto
- Deciding whether to adjust bot risk parameters
- Providing context for AI advisor prompts
- Understanding why bot performance may be changing

## 6 Components (Crypto-Adapted)

| # | Component | Indicator | Weight | What It Detects |
|---|-----------|-----------|--------|-----------------|
| 1 | USD Strength | DXY Index | 25% | Dollar strength inversely correlates with BTC |
| 2 | Risk Appetite | BTC vs Gold ratio | 20% | Crypto risk-on vs traditional safe haven |
| 3 | Crypto Market Structure | BTC Dominance | 15% | BTC leadership vs altcoin rotation |
| 4 | Yield Environment | US 10Y-2Y spread | 15% | Liquidity conditions affecting risk assets |
| 5 | Crypto Momentum | ETH/BTC ratio | 15% | Risk curve positioning within crypto |
| 6 | Volatility Regime | BTC realized vol vs VIX | 10% | Cross-market risk correlation |

## 5 Regime Classifications

### 1. Risk-On Expansion
- DXY weakening, yields stable/falling
- BTC dominance stable or falling (altcoins participating)
- ETH outperforming BTC
- Realized vol moderate
- **Bot implication**: Full position sizing, wider TP targets

### 2. BTC Dominance / Flight to Quality
- Crypto market stressed, BTC dominance rising
- ETH underperforming BTC
- Altcoins selling off
- **Bot implication**: Trade BTC only, tighter stops, reduced size

### 3. Risk-Off Contraction
- DXY strengthening, yields rising
- BTC and crypto broadly declining
- High correlation with equities
- **Bot implication**: Reduce position size to 50%, favor short signals

### 4. Liquidity Expansion
- DXY neutral/weak, yields falling
- Central banks dovish or easing
- Risk assets broadly rallying
- **Bot implication**: Increase exposure, wider trailing stops

### 5. Transitional / Uncertain
- Mixed signals across components
- No clear regime
- **Bot implication**: Default conservative settings, 70% position size

## Analysis Workflow

### Step 1: Gather Cross-Asset Data

Collect current readings for:
- DXY (US Dollar Index)
- US 10Y and 2Y Treasury yields
- BTC/USD price and 30-day realized volatility
- ETH/BTC ratio
- BTC dominance (% of total crypto market cap)
- VIX (equity volatility)
- Gold price (for BTC/Gold ratio)
- Total crypto market cap

### Step 2: Compute Signals

For each component:
- Calculate current value vs 20-day and 50-day moving averages
- Determine trend direction (rising/falling/flat)
- Score: +1 (bullish), 0 (neutral), -1 (bearish)
- Apply component weight

### Step 3: Classify Regime

Weighted score across all components:
- Score > +0.5: Risk-On Expansion or Liquidity Expansion
- Score -0.2 to +0.5: Transitional
- Score < -0.2: Risk-Off Contraction or BTC Dominance

Disambiguate using BTC dominance and ETH/BTC:
- Risk-On vs Liquidity: Yield curve slope
- Contraction vs BTC Dominance: BTC dominance direction

### Step 4: Generate Recommendations

Map regime to bot config adjustments:

| Regime | risk_per_trade | atr_tp_mult | atr_trail_mult | regime_factor |
|--------|---------------|-------------|----------------|---------------|
| Risk-On | 0.01 | 3.0-3.5 | 1.8 | 1.0 |
| BTC Dominance | 0.008 | 2.5 | 1.5 | 0.7 |
| Contraction | 0.005 | 2.0 | 1.2 | 0.5 |
| Liquidity | 0.012 | 3.5-4.0 | 2.0 | 1.0 |
| Transitional | 0.008 | 2.5 | 1.5 | 0.7 |

### Step 5: Report

Output:
1. Current regime classification with confidence
2. Component breakdown with individual scores
3. Transition signals (approaching regime change?)
4. Recommended bot parameter adjustments
5. Key levels to watch for regime change

## Integration with Bot

This analysis feeds into:
- **AI Advisor context** (`context_builder.py`): Include regime in market context
- **Risk Manager** (`risk.py`): Regime factor for position sizing
- **Strategy** (`strategy.py`): `detect_regime()` function validation
- **Config adjustments**: Manual parameter tuning based on macro view
