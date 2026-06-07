#!/usr/bin/env python3
"""Forward-test cohort tracker.

Candidates in research/forward_test_cohort.json are strategies with a 4H edge
but < 15 backtest trades — deployed on testnet to accumulate >= min_trades
attributable live trades, then re-audited.

Attribution: candidates have a FREE symbol (not traded by any main bot), so
their per-symbol trades.db rows are wholly theirs.

Usage:
    python research/forward_test_report.py
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "research" / "forward_test_cohort.json"
DB = REPO / "trades.db"

# classify lives in api/classify.py (canonical); re-exported here for backward compat.
import sys as _sys
if str(REPO) not in _sys.path:
    _sys.path.insert(0, str(REPO))
from api.classify import classify  # noqa: E402 (re-export)


def _live_stats(con: sqlite3.Connection, symbol: str, since: str) -> tuple[int, float, float]:
    """Return (closed_trades, profit_factor, win_rate_pct) for a symbol since a date."""
    cur = con.cursor()
    rows = cur.execute(
        "SELECT pnl FROM trades WHERE symbol=? AND status='closed' "
        "AND COALESCE(close_reason,'')!='orphan_reconcile' AND timestamp >= ?",
        (symbol, since),
    ).fetchall()
    pnls = [r[0] for r in rows if r[0] is not None]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    gross_w = sum(wins)
    gross_l = abs(sum(p for p in pnls if p <= 0))
    pf = (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
    wr = (len(wins) / n * 100) if n else 0.0
    return n, pf, wr


def main() -> int:
    m = json.loads(MANIFEST.read_text())
    min_tr, grad_pf, since = m["min_trades"], m["graduate_pf"], m["added"]
    con = sqlite3.connect(DB)
    testing = [c for c in m["candidates"] if c["status"] == "testing"]
    blocked = [c for c in m["candidates"] if c["status"] == "blocked_slot"]

    print(f"=== Forward-test cohort (since {since}, gate: {min_tr} trades, graduate PF >= {grad_pf}) ===\n")
    print(f"{'coin':12}{'bt_pf':>7}{'live_tr':>9}{'live_pf':>9}{'wr':>6}  recommendation")
    print("-" * 60)
    for c in testing:
        n, pf, wr = _live_stats(con, c["coin"], since)
        rec = classify(n, pf, min_tr, grad_pf)
        pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
        print(f"{c['coin']:12}{c['backtest_pf']:>7.2f}{n:>9}{pf_s:>9}{wr:>5.0f}%  {rec} ({n}/{min_tr})")

    if blocked:
        print(f"\nblocked_slot (symbol busy in main — free the slot to forward-test): "
              + ", ".join(c["coin"] for c in blocked))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
