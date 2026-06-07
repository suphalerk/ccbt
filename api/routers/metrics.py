"""N9 metric endpoints — real implementations backed by dashboard/queries.py.

All financial/metric math lives in queries.py (no re-implementation here).
"""
from __future__ import annotations

import datetime
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db_path
from api.models import (
    CalendarCell,
    CalendarResponse,
    CloseReasonItem,
    CloseReasonResponse,
    HeatmapCell,
    HeatmapResponse,
    OpenRiskResponse,
    OpenRiskRow,
    TradeGateResponse,
    TradeGateRow,
    TradeGateSummary,
)

REPO = Path(__file__).resolve().parent.parent.parent

router = APIRouter(prefix="/api", tags=["metrics"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    """Sanitise float: NaN/inf → default."""
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
    r = _safe_float(v, default)
    return r if r is not None else default


def _safe_int(v: Any, default: int = 0) -> int:
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# /api/close-reasons
# ---------------------------------------------------------------------------


@router.get("/close-reasons", response_model=CloseReasonResponse)
async def close_reasons(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> CloseReasonResponse:
    """Close-reason breakdown with total PnL and sign colouring."""
    from dashboard.queries import get_close_reason_breakdown

    df = get_close_reason_breakdown(db_path=db, symbol=symbol)
    breakdown: List[CloseReasonItem] = []
    if not df.empty and "close_reason" in df.columns:
        for _, row in df.iterrows():
            total_pnl = _safe_float_required(row.get("total_pnl"))
            breakdown.append(CloseReasonItem(
                reason=str(row.get("close_reason") or "unknown"),
                count=_safe_int(row.get("count")),
                total_pnl=round(total_pnl, 2),
                pnl_positive=total_pnl >= 0,
            ))

    return CloseReasonResponse(symbol=symbol, breakdown=breakdown)


# ---------------------------------------------------------------------------
# /api/trade-gate
# ---------------------------------------------------------------------------


@router.get("/trade-gate", response_model=TradeGateResponse)
async def trade_gate(db: str = Depends(get_db_path)) -> TradeGateResponse:
    """Per-symbol attribution gate (FREE / MIXED) with sample-size guard.

    Loads research/forward_test_cohort.json to pass since=manifest['added'] and
    the cohort symbol list to get_trade_gate().

    The ``since=`` filter is scoped to cohort symbols only — non-cohort portfolio
    symbols always use the full trade history.  This prevents the gate panel from
    appearing empty when all portfolio trades predate the manifest's added date
    (the common state on the first day of a new forward-test cohort).
    """
    from dashboard.queries import get_trade_gate

    # Load since= and cohort symbol list from the manifest
    since: Optional[str] = None
    cohort_symbols: Optional[set] = None
    manifest_path = REPO / "research" / "forward_test_cohort.json"
    try:
        import json as _json
        manifest = _json.loads(manifest_path.read_text())
        since = manifest.get("added") or None
        candidates = manifest.get("candidates", [])
        # Normalise to raw coin symbol (e.g. "ZENUSDT") matching DB format
        cohort_symbols = {
            c["coin"].upper()
            for c in candidates
            if isinstance(c, dict) and c.get("coin")
        }
    except Exception:
        pass  # manifest missing or malformed — proceed without since= / cohort filter

    result = get_trade_gate(
        db_path=db,
        project_root=REPO,
        since=since,
        cohort_symbols=cohort_symbols if cohort_symbols else None,
    )
    raw_rows = result.get("rows", [])
    summary_data = result.get("summary", {})

    rows: List[TradeGateRow] = []
    for r in raw_rows:
        pf = _safe_float(r.get("profit_factor"), default=0.0)
        if pf is None:
            pf = 0.0
        rows.append(TradeGateRow(
            symbol=str(r.get("symbol") or ""),
            config_count=_safe_int(r.get("config_count"), 1),
            trade_count=_safe_int(r.get("trades")),
            profit_factor=pf,
            win_rate_pct=round(_safe_float_required(r.get("win_rate")) * 100, 1),
            reward_to_avgloss=_safe_float(r.get("reward_to_avgloss")),
            real_r=_safe_float(r.get("real_r")),
            verdict=str(r.get("verdict") or "KEEP_TESTING"),
            meets_min_trades=bool(r.get("meets_min")),
        ))

    summary = TradeGateSummary(
        n_meeting_min=_safe_int(summary_data.get("n_meeting_min")),
        n_total=_safe_int(summary_data.get("n_total")),
        min_trades_threshold=15,
    )

    return TradeGateResponse(summary=summary, rows=rows)


# ---------------------------------------------------------------------------
# /api/risk
# ---------------------------------------------------------------------------


@router.get("/risk", response_model=OpenRiskResponse)
async def open_risk(
    symbol: Optional[str] = Query(default=None),
    db: str = Depends(get_db_path),
) -> OpenRiskResponse:
    """Open position risk (SL distance, unprotected flag, aggregate notional/max_sl_loss)."""
    from dashboard.queries import get_open_risk, get_open_trades

    df = get_open_trades(db_path=db, symbol=symbol)
    rows: List[OpenRiskRow] = []
    unprotected = 0

    if not df.empty:
        for _, row in df.iterrows():
            entry = _safe_float(row.get("entry_price"))
            sl = _safe_float(row.get("stop_loss"))
            size = _safe_float(row.get("size"))
            side = row.get("side")
            is_unprotected = sl is None

            if is_unprotected:
                unprotected += 1

            # Stop distance pct: abs(entry - stop_loss) / entry * 100
            stop_distance_pct: Optional[float] = None
            if entry and sl and entry > 0:
                stop_distance_pct = round(abs(entry - sl) / entry * 100, 2)

            rows.append(OpenRiskRow(
                symbol=str(row.get("symbol") or ""),
                side=side,
                entry_price=entry,
                stop_loss=sl,
                position_size=size,
                stop_distance_pct=stop_distance_pct,
                unprotected=is_unprotected,
            ))

    # Aggregate values from get_open_risk (avoids re-deriving financial math here)
    agg = get_open_risk(db_path=db, symbol=symbol)
    return OpenRiskResponse(
        symbol=symbol,
        rows=rows,
        unprotected_count=unprotected,
        notional=_safe_float_required(agg.get("notional"), 0.0),
        max_sl_loss=_safe_float_required(agg.get("max_sl_loss"), 0.0),
    )


# ---------------------------------------------------------------------------
# /api/calendar
# ---------------------------------------------------------------------------


@router.get("/calendar", response_model=CalendarResponse)
async def calendar_pnl(
    symbol: Optional[str] = Query(default=None),
    year: Optional[int] = Query(default=None),
    month: Optional[int] = Query(default=None, ge=1, le=12),
    db: str = Depends(get_db_path),
) -> CalendarResponse:
    """Daily PnL calendar heatmap for a given month."""
    from dashboard.queries import get_calendar_pnl

    now = datetime.datetime.utcnow()
    y = year or now.year
    m = month or now.month

    df = get_calendar_pnl(db_path=db, symbol=symbol)
    cells: List[CalendarCell] = []

    if not df.empty and "date" in df.columns:
        # Filter to requested year-month
        month_prefix = f"{y:04d}-{m:02d}"
        mask = df["date"].astype(str).str.startswith(month_prefix)
        filtered = df[mask]

        pnl_col = "daily_pnl" if "daily_pnl" in filtered.columns else "pnl"
        tc_col = "trades" if "trades" in filtered.columns else "trade_count"

        wr_col = "win_rate" if "win_rate" in filtered.columns else None

        for _, row in filtered.iterrows():
            raw_wr = row.get(wr_col) if wr_col else None
            wr_val = float(raw_wr) if raw_wr is not None else 0.0
            # win_rate from queries.py is in [0,1]; convert to percentage
            if wr_val <= 1.0:
                wr_val = round(wr_val * 100, 1)
            cells.append(CalendarCell(
                date=str(row.get("date") or ""),
                pnl=round(_safe_float_required(row.get(pnl_col)), 2),
                trade_count=_safe_int(row.get(tc_col)),
                win_rate_pct=round(wr_val, 1),
            ))

    return CalendarResponse(symbol=symbol, year=y, month=m, cells=cells)


# ---------------------------------------------------------------------------
# /api/heatmap
# ---------------------------------------------------------------------------


@router.get("/heatmap", response_model=HeatmapResponse)
async def heatmap(
    symbol: Optional[str] = Query(default=None),
    bucket_hours: int = Query(default=1, ge=1, le=24),
    db: str = Depends(get_db_path),
) -> HeatmapResponse:
    """Hour-of-day x day-of-week PnL heatmap (UTC bucketing)."""
    from dashboard.queries import get_hour_dow_stats

    df = get_hour_dow_stats(db_path=db, symbol=symbol, bucket_hours=bucket_hours)
    cells: List[HeatmapCell] = []

    if not df.empty:
        for _, row in df.iterrows():
            wr = _safe_float_required(row.get("win_rate"), 0.0)
            cells.append(HeatmapCell(
                hour=_safe_int(row.get("hour_bucket")),
                dow=_safe_int(row.get("dow")),
                pnl=round(_safe_float_required(row.get("total_pnl")), 2),
                avg_pnl=round(_safe_float_required(row.get("avg_pnl")), 2),
                trade_count=_safe_int(row.get("trades")),
                win_rate_pct=round(wr * 100, 1),
            ))

    return HeatmapResponse(symbol=symbol, bucket_hours=bucket_hours, cells=cells)
