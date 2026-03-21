"""Unit tests for bot/mode.py — per-bot operational mode flag system."""

import json
import os

import pytest

from bot.mode import BotMode, read_bot_mode, write_bot_mode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mode_file(tmp_path, symbol: str) -> str:
    return str(tmp_path / f"mode_{symbol}.json")


def _write_raw(tmp_path, symbol: str, content: str) -> None:
    path = _mode_file(tmp_path, symbol)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReadBotMode:
    def test_read_default_when_no_file(self, tmp_path):
        """Missing mode file must return NORMAL without raising."""
        result = read_bot_mode("btcusdt", data_dir=str(tmp_path))
        assert result is BotMode.NORMAL

    def test_read_default_when_corrupt_file(self, tmp_path):
        """Non-JSON content must return NORMAL, never raise."""
        _write_raw(tmp_path, "btcusdt", "this is not json }{")
        result = read_bot_mode("btcusdt", data_dir=str(tmp_path))
        assert result is BotMode.NORMAL

    def test_read_invalid_mode_string(self, tmp_path):
        """A valid JSON file with an unknown mode string must return NORMAL."""
        _write_raw(tmp_path, "ethusdt", json.dumps({"mode": "turbo_yolo"}))
        result = read_bot_mode("ethusdt", data_dir=str(tmp_path))
        assert result is BotMode.NORMAL

    def test_read_empty_json_object(self, tmp_path):
        """An empty JSON object (no 'mode' key) must return NORMAL."""
        _write_raw(tmp_path, "solusdt", json.dumps({}))
        result = read_bot_mode("solusdt", data_dir=str(tmp_path))
        assert result is BotMode.NORMAL

    def test_read_null_mode_value(self, tmp_path):
        """A null mode value must return NORMAL."""
        _write_raw(tmp_path, "solusdt", json.dumps({"mode": None}))
        result = read_bot_mode("solusdt", data_dir=str(tmp_path))
        assert result is BotMode.NORMAL


class TestWriteAndRead:
    @pytest.mark.parametrize("mode", list(BotMode))
    def test_write_and_read_all_modes(self, tmp_path, mode):
        """Each BotMode must survive a write → read round-trip exactly."""
        symbol = "btcusdt"
        write_bot_mode(symbol, mode, data_dir=str(tmp_path))
        result = read_bot_mode(symbol, data_dir=str(tmp_path))
        assert result is mode

    def test_write_atomic_file_exists(self, tmp_path):
        """After write_bot_mode, the target file must exist and no tempfile left behind."""
        symbol = "xrpusdt"
        write_bot_mode(symbol, BotMode.GRACEFUL_STOP, data_dir=str(tmp_path))

        target = str(tmp_path / f"mode_{symbol}.json")
        assert os.path.isfile(target), "Target mode file was not created"

        # No orphaned .tmp files should remain
        tmp_files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]
        assert tmp_files == [], f"Orphaned tempfiles found: {tmp_files}"

    def test_write_creates_data_dir(self, tmp_path):
        """write_bot_mode must create data_dir if it does not exist."""
        new_dir = str(tmp_path / "subdir" / "modes")
        write_bot_mode("solusdt", BotMode.PANIC, data_dir=new_dir)
        assert os.path.isdir(new_dir)
        result = read_bot_mode("solusdt", data_dir=new_dir)
        assert result is BotMode.PANIC

    def test_write_overwrites_existing_mode(self, tmp_path):
        """A second write must overwrite the previous mode."""
        symbol = "avaxusdt"
        write_bot_mode(symbol, BotMode.TP_ONLY, data_dir=str(tmp_path))
        write_bot_mode(symbol, BotMode.NORMAL, data_dir=str(tmp_path))
        result = read_bot_mode(symbol, data_dir=str(tmp_path))
        assert result is BotMode.NORMAL

    def test_write_payload_contains_updated_at(self, tmp_path):
        """Written file must contain an 'updated_at' timestamp field."""
        symbol = "linkusdt"
        write_bot_mode(symbol, BotMode.PANIC, data_dir=str(tmp_path))
        path = str(tmp_path / f"mode_{symbol}.json")
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        assert "updated_at" in payload
        assert payload["updated_at"]  # non-empty string

    def test_symbol_isolation(self, tmp_path):
        """Mode files for different symbols must not interfere with each other."""
        write_bot_mode("btcusdt", BotMode.PANIC, data_dir=str(tmp_path))
        write_bot_mode("ethusdt", BotMode.GRACEFUL_STOP, data_dir=str(tmp_path))

        assert read_bot_mode("btcusdt", data_dir=str(tmp_path)) is BotMode.PANIC
        assert read_bot_mode("ethusdt", data_dir=str(tmp_path)) is BotMode.GRACEFUL_STOP
        # A third symbol never written → NORMAL
        assert read_bot_mode("solusdt", data_dir=str(tmp_path)) is BotMode.NORMAL
