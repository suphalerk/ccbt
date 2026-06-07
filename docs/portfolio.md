# Deployed Portfolio

> Bot roster + deployment profiles. See [strategies.md](strategies.md) for how each strategy works and [research-history.md](research-history.md) for discovery history.

## Deployed Portfolio (engine-verified backtests)

### Strategy 1: EMA Crossover 15m (2 bots)
| Coin | Config | Risk | PF | WR% | Tr/yr | Sharpe | Scorer |
|------|--------|------|-----|-----|-------|--------|--------|
| **BTC** | `config.json` | 5% | 1.41 | 42% | 24 | 0.91 | Funding ON |
| **WIF** | `config_wif.json` | 3% | 1.71 | 42% | 36 | 1.46 | Funding ON |

### Strategy 2: Ichimoku Cloud 1H (8 bots)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% | Notes |
|------|--------|-----|-----|-------|--------|-----|-------|
| **AVAX** | `config_avax_ichi.json` | 2.49 | 45% | 11 | 1.28 | 3.6% | |
| **POL** | `config_polusdt_ichi.json` | 6.79 | 50% | 14 | 2.13 | 2.4% | SL1.0/Trail3.0 |
| **GUN** | `config_gunusdt_ichi.json` | 4.00 | 65% | 17 | 2.52 | 2.0% | |
| **BERA** | `config_berausdt_ichi.json` | 2.92 | 55% | 20 | 1.92 | 2.0% | |
| **ATH** | `config_athusdt_ichi.json` | 2.51 | 47% | 17 | 1.65 | 2.6% | |
| **INJ** | `config_injusdt_ichi.json` | 2.28 | 44% | 9 | 0.93 | 2.6% | |
| **TRUMP** | `config_trumpusdt_ichi.json` | 1.66 | 53% | 19 | 0.87 | 3.5% | |
| **ANIME** | `config_animeusdt_ichi.json` | 1.51 | 47% | 17 | 0.74 | 5.1% | |

### Strategy 3: EMA 15m (mass expansion, 1% risk)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **ARC** | `config_arcusdt_ema.json` | 1.59 | 47% | 32 | 1.06 | 4.1% |

### Strategy 4: 4H Ichimoku Fixed TP (5 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **1000SHIB** | `config_1000shibusdt_ichi4h.json` | 18.43 | 75% | 4 | 2.66 | 0.2% |
| **TAO** | `config_taousdt_ichi4h.json` | 6.13 | 67% | 9 | 2.77 | 0.7% |
| **RENDER** | `config_renderusdt_ichi4h.json` | 4.71 | 57% | 7 | 1.86 | 1.0% |
| **ARB** | `config_arbusdt_ichi4h.json` | 4.38 | 50% | 4 | 1.72 | 0.2% |

### Strategy 5: 4H Ichimoku Trailing Exit (6 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **ALGO** | `config_algousdt_ichi4htrail.json` | 7.21 | 50% | 4 | 1.32 | 2.0% |
| **TRX** | `config_trxusdt_ichi4htrail.json` | 5.53 | 50% | 4 | 1.31 | 1.6% |
| **POLYX** | `config_polyxusdt_ichi4htrail.json` | 5.13 | 75% | 8 | 1.78 | 1.2% |
| **FET** | `config_fetusdt_ichi4htrail.json` | 3.03 | 33% | 6 | 0.93 | 1.4% |
| **XLM** | `config_xlmusdt_ichi4htrail.json` | 2.88 | 25% | 4 | 0.76 | 2.1% |
| **SAHARA** | `config_saharausdt_ichi4htrail.json` | 2.44 | 60% | 5 | 0.95 | 0.7% |

### Strategy 6: Supertrend 1H (2 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **MSTR** | `config_mstrusdt_supertrend.json` | 2.66 | 64% | 11 | 4.63 | 1.9% |
| **XAG** | `config_xagusdt_supertrend.json` | 2.09 | 44% | 9 | 2.47 | 2.0% |

### Strategy 7: Vol Expansion Breakout 1H (2 bots, 1% risk each)
| Coin | Config | PF | WR% | Tr/yr | Sharpe | DD% |
|------|--------|-----|-----|-------|--------|-----|
| **1000PEPE** | `config_1000pepeusdt_volexp.json` | 7.23 | 70% | 10 | 2.92 | 0.6% |
| **WLD** | `config_wldusdt_volexp.json` | 3.45 | 75% | 4 | 1.40 | 1.0% |

