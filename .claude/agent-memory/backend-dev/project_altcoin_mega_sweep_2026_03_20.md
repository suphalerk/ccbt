---
name: altcoin_mega_sweep_2026_03_20
description: 150-combination mega sweep results — PA, Ichimoku, EMA across 15 coins on 15m and 1h (2026-03-20)
type: project
---

150 combinations tested: 15 coins × 5 strategies × 2 timeframes. All coins 2yr data (ETH/SOL 5yr).

**Why:** Testing whether PA patterns or Ichimoku offer any edge on altcoins, following BTC 15m EMA failure and Gold Ichimoku success.

**How to apply:** Use these findings to inform which coins/strategies are worth deeper investigation.

## Key Findings

### 15m Timeframe — Confirmed Dead
- **No strategy earns PF > 1.15 on 15m across any coin**
- Best single result: WIF EMA(9/21) PF 1.09 (barely above 1.0), ARB Ichimoku PF 1.09
- PA_Trend on 15m is consistently worst (avg PF 0.73), generating 1000+ trades/yr that churn fees
- EMA+PA on 15m: avg PF 0.77 — adding PA to EMA makes things worse (too many low-quality trades)
- Conclusion: **15m is confirmed noise for ALL strategies on ALL coins**

### 1h Timeframe — Ichimoku is the only positive signal
- **Ichimoku on 1h**: avg PF 1.02 across 15 coins, **9 of 15 coins > 1.0**
- No other strategy gets above 1.0 on average on 1h
- Top performers on Ichimoku 1h:
  - AVAX: PF 1.33, 71 trades/yr, WR 39.7%, DD 37.9%
  - ARB:  PF 1.27, 63 trades/yr, WR 38.4%, DD 37.7%
  - NEAR: PF 1.21, 68 trades/yr, WR 36.0%, DD 42.5%
  - WIF:  PF 1.18, 66 trades/yr, WR 33.6%, DD 55.0%
  - XRP:  PF 1.17, 67 trades/yr, WR 36.1%, DD 37.2%
  - BTC:  PF 1.10, 70 trades/yr, WR 34.8%, DD 22.8%

### Ichimoku + Trailing Stop — Worse than Fixed TP
- Ichi+Trail on 1h: avg PF 0.72, only AVAX > 1.0 (PF 1.03)
- Fixed TP (5× ATR) significantly outperforms trailing stop on crypto (opposite of Gold)
- This confirms Gold/crypto asymmetry: Gold trends run far → trail wins; Crypto chops → fixed TP wins

### PA (Price Action) — Broadly Fails
- PA_Trend 15m: avg PF 0.73, 0/15 coins > 1.0
- PA_Trend 1h: avg PF 0.85, 0/15 coins > 1.0
- PA generates too many trades (500-2000/yr) at low win rates (24-28%) — fee drag kills it
- PA never reaches PF > 1.0 on any coin on either timeframe

### EMA(9/21) on 1h
- avg PF 0.79, 0/15 coins > 1.0 on 1h
- Best: XRP 0.96, DOGE 0.93, BTC 0.91
- EMA crossover does NOT work on 1h for any altcoin

## Best Strategy Per Coin (1h, Ichimoku)
- AVAX: PF 1.33 *** BEST ***
- ARB:  PF 1.27
- NEAR: PF 1.21
- WIF:  PF 1.18
- XRP:  PF 1.17
- BTC:  PF 1.10 (lower but tightest DD at 22.8%)
- DOT:  PF 1.08
- LINK: PF 1.02
- SOL:  PF 1.02
- SUI:  PF 0.97 (below 1.0)
- DOGE: PF 0.98 (below 1.0)
- ADA:  PF 0.87 (all strategies fail)
- APT:  PF 0.76 (all strategies fail)
- OP:   PF 0.87 (all strategies fail)
- ETH:  PF 0.93 (all strategies fail on 1h)

## Actionable Conclusions
1. Ichimoku 1h with SL=2.0×ATR, TP=5.0×ATR is the only viable strategy across altcoins
2. Top 5 coins for Ichimoku 1h: AVAX, ARB, NEAR, WIF, XRP
3. PA patterns add no value anywhere — too many trades, fee drag destroys edge
4. 15m is dead for all indicator strategies (confirmed again)
5. Next research: portfolio approach — run Ichimoku 1h on top 5 coins simultaneously?
6. BTC Ichimoku 1h (PF 1.10) is interesting — much lower DD than BTC EMA on 15m
