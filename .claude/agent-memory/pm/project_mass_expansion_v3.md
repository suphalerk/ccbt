---
name: Mass Expansion v3 Research
description: Round 3 expansion — 4 novel strategies on 71 untried coins + 4H trailing exit enhancement
type: project
---

Round 3 research started 2026-03-21.

**Context:** Rounds 1+2 found 22 bots from 118 coins. 71 coins remain untried (never passed to any sweep). Portfolio: 22 bots, 650 trades, PF 1.71, Sharpe 4.07, DD 8%, $200→$1,119 (+460%).

**Goal:** Find 3-10 more tradeable coins. Target quality: PF>=1.3, trades>=10, DD<=25%.

**4 research tracks selected:**
1. Supertrend (ATR-band trailing signal) — sweep all 71 untried coins on 1H
2. Heikin-Ashi + EMA crossover — reduce noise on untried coins (1H)
3. Keltner Channel breakout — ATR-band momentum breakout (1H)
4. 4H Ichimoku trailing exit — replace fixed TP with trail on verified 4H winners (RENDER, HBAR, TAO)

**Coins targeted:**
- All 71 untried 1H coins (bonk, pepe, aave, ada, aia, algo, alice, apt, atom, ban, bch, bnb, cfg, cfx, coin, cos, crv, dego, dot, edge, ena, enj, etc, ethfi, fet, fil, gala, h, intc, irys, jct, kas, kat, link, lit, ltc, lyn, myx, opn, op, paxg, play, power, qnt, robo, sahara, sand, sign, sto, sui, tia, ton, tradoor, tsla, uni, virtual, vvv, waxp, wlfi, w, xai, xmr, xp, xpt, xrp, zec, zen, zro, lobster)
- 4H trailing: re-run on RENDER, HBAR, TAO, ICP, DOT (already verified 4H Ichi winners)

**Why these 4 approaches:**
- Supertrend: proven in TradFi, ATR-based = adapts to volatility, 1 signal per bar = fast to sweep
- Heikin-Ashi: smoothed candles could unlock noisy coins that fail standard EMA (ETH-correlated alts)
- Keltner Channel: breakout from ATR bands = trend-following, different from Ichimoku signal structure
- 4H trailing: Gold research showed trail>>fixed TP; RENDER/HBAR/TAO already confirmed edge — trailing may improve PF further

**How to apply:** Lightweight sweep first to filter, then full BacktestEngine verification for any coin passing PF>=1.3, trades>=10.
