"""Batch 3 — #6 Underwater drawdown pin test.

Verifies that get_equity_curve() computes the `underwater` series correctly
using the running-max formula:
  underwater[i] = cumulative_pnl[i] − running_max(cumulative_pnl[0..i])
                (always <= 0; 0 at new equity highs)

Hand-computed reference:
  cumulative_pnl = [10, 5, 20, 12]
  running_max    = [10, 10, 20, 20]
  underwater     = [0, -5, 0, -8]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Helper: build a minimal in-memory DB with known PnL rows
# ---------------------------------------------------------------------------

def _make_db_with_pnls(tmp_path: Path, pnls: list[float]) -> str:
    """Create a trades.db with the given pnl series (all 'closed', no orphans)."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            status TEXT,
            close_reason TEXT,
            pnl REAL,
            pnl_pct REAL,
            timestamp TEXT,
            entry_price REAL,
            exit_price REAL,
            size REAL,
            stop_loss REAL,
            ai_decision TEXT,
            ai_confidence REAL,
            ai_reasoning TEXT,
            ai_override INTEGER,
            duration_seconds INTEGER,
            strategy TEXT
        )
        """
    )
    for i, pnl in enumerate(pnls):
        conn.execute(
            """
            INSERT INTO trades
              (symbol, side, status, close_reason, pnl, timestamp,
               entry_price, exit_price, size)
            VALUES (?, 'long', 'closed', 'tp', ?, ?, 100.0, 101.0, 1.0)
            """,
            ("BTCUSDT", pnl, f"2026-01-{i+1:02d}T00:00:00"),
        )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUnderwaterPin:
    """Pin test for the underwater drawdown series (hand-computed reference)."""

    def test_underwater_values_pin(self, tmp_path):
        """Hand-computed: pnl=[10,5,20,12] → underwater=[0,-5,0,-8]."""
        from dashboard.queries import get_equity_curve

        db = _make_db_with_pnls(tmp_path, [10, 5, 20, 12])
        df = get_equity_curve(db_path=db)

        assert "underwater" in df.columns, "underwater column must be present"
        assert len(df) == 4, "should have 4 rows"

        cum_pnl = df["cumulative_pnl"].tolist()
        assert cum_pnl == pytest.approx([10, 15, 35, 47], abs=1e-6), \
            f"cumulative_pnl mismatch: {cum_pnl}"

        # Hand-computed underwater (running_max − cumulative_pnl):
        # running_max = [10, 15, 35, 47] — no drawdown in a monotonically rising series
        # Actually: individual pnls [10,5,20,12] → cumsum [10,15,35,47] → always rising
        # Let me re-check: pnl series gives cumsum that's always going up here.
        # For a proper drawdown test we use pnls that produce a drawdown.
        uw = df["underwater"].tolist()
        # With cum_pnl always rising: running_max == cum_pnl → underwater = 0 everywhere
        assert all(abs(u) < 1e-6 for u in uw), f"no drawdown expected: {uw}"

    def test_underwater_with_drawdown(self, tmp_path):
        """pnl=[10,-5,15,-8] → cumsum=[10,5,20,12] → underwater=[0,-5,0,-8]."""
        from dashboard.queries import get_equity_curve

        db = _make_db_with_pnls(tmp_path, [10, -5, 15, -8])
        df = get_equity_curve(db_path=db)

        assert "underwater" in df.columns

        cum_pnl = df["cumulative_pnl"].tolist()
        # Expected cumsum: [10, 5, 20, 12]
        assert cum_pnl == pytest.approx([10, 5, 20, 12], abs=1e-6), \
            f"cumulative_pnl mismatch: {cum_pnl}"

        uw = df["underwater"].tolist()
        # running_max: [10, 10, 20, 20]
        # underwater = cum_pnl − running_max = [0, -5, 0, -8]
        assert uw == pytest.approx([0, -5, 0, -8], abs=1e-6), \
            f"underwater mismatch: {uw}"

    def test_underwater_always_lte_zero(self, tmp_path):
        """Invariant: underwater must always be <= 0."""
        from dashboard.queries import get_equity_curve

        db = _make_db_with_pnls(tmp_path, [5, -10, 20, -3, 8, -15, 30])
        df = get_equity_curve(db_path=db)

        assert "underwater" in df.columns
        assert all(u <= 1e-9 for u in df["underwater"].tolist()), \
            f"underwater must be <= 0: {df['underwater'].tolist()}"

    def test_underwater_zero_at_new_highs(self, tmp_path):
        """Invariant: underwater must be 0 at points that are new equity highs."""
        from dashboard.queries import get_equity_curve

        # pnl=[1,2,3,-1] → cumsum=[1,3,6,5] → uw=[0,0,0,-1]
        db = _make_db_with_pnls(tmp_path, [1, 2, 3, -1])
        df = get_equity_curve(db_path=db)

        uw = df["underwater"].tolist()
        # First three are new highs → 0; last is -1 from peak of 6
        assert uw == pytest.approx([0, 0, 0, -1], abs=1e-6), \
            f"underwater at new high should be 0: {uw}"

    def test_underwater_empty_db(self, tmp_path):
        """Empty DB → DataFrame with underwater column, 0 rows."""
        from dashboard.queries import get_equity_curve

        db_path = str(tmp_path / "trades.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY, symbol TEXT, side TEXT, status TEXT,
                close_reason TEXT, pnl REAL, pnl_pct REAL, timestamp TEXT,
                entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
                ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
                ai_override INTEGER, duration_seconds INTEGER, strategy TEXT
            )
            """
        )
        conn.commit()
        conn.close()

        df = get_equity_curve(db_path=db_path)
        assert "underwater" in df.columns
        assert len(df) == 0


class TestEquityRouterUnderwaterField:
    """Verify the /api/equity endpoint exposes the underwater field."""

    def test_equity_point_has_underwater_field(self, tmp_path):
        """API equity points must include the underwater field (not just EquityPoint pin)."""
        import json as _json
        from fastapi.testclient import TestClient
        from api.main import app
        from api.deps import get_db_path

        db = _make_db_with_pnls(tmp_path, [10, -5, 15, -8])

        def override_db():
            return db

        app.dependency_overrides[get_db_path] = override_db
        client = TestClient(app)
        try:
            resp = client.get("/api/equity")
            assert resp.status_code == 200
            data = resp.json()
            assert "points" in data
            assert len(data["points"]) == 4
            for pt in data["points"]:
                assert "underwater" in pt, f"underwater missing from point: {pt}"
            # Spot-check the underwater values match the hand-computed series
            uw_values = [pt["underwater"] for pt in data["points"]]
            assert uw_values == pytest.approx([0, -5, 0, -8], abs=0.01), \
                f"underwater values mismatch: {uw_values}"
        finally:
            app.dependency_overrides.clear()
