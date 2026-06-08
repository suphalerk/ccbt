# Research Plan — June 2026: Edges Beyond the 18 Deployed Signals

**Owner:** quant-researcher · **Status:** ready-to-run · **Created:** 2026-06-08

## 1. Goal & Guardrails

Find **new, externally-sourced edges** that add to the 18 deployed signal types — preferring ideas that either (a) **decorrelate** the ~60-bot trend/breakout book or (b) improve the **whole book cheaply** via a risk-layer overlay (no new entry signal). Every candidate below was scored against CCBT's own 12-round / ~20k-backtest evidence and the local data inventory, and several scout assumptions were overturned by repo ground-truth (see §7).

**Non-negotiable methodology bar** (applies to EVERY experiment — see [docs/backtest-methodology.md](../../backtest-methodology.md) §"Audit Checklist" and [docs/research-pipeline.md](../../research-pipeline.md)):

1. **R-multiple PF only** — never `pnl/initial_balance` on the shared wallet (that bug inflated past results 10x). Use actual `risk_per_trade` to compute R, replay at 1% on the shared wallet.
2. **6-point audit** before any deploy: signal-verify · look-ahead (`iloc[-2]` signal / `iloc[-1]` entry) · fees+slippage+**funding** in PnL · walk-forward (OOS PF ≥ 60% of IS) · **≥15 trades** · baseline-compare on the SAME period.
3. **Multiple-testing discount** — a 133-coin × 4H sweep yields **~15–20 coins at PF>1.5 by pure chance**. Gate on **median-PF-across-coins (> 1.2)** + walk-forward + a **2022-bear OOS slice**, NEVER on top-N cherry-picks.
4. **4H is the default** (4H >> 1H is universal in CCBT). Treat 1H as the exception, 15m as effectively dead for new edges.
5. **Correlation is the currency.** The book is ~60 likely-over-correlated trend/breakout bots. Every ATR-trend/breakout candidate MUST ship a **correlation matrix vs deployed Supertrend/Ichimoku/vol_expansion** and prefer **replacing a weak existing bot** over adding a correlated one.

## 2. Ranked Experiment Backlog

