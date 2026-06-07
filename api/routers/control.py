"""Bot mode control endpoints — N8 security-hardened implementation.

Security surface:
- sym_clean() sanitises + validates every symbol BEFORE any path construction
- Roster allowlist rejects symbols not in deployed config_*.json files
- CCBT_DASH_TOKEN auth gates all state-changing POSTs
- Server-side PANIC debounce (2nd rapid bulk PANIC → 429)
- Path construction uses only sym_clean output + fixed data_dir prefix

Audit notes:
- write_bot_mode() in bot/mode.py uses tempfile + os.rename (atomic)
- data_dir resolved from BOT_DATA_DIR env or project root / 'data'
- No exchange calls, no ccxt, no sockets — pure file writes
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.deps import get_db_path, get_roster, sym_clean, verify_token
from api.models import (
    BulkModeRequest,
    BulkModeResponse,
    ModeRequest,
    ModeResponse,
)
from bot.mode import BotMode, write_bot_mode

router = APIRouter(prefix="/api/bots", tags=["control"])

# ---------------------------------------------------------------------------
# Valid modes (lowercase, matching BotMode values)
# ---------------------------------------------------------------------------

_VALID_MODE_VALUES = {m.value for m in BotMode}

# ---------------------------------------------------------------------------
# PANIC debounce state (process-global, in-memory)
# Rapid 2nd bulk PANIC within PANIC_DEBOUNCE_SECONDS → 429
# ---------------------------------------------------------------------------

_PANIC_DEBOUNCE_SECONDS: float = float(os.getenv("CCBT_PANIC_DEBOUNCE_S", "30"))
_last_bulk_panic_ts: float = 0.0


def _get_data_dir() -> str:
    """Resolve the data directory from BOT_DATA_DIR env or project default."""
    env = os.getenv("BOT_DATA_DIR", "")
    if env:
        return env
    # Fall back to project root / data
    return str(Path(__file__).resolve().parent.parent.parent / "data")


def _validate_symbol(symbol: str) -> str:
    """Validate and clean a symbol from a URL path parameter.

    Returns the cleaned symbol (uppercase, no slashes/colons).
    Raises HTTPException 400 for invalid or non-roster symbols.
    """
    # URL-decode percent-encoded sequences have already been decoded by
    # Starlette's router before we receive them, but we re-check after
    # sym_clean() for any residual path components.
    try:
        cleaned = sym_clean(symbol)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid symbol: {exc}",
        ) from exc

    roster = get_roster()
    if cleaned not in roster:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Symbol {cleaned!r} is not in the deployed roster",
        )
    return cleaned


def _validate_mode(mode_str: str) -> BotMode:
    """Parse and validate a mode string.  Raises HTTPException 422 for unknowns."""
    mode_lower = mode_str.lower()
    if mode_lower not in _VALID_MODE_VALUES:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid mode {mode_str!r}. "
                f"Must be one of: {sorted(_VALID_MODE_VALUES)}"
            ),
        )
    return BotMode(mode_lower)


# ---------------------------------------------------------------------------
# Single-bot mode endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/{symbol}/mode",
    response_model=ModeResponse,
    dependencies=[Depends(verify_token)],
)
async def set_bot_mode(
    symbol: str,
    body: ModeRequest,
    db: str = Depends(get_db_path),
) -> ModeResponse:
    """Set mode for a single bot (NORMAL | GRACEFUL_STOP | TP_ONLY | PANIC).

    Args:
        symbol: sym_clean'd symbol in the URL path (e.g. BTCUSDT).
        body: ModeRequest with ``mode`` field and optional ``confirm_panic``.

    Returns:
        ModeResponse indicating acceptance and the resulting mode.

    Raises:
        HTTPException 400: Symbol not in roster or invalid format.
        HTTPException 422: Unknown mode string.
        HTTPException 401: Missing or wrong X-Dash-Token.
    """
    cleaned = _validate_symbol(symbol)
    bot_mode = _validate_mode(body.mode)
    data_dir = _get_data_dir()

    write_bot_mode(cleaned, bot_mode, data_dir=data_dir)

    return ModeResponse(
        symbol=cleaned,
        mode=bot_mode.value,
        accepted=True,
        message=f"Mode set to {bot_mode.value}",
    )


# ---------------------------------------------------------------------------
# Bulk mode endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/mode/bulk",
    response_model=BulkModeResponse,
    dependencies=[Depends(verify_token)],
)
async def set_bulk_mode(
    body: BulkModeRequest,
    db: str = Depends(get_db_path),
) -> BulkModeResponse:
    """Set mode for multiple (or all) roster bots in one request.

    If ``symbols`` is empty, the mode is applied to every bot in the roster.

    PANIC debounce: a 2nd bulk PANIC within ``CCBT_PANIC_DEBOUNCE_S`` seconds
    returns 429 to prevent accidental double-clicks from closing everything twice.

    Args:
        body: BulkModeRequest with ``symbols`` list (empty = all) and ``mode``.

    Returns:
        BulkModeResponse with per-symbol ModeResponse entries.

    Raises:
        HTTPException 422: Unknown mode string.
        HTTPException 429: Rapid duplicate bulk PANIC.
        HTTPException 401: Missing or wrong X-Dash-Token.
    """
    global _last_bulk_panic_ts

    bot_mode = _validate_mode(body.mode)
    data_dir = _get_data_dir()
    roster = get_roster()

    # Determine target symbols
    if body.symbols:
        # Only apply to symbols explicitly requested AND in the roster
        target_symbols = [s for s in body.symbols if s in roster]
    else:
        # Empty list = all roster bots
        target_symbols = sorted(roster)

    # PANIC debounce (bulk only)
    if bot_mode is BotMode.PANIC:
        now = time.monotonic()
        if now - _last_bulk_panic_ts < _PANIC_DEBOUNCE_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Bulk PANIC debounce active — wait "
                    f"{_PANIC_DEBOUNCE_SECONDS:.0f}s between bulk PANIC commands"
                ),
            )
        _last_bulk_panic_ts = now

    results: List[ModeResponse] = []
    non_roster = (
        set(body.symbols) - set(target_symbols)
        if body.symbols
        else set()
    )

    for cleaned in target_symbols:
        try:
            write_bot_mode(cleaned, bot_mode, data_dir=data_dir)
            results.append(
                ModeResponse(
                    symbol=cleaned,
                    mode=bot_mode.value,
                    accepted=True,
                    message=f"Mode set to {bot_mode.value}",
                )
            )
        except Exception as exc:
            results.append(
                ModeResponse(
                    symbol=cleaned,
                    mode=bot_mode.value,
                    accepted=False,
                    message=f"Failed to write mode: {exc}",
                )
            )

    # Include rejected non-roster symbols in the response
    for sym in sorted(non_roster):
        results.append(
            ModeResponse(
                symbol=sym,
                mode=body.mode,
                accepted=False,
                message="Symbol not in deployed roster",
            )
        )

    return BulkModeResponse(results=results)
