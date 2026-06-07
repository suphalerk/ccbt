---
name: bch-ao-audit-2026-06-07
description: BCH Awesome Oscillator has no edge on either 1H (deployed) or 4H — RETIRE verdict from 2yr backtest + walk-forward
metadata:
  type: project
---

BCH Awesome Oscillator (config_bchusdt_awesome.json, DEPLOYED) audited on 2yr data both TFs. **Verdict: RETIRE** — neither timeframe has edge.

- **1H (deployed):** PF 0.54, WR 23.5%, 51 trades, Sharpe -1.26. WF both halves losing (IS PF 0.53, OOS PF 0.40). Broken.
- **4H:** PF 0.61, WR 36.4%, only 11 trades (<15 gate). WF collapses IS 1.14 -> OOS 0.14 (ratio 0.12). Not FORWARD_TEST-grade either (full PF <1.0 + OOS implodes).

**Why:** Part of the AO-on-1H deployment problem — all 18 config_*_awesome.json ship on 1H though AO was research-validated on 4H (Round 10 VVV/SAND 4H PF ~2.8). The 4H edge does NOT transfer to BCH; switching TF would not fix it.

**How to apply:** BCH should be removed from AO entirely. The 4H AO fix is coin-specific — do not assume SWITCH_TO_4H rescues every AO 1H bot; backtest each. See [[project_round10_github_2026_03_22]] context on AO. Audit script: /tmp/audit_bch_ao.py (resamples 1h CSV->4h, runs engine + half-split walk-forward).