Scores are EV-of-edge × CCBT-fit × novelty, discounted for dup-risk and implementation cost. "Fit" column: **drop-in** = existing pipeline / overlay; **new-signal** = full [CLAUDE.md "Adding a New Strategy" checklist](../../../CLAUDE.md#adding-a-new-strategy--checklist) (7 steps incl. the most-missed `strategy.py` dispatch + `backtest/engine.py` dispatch); **coordinator** = cross-coin shared state in `main_multi.py`.

| # | Idea | Mechanism (1-line) | TF / Coins | CCBT fit | Data | Score | Round | Source |
|---|------|--------------------|-----------|----------|------|-------|-------|--------|
| 1 | **HalfTrend ATR-Amplitude** | Trend flips on rolling amplitude-period H/L extreme crossing an ATR(100)/2 channel; ATR-trail exit | 4H (1H check) / 60 deployed + 133 discovery | new-signal | OHLCV | **72** | B | ryu878/halftrend_python; TV U1SJ8ubc |
| 2 | **Funding-Rate Filter / Regime Gate** | Per-coin 30-bar funding **percentile** gate on existing Ichi/Supertrend entries (suppress crowded side) | 4H / 60 deployed 4H bots | drop-in overlay | **8h funding CSV** (128 coins) | **70** | C | CCBT history (BTC +10%, WIF +27%); quantjourney |
| 3 | **Chande Kroll Stop** | Two-pass ATR-extreme envelope (wider hysteresis than Supertrend) + SMA(21) agreement | 4H / 133 discovery | new-signal | OHLCV | **64** | B | TrendSpider; TV GoHLj2XP |
| 4 | **Cross-Sectional 30d Momentum** | Rank 133-coin 30d return per candle; long top quintile / short bottom; gate-on-trend first | 4H / **full 133** | coordinator | OHLCV (BTC incl.) | **62** | C | SSRN 4322637; CTREND JFQA 2024 |
| 5 | **TTM Squeeze Momentum** | BB(20,2)-inside-KC(20,1.5ATR) ≥3 bars → fire with linreg-histogram direction | 4H / 133 | new-signal | OHLCV | **58** | B | FMZQuant; TrendSpider |
| 6 | **NR7 Compression Breakout** | Narrowest-range-of-7 + range<0.6×ATR20 → next-bar extreme break, EMA50 bias, tight SL | 4H / 133 | new-signal | OHLCV | **56** | B | Crabel NR7; LuxAlgo |
| 7 | **Vol-Targeted Sizing Overlay** | `size ×= target_vol / realized_vol(20)` on top of ANY signal — measure Sharpe/DD, not PF | 4H / deployed portfolio | drop-in (risk.py) | OHLCV | **55** | D | Barroso–Santa-Clara 2015; MOP 2012 |
| 8 | **Kaufman Efficiency-Ratio Gate** | KER(10)>0.6 allow trend entries, <0.3 ban (directional-efficiency regime axis) | 4H / deployed trend bots | drop-in overlay | OHLCV | **50** | D | alvarezquant; (KAMA-entry dropped) |
| 9 | **BTC-Residual Z-Score MR** | Strip BTC-beta (180-bar OLS), z-score residual(40), MR with EMA50 trend filter | 4H only / 133 alts | new-signal | OHLCV (BTC) | **44** | B | briplotnik; arxiv 2510.14435 |
| 10 | **Rolling-VWAP Band Breakout** | Rolling VWAP(20) ±1.5σ break with volume>SMA20, KER>0.4 gate | 4H / 133 | new-signal | OHLCV (volume) | **42** | B | pyquantlab AVWAP |
| 11 | **Hurst Regime Switcher** | R/S Hurst(40) routes trend (H>0.55) vs MR (H<0.45) over existing signals | 4H / large+mid cap | drop-in router | OHLCV | **36** | D | MDPI Hurst-crypto |
| 12 | **Day-of-Week / Weekend Gate** | 7d-momentum-conditioned UTC calendar entry window | 1H/4H / alt-heavy | new-signal | OHLCV | **30** | D (last) | ACR weekend-effect; Santiment |

**Dropped as CCBT-confirmed dead-ends or dups** (do NOT re-spend): PSAR Flip (PF 0.95), BTC Lead-Lag / S20 (PF 0.96, 126 coins), Funding-as-primary-trigger (s14 → 0 rows), 4×TTM/2×NR7/2×Chandelier dup-collapse, WaveTrend (Heikin-Ashi weak on crypto), TEMA-fan (= ema_ribbon), N-bar-close breakout (= deployed Donchian/price_channel, XAG PF 15.58), RSI-cross body-ratio (RSI-50-cross PF 0.94, 5m dead), OTT/KAMA/Hull adaptive-MA (PF 1.01/0.98/1.01), S17/S19 regime-routers (median PF ~1.0), London-open S15 (PF 0.81), CTREND single-coin vote (folded into #4), VW-TSMOM (folded into #4/#7), Candle-PA patterns (universal dead-end). Detail in §7.

## 3. Research Rounds (sequencing)

**Run order is EV-and-cost-ordered, not score-ordered:** the two cheapest overlays (#2, #7) are the highest expected value because they improve the *whole book* with low variance and one already validated in CCBT. New-signal sweeps come after.

```
Round C-fast (#2 funding gate)  ┐
Round D-fast (#7 vol sizing)    ┘  ── cheap, book-wide, run FIRST
Round B (#1 HalfTrend → #3 Chande Kroll → #5 TTM → #6 NR7 → #9/#10)
Round C (#4 cross-sectional, as gate-on-trend first, then standalone)
Round D (#8 KER gate, #11 Hurst, #12 calendar — low priority A/Bs)
```

### Round A — (none net-new this cycle)
Round A is reserved for **drop-in sweeps of *existing* signal keys** via `auto_research.py --strategy <key> --timeframe 4h`. None of the 12 candidates are existing keys, so Round A this cycle = the **baseline-compare runs** that every other round depends on: for each coin a candidate touches, first capture the deployed signal's R-multiple PF on the identical 4H period (`auto_research.py --strategy ichimoku --timeframe 4h --compare-deployed`) so additivity/replacement can be judged. **Do this before Round B.**

### Round B — New signal types (full engine checklist)
Each requires all 7 steps in [CLAUDE.md "Adding a New Strategy"](../../../CLAUDE.md#adding-a-new-strategy--checklist). **The two most-missed:** (3) the `generate_signal()` dispatch in `strategy.py` AND (4) the **separate** dispatch in `backtest/engine.py` — miss step 4 and the backtest passes while live bots produce zero signals; register `validated_tf` in `research/strategy_meta.json` (step 6) so the timeframe-drift guard accepts the config. Candidates: **#1 → #3 → #5 → #6**, then **#9, #10** if budget remains.

### Round C — Funding / cross-sectional / lead-lag
- **#2 funding gate** (cheap, validated edge) — wire the 8h funding CSV into the live entry path (`strategy.py`/`risk.py`); today funding only feeds the AI/context layer.
- **#4 cross-sectional momentum** (expensive, orthogonal) — needs a once-per-candle rank dict in `main_multi.py` shared state. Test as **gate-on-trend first** (lower variance) before a standalone rotation bot.

### Round D — Sizing / risk overlays (signal-agnostic)
- **#7 vol-targeting sizing** — `risk.py` multiplicative chain; **measure Sharpe + max-DD, not PF** (per-trade R is unchanged; the edge is equity-curve smoothness). Watch for double-counting against the existing `regime_factor`.
- **#8 KER gate**, **#11 Hurst router**, **#12 calendar** — low-priority incremental A/Bs.

---

## 4. Per-Experiment Specs

> Common to all: backtest with the real `BacktestEngine` (sweep/verify parity), fees 0.04% maker + 0.06% taker, 0.02% slippage, **funding charged**, max-10 concurrent, R-multiple shared-wallet replay. Universe-wide gate = **median-PF-across-coins > 1.2** AND walk-forward OOS ≥ 60% IS AND a 2022-bear OOS slice not catastrophic AND ≥15 trades/coin. Cherry-picked top-N never qualifies a deploy.

### #1 HalfTrend ATR-Amplitude — Round B (new-signal)
- **Hypothesis:** A rolling-amplitude H/L flip trigger captures 4H trend turns with fewer alt whipsaws than EMA-cross *and* with different geometry than Supertrend's centered ATR midline, so it can earn a slot by **decorrelating**, not just re-discovering, existing trend winners.
- **Exact rule (last closed candle):** maintain `up_band = highest(low, amplitude) - atr(100)/2`, `dn_band = lowest(high, amplitude) + atr(100)/2`. Trend = UP while close stays above `up_band` (ratchet `up_band` upward only); flips DOWN when close < `dn_band`. **Entry on flip**, initial SL at the opposite band, **1.5×ATR trail**. Sweep `amplitude ∈ {2,3,4,5,6}`, `atr_len ∈ {50,100,200}`.
- **Backtest:** new dispatch (add `halftrend` to `strategy.py` + `backtest/engine.py`); then sweep via the new-signal path. Baseline-compare each coin vs its deployed Ichimoku/Supertrend (Round A captures).
- **Success:** median-PF > 1.2 across the discovery universe, ≥15 tr/coin, walk-forward ≥60%, **AND a correlation matrix showing ρ<0.7 vs the same coin's Supertrend trade-return series** on ≥10 coins. Deploy only coins that are profitable AND decorrelated; prefer **replacing** an underperforming Supertrend bot.
- **Kill:** median-PF ≤ 1.1 OR ρ>0.8 vs Supertrend almost everywhere (then it's a Supertrend clone — abandon, don't tune amplitude to chase).

### #2 Funding-Rate Filter / Regime Gate — Round C (drop-in overlay)
- **Hypothesis:** Suppressing entries on the over-crowded funding side improves PF on existing 4H trend bots; **per-coin percentile** normalization fixes the coin-heterogeneity that made a fixed absolute threshold help WIF (+27%) but destroy DOGE (-94%).
- **Exact rule:** per-coin 30-bar rolling percentile of the 8h funding CSV aligned to 4H with `shift(1)` (no look-ahead). Gate: **suppress longs when funding pct > p90, suppress shorts when funding pct < p10**; otherwise pass the underlying Ichi/Supertrend entry unchanged. NOT a standalone trigger (s14 standalone already produced 0 rows — see §7).
- **Backtest:** A/B each of the ~60 deployed 4H bots: with-gate vs without-gate, R-multiple PF, on identical periods. **First verify CSV coverage/recency per coin** (sparse funding history is exactly why the s14 sweep yielded nothing); drop coins with <12mo funding.
- **Success:** PF improvement on a coin with ≥15 post-gate trades, walk-forward stable. **Per-coin opt-in:** KEEP the gate only where it lifts PF, DROP it where it hurts (DOGE-style). No blanket application.
- **Kill:** gate helps <40% of tested coins OR the median PF delta ≈ 0 (then funding adds nothing beyond noise here).

### #3 Chande Kroll Stop — Round B (new-signal)
- **Hypothesis:** Two-pass smoothed H/L extremes give wider hysteresis than Supertrend's single-pass band → fewer alt whipsaws (the ETH 0.90 / SOL 1.04 failure mode).
- **Exact rule:** `stop_short = highest(high, p) - m×ATR(10)`, `kroll_short = highest(stop_short, p)` (symmetric long). Entry on close crossing the Kroll line WITH SMA(21) trend agreement; exit on opposite-line cross; 1.5×ATR SL. Sweep `m ∈ {2,3,4}`, `p ∈ {14,21,30}`.
- **Backtest / Success / Kill:** as #1, **but framed as a Supertrend *replacement*** on coins where deployed Supertrend underperforms — the dup-risk with `dual_supertrend` is high, so the correlation gate (ρ<0.7) is decisive. Kill if it can't beat Supertrend net on its weak coins.

### #4 Cross-Sectional 30d Momentum — Round C (coordinator)
- **Hypothesis:** A cross-coin rank factor is **orthogonal** to all 26 single-coin time-series signals; even a modest edge diversifies a likely-over-correlated book.
- **Exact rule:** coordinator computes each coin's 180×4H-bar return **once per candle at the boundary** (rank uses only closed candles — look-ahead is the #1 failure mode here); permit LONG only in top quintile, SHORT only in bottom, ban the middle; liquidity filter on rolling volume. **Phase 1:** apply as a *gate* on an existing trend signal. **Phase 2:** standalone, 7-day hold + ATR trail.
- **Backtest:** extend the `main_multi.py` shared-state (SharedMarketData) with a rank dict; rebalance ~weekly. Baselines: equal-weight buy-and-hold of the universe AND the deployed portfolio's blended equity. **Rebalance churn fees must be in PnL.**
- **Success:** gate-phase lifts the underlying signal's median PF; standalone beats both baselines on Sharpe with walk-forward + an explicit **2022-bear OOS** (crypto cross-sectional momentum is period-sensitive — strong 2018–21, weaker post-2022).
- **Kill:** gate doesn't help OR standalone is not better than buy-and-hold after fees on the 2022 slice (then the factor doesn't survive the regime we most need it in).

### #5 TTM Squeeze Momentum — Round B (new-signal)
- **Hypothesis:** Entering at the *coil* (BB-inside-KC) is earlier in the vol cycle than `vol_expansion` (which fires after expansion starts) — a different timing on the same phenomenon.
- **Exact rule:** squeeze ON when BB(20,2) ⊂ KC(20,1.5×ATR); require ≥3 consecutive ON bars; entry when squeeze FIRES (BB re-expands) with linreg-momentum histogram positive (long)/negative (short); 1.5×ATR SL, trail via SMA21 / histogram flip.
- **Backtest:** **CRITICAL baseline-compare vs the deployed `vol_expansion` bot on the same coins** — same phenomenon. Deploy only where TTM beats vol_expansion AND is decorrelated.
- **Kill:** **watch for over-filtering** — CCBT's own S12 (VolExp+Supertrend) produced **0 trades** because compression-gating was too strict. If median trades/coin < 15 because the ≥3-bar squeeze rarely fires, loosen ONCE; if still <15, kill (it's a vol_expansion variant, not a new family).

### #6 NR7 Compression Breakout — Round B (new-signal)
- **Hypothesis:** Rank-based compression detection (narrowest-of-7) enters at the coil with a structurally tight SL → favorable R:R because crypto vol-clustering makes expansion often 3–5× the compression range.
- **Exact rule:** flag NR7 (narrowest H–L of last 7 bars); require range < 0.6×ATR20; next-candle break of NR7 high/low with EMA50 directional bias; SL at NR7 opposite extreme; 3×ATR trail; reject in ranging regime.
- **Backtest / Success / Kill:** as #5, baseline vs `vol_expansion` AND `dual_thrust` (related breakout family). Cheapest of the breakout trio to implement. Multiple-testing discount hits hard on a 133-coin breakout sweep — median-PF gate is mandatory.

### #7 Vol-Targeted Sizing Overlay — Round D (drop-in risk.py)
- **Hypothesis:** Scaling bet size by inverse realized vol cuts size in high-vol crash regimes, lifting Sharpe and cutting max-DD on the leveraged book (Barroso–Santa-Clara: crypto Sharpe ~1.12→1.42) — retrofittable onto *every* bot.
- **Exact rule:** add a multiplicative factor `clip(target_vol / realized_vol(20bar), lo, hi)` into the existing `risk.py` sizing chain (alongside `regime_factor`, win-rate, AI modifiers). Same entries, vol-scaled size.
- **Backtest:** replay the **deployed portfolio** with identical entries, vol-scaled size. **Compare Sharpe + max-DD, NOT PF** (per-trade R is unchanged; sizing only reshapes the equity curve).
- **Success:** Sharpe up AND max-DD down vs current sizing, walk-forward stable, with NO double-counting of volatility against `regime_factor` (test the two together).
- **Kill:** max-DD not improved OR it just duplicates `regime_factor` (collapse them into one factor instead of shipping both).

### #8 Kaufman Efficiency-Ratio Gate — Round D (drop-in overlay)
- **Hypothesis:** KER measures directional *efficiency* (serial correlation), a different axis than CCBT's volatility-magnitude regime tools — a coin can be high-ATR yet inefficient/mean-reverting; gating on KER attacks the alt EMA-cross-in-chop failure.
- **Exact rule:** `KER(10) = |net displacement| / Σ|bar moves|`. Gate: allow trend entries only when KER>0.6; ban new entries when KER<0.3. Overlay on deployed trend bots — **NOT KAMA entries** (KAMA-cross already tested PF 1.01).
- **Success / Kill:** the real question is "**is KER a better chop filter than the existing `choppiness_ema`?**" — A/B both as gates on the same bots; KEEP only if KER lifts PF where choppiness_ema doesn't. Kill if it just over-filters (S17 regime-gating sat at median PF ~1.0).

### #9 BTC-Residual Z-Score MR — Round B (new-signal, low-priority probe)
- **Hypothesis:** Stripping BTC-beta isolates idiosyncratic mispricing — a cleaner MR target than raw price, and distinct from `zscore_meanrev`/`zscore_stoch`.
- **Exact rule:** rolling 180-bar OLS of alt-return vs BTC-return; z-score the residual over 40 bars; long when z<-1.8 **AND close>EMA50**, short when z>+1.8 **AND close<EMA50** (the EMA50 filter is mandatory — prior MR failure mode was shorting uptrends); revert-to-zero exit, 2×ATR SL. **4H only** (15m MR is dead).
- **Success / Kill:** small probe only — fights two CCBT headwinds (MR broadly weak; the BTC-information channel already failed as S20, PF 0.96). Kill immediately if median-PF < 1.15; do not tune.

### #10 Rolling-VWAP Band Breakout — Round B (new-signal, low-priority)
- **Hypothesis:** Volume-weighting is the only genuinely new information source (all 26 signals are price-only); rolling (not session) VWAP suits 24/7 perps.
- **Exact rule:** rolling VWAP(20) ±1.5σ; enter on close breaking a band WITH volume>SMA20; exit on reversion to VWAP or 2×ATR SL; KER>0.4 trend gate. **Note the internal momentum-entry/MR-exit tension — test both a trail exit and the revert exit.**
- **Success / Kill:** tempered prior — CCBT's only prior volume signal (OBV Breakout) was PF 1.04 (crypto-perp volume is noisy: wash trading, venue fragmentation). Kill if it can't clear median-PF 1.2; low-priority.

### #11 Hurst Regime Switcher — Round D (drop-in router, curiosity)
- **Hypothesis:** Hurst (memory) routes to regime-appropriate existing logic.
- **Exact rule:** rolling R/S Hurst on 40-bar 4H window; H>0.55 → enable trend signals; H<0.45 → enable MR; 0.45–0.55 → halve size / no entries.
- **Success / Kill:** keep as research curiosity only — rolling Hurst on 40 bars is statistically noisy and the analogous S17 regime-router showed no edge (median PF ~1.0). High overfitting surface (3 thresholds × 2 signal classes) — kill unless it clearly beats `choppiness_ema` routing.

### #12 Day-of-Week / Weekend Gate — Round D (last, near-dead-end)
- **Hypothesis:** A time-of-week calendar gate is untested as a *deployed* signal.
- **Exact rule:** 7d-momentum direction set as a Fri→Mon / DoW-conditioned UTC entry window.
- **Success / Kill:** CCBT already swept the two closest variants — S16 DoW (median PF ~1.0–1.1) and S15 London (PF 0.81, actively bad); the 4H DoW "winner count" of 19 is a multiple-testing artifact at median PF 1.099. Calendar effects also decay fastest (arbitraged away). **Spend minimal budget; kill on first median-PF ≤ 1.1.**

## 5. Data Dependency Summary

| Deployable now (local OHLCV only) | Needs local funding CSV | Needs data we DON'T have (note as blocker) |
|-----------------------------------|-------------------------|---------------------------------------------|
| #1, #3, #4, #5, #6, #7, #8, #9, #10, #11, #12 | **#2** (8h funding, 128 coins present — verify per-coin recency) | none in this cycle |

All 12 are **OHLCV-or-funding deployable today** — no OI / L2 / tick / on-chain dependency. (Ideas that *would* need OI/L2 were dropped or deferred; CCBT only has 15m+1h OHLCV 2yr, per-coin 8h funding, BTC 5m, gold XAUUSD locally.) If a future idea needs OI/taker-flow, note it: those are **live-only (30d limit)** per the microstructure research.

## 6. Effort & Sequencing Summary

| Round | Items | Effort | Why this order |
|-------|-------|--------|----------------|
| A (prep) | baseline-compare deployed signals per touched coin | S | every later round needs the baseline |
| **C-fast** | #2 funding gate | **S–M** | only CCBT-*validated* edge; cheap A/B; per-coin opt-in |
| **D-fast** | #7 vol sizing | **S** | book-wide DD improvement, lowest variance |
| B | #1 → #3 → #5 → #6 (→ #9, #10) | **M–L each** | full engine checklist (7 steps ×4–6 signals) |
| C | #4 cross-sectional | **L** | coordinator + look-ahead-safe rank; gate-first |
| D | #8 → #11 → #12 | **S each** | low-priority incremental regime/calendar A/Bs |

**One-line rationale:** the two highest-EV bets (#2, #7) are also the cheapest and improve the whole book — run them first; genuine diversification (#4) is the highest-value new family but carries the most look-ahead risk — run it after the cheap wins; the ATR-trend/breakout new-signals (#1/#3/#5/#6) only earn slots if they **decorrelate** from the existing book.

## 7. Ground-Truth Notes (repo-verified, overturns several scout claims)

- CCBT has the **18 deployed** signals PLUS **8 more researched** (adx_di_cross, choppiness_ema, williams_r_adx, roc_momentum, stoch_supertrend, price_channel_vol, ema_alligator, supertrend_volume) and several tested-mediocre: **KAMA-Cross 1.01, Hull 0.98, OBV 1.04, Heikin-Ashi+EMA weak**.
- **Confirmed failures** in CCBT's own sweeps (do NOT re-test): PSAR Flip 1H **0.95**; BTC Cross-Coin Leader S20 **0.96** (median 0.939 / 126 coins); Regime Transition S17 1H **0.88**.
- **Decisive:** `research/sweep_new_strategies_2.py` already contains **`s14_funding_primary`** (funding-contrarian + EMA50 gate = "funding as primary trigger") and it produced **ZERO usable rows** → funding's edge is the **FILTER** role (#2), not a trigger.
- **s15 London / s16 DoW / s17 RegimeTransition / s19 ATR-Adaptive** were all already swept; their 4H "winner counts" (15–20) are **multiple-testing artifacts** — median PF clusters at 1.0–1.1 across 110+ coins (no real edge).
- **Donchian/price-channel breakout already exists** (XAG PF 15.58) → N-bar-close-breakout is a dup.
- **128 `*_funding_rate.csv`** files confirmed in `data/`; `auto_research.py` interface confirmed (`--strategy/--timeframe/--compare-deployed/--coins/--generate-configs`).

## References
- [docs/backtest-methodology.md](../../backtest-methodology.md) — R-multiple rules + 6-point audit (mandatory)
- [docs/research-pipeline.md](../../research-pipeline.md) — `auto_research.py`, 4H forward-test cohort (the ≥15-trade path for candidates that backtest well but lack trades)
- [docs/research-history.md](../../research-history.md) — Rounds 1–12 / what already failed
- [CLAUDE.md "Adding a New Strategy" checklist](../../../CLAUDE.md#adding-a-new-strategy--checklist) — 7 steps for every Round B item
