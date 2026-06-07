"""FastAPI dependency helpers for the CCBT dashboard API.

Provides:
- DB path resolution (BOT_DATA_DIR → project root)
- Auth token validation (CCBT_DASH_TOKEN)
- Bot roster allowlist (loaded from config files at startup)
- Startup safety assertion (refuse non-localhost without token)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import FrozenSet, Optional

from fastapi import Depends, Header, HTTPException, status

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Re-export sym_clean from bot/mode.py — single source of truth.
# engine.py uses the same formula inline; this function must produce identical
# results for every roster symbol (asserted in tests/test_api_control.py).
# ---------------------------------------------------------------------------

from bot.mode import sym_clean  # noqa: F401  (re-exported)

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
# Bot roster allowlist
# ---------------------------------------------------------------------------

_SYM_RE = re.compile(r"^[A-Z0-9]{2,20}$")

# Configs that are NOT deployed as trading bots (skip from roster)
_SKIP_CONFIGS: frozenset[str] = frozenset({
    "config_aggressive.json",
    "config_sniper.json",
    "config_yolo.json",
    "config_max.json",
    "config_doge.json",
    "config_sol_ichi.json",
    "config_gold.json",
    "config_gold_forex.json",
    "config_arb.json",
    "config_btc_ichi.json",
    "config_1000shibusdt_ichi.json",
    "config_trxusdt_ichi.json",
    "config_xlmusdt_ichi.json",
    "config_saharausdt_supertrend.json",
    "config_polusdt_ichi4htrail.json",
})


def get_roster() -> FrozenSet[str]:
    """Load the roster of deployed bot symbols from config_*.json files.

    Returns a frozenset of sym_clean'd symbol strings, e.g.
    ``frozenset({'BTCUSDT', '1000BONKUSDT', ...})``.

    Symbols that fail sym_clean() (e.g. OANDA XAU_USD) are silently excluded —
    those are not Binance perpetual bots and cannot be mode-controlled via this API.

    The count is COMPUTED from config files at call-time, NOT from bot_health
    (which deduplicates to one row per symbol_clean — correct for display but
    would miss the multi-config coins that need MIXED attribution).
    """
    symbols: set[str] = set()
    for cfg_path in sorted(REPO.glob("config*.json")):
        if cfg_path.name in _SKIP_CONFIGS:
            continue
        try:
            cfg = json.loads(cfg_path.read_text())
        except Exception:
            continue
        symbol = cfg.get("symbol", "")
        if not symbol:
            continue
        try:
            cleaned = sym_clean(symbol)
        except ValueError:
            continue  # OANDA XAU_USD etc. — not a Binance perp
        symbols.add(cleaned)
    return frozenset(symbols)


# ---------------------------------------------------------------------------
# Startup safety assertion
# ---------------------------------------------------------------------------

def assert_startup_safety(host: str, token: Optional[str]) -> None:
    """Refuse to start if host is not localhost and no token is set.

    This guards against accidentally exposing the control API to the network
    without authentication.  Behind nginx the localhost peer-check is vacuous;
    the token provides the real protection layer.

    Args:
        host: The bind address (e.g. '127.0.0.1' or '0.0.0.0').
        token: Value of CCBT_DASH_TOKEN env var (None or '' if unset).

    Raises:
        RuntimeError: If host is not a loopback address and token is absent.
    """
    _loopback = {"127.0.0.1", "::1", "localhost"}
    if host not in _loopback and not token:
        raise RuntimeError(
            f"CCBT_DASH_TOKEN must be set when binding to host={host!r}. "
            "Set CCBT_DASH_TOKEN or bind to 127.0.0.1."
        )


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
