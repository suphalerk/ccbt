"""Telegram command handler — respond to user commands via bot.

Runs as an async background task alongside trading bots.
Uses getUpdates long polling (no webhook needed, no extra dependencies).

Commands:
    /status    — show running bot count + uptime
    /balance   — show current USDT balance
    /pnl       — show today's PnL + total PnL
    /positions — show currently open positions
    /bots      — list all bots with status (running/stopped)
    /mode      — show current mode for all bots
    /panic     — set ALL bots to PANIC mode
    /stop      — set ALL bots to GRACEFUL_STOP
    /resume    — set ALL bots to NORMAL mode
    /help      — show available commands
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bot.mode import BotMode, write_bot_mode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Heartbeat older than this threshold means the bot is considered stopped.
# 4H bots sleep ~4 hours between candles; allow 6h margin before declaring dead.
_HEARTBEAT_STALE_SECONDS: float = 6 * 3600

# How often to poll getUpdates (seconds between polls)
_POLL_INTERVAL: float = 3.0

# Cache balance for this many seconds to avoid hammering trades.db
_BALANCE_CACHE_TTL: float = 60.0

# ---------------------------------------------------------------------------
# Module-level process start time (for uptime in /status)
# ---------------------------------------------------------------------------

_PROCESS_START: float = time.time()


# ---------------------------------------------------------------------------
# Low-level Telegram API helpers (urllib only — no external deps)
# ---------------------------------------------------------------------------

def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{_BOT_TOKEN}/{method}"


def _send_message(text: str, silent: bool = False) -> bool:
    """Send a Telegram message. Never raises — returns False on any error.

    Args:
        text: HTML-formatted message (max 4096 chars).
        silent: If True sends without notification sound.

    Returns:
        True on success, False otherwise.
    """
    if not _BOT_TOKEN or not _CHAT_ID:
        return False
    try:
        payload = json.dumps({
            "chat_id": _CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": silent,
        }).encode("utf-8")
        req = urllib.request.Request(
            _api_url("sendMessage"),
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.debug("telegram_send_failed", extra={"error": str(exc)})
        return False


def _get_updates(offset: int, timeout: int = 3) -> list[dict]:
    """Fetch pending updates via long polling.

    Args:
        offset: Only return updates with update_id >= offset.
        timeout: Long-poll timeout in seconds.

    Returns:
        List of update dicts; empty list on any error.
    """
    if not _BOT_TOKEN:
        return []
    try:
        url = (
            f"{_api_url('getUpdates')}"
            f"?offset={offset}&timeout={timeout}&allowed_updates=[\"message\"]"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("ok"):
            return body.get("result", [])
    except Exception as exc:
        logger.debug("telegram_get_updates_failed", extra={"error": str(exc)})
    return []


# ---------------------------------------------------------------------------
# Data helpers — read heartbeat files + trades.db
# ---------------------------------------------------------------------------

def _data_dir() -> str:
    return os.getenv("BOT_DATA_DIR", "data")


def _get_all_heartbeats() -> dict[str, float]:
    """Return {symbol: last_ping_timestamp} for every heartbeat file found."""
    d = Path(_data_dir())
    result: dict[str, float] = {}
    if not d.exists():
        return result
    for hb_file in d.glob("heartbeat_*"):
        symbol = hb_file.name.removeprefix("heartbeat_")
        try:
            ts = float(hb_file.read_text().strip())
            result[symbol] = ts
        except (ValueError, OSError):
            result[symbol] = 0.0
    return result


def _classify_bots() -> tuple[list[str], list[str]]:
    """Classify bots into (running, stopped) based on heartbeat age.

    Returns:
        Tuple of (running_symbols, stopped_symbols).
    """
    now = time.time()
    running: list[str] = []
    stopped: list[str] = []
    for symbol, ts in sorted(_get_all_heartbeats().items()):
        age = now - ts
        if age < _HEARTBEAT_STALE_SECONDS:
            running.append(symbol)
        else:
            stopped.append(symbol)
    return running, stopped


def _get_all_mode_symbols() -> list[str]:
    """Return symbols that have a mode file (i.e. ever ran)."""
    d = Path(_data_dir())
    symbols: list[str] = []
    if not d.exists():
        return symbols
    for f in d.glob("mode_*.json"):
        sym = f.name.removeprefix("mode_").removesuffix(".json")
        symbols.append(sym)
    return sorted(symbols)


def _read_all_modes() -> dict[str, str]:
    """Return {symbol: mode_string} for all mode files."""
    d = _data_dir()
    result: dict[str, str] = {}
    for sym in _get_all_mode_symbols():
        path = Path(d) / f"mode_{sym}.json"
        try:
            payload = json.loads(path.read_text())
            result[sym] = payload.get("mode", "normal")
        except Exception:
            result[sym] = "normal"
    return result


def _write_all_modes(mode: BotMode) -> int:
    """Write mode for every known bot (heartbeat + mode files).

    Returns count of bots written.
    """
    # Collect all symbols from both heartbeat files and mode files
    d = _data_dir()
    symbols: set[str] = set()
    for hb in Path(d).glob("heartbeat_*"):
        symbols.add(hb.name.removeprefix("heartbeat_"))
    for mf in Path(d).glob("mode_*.json"):
        symbols.add(mf.name.removeprefix("mode_").removesuffix(".json"))

    count = 0
    for sym in symbols:
        try:
            write_bot_mode(sym, mode, data_dir=d)
            count += 1
        except Exception as exc:
            logger.warning("write_mode_failed", extra={"symbol": sym, "error": str(exc)})
    return count


def _get_db_path() -> str:
    data_dir = os.getenv("BOT_DATA_DIR", "")
    return str(Path(data_dir) / "trades.db") if data_dir else "trades.db"


def _query_db(sql: str, params: tuple = ()) -> list[tuple]:
    """Run a read-only SQLite query. Returns [] on any error."""
    try:
        conn = sqlite3.connect(_get_db_path(), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows
    except Exception as exc:
        logger.debug("db_query_failed", extra={"sql": sql[:80], "error": str(exc)})
        return []


# ---------------------------------------------------------------------------
# Balance cache (avoid querying DB on every /balance call)
# ---------------------------------------------------------------------------

_balance_cache: tuple[float, float] = (0.0, 0.0)  # (value, cached_at)


def _get_cached_balance() -> float:
    """Return cached balance from bot_health table, refreshed every 60s."""
    global _balance_cache
    value, cached_at = _balance_cache
    if time.time() - cached_at < _BALANCE_CACHE_TTL and value > 0:
        return value

    # Sum unrealized_pnl + try to get base balance from health
    # The bot_health table doesn't store equity directly, so we use
    # total_pnl as an indicator when fresh balance isn't available.
    # Best effort: return 0 if data unavailable.
    rows = _query_db(
        "SELECT updated_at FROM bot_health ORDER BY updated_at DESC LIMIT 1"
    )
    if rows:
        # Placeholder — actual balance is held by the exchange client, not in DB.
        # We return 0 to indicate "not available from DB" so the /balance command
        # shows a helpful message instead of a wrong number.
        pass
    _balance_cache = (0.0, time.time())
    return 0.0


# ---------------------------------------------------------------------------
# Command handlers — each returns a formatted HTML string
# ---------------------------------------------------------------------------

def _cmd_status() -> str:
    """Build /status reply."""
    running, stopped = _classify_bots()
    total = len(running) + len(stopped)

    elapsed = int(time.time() - _PROCESS_START)
    hours, rem = divmod(elapsed, 3600)
    mins = rem // 60
    uptime_str = f"{hours}h {mins}m"

    # Last trade time
    rows = _query_db(
        "SELECT timestamp FROM trades WHERE status='closed' ORDER BY id DESC LIMIT 1"
    )
    if rows:
        try:
            last_ts = datetime.fromisoformat(rows[0][0].replace("Z", "+00:00"))
            age_s = int((datetime.now(timezone.utc) - last_ts).total_seconds())
            age_h, age_rem = divmod(age_s, 3600)
            age_m = age_rem // 60
            last_trade = f"{age_h}h {age_m}m ago" if age_h else f"{age_m}m ago"
        except Exception:
            last_trade = "unknown"
    else:
        last_trade = "no trades yet"

    return (
        "<b>CCBT Bot Status</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"Running: {len(running)}/{total} bots\n"
        f"Uptime: {uptime_str}\n"
        f"Last trade: {last_trade}"
    )


def _cmd_balance() -> str:
    """Build /balance reply."""
    # Pull from bot_health — the engine writes unrealized_pnl there.
    # Actual equity is only available on the exchange client; we show a note.
    rows = _query_db(
        "SELECT SUM(unrealized_pnl), MAX(updated_at) FROM bot_health"
    )
    if rows and rows[0][0] is not None:
        unrealized = rows[0][0]
        updated = rows[0][1] or "?"
        return (
            "<b>Balance</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"Unrealized PnL: <b>{unrealized:+.2f} USDT</b>\n"
            f"<i>DB snapshot at {updated}</i>\n"
            "<i>For exact equity, check the dashboard.</i>"
        )
    return (
        "<b>Balance</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "<i>No data yet — bots may not have run a full cycle.</i>"
    )


def _cmd_pnl() -> str:
    """Build /pnl reply."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    today_rows = _query_db(
        "SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM trades "
        "WHERE status='closed' AND timestamp LIKE ?",
        (f"{today}%",),
    )
    total_rows = _query_db(
        "SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM trades WHERE status='closed'"
    )
    wins_today = _query_db(
        "SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl > 0 "
        "AND timestamp LIKE ?",
        (f"{today}%",),
    )

    today_pnl = today_rows[0][0] if today_rows else 0.0
    today_count = today_rows[0][1] if today_rows else 0
    total_pnl = total_rows[0][0] if total_rows else 0.0
    wins = wins_today[0][0] if wins_today else 0
    wr = f"{wins/today_count*100:.0f}%" if today_count > 0 else "N/A"

    sign_today = "+" if today_pnl >= 0 else ""
    sign_total = "+" if total_pnl >= 0 else ""

    return (
        "<b>PnL Summary</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"Today: <b>{sign_today}{today_pnl:.2f} USDT</b>\n"
        f"Total: <b>{sign_total}{total_pnl:.2f} USDT</b>\n"
        f"Trades today: {today_count}\n"
        f"Win rate: {wr}"
    )


