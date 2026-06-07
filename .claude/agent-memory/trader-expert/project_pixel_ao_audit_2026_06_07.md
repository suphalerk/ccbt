---
name: pixel-ao-audit-2026-06-07
description: PIXEL AO timeframe audit — 1H deployed is broken (PF 0.87), 4H has edge (PF 2.22) but only 12 trades. Verdict FORWARD_TEST_4H.
metadata:
  type: project
---

PIXEL Awesome Oscillator (config_pixelusdt_awesome.json, DEPLOYED on 1H) audited 1H vs 4H on 2yr data (2024-03-20 → 2026-03-21, 17549 1h candles).

**1H (as deployed):** PF 0.87, WR 35.2%, 54 trades, Sharpe -0.27, DD 7.2%. Walk-forward IS PF 1.22 → OOS PF 0.55 (OOS/IS 45%, FAIL). Edge is gone on 1H — confirms the broader finding that AO is a 4H strategy shipped on 1H by mistake.

**4H (temp config, 1h resampled to 4h):** PF 2.22, WR 50.0%, 12 trades, Sharpe 0.90, DD 2.0%. Walk-forward IS PF 3.12 (7 tr) → OOS PF 1.06 (5 tr), OOS/IS 34%. PF clears 1.3 gate but trades 12 < 15 and OOS sample only 5 trades — too thin for a hard SWITCH.

**Verdict: FORWARD_TEST_4H.** 4H is clearly the right timeframe (directionally matches Round 10 AO-4H: VVV/SAND PF ~2.8) and 1H must not stay live (PF < 1.0). But 12 trades over 2yr fails the >=15 deploy gate and OOS is low-sample, so switch to 4H config in testnet/forward-test rather than declaring it audited-deploy.

**Why:** Walk-forward + trade-count gates (docs/backtest-methodology.md) protect against low-sample luck. 4H AO trades only ~6/yr — needs live confirmation.
**How to apply:** When auditing the other 17 config_*_awesome.json AO 1H bots, expect the same pattern (1H broken, 4H thin). Low 4H trade count is the recurring blocker → FORWARD_TEST is the likely modal verdict, not SWITCH.

Related: [[bch-ao-audit-2026-06-07]] (BCH AO RETIRE: both TFs broken), [[alice-timeframe-mismatch-2026-06-06]] (AO timeframe field drift).
