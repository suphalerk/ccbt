"""Bot mode control endpoints (N8) — stub implementations for N0 contract.

Security: state-changing POSTs are auth-gated (verify_token).
Real implementation in N8 (sanitize symbol, write mode file, PANIC debounce).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_db_path, verify_token
from api.models import (
    BulkModeRequest,
    BulkModeResponse,
    ModeRequest,
    ModeResponse,
)

router = APIRouter(prefix="/api/bots", tags=["control"])


@router.post("/{symbol}/mode", response_model=ModeResponse, dependencies=[Depends(verify_token)])
async def set_bot_mode(
    symbol: str,
    body: ModeRequest,
    db: str = Depends(get_db_path),
) -> ModeResponse:
    """Set mode for a single bot (NORMAL | GRACEFUL_STOP | TP_ONLY | PANIC)."""
    return ModeResponse(
        symbol=symbol,
        mode=body.mode,
        accepted=False,
        message="stub — N8 not yet implemented",
    )


@router.post("/mode/bulk", response_model=BulkModeResponse, dependencies=[Depends(verify_token)])
async def set_bulk_mode(
    body: BulkModeRequest,
    db: str = Depends(get_db_path),
) -> BulkModeResponse:
    """Set mode for multiple bots in one request."""
    return BulkModeResponse(
        results=[
            ModeResponse(
                symbol=sym,
                mode=body.mode,
                accepted=False,
                message="stub — N8 not yet implemented",
            )
            for sym in body.symbols
        ]
    )
