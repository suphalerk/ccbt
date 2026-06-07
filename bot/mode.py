"""Bot mode control: per-bot operational mode flags read each loop iteration.

The dashboard writes mode files; bots read them to adjust behavior without restart.

File format: {"mode": "normal", "updated_at": "2026-03-22 10:00:00"}
File path:   {data_dir}/mode_{symbol_clean}.json
"""

import json
import logging
import os
import re
import tempfile
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

_VALID_MODES = {"normal", "graceful_stop", "tp_only", "panic"}

# Strict pattern: uppercase alphanumeric, 2–20 chars.
# Matches every deployed symbol (e.g. BTCUSDT, 1000BONKUSDT, XAUUSDT).
# Deliberately rejects path components (dots, slashes, percent-encoding).
_SYM_RE = re.compile(r"^[A-Z0-9]{2,20}$")


def sym_clean(symbol: str) -> str:
    """Canonical sanitised symbol string for path / mode-file construction.

    Strips slashes and colons so that ccxt symbols like ``BTC/USDT:USDT``
    are normalised to ``BTCUSDTUSDT`` — exactly the same transformation the
    engine applies inline::

        config['symbol'].replace('/', '').replace(':', '')

    Then validates against ``^[A-Z0-9]{2,20}$`` to reject path-traversal
    payloads before any file-system operation.

    Args:
        symbol: Raw symbol string (ccxt or plain, e.g. ``BTC/USDT:USDT`` or
            ``BTCUSDT``).

    Returns:
        Cleaned, uppercase, filesystem-safe symbol string.

    Raises:
        ValueError: If the cleaned symbol fails the safety regex.
    """
    cleaned = symbol.replace("/", "").replace(":", "").upper()
    if not _SYM_RE.match(cleaned):
        raise ValueError(
            f"Invalid symbol: {symbol!r} → cleaned={cleaned!r} "
            f"does not match {_SYM_RE.pattern}"
        )
    return cleaned


class BotMode(Enum):
    """Operational modes for a running bot instance."""

    NORMAL = "normal"
    """Trade normally — entries and exits as configured."""

    GRACEFUL_STOP = "graceful_stop"
    """No new entries; close positions at TP or SL as they hit."""

    TP_ONLY = "tp_only"
    """No new entries; move SL to break-even and let TP close positions."""

    PANIC = "panic"
    """No new entries; close all open positions immediately at market."""


def _mode_path(symbol_clean: str, data_dir: str) -> str:
    """Return the canonical path for a bot's mode file."""
    return os.path.join(data_dir, f"mode_{symbol_clean}.json")


def read_bot_mode(symbol_clean: str, data_dir: str = "data") -> BotMode:
    """Read the current operational mode for a bot.

    Returns BotMode.NORMAL when the mode file is missing, unreadable,
    or contains an unrecognised mode string — the bot must always be
    able to continue trading even if the mode file is corrupted.

    Args:
        symbol_clean: Coin identifier used in the filename, e.g. ``btcusdt``.
        data_dir: Directory that contains mode files (default ``data``).

    Returns:
        The current BotMode; falls back to NORMAL on any error.
    """
    path = _mode_path(symbol_clean, data_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        mode_str: Optional[str] = payload.get("mode", "").lower()
        if mode_str not in _VALID_MODES:
            logger.warning("Unknown mode %r in %s — defaulting to NORMAL", mode_str, path)
            return BotMode.NORMAL
        return BotMode(mode_str)
    except FileNotFoundError:
        return BotMode.NORMAL
    except (json.JSONDecodeError, TypeError, AttributeError) as exc:
        logger.warning("Corrupt mode file %s (%s) — defaulting to NORMAL", path, exc)
        return BotMode.NORMAL


def write_bot_mode(symbol_clean: str, mode: BotMode, data_dir: str = "data") -> None:
    """Write the operational mode for a bot atomically.

    Uses a tempfile + os.rename to avoid partial writes being read by a
    concurrently running bot loop.

    Args:
        symbol_clean: Coin identifier used in the filename, e.g. ``btcusdt``.
        mode: The BotMode to persist.
        data_dir: Directory that will contain the mode file (created if absent).
    """
    os.makedirs(data_dir, exist_ok=True)
    target = _mode_path(symbol_clean, data_dir)

    from datetime import datetime, timezone

    payload = {
        "mode": mode.value,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Write to a sibling tempfile in the same directory so os.rename is atomic
    # on POSIX (same filesystem, single syscall).
    fd, tmp_path = tempfile.mkstemp(dir=data_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.rename(tmp_path, target)
    except Exception:
        # Clean up the orphaned tempfile on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
