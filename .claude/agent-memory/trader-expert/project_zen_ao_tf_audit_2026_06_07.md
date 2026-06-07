---
name: zen-ao-tf-audit
description: ZEN AO timeframe audit — 1H broken (PF 0.59, WF fail), 4H edge present (PF 2.51) but only 5 trades; verdict FORWARD_TEST_4H
metadata:
  type: project
---

ZEN Awesome Oscillator (config_zenusdt_awesome.json, was DEPLOYED on 1H) — verdict **FORWARD_TEST_4H**.

2yr data (2024-03-20 -> 2026-03-21, 17549 1H candles).
- **1H (as deployed):** PF 0.59, WR 27.8%, 36 trades, DD 7.5%, Sharpe -0.89. Walk-forward FAIL (IS 0.74 -> OOS 0.41, ratio 56%, OOS << 1.0). Losing config, actively bleeding.
- **4H (resampled):** PF 2.51, WR 60%, **5 trades**, DD 1.0%, Sharpe 0.86. WF "OK" (IS 2.34 -> OOS 2.64, 113%) but only 2 IS / 3 OOS trades = meaningless. Trades well-distributed 2024-07..2025-07, clean TP(4.0 ATR)/SL exits.

**Why:** Confirms the fleet-wide pattern — AO ships on 1H (timeframe_signal=1h) but was research-validated on 4H (Round 10 VVV/SAND). 1H has no edge; 4H has the right signature but is far below the 15-trade deploy gate.

**How to apply:** Do NOT keep ZEN on 1H. Swap config to 4H and forward-test on testnet to accumulate live trades before real capital. Same conclusion shape as [[pixel-ao-audit]], [[dash-ao-timeframe-audit]], [[pengu-ao-tf-audit]] — right TF, too thin to switch outright. AO 4H is inherently low-frequency (~2-3 trades/yr per coin), so most AO bots will land in FORWARD_TEST_4H, not SWITCH_TO_4H.
