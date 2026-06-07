---
name: project-xai-ao-timeframe-audit-2026-06-07
description: XAI Awesome Oscillator audit — both 1H and 4H lack edge, RETIRE verdict
metadata:
  type: project
---

XAI AO (config_xaiusdt_awesome.json, DEPLOYED on 1H) audited on 2yr data — verdict RETIRE.

- **1H (deployed)**: PF 0.942, WR 38.6%, 44 trades, Sharpe -0.10, DD 4.8%. WF ratio 0.95 (IS 0.997 / OOS 0.95) — "stable" only because it loses consistently in both halves; no edge.
- **4H (temp test)**: PF 0.545, WR 30.8%, only 13 trades (<15 gate), Sharpe -0.59. WF collapses: IS 1.80 (4 trades, noise) -> OOS 0.11 (9 trades), ratio 0.06.

**Why:** Round 10 validated AO on 4H for VVV/SAND (PF ~2.8) but all 18 AO bots shipped on 1H by mistake. XAI is a case where neither TF rescues it — 1H bleeds with adequate sample, 4H is worse with insufficient sample.

**How to apply:** RETIRE XAI AO. Pairs with [[project-bch-ao-audit-2026-06-07]] (also RETIRE) — 4H does NOT rescue every AO 1H bot. Contrast with [[project-dash-ao-timeframe-audit-2026-06-07]] / [[project-axs-ao-timeframe-audit-2026-06-07]] (4H has edge signature but too thin -> FORWARD_TEST_4H). A high WF ratio on a sub-1.0 PF strategy is a false-positive; always check absolute PF first.
