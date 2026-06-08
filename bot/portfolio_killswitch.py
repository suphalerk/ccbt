"""Portfolio-level kill-switch monitor (Tier 1, realized-only).

Runs as a single asyncio task in main_multi.py alongside the Telegram handler.
Created ONLY when CCBT_KILL_SWITCH=1 — when the flag is OFF this module is
imported but ``run_portfolio_killswitch`` is never called, leaving behaviour
byte-for-byte identical to today.

Design (v2, Tier 1 only):
- Tier 1 = in-process is_halted + can_open() gate ONLY.
- NEVER writes mode files on a Tier 1 trip (writing GRACEFUL_STOP to a flat
  bot kills its coroutine permanently — engine.py:961 break).
- Uses realized-only metric (get_today_realized_by_close) bucketed by CLOSE
  time, not open time.
- Equity denominator = totalWalletBalance (realized equity incl. locked margin)
  NOT free balance (which is a fraction of equity when positions are open).
- Hysteresis: M-of-N leaky counter (default M=3, N=4) to avoid false trips on
  a single transient breach.
- Bangkok daily rollover: re-snapshots equity, re-checks metric before clearing
  is_halted (prevents re-arm mid-crash).
- Persists halt state to data/portfolio_halt.json (atomic tempfile+rename) for
  restart recovery on the same Bangkok day.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Bangkok timezone (GMT+7) — matches dashboard bucketing
_TZ_BKK = timezone(timedelta(hours=7))

# Loop period (seconds)
_MONITOR_PERIOD_S: float = 15.0

# Hysteresis defaults: M of last N samples must breach threshold before trip
_DEFAULT_M: int = 3
_DEFAULT_N: int = 4

# Equity retry config on startup
_EQUITY_RETRIES: int = 3
_EQUITY_RETRY_BACKOFF_S: float = 1.0


def _bangkok_date(dt: Optional[datetime] = None) -> str:
    """Return today's date string in Bangkok time (YYYY-MM-DD)."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.astimezone(_TZ_BKK).strftime("%Y-%m-%d")


