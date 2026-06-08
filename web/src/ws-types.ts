/**
 * WebSocket message envelope types (not covered by OpenAPI).
 * Mirrors api/models.py WSSnapshotPayload, WSLogPayload, WSHeartbeat.
 *
 * Source of truth: api/models.py — keep in sync.
 */

export interface PortfolioSummary {
  total_trades: number
  closed_trades: number
  win_rate_pct: number
  profit_factor: number
  total_pnl: number
  best_bot: string | null
  worst_bot: string | null
  active_bots: number
}

export interface BotRow {
  symbol: string
  strategy: string | null
  timeframe: string | null
  status: string | null
  position_side: string | null
  position_size: number | null
  unrealized_pnl: number | null
  total_pnl: number
  win_rate_pct: number
  profit_factor: number
  trade_count: number
  last_updated: string | null
  mode: string | null
}

export interface LogLine {
  timestamp: string | null
  level: string | null
  message: string
  raw: string
}

/**
 * The real envelope shape from api/ws.py:
 *   { type: "snapshot", ts: "<ISO>", data: { portfolio: {...}, bots: [...] } }
 */
export interface WSSnapshotData {
  portfolio?: PortfolioSummary | null
  bots?: BotRow[] | null
}

export interface WSSnapshotPayload {
  type: 'snapshot'
  /** ISO timestamp from api/ws.py (field name is "ts", not "timestamp") */
  ts?: string | null
  data?: WSSnapshotData | null
  /** @deprecated legacy flat shape — use data.portfolio / data.bots + ts */
  portfolio?: PortfolioSummary | null
  /** @deprecated legacy flat shape */
  bots?: BotRow[] | null
  /** @deprecated legacy field — use ts */
  timestamp?: string | null
}

export interface WSLogPayload {
  type: 'log'
  lines: LogLine[]
  timestamp?: string | null
}

export interface WSHeartbeat {
  type: 'heartbeat'
  timestamp?: string | null
}

// ---------------------------------------------------------------------------
// uPnL feed — realtime unrealized PnL (api/markprice.py)
// ---------------------------------------------------------------------------

export interface PositionMark {
  symbol: string
  side: string
  entry_price: number
  size: number
  stop_loss: number | null
  take_profit: number | null
  mark_price: number
  upnl: number
  ts: string | null
  /** Server-computed: abs(mark - SL) / mark * 100. null when SL missing/invalid. */
  dist_to_stop_pct: number | null
  /** Server-computed: abs(TP - mark) / abs(mark - SL). null when SL or TP missing/invalid. */
  rr_remaining: number | null
}

export type FeedStatus = 'live' | 'stale' | 'offline'

export interface WSUpnlData {
  positions: PositionMark[]
  total_upnl: number
  feed_status: FeedStatus
}

export interface WSUpnlPayload {
  type: 'upnl'
  ts: string
  data: WSUpnlData
}

/** Union type for all WS message envelopes */
export type WSEnvelope = WSSnapshotPayload | WSLogPayload | WSHeartbeat | WSUpnlPayload
