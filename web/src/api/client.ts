/**
 * N3 — Typed API client.
 * Thin fetch wrapper over the generated openapi-typescript types.
 * No hand-written response types — all types come from api-types.d.ts.
 *
 * No financial math here — data arrives pre-computed from the Python backend.
 */
import type { components, operations } from '../api-types.d.ts'

// Re-export schema types for convenience
export type BotRow = components['schemas']['BotRow']
export type BotListResponse = components['schemas']['BotListResponse']
export type BotDetailResponse = components['schemas']['BotDetailResponse']
export type PortfolioSummaryResponse = components['schemas']['PortfolioSummaryResponse']
export type TradeListResponse = components['schemas']['TradeListResponse']
export type TradeRow = components['schemas']['TradeRow']
export type EquityResponse = components['schemas']['EquityResponse']
export type DailyPnlResponse = components['schemas']['DailyPnlResponse']
export type AICalibrationResponse = components['schemas']['AICalibrationResponse']
export type LogsResponse = components['schemas']['LogsResponse']
export type CloseReasonResponse = components['schemas']['CloseReasonResponse']
export type TradeGateResponse = components['schemas']['TradeGateResponse']
export type TradeGateRow = components['schemas']['TradeGateRow']
export type OpenRiskResponse = components['schemas']['OpenRiskResponse']
export type CalendarResponse = components['schemas']['CalendarResponse']
export type HeatmapResponse = components['schemas']['HeatmapResponse']
export type ModeRequest = components['schemas']['ModeRequest']
export type ModeResponse = components['schemas']['ModeResponse']
export type BulkModeRequest = components['schemas']['BulkModeRequest']
export type BulkModeResponse = components['schemas']['BulkModeResponse']
export type HealthResponse = components['schemas']['HealthResponse']
export type CandleBar = components['schemas']['CandleBar']
export type CandlesResponse = components['schemas']['CandlesResponse']

// ---- operations (unused as types, but confirms codegen is present) ---------
export type _ops = operations  // type-checks that codegen produced operations

// ---- fetch helpers ---------------------------------------------------------

/** Base API prefix — proxied in dev, same-origin in prod */
const BASE = ''

async function get<T>(path: string, params?: Record<string, string | number | boolean | null | undefined>): Promise<T> {
  const url = new URL(`${BASE}${path}`, window.location.href)
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined) {
        url.searchParams.set(k, String(v))
      }
    }
  }
  const res = await fetch(url.toString(), {
    headers: { Accept: 'application/json' },
  })
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`)
  }
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown, token?: string | null): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers['X-Dash-Token'] = token
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`)
  }
  return res.json() as Promise<T>
}

// ---- API calls -------------------------------------------------------------

export const api = {
  health: () => get<HealthResponse>('/api/health'),

  portfolioSummary: () =>
    get<PortfolioSummaryResponse>('/api/portfolio/summary'),

  listBots: () =>
    get<BotListResponse>('/api/bots'),

  botDetail: (symbol: string) =>
    get<BotDetailResponse>(`/api/bots/${encodeURIComponent(symbol)}`),

  listTrades: (params?: { symbol?: string | null; limit?: number }) =>
    get<TradeListResponse>('/api/trades', params as Record<string, string | number>),

  equityCurve: (params?: { symbol?: string | null }) =>
    get<EquityResponse>('/api/equity', params as Record<string, string>),

  dailyPnl: (params?: { symbol?: string | null }) =>
    get<DailyPnlResponse>('/api/daily-pnl', params as Record<string, string>),

  aiCalibration: () =>
    get<AICalibrationResponse>('/api/ai/calibration'),

  logs: (params?: { level?: string | null; search?: string | null; limit?: number }) =>
    get<LogsResponse>('/api/logs', params as Record<string, string | number>),

  closeReasons: (params?: { symbol?: string | null }) =>
    get<CloseReasonResponse>('/api/close-reasons', params as Record<string, string>),

  tradeGate: () =>
    get<TradeGateResponse>('/api/trade-gate'),

  openRisk: (params?: { symbol?: string | null }) =>
    get<OpenRiskResponse>('/api/risk', params as Record<string, string>),

  calendar: (params?: { symbol?: string | null; year?: number | null; month?: number | null }) =>
    get<CalendarResponse>('/api/calendar', params as Record<string, string | number>),

  heatmap: (params?: { symbol?: string | null; bucket_hours?: number }) =>
    get<HeatmapResponse>('/api/heatmap', params as Record<string, string | number>),

  setBotMode: (symbol: string, body: ModeRequest, token?: string | null) =>
    post<ModeResponse>(`/api/bots/${encodeURIComponent(symbol)}/mode`, body, token),

  setBulkMode: (body: BulkModeRequest, token?: string | null) =>
    post<BulkModeResponse>('/api/bots/mode/bulk', body, token),

  candles: (params?: { symbol?: string | null; timeframe?: string | null; limit?: number }) =>
    get<CandlesResponse>('/api/candles', params as Record<string, string | number>),
}
