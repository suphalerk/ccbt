#!/usr/bin/env python3
"""
Trading Bot Health Monitor & Telegram Alerting System.

Checks:
  - Bot Docker container is running
  - Heartbeat file is recent (< 5 minutes old)
  - Last trade time
  - Daily PnL summary
  - Circuit breaker status
  - Balance changes > 5%

Alerts are sent via Telegram bot API.

Usage:
  python3 deploy/monitoring.py                # Run all checks
  python3 deploy/monitoring.py --daily-report # Force daily PnL summary

Can be run as a cron job or systemd timer every 5 minutes.

Environment variables required:
  TELEGRAM_BOT_TOKEN  - Telegram bot API token
  TELEGRAM_CHAT_ID    - Chat/group ID to send alerts to
  BOT_DATA_DIR        - Path to bot data directory (default: /app/data)
  MONITOR_STATE_FILE  - Path to monitor state file (default: /opt/trading-bot/monitor-state.json)
"""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Telegram settings from environment
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Paths
BOT_DATA_DIR = Path(os.getenv("BOT_DATA_DIR", "/app/data"))
HEARTBEAT_FILE = BOT_DATA_DIR / "heartbeat"  # legacy single-bot heartbeat (fallback)
DB_PATH = BOT_DATA_DIR / "trades.db"

# Monitor state file: tracks previous check results to avoid duplicate alerts.
# IMPORTANT: must be on a persistent volume, NOT /tmp (which is wiped on reboot).
# A reboot would reset dedup state and cause duplicate alerts or missed
# last_notified_trade_id, leading to re-notification of old trades.
# Default to the same persistent directory as trades.db so state survives reboots.
STATE_FILE = Path(os.getenv("MONITOR_STATE_FILE", "/opt/trading-bot/monitor-state.json"))

# Thresholds
HEARTBEAT_MAX_AGE_SECONDS = 300  # 5 minutes
BALANCE_CHANGE_ALERT_PCT = 5.0   # Alert if balance changes by more than 5%
CONTAINER_NAME = "tradingbot"

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("monitor")


# ---------------------------------------------------------------------------
# Telegram Alert Functions
# ---------------------------------------------------------------------------

def send_telegram(message: str, parse_mode: str = "HTML") -> bool:
    """Send a message via Telegram Bot API.

    Args:
        message: The message text to send.
        parse_mode: Telegram parse mode (HTML or Markdown).

    Returns:
        True if message was sent successfully, False otherwise.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping alert")
        logger.info("Would have sent: %s", message)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                logger.info("Telegram alert sent successfully")
                return True
            else:
                logger.error("Telegram API returned status %d", resp.status)
                return False
    except urllib.error.URLError as e:
        logger.error("Failed to send Telegram alert: %s", e)
        return False


# ---------------------------------------------------------------------------
# State Management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load the previous monitor state from disk.

    Returns:
        Dictionary with previous state data.
    """
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    """Save the current monitor state to disk.

    Args:
        state: Dictionary with state data to persist.
    """
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2, default=str))
    except OSError as e:
        logger.error("Failed to save state: %s", e)


# ---------------------------------------------------------------------------
# Health Check Functions
# ---------------------------------------------------------------------------

