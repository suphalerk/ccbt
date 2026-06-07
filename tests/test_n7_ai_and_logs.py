"""N7 — AI Analytics + Live Log Viewer: Python backend tests.

Tests written FIRST (TDD).

Coverage:
  1. /api/logs level-filter parity with get_recent_logs()
  2. /api/logs search-filter parity with get_recent_logs()
  3. /api/logs level+search combined filter
  4. /api/logs with a real log file containing multiple levels
  5. /api/ai/calibration row schema: all required fields present
  6. /api/ai/calibration with calibration data
  7. /api/ai/calibration accuracy/influence field values match Python logic
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# DB helpers (shared with test_api_rest.py, duplicated here to avoid import)
# ---------------------------------------------------------------------------


def _create_db_with_calibration(tmp_path: Path) -> str:
    """Create a DB with ai_calibration rows for AI endpoint tests."""
    db_path = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, status TEXT, close_reason TEXT,
            pnl REAL, pnl_pct REAL, timestamp TEXT,
            entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
            ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
            ai_override INTEGER, duration_seconds INTEGER, strategy TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE ai_calibration (
            id INTEGER PRIMARY KEY,
            timestamp TEXT,
            symbol TEXT,
            side TEXT,
            entry_price REAL,
            stated_confidence REAL,
            position_size_modifier REAL,
            sl_adjustment REAL,
            tp_adjustment REAL,
            market_regime TEXT,
            reasoning TEXT,
            risk_flags TEXT,
            should_skip INTEGER,
            outcome TEXT,
            pnl REAL,
            was_correct INTEGER
        )
        """
    )
    # BTC: 5 decided trades — 4 correct (acc = 0.80 → influence = 1.25)
    calibration_rows = [
        (1, "2026-01-01T00:00:00Z", "BTC/USDT:USDT", "long", 50000.0, 0.75, 1.0, 1.0, 1.0, "trending", "test", None, 0, "win", 10.0, 1),
        (2, "2026-01-02T00:00:00Z", "BTC/USDT:USDT", "long", 51000.0, 0.80, 1.0, 1.0, 1.0, "trending", "test", None, 0, "win",  5.0, 1),
        (3, "2026-01-03T00:00:00Z", "BTC/USDT:USDT", "short",52000.0, 0.65, 1.0, 1.0, 1.0, "ranging",  "test", None, 0, "loss",-3.0, 0),
        (4, "2026-01-04T00:00:00Z", "BTC/USDT:USDT", "long", 50500.0, 0.70, 1.0, 1.0, 1.0, "trending", "test", None, 0, "win",  8.0, 1),
        (5, "2026-01-05T00:00:00Z", "BTC/USDT:USDT", "long", 49000.0, 0.60, 1.0, 1.0, 1.0, "ranging",  "test", None, 0, "win",  6.0, 1),
        # ETH: 2 decided trades — 1 correct (acc = 0.50 → influence = 0.75)
        (6, "2026-01-06T00:00:00Z", "ETH/USDT:USDT", "long", 2500.0, 0.65, 1.0, 1.0, 1.0, "trending", "test", None, 0, "win",  4.0, 1),
        (7, "2026-01-07T00:00:00Z", "ETH/USDT:USDT", "short",2600.0, 0.55, 1.0, 1.0, 1.0, "ranging",  "test", None, 0, "loss",-2.0, 0),
        # Should-skip row — excluded from decided count
        (8, "2026-01-08T00:00:00Z", "BTC/USDT:USDT", "long", 51500.0, 0.40, 0.5, 1.0, 1.0, "volatile", "test", None, 1, None, None, None),
    ]
    conn.executemany(
        """INSERT INTO ai_calibration
           (id,timestamp,symbol,side,entry_price,stated_confidence,position_size_modifier,
            sl_adjustment,tp_adjustment,market_regime,reasoning,risk_flags,should_skip,
            outcome,pnl,was_correct)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        calibration_rows,
    )
    conn.commit()
    conn.close()
    return db_path


def _create_empty_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
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
    return db_path


def _create_log_file(tmp_path: Path, entries: list[dict]) -> Path:
    """Write JSON-line log entries to a temp file."""
    log_path = tmp_path / "trading_bot.log"
    with open(log_path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return log_path


def _get_client(db_path: str):
    import api.deps as deps_module
    from fastapi.testclient import TestClient
    from api.main import app
    app.dependency_overrides[deps_module.get_db_path] = lambda: db_path
    client = TestClient(app, raise_server_exceptions=True)
    return client, app


def _reset_overrides(app) -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. /api/logs level + search filter parity tests
# ---------------------------------------------------------------------------


class TestLogsFilterParity:
    """Parity: /api/logs level + search filters == get_recent_logs() args."""

    def _seed_log(self, tmp_path: Path) -> Path:
        """Return a log file with INFO, WARNING, and ERROR entries."""
        entries = [
            {"timestamp": "2026-01-01T00:01:00Z", "level": "INFO",    "message": "bot started",    "data": {}},
            {"timestamp": "2026-01-01T00:02:00Z", "level": "WARNING", "message": "high spread",    "data": {}},
            {"timestamp": "2026-01-01T00:03:00Z", "level": "ERROR",   "message": "order rejected", "data": {}},
            {"timestamp": "2026-01-01T00:04:00Z", "level": "INFO",    "message": "signal fired",   "data": {}},
            {"timestamp": "2026-01-01T00:05:00Z", "level": "WARNING", "message": "drawdown limit", "data": {}},
        ]
        return _create_log_file(tmp_path, entries)

    # Override the log path in the router by monkeypatching get_recent_logs
    def _client_with_log(self, db_path: str, log_path: str):
        import api.deps as deps_module
        from fastapi.testclient import TestClient
        import api.routers.portfolio as portfolio_router

        # Patch the REPO reference so the router resolves to our temp log
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = Path(log_path).parent  # log file is trading_bot.log inside this dir

        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db_path
        client = TestClient(app, raise_server_exceptions=True)
        return client, app, portfolio_router, original_repo

    def test_level_filter_warning_only(self, tmp_path):
        """level=WARNING → only WARNING+ lines; matches get_recent_logs min_level."""
        db = _create_empty_db(tmp_path)
        log_path = self._seed_log(tmp_path)

        import api.routers.portfolio as portfolio_router
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = tmp_path  # REPO/trading_bot.log will resolve here

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db

        try:
            client = TestClient(app)
            resp = client.get("/api/logs?level=WARNING&limit=200")
            assert resp.status_code == 200
            data = resp.json()
            levels = [ln["level"] for ln in data["lines"]]
            # Should only have WARNING and ERROR (min_level=WARNING)
            for lvl in levels:
                assert lvl in ("WARNING", "ERROR", "CRITICAL"), (
                    f"Expected WARNING+, got {lvl!r}"
                )
            # Should include both WARNING entries and the ERROR entry
            assert len(data["lines"]) == 3, (
                f"Expected 3 lines at WARNING+, got {len(data['lines'])}: {levels}"
            )
        finally:
            app.dependency_overrides.clear()
            portfolio_router.REPO = original_repo

    def test_level_filter_error_only(self, tmp_path):
        """level=ERROR → only ERROR and CRITICAL lines."""
        db = _create_empty_db(tmp_path)
        log_path = self._seed_log(tmp_path)

        import api.routers.portfolio as portfolio_router
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = tmp_path

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db

        try:
            client = TestClient(app)
            resp = client.get("/api/logs?level=ERROR&limit=200")
            assert resp.status_code == 200
            data = resp.json()
            levels = [ln["level"] for ln in data["lines"]]
            for lvl in levels:
                assert lvl in ("ERROR", "CRITICAL"), f"Unexpected level {lvl!r}"
            assert len(data["lines"]) == 1, (
                f"Expected 1 ERROR line, got {len(data['lines'])}"
            )
        finally:
            app.dependency_overrides.clear()
            portfolio_router.REPO = original_repo

    def test_search_filter(self, tmp_path):
        """search=signal → only lines containing 'signal'."""
        db = _create_empty_db(tmp_path)
        log_path = self._seed_log(tmp_path)

        import api.routers.portfolio as portfolio_router
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = tmp_path

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db

        try:
            client = TestClient(app)
            resp = client.get("/api/logs?search=signal&limit=200")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["lines"]) == 1, (
                f"Expected 1 'signal' line, got {len(data['lines'])}"
            )
            assert "signal" in data["lines"][0]["message"].lower()
        finally:
            app.dependency_overrides.clear()
            portfolio_router.REPO = original_repo

    def test_level_and_search_combined(self, tmp_path):
        """level=WARNING + search=drawdown → narrow intersection."""
        db = _create_empty_db(tmp_path)
        log_path = self._seed_log(tmp_path)

        import api.routers.portfolio as portfolio_router
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = tmp_path

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db

        try:
            client = TestClient(app)
            resp = client.get("/api/logs?level=WARNING&search=drawdown&limit=200")
            assert resp.status_code == 200
            data = resp.json()
            # Only 1 WARNING entry contains "drawdown"
            assert len(data["lines"]) == 1, (
                f"Expected 1 line, got {len(data['lines'])}: {data['lines']}"
            )
            assert data["lines"][0]["level"] in ("WARNING", "ERROR", "CRITICAL")
            assert "drawdown" in data["lines"][0]["message"].lower()
        finally:
            app.dependency_overrides.clear()
            portfolio_router.REPO = original_repo

    def test_parity_with_get_recent_logs(self, tmp_path):
        """API /api/logs result count == get_recent_logs() for same level/search."""
        db = _create_empty_db(tmp_path)
        log_path = self._seed_log(tmp_path)

        import api.routers.portfolio as portfolio_router
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = tmp_path

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db

        try:
            from dashboard.queries import get_recent_logs

            client = TestClient(app)

            for level in ("ALL", "INFO", "WARNING", "ERROR"):
                qs_lines = get_recent_logs(
                    max_lines=200, min_level=level, search="", log_path=str(log_path)
                )
                resp = client.get(f"/api/logs?limit=200&level={level}")
                assert resp.status_code == 200
                api_lines = resp.json()["lines"]
                assert len(api_lines) == len(qs_lines), (
                    f"level={level}: api={len(api_lines)}, queries={len(qs_lines)}"
                )
        finally:
            app.dependency_overrides.clear()
            portfolio_router.REPO = original_repo

    def test_no_level_param_returns_all(self, tmp_path):
        """Omitting ?level returns all log lines."""
        db = _create_empty_db(tmp_path)
        self._seed_log(tmp_path)  # 5 entries

        import api.routers.portfolio as portfolio_router
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = tmp_path

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db

        try:
            client = TestClient(app)
            resp = client.get("/api/logs?limit=200")
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_returned"] == 5
        finally:
            app.dependency_overrides.clear()
            portfolio_router.REPO = original_repo

    def test_missing_log_file_returns_empty(self, tmp_path):
        """When trading_bot.log does not exist → 200 + empty lines."""
        db = _create_empty_db(tmp_path)
        # Do NOT create any log file

        import api.routers.portfolio as portfolio_router
        original_repo = portfolio_router.REPO
        portfolio_router.REPO = tmp_path  # no trading_bot.log here

        import api.deps as deps_module
        from fastapi.testclient import TestClient
        from api.main import app
        app.dependency_overrides[deps_module.get_db_path] = lambda: db

        try:
            client = TestClient(app)
            resp = client.get("/api/logs")
            assert resp.status_code == 200
            assert resp.json()["lines"] == []
        finally:
            app.dependency_overrides.clear()
            portfolio_router.REPO = original_repo


# ---------------------------------------------------------------------------
# 2. /api/ai/calibration — schema + accuracy/influence values
# ---------------------------------------------------------------------------


class TestAICalibrationSchema:
    """Row schema and value correctness for /api/ai/calibration."""

    def test_200_with_calibration_data(self, tmp_path):
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/ai/calibration")
            assert resp.status_code == 200
        finally:
            _reset_overrides(app)

    def test_row_schema_all_fields(self, tmp_path):
        """Each row must have: symbol, total_decisions, correct, accuracy_pct, influence_factor."""
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/ai/calibration").json()
            assert len(data["rows"]) > 0
            row = data["rows"][0]
            assert "symbol" in row
            assert "total_decisions" in row
            assert "correct" in row
            assert "accuracy_pct" in row
            assert "influence_factor" in row
        finally:
            _reset_overrides(app)

    def test_btc_accuracy_correct(self, tmp_path):
        """BTC: 4 correct out of 5 decided → accuracy_pct ~80."""
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/ai/calibration").json()
            btc_rows = [r for r in data["rows"] if "BTC" in r["symbol"]]
            assert btc_rows, "Expected a BTC row"
            r = btc_rows[0]
            # 4 correct out of 5 decided = 80% (1 should_skip excluded)
            assert abs(r["accuracy_pct"] - 80.0) < 1.0, (
                f"BTC accuracy_pct={r['accuracy_pct']}, expected ~80.0"
            )
        finally:
            _reset_overrides(app)

    def test_btc_influence_factor(self, tmp_path):
        """BTC accuracy=80% → influence_factor=1.25 (acc >= 0.65)."""
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/ai/calibration").json()
            btc_rows = [r for r in data["rows"] if "BTC" in r["symbol"]]
            assert btc_rows
            assert btc_rows[0]["influence_factor"] == 1.25, (
                f"Expected 1.25 for 80% accuracy, got {btc_rows[0]['influence_factor']}"
            )
        finally:
            _reset_overrides(app)

    def test_eth_influence_factor(self, tmp_path):
        """ETH accuracy=50% → influence_factor=0.75 (0.45 <= acc < 0.55)."""
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/ai/calibration").json()
            eth_rows = [r for r in data["rows"] if "ETH" in r["symbol"]]
            assert eth_rows, "Expected an ETH row"
            assert eth_rows[0]["influence_factor"] == 0.75, (
                f"Expected 0.75 for 50% accuracy, got {eth_rows[0]['influence_factor']}"
            )
        finally:
            _reset_overrides(app)

    def test_total_decisions_includes_skip(self, tmp_path):
        """total_decisions includes should_skip=1 rows (all rows for that symbol)."""
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/ai/calibration").json()
            btc_rows = [r for r in data["rows"] if "BTC" in r["symbol"]]
            assert btc_rows
            # BTC has 6 rows in calibration table (5 decided + 1 should_skip)
            assert btc_rows[0]["total_decisions"] == 6, (
                f"Expected 6 total (incl should_skip), got {btc_rows[0]['total_decisions']}"
            )
        finally:
            _reset_overrides(app)

    def test_empty_db_200_empty_rows(self, tmp_path):
        """No ai_calibration table → 200 with empty rows list."""
        db = _create_empty_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/ai/calibration")
            assert resp.status_code == 200
            assert resp.json()["rows"] == []
        finally:
            _reset_overrides(app)

    def test_accuracy_pct_0_to_100_range(self, tmp_path):
        """accuracy_pct must be in [0, 100] for all rows."""
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            data = client.get("/api/ai/calibration").json()
            for row in data["rows"]:
                assert 0.0 <= row["accuracy_pct"] <= 100.0, (
                    f"accuracy_pct out of range: {row['accuracy_pct']} for {row['symbol']}"
                )
        finally:
            _reset_overrides(app)

    def test_parity_with_get_calibration_data(self, tmp_path):
        """Symbol set in API must match groupby symbols in get_calibration_data()."""
        db = _create_db_with_calibration(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_calibration_data
            qs_df = get_calibration_data(db_path=db)
            qs_symbols = set(qs_df["symbol"].unique()) if not qs_df.empty else set()

            api_data = client.get("/api/ai/calibration").json()
            api_symbols = {r["symbol"] for r in api_data["rows"]}

            assert api_symbols == qs_symbols, (
                f"Symbol mismatch — api={api_symbols}, queries={qs_symbols}"
            )
        finally:
            _reset_overrides(app)
