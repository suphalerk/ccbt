# Research History (Archival)

> Round-by-round research log. Archival — what was tested, what worked, what failed. See [strategies.md](strategies.md) for the strategies that survived and [portfolio.md](portfolio.md) for the current roster.

## Research Findings (300+ backtests)
- Price Action: fails on all coins/timeframes (avg PF 0.73-0.85)
- BTC 5m: fails for ALL strategies (oracle max PF 1.10)
- EMA on ETH/SOL/ADA: fails (PF <1.04) — EMA edge is BTC + meme specific
- Ichimoku 1H: works on AVAX/NEAR/SOL (PF 1.69-1.95)
- Funding rate: improves BTC PF +10%, WIF +27%, but destroys DOGE (-94%)

## Mass Expansion v2 Research (March 2026)
- **4H Ichimoku: NEW strategy** — resampling 1H→4H unlocks large-cap coins that fail at 1H
- **4H Trailing exit** — trail >> fixed TP for trending 4H coins (ALGO PF 5.84, FET PF 2.87)
- **Supertrend implemented in engine** — ATR-adaptive bands, works on MSTR PF 2.66, XAG PF 2.09
- Long-only Ichimoku: works in sweep but BacktestEngine doesn't support long-only flag yet
- Fast Ichimoku (7/22/44): marginal improvement on some coins
- Momentum ROC: few winners, not reliable on crypto
- Adaptive SL/TP: found candidates but most failed full engine verification
- EMA(12/26): minimal improvement over 9/21
- Heikin-Ashi + EMA: mostly stock/commodity tickers, few crypto winners
- Keltner Channel: high trade count but low PF in full engine
- 6 undeployed sweep winners REJECTED: all had < 6 months data (CRCL/GUA/BEAT/ENSO/KITE/UAI)
- **4 rounds of research**: 7→19→22→26→29 bots, PF 1.68→1.77, DD 8.3%→6.4%

## Round 5 Research (March 2026)
- **Vol Expansion Breakout: NEW strategy** — ATR > rolling_mean × 1.8 + price breakout + EMA(50) trend filter
- **439 sweep winners** across 43 unique coins (1H + 4H), largest new technique pool
- Engine-verified: 1000PEPE PF 10.85 (80% WR), WLD PF 3.25 (73% WR) → deployed
- STO rejected (only 0.9yr data), SAND failed engine (PF 1.19), ASTER/HYPE failed (PF <1.3)
- MTF Confluence: 92 winners but hurts deployed coins, only SIREN/PIPPIN new (not verified)
- Regime-Adaptive Exit: infrastructure built (entry-time regime locks TP/trail), default params don't help (PF 1.77→1.60)
- **5 rounds of research**: 7→19→22→26→29→31 bots, PF 1.77→2.06, DD 6.4%→1.9%

## Round 6 Research — Weak Bot Optimization (March 2026)
- **1yr backtest all 31 bots**: 30 profitable, 1 losing (POL PF 0.37)
- **6-strategy sweep on 8 weak bots** (PF < 1.3): tested EMA/Ichi1H/Ichi4H/4HTrail/Supertrend/VolExp
- **6 replacements found**:
  - POL: Ichi4H Trail → Ichi 1H Trail (PF 0.37→6.79, SL1.0/Trail3.0)
  - TRX: Ichi 1H → 4H Trail (PF 1.01→5.53, SL1.5/Trail4.0)
  - ARB: EMA 15m → Ichi 4H (PF 1.03→4.38, SL3.0/TP4.0)
  - 1000SHIB: Ichi 1H → Ichi 4H (PF 1.06→18.43, SL3.0/TP5.0)
  - SAHARA: Supertrend → 4H Trail (PF 1.27→2.44, SL3.0/Trail4.0)
  - XLM: Ichi 1H → 4H Trail (PF 1.31→2.88, SL1.0/Trail4.0)