### Strategy 8: EMA+Ichimoku Hybrid 4H (3 bots, 1% risk each)
Signal: EMA crossover confirmation + Ichimoku cloud filter on 4H. SL 1.5 ATR, TP 4.0 ATR.
| Coin | Config | PF | Notes |
|------|--------|----|-------|
| **APT** | `config_aptusdt_emaichi4h.json` | 8.17 | |
| **NEAR** | `config_near_ichi.json` | 4.06 | upgraded from Ichi 1H |
| **ZETA** | `config_zetausdt_ichi.json` | 3.96 | upgraded from Ichi 1H |

### Strategy 9: Ichi+Supertrend Hybrid 4H (1 bot, 1% risk)
Signal: Ichimoku Tenkan/Kijun cross + Supertrend direction agreement on 4H. SL 2.0 ATR, TP 5.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **LTC** | `config_ltcusdt_ichist4h.json` | 6.90 |

### Strategy 10: Alligator 1H (1 bot, 1% risk)
Signal: Williams Alligator jaw/teeth/lips alignment + price above/below all three lines. SL 2.0 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **LIGHT** | `config_lightusdt_alligator.json` | 5.16 |

### Strategy 11: Alligator 4H (5 bots, 1% risk each)
Signal: Williams Alligator on 4H. SL 2.0 ATR, TP 4.0 ATR.
| Coin | Config | PF | Notes |
|------|--------|----|-------|
| **HBAR** | `config_hbarusdt_ichi4h.json` | 4.23 | upgraded from Ichi 4H |
| **QNT** | `config_qntusdt_alligator4h.json` | 3.49 | |
| **ARC** | `config_arcusdt_ichi.json` | 3.15 | upgraded from Ichi 1H |
| **LINK** | `config_linkusdt_alligator4h.json` | 1.66 | |
| **SUI** | `config_suiusdt_alligator4h.json` | 1.58 | |

### Strategy 12: Dual Supertrend 1H (2 bots, 1% risk each)
Signal: Two Supertrend indicators (different ATR multipliers) must agree on direction. SL 2.5 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **LYN** | `config_lynusdt_dualst.json` | 3.11 |
| **HUMA** | `config_humausdt_dualst.json` | 1.49 |

### Strategy 13: Dual Supertrend 4H (4 bots, 1% risk each)
Signal: Two Supertrend indicators agree on 4H. SL 2.5 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **ENJ** | `config_enjusdt_dualst4h.json` | 2.35 |
| **XPL** | `config_xplusdt_dualst4h.json` | 1.90 |
| **XRP** | `config_xrpusdt_dualst4h.json` | 1.59 |
| **AKT** | `config_aktusdt_dualst4h.json` | 1.56 |

### Strategy 14: Ichimoku Cloud 4H — new coins (6 bots, 1% risk each)
Signal: Ichimoku Tenkan/Kijun cross above/below cloud on 4H. SL 2.0 ATR, TP 4.0 ATR.
| Coin | Config | PF |
|------|--------|----|
| **SIGN** | `config_signusdt_ichi4h.json` | 2.43 |
| **TIA** | `config_tiausdt_ichi4h.json` | 2.08 |
| **ONDO** | `config_ondousdt_ichi4h.json` | 1.77 |
| **H** | `config_husdt_ichi4h.json` | 1.51 |
| **AXS** | `config_axsusdt_ichi4h.json` | 1.31 |

### Strategy 15: Ichimoku Cloud 1H — new coins (2 bots, 1% risk each)
| Coin | Config | PF |
|------|--------|----|
| **IP** | `config_ipusdt_ichi.json` | 1.84 |

### Strategy 16: ADX+DI Crossover 1H (Round 9, 1% risk)
Signal: DI+/DI- cross + ADX > 20 rising. SL 2.0 ATR, TP 4.0 ATR.
| Coin | PF | WR% | Trades |
|------|----|-----|--------|
| **ZRO** | 24.61 | 83% | 6 |
| **ZEC** | 2.95 | 75% | 4 |

### Strategy 17: Choppiness+EMA 1H (Round 9, 1% risk)
Signal: CI < 38.2 (trending) + EMA(9/21) crossover. SL 2.0 ATR, TP 4.0 ATR.
| Coin | PF | WR% | Trades |
|------|----|-----|--------|
| **ARIA** | 13.83 | 83% | 6 |
| **SAND** | 6.79 | 75% | 4 |
| **DEGO** | 2.82 | 57% | 7 |

