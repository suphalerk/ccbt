---
name: project_round13_combined_2026_03_23
description: Round 13 combined backtest: 125 existing + 11 new R13 bots, results and observations
type: project
---

R13 combined backtest completed 2026-03-23. Script: research/r13_combined_backtest.py. Output: data/r13_combined.json.

**Results:**
- 146 bots attempted, 136 active (7 skipped no data/trades)
- $200 → $1,449.15 (+624.6%), 1475 trades, 12.9% max DD

**Comparison vs R12 (125 bots):**
- R12: $200 → $2,035.73 (+917.9%), DD 10.3%, 1449 trades
- R13: $200 → $1,449.15 (+624.6%), DD 12.9%, 1475 trades
- R13 added 26 more trades but LOWER return — the 11 new bots net diluted the portfolio in shared-wallet replay (max 5 concurrent slots limit means new bots compete for slots vs proven bots).

**Why:** The new bots are running in the shared wallet from 2026 onwards (recent data only), so their limited contribution shows. BAN (PF 1.02) and 1000PEPE dualthrust (PF 0.93) are negative in engine — they drag.

**R13 engine results (11 bots, $10K independent):**
- HBAR ribbon_ao 1h: PF 1.30, 116 trades, +$1258
- ICP ribbon_ao 4h: PF 2.16, 29 trades, +$1158
- AAVE dualthrust_adx 1h: PF 3.84, 13 trades (low count), +$1084
- WLD dualthrust_adx 1h: PF 2.55, 17 trades, +$944
- IP ribbon_rsi_vol 4h: PF 1.55, 43 trades, +$896
- XAUUSD ribbon_ao 4h: PF 1.75, 21 trades, +$516
- XAI ribbon_rsi_vol 4h: PF 1.45, 32 trades, +$387
- ARB dualthrust_adx 1h: PF 1.45, 24 trades, +$324
- XLM ribbon_ao 4h: PF 1.29, 23 trades, +$224
- BAN ribbon_ao 1h: PF 1.02 (marginal, essentially flat), -$15
- 1000PEPE dualthrust_adx 1h: PF 0.93 (NEGATIVE), -$95 — REJECT

**How to apply:**
- REJECT: BAN ribbon_ao R13 and 1000PEPE dualthrust_adx R13 (engine PF < 1.1)
- Consider: AAVE (only 13 trades, need more data), WLD, ICP, HBAR as additions
- Portfolio performance drops when adding marginal bots due to slot competition with top bots (BTC/WIF/BERA)
