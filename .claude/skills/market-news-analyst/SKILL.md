---
name: market-news-analyst
description: Analyze crypto market-moving news and their impact on BTC and the broader crypto market. Use when analyzing recent news, understanding market reactions to regulatory decisions, exchange events, protocol updates, or macro events affecting crypto. Produces impact-ranked analysis reports.
---

# Market News Analyst (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) for crypto markets.

## Overview

Analyze market-moving news events from the past 7 days, focusing on impact on BTC/USDT and the broader crypto market. Collect news using WebSearch, evaluate impact magnitude, and produce structured reports ranked by significance.

## When to Use

- Analyzing recent crypto news before adjusting bot parameters
- Understanding why the bot's performance changed
- Providing context for AI advisor prompts
- Assessing if current market conditions suit the strategy

## News Categories

### Tier 1: Highest Impact
- **Regulatory**: SEC decisions, country-level crypto bans/approvals
- **Monetary Policy**: FOMC rate decisions, QE/QT changes
- **Exchange Events**: Exchange hacks, insolvency, withdrawal halts
- **Protocol Events**: Bitcoin halving, major forks, ETF approvals/rejections

### Tier 2: High Impact
- **Institutional**: Large fund entries/exits, corporate BTC purchases
- **Stablecoin**: USDT/USDC depeg events, new stablecoin regulation
- **DeFi**: Major protocol exploits, TVL shifts
- **Macro**: CPI/jobs data, banking crises, geopolitical events

### Tier 3: Moderate Impact
- **Mining**: Hash rate changes, miner capitulation
- **On-Chain**: Whale movements, exchange inflows/outflows
- **Market Structure**: Funding rate extremes, OI shifts, liquidation cascades

## Impact Scoring

```
Impact Score = (Price Impact × Breadth Multiplier) × Forward Modifier

Price Impact (BTC):
  Severe (±8%+):     10 points
  Major (±4-8%):      7 points
  Moderate (±2-4%):   4 points
  Minor (±1-2%):      2 points
  Negligible (<1%):   1 point

Breadth Multiplier:
  Systemic (all crypto + tradfi): 3x
  Cross-Market (BTC + alts):      2x
  BTC-Specific:                   1.5x
  Alt-Specific:                   1x

Forward Modifier:
  Regime Change: +50%
  Trend Confirmation: +25%
  Isolated Event: 0%
  Already Priced In: -25%
```

## Workflow

### Step 1: News Collection

Search for crypto news in these categories:
```
- "Bitcoin BTC regulation SEC" (past 7 days)
- "FOMC Federal Reserve crypto impact"
- "crypto exchange hack insolvency"
- "Bitcoin ETF approval"
- "stablecoin USDT USDC regulation"
- "crypto whale movement exchange flow"
- "Bitcoin mining hashrate"
- "DeFi exploit hack"
```

**Trusted Sources**:
1. Official: SEC.gov, Federal Reserve, exchange announcements
2. Tier 1: CoinDesk, The Block, Bloomberg Crypto
3. Tier 2: Cointelegraph, Decrypt, CoinTelegraph
4. On-Chain: Glassnode, CryptoQuant, Whale Alert

### Step 2: Score and Rank Events

Apply impact scoring to each news item. Rank from highest to lowest.

### Step 3: Analyze Market Reaction

For each significant event:
- BTC price reaction (direction, magnitude, timing)
- Altcoin reaction (correlated or divergent)
- Funding rate changes
- Open interest shifts
- Liquidation cascades
- Volume analysis

### Step 4: Assess Bot Implications

For each event, evaluate:
- Does this change the macro regime?
- Should bot parameters be adjusted?
- Is the AI advisor context accurate?
- Are circuit breakers appropriate for current conditions?

### Step 5: Generate Report

```markdown
# Crypto Market News Analysis - [Date Range]

## Executive Summary
[Top 2-3 events and their market impact]

## Impact Rankings
| Rank | Event | Date | Score | BTC Reaction |
|------|-------|------|-------|-------------|

## Detailed Analysis
[Per-event analysis with market reaction]

## Bot Implications
- Regime assessment: [current regime]
- Recommended adjustments: [if any]
- AI context update: [what to include]
- Risk level: [normal/elevated/high]
```

## Integration with Bot

- **news_fetcher.py**: Complements RSS feeds with deeper analysis
- **AI Advisor**: Provides structured context for market_context
- **Risk Manager**: Informs circuit breaker sensitivity
- **Strategy**: May suggest parameter adjustments
