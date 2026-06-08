# Funding-Rate Gate A/B — CONCLUSION (2026-06-09)

> Research experiment #1 from `docs/tickets/research-plan-2026-06/`. Per-coin funding-percentile
> entry gate (block LONG when funding %ile > p90, SHORT when < p10) A/B'd against gate-OFF on the
> deployed roster, same data/period, **R-multiple PF**, with a multiple-testing audit.
> Code lives on branch `research-funding-gate` (config-gated, OFF == today — NOT merged to main).

## ❌ VERDICT: INCONCLUSIVE / DO NOT DEPLOY as a blanket gate
- 54 configs processed → **22 reliable** after the audit (28 dropped as `low_n_artifact` = gate cut
  trades below the 15-trade floor; 2 `inf_pf_transition`; 4 `no_funding`).
- Of the 22 reliable: **10 helped, 12 hurt**. **Median ΔPF ≈ −0.02** (flat-to-slightly-negative).
- The gate is a FILTER → it slashes trade count everywhere (e.g. WIF 65→34, SUI dualthrust 32→5),
  which both (a) mechanically inflates/deflates PF and (b) pushes half the roster below the
  reliability floor. A 10-vs-12 coin-flip split with median ~0 is **NOT a robust edge** — picking
  the 10 winners would be textbook overfitting (the multiple-testing discount the audit enforces).

## ⚠️ Contradicts the old finding
`docs/research-history.md` claimed "funding improves … WIF +27%". Under this honest R-multiple A/B,
**WIF was HURT** (PF 1.16→0.86). The old number was likely a different period / not R-multiple /
not audited. Treat the legacy funding claims as unverified.

## Where it *appeared* to help (NOT yet trustworthy — needs walk-forward)
ENJ dualthrust +0.76, APT rangebounce +0.39, ANKR dualthrust +0.38, TRUMP emaribbon +0.36,
SUI rangebounce +0.23, PIPPIN dualthrust +0.23, ARC ema +0.21. (Concentrated in dual-thrust /
range-bounce / ema-ribbon.) These are candidates for a follow-up **walk-forward + out-of-sample**
check before any per-coin opt-in — do NOT deploy on the in-sample winners alone.

## Where it hurt
AAVE rangebounce −0.32, WIF −0.31, BERA ichi −0.27, BERA emaribbon −0.20, XRP dualthrust −0.20,
XAI rangebounce −0.19, INJ emaribbon −0.14.

## Recommendation
1. **Reject the blanket funding gate.** Net portfolio effect is ~flat-negative and it shrinks the
   tradeable sample.
2. Optional follow-up (low priority): walk-forward the ~7 "helped" dual-thrust/range-bounce coins
   on a held-out period; deploy per-coin ONLY if the edge survives OOS.
3. The reusable infra (`add_funding_percentile` + config-gated gate, look-ahead-safe) stays on the
   branch for that follow-up; not merged to main.
4. Move to the next research idea — **HalfTrend (score 72)** or **Vol-Targeted Sizing (55)**.

## Files
- `results.json` / `results.txt` — full per-coin A/B (gate ON vs OFF, R-multiple PF/WR/trades).
