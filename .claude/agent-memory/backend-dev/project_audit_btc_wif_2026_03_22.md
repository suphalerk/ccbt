---
name: Audit: BTC Stoch MTF 1H + WIF DualThrust+ADX 1H (2026-03-22)
description: Deep audit of two core7 upgrade candidates — both REJECTED despite correct PF numbers due to low trade count and walk-forward instability
type: project
---

Deep audit confirmed the PF numbers are real and correctly computed. Both strategies REJECTED.

**Why:** High PF with very low trade count (14 and 8 respectively) creates high sampling noise. Walk-forward reveals instability — OOS performance degrades significantly.

**How to apply:** Do not deploy BTC Stoch MTF 1H or WIF DualThrust+ADX 1H. Keep current deployed strategies. Require ≥ 30 trades for upgrade consideration on any 1H strategy.

## BTC Stoch MTF 1H (PF 2.96, 14 trades)

- Signal check: 14/14 PASS — all K/D crossovers and EMA50 filters verified correct
- Look-ahead: PASS — signal on closed candle i-1, entry at candle i close
- Fee check: PASS — engine correctly deducts exit commission from PnL
- Walk-forward 50/50: UNSTABLE — First half PF 4.30 (8 trades), Second half PF 2.11 (6 trades) = 49% of IS
- vs BTC EMA 15m baseline: PF delta +1.28 but only 19% of trade count (14 vs 75 trades)
- Data span: 2024-03-28 → 2026-03-18 (2 years)
- Verdict: REJECT — walk-forward degradation, too few trades for statistical confidence

## WIF DualThrust+ADX 1H (PF 2.45, 8 trades)

- Signal check: 8/8 PASS — all DualThrust breakout + ADX + DI alignment conditions correct
- Look-ahead: PASS
- Fee check: PASS
- Walk-forward 50/50: INSUFFICIENT_TRADES — First half PF 5.05 (3 trades), Second half PF 1.96 (5 trades)
- Critical pattern: Trades 6-8 are all losses (3 consecutive SLs ending Feb 2026); strategy clearly failing in current WIF regime
- vs WIF EMA 15m baseline: PF delta +1.40 but only 8% of trade count (8 vs 97 trades)
- The 3 wins happen in 2024 bull market; the 5 losses cluster in late 2025-2026
- Verdict: REJECT — insufficient trade count, regime-specific wins that don't persist

## Audit script
- `/Users/iceai/Work/ccbt/research/audit_btc_wif.py`

## Key lesson
High PF from very few trades on 1H data is structurally suspect. The engine's regime filter and conservative conditions limit entries so much that 2 years of data only produces 8-14 trades. This is insufficient for statistical confidence regardless of PF.
