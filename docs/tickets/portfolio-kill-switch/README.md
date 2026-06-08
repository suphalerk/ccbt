# Ticket — Process-wide Portfolio Kill-Switch

**Status:** DESIGN REVISED (v2, post-adversarial review) — not yet implemented
**Flag:** `CCBT_KILL_SWITCH` (default OFF — flag-OFF == byte-for-byte today)
**Owner:** bot process (`main_multi.py` / `PortfolioManager`)
**Validate:** Binance testnet first, per house rule for risky autonomous features.

---

## 1. Problem

Each bot has its OWN `bot/risk.py` `RiskManager` with a per-bot daily-loss breaker
(`max_daily_loss`, default 9% of that bot's starting balance) and a consecutive-loss
cooldown. There is **NO portfolio-level aggregate breaker today**. ~59 bots run in ONE
process (`main_multi.py`) sharing a single netted exchange wallet. A correlated drawdown
(market-wide crash) can blow past an acceptable **aggregate** loss while EVERY individual
bot is still under its own per-bot limit — so nothing halts. This ticket fills exactly
that gap: an aggregate, process-wide loss breaker that stops new risk (and optionally
flattens) before the account bleeds out.

The dashboard (`api/`, separate launchd process `com.ccbt.dashboard-v2`) can already
**detect** portfolio drawdown (`api/markprice.py` `net_today` = realized + live
unrealized) but it runs in a different process with a read-only SQLite handle — it can
only write mode files, it cannot set an in-process `is_halted` flag or gate entries. The
**enforcing** code MUST live in the bot process. This is the central constraint.

---

## 2. Architecture

### Where it lives
- **Enforcing aggregate = `PortfolioManager`** (`main_multi.py:73`). It is the one shared
  object every `TradingEngine` already references (wired at `main_multi.py:591-605` via
  `run_bot(..., portfolio_manager=...)`), it already serialises state under an
  `asyncio.Lock`, and the entry path already calls `portfolio_manager.can_open(symbol)`
  before every new position. Adding the breaker here gives a race-free, zero-file-write
  entry gate that stops ALL ~59 bots at their next signal evaluation.
- **A single async monitor task** (one per process, NOT per bot) created in `async_main`
  right next to the telegram task (`main_multi.py:609-615`). It recomputes the aggregate
  loss metric on a fixed interval (~15s), applies hysteresis, and on trip sets the flag +
  optionally writes mode files + sends one Telegram alert.

### No new exchange calls
The breaker reads loss from a **local SQLite read** (see §3 for the correct query) plus,
optionally, an **in-process markPrice WS client** for live unrealized (key-free, public;
same source `api/markprice.py` uses). It never adds a per-bot REST fan-out (the very
`-1003`/`418` problem the shared-marketdata cache exists to avoid). The balance
denominator is **equity** captured once at arm-time (see §3 for the exact source).

### Flag-gated (default OFF == today)
Everything below is gated on `os.getenv("CCBT_KILL_SWITCH") == "1"`:
- The monitor task is NOT created when OFF (like the `CCBT_USERDATA_WS` pattern at
  `main_multi.py:622`).
- `PortfolioManager.is_halted` defaults `False` and `can_open()` is unchanged when the
  flag is OFF (the new check is `if self._kill_switch_enabled and self.is_halted: return False`).
- No mode files are ever written by the breaker when OFF. Byte-for-byte today.

---

## 3. Trigger metric + threshold + action

### Metric denominator — EQUITY, not free balance (BLOCKER #1 fix)

**`get_balance()` returns FREE USDT, not account equity.** With ~10 concurrent positions,
locked margin is excluded from `raw["free"]["USDT"]`. At startup with positions already
restored, free balance can be a small fraction of total equity. Using it as the
denominator produces false trips (a $300 loss reads as a huge % of a depressed free
balance) or wrong baselines.

**Required:** use `totalWalletBalance` (Binance USDT-M futures — locked + free realized;
excludes unrealized PnL, which is appropriate for a realized-PnL metric). Fetch via
`exchange.fetch_balance()["info"]["totalWalletBalance"]`. Add a `get_equity()` method to
`SharedMarketData` that extracts this field (alongside the existing `get_balance()` for
free-USDT callers).

**Capture once per Bangkok day, not per tick.** `PortfolioManager.start_of_day_equity` is
set at arm time and re-snapshotted at each Bangkok midnight rollover. The 30s-TTL
live-balance is NOT used as the denominator. A missing or zero equity value is a
DEGRADED-DATA condition — see the fail-safe rule in §4.

**Restart correctness (MAJOR #2 fix):** `start_of_day_equity` is snapshotted at process
start, but the realized metric buckets PnL by the Bangkok day — if the process restarts
mid-day, the snapshot is the post-loss balance while today's realized loss is already
accounted for, understating `loss_pct`. On startup, back-calculate:

```
start_of_day_equity = current_equity - realized_today_by_close()
```

This reconstructs the true Bangkok-midnight balance. Persist it in
`data/portfolio_halt.json` keyed to the Bangkok date so restarts within the same day
reuse the saved value instead of re-deriving from a potentially already-reduced equity.

### Realized-today metric — bucket by CLOSE time, not open time (BLOCKER #2 fix)

`dashboard/queries.get_today_pnl` currently filters by `DATE(timestamp, '+7 hours')` —
and `timestamp` is the trade OPEN time (`logger.py:272 datetime.now(utc).isoformat()`).
`log_trade_close` (logger.py:308) does an UPDATE that never rewrites `timestamp`. A
position opened yesterday but closed today at a big loss counts on YESTERDAY's Bangkok
day, not today. In a correlated crash, the worst losses are on positions that were open
before the crash — they will NOT appear in today's realized metric until they close, and
when they do close, they're counted on the wrong day. This is a missed-trip on the most
important scenario.

**Fix:** add a `close_timestamp` column (UTC ISO string) populated in `log_trade_close`,
and write a `get_realized_since(ts)` / `get_today_realized_by_close()` query that filters
on this column. The portfolio monitor uses this new query as its realized metric. The
existing `get_today_pnl` remains for the dashboard (its open-time bucketing is acceptable
for display, but it MUST NOT be the trip-wire denominator).

Migration path: `ALTER TABLE trades ADD COLUMN close_timestamp TEXT;` (NULL for
pre-existing rows). The new query must handle NULLs gracefully (treat them as the row's
open-time as a fallback for legacy rows, not as today).

### Halt-new mechanism — in-process gate ONLY for Tier 1 (BLOCKER #3 fix)

**Writing GRACEFUL_STOP to a flat bot is FATAL.** `engine.py` GRACEFUL_STOP branch
(lines 961-963) breaks the main loop when `_tracked_trades` is empty
(`graceful_stop_complete` → `break`). `engine.run()` returns permanently — `main_multi.py`
awaits it exactly once with no outer restart loop (`run_bot:401`). So a Tier 1 trip
that fans GRACEFUL_STOP to all ~59 symbols kills every flat bot's coroutine permanently.
After the daily-reset, writing NORMAL back to the mode files has no effect — the
coroutines are gone. The fleet dies until a manual process restart.

**Architecture for Tier 1 (HALT-NEW):**

The `PortfolioManager.is_halted + can_open()` gate is the **sole** Tier 1 enforcement
mechanism. It blocks all new entries process-wide without touching mode files or killing
flat bots. It is already race-free (inside the existing `_lock`). No mode files are
written on a Tier 1 trip.

For open positions on a Tier 1 trip: the bots holding positions continue their normal
GRACEFUL_STOP-style behaviour because their SL/TP are already on the exchange. If the
user also wants a wind-down of open positions at Tier 1, they can do so via the dashboard
STOP-ALL button (which writes GRACEFUL_STOP to position-holders only). The breaker does
NOT write GRACEFUL_STOP on its own for Tier 1 — this avoids the flat-bot-kill hazard
entirely.

For Tier 2 (FLATTEN): PANIC mode files are written only to symbols that currently hold an
open position (filter the config list by `PortfolioManager._open_coins` before writing).
A PANIC-and-exit is intentional for position-holders; flat bots are untouched.

### Mode-file path resolution (BLOCKER #4 fix)

`read_bot_mode` (engine.py:891) uses `data_dir="data"` (cwd-relative default). The bot
process CWD is the repo root (launchd sets `WorkingDirectory`), so `"data"` resolves to
`<repo>/data`. The kill-switch must write to **exactly the same directory**.

**Required:** the monitor resolves `data_dir` identically to the engine:

```python
data_dir = os.environ.get("BOT_DATA_DIR", "data")
```

This is the same pattern used at `bot/engine.py` heartbeat resolution. Do NOT copy the
dashboard's `_get_data_dir()` (which uses an absolute project-root path — that works for
the dashboard but is a different resolver). At monitor startup, assert and log that the
resolved `data_dir` matches what `read_bot_mode` would read. Add a Phase-0 smoke test
confirming a breaker-written mode file is readable by `read_bot_mode` with the same
default args the engine uses, under both `BOT_DATA_DIR` set and unset.

### Denominator fail-safe — degraded-data safety (BLOCKER #5 fix)

`start_of_day_equity` is derived from a single exchange call that can fail silently at
startup (`main_multi.py:543` logs a warning and continues with `get_balance()` returning
None or 0). A zero or None denominator causes either a divide-by-zero crash (silent
missed-trip) or an infinite `loss_pct` (false trip everything).

**Rules:**

1. On startup, if `startup_warm_balance` fails, the monitor calls `get_equity(fresh=True)`
   with up to 3 retries (1s backoff) before arming.
2. If still unavailable after retries, log `killswitch_disarmed_no_equity`, send one
   Telegram alert, and keep `is_halted=False`. The monitor continues to loop but skips
   the trip path until a valid equity is obtained.
3. When equity is eventually obtained (next successful call), arm normally and log
   `killswitch_armed`.
4. At no point is `start_of_day_equity` allowed to be zero or None while the trip path
   runs. Guard every `loss_pct` computation with `if not self.start_of_day_equity: skip`.
5. A missing/stale equity does NOT auto-clear an existing halt — the halt persists until
   manually cleared or the Bangkok rollover (with fresh equity) resolves it.

### Threshold table (revised)

Two-tier, prop-firm style. Defaults are reasoned estimates only — see Open Decisions for
the mandatory pre-deployment measurement.

| Tier | Default env | Metric | Action |
|------|-------------|--------|--------|
| **Tier 1 — HALT-NEW** (default deployed) | `CCBT_KILL_HALT_PCT=6.0` | realized_today_by_close **or** net_today | `is_halted=True` (in-process gate; NO mode files for Tier 1) |
| **Tier 2 — FLATTEN** (opt-in) | `CCBT_KILL_FLATTEN_PCT=10.0` | realized_today_by_close only (robust) | PANIC mode files written to position-holders ONLY (unique coins in `_open_coins`), staggered |
| **Co-trigger** (StoplossGuard-style) | `CCBT_KILL_STOP_COUNT=5` / `CCBT_KILL_STOP_WINDOW_H=2` | ≥N hard `stop_loss` closes portfolio-wide in window | Tier 1 HALT-NEW |

`CCBT_KILL_FLATTEN_ENABLE` defaults `0` (Tier 2 OFF) — a mass PANIC sells everything at
the worst tick. Tier 2 is opt-in and should only be enabled after testnet validation.

**Note on -6% default (MINOR #4 fix):** The -6% Tier 1 threshold is NOT validated against
CCBT's actual equity-curve drawdown distribution. With ~10 concurrent 2%-risk positions,
a correlated SL cluster on a normal choppy day can realize -6% without a true blowup. Do
NOT ship -6% as the live default until the equity-curve replay in Open Decision 1 is
completed — that measurement is a **hard pre-requisite for enabling the kill-switch live**
(Phase 1 gate). Set `CCBT_KILL_HALT_PCT` conservatively higher (e.g. 8-10%) until the
replay determines the noise floor. Keep it env-tunable.

### Action (revised for BLOCKER #3)

1. **Entry gate (always, cheapest):** set `PortfolioManager.is_halted=True` under `_lock`
   **FIRST**, before any other action. This is the race-safe point — from this moment every
   bot's `can_open()` returns `False`. Do this before writing any mode files.
2. **Tier 1:** no mode files written by the breaker. `is_halted=True` is the complete
   Tier 1 action. Existing positions ride their own SL/TP on the exchange.
3. **Tier 2 flatten (opt-in):** build the actuation set from
   `PortfolioManager._open_coins` (unique coins with open positions). Write
   `BotMode.PANIC` to mode files for those symbols only, staggered at ~200ms intervals
   (matching the `_STAGGER_S` launch pattern) to spread the close fan-out. Each write is
   wrapped in `try/except`; failures are collected and retried on the next ~15s sample.
   Record the full actuation set (including failures) in `data/portfolio_halt.json`
   under `"acted_symbols"` before writing any file, so a mid-loop crash is recoverable.
   **Verify flat** by re-polling positions for a bounded window after all writes; emit a
   CRITICAL Telegram alert listing any symbol still open. A mode-write is NOT proof of
   flat — `close_all_positions()` can RAISE on hedge-mode/non-`-2022` errors.

Mode writes are idempotent within a tier transition. The breaker does NOT re-write mode
files on every monitor loop — only on the trip transition itself.

---

## 4. Reset policy + anti-flap

- **Tier 1 (HALT-NEW): daily auto-reset at the Bangkok day rollover.** On rollover:
  re-derive `start_of_day_equity` from fresh `get_equity()` call, re-snapshot the Bangkok
  date in `data/portfolio_halt.json`, clear `is_halted` — BUT ONLY if the freshly
  recomputed `loss_pct` on the NEW Bangkok day (realized_today_by_close) is below the
  (hysteresis-confirmed) Tier 1 threshold. If the loss carried into the new day exceeds
  the threshold, stay halted and re-alert. This prevents re-arming mid-crash (MAJOR #6
  fix).
- **Tier 2 (FLATTEN): manual re-arm only** (dashboard button / Telegram). A same-day
  catastrophic flatten must NOT silently come back.
- **Anti-flap is automatic:** once tripped, STAY tripped until the next scheduled daily
  reset (Tier 1) or manual clear (Tier 2) — there is no same-day PnL-recovery
  auto-resume. No halt→resume→halt cycle.
- **Confirmation / hysteresis:** require the threshold to be breached on **N consecutive
  distinct monitor samples** (default `CCBT_KILL_CONFIRM_SAMPLES=3`, ~45s) before
  tripping. "Distinct" means the underlying realized query must have been re-read (TTL >
  15s loop period so the 5s TTL is always expired between samples — assert this). Count
  consecutive breaches; use a **leaky/decay counter** rather than strict reset-to-zero so
  a metric oscillating around the threshold (e.g. -6.1%, -5.9%, -6.2%...) due to noise
  can still accumulate to a trip during a genuine sustained drawdown. Recommendation:
  "M of last N" where M=3, N=4 (MINOR #2 fix).
- **Fail-safe on degraded data:** if the markPrice WS feed is stale/disconnected or the
  realized query returns an exception, do NOT compute a phantom 0 (missed-trigger trap)
  and do NOT auto-PANIC on null. Hold last-known-good, and at most escalate to Tier 1
  entry-gate (i.e. flip `is_halted=True` if not already). Alert on degraded data. A
  missing equity denominator prevents ALL metric computation — see §3 degraded-data rules.
- **Persistence:** the trip writes `data/portfolio_halt.json` (reason, tier, threshold,
  metric snapshot, UTC timestamp, Bangkok date, `start_of_day_equity`, `acted_symbols`
  list). On startup the monitor reads it:
  - If `tier == 2` and the Bangkok date matches today: restore `is_halted=True`, stay
    halted until manual clear.
  - If `tier == 1` and the Bangkok date matches today: restore `is_halted=True`, stay
    halted until Bangkok rollover (MAJOR #5 fix — Tier 1 must also persist across
    restart, not just Tier 2).
  - If Bangkok date != today: treat as expired, do NOT restore halt.
  This resolves the inconsistency that mode files persist across restart but in-memory
  `is_halted` does not, and prevents a mid-day restart from silently re-arming entries
  after a Tier 1 trip.

---

## 5. Telegram alert

Reuse `bot/telegram.py send_alert` (already imported in `async_main` at
`main_multi.py:640`). Send ONE alert per state transition from the monitor (the breaker's
own alert). Per-bot PANIC handlers (engine.py:906) will ALSO fire their own alerts on
Tier 2 — this is expected and unavoidable (each bot's engine runs the PANIC branch
independently). Do NOT assert "exactly one alert per fleet transition" — scope that claim
to the monitor's own alert only. If the per-bot PANIC alert burst is unacceptable, add a
`"silent": true` field to the PANIC mode file that the engine checks before calling
`send_alert` (MAJOR #4 fix — opt-in per-bot silence).

Alert content per transition:
- Trip: tier, metric value, threshold, `start_of_day_equity`, realized vs unrealized
  split, action taken, list of open symbols (Tier 2: list of symbols whose mode files
  were written).
- Tier-2 residual-not-flat: CRITICAL alert listing symbols still open after PANIC verify.
- Daily auto-reset (Tier 1 cleared): one informational alert.
- Degraded-data fail-safe: one alert.
- Kill-switch disarmed (no equity): one alert at startup.

Honour `tests/conftest.py` autouse telegram block — tests must NEVER send real Telegram.

---

## 6. Components (numbered, with file targets)

1. **`PortfolioManager` breaker state** — `main_multi.py:73`
   Add: `self._kill_switch_enabled: bool`, `self.is_halted: bool`, `self.halt_reason: str`,
   `self.halt_tier: int`, `self.start_of_day_equity: float`, `self._halt_bangkok_date: str`
   (YYYY-MM-DD for persistence check), `self._daily_pnl: dict[str,float]` (optional
   in-memory path), and async helpers `report_daily_pnl(symbol, pnl)`, `trip(tier,
   reason)`, `clear(tier)`. Guard all mutations with the existing `_lock`.

2. **`can_open()` gate** — `main_multi.py:106-118`
   First line inside the lock: `if self._kill_switch_enabled and self.is_halted: return False`.

3. **Monitor task** — NEW `bot/portfolio_killswitch.py`, created in `async_main`
   (`main_multi.py` near :609). `async def run_portfolio_killswitch(shutdown_event,
   portfolio_manager, shared_market_data, configs, data_dir)`. Loops every ~15s: compute
   metric, apply hysteresis, trip/clear, write mode files (Tier 2 only, position-holders
   only), verify-flat (Tier 2), alert, manage Bangkok daily reset + lock file. Created
   ONLY when `CCBT_KILL_SWITCH=1`.

4. **Metric source (revised):**
   - **Equity:** new `SharedMarketData.get_equity()` reading
     `fetch_balance()["info"]["totalWalletBalance"]` (Binance USDT-M). Captured once per
     Bangkok day; NOT used from the 30s-TTL live cache as the denominator.
   - **Realized:** NEW `get_today_realized_by_close(db_path)` query in
     `dashboard/queries.py` filtering on `close_timestamp` (after the schema migration to
     add that column). Legacy rows with NULL `close_timestamp` fall back to `timestamp`
     (trade-open time) as a degraded approximation.
   - **Unrealized (optional):** in-process markPrice WS client factored from
     `api/markprice.py` (`MarkPriceClient`) for `net_today = realized + total_upnl`.
     Used only for Tier 1 early-warning; never drives Tier 2 FLATTEN.

5. **Mode actuation (revised):**
   - Tier 1: NO mode file writes. `is_halted=True` in `PortfolioManager` is the complete
     action.
   - Tier 2: `bot/mode.write_bot_mode(sym_clean, BotMode.PANIC, data_dir)` per symbol in
     `PortfolioManager._open_coins` (unique position-holders). Symbol list is
     deduplicated by coin. Writes are staggered ~200ms. Each write is individually
     try/excepted; failures collected and retried. `is_halted=True` is set FIRST (under
     `_lock`) before any file write.
   - `data_dir` resolved as `os.environ.get("BOT_DATA_DIR", "data")` — same as engine
     reads. NOT the dashboard's `_get_data_dir()`.

6. **Lock-file persistence** — `data/portfolio_halt.json`. Schema:
   ```json
   {
     "tier": 1,
     "reason": "realized_loss_pct",
     "threshold_pct": 6.0,
     "metric_snapshot": -6.4,
     "start_of_day_equity": 4823.15,
     "bangkok_date": "2026-06-08",
     "utc_timestamp": "2026-06-08T09:15:00Z",
     "acted_symbols": ["BTCUSDTUSDT", "ETHUSDTUSDT"],
     "acted_mode": "panic"
   }
   ```
   Atomic tempfile+rename write (same pattern as `write_bot_mode`). Includes
   `acted_symbols` for safe reset (see §7 reset blast-radius fix).

7. **Daily reset (mode revert) — blast-radius control (MAJOR #7 fix):**
   On Bangkok rollover (Tier 1 clear) or manual Tier 2 re-arm, revert ONLY symbols in
   `portfolio_halt.json["acted_symbols"]`, AND ONLY if their current on-disk mode still
   equals `acted_mode`. Re-read each mode file before reverting — if a user changed it in
   the interim, leave it. This makes test 8 implementable. A user-set GRACEFUL_STOP on
   some bot is never clobbered by the breaker reset.

8. **Dashboard surfacing (optional, read-only)** — `api/` can read
   `data/portfolio_halt.json` to show breaker state + a manual Tier-2 re-arm button that
   deletes the lock file. No enforcement in `api/`.

9. **Config / env wiring** — document the following in `CLAUDE.md` Environment Variables:
   `CCBT_KILL_SWITCH`, `CCBT_KILL_HALT_PCT`, `CCBT_KILL_FLATTEN_PCT`,
   `CCBT_KILL_FLATTEN_ENABLE`, `CCBT_KILL_CONFIRM_SAMPLES`, `CCBT_KILL_STOP_COUNT`,
   `CCBT_KILL_STOP_WINDOW_H`, `CCBT_KILL_INCLUDE_UNREALIZED`.

---

## 7. Failure modes + mitigations

| Failure mode | Mitigation |
|---|---|
| **Process-boundary mistake** (logic put in `api/`) | Enforcing code MUST be in `main_multi.py`/`PortfolioManager`; `api/` is read-only. Documented at top of ticket. |
| **Wrong denominator** (free balance ≠ equity) | Use `totalWalletBalance` via new `get_equity()`; never use `get_balance()` (free USDT) as the loss % denominator. Test asserts denominator == equity when positions are open. |
| **Realized metric mis-buckets by open time** | New `get_today_realized_by_close()` query on `close_timestamp`; schema migration adds that column. Old `get_today_pnl` retained for dashboard display only. |
| **Flat-bot coroutine killed by GRACEFUL_STOP** | Tier 1 uses ONLY the in-process `is_halted` gate — NO mode file writes. GRACEFUL_STOP/PANIC mode files written only for Tier 2, and only to position-holders. |
| **Mode-file path mismatch** | Breaker resolves `data_dir = os.environ.get("BOT_DATA_DIR", "data")` — identical to engine. Smoke test asserts write/read path agreement. |
| **Missing equity denominator on startup** | Retry up to 3x; if still unavailable, `killswitch_disarmed_no_equity` + alert; skip trip path until equity obtained. No phantom 0 division. |
| **Mid-day restart loses Tier 1 halt** | Persist Tier 1 in `portfolio_halt.json` with Bangkok date; restore `is_halted=True` on startup if date matches today. |
| **Baseline drift on restart** | Back-calculate `start_of_day_equity = current_equity - realized_today_by_close()` on startup if same Bangkok day; persist result. |
| **False trip on mark-wick / stale WS** | Hysteresis (M of N consecutive samples, leaky counter) + unrealized never drives Tier 2 + fail-safe on null (never trip on missing data). |
| **Missed trip during open-position crash (4H roster)** | Unrealized path (opt-in, `CCBT_KILL_INCLUDE_UNREALIZED`) drives Tier 1 early-warning. Concede: realized-only CANNOT fire until SLs close. Size thresholds must account for this latency; validate against 4H crash replay (Open Decision 1). |
| **Missed trip (WS down during crash / silent query exception)** | Always run the realized floor (local SQLite, robust); don't substitute 0 on exception — treat throw as degraded-data → escalate to Tier 1 + alert. |
| **Flap** (halt→resume→halt) | Once tripped, stay tripped to next Bangkok reset; no PnL-recovery auto-resume; Tier 2 needs manual re-arm. |
| **Tier 1 restart persistence hole** | Mode files persist on disk; `portfolio_halt.json` re-arms `is_halted=True` for Tier 1 on same Bangkok day. |
| **Partial-apply across ~59 mode writes (Tier 2)** | `is_halted=True` set FIRST; actuation set written to lock file before writes begin; each write individually try/excepted; failures retried on next sample; verify-flat reconciles against intended set, not just open positions. |
| **Double-alert burst (per-bot PANIC + breaker alert)** | Accept as expected; scope the "one alert" claim to the monitor only. Optional: `"silent": true` flag in mode file for per-bot PANIC alert suppression. |
| **Per-bot PANIC flood (AXS×7)** | Deduplicate PANIC target set by unique coin from `_open_coins`; stagger writes ~200ms; idempotent close path handles already-flat. |
| **Rate-limit storm on mass PANIC close** | Staggered mode writes spread the close fan-out; one PANIC per unique coin (AXS×7 → one PANIC file); shared-market-data cache for balance/OHLCV (positions never cached — this is by design). |
| **PANIC partial failure** | `close_all_positions` RAISES on hedge-mode/non-`-2022`; engine logs `panic_close_failed` and breaks (position left LIVE). Controller MUST verify-flat + CRITICAL alert on residual. Never report success on mode-write alone. |
| **Denominator double-count** | Use the single account `totalWalletBalance` once, NOT sum of per-bot `starting_balance`. |
| **Day-boundary mismatch** | Baseline + realized BOTH on Bangkok day (match dashboard). Per-bot `reset_daily` is UTC (7h offset) — deliberate divergence; documented in CLAUDE.md. During the 7h window per-bot and portfolio disagree on "today" — accept as known gap, sized via Open Decision 1. |
| **Rollover re-arm mid-crash** | On rollover, re-compute `loss_pct` for the new day before clearing `is_halted`; stay halted if already above threshold. |
| **Manual-mode clobber on reset** | Reset reverts only symbols in `acted_symbols` whose on-disk mode still equals `acted_mode`. User-set modes are never clobbered. |
| **Interaction with per-bot breaker** | Complementary: per-bot catches one bot; portfolio catches aggregate. Portfolio Tier 1 = entry-gate (no mode writes). Set portfolio threshold above the noise floor (measured per Open Decision 1) and ensure clearing one layer doesn't un-halt the other. |
| **Halt latency on 4H bots** | `can_open` stops a bot at its next signal eval (4H bots hours away). For open positions mode files (Tier 2 only) are read every tick. Entry gate alone is sufficient for Tier 1 since we're not trying to close positions, only block new ones. |

---

## 8. Test plan (TDD — fakes, no live, honour conftest telegram block)

All tests in `tests/`, no exchange/network, no real Telegram (autouse `conftest.py` guard).

1. **`can_open` gate** — `PortfolioManager` with flag ON + `is_halted=True` → `can_open()`
   returns `False` for any symbol; with flag OFF + `is_halted=True` → still returns `True`
   (proves flag-OFF == today).
2. **Denominator uses equity, not free** — fake `get_equity()` returns 5000.0 while
   `get_balance()` (free) returns 1200.0 (positions open). Assert `loss_pct` denominator
   == 5000.0. Feed fake `get_today_realized_by_close()` = -300 → `loss_pct` == 6.0%,
   not 25.0%.
3. **Threshold trip (realized by close time)** — feed `get_today_realized_by_close()`
   fake returning -6.1% → Tier 1 trips after `CCBT_KILL_CONFIRM_SAMPLES` consecutive
   samples, not on the first sample.
4. **Hysteresis (leaky counter)** — oscillating samples (-6.1%, -5.9%, -6.2%, -5.8%)
   around the threshold: M=3-of-N=4 → trips. Single -7% then recovery → NO trip (strict
   consecutive); 3 consecutive -7% → trip. Verify each sample reads a fresh realized
   value (not a cached hit).
5. **Fail-safe on degraded data** — fake WS `feed_status='stale'` / realized query raises
   → no phantom-0 trip, escalate at most to Tier 1, alert fired (mocked). Zero/None
   equity → monitor skips trip path, logs `killswitch_disarmed_no_equity`, sends alert.
6. **Tier ladder** — -6.5% → `is_halted=True`, NO mode files written (Tier 1); with
   `CCBT_KILL_FLATTEN_ENABLE=1` and -10.5% realized → PANIC written only to symbols in
   `_open_coins` (not to flat bots), staggered; flat bots' mode files untouched.
7. **Idempotent mode writes** — trip twice → `write_bot_mode` called once per transition
   (spy), not every loop. `is_halted` set FIRST before any mode write (ordering assertion).
8. **Daily reset (Tier 1) — blast-radius** — simulate Bangkok rollover with
   `acted_symbols=["BTCUSDTUSDT"]` in lock file; only BTCUSDTUSDT reverted to NORMAL;
   a user-set TP_ONLY on ETHUSDTUSDT left untouched; `is_halted` cleared (new-day
   `loss_pct` < threshold). Also: if new-day loss already above threshold → `is_halted`
   stays True.
9. **Tier 2 manual-reset persistence** — lock file present on startup (same Bangkok date,
   tier=2) → monitor restores `is_halted=True`; deleting the file → re-arms.
   Tier 1 lock file (same day) → also restores `is_halted=True` (mid-day restart safe).
10. **Verify-flat after PANIC** — fake exchange returns a residual open position → CRITICAL
    alert (mocked) listing that symbol; no false "flattened" success.
11. **Co-trigger** — feed ≥5 `stop_loss` closes within window via fake DB → Tier 1 trips
    on count even when `%` not hit.
12. **Flag-OFF byte-for-byte** — with `CCBT_KILL_SWITCH` unset, monitor task not created,
    no mode files written, `can_open` unchanged across a simulated drawdown.
13. **Mode-file path agreement** — `write_bot_mode` (breaker resolver) and `read_bot_mode`
    (engine default) produce the same path under both `BOT_DATA_DIR` set and unset.
14. **Realized-by-close query** — trade opened yesterday, closed today at a loss → appears
    in `get_today_realized_by_close()` today (not yesterday). Trade opened today, still
    open → not counted. NULL `close_timestamp` (legacy row) falls back to open-time.
15. **Restart equity reconstruction** — simulate restart at 14:00 Bangkok with
    current_equity=4700, realized_today_by_close=-300 → `start_of_day_equity` reconstructed
    to 5000. Persisted value reused on second restart in same day without re-deriving.
16. **Partial-apply / actuation set** — write 3 mode files, 2nd raises ENOSPC. Assert:
    lock file records all 3 in `acted_symbols`; failed symbol flagged in CRITICAL alert;
    retry on next sample writes only the failed symbol; verify-flat checks all 3.
17. **Fakes:** fake `get_today_realized_by_close`, fake `SharedMarketData.get_equity`,
    fake markprice snapshot, spy on `write_bot_mode` + `send_alert`. `fetch_*` fakes
    return real ccxt shapes (`fetch_ohlcv` → `list[list]`), per house rule.

---

## 9. Flag-OFF == byte-for-byte today

- `CCBT_KILL_SWITCH` unset/`0`: monitor task not created; `PortfolioManager.is_halted`
  exists but the `can_open` check is skipped
  (`if self._kill_switch_enabled and self.is_halted`); no mode files written by the
  breaker; no lock file read/written. Identical to current behaviour.

---

## 10. Open decisions (for the user)

1. **Threshold values** — The -6% Tier 1 / -10% Tier 2 defaults are NOT measured against
   CCBT's equity-curve drawdown distribution. **MUST replay portfolio equity curve before
   enabling live.** This is a hard pre-requisite (Phase 1 gate), not an optional
   recommendation. In the interim, default to a conservative value derived from the
   observed worst normal daily drawdown in `trades.db` (e.g. `SELECT MIN(DATE_pnl) FROM
   ...`). Confirm the number doesn't false-trip on normal intraday variance with ~10 open
   2%-risk positions.
2. **Halt-vs-flatten default** — deploy with Tier 1 (in-process entry gate only,
   recommended safe default) only, with Tier 2 (PANIC mode files to position-holders)
   behind `CCBT_KILL_FLATTEN_ENABLE`? Or enable Tier 2 from the start?
3. **Reset policy** — confirm: Tier 1 daily auto-reset at Bangkok rollover (with
   re-check before clearing), Tier 2 manual re-arm only?
4. **Include unrealized?** — realized-only (robust, lags up to a full 4H candle) for the
   trip, or realized + live-unrealized (`net_today`, timely but mark-noise-prone, requires
   in-process markPrice WS client)? **For a 4H roster, realized-only cannot fire during
   an open-position crash until SLs hit** — the FLATTEN floor is slow when you need it
   most. Unrealized path adds WS complexity but catches the crash earlier. Recommendation:
   realized for FLATTEN floor, unrealized as early-warning for Tier 1 only (never PANIC
   on WS noise). Validate with a 4H crash replay (Open Decision 1).
5. **Day boundary** — Bangkok (matches `get_today_realized_by_close` + dashboard) vs UTC
   (matches per-bot `reset_daily`)? Recommendation: Bangkok, to agree with the dashboard.
   Document the 7h offset vs per-bot in CLAUDE.md.

---

## 11. Phasing

- **Phase 0 — scaffolding (flag OFF):** add `PortfolioManager` breaker fields + `can_open`
  guard (no-op while flag OFF) + `bot/portfolio_killswitch.py` skeleton + env docs +
  `close_timestamp` schema migration + `get_today_realized_by_close()` + `get_equity()`.
  Tests 1, 2, 12, 13, 14, 15. No behaviour change.
  **Gate:** schema migration deployed and validated before Phase 1 can begin.

- **Phase 1 — realized Tier 1 (testnet):** monitor task reads
  `get_today_realized_by_close()`, leaky-counter hysteresis, trip → `is_halted=True`
  (NO mode files) + Telegram + Bangkok daily reset + lock file persistence + restart
  restore. Tests 3, 4, 5, 6 (Tier 1), 7, 8, 9 (Tier 1), 11, 16.
  **Gate:** equity-curve replay (Open Decision 1) completed; threshold confirmed not to
  false-trip on historical data. Validate on testnet.

- **Phase 2 — Tier 2 flatten + verify-flat (opt-in):** PANIC mode files (position-holders
  only, staggered) behind `CCBT_KILL_FLATTEN_ENABLE`; verify-flat; manual re-arm; partial-
  apply retry. Tests 6 (Tier 2), 9 (Tier 2), 10, 16 (extended).

- **Phase 3 — unrealized early-warning + co-trigger (opt-in):** in-process markPrice WS
  client, fail-safe on degraded data, StoplossGuard-style count co-trigger. Tests 5
  (extended), 11.

- **Phase 4 — dashboard surfacing:** read-only breaker state + manual re-arm button in
  `api/`.

---

## 12. Review v1 → addressed

| Finding | Resolution in v2 |
|---------|-----------------|
| **BLOCKER 1** Free balance ≠ equity | §3: new `get_equity()` using `totalWalletBalance`; `get_balance()` (free) never used as denominator. Test 2 added. |
| **BLOCKER 2** Open-time bucketing misses crash losses | §3: new `close_timestamp` column + `get_today_realized_by_close()` query. Schema migration in Phase 0. Tests 14, 15 added. |
| **BLOCKER 3** GRACEFUL_STOP kills flat bots permanently | §3 Action redesigned: Tier 1 uses `is_halted` gate ONLY — no mode file writes. PANIC (Tier 2) written only to `_open_coins`. GRACEFUL_STOP not used by the breaker at all. Test 6 updated. |
| **BLOCKER 4** Mode-file path resolver divergence | §6.5: breaker uses `BOT_DATA_DIR` env with fallback `"data"` — same as engine. Test 13 added. |
| **BLOCKER 5** Zero/None equity → divide-by-zero or never-trip | §3 fail-safe: 3-retry arm, `killswitch_disarmed_no_equity` state, skip trip path until valid equity. Test 5 updated. |
| **MAJOR 1** Unrealized blind spot on 4H roster | §7 + Open Decision 4: conceded as known gap for realized-only; unrealized path escalates Tier 1 (Phase 3); threshold backtest mandatory (Open Decision 1). |
| **MAJOR 2** Mid-day restart baseline mismatch | §3: `start_of_day_equity = current_equity - realized_today_by_close()` on same-day restart; persisted in lock file. Test 15 added. |
| **MAJOR 3** Partial-apply + race on `is_halted` vs mode writes | §3 Action: `is_halted=True` set FIRST under lock; actuation set written to lock file before any write; per-file try/except + retry; verify-flat against intended set. Tests 7, 16 updated/added. |
| **MAJOR 4** Double-alert burst (per-bot PANIC + breaker) | §5: accepted as expected; optional `"silent"` flag in mode file for per-bot suppression. Test note added. |
| **MAJOR 5** Tier 1 restart persistence hole | §4: `portfolio_halt.json` persists Tier 1 too; same-day restart restores `is_halted=True` for both tiers. Test 9 updated. |
| **MAJOR 6** Rollover re-arms mid-crash | §4: on rollover, re-compute `loss_pct` before clearing; stay halted if still above threshold. Test 8 updated. |
| **MAJOR 7** Reset blast-radius (user-set modes clobbered) | §6.7: reset reverts only `acted_symbols` where on-disk mode == `acted_mode`. Lock-file schema includes `acted_symbols` + `acted_mode`. Test 8 updated. |
| **MAJOR 8** Tier 2 rate-limit storm / stagger | §3 Action + §7: deduplicate to unique coins; stagger ~200ms; note Tier 2 default-OFF is the primary mitigation. |
| **MINOR 1** TTL cache hit counting as distinct hysteresis sample | §4: assert loop period (15s) > TTL (5s); force fresh read per sample. Test 4 updated. |
| **MINOR 2** Hysteresis counter reset on single sub-threshold sample | §4: leaky/decay counter "M of last N" instead of strict reset-to-zero. Test 4 updated. |
| **MINOR 3** data_dir consistency (same as BLOCKER 4) | Resolved with BLOCKER 4. |
| **MINOR 4** -6% default ships without measurement | §3 threshold table: -6% is NOT the default to ship; conservative higher value until equity-curve replay done; Phase 1 gate added. |
