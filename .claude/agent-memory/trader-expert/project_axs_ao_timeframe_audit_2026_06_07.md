---
name: axs-ao-timeframe-audit-2026-06-07
description: AXS Awesome Oscillator audit — deployed on 1H (PF 0.56, losing) but validated on 4H (Round 10). 4H PF 1.57 but walk-forward fails. Verdict FORWARD_TEST_4H.
metadata:
  type: project
---

AXS Awesome Oscillator (config_axsusdt_awesome.json) timeframe mismatch audit. 2yr data (2024-03-20 to 2026-03-21).

**The bug pattern:** AO was research-validated on 4H (Round 10: VVV/SAND 4H PF ~2.8) but ALL 18 config_*_awesome.json ship with timeframe_signal=1h / timeframe_trend=1h. Same class of timeframe-field drift as [[project_alice_timeframe_mismatch_2026_06_06]].

**AXS results (signal_scorer + ai_layer OFF, fees+slippage on):**
- **1H (deployed):** PF 0.56, WR 28.6%, 42 trades, net -$668. Decisive loser — no edge. Both walk-forward halves lose (IS PF 0.42, OOS PF 0.88); the OOS/IS ratio "passes" only because both are terrible — meaningless gate when full PF < 1.
- **4H:** PF 1.57, WR 40%, 15 trades (exactly the minimum), net +$355, DD 2.3%. Walk-forward FAILS: IS PF 3.11 -> OOS PF 0.89 (ratio 0.29, < 0.60). OOS half (8 trades, 25% WR) is losing. Full-period edge is front-loaded into year 1.

**Verdict: FORWARD_TEST_4H.** Not SWITCH (WF fail + only 15 trades), not RETIRE (4H full PF 1.57 > 1.3 shows plausible edge), not KEEP_1H (1H is losing money). Switch config to 4H but treat as unproven — confirm on live/testnet before committing real size.

**Why:** 4H is where AO's documented edge lives; 1H is bleeding. But 4H's edge is marginal-sample and OOS-unstable, so it earns a forward test, not an automatic deploy.

**How to apply:** When auditing the other 17 AO 1H bots, expect 1H to be broken across the board. For each, run the same 1H-vs-4H + walk-forward check. Auto-switch only those whose 4H passes ALL gates (PF>=1.3, trades>=15, OOS PF >= 60% IS); the rest are FORWARD_TEST or RETIRE. Script: /tmp/audit_axs_ao.py (resamples 1h CSV to 4h, runs full + first/second-half walk-forward).
