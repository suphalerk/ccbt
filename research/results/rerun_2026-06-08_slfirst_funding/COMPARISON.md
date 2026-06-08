# Portfolio re-run — SL-first + funding vs baseline (2026-06-08)

> Downstream of backtest **PR-B (SL-first same-candle tiebreak)** + **PR-C (funding deduction)**.
> Results stored here, SEPARATE — `docs/portfolio.md` and the canonical `data/final_backtest_fixed.json`
> are UNCHANGED (the baseline json was backed up to `final_backtest_BASELINE_preSLfunding.json` and the
> canonical file restored).

## Files
- `final_backtest_NEW_slfirst_funding.json` — current engine (SL-first + funding)
- `final_backtest_BASELINE_preSLfunding.json` — pre-PR-B/PR-C engine (the old `final_backtest_fixed.json`)
- `final_backtest.txt` / `portfolio_v2.txt` — full stdout (per-bot PF/WR table + monthly/daily)

## ⚠️ Caveat — this is NOT a clean engine-only isolation
The BASELINE json is an **older run** (produced before PR-B/PR-C). Comparing it to the new run mixes TWO
effects: (1) the engine change (SL-first + funding) AND (2) any **data-period drift** (the `*_2y.csv` files
may have grown / shifted since the baseline was generated). Some of the larger swings below (esp. the
"flipped UP" bots) are bigger than SL-first+funding alone would cause → data drift is present.
**For a precise engine-only delta**, run baseline-engine vs new-engine on the SAME data NOW via temporary
toggle flags (`CCBT_BT_LEGACY_TIEBREAK` / `CCBT_BT_NO_FUNDING`) — not yet done (offered).

## Headline portfolio impact (final_backtest, shared wallet $200, 125 bots)
| Metric | BASELINE | NEW (SL-first + funding) | Δ |
|---|---|---|---|
| Final balance | $2,035.73 | **$1,694.65** | −$341 |
| Total return | +917.9% | **+747.3%** | −170 pts |
| Max drawdown | 10.3% | **14.7%** | +4.4 pts (worse) |
| Total trades | 1,449 | 1,421 | −28 |

Direction is as expected: the conservative tiebreak turns some same-candle "wins" into losses and funding
charges holding cost → lower return, higher DD. **The NEW (lower) numbers are the more trustworthy ones.**

## Bots that flipped PROFITABLE → LOSING (19) — re-audit these
The ones to scrutinise (were positive, now negative under the honest engine):
| Bot | baseline PnL | new PnL |
|---|---|---|
| IP dual_thr R10 | +71.7 | −0.8 |
| W range_bo R10 | +67.9 | −0.5 |
| CFX range_bo R10 | +18.1 | −28.6 |
| TIA Ichi 4H | +28.1 | −13.5 |
| DOT dual_thr R10 | +5.5 | −33.8 |
| ONDO range_bo R10 | +4.8 | −33.0 |
| ZEC dual_thr R10 | +6.4 | −25.4 |
| OP dualthru R12 | +14.8 | −12.0 |
| ATOM dual_thr R10 | +15.6 | −9.8 |
| DOT range_bo R10 | +10.0 | −14.5 |
| XAUUSD zscore_s R12 | +15.8 | −5.0 |
| INJ ema_ribb R11 | +1.3 | −15.5 |
| (+7 more marginal) | | |

## Bots that flipped LOSING → PROFITABLE (10) — likely data-drift, verify
AXS awesome_ R10 (−42→+30), ANKR dual_thr R10 (−23→+30), FIL awesome_ R10 (−9→+35),
ENJ dualthru R12 (−9→+28), ONDO awesome_ R10 (−6→+25), 1000SHIB zscore_m R11 (−1→+26), … —
swings this large smell of data drift more than SL-first/funding; treat with suspicion.

## 10 most HURT (PnL delta)
BERA ema_ribb (Δ−85), IP dual_thr (Δ−72, flip), W range_bo (Δ−68, flip), TRUMP Ichi 1H (Δ−64),
DASH Ichi4H (Δ−51), ICP dual_thr (Δ−49), ARC EMA 15m (Δ−48), CFX range_bo (Δ−47, flip),
TRUMP ema_ribb (Δ−46), TIA Ichi 4H (Δ−42, flip).

## Recommendation
1. **Trust the lower numbers** — the roster's published PF/return in `docs/portfolio.md` are optimistic.
2. **Re-audit the 19 down-flipped bots** before keeping them (esp. dual_thr / range_bo / Ichi on the list).
3. **Run the clean engine-only isolation** (toggle flags, same data) to separate the funding cost from the
   SL-first cost from data-drift — then update `docs/portfolio.md` with the honest figures + a "funding ON /
   SL-first" provenance note.