### Strategy 18: ROC Momentum 1H/4H (Round 9, 1% risk)
Signal: ROC(10) zero-cross + EMA(50) trend. SL 2.0 ATR, TP 4.0 ATR.
| Coin | TF | PF | WR% | Trades |
|------|----|----|-----|--------|
| **TSLA** | 1H | 2.72 | 65% | 17 |
| **RIVER** | 1H | 1.78 | 44% | 34 |
| **TON** | 4H | 1.52 | 57% | 42 |
| **XMR** | 4H | 1.20 | 42% | 31 |

### Strategy 19: Other Round 9 strategies (1% risk each)
| Coin | Strategy | TF | PF | WR% | Trades |
|------|----------|----|-----|-----|--------|
| **APR** | PriceChannel+Vol | 4H | 98.10 | 75% | 4 |
| **CRCL** | PriceChannel+Vol | 1H | 3.89 | 56% | 9 |
| **KAS** | Williams %R+ADX | 4H | 2.09 | 44% | 9 |
| **DASH** | Supertrend+Vol | 4H | 1.56 | 25% | 4 |
| **DOT** | Supertrend+Vol | 4H | 1.51 | 33% | 6 |

### Strategy 20: Dual Thrust (34 bots, 1% risk each, R10+R12)
Signal: Previous-day high/low range × multiplier sets breakout levels above/below open. Entry on breakout. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: GALA (PF 7.84), PHA (PF 4.39), ZEC (PF 3.41), DOT (PF 7.77), AVAX (PF 3.23), ARC (PF 2.50)

### Strategy 21: Range Bounce (19 bots, 1% risk each, R10)
Signal: Bollinger Band %B < 0.2 (oversold) or > 0.8 (overbought) + RSI reversal + range detection filter (CI > 50). SL 2.0 ATR, TP 3.0 ATR.
Top verified coins: AXS (PF 13.46, 92% WR), FIL (PF 3.25)

### Strategy 22: Awesome Oscillator (18 bots, 1% risk each, R10)
Signal: AO zero-cross + Ichimoku cloud direction filter on 4H. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: VVV (PF 2.81), SAND (PF 2.88), ALICE (PF 1.97)

### Strategy 23: Z-Score Mean Reversion (19 bots, 1% risk each, R11)
Signal: Statistical z-score of price vs rolling 20-period mean crosses ±1.5σ threshold + EMA(50) filter prevents counter-trend trades. SL 2.0 ATR, TP 3.0 ATR.
Top verified coins: AKT (PF 2.94), CRV (PF 5.82), VVV (PF 6.17), PENGU (PF 6.01), POL (PF 3.57)

### Strategy 24: Stoch MTF (8 bots, 1% risk each, R11)
Signal: Stochastic crossover on signal timeframe confirmed by same direction on higher timeframe. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: AXS (PF 5.86)

### Strategy 25: EMA Ribbon (11 bots, 1% risk each, R11+R12)
Signal: Fan of 6 EMAs (8/13/21/34/55/89) must all align in same direction; entry when fast EMA crosses slow EMA. SL 2.0 ATR, TP 4.0 ATR.
Top verified coins: IP (PF 2.45), OP (PF 1.95), AVAX (PF 2.10), KAS (PF 1.95), BERA (PF 2.06)

### Strategy 26: Ichi+ADX (5 bots, 1% risk each, R12)
Signal: Ichimoku Tenkan/Kijun cross with ADX > 20 confirmation for trend strength. SL 2.0 ATR, TP 4.0 ATR.

### Strategy 27: Ribbon+AO (3 bots, 1% risk each, R12)
Signal: EMA Ribbon alignment + Awesome Oscillator momentum direction agreement. SL 2.0 ATR, TP 4.0 ATR.

### Retired (no edge found in 1yr backtest)
| Coin | Was | Best PF | Reason |
|------|-----|---------|--------|
| **DOGE** | EMA 15m | 1.11 | All strategies PF < 1.2 |
| **SOL** | Ichi 1H | 1.17 | All strategies PF < 1.2 |

### Live tuning — losing-coins workflow (2026-06-07)
Diagnosed 6 net-losing testnet coins (workflow `losing-coins-diagnosis`, backtest-verified). Configs removed from `deploy/macos/start.sh`:
| Coin | Action | Reason |
|------|--------|--------|
| **ENJ** | retire `awesome`(1H PF 0.93) + `rangebounce`(0.91) → keep **Dual Thrust** | wrong-TF / fades trend |
| **TON** | retire `awesome` + `rangebounce` → **drops out** | AO on 1H (validated 4H); AO-4H = needs data |
| **ALICE** | retire `awesome` → **drops out** | sole strat, AO 1H PF 0.77 |
| **TRUMP** | retire `stochmtf`(6tr) + `zscore`(1tr) + `ichi`(walk-fwd IS 0.93) → keep **EMA Ribbon** | <15-trade gate / unstable |
| **1000SHIB** | retire `zscore`(3tr); retune Ribbon **SL 2.0→3.0 ATR** | no edge / SL premature-stopped 70% |
| **ARC** | keep EMA 15m; retune **TP 5.0→3.0, trail 3.0→2.5 ATR** | genuine edge, TP unreachable (R:R 0.63) |

