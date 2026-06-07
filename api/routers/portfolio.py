"""Portfolio and bot endpoints — stub implementations for N0 contract.

Real query implementations land in N1 (REST over queries.py).
All endpoints return empty/default responses so OpenAPI is generated correctly
and frontend type generation (openapi-typescript) can proceed in parallel.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db_path
from api.models import (
    AICalibrationResponse,
    BotDetailResponse,
    BotListResponse,
    BotRow,
    DailyPnlResponse,
    EquityResponse,
    LogsResponse,
    PortfolioSummaryResponse,
    TradeListResponse,
)

router = APIRouter(prefix="/api", tags=["portfolio"])


@router.get("/portfolio/summary", response_model=PortfolioSummaryResponse)
async def portfolio_summary(db: str = Depends(get_db_path)) -> PortfolioSummaryResponse:
    """Aggregated portfolio-level stats."""
    return PortfolioSummaryResponse(
        total_trades=0,
        closed_trades=0,
        win_rate_pct=0.0,
        profit_factor=0.0,
        total_pnl=0.0,
        best_bot=None,
        worst_bot=None,
        active_bots=0,
    )


@router.get("/bots", response_model=BotListResponse)
async def list_bots(db: str = Depends(get_db_path)) -> BotListResponse:
    """List all bots with summary stats."""
    return BotListResponse(bots=[])


@router.get("/bots/{symbol}", response_model=BotDetailResponse)
async def bot_detail(symbol: str, db: str = Depends(get_db_path)) -> BotDetailResponse:
    """Detail for a single bot."""
    empty_row = BotRow(
        symbol=symbol,
        strategy=None,
        timeframe=None,
        status=None,
        position_side=None,
        position_size=None,
        unrealized_pnl=None,
        total_pnl=0.0,
        win_rate_pct=0.0,
        profit_factor=0.0,
        trade_count=0,
        last_updated=None,
        mode=None,
    )
    return BotDetailResponse(symbol=symbol, summary=empty_row, recent_trades=[])


@router.get("/trades", response_model=TradeListResponse)
async def list_trades(
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: str = Depends(get_db_path),
) -> TradeListResponse:
    """List closed trades, optionally filtered by symbol."""
    return TradeListResponse(trades=[], total=0)


@router.get("/equity", response_model=EquityResponse)
async def equity_curve(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> EquityResponse:
    """Equity curve data points."""
    return EquityResponse(symbol=symbol, points=[])


@router.get("/daily-pnl", response_model=DailyPnlResponse)
async def daily_pnl(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> DailyPnlResponse:
    """Daily PnL aggregated by UTC date."""
    return DailyPnlResponse(symbol=symbol, days=[])


@router.get("/ai/calibration", response_model=AICalibrationResponse)
async def ai_calibration(db: str = Depends(get_db_path)) -> AICalibrationResponse:
    """AI advisor calibration stats."""
    return AICalibrationResponse(rows=[])


@router.get("/logs", response_model=LogsResponse)
async def get_logs(
    level: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: str = Depends(get_db_path),
) -> LogsResponse:
    """Recent log lines, optionally filtered."""
    return LogsResponse(lines=[], total_returned=0)
