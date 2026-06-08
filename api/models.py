"""Pydantic response models for the CCBT dashboard API (v2).

All financial math stays in Python (dashboard/queries.py).
TypeScript only formats and renders — never re-implements these shapes.

Python 3.10+ required (uses X | None union syntax in some places, but all
Pydantic field types use Optional[X] for 3.9 compat if ever backported).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """GET /api/health"""

    status: str = "ok"
    db_exists: bool = False
    version: str = "2"


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


class PortfolioSummaryResponse(BaseModel):
    """GET /api/portfolio/summary — top-level portfolio stats."""

    total_trades: int
    closed_trades: int
    win_rate_pct: float
    profit_factor: float
    total_pnl: float
    best_bot: Optional[str]
    worst_bot: Optional[str]
    active_bots: int


# ---------------------------------------------------------------------------
# Bot list and per-bot
# ---------------------------------------------------------------------------


class BotRow(BaseModel):
    """One row in GET /api/bots."""

    symbol: str
    strategy: Optional[str]
    timeframe: Optional[str]
    status: Optional[str]
    position_side: Optional[str]
    position_size: Optional[float]
    unrealized_pnl: Optional[float]
    total_pnl: float
    win_rate_pct: float
    profit_factor: float
    trade_count: int
    last_updated: Optional[str]
    mode: Optional[str]
    error_count: int = 0


class BotListResponse(BaseModel):
    """GET /api/bots"""

    bots: List[BotRow]


class TradeRow(BaseModel):
    """One closed trade row."""

    id: Optional[int]
    symbol: str
    side: Optional[str]
    entry_price: Optional[float]
    exit_price: Optional[float]
    pnl: Optional[float]
    pnl_pct: Optional[float]
    close_reason: Optional[str]
    timestamp: Optional[str]
    duration_seconds: Optional[int]
    strategy: Optional[str]


class BotDetailResponse(BaseModel):
    """GET /api/bots/{symbol}"""

    symbol: str
    summary: BotRow
    recent_trades: List[TradeRow]


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


class TradeListResponse(BaseModel):
    """GET /api/trades?symbol=&limit="""

    trades: List[TradeRow]
    total: int


# ---------------------------------------------------------------------------
# Equity curve
# ---------------------------------------------------------------------------


class EquityPoint(BaseModel):
    timestamp: str
    equity: float
    cumulative_pnl: float


class EquityResponse(BaseModel):
    """GET /api/equity?symbol="""

    symbol: Optional[str]
    points: List[EquityPoint]


# ---------------------------------------------------------------------------
# Daily PnL
# ---------------------------------------------------------------------------


class DailyPnlPoint(BaseModel):
    date: str
    pnl: float
    trade_count: int


class DailyPnlResponse(BaseModel):
    """GET /api/daily-pnl?symbol="""

    symbol: Optional[str]
    days: List[DailyPnlPoint]


# ---------------------------------------------------------------------------
# AI calibration
# ---------------------------------------------------------------------------


class AICalibrationRow(BaseModel):
    symbol: str
    total_decisions: int
    correct: int
    accuracy_pct: float
    influence_factor: float


class AICalibrationAggregate(BaseModel):
    """Portfolio-level AI calibration aggregate (weighted across all symbols)."""

    total_decisions: int = 0
    decided_trades: int = 0
    weighted_accuracy_pct: float = 0.0  # accuracy across all decided trades
    avg_influence_factor: float = 1.0   # portfolio-level influence multiplier


class AICalibrationResponse(BaseModel):
    """GET /api/ai/calibration"""

    rows: List[AICalibrationRow]
    aggregate: Optional[AICalibrationAggregate] = None


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------


class LogLine(BaseModel):
    timestamp: Optional[str]
    level: Optional[str]
    message: str
    raw: str


class LogsResponse(BaseModel):
    """GET /api/logs?level=&search=&limit="""

    lines: List[LogLine]
    total_returned: int


# ---------------------------------------------------------------------------
# N9 endpoints
# ---------------------------------------------------------------------------


class CloseReasonItem(BaseModel):
    reason: str
    count: int
    total_pnl: float
    pnl_positive: bool


class CloseReasonResponse(BaseModel):
    """GET /api/close-reasons?symbol="""

    symbol: Optional[str]
    breakdown: List[CloseReasonItem]


class TradeGateRow(BaseModel):
    symbol: str
    config_count: int
    trade_count: int
    profit_factor: float
    win_rate_pct: float
    reward_to_avgloss: Optional[float]
    real_r: Optional[float]
    verdict: str  # KEEP_TESTING | READY_TO_AUDIT | DROP | MARGINAL | MIXED
    meets_min_trades: bool


class TradeGateSummary(BaseModel):
    n_meeting_min: int
    n_total: int
    min_trades_threshold: int


class TradeGateResponse(BaseModel):
    """GET /api/trade-gate"""

    summary: TradeGateSummary
    rows: List[TradeGateRow]


class OpenRiskRow(BaseModel):
    symbol: str
    side: Optional[str]
    entry_price: Optional[float]
    stop_loss: Optional[float]
    position_size: Optional[float]
    stop_distance_pct: Optional[float]  # abs(entry - stop_loss) / entry * 100
    unprotected: bool


class OpenRiskResponse(BaseModel):
    """GET /api/risk?symbol="""

    symbol: Optional[str]
    rows: List[OpenRiskRow]
    unprotected_count: int
    notional: float = 0.0            # sum of abs(entry_price * size) for all open positions
    max_sl_loss: float = 0.0         # sum of abs(entry - stop_loss) * size for protected positions


class CalendarCell(BaseModel):
    date: str  # YYYY-MM-DD
    pnl: float
    trade_count: int
    win_rate_pct: float = 0.0  # 0-100


class CalendarResponse(BaseModel):
    """GET /api/calendar?symbol=&year=&month="""

    symbol: Optional[str]
    year: int
    month: int
    cells: List[CalendarCell]


class HeatmapCell(BaseModel):
    hour: int  # 0-23 UTC
    dow: int  # 0=Sunday, 6=Saturday (SQLite strftime %w)
    pnl: float        # total_pnl (kept for backward compat)
    avg_pnl: float    # avg_pnl — true expectancy per trade (use this for colouring/display)
    trade_count: int
    win_rate_pct: float


class HeatmapResponse(BaseModel):
    """GET /api/heatmap?symbol=&bucket_hours="""

    symbol: Optional[str]
    bucket_hours: int
    cells: List[HeatmapCell]


# ---------------------------------------------------------------------------
# Bot mode control (N8)
# ---------------------------------------------------------------------------


class ModeRequest(BaseModel):
    """POST /api/bots/{symbol}/mode"""

    mode: str  # NORMAL | GRACEFUL_STOP | TP_ONLY | PANIC
    confirm_panic: Optional[bool] = None


class BulkModeRequest(BaseModel):
    """POST /api/bots/mode/bulk"""

    symbols: List[str]
    mode: str
    confirm_panic: Optional[bool] = None


class ModeResponse(BaseModel):
    symbol: str
    mode: str
    accepted: bool
    message: Optional[str]


class BulkModeResponse(BaseModel):
    results: List[ModeResponse]


# ---------------------------------------------------------------------------
# N6 candles (persisted OHLCV from the bot — never fetched from the exchange)
# ---------------------------------------------------------------------------


class CandleBar(BaseModel):
    """One OHLCV candle with indicator snapshots."""

    ts: int                    # unix epoch ms (open time)
    open: float
    high: float
    low: float
    close: float
    volume: float
    ema9: Optional[float]
    ema21: Optional[float]
    rsi14: Optional[float]


class CandlesResponse(BaseModel):
    """GET /api/candles?symbol=&timeframe=&limit=

    When available=False the candles list is empty and the UI shows a fallback.
    """

    available: bool
    symbol: Optional[str]
    timeframe: Optional[str]
    candles: List[CandleBar] = []


# ---------------------------------------------------------------------------
# WebSocket envelopes (not covered by OpenAPI — defined here for TS codegen)
# ---------------------------------------------------------------------------


class WSSnapshotPayload(BaseModel):
    """Payload of a /ws snapshot message."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: str = "snapshot"
    portfolio: Optional[PortfolioSummaryResponse] = None
    bots: Optional[List[BotRow]] = None
    timestamp: Optional[str] = None


class WSLogPayload(BaseModel):
    """Payload of a /ws/logs message (high-freq tail)."""

    type: str = "log"
    lines: List[LogLine] = []
    timestamp: Optional[str] = None


class WSHeartbeat(BaseModel):
    """Heartbeat sent every ~30s on both WS channels."""

    type: str = "heartbeat"
    timestamp: Optional[str] = None


# Union type for the WS envelope (use Union[] not X|Y — must be 3.9-safe at runtime)
WSEnvelope = Union[WSSnapshotPayload, WSLogPayload, WSHeartbeat]
