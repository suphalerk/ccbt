---
name: scenario-analyzer
description: Analyze news headlines into 18-month crypto market scenarios. Use when evaluating how macro events (FOMC, regulation, halving) affect BTC and crypto positioning. Builds Base/Bull/Bear scenarios with probability-weighted sector impacts.
disable-model-invocation: true
---

# Scenario Analyzer (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills).

## Overview

Transform a news headline or event into 18-month crypto market scenarios. Analyze 1st/2nd/3rd-order effects, assign probabilities, and recommend bot configuration adjustments.

## Usage

```
/scenario-analyzer "Fed cuts rates by 50bp"
/scenario-analyzer "SEC approves spot ETH ETF"
/scenario-analyzer "Major exchange hack, $500M stolen"
```

## Workflow

### Step 1: Classify Event Type

| Type | Examples |
|------|---------|
| Monetary Policy | FOMC rate change, QE/QT, ECB decisions |
| Regulation | SEC actions, country bans/approvals, stablecoin rules |
| Technology | Bitcoin halving, major protocol upgrade, exploit |
| Geopolitical | War, sanctions, trade disputes |
| Market Structure | ETF approval, exchange failure, liquidation cascade |
| Corporate | Major adoption, corporate treasury, fund launch |

### Step 2: Build Scenarios

**Base Case (50-60%)**:
- Most probable outcome based on historical precedents
- 1st order: Direct market reaction
- 2nd order: Secondary effects within crypto
- 3rd order: Broader market and regulatory response

**Bull Case (20-30%)**:
- Optimistic interpretation
- Catalysts that could amplify positive effects
- Target BTC levels and timeline

**Bear Case (15-25%)**:
- Pessimistic interpretation
- Risk factors that could worsen situation
- Downside BTC levels and timeline

### Step 3: Analyze Order Effects

**1st Order** (immediate, 1-7 days):
- BTC price direction and magnitude
- Altcoin correlation
- Volume and volatility response
- Funding rate shift

**2nd Order** (medium-term, 1-3 months):
- Market regime change
- Institutional flow changes
- Mining economics impact
- DeFi/stablecoin effects

**3rd Order** (long-term, 3-18 months):
- Regulatory precedent setting
- Market structure evolution
- New narratives emerging
- Adoption trajectory change

### Step 4: Bot Implications per Scenario

For each scenario, recommend:
- Risk parameter adjustments
- Strategy modifications
- AI advisor context updates
- Circuit breaker sensitivity
- Timeline for review

### Step 5: Generate Report

Save to `reports/scenario_analysis_<topic>_YYYYMMDD.md`

## Example

```
/scenario-analyzer "Fed cuts rates by 50bp"

## Scenario Analysis: Fed Cuts Rates by 50bp

### Base Case (55%): Controlled Easing Rally
1st order: BTC +5-8%, risk-on sentiment, DXY weakens
2nd order: Institutional inflows increase, ETF volumes up
3rd order: Broader crypto adoption as real yields drop

Bot: Increase atr_tp_mult to 3.5, regime = Risk-On

### Bull Case (25%): Liquidity Supercycle
1st order: BTC +10-15%, altcoin rotation begins
2nd order: Stablecoin TVL expands, DeFi yields attractive
3rd order: New ATH within 6 months

Bot: risk_per_trade 1.2%, wider trailing stops

### Bear Case (20%): "Emergency Cut = Recession Signal"
1st order: BTC +3% then reversal as recession fears grow
2nd order: Risk-off shift, correlation with equities increases
3rd order: Prolonged uncertainty, range-bound for months

Bot: Reduce to 0.5% risk, tighter stops, favor short signals
```
