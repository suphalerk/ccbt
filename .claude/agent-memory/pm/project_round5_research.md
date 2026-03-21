---
name: Round 5 Research Plan
description: Round 5 — 3 new techniques: Volatility Expansion Breakout, Multi-TF Confluence, Regime-Adaptive Exit (2026-03-21)
type: project
---

Round 5 research started 2026-03-21.

**Context:** Portfolio 29 bots, 731 trades, PF 1.77, Sharpe 4.49, DD 6.4%, $200→$1,240 (+520%).
5 strategies deployed: EMA 15m, Ichimoku 1H, Ichimoku 4H, 4H Trail, Supertrend.

**Goal:** Find more coins + improve deployed PF using 3 novel techniques from trading skills:
1. Volatility Expansion Breakout (ATR expansion signal) — find NEW coins on 1H + 4H
2. Multi-TF Confluence (15m entry + 1H trend + 4H Ichimoku) — improve deployed + rejected coins
3. Regime-Adaptive Exit (dynamic TP/Trail based on detected regime) — improve existing bots

**Why:** All 3 are fundamentally different from existing strategies. No look-ahead bias risk. Each tests on full 2yr dataset.

**How to apply:** Track results in sweep_vol_expansion_winners.json, sweep_mtf_confluence_winners.json, and regime_adaptive_results.json. Full BacktestEngine verify for any coin passing PF>=1.3, trades>=10.
