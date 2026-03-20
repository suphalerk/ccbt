---
name: Multi-Coin EMA Crossover Analysis
description: Comprehensive backtest of 15 coins with EMA(9/21) crossover - only BTC (PF 1.51) and DOGE (PF 1.33) have viable edge. ETH/SOL/most ALTs fail.
type: project
---

## Multi-Coin Day Trading Analysis (2026-03-20)

Backtested 15 coins using exact EMA(9/21) crossover strategy on 15m with current config (R10%/L25x).

### Results Summary (2yr backtest, $10K initial)

**Profitable (PF > 1.2)**:
- BTC: PF 1.51, Sharpe 1.77, 75 trades, +381%/yr
- DOGE: PF 1.33, Sharpe 1.15, 41 trades, +44%/yr
- AVAX: PF 1.30, Sharpe 0.64, 23 trades (low sample), +27%/yr
- ARB: PF 1.27, Sharpe 0.96, 41 trades, +53%/yr

**Marginal (PF 1.0-1.2)**:
- WIF: PF 1.21, 65 trades, +52%/yr
- ETH: PF 1.05, 91 trades, +4%/yr (5yr data)
- SOL: PF 1.00, 78 trades, 0%/yr (5yr data)

**Losing (PF < 1.0)**:
- XRP: PF 0.95, ADA: PF 0.39, DOT: PF 0.54, LINK: PF 0.80, NEAR: PF 0.85, OP: PF 0.85, SUI: PF 0.72, APT: PF 0.94, PAXG: PF 0.45

### Key Findings

1. **BTC is structurally unique** -- its trend persistence on 15m supports EMA crossover better than any altcoin
2. **ETH/SOL cannot be rescued by parameter tuning** -- full sweep (36 combos) shows best ETH is PF 1.11
3. **15 coins only produces ~1 trade/day** -- NOT day trading frequency
4. **Monthly PnL correlation between BTC/DOGE/ARB/WIF is low** (-0.06 to 0.42) -- some diversification benefit
5. **Crash correlation is still 1.0** -- all coins crash together
6. **Max 2 concurrent positions** across 4 coins simultaneously

**Why:** BTC leads the market. ALT crossovers are lagging reflections of BTC moves, adding noise but not alpha.
**How to apply:** Do not expand to multi-coin for frequency. Better path: add more signal types to BTC, or BTC+DOGE only with reduced risk.