def _cmd_positions() -> str:
    """Build /positions reply."""
    rows = _query_db(
        "SELECT symbol, side, size, entry_price, unrealized_pnl "
        "FROM trades WHERE status='open' ORDER BY timestamp"
    )

    # Also check bot_health for positions (more current via engine updates)
    health_rows = _query_db(
        "SELECT symbol, position_side, position_size, position_entry, unrealized_pnl "
        "FROM bot_health WHERE position_side IS NOT NULL AND position_size > 0"
    )

    if not rows and not health_rows:
        return (
            "<b>Open Positions</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "No open positions."
        )

    lines = ["<b>Open Positions</b>", "━━━━━━━━━━━━━━━"]

    # Prefer bot_health data (fresher)
    source = health_rows if health_rows else rows
    for row in source:
        sym, side, size, entry, upnl = row
        side_str = side.upper() if side else "?"
        icon = "🟢" if side_str in ("BUY", "LONG") else "🔴"
        upnl_str = f" ({upnl:+.2f})" if upnl is not None else ""
        lines.append(f"{icon} {sym} {side_str} {size:.6g} @ {entry:.2f}{upnl_str}")

    total = len(source)
    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"Total: {total} position{'s' if total != 1 else ''}")
    return "\n".join(lines)


def _cmd_bots() -> str:
    """Build /bots reply — compact symbol list grouped by status."""
    running, stopped = _classify_bots()
    total = len(running) + len(stopped)

    lines = [f"<b>Bot Overview ({len(running)}/{total})</b>", "━━━━━━━━━━━━━━━"]

    if running:
        running_str = " ".join(s.removesuffix("USDT") for s in running)
        lines.append(f"🟢 {running_str}")
    else:
        lines.append("🟢 (none running)")

    if stopped:
        stopped_str = " ".join(s.removesuffix("USDT") for s in stopped)
        lines.append(f"🔴 {stopped_str} ({len(stopped)} stopped)")

    return "\n".join(lines)


