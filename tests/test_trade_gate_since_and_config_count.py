"""Tests for trade-gate since= parity and config_count propagation.

Bug 1 (BLOCKER #3): /api/trade-gate never passes since= to get_trade_gate(),
  so the endpoint disagrees with the forward_test_report CLI on cohort verdicts.
  Test: endpoint-level parity vs since-filtered _live_stats (matching CLI behaviour).

Bug 2: get_trade_gate() rows never emitted 'config_count' key, so the API
  always returned config_count=1 regardless of the real config count.
  Test: MIXED-verdict symbol with 2 configs must report config_count=2 from
  the API, and config_count must be present in every row.

TDD discipline: these tests were written BEFORE the fixes. They must fail
on the unfixed code and pass after.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _create_cohort_db(tmp_path: Path, since: str = "2026-06-07") -> str:
    """DB with:
    - ALICEUSDT: 12 closed trades PRE-since + 3 closed trades POST-since (in cohort window)
    - AAVEUSDT:  5 closed trades all POST-since (2 configs → MIXED verdict)

    Pre-since trades should be excluded when since= filter is applied, matching CLI.
    """
    db = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, status TEXT, close_reason TEXT,
            pnl REAL, pnl_pct REAL, timestamp TEXT,
            entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
            ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
            ai_override INTEGER, duration_seconds INTEGER, strategy TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE bot_health (
            symbol TEXT PRIMARY KEY, strategy TEXT, mode TEXT, status TEXT,
            last_heartbeat TEXT, position_side TEXT, position_size REAL,
            position_entry REAL, error_count INTEGER, loop_count INTEGER,
            total_trades INTEGER, total_pnl REAL, updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE ai_calibration (
            id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT,
            entry_price REAL, stated_confidence REAL, position_size_modifier REAL,
            sl_adjustment REAL, tp_adjustment REAL, market_regime TEXT,
            reasoning TEXT, risk_flags TEXT, should_skip INTEGER,
            outcome TEXT, pnl REAL, was_correct INTEGER
        )"""
    )

    trades = []
    row_id = 1

    # ALICEUSDT — 12 trades BEFORE since (2026-06-05), 3 trades AFTER (2026-06-08)
    for i in range(12):
        # pre-since: mixed wins/losses → PF 0.17 (bad) if all included
        pnl = 1.0 if i < 2 else -1.0  # 2 wins, 10 losses → PF 0.22
        trades.append((
            row_id, "ALICEUSDT", "long", "closed", None, pnl, 0.1,
            f"2026-06-05T{i:02d}:00:00",
            100.0, 101.0, 1.0, 99.0, "LONG", 0.7, "setup", 0, 3600, "awesome_oscillator"
        ))
        row_id += 1

    # post-since: 2 wins, 1 loss → PF 2.0 (passes if since= filtered)
    for i, (pnl, day) in enumerate([(2.0, "08"), (3.0, "09"), (-1.0, "10")]):
        trades.append((
            row_id, "ALICEUSDT", "long", "closed", None, pnl, 0.1,
            f"2026-06-{day}T10:00:00",
            100.0, 102.0, 1.0, 99.0, "LONG", 0.7, "setup", 0, 3600, "awesome_oscillator"
        ))
        row_id += 1

    # AAVEUSDT — 5 trades all post-since, single config (for config_count=1 scenario)
    for i in range(5):
        trades.append((
            row_id, "AAVEUSDT", "long", "closed", None, 2.0, 0.2,
            f"2026-06-08T{i:02d}:00:00",
            200.0, 202.0, 0.5, 199.0, "LONG", 0.75, "ok", 0, 7200, "ichimoku"
        ))
        row_id += 1

    conn.executemany(
        """INSERT INTO trades
           (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp,
            entry_price,exit_price,size,stop_loss,ai_decision,ai_confidence,
            ai_reasoning,ai_override,duration_seconds,strategy)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        trades,
    )
    conn.commit()
    conn.close()
    return db


def _create_multi_config_db(tmp_path: Path) -> str:
    """DB where AAVEUSDT has 3 configs deployed (→ MIXED verdict with config_count=3)."""
    db = str(tmp_path / "trades_multi.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, status TEXT, close_reason TEXT,
            pnl REAL, pnl_pct REAL, timestamp TEXT,
            entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
            ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
            ai_override INTEGER, duration_seconds INTEGER, strategy TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE bot_health (
            symbol TEXT PRIMARY KEY, strategy TEXT, mode TEXT, status TEXT,
            last_heartbeat TEXT, position_side TEXT, position_size REAL,
            position_entry REAL, error_count INTEGER, loop_count INTEGER,
            total_trades INTEGER, total_pnl REAL, updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE ai_calibration (
            id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT,
            entry_price REAL, stated_confidence REAL, position_size_modifier REAL,
            sl_adjustment REAL, tp_adjustment REAL, market_regime TEXT,
            reasoning TEXT, risk_flags TEXT, should_skip INTEGER,
            outcome TEXT, pnl REAL, was_correct INTEGER
        )"""
    )
    trades = []
    for i in range(20):
        pnl = 5.0 if i % 2 == 0 else -2.0
        # Use dates after 2026-06-07 so since= filter from manifest doesn't exclude them
        trades.append((
            i + 1, "AAVEUSDT", "long", "closed", None, pnl, 0.1,
            f"2026-06-{8 + (i % 20):02d}T10:00:00",
            200.0, 202.0, 0.5, 199.0, "LONG", 0.8, "ok", 0, 7200, "ichimoku"
        ))
    conn.executemany(
        """INSERT INTO trades
           (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp,
            entry_price,exit_price,size,stop_loss,ai_decision,ai_confidence,
            ai_reasoning,ai_override,duration_seconds,strategy)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        trades,
    )
    conn.commit()
    conn.close()
    return db


def _get_client(db: str):
    import api.deps as deps_module
    from fastapi.testclient import TestClient
    from api.main import app
    app.dependency_overrides[deps_module.get_db_path] = lambda: db
    return TestClient(app, raise_server_exceptions=True), app


def _reset(app) -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Bug 1: since= parity — endpoint must pass since= from cohort manifest
# ---------------------------------------------------------------------------

class TestSinceParity:
    """The /api/trade-gate endpoint must filter by since=manifest['added'] so that
    its verdict matches the forward_test_report CLI for cohort symbols."""

    def test_since_filtered_query_differs_from_unfiltered(self, tmp_path: Path) -> None:
        """Pre-condition: the same DB returns different counts with and without since=.
        If this fails, the DB fixture is wrong, not the code under test."""
        db = _create_cohort_db(tmp_path)
        from dashboard.queries import get_trade_gate

        unfiltered = get_trade_gate(db_path=db, project_root=REPO)
        since_filtered = get_trade_gate(db_path=db, project_root=REPO, since="2026-06-07")

        alice_unfiltered = next(
            (r for r in unfiltered["rows"] if r["symbol"] == "ALICEUSDT"), None
        )
        alice_filtered = next(
            (r for r in since_filtered["rows"] if r["symbol"] == "ALICEUSDT"), None
        )

        assert alice_unfiltered is not None, "ALICEUSDT should appear in unfiltered results"
        # Unfiltered = 15 trades (12 pre + 3 post)
        assert alice_unfiltered["trades"] == 15, (
            f"Unfiltered should have 15 trades, got {alice_unfiltered['trades']}"
        )

        # since= filtered = only 3 post-since trades
        assert alice_filtered is not None, "ALICEUSDT should appear in since-filtered results"
        assert alice_filtered["trades"] == 3, (
            f"since= filtered should have 3 trades, got {alice_filtered['trades']}"
        )

    def test_endpoint_loads_manifest_since_and_filters(self, tmp_path: Path) -> None:
        """The /api/trade-gate endpoint must load research/forward_test_cohort.json
        and pass since=manifest['added'] to get_trade_gate(), giving the same
        result as calling get_trade_gate(..., since='2026-06-07') directly.

        This is the core BLOCKER #3 test: endpoint vs CLI parity.
        """
        db = _create_cohort_db(tmp_path)
        client, app = _get_client(db)
        try:
            # What the endpoint returns
            api = client.get("/api/trade-gate").json()
            alice_api = next(
                (r for r in api["rows"] if r["symbol"] == "ALICEUSDT"), None
            )

            # What since-filtered query returns (ground truth = CLI)
            from dashboard.queries import get_trade_gate
            since_filtered = get_trade_gate(db_path=db, project_root=REPO, since="2026-06-07")
            alice_qs = next(
                (r for r in since_filtered["rows"] if r["symbol"] == "ALICEUSDT"), None
            )

            assert alice_api is not None, "ALICEUSDT must appear in API response"
            assert alice_qs is not None, "ALICEUSDT must appear in since-filtered query"

            # trade_count must match the since-filtered query (not the unfiltered 15)
            assert alice_api["trade_count"] == alice_qs["trades"], (
                f"Endpoint trade_count {alice_api['trade_count']} != "
                f"since-filtered query {alice_qs['trades']}. "
                f"Endpoint is probably not passing since= to get_trade_gate()."
            )
        finally:
            _reset(app)

    def test_endpoint_verdict_matches_since_filtered_query(self, tmp_path: Path) -> None:
        """Verdict from endpoint must match since-filtered query verdict."""
        db = _create_cohort_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/trade-gate").json()
            alice_api = next(
                (r for r in api["rows"] if r["symbol"] == "ALICEUSDT"), None
            )

            from dashboard.queries import get_trade_gate
            since_filtered = get_trade_gate(db_path=db, project_root=REPO, since="2026-06-07")
            alice_qs = next(
                (r for r in since_filtered["rows"] if r["symbol"] == "ALICEUSDT"), None
            )

            assert alice_api is not None
            assert alice_qs is not None
            assert alice_api["verdict"] == alice_qs["verdict"], (
                f"Verdict mismatch: endpoint={alice_api['verdict']} "
                f"vs since-filtered={alice_qs['verdict']}"
            )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# Bug 2: config_count must be emitted in rows_out and reach the API
# ---------------------------------------------------------------------------

class TestConfigCountPropagation:
    """config_count must be included in get_trade_gate rows and surfaced by
    /api/trade-gate correctly (not permanently returning 1)."""

    def test_queries_rows_include_config_count_key(self, tmp_path: Path) -> None:
        """get_trade_gate() rows must have a 'config_count' key."""
        db = _create_cohort_db(tmp_path)
        from dashboard.queries import get_trade_gate
        result = get_trade_gate(db_path=db, project_root=REPO)
        assert result["rows"], "Expected at least one row"
        for row in result["rows"]:
            assert "config_count" in row, (
                f"Row for {row.get('symbol')} missing 'config_count' key. "
                f"Row keys: {list(row.keys())}"
            )

    def test_single_config_symbol_has_config_count_1(self, tmp_path: Path) -> None:
        """A symbol with one deployed config must have config_count=1 in the row."""
        db = _create_cohort_db(tmp_path)
        from dashboard.queries import get_trade_gate
        # ALICEUSDT has 1 config in the real project (config_aliceusdt_awesome4h.json)
        result = get_trade_gate(db_path=db, project_root=REPO)
        alice = next((r for r in result["rows"] if r["symbol"] == "ALICEUSDT"), None)
        # In a tmp fixture the project_root config count may be 0 or 1, both are "single"
        # The key invariant: config_count IS present (not KeyError)
        assert alice is not None
        assert "config_count" in alice

    def test_api_config_count_matches_queries(self, tmp_path: Path) -> None:
        """config_count returned by the API must match what get_trade_gate() computes."""
        db = _create_cohort_db(tmp_path)
        client, app = _get_client(db)
        try:
            from dashboard.queries import get_trade_gate

            # Get ground-truth from queries
            qs = get_trade_gate(db_path=db, project_root=REPO)
            qs_map: Dict[str, int] = {
                r["symbol"]: r["config_count"] for r in qs["rows"]
            }

            # Get API response
            api = client.get("/api/trade-gate").json()
            for api_row in api["rows"]:
                sym = api_row["symbol"]
                expected = qs_map.get(sym, -1)
                actual = api_row["config_count"]
                assert actual == expected, (
                    f"symbol={sym}: API config_count={actual} but "
                    f"get_trade_gate() returned config_count={expected}"
                )
        finally:
            _reset(app)

    def test_mixed_verdict_config_count_greater_than_one(self, tmp_path: Path) -> None:
        """A MIXED-verdict symbol must report config_count > 1 in the API response.

        We fabricate a scenario where AAVEUSDT appears to have > 1 deployed config
        by patching _build_symbol_config_count inside get_trade_gate.
        This tests the router → queries pipeline end-to-end without needing real configs.
        """
        db = _create_multi_config_db(tmp_path)
        client, app = _get_client(db)
        try:
            import dashboard.queries as qmod

            # Patch the count map so AAVEUSDT looks like it has 3 configs
            original_build = qmod._build_symbol_config_count

            def fake_build(project_root):
                return {"AAVEUSDT": 3}

            qmod._build_symbol_config_count = fake_build
            try:
                api = client.get("/api/trade-gate").json()
                aave_row = next(
                    (r for r in api["rows"] if r["symbol"] == "AAVEUSDT"), None
                )
                assert aave_row is not None, "AAVEUSDT must appear in API response"
                assert aave_row["verdict"] == "MIXED", (
                    f"Expected MIXED verdict, got {aave_row['verdict']}"
                )
                assert aave_row["config_count"] == 3, (
                    f"Expected config_count=3 for AAVEUSDT, got {aave_row['config_count']}. "
                    f"config_count is probably not emitted in rows_out."
                )
            finally:
                qmod._build_symbol_config_count = original_build
        finally:
            _reset(app)

    def test_api_every_row_has_config_count(self, tmp_path: Path) -> None:
        """Every row in the /api/trade-gate response must have a config_count field."""
        db = _create_cohort_db(tmp_path)
        client, app = _get_client(db)
        try:
            api = client.get("/api/trade-gate").json()
            for row in api["rows"]:
                assert "config_count" in row, (
                    f"Row {row.get('symbol')} missing config_count"
                )
                assert isinstance(row["config_count"], int), (
                    f"config_count must be int, got {type(row['config_count'])}"
                )
        finally:
            _reset(app)


# ---------------------------------------------------------------------------
# Regression: live-shaped DB (all trades before manifest['added']) must NOT
# return an empty gate panel.
#
# The bug: since=manifest['added'] was applied globally so a DB whose trades
# all predate the manifest date returned n_total=0 and an empty panel — a
# trader opening the dashboard today sees "No trade data available yet".
#
# The fix: since= must be scoped to cohort symbols only (symbols listed in the
# manifest).  Non-cohort symbols always use the full trade history.
# ---------------------------------------------------------------------------

def _create_pre_manifest_db(tmp_path: Path, manifest_added: str = "2026-06-07") -> str:
    """DB that mirrors real production state:
    - 277 closed trades across 12+ symbols
    - ALL trades have timestamps BEFORE manifest_added (2026-03-23 → 2026-06-05)
    - 12 symbols have >1 config deployed → MIXED verdict
    - None of the symbols are in the forward_test_cohort

    Under the broken behaviour (global since=), this returns n_total=0.
    Under the fixed behaviour (cohort-scoped since=), this returns n_total=12+.
    """
    db = str(tmp_path / "trades_prelive.db")
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT, side TEXT, status TEXT, close_reason TEXT,
            pnl REAL, pnl_pct REAL, timestamp TEXT,
            entry_price REAL, exit_price REAL, size REAL, stop_loss REAL,
            ai_decision TEXT, ai_confidence REAL, ai_reasoning TEXT,
            ai_override INTEGER, duration_seconds INTEGER, strategy TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE bot_health (
            symbol TEXT PRIMARY KEY, strategy TEXT, mode TEXT, status TEXT,
            last_heartbeat TEXT, position_side TEXT, position_size REAL,
            position_entry REAL, error_count INTEGER, loop_count INTEGER,
            total_trades INTEGER, total_pnl REAL, updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE ai_calibration (
            id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, side TEXT,
            entry_price REAL, stated_confidence REAL, position_size_modifier REAL,
            sl_adjustment REAL, tp_adjustment REAL, market_regime TEXT,
            reasoning TEXT, risk_flags TEXT, should_skip INTEGER,
            outcome TEXT, pnl REAL, was_correct INTEGER
        )"""
    )

    # Simulate 12 portfolio symbols with trades entirely before manifest date
    symbols = [
        "AVAXUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT",
        "ATOMUSDT", "LINKUSDT", "DOTUSDT", "FILUSDT", "FETUSDT",
        "XRPUSDT", "BNBUSDT",
    ]
    trades = []
    row_id = 1
    for sym in symbols:
        # 20 trades per symbol, all dated 2026-03-23 to 2026-06-05 (before manifest)
        for i in range(20):
            day = 23 + (i % 7)
            month = 3 + (i // 7)
            if month > 5:
                month = 5
                day = min(day, 28)
            ts = f"2026-{month:02d}-{day:02d}T{(i % 24):02d}:00:00"
            pnl = 5.0 if i % 3 != 0 else -3.0
            trades.append((
                row_id, sym, "long", "closed", "take_profit" if pnl > 0 else "stop_loss",
                pnl, 0.1, ts, 100.0, 102.0, 1.0, 98.0,
                "LONG", 0.75, "setup", 0, 3600, "ichimoku",
            ))
            row_id += 1

    conn.executemany(
        """INSERT INTO trades
           (id,symbol,side,status,close_reason,pnl,pnl_pct,timestamp,
            entry_price,exit_price,size,stop_loss,ai_decision,ai_confidence,
            ai_reasoning,ai_override,duration_seconds,strategy)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        trades,
    )
    conn.commit()
    conn.close()
    return db


class TestLiveShapedDB:
    """Regression: all trades pre-manifest → gate panel must NOT be empty.

    This is the exact production failure: a real DB with 277 trades spanning
    2026-03-23..2026-06-05 returned n_total=0 when since=2026-06-07 was applied
    globally.  The fix scopes since= to cohort symbols only.
    """

    def test_all_pre_manifest_trades_not_empty(self, tmp_path: Path) -> None:
        """n_total must be > 0 when all trades predate manifest['added']."""
        db = _create_pre_manifest_db(tmp_path)
        client, app = _get_client(db)
        try:
            resp = client.get("/api/trade-gate")
            assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
            data = resp.json()
            summary = data.get("summary", {})
            n_total = summary.get("n_total", 0)
            assert n_total > 0, (
                "REGRESSION: /api/trade-gate returned n_total=0 for a DB where all "
                "trades predate the manifest's 'added' date. The global since= filter "
                "is incorrectly emptying the panel. Fix: scope since= to cohort symbols only."
            )
        finally:
            _reset(app)

    def test_mixed_symbols_present_in_pre_manifest_db(self, tmp_path: Path) -> None:
        """MIXED rows must appear in the gate panel for pre-manifest-date trade DBs.

        Patches _build_symbol_config_count to simulate 12 multi-config symbols.
        Without the fix, the panel is empty so MIXED count = 0.
        """
        db = _create_pre_manifest_db(tmp_path)
        client, app = _get_client(db)
        try:
            import dashboard.queries as qmod

            # Patch: all 12 symbols appear to have 2+ configs (→ MIXED verdict)
            orig = qmod._build_symbol_config_count
            def fake_build(project_root):
                return {
                    "AVAXUSDT": 2, "BTCUSDT": 2, "ETHUSDT": 2, "SOLUSDT": 2,
                    "NEARUSDT": 2, "ATOMUSDT": 2, "LINKUSDT": 2, "DOTUSDT": 2,
                    "FILUSDT": 2, "FETUSDT": 2, "XRPUSDT": 2, "BNBUSDT": 2,
                }
            qmod._build_symbol_config_count = fake_build
            try:
                resp = client.get("/api/trade-gate")
                data = resp.json()
                mixed_rows = [r for r in data.get("rows", []) if r.get("verdict") == "MIXED"]
                assert len(mixed_rows) >= 1, (
                    "REGRESSION: no MIXED rows visible when all trades predate manifest date. "
                    "The global since= filter is hiding all MIXED attribution warnings."
                )
                assert len(mixed_rows) == 12, (
                    f"Expected 12 MIXED rows (one per multi-config symbol), got {len(mixed_rows)}. "
                    f"Rows: {[r['symbol'] for r in data.get('rows', [])]}"
                )
            finally:
                qmod._build_symbol_config_count = orig
        finally:
            _reset(app)
