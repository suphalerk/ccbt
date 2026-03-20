---
name: Multi-Coin EMA Crossover Research (ETH, SOL, PAXG) — 2026-03-20
description: 15m EMA crossover strategy tested on ETH/SOL/PAXG — edge exists only on BTC, not on altcoins
type: project
---

BTC EMA crossover edge (PF 1.51) does NOT transfer to ETH, SOL, or PAXG on 15m timeframe.

**Why:** BTC's atr_min filter (default from config) blocks all ETH/SOL/PAXG trades because their ATR values are below threshold. When atr_min=0 is applied, trades are generated but PF < 1.10 for all alts. The strategy's edge is BTC-specific.

**Key findings:**
- BTC 2yr: PF 1.51, 75 trades, +751% total (high leverage effect), Sharpe 1.77
- BTC 5yr: PF 1.42, 103 trades, +421% total, Sharpe 0.95
- ETH: atr_min blocks all trades with default config (only 3 generated). With atr_min=0: PF 0.90, WR 27%, DD 73%. No config variant breaks PF 1.0.
- SOL: Same blockage. Best result atr_min=0 only: PF 1.04 (barely above 1.0, statistically noise). Win rate 31%, DD 57%.
- PAXG: Blocked completely by default. With relaxed params: PF 0.78-0.90 max. Too few trades (14-34) over 2yr.

**Per-source insight (SOL, PF 1.04 case):**
- ema_crossover: 89t, WR 29%, PF 0.99 (loser)
- ema_fast_crossover: 20t, WR 40%, PF 1.30 (minor winner)
- SOL's edge comes only from fast crossover, which is insufficient as standalone

**Root cause hypothesis:** ETH/SOL have lower trending regime frequency in 2021-2026 data. The 2021-2022 altcoin bear market destroys PF badly. BTC has more sustained directional trends.

**How to apply:**
- Do NOT deploy EMA crossover bot on ETH or SOL without fundamentally different strategy parameters
- If expanding to altcoins, explore different strategies entirely (momentum, mean reversion)
- PAXG on 15m has too few signals — use OANDA XAU/USD for gold, not PAXG perpetual
- Keep bot focused on BTC only for now