def _cmd_mode() -> str:
    """Build /mode reply — count bots per mode."""
    modes = _read_all_modes()

    if not modes:
        return (
            "<b>Bot Modes</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "<i>No mode files found.</i>"
        )

    counts: dict[str, int] = {}
    for m in modes.values():
        counts[m] = counts.get(m, 0) + 1

    lines = ["<b>Bot Modes</b>", "━━━━━━━━━━━━━━━"]
    for mode_name in ("normal", "graceful_stop", "tp_only", "panic"):
        lines.append(f"{mode_name}: {counts.get(mode_name, 0)} bots")

    return "\n".join(lines)


def _cmd_panic() -> str:
    """Set all bots to PANIC mode."""
    count = _write_all_modes(BotMode.PANIC)
    return f"<b>PANIC mode set for ALL {count} bots!</b>"


def _cmd_stop() -> str:
    """Set all bots to GRACEFUL_STOP."""
    count = _write_all_modes(BotMode.GRACEFUL_STOP)
    return f"<b>Graceful stop set for ALL {count} bots.</b>\nNew entries disabled; positions will close at TP/SL."


def _cmd_resume() -> str:
    """Set all bots back to NORMAL mode."""
    count = _write_all_modes(BotMode.NORMAL)
    return f"<b>NORMAL mode resumed for ALL {count} bots.</b>"


