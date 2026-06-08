"""Portfolio and bot endpoints — real implementations backed by dashboard/queries.py.

All financial/metric math lives in queries.py (no re-implementation here).
DataFrames are converted via to_dict(orient='records') then mapped to Pydantic models.
NaN and inf are sanitised server-side before serialisation.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db_path
from api.models import (
    AICalibrationAggregate,
    AICalibrationRow,
    AICalibrationResponse,
    BotDetailResponse,
    BotListResponse,
    BotRow,
    DailyPnlPoint,
    DailyPnlResponse,
    EquityPoint,
    EquityResponse,
    LogLine,
    LogsResponse,
    PortfolioSummaryResponse,
    TradeListResponse,
    TradeRow,
)

REPO = Path(__file__).resolve().parent.parent.parent

router = APIRouter(prefix="/api", tags=["portfolio"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    """Convert a value to float; return default for NaN/inf/None."""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(f) or math.isinf(f):
        return default
    return f


def _safe_float_required(v: Any, default: float = 0.0) -> float:
    """Convert a value to float with a non-None default (for required fields)."""
    result = _safe_float(v, default)
    return result if result is not None else default


def _safe_int(v: Any, default: int = 0) -> int:
    """Convert to int safely."""
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_str(v: Any) -> Optional[str]:
    """Convert value to str, returning None for NaN/None/empty."""
    if v is None:
        return None
    try:
        import math as _math
        if isinstance(v, float) and (_math.isnan(v) or _math.isinf(v)):
            return None
    except Exception:
        pass
    s = str(v)
    return s if s not in ("nan", "None", "") else None


def _trade_row_from_dict(d: Dict[str, Any]) -> TradeRow:
    """Map a raw trades row dict to a TradeRow Pydantic model."""
    return TradeRow(
        id=_safe_int(d.get("id"), 0) or None,
        symbol=str(d.get("symbol") or ""),
        side=_safe_str(d.get("side")),
        entry_price=_safe_float(d.get("entry_price")),
        exit_price=_safe_float(d.get("exit_price")),
        pnl=_safe_float(d.get("pnl")),
        pnl_pct=_safe_float(d.get("pnl_pct")),
        close_reason=_safe_str(d.get("close_reason")),
        timestamp=_safe_str(d.get("timestamp")),
        duration_seconds=_safe_int(d.get("duration_seconds"), 0) or None,
        strategy=_safe_str(d.get("strategy")),
    )


# ---------------------------------------------------------------------------
# /api/portfolio/summary
# ---------------------------------------------------------------------------


@router.get("/portfolio/summary", response_model=PortfolioSummaryResponse)
async def portfolio_summary(db: str = Depends(get_db_path)) -> PortfolioSummaryResponse:
    """Aggregated portfolio-level stats."""
    from dashboard.queries import get_per_bot_summary, get_trade_stats

    stats = get_trade_stats(db_path=db)
    per_bot = get_per_bot_summary(db_path=db)

    # Open count: number of distinct symbols with at least one open trade
    from dashboard.queries import get_open_trades
    open_df = get_open_trades(db_path=db)
    active_bots = int(open_df["symbol"].nunique()) if not open_df.empty and "symbol" in open_df.columns else 0

    # Notional: sum of abs(entry_price * size) for all open positions (Python-only, no exchange call)
    notional = 0.0
    if not open_df.empty and "entry_price" in open_df.columns and "size" in open_df.columns:
        import numpy as _np
        ep = open_df["entry_price"].fillna(0.0).astype(float)
        sz = open_df["size"].fillna(0.0).astype(float)
        notional_series = (ep * sz.abs())
        notional = float(notional_series.sum())
        if _np.isnan(notional) or _np.isinf(notional):
            notional = 0.0

    best_bot: Optional[str] = None
    worst_bot: Optional[str] = None
    if not per_bot.empty and "total_pnl" in per_bot.columns and "symbol" in per_bot.columns:
        best_bot = str(per_bot.iloc[0]["symbol"]) if len(per_bot) > 0 else None
        worst_bot = str(per_bot.iloc[-1]["symbol"]) if len(per_bot) > 0 else None

    pf = _safe_float(stats.get("profit_factor"), default=0.0)
    # profit_factor: cap inf at 999 for JSON safety, or leave as null if no losses
    if pf is None:
        pf = 0.0

    return PortfolioSummaryResponse(
        total_trades=_safe_int(stats.get("total_trades")),
        closed_trades=_safe_int(stats.get("total_trades")),
        win_rate_pct=round(_safe_float_required(stats.get("win_rate")) * 100, 1),
        profit_factor=pf,
        total_pnl=round(_safe_float_required(stats.get("total_pnl")), 2),
        best_bot=best_bot,
        worst_bot=worst_bot,
        active_bots=active_bots,
        notional=round(notional, 2),
    )


# ---------------------------------------------------------------------------
# /api/bots
# ---------------------------------------------------------------------------


@router.get("/bots", response_model=BotListResponse)
async def list_bots(db: str = Depends(get_db_path)) -> BotListResponse:
    """List all bots with per-symbol summary stats."""
    from dashboard.queries import get_bot_health, get_per_bot_summary

    per_bot = get_per_bot_summary(db_path=db)
    health = get_bot_health(db_path=db)

    # Build health lookup by symbol
    health_map: Dict[str, Any] = {}
    if not health.empty and "symbol" in health.columns:
        for _, row in health.iterrows():
            sym = str(row.get("symbol") or "")
            if sym:
                health_map[sym] = row

    bots: List[BotRow] = []
    if not per_bot.empty:
        for _, row in per_bot.iterrows():
            symbol = str(row.get("symbol") or "")
            # h is a pandas Series when present, else None. NEVER use `if h` on a
            # Series (ambiguous truth value) — gate on membership instead.
            h = health_map.get(symbol)
            has_h = h is not None

            def _hstr(key: str) -> Optional[str]:
                if not has_h:
                    return None
                val = h.get(key)
                return (str(val) if val is not None else "") or None

            # win_rate from per_bot summary (stored as 0-100 pct column 'win_rate')
            wr = _safe_float(row.get("win_rate"), 0.0)
            if wr is None:
                wr = 0.0

            raw_mode = _hstr("mode")
            # Normalise to UPPERCASE so the frontend can do simple `=== 'PANIC'` comparisons.
            # bot_health.mode is written by engine.py as BotMode.value (lowercase); upper()
            # fixes the case-mismatch bug where 'panic' never matched 'PANIC' on the frontend.
            mode_upper = raw_mode.upper() if raw_mode else None

            bots.append(BotRow(
                symbol=symbol,
                strategy=_hstr("strategy"),
                timeframe=None,  # not in per_bot summary
                status=_hstr("status"),
                position_side=_hstr("position_side"),
                position_size=_safe_float(h.get("position_size")) if has_h else None,
                unrealized_pnl=None,
                total_pnl=round(_safe_float_required(row.get("total_pnl")), 2),
                win_rate_pct=round(wr, 1),
                profit_factor=_safe_float_required(row.get("profit_factor")),
                trade_count=_safe_int(row.get("trades")),
                last_updated=str(row.get("last_trade")) if row.get("last_trade") is not None else None,
                mode=mode_upper,
                error_count=_safe_int(h.get("error_count")) if has_h else 0,
            ))

    return BotListResponse(bots=bots)


# ---------------------------------------------------------------------------
# /api/bots/{symbol}
# ---------------------------------------------------------------------------


@router.get("/bots/{symbol:path}", response_model=BotDetailResponse)
async def bot_detail(symbol: str, db: str = Depends(get_db_path)) -> BotDetailResponse:
    """Detail for a single bot — stats + recent trades."""
    from dashboard.queries import get_bot_health, get_recent_trades, get_trade_stats

    stats = get_trade_stats(db_path=db, symbol=symbol)
    recent = get_recent_trades(limit=20, db_path=db, symbol=symbol)

    # Pull live health for this symbol (same source as list_bots) so that
    # BotDetailPage can read the actual mode and highlight the correct button.
    health_df = get_bot_health(db_path=db)
    h_row = None
    if not health_df.empty and "symbol" in health_df.columns:
        matching = health_df[health_df["symbol"].astype(str) == symbol]
        if not matching.empty:
            h_row = matching.iloc[0]

    def _hd(key: str):
        if h_row is None:
            return None
        val = h_row.get(key)
        return (str(val) if val is not None else "") or None

    raw_mode = _hd("mode")
    mode_upper = raw_mode.upper() if raw_mode else None

    pf = _safe_float(stats.get("profit_factor"), default=0.0)
    if pf is None:
        pf = 0.0

    summary = BotRow(
        symbol=symbol,
        strategy=_hd("strategy"),
        timeframe=None,
        status=_hd("status"),
        position_side=_hd("position_side"),
        position_size=_safe_float(h_row.get("position_size")) if h_row is not None else None,
        unrealized_pnl=None,
        total_pnl=round(_safe_float_required(stats.get("total_pnl")), 2),
        win_rate_pct=round(_safe_float_required(stats.get("win_rate")) * 100, 1),
        profit_factor=pf,
        trade_count=_safe_int(stats.get("total_trades")),
        last_updated=None,
        mode=mode_upper,
        error_count=_safe_int(h_row.get("error_count")) if h_row is not None else 0,
    )

    trades: List[TradeRow] = []
    if not recent.empty:
        for _, row in recent.iterrows():
            trades.append(_trade_row_from_dict(dict(row)))

    return BotDetailResponse(symbol=symbol, summary=summary, recent_trades=trades)


# ---------------------------------------------------------------------------
# /api/trades
# ---------------------------------------------------------------------------


@router.get("/trades", response_model=TradeListResponse)
async def list_trades(
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: str = Depends(get_db_path),
) -> TradeListResponse:
    """List closed trades, optionally filtered by symbol."""
    from dashboard.queries import get_recent_trades

    df = get_recent_trades(limit=limit, db_path=db, symbol=symbol)
    trades: List[TradeRow] = []
    if not df.empty:
        for _, row in df.iterrows():
            trades.append(_trade_row_from_dict(dict(row)))

    return TradeListResponse(trades=trades, total=len(trades))


# ---------------------------------------------------------------------------
# /api/equity
# ---------------------------------------------------------------------------


@router.get("/equity", response_model=EquityResponse)
async def equity_curve(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> EquityResponse:
    """Equity curve data points."""
    from dashboard.queries import get_equity_curve

    df = get_equity_curve(db_path=db, symbol=symbol)
    points: List[EquityPoint] = []
    if not df.empty and "timestamp" in df.columns and "cumulative_pnl" in df.columns:
        for _, row in df.iterrows():
            cum_pnl = _safe_float_required(row.get("cumulative_pnl"), 0.0)
            points.append(EquityPoint(
                timestamp=str(row.get("timestamp") or ""),
                equity=round(cum_pnl, 2),
                cumulative_pnl=round(cum_pnl, 2),
            ))

    return EquityResponse(symbol=symbol, points=points)


# ---------------------------------------------------------------------------
# /api/daily-pnl
# ---------------------------------------------------------------------------


@router.get("/daily-pnl", response_model=DailyPnlResponse)
async def daily_pnl(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> DailyPnlResponse:
    """Daily PnL aggregated by UTC date."""
    from dashboard.queries import get_daily_pnl

    df = get_daily_pnl(db_path=db, symbol=symbol)
    days: List[DailyPnlPoint] = []
    if not df.empty and "date" in df.columns:
        # Column may be 'daily_pnl' or 'pnl' depending on which function
        pnl_col = "daily_pnl" if "daily_pnl" in df.columns else "pnl"
        tc_col = "trade_count" if "trade_count" in df.columns else "trades"
        for _, row in df.iterrows():
            days.append(DailyPnlPoint(
                date=str(row.get("date") or ""),
                pnl=round(_safe_float_required(row.get(pnl_col)), 2),
                trade_count=_safe_int(row.get(tc_col)),
            ))

    return DailyPnlResponse(symbol=symbol, days=days)


# ---------------------------------------------------------------------------
# /api/ai/calibration
# ---------------------------------------------------------------------------


@router.get("/ai/calibration", response_model=AICalibrationResponse)
async def ai_calibration(db: str = Depends(get_db_path)) -> AICalibrationResponse:
    """AI advisor calibration stats per symbol + portfolio-level aggregate.

    Uses get_calibration_stats() for the influence ladder to avoid re-deriving
    the same logic in the router.
    """
    from dashboard.queries import get_calibration_data, get_calibration_stats

    df = get_calibration_data(db_path=db)
    rows: List[AICalibrationRow] = []

    if not df.empty and "symbol" in df.columns:
        for symbol, grp in df.groupby("symbol"):
            decided = grp[grp["outcome"].notna() & (grp.get("should_skip", 0) == 0)] \
                if "should_skip" in grp.columns else grp[grp["outcome"].notna()]
            total = len(grp)
            correct = int(decided["was_correct"].sum()) if "was_correct" in decided.columns and len(decided) > 0 else 0
            accuracy = correct / len(decided) if len(decided) > 0 else 0.0
            acc = accuracy
            # Influence ladder — same thresholds as get_calibration_stats
            if acc < 0.45:
                influence = 0.5
            elif acc < 0.55:
                influence = 0.75
            elif acc < 0.65:
                influence = 1.0
            else:
                influence = 1.25

            rows.append(AICalibrationRow(
                symbol=str(symbol),
                total_decisions=total,
                correct=correct,
                accuracy_pct=round(accuracy * 100, 1),
                influence_factor=influence,
            ))

    # Portfolio-level aggregate from get_calibration_stats (canonical source)
    agg_stats = get_calibration_stats(db_path=db)
    aggregate = AICalibrationAggregate(
        total_decisions=int(agg_stats.get("total_decisions", 0)),
        decided_trades=int(agg_stats.get("decided_trades", 0)),
        weighted_accuracy_pct=round(float(agg_stats.get("accuracy", 0.0)) * 100, 1),
        avg_influence_factor=float(agg_stats.get("influence_multiplier", 1.0)),
    )

    return AICalibrationResponse(rows=rows, aggregate=aggregate)


# ---------------------------------------------------------------------------
# /api/logs
# ---------------------------------------------------------------------------


@router.get("/logs", response_model=LogsResponse)
async def get_logs(
    level: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    db: str = Depends(get_db_path),
) -> LogsResponse:
    """Recent log lines, optionally filtered by level/search."""
    from dashboard.queries import get_recent_logs

    # Resolve log file path relative to DB location
    import os
    data_dir = os.getenv("BOT_DATA_DIR", "")
    if data_dir:
        log_path = str(Path(data_dir) / "trading_bot.log")
    else:
        log_path = str(REPO / "trading_bot.log")

    raw = get_recent_logs(
        max_lines=limit,
        min_level=level or "ALL",
        search=search or "",
        log_path=log_path,
    )

    lines: List[LogLine] = []
    for entry in raw:
        lines.append(LogLine(
            timestamp=entry.get("timestamp"),
            level=entry.get("level"),
            message=str(entry.get("message") or ""),
            raw=str(entry.get("message") or ""),
        ))

    return LogsResponse(lines=lines, total_returned=len(lines))
