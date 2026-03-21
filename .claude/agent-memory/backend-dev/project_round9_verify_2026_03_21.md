---
name: Round 9 Verification and Portfolio Backtest (2026-03-21)
description: Round 9 engine verification — 14/77 new coins pass; portfolio grows to $3,131 (+1466%) from $200 with 61 bots
type: project
---

Round 9 verification completed 2026-03-21. Script: `research/round9_verify_and_backtest.py`.

**Why:** Expand portfolio with new strategies (ADX+DI Cross, Choppiness+EMA, WilliamsR+ADX, ROC Momentum, Stoch+Supertrend, PriceChannel+Vol, EMA+Alligator, Supertrend+Volume) from sweep_round9.json.

**Results summary:**
- 77 new coins evaluated (1 non-ASCII coin skipped before script)
- 58 had engine-compatible strategies (19 SKIP_NOT_IMPL: CMF, TRIX, LinReg, HeikinAshi, Donchian, Vortex, KAMA, DEMA, RSI+Ichimoku)
- **14 PASS, 44 FAIL** (pass = engine_pf >= 1.2 AND trades >= 4)

**14 verified new coins:**
| Coin | Strategy | TF | Engine_PF | WR% | Trades |
|------|----------|----|-----------|-----|--------|
| APR | PriceChannel+Vol | 4H | 98.10 | 75% | 4 |
| ZRO | ADX+DI Cross | 1H | 24.61 | 83% | 6 |
| ARIA | Choppiness+EMA | 1H | 13.83 | 83% | 6 |
| SAND | Choppiness+EMA | 1H | 6.79 | 75% | 4 |
| CRCL | PriceChannel+Vol | 1H | 3.89 | 56% | 9 |
| ZEC | ADX+DI Cross | 1H | 2.95 | 75% | 4 |
| DEGO | Choppiness+EMA | 1H | 2.82 | 57% | 7 |
| TSLA | ROC Momentum | 1H | 2.72 | 65% | 17 |
| KAS | WilliamsR+ADX | 4H | 2.09 | 44% | 9 |
| RIVER | ROC Momentum | 1H | 1.78 | 44% | 34 |
| DASH | Supertrend+Volume | 4H | 1.56 | 25% | 4 |
| TON | ROC Momentum | 4H | 1.52 | 57% | 42 |
| DOT | Supertrend+Volume | 4H | 1.51 | 33% | 6 |
| XMR | ROC Momentum | 4H | 1.20 | 42% | 31 |

**Notable failures (high sweep PF but engine fails):**
- WAXP PF 14.75 sweep → 3.86 engine (only 2 trades, fail)
- ALICE PF 12.85 sweep → 0 trades (fail)
- PIPPIN PF 6.85 sweep → 0 trades (fail)
- BARD PF 6.09 sweep → 0 trades (fail)
- 1000BONK PF 4.78 sweep → inf engine but only 2 trades (fail)
- Many coins with PF inf or very high but < 4 trades = FAIL (too sparse)

**Combined portfolio (47 existing + 14 new = 61 bots):**
- $200 → $3,131 (+1,466%) over 1 year
- 665 total trades, 48.1% WR
- Top performers: POL (+$232), TRX (+$205), GUN (+$204), TSLA (+$194), ATH (+$165), RIVER (+$134)
- March 2026 was exceptional: +$1,125 (69 trades, 62% WR)

**Data saved:** `data/round9_verified.json`, `data/portfolio_full.json`

**How to apply:** When deploying new bots, use the 14 verified coins with 1% risk. APR/ZRO/ARIA have very high engine PF but low trade counts — treat as higher volatility bots. TSLA/RIVER/TON are highest-volume new additions (17, 34, 42 trades/yr).
