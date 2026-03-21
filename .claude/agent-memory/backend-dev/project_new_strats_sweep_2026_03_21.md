---
name: New Strategies Sweep Results (S11-S20) — 2026-03-21
description: 1738-combo sweep of 10 new strategies across 133 coins, 1H 2yr data. Top signals and strategies identified.
type: project
---

Sweep of strategies 11-20 across 133 coins (1H 2yr data + 4H resampled).
Script: `/Users/iceai/Work/ccbt/research/sweep_new_strategies_2.py`
Results: `/Users/iceai/Work/ccbt/data/sweep_new_strats_2.json`
Total tested: 1738 combinations. PF > 1.0: 889.

**Why:** Research sweep to find new deployable strategies beyond current 8 types.
**How to apply:** Use verify_winners.py to run full BacktestEngine on candidates with PF >= 1.5 and trades >= 8. Reject coins with < 6 months data (CRCL, GUA, TRADOOR, JCT, etc may be new listings).

## Strategy Winners by Category

### S12 VolExp+Supertrend Confluence (strongest overall)
- WAXP 1H: PF 12.45, 80% WR, 5 trades
- CRCL 1H: PF 9.27, 78% WR, 9 trades
- ATOM 1H: PF 5.38, 75% WR, 8 trades
- SIREN 4H: PF 6.37, 80% WR, 5 trades
- WLD 4H: PF 5.52, 75% WR, 4 trades (WLD already deployed via VolExp — different variant)
- CFX 4H: PF 4.76, 75% WR, 4 trades
- HBAR 4H: PF 4.71, 67% WR, 6 trades (HBAR already deployed Ichi4H)
- 1000PEPE 1H: PF 3.62, 63% WR, 16 trades — best trade count at high PF

### S15 London Open (session breakout)
- MSTR 1H: PF 5.92, 78% WR, 9 trades — MSTR already deployed Supertrend
- IP 1H: PF 2.78, 61% WR, 31 trades — strong trade count
- RIVER 1H: PF 2.70, 57% WR, 23 trades

### S11 EMA+Ichi Hybrid
- VVV 4H: PF 5.12, 62% WR, 13 trades — VVV not in portfolio
- DEGO 4H: PF 4.01, 43% WR, 7 trades
- ATH 4H: PF 3.24, 50% WR, 18 trades — ATH already deployed Ichi1H

### S19 ATR Adaptive Entry
- JCT 4H: PF 5.21, 75% WR, 8 trades
- LYN 4H: PF 4.48, 75% WR, 8 trades
- XAG 4H: PF 4.47, 75% WR, 4 trades — XAG already deployed Supertrend
- SOL 4H: PF 1.57, 50% WR, 129 trades — high frequency, SOL already deployed

### S16 Day-of-Week + EMA Cross
- VVV 4H: PF 3.94, 65% WR, 17 trades
- SOL 4H: PF 1.75, 47% WR, 71 trades
- ETH 4H: PF 1.44, 39% WR, 104 trades

### S18 Consecutive Candles
- GUA 4H: PF 6.90, 75% WR, 4 trades
- SOL/AVAX 4H: PF 1.75
- PENGU 4H: PF 1.89, 50% WR, 58 trades — best PF with good trade count

### S17 Regime Transition
- AXS 4H: PF 2.43, 49% WR, 35 trades
- ENSO 4H: PF 2.51, 43% WR, 7 trades

### S13 MTF RSI Align
- XAN 1H: PF 2.60, 55% WR, 22 trades
- ARIA 1H: PF 2.44, 51% WR, 37 trades

### S14 Funding Primary
- Only available for 128/133 coins with funding data
- No standout results (contrarian threshold may be too aggressive)

### S20 BTC Leader
- Results generally weak (PF 1.1-1.2 range), high trade counts, signal is too delayed/noisy

## Best Candidates for Full Engine Verification
Priority: coins not already in portfolio, PF >= 1.5, trades >= 6:
1. VVV — S11 EMA+Ichi 4H (PF 5.12, 13tr) + S16 DoW 4H (PF 3.94, 17tr)
2. ATOM — S12 VolExp+ST 1H (PF 5.38, 8tr)
3. 1000PEPE — S12 VolExp+ST 1H (PF 3.62, 16tr) — already has VolExp deployed
4. PENGU — S18 Consec 4H (PF 1.89, 58tr) — good freq
5. IP — S15 London 1H (PF 2.78, 31tr) — novel session strategy

## Key Findings
- S12 VolExp+Supertrend is strongest NEW signal type — confirms vol expansion works with ST filter
- 4H resampling consistently helps: S16/S18/S19 all stronger on 4H
- S20 BTC Leader is a dud — correlation signal too weak/delayed for alts
- S14 Funding Primary adds nothing new (already captured by existing scorer)
- S15 London Open is novel and has niche winners (MSTR, IP)
- S18 Consecutive Candles on 4H is reliable across many coins (consistent 1.5-1.75 PF range)
