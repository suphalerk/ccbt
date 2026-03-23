---
name: project_round11_2026_03_22
description: Round 11 — 3 new strategies implemented (StochMTF/ZScoreMeanRev/EMAribbon); 36/87 pass engine; portfolio $200→$1,579 (+689%)
type: project
---

Round 11 research completed 2026-03-22.

**New strategies implemented in engine:**
- `stoch_mtf` — Stochastic MTF: K/D crossover in oversold/overbought zone + EMA50 direction filter. 8/29 passed engine.
- `zscore_meanrev` — Z-Score Mean Reversion: zscore < -2 long (in uptrend) / zscore > +2 short (in downtrend). 17/27 passed engine. Best performers.
- `ema_ribbon` — EMA Ribbon: 6 EMAs (8/13/21/34/55/89) fully aligned, edge-detect on first aligned bar. 11/31 passed engine.

**Top new verified bots:**
- ADA stoch_mtf 4H: engine PF 6.46, 9 trades, WR 67%
- BAN zscore_meanrev 1H: engine PF 3.83, 13 trades, WR 69%
- AKT zscore_meanrev 1H: engine PF 3.09, 13 trades, WR 62%
- BERA ema_ribbon 4H: engine PF 3.07, 19 trades, WR 58%
- ICP ema_ribbon 4H: engine PF 2.30, 32 trades, WR 50%

**Key finding:** zscore_meanrev has the highest pass rate (17/27 = 63%) and best per-bot PF. Mean reversion with trend filter works well.

**Portfolio backtest (all rounds, $200 start, max 5 concurrent):**
- Final balance: $1,579 (+689%)
- Total trades: 1,465
- Unique bots: 85
- Max drawdown: 26.1% ($412 from peak)

**Why:** zscore_meanrev placed OUTSIDE trend_signals_gated in engine (like range_bounce) — it's mean-reversion logic.

**How to apply:** For future sweeps, zscore_meanrev is the most reliable of the three new strategies. EMA ribbon generates too many trades at 1H (100+ per year) with mediocre PF. Stoch MTF works better on 4H than 1H.