def _read_halt_file(data_dir: str) -> Optional[dict]:
    """Read the portfolio_halt.json lock file.  Returns None on any error."""
    path = os.path.join(data_dir, "portfolio_halt.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("halt_file_read_error", extra={"error": str(exc)})
        return None


def _write_halt_file(data_dir: str, payload: dict) -> None:
    """Atomically write portfolio_halt.json (tempfile + rename)."""
    os.makedirs(data_dir, exist_ok=True)
    target = os.path.join(data_dir, "portfolio_halt.json")
    fd, tmp_path = tempfile.mkstemp(dir=data_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.rename(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _delete_halt_file(data_dir: str) -> None:
    """Remove portfolio_halt.json (ignore missing)."""
    path = os.path.join(data_dir, "portfolio_halt.json")
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("halt_file_delete_error", extra={"error": str(exc)})


async def run_portfolio_killswitch(
    shutdown_event: asyncio.Event,
    portfolio_manager: "PortfolioManager",  # noqa: F821 — forward ref
    get_equity_fn: Callable[[], Optional[float]],
    db_path: str,
    data_dir: str,
) -> None:
    """Async kill-switch monitor loop (Tier 1, realized-only).

    Args:
        shutdown_event: Set by async_main on SIGINT/SIGTERM — monitor exits.
        portfolio_manager: The shared PortfolioManager from main_multi.
        get_equity_fn: Callable returning Optional[float] total wallet equity.
            Called with fresh=True at arm time; the caller wraps SharedMarketData
            so the monitor never holds a direct exchange reference.
        db_path: Absolute path to trades.db.
        data_dir: Data directory for mode files + halt lock file.  Must be
            identical to what bot/engine.py's read_bot_mode uses
            (os.environ.get("BOT_DATA_DIR", "data")).
    """
    from bot.telegram import send_alert
    from dashboard.queries import get_today_realized_by_close

    halt_pct: float = float(os.getenv("CCBT_KILL_HALT_PCT", "6.0"))
    confirm_m: int = int(os.getenv("CCBT_KILL_CONFIRM_M", str(_DEFAULT_M)))
    confirm_n: int = int(os.getenv("CCBT_KILL_CONFIRM_N", str(_DEFAULT_N)))

    logger.info(
        "killswitch_monitor_starting",
        extra={
            "halt_pct": halt_pct,
            "confirm_m": confirm_m,
            "confirm_n": confirm_n,
            "data_dir": data_dir,
            "db_path": db_path,
            "monitor_period_s": _MONITOR_PERIOD_S,
        },
    )

    # ------------------------------------------------------------------
    # Phase 0: Restore halt state from prior run (same Bangkok day)
    # ------------------------------------------------------------------
    today = _bangkok_date()
    existing_halt = _read_halt_file(data_dir)
    if existing_halt and existing_halt.get("bangkok_date") == today:
        # Restore is_halted=True for both Tier 1 and Tier 2 halt files
        async with portfolio_manager._lock:
            portfolio_manager.is_halted = True
            portfolio_manager.halt_tier = existing_halt.get("tier", 1)
            portfolio_manager.halt_reason = existing_halt.get("reason", "restored")
            portfolio_manager._halt_bangkok_date = today
        # Restore persisted start_of_day_equity if available
        saved_equity = existing_halt.get("start_of_day_equity")
        if saved_equity and float(saved_equity) > 0:
            portfolio_manager.start_of_day_equity = float(saved_equity)
        logger.warning(
            "killswitch_restored_from_halt_file",
            extra={
                "tier": portfolio_manager.halt_tier,
                "reason": portfolio_manager.halt_reason,
                "start_of_day_equity": portfolio_manager.start_of_day_equity,
            },
        )
        send_alert(
            f"KILL-SWITCH RESTORED (restart): Tier {portfolio_manager.halt_tier} halt "
            f"active from prior run on {today}. is_halted=True."
        )

    # ------------------------------------------------------------------
    # Phase 1: Arm — get start_of_day_equity
    # ------------------------------------------------------------------
    if portfolio_manager.start_of_day_equity is None or portfolio_manager.start_of_day_equity <= 0:
        equity = await _get_equity_with_retries(get_equity_fn)
        if equity is None or equity <= 0:
            logger.warning(
                "killswitch_disarmed_no_equity",
                extra={"retries": _EQUITY_RETRIES},
            )
            send_alert(
                "KILL-SWITCH DISARMED: Could not obtain equity denominator after "
                f"{_EQUITY_RETRIES} retries. Monitor will retry on next loop."
            )
            # Don't return — keep looping and retry arming below
        else:
            # Mid-day restart: back-calculate Bangkok-midnight balance
            # Use to_thread: SQLite read blocks the event loop if held too long.
            realized_today = await asyncio.to_thread(get_today_realized_by_close, db_path)
            start_equity = equity - realized_today
            if start_equity <= 0:
                # Fallback: use current equity as the denominator
                start_equity = equity
            portfolio_manager.start_of_day_equity = start_equity
            portfolio_manager._halt_bangkok_date = today
            logger.info(
                "killswitch_armed",
                extra={
                    "start_of_day_equity": start_equity,
                    "current_equity": equity,
                    "realized_today": realized_today,
                    "bangkok_date": today,
                },
            )
            # Persist the start-of-day equity so restarts reuse it
            _persist_arm_state(data_dir, start_equity, today, existing_halt)

    # ------------------------------------------------------------------
    # Phase 2: Monitor loop
    # ------------------------------------------------------------------
    # Leaky/sliding-window counter: deque of True/False (True=breach)
    breach_window: deque[bool] = deque(maxlen=confirm_n)
    # Track whether we've already fired an alert for this halt (to avoid
    # alerting on every loop iteration when is_halted stays True)
    _halted_alerted: bool = portfolio_manager.is_halted  # don't re-alert on restore

    while not shutdown_event.is_set():
        # Interruptible wait: exits within milliseconds of SIGTERM instead of
        # blocking asyncio.gather for up to _MONITOR_PERIOD_S.  Mirrors the
        # telegram handler pattern (bot/telegram_commands.py:819-820).
        try:
            await asyncio.wait_for(
                asyncio.shield(shutdown_event.wait()),
                timeout=_MONITOR_PERIOD_S,
            )
            break  # shutdown_event was set — exit immediately
        except asyncio.TimeoutError:
            pass  # normal case: period elapsed, run a tick
        if shutdown_event.is_set():
            break

        try:
            await _monitor_tick(
                portfolio_manager=portfolio_manager,
                get_equity_fn=get_equity_fn,
                db_path=db_path,
                data_dir=data_dir,
                halt_pct=halt_pct,
                confirm_m=confirm_m,
                breach_window=breach_window,
                halted_alerted_ref=[_halted_alerted],
                send_alert_fn=send_alert,
                get_realized_fn=get_today_realized_by_close,
            )
            # Sync local flag from shared state
            _halted_alerted = portfolio_manager.is_halted and _halted_alerted
        except Exception as exc:
            logger.error(
                "killswitch_monitor_tick_error",
                extra={"error": str(exc)},
            )

    logger.info("killswitch_monitor_stopped")


async def _get_equity_with_retries(
    get_equity_fn: Callable[[], Optional[float]],
) -> Optional[float]:
    """Call get_equity_fn up to _EQUITY_RETRIES times with backoff.

    Wraps each call in asyncio.to_thread so the synchronous ccxt
    fetch_balance() HTTP round-trip never blocks the event loop that drives
    all ~59 bot coroutines.
    """
    for attempt in range(_EQUITY_RETRIES):
        try:
            equity = await asyncio.to_thread(get_equity_fn)
        except Exception as exc:
            logger.warning("get_equity_attempt_failed", extra={"attempt": attempt, "error": str(exc)})
            equity = None
        if equity is not None and equity > 0:
            return equity
        if attempt < _EQUITY_RETRIES - 1:
            await asyncio.sleep(_EQUITY_RETRY_BACKOFF_S)
    return None


def _persist_arm_state(
    data_dir: str,
    start_equity: float,
    bangkok_date: str,
    existing: Optional[dict],
) -> None:
    """Persist the start-of-day equity into the halt file (non-halt state)."""
    if existing and existing.get("bangkok_date") == bangkok_date:
        # Update existing record with corrected equity
        payload = {**existing, "start_of_day_equity": start_equity}
    else:
        payload = {
            "tier": 0,
            "reason": "armed",
            "threshold_pct": float(os.getenv("CCBT_KILL_HALT_PCT", "6.0")),
            "metric_snapshot": None,
            "start_of_day_equity": start_equity,
            "bangkok_date": bangkok_date,
            "utc_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "acted_symbols": [],
            "acted_mode": None,
        }
    try:
        _write_halt_file(data_dir, payload)
    except Exception as exc:
        logger.warning("persist_arm_state_failed", extra={"error": str(exc)})


async def _monitor_tick(
    portfolio_manager: "PortfolioManager",  # noqa: F821
    get_equity_fn: Callable[[], Optional[float]],
    db_path: str,
    data_dir: str,
    halt_pct: float,
    confirm_m: int,
    breach_window: "deque[bool]",
    halted_alerted_ref: list,
    send_alert_fn: Callable,
    get_realized_fn: Callable,
) -> None:
    """Single monitor loop iteration."""
    from bot.telegram import send_alert

    today = _bangkok_date()

    # ------------------------------------------------------------------
    # Bangkok day rollover
    # ------------------------------------------------------------------
    if portfolio_manager._halt_bangkok_date and portfolio_manager._halt_bangkok_date != today:
        await _handle_daily_rollover(
            portfolio_manager=portfolio_manager,
            get_equity_fn=get_equity_fn,
            db_path=db_path,
            data_dir=data_dir,
            halt_pct=halt_pct,
            today=today,
            send_alert_fn=send_alert_fn,
            get_realized_fn=get_realized_fn,
            breach_window=breach_window,
        )
        halted_alerted_ref[0] = portfolio_manager.is_halted

    # ------------------------------------------------------------------
    # Arm if equity was unavailable at startup
    # ------------------------------------------------------------------
    if portfolio_manager.start_of_day_equity is None or portfolio_manager.start_of_day_equity <= 0:
        equity = await _get_equity_with_retries(get_equity_fn)
        if equity is None or equity <= 0:
            logger.warning("killswitch_still_no_equity_skipping_trip")
            return
        realized_today = await asyncio.to_thread(get_realized_fn, db_path)
        start_equity = max(equity - realized_today, equity)  # never go below current
        portfolio_manager.start_of_day_equity = start_equity
        portfolio_manager._halt_bangkok_date = today
        _persist_arm_state(data_dir, start_equity, today, _read_halt_file(data_dir))
        logger.info(
            "killswitch_armed_delayed",
            extra={"start_of_day_equity": start_equity, "bangkok_date": today},
        )

    # ------------------------------------------------------------------
    # Already halted — no need to re-evaluate trip; anti-flap
    # ------------------------------------------------------------------
    if portfolio_manager.is_halted:
        return

    # ------------------------------------------------------------------
    # Compute realized loss %
    # ------------------------------------------------------------------
    start_equity = portfolio_manager.start_of_day_equity
    if not start_equity or start_equity <= 0:
        logger.warning("killswitch_no_equity_skip")
        return

    try:
        realized_today = await asyncio.to_thread(get_realized_fn, db_path)
    except Exception as exc:
        logger.error("killswitch_realized_query_failed", extra={"error": str(exc)})
        breach_window.append(False)
        return

    loss_pct = (realized_today / start_equity) * 100.0  # negative = loss

    # ------------------------------------------------------------------
    # Hysteresis: M-of-N leaky counter
    # ------------------------------------------------------------------
    breaching = loss_pct <= -halt_pct
    breach_window.append(breaching)

    breach_count = sum(1 for b in breach_window if b)
    logger.debug(
        "killswitch_tick",
        extra={
            "loss_pct": round(loss_pct, 4),
            "halt_pct": halt_pct,
            "breach_count": breach_count,
            "confirm_m": confirm_m,
            "window_size": len(breach_window),
            "breaching": breaching,
        },
    )

    if breach_count >= confirm_m:
        # Trip Tier 1
        await portfolio_manager.trip(tier=1, reason="realized_loss_pct")
        portfolio_manager._halt_bangkok_date = today

        halt_payload = {
            "tier": 1,
            "reason": "realized_loss_pct",
            "threshold_pct": halt_pct,
            "metric_snapshot": round(loss_pct, 4),
            "start_of_day_equity": start_equity,
            "bangkok_date": today,
            "utc_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "acted_symbols": [],
            "acted_mode": None,
        }
        try:
            _write_halt_file(data_dir, halt_payload)
        except Exception as exc:
            logger.error("halt_file_write_failed", extra={"error": str(exc)})

        if not halted_alerted_ref[0]:
            halted_alerted_ref[0] = True
            msg = (
                f"KILL-SWITCH TRIPPED (Tier 1)\n"
                f"Realized loss: {loss_pct:.2f}% (threshold: -{halt_pct:.1f}%)\n"
                f"Start-of-day equity: {start_equity:.2f} USDT\n"
                f"Action: new entries BLOCKED (is_halted=True). "
                f"Open positions ride existing SL/TP."
            )
            logger.warning(
                "killswitch_tripped",
                extra={
                    "tier": 1,
                    "loss_pct": round(loss_pct, 4),
                    "threshold_pct": halt_pct,
                    "start_of_day_equity": start_equity,
                },
            )
            send_alert_fn(msg)


async def _handle_daily_rollover(
    portfolio_manager: "PortfolioManager",  # noqa: F821
    get_equity_fn: Callable[[], Optional[float]],
    db_path: str,
    data_dir: str,
    halt_pct: float,
    today: str,
    send_alert_fn: Callable,
    get_realized_fn: Callable,
    breach_window: "deque",
) -> None:
    """Handle Bangkok midnight rollover.

    Re-snapshots equity for the new Bangkok day, re-checks the new-day metric
    BEFORE clearing is_halted (prevents re-arm mid-crash).
    """
    logger.info(
        "killswitch_daily_rollover",
        extra={"new_date": today, "was_halted": portfolio_manager.is_halted},
    )

    # Get fresh equity for the new day
    equity = await _get_equity_with_retries(get_equity_fn)
    if equity is None or equity <= 0:
        logger.warning("killswitch_rollover_no_equity")
        # Keep existing start_of_day_equity — do not reset
        portfolio_manager._halt_bangkok_date = today
        return

    # New-day realized loss (should be 0 or near-0 at rollover, but check)
    try:
        new_day_realized = await asyncio.to_thread(get_realized_fn, db_path)
    except Exception:
        new_day_realized = 0.0

    new_day_loss_pct = (new_day_realized / equity) * 100.0 if equity > 0 else 0.0

    # Reset the breach window for the new day
    breach_window.clear()

    # Update equity for new day
    portfolio_manager.start_of_day_equity = equity
    portfolio_manager._halt_bangkok_date = today

    was_halted = portfolio_manager.is_halted

    if was_halted:
        if new_day_loss_pct <= -halt_pct:
            # Still in a drawdown on the new day — stay halted
            logger.warning(
                "killswitch_rollover_still_halted",
                extra={"new_day_loss_pct": round(new_day_loss_pct, 4)},
            )
            send_alert_fn(
                f"KILL-SWITCH ROLLOVER: Still halted on new day {today}. "
                f"New-day loss: {new_day_loss_pct:.2f}% >= threshold -{halt_pct:.1f}%."
            )
            # Update halt file with new equity
            halt_file = _read_halt_file(data_dir)
            if halt_file:
                halt_file["start_of_day_equity"] = equity
                halt_file["bangkok_date"] = today
                try:
                    _write_halt_file(data_dir, halt_file)
                except Exception as exc:
                    logger.warning("halt_file_update_failed", extra={"error": str(exc)})
        else:
            # New day is clean — clear the halt (Tier 1 daily auto-reset)
            await portfolio_manager.clear(tier=1)
            logger.info(
                "killswitch_daily_reset",
                extra={"new_equity": equity, "bangkok_date": today},
            )
            send_alert_fn(
                f"KILL-SWITCH RESET (daily rollover): Tier 1 cleared. "
                f"New Bangkok day: {today}. Start equity: {equity:.2f} USDT."
            )
            # Write armed (non-halt) state with fresh equity
            _persist_arm_state(data_dir, equity, today, None)
    else:
        # Was not halted — just update the equity for the new day
        _persist_arm_state(data_dir, equity, today, None)
        logger.info(
            "killswitch_new_day_equity_updated",
            extra={"equity": equity, "bangkok_date": today},
        )
