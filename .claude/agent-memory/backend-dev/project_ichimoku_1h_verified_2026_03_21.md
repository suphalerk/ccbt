---
name: Ichimoku 1H full-engine verification results (2026-03-21)
description: Full BacktestEngine verification of Ichimoku 1H on 10 altcoins — confirmed 4 profitable coins, configs created
type: project
---

Ran full BacktestEngine (not lightweight simulator) on Ichimoku 1H across 10 coins. Results differ materially from mega sweep: ARB (was PF 1.27) and XRP (was PF 1.17) are now clearly unprofitable.

**Why:** Full engine includes realistic commission, slippage (time-of-day adjusted), regime filter, and trailing stop — lightweight simulator lacked all of these.

**How to apply:** Only deploy the 4 confirmed coins below. Do not trust mega sweep PF figures for ARB/XRP/WIF/LINK/OP.

## Confirmed Profitable (PF >= 1.15)

| Coin | Best SL/TP | Trades/yr | WR% | PF | DD% | Sharpe | Config |
|------|-----------|-----------|-----|-----|-----|--------|--------|
| AVAX | SL2.0/TP5.0 | 13 | 44% | 1.93 | 7.4% | 1.05 | config_avax_ichi.json |
| NEAR | SL2.5/TP5.0 | 13 | 48% | 1.84 | 7.6% | 1.03 | config_near_ichi.json |
| SOL  | SL1.5/TP4.0 | 11 | 46% | 1.63 | 11.8% | 1.03 | config_sol_ichi.json |
| BTC  | SL2.0/TP6.0 | 15 | 35% | 1.40 | 14.2% | 0.60 | config_btc_ichi.json |

Notes:
- AVAX/NEAR use 2yr data only; SOL/BTC use 5yr data — AVAX/NEAR results could be sample-size limited (25 trades each)
- BTC Ichimoku 1H (PF 1.40) is weaker than BTC EMA 15m strategy — prefer config.json for BTC
- All configs use risk_per_trade=0.02 (conservative for multi-coin portfolio)
- Portfolio generates ~51 trades/yr combined (0.14/day) — very low frequency

## Failed Coins (PF < 1.10 on all configs)

ARB (best PF 0.59), XRP (0.55), LINK (0.72), DOGE (1.02), OP (1.11), WIF (1.03)

## Key config parameters
- ichimoku_tenkan=9, ichimoku_kijun=26, ichimoku_senkou_b=52 (standard)
- min_rr_ratio=0 (cloud quality is the gate, not R:R)
- volume_mult=1.0 (no volume filter — reduces trades too much on 1H)
- atr_min=0.0
- trading_hours 03-20 UTC, no weekends, regime_filter skip_ranging=true

## Verification script
`/Users/iceai/Work/ccbt/research/test_ichimoku_1h_verify.py`
