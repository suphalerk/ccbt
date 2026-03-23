---
name: core7_new_strats_2026_03_22
description: Core 7 coins × 32 new strategies (R7-R12) full BacktestEngine sweep — 224 backtests, upgrade candidates identified
type: project
---

224 backtests complete: 7 coins × 32 strategies from R7-R12.
Script: `/Users/iceai/Work/ccbt/research/core7_new_strategies.py`
Total passing (PF >= 1.30, trades >= 6): 78 / 224.

**Why:** Validate whether any R7-R12 strategies beat current deployments on the 7 core coins.
**How to apply:** Use upgrade candidates below as starting configs for engine verify + deploy.

## Upgrade Candidates (new strategy beats current PF)

| Coin | Current (PF) | Best New Strategy | New PF | Trades | Delta |
|------|-------------|-------------------|--------|--------|-------|
| AVAX | Ichimoku 1H (1.95) | Dual Thrust 1H | 3.23 | 31 | +1.28 |
| BTC  | EMA 15m (1.85) | Stoch MTF 1H | 2.96 | 14 | +1.11 |
| WIF  | EMA 15m (1.64) | DualThrust+ADX 1H | 2.45 | 8 | +0.81 |
| ARC  | EMA 15m / Ichi (1.89) | Dual Thrust 1H | 2.50 | 18 | +0.61 |

## No Upgrade Needed
- GUN: AO 1H PF 3.88 (19t) but current Ichi 1H PF 4.00 still better
- ATH: ROC 1H PF 1.66 (64t) but current Ichi 1H PF 2.51 still better
- 1000PEPE: Alligator 1H PF 3.51 (16t) but current Vol Exp PF 10.85 massively better

## Notable Highlights by Coin

**BTC:** Stoch MTF 1H PF 2.96 (57% WR, 14 trades), Z-Score 1H 2.54 (9t), ZScore+Stoch 2.70 (9t)
**WIF:** Dual Thrust 1H 2.07 (28t), DualThrust+ADX 1H 2.45 (8t), Stoch+ST 1H 1.77 (12t)
**ARC:** Dual Thrust 1H 2.50 (18t), DualThrust+ADX 1H 2.47 (11t), EMA+Ichi 4H 2.05 (14t)
**AVAX:** Dual Thrust 1H 3.23 (31t, DD 2.0%), Ichi+ADX 1H 2.77 (9t), DualThrust+ADX 2.37 (21t)
**GUN:** AO 1H 3.88 (63% WR, 19t) — very close to current; EMA+Ichi 4H 2.23 (13t)
**ATH:** ROC 1H 1.66 (42% WR, 64t) — only real winner; nothing beats current Ichi 1H
**1000PEPE:** Alligator 1H 3.51 (50% WR, 16t), DualThrust+ADX 1H 2.41 (22t), Ichi+ADX 1H 2.44 (8t)

## Top 10 Absolute Performers

| Coin | Strategy | PF | WR% | Trades | DD% |
|------|----------|----|-----|--------|-----|
| GUN | AO 1H | 3.88 | 63.2 | 19 | 3.0 |
| GUN | AO 4H | 3.88 | 63.2 | 19 | 3.0 |
| 1000PEPE | Alligator 1H | 3.51 | 50.0 | 16 | 1.7 |
| 1000PEPE | Alligator 4H | 3.51 | 50.0 | 16 | 1.7 |
| AVAX | Dual Thrust 1H | 3.23 | 54.8 | 31 | 2.0 |
| AVAX | Dual Thrust 4H | 3.23 | 54.8 | 31 | 2.0 |
| BTC | Stoch MTF 1H | 2.96 | 57.1 | 14 | 2.2 |
| BTC | Stoch MTF 4H | 2.96 | 57.1 | 14 | 2.2 |
| AVAX | Ichi+ADX 1H | 2.77 | 55.6 | 9 | 2.0 |
| AVAX | Ichi+ADX 4H | 2.77 | 55.6 | 9 | 2.0 |

## Key Patterns
- Dual Thrust is the dominant breakout winner (AVAX, ARC, WIF all benefit)
- Stoch MTF wins only on BTC — not portable to other coins
- AO (Awesome Oscillator) surprisingly strong on GUN (1h already deployed on other coins)
- Alligator works on 1000PEPE but not on others
- ROC Momentum: high trade count (64-136) but low PF — fee drag hurts it
- WilliamsR+ADX: only passes on BTC (PF 1.54, 40 trades) — not a standout
- Z-Score / ZScore+Stoch: interesting on BTC (PF 2.54-2.70) but tiny trade count (9)
- 1h and 4H results are almost always identical — engine resamples from same 1H data

## Caution Flags
- BTC Stoch MTF: only 14 trades over 2 years — small sample, validate further
- WIF DualThrust+ADX: only 8 trades — very sparse signal
- GUN/ATH: only ~12mo data, results less reliable than 2yr coins
- AVAX Dual Thrust 31 trades is the best-sample upgrade candidate