def check_container_running() -> tuple[bool, str]:
    """Check if the bot Docker container is running.

    Returns:
        Tuple of (is_running, status_message).
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = result.stdout.strip()
        if status == "running":
            return True, "running"
        else:
            return False, f"Container status: {status}"
    except subprocess.TimeoutExpired:
        return False, "Docker inspect timed out"
    except FileNotFoundError:
        # Docker not installed; might be running without Docker
        return True, "docker not available (non-Docker deployment)"
    except Exception as e:
        return False, f"Error checking container: {e}"


def check_heartbeat() -> tuple[bool, float]:
    """Check if the bot heartbeat file is recent (legacy single-bot fallback).

    Returns:
        Tuple of (is_healthy, age_in_seconds).
    """
    if not HEARTBEAT_FILE.exists():
        return False, -1.0

    try:
        timestamp = float(HEARTBEAT_FILE.read_text().strip())
        age = time.time() - timestamp
        return age < HEARTBEAT_MAX_AGE_SECONDS, age
    except (ValueError, OSError) as e:
        logger.error("Error reading heartbeat: %s", e)
        return False, -1.0


def check_per_bot_heartbeats(max_stale_seconds: float = HEARTBEAT_MAX_AGE_SECONDS) -> list[dict]:
    """Scan data/heartbeat_* files and return per-bot health status.

    Reads the bot_health DB table (if available) to detect bots in
    graceful_stop mode, which should not trigger stale alerts.

    Returns:
        List of dicts with keys: symbol, age_seconds, status, mode.
        Only includes bots whose heartbeat file is stale or missing
        (i.e. bots that are potentially stuck or down).
    """
    now = time.time()
    stale_bots: list[dict] = []

    # Read bot_health table to get mode per symbol (skip graceful_stop bots)
    mode_by_symbol: dict[str, str] = {}
    if DB_PATH.exists():
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_health'"
                )
                if cursor.fetchone():
                    rows = conn.execute("SELECT symbol, mode FROM bot_health").fetchall()
                    for symbol, mode in rows:
                        mode_by_symbol[symbol] = mode or "normal"
        except sqlite3.Error as e:
            logger.warning("Could not read bot_health table: %s", e)

    if not BOT_DATA_DIR.exists():
        return stale_bots

    for hb_file in BOT_DATA_DIR.glob("heartbeat_*"):
        symbol = hb_file.name[len("heartbeat_"):]
        bot_mode = mode_by_symbol.get(symbol, "normal")

        # Skip bots in graceful_stop — they intentionally wind down
        if bot_mode == "graceful_stop":
            logger.info("Skipping stale check for %s (graceful_stop mode)", symbol)
            continue

        try:
            ts = float(hb_file.read_text().strip())
            age = now - ts
            if age > max_stale_seconds:
                stale_bots.append({"symbol": symbol, "age_seconds": age, "status": "stale", "mode": bot_mode})
        except (ValueError, OSError) as e:
            logger.warning("Could not read heartbeat for %s: %s", symbol, e)
            stale_bots.append({"symbol": symbol, "age_seconds": -1.0, "status": "missing", "mode": bot_mode})

    return stale_bots


def get_last_trade_time() -> Optional[datetime]:
    """Get the timestamp of the most recent trade from the database.

    Returns:
        Datetime of last trade, or None if no trades found.
    """
    if not DB_PATH.exists():
        return None

    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            result = conn.execute(
                "SELECT timestamp FROM trades WHERE status != 'ai_skipped' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if result:
                return datetime.fromisoformat(result[0])
    except sqlite3.Error as e:
        logger.error("Database error getting last trade: %s", e)

    return None


def get_daily_pnl() -> tuple[float, int, int]:
    """Get today's PnL summary from the database.

    Returns:
        Tuple of (total_pnl, win_count, loss_count).
    """
    if not DB_PATH.exists():
        return 0.0, 0, 0

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            # Total PnL
            pnl_result = conn.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades "
                "WHERE timestamp LIKE ? AND status = 'closed'",
                (f"{today}%",),
            ).fetchone()
            total_pnl = pnl_result[0] if pnl_result else 0.0

            # Win/loss counts
            wins = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE timestamp LIKE ? AND status = 'closed' AND pnl > 0",
                (f"{today}%",),
            ).fetchone()

            losses = conn.execute(
                "SELECT COUNT(*) FROM trades "
                "WHERE timestamp LIKE ? AND status = 'closed' AND pnl <= 0",
                (f"{today}%",),
            ).fetchone()

            return total_pnl, wins[0] if wins else 0, losses[0] if losses else 0
    except sqlite3.Error as e:
        logger.error("Database error getting daily PnL: %s", e)
        return 0.0, 0, 0


def get_recent_trades(hours: int = 1) -> list[dict]:
    """Get trades from the last N hours.

    Args:
        hours: Look-back period in hours.

    Returns:
        List of trade dictionaries.
    """
    if not DB_PATH.exists():
        return []

    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).isoformat()

    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE timestamp > ? AND status != 'ai_skipped' "
                "ORDER BY id DESC",
                (cutoff,),
            ).fetchall()
            return [dict(row) for row in rows]
    except sqlite3.Error as e:
        logger.error("Database error getting recent trades: %s", e)
        return []


def check_circuit_breaker() -> tuple[bool, list[str]]:
    """Check if any circuit breaker conditions are active.

    Looks at the bot's log output for circuit breaker triggers.

    Returns:
        Tuple of (any_triggered, list_of_reasons).
    """
    reasons = []

    try:
        # Check Docker logs for circuit breaker messages in the last 10 minutes
        result = subprocess.run(
            ["docker", "logs", "--since", "10m", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout + result.stderr

        # Parse JSON log entries for circuit breaker indicators
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                msg = entry.get("message", "")
                if "circuit_breaker" in msg or "bot_halted" in msg:
                    reasons.append(msg)
                elif msg == "trade_skipped":
                    data = entry.get("data", {})
                    reason = data.get("reason", "")
                    if "daily loss" in reason.lower() or "consecutive" in reason.lower():
                        reasons.append(reason)
            except json.JSONDecodeError:
                if "circuit_breaker" in line or "bot_halted" in line:
                    reasons.append(line[:200])
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return len(reasons) > 0, reasons


def get_current_balance() -> Optional[float]:
    """Get the current balance from the most recent log entry.

    Returns:
        Current balance, or None if unavailable.
    """
    try:
        result = subprocess.run(
            ["docker", "logs", "--since", "30m", "--tail", "100", CONTAINER_NAME],
            capture_output=True,
            text=True,
            timeout=10,
        )

        balance = None
        for line in result.stdout.strip().split("\n"):
            try:
                entry = json.loads(line)
                data = entry.get("data", {})
                if "balance" in data:
                    balance = float(data["balance"])
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
        return balance
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def check_disk_space(threshold_pct: float = 85.0) -> Optional[str]:
    """Check whether the root filesystem is running low on disk space.

    Args:
        threshold_pct: Alert when disk usage exceeds this percentage (default 85%).

    Returns:
        Alert string if disk usage exceeds the threshold, otherwise None.
    """
    try:
        disk = shutil.disk_usage("/")
        disk_pct = disk.used / disk.total * 100
        if disk_pct > threshold_pct:
            free_gb = disk.free / 1e9
            return (
                f"DISK WARNING: {disk_pct:.1f}% used "
                f"({free_gb:.1f} GB free)"
            )
    except OSError as e:
        logger.error("Failed to check disk space: %s", e)
    return None


# ---------------------------------------------------------------------------
# Main Monitoring Logic
# ---------------------------------------------------------------------------

def run_checks() -> None:
    """Run all health checks and send alerts as needed."""
    state = load_state()
    alerts = []

    # --- Check 1: Container running ---
    container_ok, container_status = check_container_running()

    if not container_ok:
        was_down = state.get("container_down", False)
        if not was_down:
            alerts.append(
                "🚨 <b>BOT DOWN</b>\n"
                f"Container '{CONTAINER_NAME}' is not running.\n"
                f"Status: {container_status}\n"
                f"Time: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        state["container_down"] = True
    else:
        if state.get("container_down", False):
            alerts.append(
                "✅ <b>BOT RECOVERED</b>\n"
                f"Container '{CONTAINER_NAME}' is running again.\n"
                f"Time: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )
        state["container_down"] = False

    # --- Check 2: Heartbeat (per-bot) ---
    # Use per-bot heartbeat files (data/heartbeat_<symbol>) when available.
    # Falls back to the legacy single-file check if no per-bot files exist.
    stale_bots = check_per_bot_heartbeats()
    prev_stale_symbols: set = set(state.get("stale_bot_symbols", []))
    current_stale_symbols: set = {b["symbol"] for b in stale_bots}

    # Alert on newly stale bots only (avoid repeat alerts every 5 min)
    newly_stale = [b for b in stale_bots if b["symbol"] not in prev_stale_symbols]
    for bot in newly_stale:
        age_str = f"{bot['age_seconds']:.0f}s" if bot["age_seconds"] >= 0 else "no heartbeat file"
        alerts.append(
            "⚠️ <b>HEARTBEAT STALE</b>\n"
            f"Bot <b>{bot['symbol']}</b> may be stuck or crashed.\n"
            f"Last heartbeat: {age_str} ago\n"
            f"Mode: {bot['mode']}"
        )

    # Recovery alerts for bots that were stale but are healthy again
    recovered = prev_stale_symbols - current_stale_symbols
    for symbol in recovered:
        alerts.append(
            "✅ <b>BOT HEARTBEAT RECOVERED</b>\n"
            f"Bot <b>{symbol}</b> is sending heartbeats again."
        )

    state["stale_bot_symbols"] = list(current_stale_symbols)

    # Legacy single-file fallback: if no per-bot files found at all, check old file
    if not list(BOT_DATA_DIR.glob("heartbeat_*")) if BOT_DATA_DIR.exists() else True:
        heartbeat_ok, heartbeat_age = check_heartbeat()
        if not heartbeat_ok and container_ok:
            was_stale = state.get("heartbeat_stale", False)
            if not was_stale:
                age_str = f"{heartbeat_age:.0f}s" if heartbeat_age >= 0 else "no heartbeat file"
                alerts.append(
                    "⚠️ <b>HEARTBEAT STALE</b>\n"
                    f"Bot may be stuck or crashed inside container.\n"
                    f"Last heartbeat: {age_str} ago\n"
                    f"Threshold: {HEARTBEAT_MAX_AGE_SECONDS}s"
                )
            state["heartbeat_stale"] = True
        else:
            state["heartbeat_stale"] = False

    # --- Check 3: Recent trades ---
    recent_trades = get_recent_trades(hours=1)
    last_notified_trade_id = state.get("last_notified_trade_id", 0)

    for trade in recent_trades:
        if trade["id"] > last_notified_trade_id and trade["status"] == "closed":
            pnl = trade.get("pnl", 0) or 0
            pnl_emoji = "📈" if pnl > 0 else "📉"
            alerts.append(
                f"{pnl_emoji} <b>TRADE CLOSED</b>\n"
                f"Symbol: {trade['symbol']}\n"
                f"Side: {trade['side']}\n"
                f"Entry: ${trade['entry_price']:.2f}\n"
                f"Exit: ${trade.get('exit_price', 0):.2f}\n"
                f"PnL: ${pnl:.2f} ({trade.get('pnl_pct', 0):.2f}%)\n"
                f"Reason: {trade.get('close_reason', 'unknown')}"
            )
            last_notified_trade_id = max(last_notified_trade_id, trade["id"])

    # Also notify on new trade openings
    for trade in recent_trades:
        if trade["id"] > last_notified_trade_id and trade["status"] == "open":
            alerts.append(
                f"🔔 <b>TRADE OPENED</b>\n"
                f"Symbol: {trade['symbol']}\n"
                f"Side: {trade['side']}\n"
                f"Entry: ${trade['entry_price']:.2f}\n"
                f"SL: ${trade.get('stop_loss', 0):.2f}\n"
                f"TP: ${trade.get('take_profit', 0):.2f}\n"
                f"AI: {trade.get('ai_decision', 'disabled')} "
                f"({trade.get('ai_confidence', 0):.0%})"
            )
            last_notified_trade_id = max(last_notified_trade_id, trade["id"])

    state["last_notified_trade_id"] = last_notified_trade_id

    # --- Check 4: Circuit breaker ---
    cb_triggered, cb_reasons = check_circuit_breaker()
    if cb_triggered and not state.get("circuit_breaker_alerted", False):
        reason_text = "\n".join(f"  - {r}" for r in cb_reasons[:5])
        alerts.append(
            "🛑 <b>CIRCUIT BREAKER TRIGGERED</b>\n"
            f"The bot has paused trading.\n"
            f"Reasons:\n{reason_text}"
        )
        state["circuit_breaker_alerted"] = True
    elif not cb_triggered:
        state["circuit_breaker_alerted"] = False

    # --- Check 5: Balance change > 5% ---
    current_balance = get_current_balance()
    if current_balance is not None:
        prev_balance = state.get("last_known_balance")
        if prev_balance is not None and prev_balance > 0:
            change_pct = ((current_balance - prev_balance) / prev_balance) * 100
            if abs(change_pct) >= BALANCE_CHANGE_ALERT_PCT:
                direction = "increased" if change_pct > 0 else "decreased"
                emoji = "💰" if change_pct > 0 else "💸"
                alerts.append(
                    f"{emoji} <b>BALANCE {direction.upper()}</b>\n"
                    f"Previous: ${prev_balance:.2f}\n"
                    f"Current: ${current_balance:.2f}\n"
                    f"Change: {change_pct:+.2f}%"
                )
        state["last_known_balance"] = current_balance

    # --- Check 6: Disk space ---
    disk_alert = check_disk_space()
    if disk_alert:
        alerts.append(f"⚠️ <b>{disk_alert}</b>\nCheck VPS disk usage before logs or backups fill the volume.")

    # --- Check 7: Daily PnL summary (at 23:55 UTC or when forced) ---
    now_utc = datetime.now(tz=timezone.utc)
    current_hour = now_utc.hour
    last_daily_report_date = state.get("last_daily_report_date", "")
    today_str = now_utc.strftime("%Y-%m-%d")
    force_daily = "--daily-report" in sys.argv

    # Send daily report at 23:55 UTC or when forced
    if (current_hour == 23 and today_str != last_daily_report_date) or force_daily:
        total_pnl, wins, losses = get_daily_pnl()
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0

        balance_str = f"${current_balance:.2f}" if current_balance else "N/A"

        pnl_emoji = "📊"
        if total_pnl > 0:
            pnl_emoji = "✅"
        elif total_pnl < 0:
            pnl_emoji = "❌"

        alerts.append(
            f"{pnl_emoji} <b>DAILY PnL REPORT</b>\n"
            f"Date: {today_str}\n"
            f"{'─' * 24}\n"
            f"Total PnL: ${total_pnl:+.2f}\n"
            f"Trades: {total_trades} (W: {wins} / L: {losses})\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"Balance: {balance_str}"
        )
        state["last_daily_report_date"] = today_str

    # --- Send alerts ---
    for alert in alerts:
        send_telegram(alert)
        # Small delay between messages to avoid Telegram rate limiting
        time.sleep(0.5)

    # Save state for next run
    state["last_check_time"] = datetime.now(tz=timezone.utc).isoformat()
    save_state(state)

    if alerts:
        logger.info("Sent %d alert(s)", len(alerts))
    else:
        logger.info("All checks passed, no alerts needed")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        run_checks()
    except Exception as e:
        logger.error("Monitor failed with error: %s", e, exc_info=True)
        # Try to send an alert about the monitor itself failing
        send_telegram(
            f"⚠️ <b>MONITOR ERROR</b>\n"
            f"The monitoring script itself encountered an error:\n"
            f"<code>{str(e)[:500]}</code>"
        )
        sys.exit(1)
