---
name: Gold Ichimoku Strategy Implementation
description: PM delegation plan for adding Ichimoku Cloud strategy to gold (XAU/USD) OANDA trading bot
type: project
---

Gold Ichimoku strategy implementation delegated to team on 2026-03-20.

**Why:** Research confirmed Ichimoku + Trail + Hours(8-20) yields PF 2.02, +46%/yr, DD 8.8% on XAU/USD 1H data — a strong, backtest-validated edge worth implementing. OANDA wrapper (bot/forex_exchange.py) already exists so infrastructure risk is low.

**Key design decisions:**
- Ichimoku indicators go in bot/data.py (add_ichimoku_indicators function)
- Signal logic goes in bot/strategy.py (check_ichimoku_conditions + new signal_source="ichimoku_cloud")
- Gold config lives in config_gold.json (separate from crypto config, OANDA exchange, XAU_USD symbol)
- Trading hours 8-20 UTC enforced via existing trading_hours config key
- No fixed TP — trailing stop only (3.0×ATR), SL at 2.5×ATR
- Long-only variant available if short has worse live performance

**How to apply:** When reviewing implementation PRs, verify: (1) Ichimoku warmup period ≥52 bars enforced before signals emit, (2) Cloud signals use iloc[-2] (last closed candle), (3) trailing stop path in strategy.py reuses compute_trailing_stop() unchanged, (4) config_gold.json keys match what add_ichimoku_indicators() reads.
