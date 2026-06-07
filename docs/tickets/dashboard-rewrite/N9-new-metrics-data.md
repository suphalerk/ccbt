# N9 — New Metrics Data Layer (the dashboard-analysis features)

**Phase A · est. M–L (1–2 days) · Python only — highest ROI**

## Goal
Implement the new analytical metrics **in `dashboard/queries.py`** (tested, single source of truth) and
expose via FastAPI. Absorbs old T1–T5's data layer. N1+N11 land in Streamlit automatically; **these N9
functions need new Streamlit panels (not scoped here) — so for Streamlit they ship only if a panel is
added, otherwise they go live with the React UI (N10).** React rendering is N10.

## ⚠️ Reality baked in (VERIFIED against trades.db + roster)
- **242** closed non-orphan trades (277 closed − 35 orphan_reconcile). **48** distinct closed symbols.
  Only **3** symbols clear 15 trades.
- **12 deployed coins run multiple configs on one netted position** (axs=4; aave/dot/fil=3; +8 run 2:
  atom,bera,inj,ip,ondo,pippin,pixel,sui) → per-symbol trades.db rows BLEND strategies → a single
  KEEP/DROP verdict is meaningless for them. **(count must be COMPUTED at runtime, never hardcoded.)**

## Functions to add (queries.py) + endpoints
1. `get_close_reason_breakdown(db_path, symbol)` → `/api/close-reasons`
   - cols: close_reason, count, pct, total_pnl, avg_pnl. Closed excl. orphan. Taxonomy-agnostic
     (auto-picks up trail_stop/breakeven after N11).
