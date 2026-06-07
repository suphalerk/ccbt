---
name: arc-qa-2026-06-06
description: QA verification of ARCUSDT EMA-15m proposed fixes (TP retune) — DEPLOY verdict, walk-forward stable
metadata:
  type: project
---

QA-verified ARCUSDT EMA crossover 15m (config_arcusdt_ema.json) proposed fixes on 2026-06-06.

**Verdict: TP retune fixes DEPLOY. Do NOT retire (keep validated).**

Backtest facts (arcusdt 15m/1h, data 2025-01-17 to 2026-03-21 = ~14mo, passes 12mo min):
- BASELINE TP5.0/SL2.0/trail3.0: PF 1.70, WR 50%, 36 trades, DD 4.1%, OOS/IS 221%
- FIX1 TP3.0: PF 1.71, WR 53%, 36 tr, OOS/IS 167%
- FIX1b TP3.0+trail2.5: PF 1.81 (best), WR 53%, 36 tr, OOS/IS 173% — best risk-adjusted
- FIX2 trail2.5 only: PF 1.80, OOS/IS 229%
- All survive 2x friction (PF >= 1.62)

**Why:** Live -$49.73 over 11 trades was variance + exit-geometry (5.0 ATR TP never hit live; winners scratched by trail). All checks pass: trades>=15, walk-forward OOS>=60% IS, fees/slippage modeled.

**How to apply:** TP-tightening on ARC is safe — does not curve-fit (PF flat-to-better across TP3-5). Wider SL HURTS (SL2.5 PF 1.59, SL3.0 PF 1.53) — confirms diagnosis root_cause #4. Note: trades.db logs ALL ARC exits as close_reason 'sl' even trailing-stop scratches — close_reason granularity is poor, don't infer TP-never-hit purely from 'sl' count without checking pnl sign.