- **2 bots RETIRED** (no strategy PF > 1.2): DOGE (best 1.11), SOL (best 1.17)
- **Dashboard overhauled**: multi-bot portfolio view, per-bot drill-down, auto-config discovery
- **Binance leverage fix**: auto-halving retry on -4028 rejection
- **6 rounds of research**: 7→19→22→26→29→31→29 bots (net -2), portfolio PF improved

## Round 7 Research — 20 New Strategies Mega Sweep (March 2026)
- **20 brand new strategies** designed and swept across **133 coins × 2 timeframes (1H + 4H)**
- **3,935 backtests** completed (2yr data), **760 winners** (PF >= 1.3, trades >= 6)
- **80 new coin candidates** found (not already deployed, PF >= 1.5, data > 1yr)
- **5 new strategy types proven on crypto:**
  1. **Dual Supertrend 4H** — fast(7,2.0) + slow(14,3.0) agreement → 15 new coins, 52% hit rate
  2. **VolExp+Supertrend 1H** — vol expansion + supertrend direction confluence → 10 new coins, avg PF 1.47
  3. **Alligator 4H** — Bill Williams 3-SMMA (jaw=13,teeth=8,lips=5) → 9 new coins, LIGHT PF 4.99
  4. **EMA+Ichimoku 4H** — EMA(9/21) cross + cloud direction filter → 7 new coins, VVV PF 5.12
  5. **Ichi+Supertrend 4H** — Ichimoku cloud + Supertrend entry → 6 new coins, PIPPIN PF 7.09
- **Top new coin×strategy candidates (pending engine verification):**
  - PIPPIN Ichi+ST 4H PF 7.09, LYN Dual ST 1H PF 5.79, ATOM VolExp+ST PF 5.38
  - VVV EMA+Ichi 4H PF 5.12, LIGHT Alligator 1H PF 4.99, XRP Dual ST 4H PF 3.13
- **Strategies that FAILED on crypto:**
  - RSI 50-Cross 1H (PF 0.94), PSAR Flip 1H (PF 0.95), StochRSI 1H (PF 0.95)
  - BTC Cross-Coin Leader (PF 0.96), Regime Transition 1H (PF 0.88)
  - Elder Impulse 1H (PF 0.97), CCI 1H (PF 0.97)
- **Key insight**: 4H consistently outperforms 1H for all new strategies (noise reduction)
- Scripts: `research/sweep_new_strategies_1.py` (S1-S10), `research/sweep_new_strategies_2.py` (S11-S20)
- Results: `data/sweep_new_strats_1.json`, `data/sweep_new_strats_2.json`

## Round 8 Research — Engine Verification + Deploy (March 2026)
- **Full BacktestEngine verification** of 80 sweep winners: Tier A+B 14/46 pass (30%), Tier C+D 7/34 pass (21%)
- **VolExp+Supertrend FAILS full engine** — 0 trades on every coin (signal conditions too strict vs lightweight sweep)
- **18 new coins deployed**: APT, LTC, LIGHT, QNT, LYN, SIGN, ENJ, TIA, XPL, IP, ONDO, LINK, XRP, SUI, AKT, H, HUMA, AXS
- **4 existing coins upgraded**: NEAR/ZETA → EMA+Ichi 4H, HBAR/ARC → Alligator 4H
- **9 deployed coins tested × 5 new strategies**: BTC Dual ST 4H PF 1.71 (+21% vs EMA), but kept EMA as flagship
- **Portfolio backtest ($200 shared wallet, 1yr)**: 528 trades, PF 1.75, $200→$1,794 (+797%), 12/13 months profitable
- **5 new signal types implemented in engine**: dual_supertrend, alligator, ema_ichimoku_hybrid, ichi_supertrend, volexp_supertrend
- **Auto-research script**: `research/auto_research.py` — one-command pipeline (sweep→verify→deploy)
- **8 rounds of research**: 7→19→22→26→29→31→29→47 bots, $200→$1,794 in 1yr

