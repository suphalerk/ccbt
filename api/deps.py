"""FastAPI dependency helpers for the CCBT dashboard API.

Provides:
- DB path resolution (BOT_DATA_DIR → project root)
- Auth token validation (CCBT_DASH_TOKEN)
- Bot roster allowlist (loaded from config files at startup)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# DB path
# ---------------------------------------------------------------------------

_data_dir = os.getenv("BOT_DATA_DIR", "")
if _data_dir and Path(_data_dir, "trades.db").exists():
    DEFAULT_DB_PATH = Path(_data_dir) / "trades.db"
else:
    DEFAULT_DB_PATH = REPO / "trades.db"


def get_db_path() -> str:
    """Dependency: return path to trades.db as a string."""
    return str(DEFAULT_DB_PATH)


# ---------------------------------------------------------------------------
# Symbol sanitization (N8 security — sym_clean)
# ---------------------------------------------------------------------------

_SYM_RE = re.compile(r"^[A-Z0-9]{2,20}$")


def sym_clean(symbol: str) -> str:
    """Canonical, sanitized symbol string for path/mode-file construction.

    Strips slashes and colons (e.g. BTC/USDT:USDT → BTCUSDTUSDT) and
    validates against ^[A-Z0-9]{2,20}$.  Raises ValueError for invalid input.
    """
    cleaned = symbol.replace("/", "").replace(":", "").upper()
    if not _SYM_RE.match(cleaned):
        raise ValueError(f"Invalid symbol: {symbol!r} → {cleaned!r}")
    return cleaned


# ---------------------------------------------------------------------------
# Auth token
# ---------------------------------------------------------------------------

CCBT_DASH_TOKEN: Optional[str] = os.getenv("CCBT_DASH_TOKEN")


async def verify_token(
    x_dash_token: Optional[str] = Header(default=None, alias="X-Dash-Token"),
) -> None:
    """Dependency for state-changing endpoints (POST).

    If CCBT_DASH_TOKEN is set in the environment the header must match.
    If the env var is absent (local dev) auth is skipped.
    """
    if CCBT_DASH_TOKEN and x_dash_token != CCBT_DASH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Dash-Token header",
        )
