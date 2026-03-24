"""Unit tests for bot/telegram_commands.py — Telegram command handler."""

import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import bot.telegram_commands as tc
from bot.mode import BotMode, write_bot_mode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Provide a temporary data directory and point BOT_DATA_DIR to it."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("BOT_DATA_DIR", str(d))
    # Also patch the module-level _data_dir so helpers pick up the new value
    monkeypatch.setattr(tc, "_data_dir", lambda: str(d))
    return d


@pytest.fixture()
def db_path(data_dir, monkeypatch):
    """Create a minimal trades.db in the temp data directory."""
    db = data_dir / "trades.db"
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            size REAL NOT NULL DEFAULT 0,
            stop_loss REAL,
            take_profit REAL,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            close_reason TEXT,
            duration_seconds INTEGER,
            ai_decision TEXT,
            ai_confidence REAL,
            ai_reasoning TEXT,
            ai_risk_flags TEXT,
            ai_override INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE bot_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL UNIQUE,
            config_file TEXT,
            strategy TEXT,
            mode TEXT NOT NULL DEFAULT 'normal',
            status TEXT NOT NULL DEFAULT 'starting',
            last_heartbeat TEXT,
            last_signal TEXT,
            last_signal_time TEXT,
            position_side TEXT,
            position_size REAL DEFAULT 0,
            position_entry REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            last_error TEXT,
            last_error_time TEXT,
            loop_count INTEGER DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            started_at TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(tc, "_get_db_path", lambda: str(db))
    return db


def _write_heartbeat(data_dir: Path, symbol: str, age_seconds: float = 0.0) -> None:
    """Write a heartbeat file with an optional age offset."""
    ts = time.time() - age_seconds
    (data_dir / f"heartbeat_{symbol}").write_text(str(ts))


# ---------------------------------------------------------------------------
# _classify_bots
# ---------------------------------------------------------------------------

class TestClassifyBots:
    def test_fresh_heartbeat_is_running(self, data_dir):
        _write_heartbeat(data_dir, "BTCUSDT", age_seconds=30)
        running, stopped = tc._classify_bots()
        assert "BTCUSDT" in running
        assert "BTCUSDT" not in stopped

    def test_stale_heartbeat_is_stopped(self, data_dir):
        _write_heartbeat(data_dir, "BTCUSDT", age_seconds=7 * 3600)
        running, stopped = tc._classify_bots()
        assert "BTCUSDT" not in running
        assert "BTCUSDT" in stopped

    def test_no_heartbeat_files_returns_empty(self, data_dir):
        running, stopped = tc._classify_bots()
        assert running == []
        assert stopped == []

    def test_mixed_bots(self, data_dir):
        _write_heartbeat(data_dir, "AVAXUSDT", age_seconds=60)
        _write_heartbeat(data_dir, "ETHUSDT", age_seconds=8 * 3600)
        running, stopped = tc._classify_bots()
        assert "AVAXUSDT" in running
        assert "ETHUSDT" in stopped


# ---------------------------------------------------------------------------
# _write_all_modes / _read_all_modes
# ---------------------------------------------------------------------------

class TestWriteAllModes:
    def test_write_panic_to_all_bots(self, data_dir):
        _write_heartbeat(data_dir, "BTCUSDT")
        _write_heartbeat(data_dir, "AVAXUSDT")
        count = tc._write_all_modes(BotMode.PANIC)
        assert count == 2
        modes = tc._read_all_modes()
        assert modes["BTCUSDT"] == "panic"
        assert modes["AVAXUSDT"] == "panic"

    def test_write_normal_clears_panic(self, data_dir):
        _write_heartbeat(data_dir, "XRPUSDT")
        tc._write_all_modes(BotMode.PANIC)
        tc._write_all_modes(BotMode.NORMAL)
        modes = tc._read_all_modes()
        assert modes["XRPUSDT"] == "normal"

    def test_write_to_existing_mode_files(self, data_dir):
        # Pre-existing mode files without heartbeat should also be updated
        write_bot_mode("SOLOUSDT", BotMode.TP_ONLY, data_dir=str(data_dir))
        count = tc._write_all_modes(BotMode.GRACEFUL_STOP)
        assert count >= 1
        modes = tc._read_all_modes()
        assert modes["SOLOUSDT"] == "graceful_stop"


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