**ICP** (`dualthrust`/`emaribbon`/`rangebounce`) retired 2026-06-07 — Binance testnet lists ICP as SPOT only, no USDT perp (crashed every restart).

**Cross-coin finding (pending):** all 18 `config_*_awesome.json` ship on 1H but Awesome Oscillator was validated on 4H → systemic edge loss. Audit pending — see docs/plans / memory.

**Deployed: 59 configs (single process via `main_multi.py`, ~340 MB) through a DigitalOcean SOCKS5 proxy + shared market-data cache.**

### Walk-Forward Audit Status
- **96 PASS** — IS/OOS ratio >= 60%, full PF >= 1.2, stable edge
- **29 FAIL** — removed (full PF < 1.2 or OOS degraded > 40%)
- Key failures: GALA/QNT/DEGO/W/ZEC dual_thrust, CFX all strategies, VVV/TIA range_bounce
- Audit scripts: `research/audit_btc_wif.py`, `research/audit_43_bots.py`

## Main Setting (Target: Realistic 1000%/yr)
```
Max concurrent positions: 10
Risk per trade:           0.9%
Starting capital:         $200+ (recommend $500+ for min order sizes)
Leverage:                 25x
Active bots:              96 (walk-forward audited only)
```

## Deployment Profiles (5 levels)
```
testnet   → fake, 96 bots, 1%, 25x, max 10 concurrent
                  ↓ testnet OK 1 week
real-test → $45, 5 bots, 3%, 10x, max 2 concurrent
                  ↓ 30+ trades, WR > 35%
real-safe → $200+, 10 bots, 1%, 10x, max 5 concurrent
                  ↓ 50+ trades, DD < 20%
real-grow → $500+, 50 bots, 0.9%, 15x, max 10 concurrent
                  ↓ 100+ trades, DD < 25%
real-full → $1K+, 96 bots, 0.9%, 25x, max 10 concurrent
```

| Profile | Capital | Risk | Leverage | Bots | MaxConc | Upgrade Condition |
|---------|---------|------|----------|------|---------|-------------------|
| **testnet** | fake | 1% | 25x | 96 | 10 | — |
| **real-test** | $45 | 3% | 10x | 5 | 2 | testnet OK 1 week |
| **real-safe** | $200+ | 1% | 10x | 10 | 5 | 30+ trades, WR > 35% |
| **real-grow** | $500+ | 0.9% | 15x | 50 | 10 | 50+ trades, DD < 20% |
| **real-full** | $1K+ | 0.9% | 25x | 96 | 10 | 100+ trades, DD < 25% |

### Backtest Results per Profile
| Profile | Capital | Backtest Return | DD | Trades | Realistic est. |
|---------|---------|----------------|-----|--------|---------------|
| **real-test** V2 | $45 | +348% | 17.4% | 208 | +100-175%/yr |
| real-safe | $200 | +617% | 9.7% | 631 | +185-310%/yr |
| real-grow | $500 | +3,339% | 20.5% | 1,643 | +1,000-1,670%/yr |
| **real-full** (Main) | $1,000 | +2,099% | 23.9% | 2,200 | ~840%/yr |

### real-test V2 Bots (5 bots — mainnet first deployment)
| Bot | Strategy | TF | PF | WR% | $/trade |
|-----|----------|----|-----|------|---------|
| DASH | Ichimoku Cloud | 4H | 7.01 | 71% | $14 |
| BTC | EMA Crossover | 15m | 1.38 | 38% | $10 |
| XAUUSD | Ribbon+AO | 4H | 2.97 | 59% | $10 |
| AVAX | EMA Ribbon | 4H | 3.00 | 50% | $8 |
| 1000SHIB | EMA Ribbon | 1H | 1.21 | 39% | $5 |

```bash
# Testnet: multi-bot mode (17 containers, ~4GB RAM)
docker compose -f docker-compose-multi.yml up -d

# Dashboard
streamlit run dashboard/app.py
```