2. `get_trade_gate(db_path, min_trades=15, graduate_pf=None)` → `/api/trade-gate`
   - per symbol: trades, win_rate, profit_factor, total_pnl, avg_pnl, `reward_to_avgloss`, `meets_min`,
     **verdict**. Response also carries summary `{n_meeting_min, n_total}` (computed, never the literal 48).
   - **classify import (ordering fix)**: import `classify` directly from `research.forward_test_report`
     (it exists there today, line 25). The N0 "lift to a shared util" is a later **no-op refactor** that
     keeps this import working — N9 must NOT depend on N0 landing first (N9 is Phase A, N0 is Phase B).
   - **Verdict vocabulary = the REAL `classify()` return set** `{KEEP_TESTING, READY_TO_AUDIT, MARGINAL,
     DROP}` **plus `MIXED`** (do NOT invent KEEP/DROP). `graduate_pf` defaults to the value in the
     forward-test manifest (same source as the CLI), not a hardcoded 1.3.
   - **🔴 Attribution guard (blocker — must-fix #3 done RIGHT)**: build the symbol→config-count map by
     **counting `config_*.json` files per `symbol_clean`** (reuse the `skip_configs` set + normalization
     from `get_bot_statuses`), OR by parsing `deploy/macos/start.sh` (`AUDITED_CONFIGS` +
     `FORWARD_TEST_CONFIGS`). **Do NOT use `get_bot_statuses()` — it dedupes to one row per symbol_clean
     (queries.py:597-600) and structurally cannot report axs=4.** Symbols with count > 1 → `verdict =
     "MIXED"` (never KEEP/DROP); count == 1 → `classify(...)`.
   - **`reward_to_avgloss`** = `mean(pnl)/mean(abs(losing pnl))` — a **$-ratio, NOT** a risk-normalized R
     (AI sizing varies 0.09–709). OPTIONAL real-R column: `mean( pnl / (abs(entry-stop_loss)*size) )`
     (stop_loss is populated on all 242 closed rows; 0 NULLs). Return null/"—" for zero-loss symbols;
     suppress the column for symbols with < ~10 trades.
3. `get_open_risk(db_path, symbol)` → `/api/risk`
   - open_count, notional=Σ|entry·size|, max_sl_loss=Σ abs(entry−stop_loss)·size (skip + count null SL as
     `unprotected_count`). **No fabricated balance** (no app.py:751 `*50` hack); `%` only if a real
     balance is persisted (see "Ships degraded" below).
4. `get_calendar_pnl(db_path, symbol)` → `/api/calendar`
   - per UTC date: daily_pnl, trades, wins, win_rate. **Use the SAME `DATE(timestamp)` SQL that
     `get_daily_pnl` (queries.py:276) already uses** — it is proven on the real format. Extends
     get_daily_pnl; leave get_daily_pnl untouched.
5. `get_hour_dow_stats(db_path, symbol, bucket_hours=4)` → `/api/heatmap`
   - per (dow 0–6, hour_bucket): trades, avg_pnl, win_rate, total_pnl. Returns raw per-cell `trades` so
     N10 can mask. **Diagnostic-only.** Colour mask threshold **≥20** (at 242 trades a 7×6 grid averages
     ~5.7/cell, so min_samples=5 sits at the mean).

## 🔴 UTC bucketing — the recipe (blocker fix; the earlier `strftime('%w','utc',ts)` was WRONG)
Verified in SQLite on the real format `2026-03-23T03:01:20.752540+00:00`: `strftime('%w','utc',ts)`
returns **NULL** (can't parse fractional seconds) and `DATE(ts,'utc')` double-shifts offset-aware ts
**off by a day**. Recipe:
- **Date** (calendar): plain `DATE(timestamp)` (matches get_daily_pnl — proven).
- **Weekday + hour** (heatmap): do NOT fight SQLite — fetch the rows and bucket in **pandas**:
  `t = pd.to_datetime(df.timestamp, utc=True); dow = t.dt.dayofweek; hour = t.dt.hour`. This is correct for
  ANY offset (handles the OANDA/gold +07:00 path), not just +00:00.

## Rules
- All reuse existing hygiene: closed-only, orphan-excluded, div-by-zero guards, PF inf-safe. No business math in TS.

## ⚠️ Ships degraded (no owning ticket for bot-side persistence)
The % risk gauge (open_risk %) and N6 persisted candles depend on the **bot writing balance + recent
OHLCV to the DB** — that touches the live engine and needs the same explicit go-ahead as N11, and has no
numbered ticket yet. Until it's scheduled: open_risk shows **absolute $ only (no %)**, and N6 **drops live
candles**. Do not block N9/N6 on it.

## Tests (write first) — `tests/test_new_metrics.py`
- each function's numbers verified on a seeded DB (orphan excluded; pct sums ~100; risk null-SL →
  unprotected; short-side `abs()`; PF inf path).
- **UTC correctness vs Python ground truth**: assert `get_hour_dow_stats` dow/hour equal
  `pd.to_datetime(ts, utc=True).dt.{dayofweek,hour}` for crafted rows INCLUDING a `2026-06-07T02:00:00+07:00`
  fixture (must bucket to the UTC day/weekday/hour, not local). Assert `get_calendar_pnl` reconciles with
  `get_daily_pnl` on the same data (no regression).
- **Attribution**: a fixture with TWO config files sharing one symbol_clean → the COUNT MAP returns >1 for
  that symbol AND its gate verdict is `MIXED`; a single-config symbol → verdict equals `classify(...)`.
  (Test the count-map derivation directly, not only the consumer — a hand-built map would mask the bug.)
- verdict values ∈ {KEEP_TESTING, READY_TO_AUDIT, MARGINAL, DROP, MIXED}; summary {n_meeting_min,n_total}
  computed (not literal 48); `reward_to_avgloss` formula; real-R null for zero-loss symbols.
- API: each endpoint 200 + shape + parity with the function.

## Acceptance
- Five endpoints live; numbers match direct calls; single-config gate verdicts == the CLI; multi-config
  symbols show MIXED (count from config files, not get_bot_statuses); UTC bucketing correct for a +07:00
  fixture; heatmap mask ≥20 with per-cell counts; risk shows $-only until balance is persisted.

## Result
_(fill on completion)_