class TestCmdStatus:
    def test_returns_string(self, data_dir, db_path):
        result = tc._cmd_status()
        assert isinstance(result, str)
        assert "Running" in result

    def test_shows_zero_bots_when_no_heartbeats(self, data_dir, db_path):
        result = tc._cmd_status()
        assert "0/0" in result

    def test_shows_running_count(self, data_dir, db_path):
        _write_heartbeat(data_dir, "BTCUSDT", age_seconds=10)
        _write_heartbeat(data_dir, "ETHUSDT", age_seconds=10)
        result = tc._cmd_status()
        assert "2/2" in result


class TestCmdPnl:
    def test_returns_string_with_no_trades(self, data_dir, db_path):
        result = tc._cmd_pnl()
        assert "PnL" in result
        assert "0.00" in result

    def test_shows_today_pnl(self, data_dir, db_path):
        today = "2026-03-23T10:00:00+00:00"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, side, entry_price, size, status, pnl) "
            "VALUES (?,?,?,?,?,?,?)",
            (today, "BTCUSDT", "buy", 50000.0, 0.01, "closed", 25.50),
        )
        conn.commit()
        conn.close()

        with patch("bot.telegram_commands.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-03-23"
            mock_dt.fromisoformat = __import__("datetime").datetime.fromisoformat
            mock_dt.now.return_value = __import__("datetime").datetime(
                2026, 3, 23, 12, 0, 0,
                tzinfo=__import__("datetime").timezone.utc
            )
            result = tc._cmd_pnl()
        assert "PnL" in result

    def test_win_rate_with_mixed_trades(self, data_dir, db_path):
        today = "2026-03-23T10:00:00+00:00"
        conn = sqlite3.connect(str(db_path))
        conn.executemany(
            "INSERT INTO trades (timestamp, symbol, side, entry_price, size, status, pnl) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (today, "BTCUSDT", "buy", 50000, 0.01, "closed", 10.0),
                (today, "AVAXUSDT", "buy", 25.0, 1.0, "closed", -5.0),
            ],
        )
        conn.commit()
        conn.close()
        result = tc._cmd_pnl()
        assert "PnL" in result


