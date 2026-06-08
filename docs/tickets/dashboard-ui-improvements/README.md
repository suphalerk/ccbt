# Dashboard UI Improvements — Plan

> **STATUS (2026-06-08):** Batch 1 ✅ DONE + deployed ([batch-1-operational.md](batch-1-operational.md)) —
> mode buttons (#1), STOP-ALL/PANIC-ALL/RESUME-ALL (#2), alert banner (#4). Batch 2 ✅ DONE + deployed
> ([batch-2-live-trade.md](batch-2-live-trade.md)) — SL/TP+R:R+dist% in UpnlPanel (#3), Today's-PnL/notional
> header cards (#5). **Batch 3 ⏳ pending** ([batch-3-analytics.md](batch-3-analytics.md)) — underwater chart
> (#6), BotGrid symbol labels (#7), Bot-table filter (#8). Each batch shipped via its own branch + TDD +
> multi-lens review; Batch 1 review caught 2 BLOCKERS (mode case-mismatch banner false-alarm; dead
> mode-highlight) + 2 MAJORS — all fixed before merge.

> From a 2026-06-08 UI review of the CCBT v2 dashboard vs the best trading-bot dashboards (freqUI/Freqtrade,
> Jesse, Hummingbot, OctoBot, Binance Futures, TradingView, 3Commas, Gunbot, Coinrule, crypto.dobot.trade).
> Verdict: the dashboard is architecturally clean (no math in TS, WS hydration, correct dark-theme semantics,
> good empty states) — the gaps are almost entirely **operational/workflow, not aesthetic**, matching the
> user's "useful not fancy" bar. NOT scheduled yet — picked up after the current queue (uPnL merge + telegram
> /upnl + restart). All ADD_NOW items build from data CCBT already has (trades.db, bot_health, bot_ohlcv, the
> existing endpoints + markprice WS) — none need a new exchange API call.

## 💡 Headline finding — mode control is already 90% built
The backend + client for bot-mode control EXIST and are tested/auth-gated, but NO React component calls them:
- `POST /api/bots/{symbol}/mode` and `POST /api/bots/mode/bulk` (PANIC debounce + bulk-all = built-in
  STOP-ALL/PANIC-ALL) in `api/routers/control.py`; `setBotMode` / `setBulkMode` in `web/src/api/client.ts`;
  `bot/mode.py write_bot_mode()` (atomic tempfile+rename); `verify_token` guards it.
- So ADD_NOW #1 and #2 are pure FRONTEND WIRING of existing, security-hardened endpoints. Highest value/effort.

## ✅ ADD_NOW (prioritized — high value, data ready)
| # | Feature | Why | Effort | Where |
|---|---------|-----|--------|-------|
| 1 | **Mode buttons on BotDetailPage** (NORMAL / GRACEFUL_STOP / TP_ONLY / PANIC) | #1 operational gap — dashboard shows mode but can't change it → manual JSON edit mid-drawdown. Endpoint+client exist; PANIC needs a confirm dialog. | S (~3-4h) | web BotDetailPage + `setBotMode` |
| 2 | **Global STOP-ALL / PANIC-ALL** on Portfolio header | Fleet kill switch for a 59-bot crash. Default the prominent button to GRACEFUL_STOP (safer, no forced market exits); PANIC behind a confirm. Bulk endpoint with empty `symbols=[]` already hits the whole roster + 30s PANIC debounce (429). | S (~2-3h) | Portfolio header + `setBulkMode` |
| 3 | **SL + TP + R:R / dist-to-stop% columns in UpnlPanel** | See R:R remaining on a live trade without opening Binance (3Commas/Binance). | S (~3h) | add `stop_loss,take_profit` to the SELECT at `api/markprice.py:166`; compute R:R server-side (no math in TS) |
| 4 | **Portfolio alert banner**: error_count + non-NORMAL-mode count + circuit-breaker flag | Opening the dashboard, instantly see what needs attention instead of scanning 59 grid dots. | S (~2h) | aggregate the already-loaded `/api/bots` list |
| 5 | **'Today's PnL' + 'Open positions / notional'** header cards | Today's PnL is checked every few minutes but is currently buried in the daily-PnL bar chart. | S (~2-3h) | last row of `/api/daily-pnl`; notional = Σ(size·entry) from open trades (+ a `notional` field on portfolio/summary) |
| 6 | **Underwater drawdown sub-chart** below the equity curve | A single max-DD number hides WHEN/HOW LONG bad periods lasted (Jesse/TradingView). | S/M (~3-4h) | pure frontend: running-max − cumulative_pnl on the series `/api/equity` already returns; reuse Charts.tsx |
| 7 | **Visible symbol labels in BotGrid pills** (truncated 3-6 char) | 59 colored dots with the symbol in `aria-label` only = unscannable. The dot-only design was a test-DOM constraint, not UX. | S (~1-2h) | BotGrid pill + fix any selector relying on dot-only |
| 8 | **Search + status + strategy filter on Bot Overview Table** | 59 rows: sort alone can't answer "show only error bots" / "all GRACEFUL_STOP" / "all ichimoku DROP". | S (~2-3h) | client-side filter over already-loaded rows, no API change |

## 🟡 CONSIDER (needs new data — schedule when relevant)
- **Liquidation price + margin ratio per open position** (Binance) — the best "about to blow up" number, but
  the ONE feature NOT free: no leverage/liq column in trades.db/bot_health → needs an exchange call or a
  maintenance-margin formula. **Defer to mainnet**; near-zero value on testnet.
- **Daily-loss-consumed progress bar** (Coinrule) — risk.py enforces max_daily_loss but `RiskManager.state`
  (daily_pnl, starting_balance, is_halted) is in-process, not exposed. Needs a bot_health column/endpoint.
  Medium on testnet, high once live.
- **Normalised 'PnL per $1000 deployed' column** in Bot Overview (Gunbot) — fair cross-bot compare (removes
  capital bias); needs a config-derived per-bot capital figure. Worth it when deciding which bots to retire.
- **Actual-balance equity line** (incl. open unrealized) as a 2nd curve (freqUI) — current curve = closed PnL
  only, understates DD when positions are open+losing. The markprice WS already has total unrealized.
- **Per-bot ATR / SL / TP / risk-in-USDT** in BotDetail Risk Monitor — same trades-table data as #3; do in the
  same pass if touching BotDetail.
- **Symbol selector on the Analytics page** — CloseReasonDonut/MonthlyCalendar/ExpectancyHeatmap already accept
  a `symbol` prop but no UI drives it → turn portfolio panels into per-bot diagnostics. Low effort.
- **Month-granularity returns heatmap** (year×month) alongside the day calendar — surfaces seasonality;
  little to show until >1yr of data.
- **Rolling 20-trade AI accuracy line** on AI Analytics — "is the advisor calibrating up or down?" Data exists,
  only the time-series view is missing.

## 🔴 SKIP (flashy / redundant — deliberately NOT building)
- Drag-and-drop customizable panel layout — flash, zero workflow value for a single-user localhost tool.
- **In-dashboard backtesting UI** (freqUI/Jesse/Hummingbot) — duplicates the validated `research/` CLI pipeline
  and risks re-introducing the 10x-inflation bug class. Keep backtesting in the pipeline (+ the new test suite).
- Mobile-first app / push UI (OctoBot) — Telegram already covers mobile alerts.
- TradingView MFE/MAE excursion bars — needs per-trade max-adverse/favorable logging CCBT doesn't do.
- Plot Configurator (named indicator sets per pair) — over-engineering for a config-defined fixed roster.
- Monte Carlo / Pine Script export — research-tool territory, not operations.
- Order-book depth / Executor Distribution — CCBT bots don't place resting limit ladders.
- Responsive sidebar collapse / hamburger — low priority for a localhost Mac tool.

## Suggested batch / sequence
- **Batch 1 (operational, highest value — endpoints exist)**: #1 mode buttons + #2 STOP-ALL + #4 alert banner.
  (Security note: these are state-changing — reuse the token-gated control endpoints + the PANIC confirm dialog;
  bulk PANIC already debounced server-side. One focused PR + review.)
- **Batch 2 (live-trade context)**: #3 SL/TP+R:R in UpnlPanel + #5 today's-PnL/notional header.
- **Batch 3 (analytics polish)**: #6 underwater chart + #7 symbol labels + #8 table filter.
- CONSIDER items scheduled individually as their data dependency is added (e.g. daily-loss bar + liq price when
  moving toward mainnet).

## Notes
- Everything ADD_NOW = no new exchange API call, respects never-interfere + no-math-in-TS.
- Each batch = its own branch + TDD + multi-lens review (the project's established flow).
