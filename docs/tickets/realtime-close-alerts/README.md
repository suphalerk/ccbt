# Ticket — Realtime TP/SL Close Alerts (user-data WS as TRIGGER, REST as TRUTH)

Status: **PR1 ✅ + PR2 ✅ merged (flag-OFF, dormant) — PR3 (live testnet validation) PENDING user GO**  
Owner: TBD  
Flag: `CCBT_USERDATA_WS=1` (flag-OFF == today's behaviour, byte-for-byte)

**Progress (2026-06-08):**
- **PR1 ✅** (commit b41cc7b) — wake plumbing: `wake_events` registry, 3-way `_interruptible_sleep` race
  (both call sites), `_verify_close_after_ws` (exception-safe, latched `_woke_via_ws`, snapshot cache).
  Review caught a BLOCKER (flag clobber) + the Py3.9 Event-binding doc — fixed. 32+263 tests, flag-OFF parity verified.
- **PR2 ✅** (commit b33d256) — `bot/user_data_stream.py`: dedicated ccxt for listenKey, keepalive,
  run_forever+backoff, SET-ONLY frame handler (`ACCOUNT_UPDATE pa=='0'` primary + `ORDER_TRADE_UPDATE`
  secondary), fail-closed SOCKS (lazy `python-socks` import; `websockets.connect(proxy=...)`), debounced
  reconcile sweep on open DB trades; launched flag-gated in `async_main`. Review caught 2 BLOCKERS (mainnet
  REST domain = `fapi.binance.com` not the WS host; broken proxied connect → native `proxy=` kwarg) + 4
  MAJORS — fixed. 73 tests. Remaining: MAJOR-5 (CancelledError DELETE on overlapping restart) + 10 minors
  are mainnet/proxy ops concerns deferred to PR3.
- **PR3 ⏳ PENDING** — enable `CCBT_USERDATA_WS=1` on **testnet**, restart, validate a real SL/TP fill wakes
  within seconds + routes through `check_closed_positions`, no duplicate alerts, WS-down → candle backstop.
  Needs a bot restart (touches live positions) → **requires explicit user GO**. Mainnet stays flag-OFF.

---

## 1. Problem

Close detection (SL/TP fill → `send_alert` + DB reconcile + orphan-algo cleanup) happens
**once per candle**. A bot blocks in `_sleep_until_next_candle()` and only runs
`check_closed_positions()` at the next candle boundary — so on a 4H bot a TP/SL fill can go
**unalerted and unreconciled for up to ~4 hours**. We want **seconds**, without changing the
audited verify/alert/reconcile math and without re-introducing the prior Telegram-flood
(duplicate-alert) hazard.

## 2. Chosen design (HONORED — do not deviate)

The Binance USDⓈ-M Futures **user-data WS stream is a low-latency TRIGGER ONLY**. It NEVER
alerts, NEVER computes PnL, NEVER touches the DB. When a relevant event fires (an SL/TP
`reduceOnly` order `FILLED`, or a position amount → 0), the WS task **sets a per-engine
`asyncio.Event`** that wakes the owning bot's sleep early. The woken bot then runs the
**existing authoritative path** — `get_positions()` (real position state) →
`check_closed_positions()` / `_infer_close_reason()` — which **VERIFIES the position is
actually absent** before it alerts + reconciles. A bounded **RETRY** loop covers the
WS-leads-REST lag / transient fetch failure. The **per-candle absence-based check remains the
correctness backstop** and is never removed; if the WS is down the system degrades to exactly
today's behaviour.

Critical invariants:
- **Only real-position-absence in `check_closed_positions` may drive an alert** — never the
  raw WS event.
- **A fetch exception is NEVER absence.** If `get_positions()` raises or returns a non-list,
  treat the attempt as PRESENT/ambiguous and do not alert. Never conclude "closed" from a
  failed fetch.
- **`_verify_close_after_ws` performs NO dedup of its own** — it only re-fetches positions
  and re-invokes the unchanged synchronous `check_closed_positions`, which remains the single
  exactly-once arbiter.

---

## 3. Architecture

```
                       ┌──────────────────────────────────────────────────────────────┐
  ONE process-level    │  bot/user_data_stream.py : run_user_data_stream()            │
  WS task (account-    │  • Dedicated ccxt instance (same keys + socksProxy, own      │
  wide, NOT per-bot)   │    requests.Session) for listenKey REST ONLY                 │
                       │  • POST listenKey, PUT keepalive every ~30 min (60-min TTL)  │
                       │  • connect wss://…/ws/<listenKey>  (SOCKS-proxied)           │
                       │  • run_forever + exp backoff (markprice.py skeleton)         │
                       │  • STALE_AFTER watchdog + reconnect + -1125 recreate         │
                       │  • 24h socket cap handled by run_forever reconnect (separate │
                       │    concern from the 60-min listenKey keepalive timer)        │
                       └───────────────┬──────────────────────────────────────────────┘
                                       │ parse frame, map o.s/P[].s → coin
                                       │ ORDER_TRADE_UPDATE  X==FILLED & R==true  OR
                                       │ ACCOUNT_UPDATE      P[].pa=="0"  (preferred;
                                       │   only fires on true flat, not partial-TP)
                                       ▼
              wake_events: dict[str, list[asyncio.Event]]   (keyed by 'BTCUSDT' upper)
              for evt in wake_events.get(coin, []):
                  evt.set()                 ← TRIGGER ONLY; each engine has its OWN Event
                                       │
        ┌──────────────────────────────┼───────────────────────────────┐
        ▼  Engine A (BTC bot #1)                              Engine B (BTC bot #2)
  TradingEngine.run() loop (engine.py)                   (separate private Event, same coin)
  ─ _interruptible_sleep races {shutdown, self._wake_event} vs candle-timeout
  ─ wake-event branch wins → sets self._woke_via_ws=True (captured from which future
    completed, NOT from post-hoc is_set() read) → clears ONLY self._wake_event
  ─ run() loop top: if self._woke_via_ws → _verify_close_after_ws() immediately
    (skip the normal loop-top get_positions / _monitor_positions for this tick —
    verify helper does the authoritative fetch and calls check_closed_positions;
    result is cached for the loop-top path, avoiding a back-to-back double fetch)
  ─ either way: positions snapshot → _monitor_positions() → check_closed_positions()
    ← VERIFY (absence) + classify + send_alert + DB reconcile + cancel orphan algo
      orders + recently_closed dedup (exactly-once; same 60s TTL, unchanged)
```

Key properties:

- **Exactly ONE listenKey / ONE WS for the whole process** (one Binance account → one
  user-data stream carries every symbol's events). Not per-bot.
- **Per-engine private `asyncio.Event`** — the WS handler holds a
  `dict[coin, list[asyncio.Event]]` and `.set()`s each registered engine's own event. No
  shared Event, so no lost-wakeup / double-clear race between sibling bots on the same coin.
  `wake_events.setdefault(coin, []).append(engine_event)` at engine init.
- **`woke_via_ws` captured from race winner** — determined by which `asyncio.Future` completed
  first in the `asyncio.wait()` call, not from a post-hoc `is_set()` read that a sibling may
  already have cleared.
- **Verify is the sole off-cadence close check** — the loop-top `get_positions` +
  `_monitor_positions` is short-circuited (or fed the verify's fresh snapshot) when
  `woke_via_ws` is True, preventing a back-to-back double `get_positions` per woken bot.
- **Single source of truth = `check_closed_positions()`** (`bot/engine.py:145`). The WS only
  changes *when* it runs (within ~1 s of the fill vs up to a full candle later).
- **Bounded retry/backoff** on the verify fetch (`_verify_close_after_ws`) for WS-leads-REST
  lag / transient fetch failure; if still ambiguous after N, **drop the trigger** and let the
  candle backstop handle it.
- **Candle backstop unchanged**: `_sleep_until_next_candle` still computes the candle-boundary
  timeout; `check_closed_positions` still runs once per candle regardless of the WS.
- **Dedicated ccxt for listenKey REST**: NOT the shared trading ccxt instance. Same keys, same
  `socksProxy`, own `requests.Session`. Eliminates thread-safety race (to_thread vs event-loop
  bots both touching the sync shared ccxt) and guarantees REST+WS egress come from the same
  IP.
- **SOCKS-WS library explicit dependency**: `python-socks` (or `aiohttp-socks`) must be in
  requirements and importable. If `CCBT_SOCKS_PROXY` is set but the connector cannot be
  imported, the WS task refuses to start and degrades to candle backstop — never connects
  without the proxy (mainnet IP-leak hazard).
- **Testnet/mainnet derived from shared ccxt** — NOT from `markprice._is_testnet()`. The WS
  host, listenKey REST host, and API keys are all derived from the same exchange object and
  `use_testnet` flag so they cannot point at different environments.

---

## 4. Components (numbered, with file targets)

1. **NEW `bot/user_data_stream.py`** — the account-wide WS trigger task.
   - `async def run_user_data_stream(shutdown_event, shared_exchange, wake_events) -> None`
     where `wake_events: dict[str, list[asyncio.Event]]`.
   - **Testnet/mainnet resolution**: derive `use_testnet` from the `shared_exchange` object's
     config (NOT from markprice's standalone env resolver). WS base URLs:
     testnet `wss://stream.binancefuture.com/ws/<listenKey>`,
     mainnet `wss://fstream.binance.com/ws/<listenKey>`. Add a startup assertion that the WS
     host's domain matches the ccxt's `fapiPrivate` base URL domain.
   - **Dedicated ccxt instance** for listenKey lifecycle: create a second `ccxt.binance`
     (same `apiKey`, `secret`, `socksProxy`, testnet flag) used ONLY for
     `fapiPrivatePostListenKey()`, `fapiPrivatePutListenKey()`, `fapiPrivateDeleteListenKey()`.
     Never touch the shared trading ccxt from this task. This isolates the thread-safety race
     and lets you verify the listenKey-REST egress IP independently.
   - **SOCKS pre-flight** (at task startup, before `run_forever`):
     - If `CCBT_SOCKS_PROXY` is set: attempt to import the SOCKS-WS connector
       (`python_socks` async + `websockets` proxy transport, or `aiohttp_socks`).
     - If import fails or connector test raises: log once at WARNING level and `return` — the
       bot fleet runs on candle-boundary detection, unaffected. Never open a raw WS when a
       proxy is mandated (IP-leak hazard).
     - If `CCBT_SOCKS_PROXY` is unset: connect directly (dev/testnet only scenario).
   - listenKey lifecycle: all three `fapiPrivate*ListenKey` calls are sync methods on the
     dedicated ccxt instance; call via `asyncio.to_thread` against that DEDICATED instance
     only (no shared-ccxt thread safety concern).
   - `run_forever()` + exponential backoff (`BACKOFF_BASE_S=1 → MAX_BACKOFF_S=60`, reset on
     successful connect) — same pattern as `markprice.py:366-388`; `_run_once()` opens the
     socket and reads frames.
   - **Two independent timers** (both run as sibling tasks inside `run_forever`):
     1. **listenKey keepalive** (`LISTEN_KEY_KEEPALIVE_S=1800`, ~30 min): PUT the listenKey.
        On `-1125 'listenKey does not exist'` → POST a fresh key + flag reconnect. The 60-min
        validity window is extended by this PUT; this timer handles nothing else.
     2. **WS connection**: the 24h hard cap is handled naturally by `run_forever` — Binance
        force-closes the socket after 24h; `_run_once` returns, `run_forever` reconnects.
        These are orthogonal concerns; the keepalive PUT does NOT reset the 24h clock.
   - **STALE watchdog**: last-message-age > `STALE_AFTER_S` (start at 60 s; user-data is
     sparse, so this is higher than markprice's 15 s) → close + reconnect. Open question: tune
     against testnet; consider relaxing when no trades are open.
   - **Frame handler** (set-only, idempotent): for each frame, on
     `ORDER_TRADE_UPDATE` where `o.X=="FILLED"` and `o.R==true` and
     `o.o in {STOP_MARKET, TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET, LIQUIDATION}`,
     OR `ACCOUNT_UPDATE` with any `a.P[].pa=="0"` (preferred primary trigger — only fires on
     a true flat position, not partial-TP reductions):
     - map `s` (already `BTCUSDT` upper) to `wake_events.get(coin, [])` and `.set()` each
       registered engine event. Unknown/untracked coins → no-op. Never parse `rp`/`pa` into
       an alert.
     - Note: `ORDER_TRADE_UPDATE` with `R==true` fires on partial-TP fills too (position still
       open). The verify path correctly handles this — it finds the side PRESENT and drops the
       trigger. However, partial-TP/pyramid configs burn the 3-retry budget on spurious wakes.
       The `ACCOUNT_UPDATE pa=="0"` path avoids this for clean full-close detection; prefer it
       where reliable. See open question #4.
   - **Reconnect reconcile sweep**: on every `_run_once` startup (connection or re-connection),
     fire a one-shot reconcile to catch closes that happened while the stream was down.
     - Sweep MUST key on **open DB trades** (`journal.get_open_trades()` filtered to this
       account/coins), NOT on `_tracked_trades` (which is empty for positions that closed
       during a restart gap, exactly the case we need to catch). For coins with an open DB row
       but no `_tracked_trades` entry, trigger a one-shot orphan-reconcile against real
       position state (side absent → `mark closed/orphan_reconcile`).
     - Debounce the sweep: only execute if > `RECONCILE_DEBOUNCE_S` (e.g. 30s) since the last
       sweep, to prevent reconnect storms (proxy flap → fast reconnect loop) from generating
       a get_positions storm per reconnect cycle.
     - Scope: the sweep is a best-effort tightening. Positions that opened AND closed entirely
       during a WS-downtime window remain detectable only by the candle backstop; the sweep
       does not change that guarantee. The design does NOT claim to catch "any close that fired
       while the stream was down" — that claim is scoped to positions with open DB rows.
   - **Shutdown**: on `shutdown_event`, wrap the listenKey DELETE in `asyncio.wait_for(2s)` and
     swallow all exceptions. Do NOT block shutdown on it — the key expires on its own within
     60 min. Ensure `run_forever` propagates `CancelledError` promptly (same discipline as
     `markprice.py`) so WS teardown does not compete with ~59 bots closing positions in
     launchd's tight SIGTERM→SIGKILL window. On clean (non-overlapping) shutdown, prefer
     letting the key expire rather than DELETE-ing it: a DELETE from a dying process can
     invalidate a freshly-started sibling process's stream (Binance returns the SAME key for
     an account; DELETE invalidates it for everyone). Only DELETE on a confirmed sole-process,
     non-overlapping shutdown. Document: only ONE CCBT process per account may run the WS.
   - **Optionality**: if `websockets`/SOCKS import fails or creds missing → log once and
     return; bots unaffected (candle-boundary detection continues unchanged).

2. **EDIT `main_multi.py` (`async_main`, ~:573-601)** — build & launch.
   - `wake_events: dict[str, list[asyncio.Event]] = {}` created before the bot loop.
   - Pass `wake_events=wake_events` into each `run_bot(...)`.
   - Gated by `if os.getenv("CCBT_USERDATA_WS") == "1":` create
     `user_data_task = asyncio.create_task(run_user_data_stream(shutdown_event, shared_exchange,
     wake_events), name="user-data-ws")` and `tasks.append(user_data_task)` — a structural
     sibling of `telegram_task`. Adjust `bot_count = len(tasks) - N` accounting.

3. **EDIT `main_multi.py` (`run_bot`, :347)** — thread the param through: new kwarg
   `wake_events=None`, forwarded to `TradingEngine(...)`.

4. **EDIT `bot/engine.py` `TradingEngine.__init__` (:707)** — accept
   `wake_events: Optional[dict] = None`.
   - Compute `self._norm_symbol` with the SAME normalization `check_closed_positions` uses
     (`engine.py:231-234`): `symbol.replace('/','').replace(':USDT','').replace('-','').upper()`.
   - Create `self._wake_event = asyncio.Event()` (a PRIVATE event, owned exclusively by this
     engine instance).
   - If `wake_events is not None`: `wake_events.setdefault(self._norm_symbol, []).append(self._wake_event)`.
     Multiple engines on the same coin each register their own event; the WS handler sets all
     of them.
   - If `wake_events is None` (flag-OFF / unit-test): `self._wake_event` is a fresh private
     event that never fires — behaviour is byte-for-byte today.
   - `self._woke_via_ws = False` — reset each loop iteration.

5. **EDIT `bot/engine.py` `_interruptible_sleep` (:2001-2015)** — race three awaitables.
   - Replace the single `wait_for(shutdown)` with:
     ```python
     shutdown_fut = asyncio.ensure_future(self._shutdown_event.wait())
     wake_fut     = asyncio.ensure_future(self._wake_event.wait())
     done, pending = await asyncio.wait(
         {shutdown_fut, wake_fut}, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
     )
     for t in pending:
         t.cancel()
     if self._shutdown_event.is_set():
         return True
     # Capture woke_via_ws from race result (NOT from post-hoc is_set check)
     self._woke_via_ws = wake_fut in done
     self._wake_event.clear()   # clear OUR event only; siblings have their own
     return False
     ```
   - This must be applied at BOTH call sites:
     - `:1999` (`_sleep_until_next_candle`) — the main candle sleep.
     - `:1316` (`_evaluate_and_execute`, `can_trade==False` cooldown sleep) — equally
       important: a bot in a post-loss cooldown or circuit-breaker state is precisely the most
       common moment an SL just filled. A WS wake here must flow into the verify path on the
       NEXT loop iteration just as it does from the candle sleep.
   - After returning from `_interruptible_sleep`, `self._woke_via_ws` is checked at the top of
     `run()` to route into `_verify_close_after_ws`.

6. **NEW `bot/engine.py` `_verify_close_after_ws()` helper** — bounded retry.
   - Called from the `run()` loop top when `self._woke_via_ws is True` (reset to False
     immediately on entry). Applies in the NORMAL branch and the GRACEFUL_STOP/TP_ONLY
     branches; short-circuits immediately if `_tracked_trades` is empty (sibling already
     deduped this coin's close — skip cleanly).
   - Fetch `get_positions()` up to `N=3` times with backoff `(0.4s, 0.8s, 1.6s)` using
     `asyncio.sleep` (never blocking):
     - **EACH call wrapped in `try/except`**: on exception, non-list result, or any other
       sentinel → treat as PRESENT/ambiguous, log at DEBUG, back off, retry. NEVER pass an
       error-derived empty list to `check_closed_positions`. NEVER conclude "closed" from a
       fetch failure.
     - On a real list where the tracked side is **absent**: call `check_closed_positions(...)`
       (passing the fresh snapshot), which does the journal + alert + dedup as normal.
     - On a real list where the tracked side is still **present**: not closed yet, retry.
     - After N ambiguous/exception attempts: drop the trigger, reset `self._woke_via_ws`.
       Log "verify_ambiguous_drop_trigger". The candle backstop catches it next tick. NEVER
       alert on ambiguity.
   - **Cache the fresh snapshot** returned by a successful `get_positions()` call as
     `self._cached_positions_this_tick`. The subsequent loop-top `_monitor_positions` path uses
     this cached value instead of issuing a second `get_positions()`, preventing the back-to-back
     double fetch per woken bot that would otherwise occur.
   - **CCBT_SHARED_MARKETDATA interaction**: the `_should_skip_positions_fetch` gate at
     `:946-954` applies to the NORMAL periodic loop, not to the verify helper. The verify
     helper always fetches because it has an open trade that may have just closed (it only runs
     when `_tracked_trades` is non-empty). Ensure the skip-gate is not applied to the verify
     path.
   - **Cascade coalescing** (open question #3): under a market-wide SL cascade, many bots wake
     simultaneously. Each bot issues up to 3 `get_positions()` calls sequentially (the event
     loop serializes them). Total blast = `(woken_bots × N)` positionRisk fetches within a few
     seconds. Mitigation: keep N small (3), keep backoffs short-total (≤ ~3s), and consider a
     process-level shared 1-2s TTL positions snapshot for the verify path (build only if
     -1003/418 rate-limit pressure is observed in testnet cascade testing — open question #3).
   - **Invariant to enforce in tests**: `_verify_close_after_ws` must NOT itself read/write
     `recently_closed` or `open_trade_ids` directly. It only calls the unchanged synchronous
     `check_closed_positions`, which is the sole arbiter of the exactly-once guarantee.

> `check_closed_positions`, `_infer_close_reason`, `_monitor_positions`,
> `_sleep_until_next_candle` bodies are otherwise **UNCHANGED**. Minimal diff: 1 new module +
> 1 new helper + 4 small edits, all additive / flag-guarded.

---

## 5. Event → wake → verify → retry → alert flow (precise)

Happy path:
1. Binance pushes `ORDER_TRADE_UPDATE` (SL `STOP_MARKET` `reduceOnly` `X=FILLED`) for
   `BTCUSDT`.
2. WS task: `o.X=="FILLED" && o.R==true && o.o in {STOP_MARKET,…}` → for each engine event
   in `wake_events["BTCUSDT"]`: `evt.set()`. (No alert, no DB, no PnL.)
3. Every bot tracking BTC is blocked in `_interruptible_sleep`; the private wake-event future
   wins the race → each engine returns `False`, having captured `woke_via_ws=True` from the
   race result, after clearing ITS OWN event.
4. `run()` loop top: `woke_via_ws=True` → call `_verify_close_after_ws()`.
5. `get_positions()` (wrapped in try/except) returns a real list; the BTC long side is
   **absent** → VERIFIED closed. Call `check_closed_positions(...)` with that snapshot.
6. The existing path fetches the fill (`get_closed_pnl`), applies the `_MAX_EXIT_RATIO=5.0` /
   `_MAX_CANDLE_MOVE_PCT=50.0` guards, `_infer_close_reason` classifies
   tp/stop_loss/trail_stop/breakeven, journals, `send_alert`, `register_close`, cancels orphan
   algo orders. First same-coin bot owns the alert; siblings dedup via `recently_closed`.
   **Latency: seconds.**
7. Cache that snapshot as `_cached_positions_this_tick`; the loop-top `_monitor_positions`
   reuses it, skipping the otherwise redundant second `get_positions()` call.

Retry path (WS leads REST):
- At step 5 the side is still PRESENT (REST lags the WS by a beat) → `_verify_close_after_ws`
  re-fetches up to N=3 with 0.4/0.8/1.6 s async backoff. As soon as the side goes absent →
  run `check_closed_positions` → alert. If still present after N → drop trigger; the candle
  backstop catches it next tick. **No false alert.**

Fetch-error path:
- At step 5 `get_positions()` raises (network flicker, proxy hiccup, -1003): treat as
  PRESENT/ambiguous — do NOT pass an empty snapshot to `check_closed_positions`. Log, back
  off, retry. After N failures → drop trigger, fall to candle backstop. **No false alert from
  fetch failure.**

WS-down fallback:
- listenKey expired / socket dropped / proxy down → `wake_events` never fire → every bot
  detects closes at the candle boundary exactly as today. The WS task `run_forever`-backs-off
  and re-arms in the background. Graceful degradation to status quo.

listenKey-expiry fallback:
- Keepalive PUT returns `-1125` (or a 401-style failure) → WS task POSTs a fresh listenKey on
  the DEDICATED ccxt instance, reconnects to `…/ws/<newKey>`. On every (re)connect, debounced
  reconcile sweep fires (see component 1 above).

Shutdown:
- On `shutdown_event`, the WS task attempts listenKey DELETE with a 2s `wait_for` bound
  (swallow all failures). `CancelledError` in `run_forever` is re-raised promptly so the task
  exits without competing for the SIGTERM→SIGKILL window.

---

## 6. Failure modes + mitigations

| Failure mode | Mitigation |
|---|---|
| **False alert** (alerting off a partial fill / requote / reduce that didn't fully close) | WS handler is **set-only**; the ONLY thing that alerts is real-position-absence in `check_closed_positions`. Trigger only on terminal signals (`X==FILLED && R==true`, or `pa=="0"`). Never parse `rp`/`pa` into an alert. |
| **False alert from fetch error** (get_positions() raises / returns non-list → empty list treated as absent) | **Each `get_positions()` call in `_verify_close_after_ws` is individually wrapped in try/except. Exception or non-list → PRESENT/ambiguous, never absence. A fetch failure is never absence.** Explicit invariant; covered by test 7b. |
| **Missed alert** (close happened while WS down / stream silently stalled) | Candle backstop ALWAYS runs (`check_closed_positions` every candle). STALE_AFTER watchdog + reconnect. **Debounced reconcile sweep on every (re)connect** keys on open DB trades, not just `_tracked_trades`. |
| **Duplicate alert** (at-least-once WS redelivery on reconnect; WS edge + candle edge race; multi-bot-per-coin) | Existing `recently_closed` `(norm_symbol, side)` 60 s TTL registry + the DB `open→closed` one-way transition. Each engine has its own private Event — no shared-clear race. `_verify_close_after_ws` does NO dedup of its own; the single synchronous `check_closed_positions` critical section (no `await` within it) is the sole arbiter. |
| **listenKey expiry** (60-min TTL; -1125; account event invalidation) | Keepalive every ~30 min (not the 60-min edge). On -1125/keepalive failure → POST fresh key via dedicated ccxt + reconnect + debounced reconcile sweep. ONE shared key for the process. |
| **WS reconnect storm** (24h connection cap, network flaps, proxy flicker) | `run_forever` + exponential backoff (1→60 s, reset on success). **Reconcile sweep is debounced** (min interval 30s) so a proxy flap cannot drive a get_positions storm. The 24h cap is orthogonal to the 60-min keepalive — both timers exist independently; run_forever reconnect handles the 24h forced disconnect. |
| **Proxy / egress** (account stream must come from the whitelisted IP) | SOCKS connector (`python-socks` / `aiohttp-socks`) is an explicit runtime dep (in requirements). If `CCBT_SOCKS_PROXY` is set but the connector import fails or is unreachable, **refuse to start the WS task** (log + return) — degrade to candle backstop. Never open a proxy-bypassing raw WS when a proxy is mandated. |
| **Thread-safety on listenKey REST** | listenKey REST runs via `asyncio.to_thread` against a **DEDICATED ccxt instance** (not the shared trading ccxt). The shared sync ccxt is never touched from a worker thread. |
| **Double get_positions on WS wake** (verify + loop-top back-to-back) | Verify caches its fresh snapshot as `_cached_positions_this_tick`; the loop-top `_monitor_positions` path reuses it, skipping the second fetch. No double round-trip per woken bot. |
| **Wake lost at :1316 cooldown sleep** | Both `_interruptible_sleep` call sites (candle sleep and `:1316` `can_trade==False`) use the 3-way race. A wake at `:1316` sets `_woke_via_ws=True`; the NEXT loop iteration runs `_verify_close_after_ws`. Each engine has its own private event — the cooldown bot clearing its own event does not steal the edge from sibling bots. |
| **Testnet vs mainnet** | Testnet/mainnet derived from the SAME shared ccxt object's `use_testnet` flag — NOT from markprice's standalone resolver. WS host and listenKey REST host are guaranteed consistent with the key set. Testnet user-data is historically flaky/delayed → candle backstop is the correctness guarantee on testnet; WS is best-effort only (ship flag-OFF by default until validated — see open question #5). |
| **Overlapping process restart** (SIGTERM→SIGKILL window; two processes briefly share same account) | On shutdown, PREFER letting the listenKey expire (bounded 2s DELETE attempt, swallow failure) rather than blocking on DELETE. A dying process that successfully DELETE-s invalidates the new process's stream (-1125 on its next keepalive). On startup treat an immediate -1125 as "another process owns this key" → POST fresh + reconnect. Document: ONE CCBT process per account. |
| **Reconcile sweep misses restart-orphan closes** | Sweep keys on open DB trades (`journal.get_open_trades()`) not `_tracked_trades`. For a coin with an open DB row but no tracked trade (closed during restart gap), one-shot orphan-reconcile against real position state fires. Positions that opened AND closed entirely during WS downtime remain a candle-backstop concern (separate pre-existing orphan path, out of scope for this feature). |
| **Duration-accuracy quirk** (pre-existing) | Restored positions reset `open_time=time.time()`, so `since_ms` for fill fetch / alert duration can under-report after a restart. The reconcile sweep should widen the lookback for restored trades. Note in code, not a blocker. |

---

## 7. TDD test plan (fakes only — NO real network/keys; conftest telegram block honored)

All tests run under SYSTEM `python3`; `tests/conftest.py` autouse-blanks Telegram creds + no-ops
`send_alert` — **do not bypass it, do not assert a real send.** No live sockets, no live keys.

1. **`test_user_data_parse_order_trade_update`** — feed a canned `ORDER_TRADE_UPDATE` JSON
   (`o.X=FILLED`, `o.R=true`, `o.o=STOP_MARKET`, `o.s=BTCUSDT`) into the frame handler with a
   fake `wake_events` dict (lists of Events) → asserts ONLY the BTC engine events are set,
   others untouched.
2. **`test_user_data_parse_account_update_flat`** — `ACCOUNT_UPDATE` with
   `a.P=[{s:ETHUSDT, pa:"0"}]` → sets all `wake_events["ETHUSDT"]` events only.
3. **`test_user_data_ignores_nonterminal`** — `NEW`, `PARTIALLY_FILLED`, `CANCELED`,
   non-reduceOnly, and `pa!="0"` frames → NO event set.
4. **`test_user_data_unknown_symbol_noop`** — event for a coin not in `wake_events` → no crash,
   no set.
5. **`test_interruptible_sleep_wakes_on_event`** — set the engine's private `_wake_event`
   mid-sleep → returns `False` quickly; `_woke_via_ws` is `True` (from race winner, not
   post-hoc `is_set()`); event is cleared on return. A separate shutdown-set returns `True`.
6. **`test_verify_close_after_ws_confirms_absence`** — fake client whose `get_positions()`
   returns the side PRESENT on calls 1-2 then ABSENT on call 3 → `_verify_close_after_ws`
   drives `check_closed_positions` to reconcile + (one) alert; assert exactly one
   journal/close.
7. **`test_verify_close_after_ws_ambiguous_no_alert`** — fake client returns the side PRESENT
   on ALL N calls → asserts NO alert / NO journal close (the candle backstop owns it later).
   **This is the never-false-alert guard.**
7b. **`test_verify_close_after_ws_exception_no_alert`** — fake client whose `get_positions()`
   RAISES on all N calls → asserts NO alert / NO journal close. **This is the
   fetch-exception-is-never-absence guard.** (Sibling of test 7.)
8. **`test_ws_event_dedup_multi_bot_per_coin`** — two engines sharing one coin, each with its
   own private Event; WS sets both; on a confirmed close only ONE journals+alerts via
   `recently_closed`, the other drops its trade_id. Extend to test interleaved verify retry
   loops (two engines await between retries on the same event loop) → exactly one
   journal+alert. (Reuses the existing per-candle dedup test fixtures.)
9. **`test_listenkey_keepalive_recreate_on_1125`** — fake dedicated ccxt whose
   `fapiPrivatePutListenKey` raises `-1125` → handler calls `fapiPrivatePostListenKey`
   (recreate) on the DEDICATED instance and flags reconnect. (Dedicated ccxt faked, `to_thread`
   real but no network; assert shared ccxt is never called.)
10. **`test_ws_disabled_when_flag_off`** — with `CCBT_USERDATA_WS` unset, `async_main` does NOT
    create the user-data task and engines get a never-firing wake event → behaviour identical to
    today (a smoke assert on task names / count).
11. **`test_ws_optional_import_failure`** — simulate `websockets`/SOCKS import error →
    `run_user_data_stream` logs and returns without raising; bots unaffected.
12. **`test_reconcile_sweep_on_reconnect`** — on (re)connect with two open DB trade rows
    (from `journal.get_open_trades()`, not `_tracked_trades`), the sweep `.set()`s both coins'
    engine events; a coin with an open DB row but no tracked trade triggers orphan-reconcile.
13. **`test_no_double_fetch_on_ws_wake`** — verify helper caches positions; assert `get_positions`
    is called exactly ONCE per woken-bot tick (not twice — once in verify and once at loop top).
14. **`test_woke_via_ws_at_cooldown_sleep`** — bot in `can_trade==False` state blocked at `:1316`
    sleep; WS event fires → `_woke_via_ws=True` on return; NEXT loop iteration runs verify; the
    clearing of this engine's event does NOT affect a sibling engine's own event on the same coin.

Fakes: a `FakeDedicatedExchange` exposing `fapiPrivatePostListenKey/Put/Delete` (the dedicated
instance); a `FakeSharedExchange` for `get_positions()` with a scripted present→absent sequence
(assert it is NEVER called for listenKey REST); a `FakeWS` yielding canned frames from a list
(async iterator). No real `websockets.connect`. Keep the `check_closed_positions` dedup critical
section synchronous in any async-driven test (run via the same function, not a re-implementation).

---

## 8. Phased build plan (PRs)

- **PR1 — wake plumbing (no WS yet).** `wake_events` registry
  (`dict[str, list[asyncio.Event]]`) in `main_multi`, thread through `run_bot` →
  `TradingEngine.__init__` (`_norm_symbol`, `_wake_event` private, `_woke_via_ws`). 3-way
  race in `_interruptible_sleep` at BOTH call sites. `_verify_close_after_ws` helper with
  exception-safe get_positions wrapping and snapshot caching. Tests 5-8, 7b, 13, 14. Flag-OFF
  == today (never-firing event). **No network.** *Lands the engine changes safely behind the
  existing candle behaviour.*

- **PR2 — WS client (REST + parse, proxied), behind `CCBT_USERDATA_WS`.** `bot/user_data_stream.py`:
  dedicated ccxt instance, listenKey create/keepalive/delete via that dedicated instance +
  `to_thread`, `run_forever`+backoff, frame parsing → per-engine `wake_events[coin][*].set()`,
  SOCKS-proxied connect (IPv4, fail-closed on missing connector), STALE watchdog, -1125 recreate,
  debounced reconnect reconcile sweep (keyed on DB open trades). Launch task in `async_main`
  (flag-gated). Derive testnet/mainnet from shared ccxt, not markprice. Startup assertion:
  WS host domain matches ccxt fapiPrivate base URL. Tests 1-4, 9-12. **All fakes.**

- **PR3 — live testnet bring-up + ops doc.** Hands-on testnet connect through the SOCKS proxy;
  verify a real SL/TP fill wakes within seconds and routes through `check_closed_positions`;
  confirm WS-down degrades to candle backstop; confirm no duplicate alerts across WS+candle;
  measure get_positions blast under a simulated multi-coin cascade. Add a short ops section to
  this README + a `deploy/` note. Default the flag ON for testnet only after observation; keep
  flag-OFF for mainnet until SOCKS-WS lib is validated end-to-end and testnet reliability is
  confirmed (open question #5).

---

## 9. Effort

~3-4 focused days. PR1 ~0.5-1 d (small, well-scoped engine edits + unit tests — both
`_interruptible_sleep` call sites + exception-safe verify helper + snapshot cache). PR2 ~1.5-2 d
(new WS module, dedicated ccxt + SOCKS-on-raw-socket is the main novelty + listenKey lifecycle +
fakes). PR3 ~0.5-1 d (live testnet validation; user-data-WS flakiness and proxy egress need
hands-on time). Low blast radius: all changes are additive and flag-guarded; flag-OFF is
byte-for-byte today.

---

## 10. Open questions

1. **SOCKS-on-WS library choice** — `python-socks` + `websockets` proxy transport vs `aiohttp`
   WS with `aiohttp-socks`. Confirm IPv4-forcing and that it works under the existing
   `CCBT_SOCKS_PROXY` droplet. Must be added to requirements before PR2 merges; the
   fail-closed pre-flight in PR2 makes this mandatory, not optional.
2. **STALE_AFTER threshold** for a sparse user-data stream (user-data is quiet for long
   stretches with no open trades) — 60 s start, tune against testnet. Should the watchdog
   relax when zero trades are open? Consider heartbeating with a dummy subscribe to keep the
   connection verifiably alive.
3. **Verify retry N / backoff and cascade coalescing** (N=3, 0.4/0.8/1.6 s) under a
   market-wide SL cascade — measure shared-ccxt positionRisk contention on testnet when many
   coins wake simultaneously. If -1003/418 rate-limit pressure appears, implement a
   process-level short-TTL (1-2s) positions snapshot shared across all verify callers within
   the same tick (a single `get_positions()` per burst, not one per woken bot per retry).
4. **ACCOUNT_UPDATE pa=="0" vs ORDER_TRADE_UPDATE for partial-TP configs** — prefer
   `pa=="0"` as the primary trigger to avoid spurious wakes on partial fills. Validate on
   testnet that Binance reliably emits `ACCOUNT_UPDATE` with `pa=="0"` for every full close
   on USDⓈ-M futures (partial-TP reduces position but does not zero it, so `pa=="0"` is
   correctly silent). Keep `ORDER_TRADE_UPDATE` as secondary / belt-and-suspenders.
5. **Testnet user-data reliability** — verify a real testnet SL/TP actually emits
   `ORDER_TRADE_UPDATE` and/or `ACCOUNT_UPDATE` reliably before defaulting the flag ON;
   testnet user-data streams have historically been delayed or silent. Ship flag-OFF-by-default
   and document. Only enable mainnet after the SOCKS-WS connector is validated end-to-end and
   egress IP is confirmed identical to the ccxt REST egress.
6. **Reconcile lookback for restored positions** — `_restore_positions` resets
   `open_time=time.time()`, so the `get_closed_pnl` `since_ms` window for a fill that
   happened before a restart may miss the fill. The reconcile sweep should widen the
   `since_ms` lookback for DB rows whose `open_time` predates the `_restore_positions` call.
   Confirm the correct widening factor before implementing.

---

## 11. Review v1 → addressed

Summary of how each blocker and major from the adversarial review is handled in this revision.

**BLOCKER 1 — fetch exception treated as absence**
`_verify_close_after_ws` now wraps each `get_positions()` individually in `try/except`. Any
exception, non-list, or sentinel → PRESENT/ambiguous, log, back off, retry. The explicit
invariant "a fetch exception is NEVER absence" is stated in section 2 and section 4 component 6.
Test 7b adds a dedicated guard.

**BLOCKER 2 — SOCKS-WS library not installed; fail-open hazard**
`python-socks` (or `aiohttp-socks`) is now an explicit runtime dependency (add to requirements).
The component 1 pre-flight refuses to open the WS if `CCBT_SOCKS_PROXY` is set but the connector
cannot be imported — degrading to candle backstop instead of connecting without the proxy. The
open question (#1) now states this must be resolved before PR2 merges, not left as optional.

**BLOCKER 3 — listenKey REST on shared trading ccxt (thread-safety + proxy split-brain)**
Component 1 now mandates a **dedicated ccxt instance** (own `requests.Session`, same keys, same
`socksProxy`) used exclusively for listenKey POST/PUT/DELETE. The shared trading ccxt is never
touched from the WS task. Test 9 asserts the shared ccxt is never called for listenKey REST.

**MAJOR 4 — double get_positions on WS wake (verify + loop-top back-to-back)**
`_verify_close_after_ws` caches its fresh positions snapshot as `_cached_positions_this_tick`;
the loop-top `_monitor_positions` reuses it. CCBT_SHARED_MARKETDATA skip-gate is explicitly
scoped to the normal periodic loop, not the verify helper. Test 13 guards one-fetch-per-tick.

**MAJOR 5 — shared Event lost-wakeup / per-edge semantics across siblings**
Replaced shared Event with per-engine private Events. The WS handler holds
`dict[coin, list[Event]]` and sets each engine's own event. Siblings never clear each other's
event. The design now states explicitly that the Event is a "best-effort nudge" and correctness
rests entirely on absence-in-real-state + `recently_closed` + candle backstop.

**MAJOR 6 — reconcile sweep keyed on _tracked_trades misses restart-orphan closes**
Component 1 reconnect reconcile sweep now keys on `journal.get_open_trades()` (open DB rows),
not `_tracked_trades`. For coins with an open DB row but no tracked trade, a one-shot
orphan-reconcile against real position state fires. The over-claim "catches any close that fired
while stream was down" is replaced with a scoped statement. Test 12 updated.

**MAJOR 7 — second _interruptible_sleep call site at :1316 not wired**
Section 4 component 5 now explicitly requires both call sites (`:1999` candle sleep and `:1316`
cooldown sleep) to use the 3-way race. The `_woke_via_ws` flag drives the verify from the NEXT
loop iteration top regardless of which call site woke. Per-engine private Events prevent the
cooldown bot from consuming the edge for sibling bots. Test 14 guards the :1316 path.

**MAJOR 8 — listenKey DELETE blocks launchd SIGTERM→SIGKILL window**
Shutdown listenKey DELETE now wrapped in `asyncio.wait_for(2s)`, swallow all failures. Task
propagates `CancelledError` promptly (same discipline as `markprice.py`). Prefer letting the key
expire over DELETE on a restarting process. Addressed in component 1 shutdown section.

**MAJOR 9 — listenKey 60-min timer vs 24h WS cap conflated**
Section 4 component 1 now has two explicitly labelled independent timers: (1) keepalive PUT timer
for the 60-min listenKey validity; (2) run_forever reconnect for the 24h hard socket cap. Failure
mode row updated.

**MAJOR 10 — DELETE on shutdown invalidates survivor's stream on overlapping restart**
Component 1 shutdown now says: prefer letting the key expire; only DELETE on a confirmed
non-overlapping shutdown; on startup treat an immediate -1125 as "another process owns the key"
→ POST fresh + reconnect. Documented: ONE CCBT process per account.

**MINORS folded in**:
- Dedicated ccxt eliminates the to_thread shared-ccxt thread-safety concern (overlaps BLOCKER 3).
- Reconcile sweep debounce added (RECONCILE_DEBOUNCE_S, e.g. 30s) to prevent reconnect storm
  driving a get_positions storm.
- Explicit invariant: `_verify_close_after_ws` does NO dedup (section 2 invariants + component 6).
- ACCOUNT_UPDATE `pa=="0"` preferred as primary trigger for partial-TP configs (component 1
  frame handler note + open question #4).
- Testnet/mainnet derived from shared ccxt, not markprice resolver (component 1, failure modes
  table).
- Cascade coalescing: open question #3 elevated with a concrete design option (process-level
  short-TTL shared snapshot, build-if-needed).
