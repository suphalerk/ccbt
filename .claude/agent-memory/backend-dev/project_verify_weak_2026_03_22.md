---
name: project_verify_weak_2026_03_22
description: Weak coin engine verification (29 coins): 8/29 pass full BacktestEngine, top picks PENGU/POL/AVAX/ALICE
type: project
---

29 weak coins verified with full BacktestEngine against their best sweep combo. Script: `research/verify_weak_improvements.py`. Results: `data/verify_weak_improvements.json`.

**Why:** These coins showed PF > 1.2 in lightweight sweep but needed engine verification to confirm edge is real. Many zscore_meanrev 1H winners collapsed in engine (too few trades or PF < 1.2).

**Results (8 PASS / 21 FAIL):**

| Coin  | Signal            | TF | Sweep PF | Engine PF | Trades | WR% | DD%  | Sharpe |
|-------|-------------------|----|----------|-----------|--------|-----|------|--------|
| PENGU | ichimoku_cloud    | 4H | 3.00     | 6.009     | 10     | 50% | 1.1% | 1.04   |
| POL   | ichimoku_cloud    | 4H | 2.49     | 3.572     | 13     | 54% | 0.9% | 1.13   |
| AVAX  | ema_ribbon        | 4H | 1.89     | 2.103     | 30     | 43% | 2.1% | 1.20   |
| ALICE | awesome_oscillator| 4H | 1.52     | 1.965     | 16     | 56% | 1.4% | 0.76   |
| DASH  | ichimoku_cloud    | 4H | 1.96     | 1.963     | 14     | 50% | 2.1% | 0.92   |
| KAS   | ema_ribbon        | 4H | 2.23     | 1.952     | 28     | 46% | 3.9% | 1.11   |
| TON   | ichi_adx          | 4H | 2.44     | 1.314     | 7      | 43% | 1.0% | 0.29   |
| ZEN   | supertrend        | 4H | 1.27     | 1.270     | 41     | 44% | 4.7% | 0.47   |

**Key findings:**
- zscore_meanrev 1H: almost all failed engine (VVV 0 trades, ZEC 5 trades, ICP 0 trades) — sweep was overfitting
- ichi_adx 4H: DOT/SUI/ADA failed (only 3-5 trades each) — too few trades for the lookback period
- awesome_oscillator 4H: ALICE passes (PF 1.965, 16 trades) — only AO winner
- 4H Ichimoku variants dominate winners: PENGU PF 6.0, POL PF 3.6, DASH PF 2.0
- AVAX ema_ribbon 4H: PF 2.10 (improvement over current AVAX Ichimoku 1H PF 1.95)
- KAS ema_ribbon 4H: PF 1.95, 14 trades/yr — new coin candidate
- ZEN/TON border cases: PF just above threshold, low Sharpe (0.29-0.47) — marginal

**How to apply:** PENGU/POL/AVAX/ALICE/DASH/KAS are strong deploy candidates (PF >= 1.95, DD <= 2.1%, good Sharpe). TON/ZEN are marginal — deploy at lower confidence. All 4H strategy needing config with `timeframe_signal: "4h"` equivalent (resample 1H data).
