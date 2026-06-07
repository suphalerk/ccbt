---
name: arc-diagnosis-2026-06-06
description: ARCUSDT live-loss diagnosis — backtest healthy (PF 1.70) but live PF 0.52 from 11-trade micro-sample + TP never reached
metadata:
  type: project
---

ARCUSDT (config_arcusdt_ema.json, EMA crossover 15m, 25x lev, 1% risk) net-losing on testnet.

**Live (excl 2026-03-23 glitch):** 11 trades, 5W/6L (45%), net -$49.7, PF 0.52. ALL closes logged "sl" (trailing-stop scratches + full SL hits). avg_win $10.9 vs avg_loss $17.4 (R:R ~0.63). 7 shorts -$43, 4 longs -$6.7.

**Backtest (14mo, Jan2025-Mar2026 — ARC is newer listing, no full 2yr):** PF 1.70, WR 50%, 36 trades, Sharpe 1.21, DD 4.1%, net +$821. Walk-forward OOS/IS = 221% (PASS). Backtest shorts are the BEST side (64% WR) — opposite of live, confirming live short-loss is sample noise not structural.

**Root cause:** strategy has real edge; live loss is (1) 11-trade micro-sample in adverse cluster, (2) exit geometry — 5.0 ATR TP never reached live (TP dist 2.5-6.4%, realized moves all <2.2%), so winners get scratched by trail while losers hit full 2.0 ATR SL.

**Backtestable fix:** TP 5.0->3.0 raises TP-hits 7->13, WR 50->53%, PF holds 1.71. Trail 2.5 marginally beats 3.0 (PF 1.80). Widening SL HURTS (SL2.5 PF 1.59, SL3.0 PF 1.53) — strategy needs tight 2.0 SL. Verdict: KEEP strategy, retune TP down to 3.0, let live sample grow to 15+ trades before judging.

**Why:** demonstrates live<->backtest divergence driven by sample size + far TP, not broken edge. **How to apply:** don't retire coins on <15 live trades when backtest is walk-forward-healthy.
