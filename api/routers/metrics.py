"""N9 metric endpoints — stub implementations for N0 contract.

Real implementations land in N1 (linked to queries.py).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db_path
from api.models import (
    CalendarResponse,
    CloseReasonResponse,
    HeatmapResponse,
    OpenRiskResponse,
    TradeGateResponse,
    TradeGateSummary,
)

router = APIRouter(prefix="/api", tags=["metrics"])


@router.get("/close-reasons", response_model=CloseReasonResponse)
async def close_reasons(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> CloseReasonResponse:
    """Close-reason breakdown with PnL sign colouring."""
    return CloseReasonResponse(symbol=symbol, breakdown=[])


@router.get("/trade-gate", response_model=TradeGateResponse)
async def trade_gate(db: str = Depends(get_db_path)) -> TradeGateResponse:
    """Per-symbol attribution gate (FREE / MIXED) with sample-size guard."""
    return TradeGateResponse(
        summary=TradeGateSummary(n_meeting_min=0, n_total=0, min_trades_threshold=15),
        rows=[],
    )


@router.get("/risk", response_model=OpenRiskResponse)
async def open_risk(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> OpenRiskResponse:
    """Open position risk (SL distance, % risk, unprotected flag)."""
    return OpenRiskResponse(symbol=symbol, rows=[], unprotected_count=0)


@router.get("/calendar", response_model=CalendarResponse)
async def calendar_pnl(
    symbol: Optional[str] = Query(default=None),
    year: Optional[int] = Query(default=None),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    db: str = Depends(get_db_path),
) -> CalendarResponse:
    """Daily PnL calendar heatmap for a given month."""
    import datetime

    now = datetime.datetime.utcnow()
    y = year or now.year
    m = month or now.month
    return CalendarResponse(symbol=symbol, year=y, month=m, cells=[])


@router.get("/heatmap", response_model=HeatmapResponse)
async def heatmap(
    symbol: Optional[str] = Query(default=None),
    bucket_hours: int = Query(default=1, ge=1, le=24),
    db: str = Depends(get_db_path),
) -> HeatmapResponse:
    """Hour-of-day × day-of-week PnL heatmap (UTC bucketing)."""
    return HeatmapResponse(symbol=symbol, bucket_hours=bucket_hours, cells=[])
