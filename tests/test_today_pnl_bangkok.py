"""get_today_pnl — 'Today's PnL' card sourced in Bangkok time (GMT+7).

Anti-tautology: the expected sum is computed INDEPENDENTLY in Python (converting
each UTC timestamp to Bangkok and comparing dates) — never by calling the SQL
under test. Includes an anchored trade that is UTC-yesterday but Bangkok-today,
which a UTC-grouped implementation would wrongly exclude (the Bangkok pin).
"""
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dashboard.queries import get_today_pnl

# Bangkok is a fixed UTC+7 (no DST) — exact, and avoids a tzdata dependency.
BKK = timezone(timedelta(hours=7))


def _make_db(tmp_path: Path, trades) -> str:
    """trades: list of (utc_dt, pnl, status, close_reason)."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            size REAL NOT NULL,
            pnl REAL,
            status TEXT NOT NULL DEFAULT 'open',
            close_reason TEXT
        )
        """
    )
    for i, (dt, pnl, status, cr) in enumerate(trades):
        conn.execute(
            "INSERT INTO trades (id, timestamp, symbol, side, entry_price, size, pnl, status, close_reason)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (i + 1, dt.astimezone(timezone.utc).isoformat(), "BTCUSDT", "buy", 100.0, 1.0, pnl, status, cr),
        )
    conn.commit()
    conn.close()
    return db_path


def test_today_pnl_counts_only_bangkok_today_closed_trades(tmp_path):
    now_utc = datetime.now(timezone.utc)
    bkk_now = now_utc.astimezone(BKK)
    bkk_today = bkk_now.date()

    specs = [
        (now_utc - timedelta(hours=2), 10.0, "closed", None),    # today BKK
        (now_utc - timedelta(hours=10), 7.0, "closed", None),    # maybe today/yesterday
        (now_utc - timedelta(hours=20), 3.0, "closed", None),    # maybe today/yesterday
        (now_utc - timedelta(hours=26), -5.0, "closed", None),   # definitely a prior BKK day
        (now_utc - timedelta(hours=50), -9.0, "closed", None),   # definitely a prior BKK day
        (now_utc - timedelta(hours=1), 999.0, "closed", "orphan_reconcile"),  # excluded (orphan)
        (now_utc - timedelta(minutes=30), 888.0, "open", None),  # excluded (not closed)
    ]

    # Bangkok-pin: a trade at 01:00 *today Bangkok* = 18:00 *yesterday UTC*. A
    # UTC-grouped query would put it on yesterday and exclude it; the +7h shift
    # keeps it today. Only add when it's safely in the past (skip 00:00–00:59 BKK).
    anchor = bkk_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if anchor < bkk_now:
        specs.append((anchor, 21.0, "closed", None))

    expected = round(
        sum(
            pnl
            for dt, pnl, st, cr in specs
            if st == "closed"
            and cr != "orphan_reconcile"
            and dt.astimezone(BKK).date() == bkk_today
        ),
        2,
    )

    db_path = _make_db(tmp_path, specs)
    assert round(get_today_pnl(db_path=db_path), 2) == expected


def test_today_pnl_zero_when_no_trades_today(tmp_path):
    now_utc = datetime.now(timezone.utc)
    # only old trades → today's PnL must be a truthful 0.0, not a stale prior day
    db_path = _make_db(tmp_path, [(now_utc - timedelta(days=3), 50.0, "closed", None)])
    assert get_today_pnl(db_path=db_path) == 0.0


def test_today_pnl_empty_db(tmp_path):
    db_path = _make_db(tmp_path, [])
    assert get_today_pnl(db_path=db_path) == 0.0