## Round 9 Research — 20 More New Strategies (March 2026)
- **20 brand new strategies** swept across 129 coins × 2 timeframes = **5,160 backtests**
- **799 winners** (PF >= 1.3, trades >= 6), **78 new coin candidates**
- **Top new strategy types:**
  1. **ADX+DI Crossover** — DI+/DI- cross with ADX rising, avg PF 1.58, 43% hit rate, **12 new coins**
  2. **Choppiness Index + EMA** — CI < 38.2 (trending) + EMA cross, avg PF 1.44, 40% hit rate, **9 new coins**
  3. **Williams %R + ADX** — %R extremes in trending market, **8 new coins** (ATOM PF 2.73, BCH PF 2.15)
  4. **Stochastic + Supertrend** — Stoch timing + ST direction, **6 new coins** (PENGU PF 3.02)
  5. **ROC Momentum** — ROC zero-cross + trend, **7 new coins** (RIVER PF 2.55, TON PF 1.73)
- **Deployed coin upgrades found:**
  - XAG: Supertrend → Donchian+ADX 4H (PF 2.09→15.58)
  - MSTR: Supertrend → Keltner+ADX 1H (PF 2.66→7.45)
  - ANIME: Ichi 1H → ADX+DI Cross 1H (PF 1.51→6.16)
  - AXS: Ichi 4H → Choppiness+EMA 4H (PF 1.31→4.08)
- **Strategies that work**: ADX+DI, Choppiness+EMA, EMA+Alligator, Supertrend+Volume, PriceChannel+Vol
- **Strategies mediocre**: Hull MA Cross (PF 0.98), KAMA Cross (PF 1.01), OBV Breakout (PF 1.04)
- **Engine verification**: 14/78 PASS (18%), 19 skipped (strategy not in engine), 44 FAIL
- **8 new signal types implemented**: adx_di_cross, choppiness_ema, williams_r_adx, roc_momentum, stoch_supertrend, price_channel_vol, ema_alligator, supertrend_volume
- **14 new coins engine-verified**: ZRO, ARIA, SAND, CRCL, ZEC, DEGO, TSLA, KAS, RIVER, TON, DASH, DOT, APR, XMR
- **Backtest audit found critical bugs**: double compounding (pnl/initial instead of pnl/current), no concurrent limit, loose data filters
- **Corrected portfolio (14 verified bots, $200 shared, max 5 concurrent)**: 320 trades, $200→$497 (+149%/yr), DD 8.1%
- **47/61 bots filtered out**: data < 12mo or trades < 10 — configs ready but need more data before live
- **9 rounds of research**: 7→19→22→26→29→31→29→47→61 bots (14 verified), $200→$497 realistic
- Script: `research/sweep_round9.py`, `research/round9_verify_and_backtest.py`

## Round 10 Research — GitHub-Inspired Strategies (March 2026)
- **5 strategies from top GitHub trading bots** (Freqtrade, je-suis-tm, OctoBot, NostalgiaForInfinity)
- Swept 54 coins × parameter grids (108 combos/coin) = thousands of backtests
- **ALL 5 strategies PASSED** — first time every strategy in a round has winners:
  1. **Dual Thrust** (je-suis-tm 4.5K stars) — range breakout, ZEN 4H PF 8.34, DOT PF 7.77
  2. **Kalman Filter Trend** (QuantConnect) — adaptive MA crossover, less lag than EMA
  3. **Awesome Oscillator** (je-suis-tm) — AO zero-cross, VVV 4H PF 2.81, 28 winners
  4. **Range Bounce** (OctoBot 4K stars) — ranging market bounces, AXS PF 13.46 (92% WR!), **45 winners**
  5. **Multi-Indicator Confluence** (NostalgiaForInfinity 2.9K stars) — 3-4/5 indicators agree, IP 4H PF 2.11
