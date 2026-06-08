"""Telegram command handler — respond to user commands via bot.

Runs as an async background task alongside trading bots.
Uses getUpdates long polling (no webhook needed, no extra dependencies).

Commands:
    /status    — show running bot count + uptime
    /balance   — show current USDT balance
    /pnl       — show today's PnL + total PnL
    /positions — show currently open positions
    /upnl      — show live unrealized PnL of all open positions
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
# Module-level exchange holder (injected by main_multi.py for /upnl)
# ---------------------------------------------------------------------------

# NOTE: /upnl must NEVER share the live trading ccxt instance.
# Trading bots call create_order/fetch_positions on that instance from the
# event-loop thread; _cmd_upnl runs in a run_in_executor thread. ccxt sync
# Exchange objects carry mutable per-instance state (rate-limiter, nonce,
# last_http_response) and are not thread-safe. Using the same instance
# would corrupt rate-limiter bookkeeping intermittently.
#
# Instead, set_exchange() receives the *live* instance only to copy its
# credentials and endpoint config into a DEDICATED telemetry instance.
# The telemetry instance is only ever called from the executor thread
# that runs _cmd_upnl, so there is no concurrent access.

_EXCHANGE: Optional[object] = None
# Internal lock guards the _EXCHANGE reference itself (single writer).
_exchange_lock = __import__("threading").Lock()


def set_exchange(exchange: Optional[object]) -> None:
    """Inject the exchange whose credentials /upnl should use.

    A DEDICATED copy of the exchange is created so that /upnl never
    touches the live trading instance that all bot coroutines share.
    This preserves the one-fetch-per-call and never-interfere guarantees.

    Args:
        exchange: A synchronous ccxt exchange instance whose API key /
            endpoint config should be cloned for telemetry.  Pass None
            to clear (graceful degradation — /upnl returns unavailable).
    """
    global _EXCHANGE
    if exchange is None:
        with _exchange_lock:
            _EXCHANGE = None
        return

    # Clone the live exchange into an independent telemetry instance so
    # /upnl never races with bot coroutines that use the shared instance.
    try:
        import ccxt as _ccxt  # noqa: PLC0415

        exchange_id = getattr(exchange, "id", None)
        exchange_cls = getattr(_ccxt, exchange_id) if exchange_id else None
        if exchange_cls is None:
            # Fallback: use the provided instance as-is (test doubles, etc.)
            with _exchange_lock:
                _EXCHANGE = exchange
            return

        # Re-use the same API key + secret as the live instance
        api_key = getattr(exchange, "apiKey", "") or ""
        api_secret = getattr(exchange, "secret", "") or ""
        options = dict(getattr(exchange, "options", {}) or {})

        params: dict = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }
        if options:
            params["options"] = options

        telemetry = exchange_cls(params)

        # Copy URL overrides (testnet endpoints) from the live instance
        live_urls = getattr(exchange, "urls", {}) or {}
        live_api_urls = live_urls.get("api", {})
        if live_api_urls and isinstance(live_api_urls, dict):
            for endpoint, url in live_api_urls.items():
                try:
                    telemetry.urls["api"][endpoint] = url
                except (KeyError, TypeError):
                    pass

        # Copy has-overrides (e.g. fetchCurrencies disabled on testnet)
        live_has = getattr(exchange, "has", {}) or {}
        live_internal_has = getattr(exchange, "_cloneInstance", None)
        # Only copy has entries that differ from defaults (testnet patches)
        _BINANCE_TESTNET_HAS_OVERRIDES = {"fetchCurrencies": False, "fetchMarginMarkets": False}
        for k, v in _BINANCE_TESTNET_HAS_OVERRIDES.items():
            if live_has.get(k) == v:
                telemetry.has[k] = v

        # Copy SOCKS proxy if set
        socks_proxy = getattr(exchange, "socksProxy", None)
        if socks_proxy:
            telemetry.socksProxy = socks_proxy

        # Telemetry instance shares the already-loaded markets so it does
        # not need a second load_markets() network call.
        live_markets = getattr(exchange, "markets", None)
        if live_markets:
            telemetry.markets = live_markets

        with _exchange_lock:
            _EXCHANGE = telemetry

        logger.info("upnl_telemetry_exchange_created", extra={"exchange_id": exchange_id})

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "upnl_telemetry_exchange_clone_failed",
            extra={"error": str(exc)},
        )
        # Last resort: store the provided instance (maintains prior behaviour,
        # but log a clear warning so an operator can see the degraded state).
        logger.warning(
            "upnl_falling_back_to_shared_exchange — thread safety not guaranteed"
        )
        with _exchange_lock:
            _EXCHANGE = exchange


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
    """Find trades.db — check BOT_DATA_DIR first, then common locations."""
    data_dir = os.getenv("BOT_DATA_DIR", "")
    if data_dir:
        p = Path(data_dir) / "trades.db"
        if p.exists():
            return str(p)
    # Fallback: check project root (nohup mode writes here)
    if Path("trades.db").exists():
        return "trades.db"
    # Fallback: check ./data/
    if Path("data/trades.db").exists():
        return "data/trades.db"
    return "trades.db"


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

    # Open positions
    open_rows = _query_db("SELECT COUNT(*) FROM trades WHERE status='open'")
    open_count = open_rows[0][0] if open_rows else 0

    # Last trade time (use Bangkok time in DB)
    rows = _query_db(
        "SELECT timestamp FROM trades WHERE status='closed' ORDER BY id DESC LIMIT 1"
    )
    if rows:
        try:
            # Timestamps are Bangkok time "2026-03-24 05:30:00"
            from datetime import timedelta
            _tz_bkk = timezone(timedelta(hours=7))
            last_ts = datetime.strptime(rows[0][0][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz_bkk)
            age_s = int((datetime.now(_tz_bkk) - last_ts).total_seconds())
            age_h, age_rem = divmod(max(0, age_s), 3600)
            age_m = age_rem // 60
            last_trade = f"{age_h}h {age_m}m ago" if age_h else f"{age_m}m ago"
        except Exception:
            last_trade = rows[0][0][:19]
    else:
        last_trade = "no trades yet"

    return (
        "<b>📊 CCBT Bot Status</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"Running: {len(running)}/{total} bots\n"
        f"Open positions: {open_count}\n"
        f"Uptime: {uptime_str}\n"
        f"Last trade: {last_trade}"
    )


def _cmd_balance() -> str:
    """Build /balance reply — reads from trades DB."""
    # Calculate balance from closed trades PnL
    total_pnl_rows = _query_db(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed'"
    )
    open_count_rows = _query_db(
        "SELECT COUNT(*) FROM trades WHERE status='open'"
    )

    total_pnl = total_pnl_rows[0][0] if total_pnl_rows else 0.0
    open_count = open_count_rows[0][0] if open_count_rows else 0

    return (
        "<b>💰 Balance</b>\n"
        "━━━━━━━━━━━━━━━\n"
        f"Realized PnL: <b>{total_pnl:+,.2f} USDT</b>\n"
        f"Open positions: {open_count}\n"
        "<i>Note: testnet prices may be unrealistic</i>"
    )


def _cmd_pnl() -> str:
    """Build /pnl reply."""
    from datetime import timedelta
    _tz_bkk = timezone(timedelta(hours=7))
    today = datetime.now(_tz_bkk).strftime("%Y-%m-%d")

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
    """Build /positions reply from trades table (status='open')."""
    rows = _query_db(
        "SELECT symbol, side, size, entry_price, stop_loss, take_profit "
        "FROM trades WHERE status='open' ORDER BY timestamp DESC"
    )

    if not rows:
        return (
            "<b>Open Positions</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "No open positions."
        )

    lines = ["<b>📊 Open Positions</b>", "━━━━━━━━━━━━━━━"]

    for sym, side, size, entry, sl, tp in rows:
        side_str = side.upper() if side else "?"
        icon = "🟢" if side_str in ("BUY", "LONG") else "🔴"
        # Short symbol for compact display
        short_sym = sym.replace("/USDT:USDT", "").replace("USDT", "")
        lines.append(f"{icon} <b>{short_sym}</b> {side_str} @ {entry}")

    lines.append("━━━━━━━━━━━━━━━")
    lines.append(f"Total: {len(rows)} position{'s' if len(rows) != 1 else ''}")
    return "\n".join(lines)


def _fmt_price(price: float) -> str:
    """Format a price for human-readable display on Telegram.

    Selects decimal precision based on magnitude so that large prices
    (BTC at 50 000) show as '50,000.00' and small prices (0.0042) show
    full precision.  Never produces scientific notation (unlike :.4g).

    Args:
        price: The price to format (must be a finite float).

    Returns:
        A human-readable string such as '50,255.00', '1,974.50', '0.0042'.
    """
    if price >= 1_000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:.4f}"
    # Sub-dollar (e.g. meme coins): up to 6 significant decimal places
    return f"{price:.6f}"


def _cmd_upnl() -> str:
    """Build /upnl reply — live unrealized PnL from exchange positions.

    Calls fetch_positions() on the shared exchange instance. Filters out
    flat positions (contracts == 0). Returns a graceful message when the
    exchange is not connected or when the call fails.
    """
    if _EXCHANGE is None:
        return (
            "<b>💰 Unrealized PnL</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "<i>Live uPnL unavailable — exchange not connected.</i>"
        )

    try:
        positions = _EXCHANGE.fetch_positions()
    except Exception as exc:
        logger.warning("upnl_fetch_failed", extra={"error": str(exc)})
        return (
            "<b>💰 Unrealized PnL</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"<i>Error fetching uPnL: {exc}</i>"
        )

    # Keep only positions with nonzero size
    open_positions = [
        p for p in positions
        if abs(float(p.get("contracts") or 0)) > 0
    ]

    if not open_positions:
        return (
            "<b>💰 Unrealized PnL</b>\n"
            "━━━━━━━━━━━━━━━\n"
            "No open positions."
        )

    lines = ["<b>💰 Unrealized PnL</b>", "━━━━━━━━━━━━━━━"]
    total_upnl = 0.0

    for p in open_positions:
        sym = p.get("symbol", "?")
        short_sym = sym.replace("/USDT:USDT", "").replace("USDT", "")
        side = (p.get("side") or "?").upper()
        upnl = float(p.get("unrealizedPnl") or 0)
        entry = p.get("entryPrice")
        mark = p.get("markPrice")
        total_upnl += upnl

        icon = "🟢" if upnl >= 0 else "🔴"
        sign = "+" if upnl >= 0 else ""
        line = f"{icon} <b>{short_sym}</b> {side}: {sign}{upnl:.2f} USDT"
        if entry is not None and mark is not None:
            try:
                entry_f = float(entry)
                mark_f = float(mark)
                line += f" ({_fmt_price(entry_f)}→{_fmt_price(mark_f)})"
            except (ValueError, TypeError):
                pass
        lines.append(line)

    lines.append("━━━━━━━━━━━━━━━")
    total_icon = "🟢" if total_upnl >= 0 else "🔴"
    total_sign = "+" if total_upnl >= 0 else ""
    lines.append(f"{total_icon} Total uPnL: <b>{total_sign}{total_upnl:.2f} USDT</b>")
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
        "/upnl      — live unrealized PnL (exchange)\n"
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
    "/upnl": _cmd_upnl,
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

async def run_telegram_handler(
    shutdown_event: asyncio.Event,
    exchange: Optional[object] = None,
) -> None:
    """Run the Telegram command handler as an async background task.

    Polls getUpdates every few seconds and responds to recognised commands
    from the authorised chat ID.  Never raises — all errors are caught and
    logged so the trading loop is not affected.

    Args:
        shutdown_event: When set, this coroutine exits cleanly.
        exchange: Optional shared ccxt exchange instance injected for /upnl.
            When provided, set_exchange() is called so _cmd_upnl can fetch
            live positions.  Defaults to None (graceful degradation).
    """
    set_exchange(exchange)

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
                reply = await asyncio.get_event_loop().run_in_executor(
                    None, _dispatch, text
                )
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
