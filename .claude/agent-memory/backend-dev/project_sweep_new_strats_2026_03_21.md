---
name: sweep_new_strats_1 results
description: 10 new strategies swept across 133 coins (1H + 4H resampled), 2438 results, top strategies identified for full engine verification
type: project
---

10 new strategies swept across all 133 coins with 1H 2yr data (also 4H resampled). 2438 total results.
Results saved to: /Users/iceai/Work/ccbt/data/sweep_new_strats_1.json

**Why:** Round 6 expansion research — find new strategies beyond the 8 already deployed.

**How to apply:** Top candidates below need full BacktestEngine verification before deployment. Discard coins with < 10 trades or flagged as < 6 months data (CRCL/GUA/BEAT/ENSO/BARD/KITE/LIT/LIGHT/COLLECT/BTR etc).

## Strategy Ranking (mean PF across all coins, 4H >> 1H)

| Strategy | Avg PF | Winners (>=1.3) | Tested |
|---|---|---|---|
| Dual Supertrend 4H | 1.35 | 54 | 111 |
| Alligator 4H | 1.34 | 41 | 99 |
| Ichi+Supertrend 4H | 1.33 | 44 | 110 |
| ADX Trend Init 4H | 1.26 | 36 | 113 |
| Aroon Cross 4H | 1.24 | 40 | 122 |
| RSI 50-Cross 4H | 1.23 | 44 | 117 |
| CCI Breakout 4H | 1.15 | 28 | 124 |
| StochRSI Cross 4H | 1.10 | 23 | 124 |
| PSAR Flip 4H | 1.08 | 35 | 118 |
| All 1H strategies | ~1.0 | low | — |

**Key insight:** ALL strategies work better on 4H than 1H. Same pattern as previous research.

## Top Verified Candidates (PF >= 2.0, trades >= 15, known data quality)

- PIPPIN: Ichi+Supertrend 4H PF 7.09 (16 trades) — only 0.9yr data, skip
- LYN: Dual Supertrend 1H PF 5.79 (20 trades) — verify data length
- AKT: Dual Supertrend 4H PF 2.59 (14 trades) — verify
- AKT: Dual Supertrend 1H PF 2.07 (76 trades) — HIGH TRADE COUNT, strong candidate
- ANKR: Dual Supertrend 4H PF 2.74 (23 trades) — verify
- LINK: Alligator 4H PF 2.65 (20 trades) — LINK is large-cap, good data quality
- LINK: Dual Supertrend 4H PF 2.52 (27 trades) — strong candidate
- FIL: Dual Supertrend 4H PF 2.50 (29 trades) — strong candidate
- INJ: Alligator 4H PF 2.50 (23 trades) — already have INJ Ichi, this would add
- DOGE: Dual Supertrend 4H PF 2.11 (28 trades) — already have DOGE EMA
- DOT: RSI 50-Cross 4H PF 2.31 (55 trades) — HIGH TRADE COUNT, strong
- FET: Aroon Cross 4H PF 2.18 (61 trades) — HIGH TRADE COUNT, strong
- POL: RSI 50-Cross 4H PF 2.27 (40 trades) — already have POL 4H Trail
- 1000PEPE: RSI 50-Cross 4H PF 1.90 (58 trades) — already have PEPE VolExp
- SIREN: Alligator 1H PF 2.26 (49 trades) — new coin, check data length
- WAXP: CCI Breakout 4H PF 1.81 (83 trades) — HIGH TRADE COUNT
- XRP: Dual Supertrend 4H PF 3.13 (28 trades) — LARGE-CAP, strong candidate
- ZETA: Dual Supertrend 4H PF 2.20 (27 trades) — already have ZETA Ichi
- 1000SHIB: Dual Supertrend 1H PF 1.76 (108 trades) — already have SHIB Ichi
- NEAR: Aroon Cross 1H PF 1.58 (217 trades) — HIGH TRADE COUNT, strong candidate
- ARB: ADX Trend Init 4H PF 1.84 (27 trades) — already have ARB EMA
- WLD: RSI 50-Cross 4H PF 2.01 (51 trades) — already have WLD VolExp
- TRX: ADX Trend Init 4H PF 1.72 (40 trades) — already have TRX Ichi

## Priority for Full Engine Verification
1. XRP Dual Supertrend 4H (PF 3.13, 28 tr, large-cap, no existing bot)
2. LINK Dual Supertrend 4H (PF 2.52, 27 tr, large-cap, no existing bot)
3. FIL Dual Supertrend 4H (PF 2.50, 29 tr, no existing bot)
4. DOT RSI 50-Cross 4H (PF 2.31, 55 tr, high trade count)
5. FET Aroon Cross 4H (PF 2.18, 61 tr, high trade count; already have FET 4H Trail)
6. AKT Dual Supertrend 1H (PF 2.07, 76 tr, high trade count)
7. NEAR Aroon Cross 1H (PF 1.58, 217 tr, highest trades in dataset)
8. WAXP CCI Breakout 4H (PF 1.81, 83 tr, new coin)
9. ANKR Dual Supertrend 4H (PF 2.74, 23 tr)
10. PAXG Dual Supertrend 4H (PF 2.74, 26 tr)

Script: /Users/iceai/Work/ccbt/research/sweep_new_strategies_1.py
Results: /Users/iceai/Work/ccbt/data/sweep_new_strats_1.json