- **Key surprise**: Range Bounce (mean reversion variant) WORKS when gated by range detection + RSI
- **High-frequency strategies FAIL**: 10 scalping strategies (15m) tested, all PF 0.32-0.71 — fees kill edge
- Script: `research/sweep_github_inspired.py`, `research/sweep_highfreq.py`
- Results: `data/sweep_github.json`, `data/sweep_highfreq.json`
- **Engine verification**: 76/180 PASS. 3 strategies implemented (Dual Thrust, Awesome Oscillator, Range Bounce)
- **Top verified new coins**: GALA DualThrust PF 7.84, PHA DualThrust PF 4.39, ZEC DualThrust PF 3.41, FIL RangeBounce PF 3.25, SAND AwesomeOsc PF 2.88
- **Realistic backtest (62 bots, $200 shared, R-multiple, max 5 concurrent)**: $200→$1,106 (+453%), PF 1.20, DD 34%, 1,452 trades, 10/13 months profitable
- **10 rounds of research**: 7→19→22→26→29→31→29→47→61→62 bots

## Round 11 Research — FMZQuant/TradingView/Academic Strategies (March 2026)
- **20 strategies** from FMZQuant, TradingView, Connors, academic papers — 2,811 backtests, 78 coins
- **Top performers**: Stoch MTF (AXS PF 5.86), Z-Score MeanRev (AKT PF 2.94), EMA Ribbon (IP PF 2.45)
- **3 new strategies implemented in engine**: stoch_mtf, zscore_meanrev, ema_ribbon
- **Engine verification**: 36/87 PASS — zscore_meanrev 63% pass rate (best)
- **Turtle Trading FAILS** on crypto (avg PF 0.85)
- **Z-Score Mean Reversion WORKS** — first statistical mean reversion success with EMA50 filter
- **Realistic backtest (85 bots, $200 shared, R-multiple, max 5 concurrent)**:
  - **$200 → $1,579 (+689%)** | DD 26.1% | 1,465 trades | 11/13 months profitable
  - Best months: Nov +$358, Jan +$384, Mar +$198
  - Worst: Dec -$185
- **11 rounds total**: 7→19→22→26→29→31→29→47→61→62→85 bots
- Scripts: `research/sweep_round11.py`, `research/round11_verify_backtest.py`

## Round 12 Research — 100-Strategy Mega Sweep + Combo Functions (March 2026)
- **100 strategy COMBINATIONS** (2-3 indicators each) swept across 104 coins = **6,376 backtests**
- **744 winners** (PF >= 1.3, trades >= 10)
- **Top combos**: Ribbon+RSI+Vol (avgPF 1.44), DualThrust+ADX (32 winners), ZScore+Stoch (avgPF 1.28)
- **5 combo check functions implemented in engine**: ribbon_rsi_vol, dualthrust_adx, zscore_stoch, ichi_adx, ribbon_ao
- **Combo verification**: 23/120 PASS — DualThrust+ADX dominant (13 passes), Ichi+ADX (5), Ribbon+AO (3)
- **Key finding**: combo strategies (2 indicators AND) outperform single signals — ADX confirmation is the #1 edge booster
- **Pattern strategies FAIL**: Hammer, Engulfing, InsideBar all PF < 1.0 — candle patterns don't work on crypto
- **Multi-confluence (4-5 indicators) doesn't help**: too selective, fewer trades = worse compounding
- **Full combined portfolio (136 bots, $200 shared, R-multiple, max 5 concurrent)**:
  - **$200 → $1,790 (+795%)** | DD 23.0% | 1,541 trades | 10/13 months profitable
  - Best month: Nov +$617 | Worst: Dec -$188
  - Top contributors: BTC EMA +$471, BERA ema_ribbon +$306
- **12 rounds of research**: 7→19→22→26→29→31→29→47→61→62→85→136 bots, $200→$1,790 realistic

## Core Coins Retested + Audited (March 2026)
- **224 backtests**: 7 core coins × 32 strategies + OP/ZRO × 32 = 288 total
- **Confirmed upgrades (walk-forward stable, trades >= 18)**:
  - AVAX: Ichi 1H (PF 1.95) → **Dual Thrust 1H (PF 3.23, 31 trades)** — most reliable
  - ARC: EMA 15m (PF 1.89) → **Dual Thrust 1H (PF 2.50, 18 trades)**
  - OP: DualThrust → **EMA Ribbon 1H (PF 1.95, 24 trades)**
