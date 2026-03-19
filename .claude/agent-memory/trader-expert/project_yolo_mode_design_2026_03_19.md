---
name: YOLO Mode Optimization Results
description: YOLO config fine-tuning rounds 2-20 results. Champion: $100 -> $9.35e+28 over 5yr (100x lev, 17% risk, 20 pyramids, adaptive OFF, vol 0.7, SL 1.65, TP 4.0). DD 24.1%. Key insight: adaptive sizing HURTS at YOLO levels, lower volume threshold massively increases profitable trades.
type: project
---

## YOLO Champion Config (saved to config_yolo.json)
- Leverage: 100x (was 30x)
- Risk: 17% (was 10%)
- Pyramids: 20 adds (was 7)
- Adaptive sizing: OFF (was ON — hurts at YOLO levels)
- Volume threshold: 0.7x MA (was 1.3x — lower = more trades with high PF)
- RSI short range: 28-55 (was 30-52 — slightly wider)
- EMA slope filter: OFF (was 0.02 — adds trades without hurting PF)
- Max consecutive losses: 20 (was 10)
- SL: 1.65 ATR (was 1.2 — wider SL = fewer stopped out, higher PF)
- TP: 4.0 ATR (was 3.0 — runners go further)
- Post-TP1 trail: 5.0 ATR (was 3.0 — let winners run longer)
- MTD tiers: 2.0/1.5/1.0/0.7/0.4 (aggressive downscale protects DD)

## Results: $100 -> $9.35e+28 | 473 trades | PF 4.80 | DD 24.1%

## Key Findings by Round
- R2: Leverage scales exponentially, DD plateaus at ~23.7% (SL ratcheting bounds it)
- R3: Risk 13% = sweet spot for original config ($307B, DD 29.9%)
- R5: MTD aggressive downscale (0.7/0.4) is key DD reducer
- R9: **Adaptive sizing OFF = 5x more profit, lower DD** (biggest single finding)
- R11: Volume 0.7x = 370+ trades vs 260, PF stays high = exponential compounding
- R15: SL 1.65 ATR = 15,000x improvement over 1.2 ATR
- R19: TP 4.0 ATR + trail 5.0 = $9.35e+28 vs $5.47e+25

## Stress Tests
- Bear market (2021-2023): DD 46.3% — expected for YOLO
- Bull market (2024-2026): DD 24.1% — works great
- 1.5x costs: DD 31% — barely busts, cost-sensitive
- 2x costs: Still $5e+18, DD 30.1%

## Why adaptive sizing hurts at YOLO levels
At high leverage, the A-grade 2x risk multiplier causes outsized losses that compound negatively. Flat sizing avoids this while still benefiting from pyramiding into winners.
