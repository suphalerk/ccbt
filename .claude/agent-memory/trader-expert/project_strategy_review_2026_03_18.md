---
name: Strategy Review Baseline - March 2026
description: Comprehensive strategy review findings covering signal quality, risk/reward, regime adaptation, and profitability edge analysis
type: project
---

Completed first comprehensive strategy review of the CCBT codebase on 2026-03-18. Key findings:

1. RSI ranges are hardcoded with magic offsets (+3, +10, -20, -13) in check_entry_conditions rather than being config-driven
2. Slippage rate inconsistency: config.json has 0.0002, but backtest engine uses 0.0015 (7.5x higher)
3. Trailing stop uses static ATR from entry time, never updates ATR as market evolves
4. No partial take-profit mechanism - all-or-nothing TP/SL
5. Volume filter uses 1.0x multiplier (effectively no filter)
6. No time-based filters (weekend low liquidity, high-impact news windows)
7. Dynamic risk factor has only 3 discrete steps (0.7, 0.85, 1.0) - should be continuous
8. Regime detection is purely ATR-based with HH/HL counting, no integration with funding/OI data

**Why:** These are the highest-impact areas for improving expected value per trade.
**How to apply:** Prioritize fixes by expected profitability impact. P0 items (slippage inconsistency, RSI config) should be addressed before testnet validation.