- **REJECTED after deep audit (walk-forward fail / small sample)**:
  - BTC Stoch MTF 1H: PF 2.96 but only 14 trades, walk-forward degrades 49% → **KEEP EMA 15m**
  - WIF DualThrust+ADX 1H: PF 2.45 but only 8 trades, last 3 trades all losses → **KEEP EMA 15m**
  - ZRO ADX+DI 1H: PF 21.22 but only 6 trades → **KEEP Dual ST 1H**
- **Keep current (confirmed best)**: BTC (EMA 15m), WIF (EMA 15m), GUN (Ichi PF 4.00), ATH (Ichi PF 2.51), 1000PEPE (VolExp PF 10.85), ZRO (Dual ST 1H)
- **Audit methodology**: signal verification (every trade), look-ahead check, fee check, walk-forward (first/second half), baseline comparison
- Scripts: `research/core7_new_strategies.py`, `research/audit_btc_wif.py`, `research/sweep_op_zro.py`

## Weak Bot Optimization — Parameter Sweep (March 2026)
- **5,940 backtests**: 30 weak coins × 10 strategies × 10 param sets × 2 TF
- **29/30 found improvements** in lightweight sweep (only BCH had no solution)
- **Engine verification**: 8/29 PASS — Z-Score 1H collapsed (overfitting), 4H strategies won
- **6 deploy-grade upgrades**:
  - PENGU: Dual Thrust → Ichi 4H Trail (PF 6.01, 10 trades)
  - POL: Ichi Trail → Ichi 4H Trail (PF 3.57, 13 trades)
  - AVAX: Dual Thrust → EMA Ribbon 4H (PF 2.10, 30 trades)
  - ALICE: Dual Thrust → Awesome Osc 4H (PF 1.97, 16 trades)
  - DASH: Dual Thrust → Ichi 4H (PF 1.96, 14 trades)
  - KAS: Range Bounce → EMA Ribbon 4H (PF 1.95, 28 trades)
- **Key lesson**: Z-Score MeanRev sweep PFs (4-6) were overfitting — engine gets 0-5 trades. Always verify.
- Scripts: `research/sweep_improve_weak.py`, `research/verify_weak_improvements.py`

## Realistic Results History
| Round | Bots | Return | DD | Trades | Method | Status |
|-------|------|--------|-----|--------|--------|--------|
| R8 | 14 | +149% | 8.1% | 320 | Conservative | ✅ Audited |
| **Final** | **125** | **+918%** | **10.3%** | **1,449** | **All rounds + 6 upgrades** | **✅ AUDITED (R-multiple fixed)** |

**⚠️ Previous results were inflated by R-multiple risk bug:**
- R10 (+453%), R11 (+689%), R12 (+795%), R12 Full (+1,611%) — ALL had bug where BTC (5% risk) and WIF (3% risk) got 3-5x inflated PnL in shared wallet
- Bug: `dollar_pnl = balance × bot_risk_pct × R` instead of `balance × 0.01 × R`
- Scripts affected: full_portfolio_74.py, round10/11_verify_backtest.py (NOT portfolio_backtest_realistic.py)
- Only `final_backtest.py` (fixed) and `portfolio_backtest_realistic.py` produce correct results

### Scripts
- `research/final_backtest.py` — **CURRENT** full portfolio (R-multiple FIXED, 125 bots)
- `research/portfolio_backtest_realistic.py` — corrected R-multiple (14 bots only)
- `research/full_portfolio_74.py` — DEPRECATED (R-multiple risk bug)
- `research/round10_verify_backtest.py` — DEPRECATED (R-multiple risk bug)
- `research/round11_verify_backtest.py` — DEPRECATED (R-multiple risk bug)
- `research/portfolio_backtest_v2.py` — DEPRECATED (double compounding bug)
- `research/portfolio_backtest_full.py` — DEPRECATED (per-bot $200, not shared wallet)
