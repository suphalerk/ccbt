---
name: pengu-ao-tf-audit-2026-06-07
description: PENGU Awesome Oscillator 1H vs 4H timeframe audit — 1H has no edge (PF 0.81), 4H good PF but too few trades, verdict FORWARD_TEST_4H
metadata:
  type: project
---

PENGU AO (config_penguusdt_awesome.json, NOT deployed) audited on both 1H (as shipped) and 4H (resampled) over available data.

**Result:**
- 1H (deployed config, timeframe_signal/trend=1h): PF 0.81, WR 32%, 25 trades, Sharpe -0.36. WF IS PF 0.77 / OOS PF 0.60 — both halves losing. No edge. Confirms the broader bug: all 18 config_*_awesome.json ship on 1H but AO was validated on 4H (Round 10).
- 4H (resampled from 1h CSV): Full PF 4.00, WR 57%, only 7 trades, Sharpe 1.56. WF IS PF 12.0 (4 tr) -> OOS PF 1.51 (3 tr), ratio 0.13 — high PF is small-sample driven, WF unstable.

**Verdict: FORWARD_TEST_4H.** 4H has clearly better PF (4.0 >> 1.3) and direction matches Round 10 AO-on-4H thesis, but 7 trades < 15-trade deploy gate and WF not stable. Cannot SWITCH yet; cannot RETIRE (4H shows real edge directionally).

**Data caveat:** PENGU only ~1.25yr (Dec 2024 -> Mar 2026, 11005 1h candles / 2752 4h candles), not full 2yr — low 4H trade count is partly structural (recent listing). Same caveat applies to VIRTUAL.

Engine note: BacktestEngine does NOT resample internally — must resample 1h->4h manually and pass 4h df as BOTH signal and trend. Filters (trading_hours 03-20 UTC, regime skip_ranging) kept identical across TFs so timeframe is the only variable.

Related: [[project_alice_timeframe_mismatch_2026_06_06]] (same AO TF drift class — config runs wrong TF vs validation).
