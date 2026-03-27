#!/usr/bin/env python3
"""Auto checkpoint review — sends Telegram alert at trade milestones.

Run via cron/launchd every hour. Checks trades.db and sends summary
when hitting 100, 200, 500 trade milestones or if PF drops below 1.0.

Usage:
    python3 deploy/macos/checkpoint_review.py
"""

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "trades.db"
STATE_FILE = PROJECT_ROOT / "data" / "checkpoint_state.json"

# Telegram config
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Milestones to alert on
MILESTONES = [50, 100, 200, 500, 1000]

# Alert thresholds
PF_ALERT_THRESHOLD = 1.0  # alert if PF drops below this
WR_ALERT_THRESHOLD = 30.0  # alert if WR drops below this
DD_ALERT_THRESHOLD = 25.0  # alert if estimated DD exceeds this


def send_telegram(message: str):
    """Send message via Telegram bot."""
    if not BOT_TOKEN or not CHAT_ID:
        print(f"Telegram not configured. Message:\n{message}")
        return False
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False


def load_state() -> dict:
    """Load previous checkpoint state."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_milestone": 0, "last_alert_time": "", "last_trade_count": 0}


def save_state(state: dict):
    """Save checkpoint state."""
    STATE_FILE.parent.mkdir(exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_clean_stats() -> dict:
    """Get trade stats excluding suspicious trades."""
    if not DB_PATH.exists():
        return {}

    conn = sqlite3.connect(str(DB_PATH))

    # Count clean closed trades (exclude glitches + graceful_shutdown)
    trades = conn.execute("""
        SELECT symbol, side, entry_price, exit_price, pnl, pnl_pct,
               close_reason, timestamp
        FROM trades
        WHERE status = 'closed' AND pnl IS NOT NULL
    """).fetchall()

    conn.close()

    clean = []
    suspicious = 0
    for t in trades:
        symbol, side, entry, exit_p, pnl, pnl_pct, reason, ts = t
        # Filter suspicious
        if reason == 'graceful_shutdown':
            suspicious += 1
            continue
        if pnl_pct and abs(pnl_pct) > 100:
            suspicious += 1
            continue
        if entry and exit_p and abs(exit_p - entry) / entry > 0.5:
            suspicious += 1
            continue
        clean.append(t)

    if not clean:
        return {"total": 0}

    total = len(clean)
    wins = sum(1 for t in clean if t[4] > 0)
    total_pnl = sum(t[4] for t in clean)
    gross_profit = sum(t[4] for t in clean if t[4] > 0)
    gross_loss = abs(sum(t[4] for t in clean if t[4] <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    wr = wins / total * 100 if total else 0

    # Per-symbol worst performers
    from collections import defaultdict
    sym_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
    for t in clean:
        sym = t[0]
        sym_stats[sym]["trades"] += 1
        sym_stats[sym]["pnl"] += t[4]
        if t[4] > 0:
            sym_stats[sym]["wins"] += 1

    worst = sorted(sym_stats.items(), key=lambda x: x[1]["pnl"])[:5]
    best = sorted(sym_stats.items(), key=lambda x: -x[1]["pnl"])[:5]

    # Open positions
    conn2 = sqlite3.connect(str(DB_PATH))
    open_count = conn2.execute("SELECT COUNT(*) FROM trades WHERE status = 'open'").fetchone()[0]
    conn2.close()

    return {
        "total": total,
        "suspicious": suspicious,
        "wins": wins,
        "wr": round(wr, 1),
        "pf": round(pf, 2),
        "total_pnl": round(total_pnl, 2),
        "open_positions": open_count,
        "best": [(s, d["pnl"], d["trades"]) for s, d in best],
        "worst": [(s, d["pnl"], d["trades"]) for s, d in worst],
    }


def format_report(stats: dict, reason: str) -> str:
    """Format Telegram message."""
    msg = f"🤖 *CCBT Checkpoint: {reason}*\n\n"
    msg += f"📊 *{stats['total']} clean trades*"
    if stats.get('suspicious'):
        msg += f" ({stats['suspicious']} excluded)"
    msg += "\n"
    msg += f"💰 PnL: *${stats['total_pnl']:+.2f}*\n"
    msg += f"📈 WR: {stats['wr']}% | PF: {stats['pf']}\n"
    msg += f"📂 Open: {stats['open_positions']} positions\n\n"

    msg += "🏆 *Top 3:*\n"
    for sym, pnl, tr in stats['best'][:3]:
        msg += f"  {sym}: ${pnl:+.2f} ({tr}tr)\n"

    msg += "\n⚠️ *Bottom 3:*\n"
    for sym, pnl, tr in stats['worst'][:3]:
        msg += f"  {sym}: ${pnl:+.2f} ({tr}tr)\n"

    # Alerts
    alerts = []
    if stats['pf'] < PF_ALERT_THRESHOLD:
        alerts.append(f"🔴 PF {stats['pf']} < {PF_ALERT_THRESHOLD}")
    if stats['wr'] < WR_ALERT_THRESHOLD:
        alerts.append(f"🔴 WR {stats['wr']}% < {WR_ALERT_THRESHOLD}%")

    if alerts:
        msg += "\n🚨 *ALERTS:*\n"
        for a in alerts:
            msg += f"  {a}\n"

    msg += f"\n⏰ {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"
    return msg


def main():
    # Load .env
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

    global BOT_TOKEN, CHAT_ID
    BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

    state = load_state()
    stats = get_clean_stats()

    if not stats or stats["total"] == 0:
        return

    total = stats["total"]
    should_alert = False
    reason = ""

    # Check milestones
    for milestone in MILESTONES:
        if total >= milestone and state["last_milestone"] < milestone:
            should_alert = True
            reason = f"{milestone} trades reached"
            state["last_milestone"] = milestone
            break

    # Check PF alert (every 24h max)
    last_alert = state.get("last_alert_time", "")
    if last_alert:
        try:
            last_dt = datetime.fromisoformat(last_alert)
            hours_since = (datetime.utcnow() - last_dt).total_seconds() / 3600
        except:
            hours_since = 999
    else:
        hours_since = 999

    if stats["pf"] < PF_ALERT_THRESHOLD and hours_since > 24:
        should_alert = True
        reason = f"PF Alert ({stats['pf']} < {PF_ALERT_THRESHOLD})"

    # Daily summary (every 24h)
    if hours_since > 24 and total > state.get("last_trade_count", 0):
        should_alert = True
        reason = "Daily Summary"

    if should_alert:
        msg = format_report(stats, reason)
        success = send_telegram(msg)
        if success:
            state["last_alert_time"] = datetime.utcnow().isoformat()
            state["last_trade_count"] = total
            save_state(state)
            print(f"Alert sent: {reason}")
        else:
            print(f"Failed to send alert")
    else:
        print(f"No alert needed. Trades: {total}, Last milestone: {state['last_milestone']}")


if __name__ == "__main__":
    main()