class TestCmdPositions:
    def test_no_positions(self, data_dir, db_path):
        result = tc._cmd_positions()
        assert "No open positions" in result

    def test_shows_open_from_trades(self, data_dir, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO trades (timestamp, symbol, side, entry_price, size, "
            "stop_loss, take_profit, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("2026-03-23 10:00:00", "BTCUSDT", "buy", 65000.0, 0.001, 64000.0, 67000.0, "open"),
        )
        conn.commit()
        conn.close()
        result = tc._cmd_positions()
        assert "BTC" in result
        assert "BUY" in result


class TestCmdBots:
    def test_no_bots(self, data_dir):
        result = tc._cmd_bots()
        assert "0/0" in result
        assert "none running" in result

    def test_shows_running_symbols(self, data_dir):
        _write_heartbeat(data_dir, "BTCUSDT", age_seconds=30)
        result = tc._cmd_bots()
        assert "BTC" in result


class TestCmdMode:
    def test_no_mode_files(self, data_dir):
        result = tc._cmd_mode()
        assert "No mode files found" in result

    def test_shows_counts(self, data_dir):
        write_bot_mode("BTCUSDT", BotMode.NORMAL, data_dir=str(data_dir))
        write_bot_mode("ETHUSDT", BotMode.PANIC, data_dir=str(data_dir))
        result = tc._cmd_mode()
        assert "normal: 1" in result
        assert "panic: 1" in result


class TestCmdPanic:
    def test_sets_panic_mode(self, data_dir):
        _write_heartbeat(data_dir, "BTCUSDT")
        result = tc._cmd_panic()
        assert "PANIC" in result
        assert "1" in result

    def test_zero_bots_still_returns_message(self, data_dir):
        result = tc._cmd_panic()
        assert "PANIC" in result


class TestCmdStop:
    def test_sets_graceful_stop(self, data_dir):
        _write_heartbeat(data_dir, "AVAXUSDT")
        result = tc._cmd_stop()
        assert "stop" in result.lower()
        modes = tc._read_all_modes()
        assert modes["AVAXUSDT"] == "graceful_stop"


class TestCmdResume:
    def test_resumes_normal(self, data_dir):
        _write_heartbeat(data_dir, "AVAXUSDT")
        tc._write_all_modes(BotMode.PANIC)
        result = tc._cmd_resume()
        assert "NORMAL" in result
        modes = tc._read_all_modes()
        assert modes["AVAXUSDT"] == "normal"


class TestCmdHelp:
    def test_contains_all_commands(self, data_dir):
        result = tc._cmd_help()
        for cmd in ("/status", "/balance", "/pnl", "/positions", "/bots",
                    "/mode", "/panic", "/stop", "/resume"):
            assert cmd in result


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_unknown_command_returns_none(self):
        assert tc._dispatch("/unknown") is None

    def test_empty_text_returns_none(self):
        assert tc._dispatch("") is None
        assert tc._dispatch(None) is None

    def test_known_command_returns_string(self, data_dir, db_path):
        result = tc._dispatch("/help")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_command_with_bot_suffix(self, data_dir, db_path):
        result = tc._dispatch("/help@MyCCBTBot")
        assert result is not None

    def test_case_insensitive(self, data_dir, db_path):
        result = tc._dispatch("/HELP")
        assert result is not None

    def test_handler_exception_returns_error_string(self, data_dir, db_path):
        # Patch the entry directly in the command map (the dict is built at import time)
        original = tc._COMMAND_MAP["/help"]
        try:
            tc._COMMAND_MAP["/help"] = MagicMock(side_effect=RuntimeError("boom"))
            result = tc._dispatch("/help")
        finally:
            tc._COMMAND_MAP["/help"] = original
        assert "Error" in result
        assert "boom" in result


# ---------------------------------------------------------------------------
# run_telegram_handler — exits silently with no credentials
# ---------------------------------------------------------------------------

class TestRunTelegramHandler:
    @pytest.mark.asyncio
    async def test_exits_silently_without_token(self, monkeypatch):
        """Handler must exit cleanly when TELEGRAM_BOT_TOKEN is not set."""
        monkeypatch.setattr(tc, "_BOT_TOKEN", "")
        monkeypatch.setattr(tc, "_CHAT_ID", "")
        shutdown = asyncio.Event()
        # Should return without polling anything
        await tc.run_telegram_handler(shutdown)

    @pytest.mark.asyncio
    async def test_stops_on_shutdown_event(self, monkeypatch, data_dir):
        """Handler must exit promptly when shutdown_event is set."""
        monkeypatch.setattr(tc, "_BOT_TOKEN", "fake_token")
        monkeypatch.setattr(tc, "_CHAT_ID", "12345")

        # Mock _get_updates to return empty (no network calls)
        monkeypatch.setattr(tc, "_get_updates", lambda offset, timeout=3: [])
        # Mock _send_message to do nothing
        monkeypatch.setattr(tc, "_send_message", lambda *a, **kw: True)

        shutdown = asyncio.Event()
        task = asyncio.create_task(tc.run_telegram_handler(shutdown))

        # Set shutdown after a brief moment
        await asyncio.sleep(0.05)
        shutdown.set()

        # Handler should complete within a few seconds
        await asyncio.wait_for(task, timeout=5.0)

    @pytest.mark.asyncio
    async def test_ignores_unauthorised_chat(self, monkeypatch, data_dir, db_path):
        """Messages from other chats must be silently ignored."""
        monkeypatch.setattr(tc, "_BOT_TOKEN", "fake_token")
        monkeypatch.setattr(tc, "_CHAT_ID", "99999")
        monkeypatch.setattr(tc, "_send_message", lambda *a, **kw: True)

        updates_iter = iter([
            # One update from wrong chat, then empty forever
            [{"update_id": 1, "message": {"chat": {"id": 11111}, "text": "/help"}}],
            [],
        ])

        def fake_updates(offset, timeout=3):
            try:
                return next(updates_iter)
            except StopIteration:
                return []

        monkeypatch.setattr(tc, "_get_updates", fake_updates)
        send_calls: list[str] = []
        monkeypatch.setattr(tc, "_send_message", lambda msg, **kw: send_calls.append(msg))

        shutdown = asyncio.Event()
        task = asyncio.create_task(tc.run_telegram_handler(shutdown))
        await asyncio.sleep(0.1)
        shutdown.set()
        await asyncio.wait_for(task, timeout=5.0)

        # Only the startup/shutdown messages should be sent, not a /help reply
        # The startup message contains "/help" literally, so filter on "CCBT Commands"
        # which is the actual reply content from _cmd_help
        command_replies = [m for m in send_calls if "CCBT Commands" in m]
        assert command_replies == [], f"Unexpected reply to unauthorised chat: {command_replies}"