def _cmd_help() -> str:
    """Build /help reply."""
    return (
        "<b>CCBT Commands</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "/status    — running bots + uptime\n"
        "/balance   — unrealized PnL from DB\n"
        "/pnl       — today's and total PnL\n"
        "/positions — currently open positions\n"
        "/bots      — all bots (running/stopped)\n"
        "/mode      — mode breakdown for all bots\n"
        "/panic     — close all positions immediately\n"
        "/stop      — graceful stop (no new entries)\n"
        "/resume    — resume normal trading\n"
        "/help      — this message"
    )


# ---------------------------------------------------------------------------
# Command dispatcher
# ---------------------------------------------------------------------------

_COMMAND_MAP: dict[str, callable] = {
    "/status": _cmd_status,
    "/balance": _cmd_balance,
    "/pnl": _cmd_pnl,
    "/positions": _cmd_positions,
    "/bots": _cmd_bots,
    "/mode": _cmd_mode,
    "/panic": _cmd_panic,
    "/stop": _cmd_stop,
    "/resume": _cmd_resume,
    "/help": _cmd_help,
}


def _dispatch(text: str) -> Optional[str]:
    """Parse message text and return a reply string, or None to ignore.

    Commands may have a bot-username suffix (e.g. /status@MyBot).

    Args:
        text: Raw message text from Telegram.

    Returns:
        Reply string or None.
    """
    if not text:
        return None
    # Strip /command@botname suffix
    base = text.strip().split()[0].split("@")[0].lower()
    handler = _COMMAND_MAP.get(base)
    if handler is None:
        return None
    try:
        return handler()
    except Exception as exc:
        logger.exception("command_handler_error", extra={"cmd": base, "error": str(exc)})
        return f"Error running {base}: {exc}"


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

async def run_telegram_handler(shutdown_event: asyncio.Event) -> None:
    """Run the Telegram command handler as an async background task.

    Polls getUpdates every few seconds and responds to recognised commands
    from the authorised chat ID.  Never raises — all errors are caught and
    logged so the trading loop is not affected.

    Args:
        shutdown_event: When set, this coroutine exits cleanly.
    """
    if not _BOT_TOKEN or not _CHAT_ID:
        logger.info("telegram_handler_disabled — no TELEGRAM_BOT_TOKEN/CHAT_ID configured")
        return

    logger.info("telegram_command_handler_started", extra={"chat_id": _CHAT_ID})
    _send_message("CCBT started — type /help for commands.", silent=True)

    offset: int = 0
    authorised_chat_id = int(_CHAT_ID)

    while not shutdown_event.is_set():
        try:
            # Run blocking urllib call in a thread so we don't block the event loop
            updates: list[dict] = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _get_updates(offset, timeout=3)
            )

            for update in updates:
                # Always advance offset past processed updates
                offset = update.get("update_id", offset) + 1

                msg = update.get("message", {})
                if not msg:
                    continue

                # Security: only respond to the authorised chat
                chat_id = msg.get("chat", {}).get("id")
                if chat_id != authorised_chat_id:
                    logger.warning(
                        "telegram_unauthorised_message",
                        extra={"from_chat_id": chat_id},
                    )
                    continue

                text: str = msg.get("text", "")
                if not text.startswith("/"):
                    continue  # Ignore non-command messages silently

                logger.info("telegram_command_received", extra={"text": text[:80]})
                reply = _dispatch(text)
                if reply:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda r=reply: _send_message(r)
                    )

        except Exception as exc:
            # Never crash the handler — log and keep polling
            logger.error(
                "telegram_handler_error",
                extra={"error": str(exc)},
                exc_info=True,
            )

        # Sleep between polls, but wake immediately on shutdown
        try:
            await asyncio.wait_for(
                asyncio.shield(shutdown_event.wait()),
                timeout=_POLL_INTERVAL,
            )
        except asyncio.TimeoutError:
            pass  # Normal — just means no shutdown yet

    logger.info("telegram_command_handler_stopped")
    _send_message("CCBT shutting down.", silent=True)
