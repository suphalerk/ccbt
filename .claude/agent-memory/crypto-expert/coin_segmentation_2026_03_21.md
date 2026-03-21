---
name: Coin Segmentation & Tradability Assessment 2026-03-21
description: 50+ coins segmented into 7 categories. Top 20 prioritized for backtesting. AI narrative cluster (TAO/RENDER/FET) = highest conviction. 20+ coins flagged untradeable.
type: project
---

## Coin Segmentation (7 segments)

### Best Segments for Trading
1. **High-Momentum Altcoins** (TAO, RENDER, FET, SUI, TIA, HYPE) — strong narrative-driven trends, low BTC corr, Ichimoku 1H or long-only
2. **Meme Extension** (1000PEPE, 1000BONK, VIRTUAL) — EMA 15m + funding scorer, similar to DOGE/WIF approach

### Marginal Segments
3. **DeFi** (AAVE, ENA) — 4H Ichimoku only, selective
4. **Large-Cap** (BNB, ATOM) — 4H Ichimoku, very few will work
5. **Infrastructure** (KAS only) — PoW dynamics give BTC-like trend persistence

### Skip Entirely
6. **Gaming/Metaverse** (GALA, ENJ, SAND, AXS, ALICE) — dead narrative
7. **Privacy/Legacy** (XMR, DASH, ZEC, ETC) — structural headwinds

## Top 8 Priority Coins
1. TAO — AI narrative, Ichimoku 1H long-only, SL 3.0/TP 6.0
2. RENDER — AI/GPU, Ichimoku 1H long-only
3. FET — AI merged token, Ichimoku 1H long-only
4. SUI — DeFi ecosystem growth, Ichimoku 1H
5. 1000PEPE — meme momentum, EMA 15m + funding
6. TIA — modular blockchain, Ichimoku 1H
7. 1000BONK — Solana meme, EMA 15m + funding
8. KAS — PoW coin, Ichimoku 1H

## Untradeable (skip backtesting)
XRP, ADA, LTC, BCH, DOT, ALGO, FIL, XMR, DASH, ZEC, ETC, GALA, ENJ, SAND, AXS, ALICE, WAXP, CFX, QNT, POL, ETHFI, PAXG, W, ZRO

## New Approaches to Test
- Long-only Ichimoku 1H (config flag needed)
- 4H Ichimoku (need 4H data generation)
- Momentum ROC entry filter
- Adaptive SL/TP by volatility regime
- Correlation-aware position sizing (TAO/RENDER/FET cluster ~0.70 corr)

**Why:** EMA 15m is BTC-specific; Ichimoku 1H works on mid-caps. High-momentum narrative coins (AI cluster) have structural trend persistence from real fundamental demand. Meme coins work with EMA because retail momentum creates persistent trends on 15m.

**How to apply:** Test Round 1 (TAO, RENDER, FET, 1000PEPE, SUI) first. If 3+ show PF > 1.3, proceed to Round 2. Group correlated coins for portfolio sizing.
