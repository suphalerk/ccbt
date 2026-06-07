---
name: dash-ao-timeframe-audit-2026-06-07
description: DASH Awesome Oscillator audit — 1H (shipped) has no edge & fails walk-forward; 4H walk-forward-stable but too few trades. Verdict FORWARD_TEST_4H.
metadata:
  type: project
---

DASH AO (`config_dashusdt_awesome.json`, not deployed) audited on 2yr data (data/dashusdt_1h_2y.csv, 17549 1H candles; 4388 candles after resample to 4H).

**1H (as shipped, timeframe_signal/trend=1h):** 41 trades, PF 0.98, WR 36.6%, DD 4.9%, Sharpe -0.02. Walk-forward FAILS hard: IS PF 1.42 (30 tr) -> OOS PF 0.33 (11 tr), ratio 23% (gate is 60%). OOS WR collapses to 18%. Classic overfit / regime-decayed edge.

**4H (resampled, temp config):** 11 trades, PF 1.18, WR 36.4%, DD 2.7%, Sharpe 0.18. Walk-forward STABLE: IS PF 1.38 (6 tr) / OOS PF 1.43 (5 tr), ratio 103%. Both halves profitable, low DD.

**Verdict: FORWARD_TEST_4H.** 4H shows the durable-edge signature (WF-stable, both halves green) but only 11 trades total (< 15 deploy gate) and PF 1.18 (< 1.3 switch gate). 1H must NOT stay live — coin-flip WR with degrading OOS.

**Why:** Confirms the broader AO bug — all 18 config_*_awesome.json ship on 1H but AO was research-validated on 4H (Round 10 VVV/SAND 4H PF ~2.8). The 1H edge is largely gone (sampled 8/10 AO 1H bots PF < 1.2).

**How to apply:** When auditing the other AO bots, expect same pattern: 1H PF < 1.2 + WF fail, 4H better but thin trade count. Engine has no internal resampling — pass 4H-resampled DF as both signal+trend; config timeframe_signal/trend is informational only. Related: [[project_alice_timeframe_mismatch_2026_06_06]] (AO ALICE same TF-drift class of bug).
