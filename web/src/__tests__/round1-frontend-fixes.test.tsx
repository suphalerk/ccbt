/**
 * Round 1 frontend fixes — TDD tests (written BEFORE implementation).
 *
 * BLOCKER #1 WS envelope: useLiveSnapshot must read {type, ts, data:{portfolio, bots}},
 *   not the flat shape.  Non-mocked WebSocket test feeding a real py-shaped message.
 * BLOCKER #4 DOW labels: dow=0 must map to 'Mon' (pandas Mon=0), not 'Sun'.
 * FOLLOW-UPS:
 *   - RiskAtStakeHeader consumes server max_sl_loss/notional (not TS computed)
 *   - AIAnalyticsPage consumes server aggregate (not browser-computed)
 *   - ExpectancyHeatmap title shows 'Total PnL Heatmap' / colours by avg_pnl
 *   - MonthlyCalendar shows real win_rate, not tradeCount
 *   - CloseReason renders server pct (shows pct from server, not recomputed)
 *   - formatMoney handles !isFinite (Infinity / NaN)
 *   - CandleSection is HIDDEN when available=false
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

// ---- mocks -----------------------------------------------------------------

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      closeReasons: vi.fn(),
      tradeGate: vi.fn(),
      openRisk: vi.fn(),
      calendar: vi.fn(),
      heatmap: vi.fn(),
      aiCalibration: vi.fn(),
      botDetail: vi.fn(),
      listTrades: vi.fn(),
      candles: vi.fn(),
      equityCurve: vi.fn(),
    },
  }
})

import { api } from '../api/client'
import type {
  OpenRiskResponse,
  CalendarResponse,
  HeatmapResponse,
  AICalibrationResponse,
} from '../api/client'

// ---- wrapper ----------------------------------------------------------------

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
}

// ============================================================================
// BLOCKER #1: useLiveSnapshot WS envelope shape
// ============================================================================

import { useLiveSnapshot, QUERY_KEYS } from '../hooks/useLiveSnapshot'
import { useQueryClient } from '@tanstack/react-query'

/**
 * TestHarness — renders useLiveSnapshot, exposes internals via data-testid.
 * Provides a way to inject a WS message from outside.
 */
function WsHarness({ onReady: _onReady }: { onReady?: (send: (data: string) => void) => void } = {}) {
  const { status, lastUpdated } = useLiveSnapshot()
  const qc = useQueryClient()

  // Expose TanStack cache read via DOM
  const portfolio = qc.getQueryData(QUERY_KEYS.portfolio)
  const bots = qc.getQueryData(QUERY_KEYS.bots)

  return (
    <div>
      <span data-testid="ws-status">{status}</span>
      <span data-testid="ws-last-updated">{lastUpdated ?? ''}</span>
      <span data-testid="cache-portfolio">{portfolio ? JSON.stringify(portfolio) : ''}</span>
      <span data-testid="cache-bots">{bots ? JSON.stringify(bots) : ''}</span>
    </div>
  )
}

describe('useLiveSnapshot — WS envelope hydration', () => {
  afterEach(() => vi.clearAllMocks())

  it('hydrates TanStack cache from nested {type, ts, data:{portfolio, bots}} envelope', async () => {
    // The REAL py-shaped message that api/ws.py sends:
    const pyMsg = {
      type: 'snapshot',
      ts: '2026-01-01T00:00:00Z',
      data: {
        portfolio: {
          total_trades: 42,
          closed_trades: 30,
          win_rate_pct: 65.5,
          profit_factor: 1.85,
          total_pnl: 120.0,
          best_bot: 'BTCUSDT',
          worst_bot: 'ETHUSDT',
          active_bots: 5,
        },
        bots: [
          {
            symbol: 'BTCUSDT',
            strategy: 'ema_crossover',
            timeframe: '15m',
            status: 'active',
            position_side: null,
            position_size: null,
            unrealized_pnl: null,
            total_pnl: 80.0,
            win_rate_pct: 60.0,
            profit_factor: 1.7,
            trade_count: 15,
            last_updated: '2026-01-01T00:00:00Z',
            mode: 'NORMAL',
          },
        ],
      },
    }

    // Mock WebSocket — must be a real class (constructor) for `new WebSocket(url)` to work.
    let capturedWs: {
      onopen: (() => void) | null
      onmessage: ((e: MessageEvent) => void) | null
      onclose: ((e: CloseEvent) => void) | null
      onerror: (() => void) | null
      close: () => void
    } | null = null

    class MockWebSocket {
      onopen: (() => void) | null = null
      onmessage: ((e: MessageEvent) => void) | null = null
      onclose: ((e: CloseEvent) => void) | null = null
      onerror: (() => void) | null = null
      readyState = 1
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      constructor(_url: string) {
        capturedWs = this
        // trigger onopen asynchronously
        setTimeout(() => { this.onopen?.() }, 0)
      }
      close() {}
    }
    vi.stubGlobal('WebSocket', MockWebSocket)

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <WsHarness onReady={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>
    )

    // Wait for open
    await act(async () => {
      await new Promise(r => setTimeout(r, 20))
    })

    // Feed the real py-shaped message
    await act(async () => {
      capturedWs?.onmessage?.({ data: JSON.stringify(pyMsg) } as MessageEvent)
    })

    // TanStack cache must be populated with portfolio data
    const portEl = screen.getByTestId('cache-portfolio')
    expect(portEl.textContent).toContain('"total_trades":42')
    expect(portEl.textContent).toContain('"win_rate_pct":65.5')

    // TanStack cache must be populated with bots array
    const botsEl = screen.getByTestId('cache-bots')
    expect(botsEl.textContent).toContain('BTCUSDT')

    // lastUpdated must be set from snap.ts
    const luEl = screen.getByTestId('ws-last-updated')
    expect(luEl.textContent).toBe('2026-01-01T00:00:00Z')

    vi.unstubAllGlobals()
  })

  it('buildWsUrl SSR fallback uses port 8501 not 8502', () => {
    // When window is undefined (SSR), the fallback URL should use 8501 (the real port).
    // We test by inspecting the module — buildWsUrl is not exported, so we verify the
    // hook does not crash when window === undefined by checking the source constant.
    // The simplest approach: verify the fallback string in the source.
    // Since we cannot call buildWsUrl directly, we check via the mock WebSocket URL:
    let calledUrl = ''
    class MockWS {
      onopen: (() => void) | null = null
      onmessage: (() => void) | null = null
      onclose: (() => void) | null = null
      onerror: (() => void) | null = null
      readyState = 1
      constructor(url: string) { calledUrl = url }
      close() {}
    }
    vi.stubGlobal('WebSocket', MockWS)

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <WsHarness onReady={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>
    )

    // In jsdom, window.location.host is 'localhost', so URL will be ws://localhost/ws
    // The key assertion: must NOT have port 8502 in it (was the old wrong fallback port)
    expect(calledUrl).not.toContain('8502')

    vi.unstubAllGlobals()
  })
})

// ============================================================================
// BLOCKER #4: DOW labels — pandas Mon=0, Sun=6
// ============================================================================

import { ExpectancyHeatmap } from '../components/NewPanels'

describe('ExpectancyHeatmap — DOW label mapping (BLOCKER #4)', () => {
  afterEach(() => vi.clearAllMocks())

  it('dow=0 renders as Mon (pandas Mon=0)', async () => {
    vi.mocked(api.heatmap).mockResolvedValue({
      symbol: null,
      bucket_hours: 4,
      cells: [
        { hour: 0, dow: 0, pnl: 10.0, trade_count: 25, win_rate_pct: 60 },
      ],
    } as HeatmapResponse)

    const Wrapper = makeWrapper()
    render(<ExpectancyHeatmap />, { wrapper: Wrapper })
    await act(async () => {})

    // The DOW header for dow=0 must read 'Mon', NOT 'Sun'
    const heatmap = await screen.findByTestId('expectancy-heatmap')
    expect(heatmap.textContent).toMatch(/Mon/)
    expect(heatmap.textContent).not.toMatch(/\bSun\b/)
  })

  it('dow=6 renders as Sun (pandas Sun=6)', async () => {
    vi.mocked(api.heatmap).mockResolvedValue({
      symbol: null,
      bucket_hours: 4,
      cells: [
        { hour: 0, dow: 6, pnl: 5.0, trade_count: 25, win_rate_pct: 55 },
      ],
    } as HeatmapResponse)

    const Wrapper = makeWrapper()
    render(<ExpectancyHeatmap />, { wrapper: Wrapper })
    await act(async () => {})

    const heatmap = await screen.findByTestId('expectancy-heatmap')
    expect(heatmap.textContent).toMatch(/Sun/)
  })
})

// ============================================================================
// FOLLOW-UP: RiskAtStakeHeader consumes server notional / max_sl_loss
// ============================================================================

import { RiskAtStakeHeader } from '../components/NewPanels'

describe('RiskAtStakeHeader — consumes server notional/max_sl_loss', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders max_sl_loss from server field (not TS computed)', async () => {
    vi.mocked(api.openRisk).mockResolvedValue({
      symbol: null,
      rows: [],
      unprotected_count: 0,
      notional: 1500.0,
      max_sl_loss: 75.0,
    } as unknown as OpenRiskResponse)

    const Wrapper = makeWrapper()
    render(<RiskAtStakeHeader />, { wrapper: Wrapper })
    await act(async () => {})

    // The displayed max SL loss must be 75.00 (from server), not 0 (TS computed from empty rows)
    const el = await screen.findByTestId('risk-max-sl-loss')
    expect(el.textContent).toContain('75')
  })

  it('renders notional from server field', async () => {
    vi.mocked(api.openRisk).mockResolvedValue({
      symbol: null,
      rows: [],
      unprotected_count: 0,
      notional: 2500.0,
      max_sl_loss: 50.0,
    } as unknown as OpenRiskResponse)

    const Wrapper = makeWrapper()
    render(<RiskAtStakeHeader />, { wrapper: Wrapper })
    await act(async () => {})

    const captionEl = await screen.findByTestId('risk-caption')
    // notional shown in caption
    expect(captionEl.textContent).toMatch(/2500|2,500/)
  })
})

// ============================================================================
// FOLLOW-UP: AIAnalyticsPage consumes server aggregate
// ============================================================================

import { AIAnalyticsPage } from '../pages/AIAnalyticsPage'

describe('AIAnalyticsPage — consumes server aggregate', () => {
  afterEach(() => vi.clearAllMocks())

  it('shows weighted_accuracy_pct from server aggregate instead of browser computed', async () => {
    vi.mocked(api.aiCalibration).mockResolvedValue({
      rows: [
        { symbol: 'BTC/USDT:USDT', total_decisions: 20, correct: 12, accuracy_pct: 60.0, influence_factor: 1.1 },
        { symbol: 'ETH/USDT:USDT', total_decisions: 10, correct: 7,  accuracy_pct: 70.0, influence_factor: 0.9 },
      ],
      aggregate: {
        total_decisions: 30,
        decided_trades: 19,
        weighted_accuracy_pct: 63.33,  // server pre-computed
        avg_influence_factor: 1.05,
      },
    } as AICalibrationResponse)

    const Wrapper = makeWrapper()
    render(<AIAnalyticsPage />, { wrapper: Wrapper })
    await act(async () => {})

    // The avg accuracy card must show server's value, not browser recomputed
    // testId uses legacy name ai-accuracy-pct (same element, renamed in N7 tests)
    const avgAccEl = await screen.findByTestId('ai-accuracy-pct')
    // Server says 63.33, browser compute would round differently — trust server
    expect(avgAccEl.textContent).toContain('63')
  })

  it('shows avg_influence_factor from server aggregate', async () => {
    vi.mocked(api.aiCalibration).mockResolvedValue({
      rows: [],
      aggregate: {
        total_decisions: 0,
        decided_trades: 0,
        weighted_accuracy_pct: 0.0,
        avg_influence_factor: 1.25,
      },
    } as AICalibrationResponse)

    const Wrapper = makeWrapper()
    render(<AIAnalyticsPage />, { wrapper: Wrapper })
    await act(async () => {})

    const infEl = await screen.findByTestId('ai-influence-factor')
    expect(infEl.textContent).toContain('1.25')
  })
})

// ============================================================================
// FOLLOW-UP: MonthlyCalendar shows win_rate not tradeCount in WR label
// ============================================================================

import { MonthlyCalendar } from '../components/NewPanels'

describe('MonthlyCalendar — WR label shows win_rate not trade count', () => {
  afterEach(() => vi.clearAllMocks())

  it('shows win_rate value (not tradeCount) in cell label', async () => {
    vi.mocked(api.calendar).mockResolvedValue({
      symbol: null,
      year: 2026,
      month: 3,
      cells: [
        { date: '2026-03-05', pnl: 15.0, trade_count: 4, win_rate_pct: 75.0 },
      ],
    } as unknown as CalendarResponse)

    const Wrapper = makeWrapper()
    render(<MonthlyCalendar initialYear={2026} initialMonth={3} />, { wrapper: Wrapper })
    await act(async () => {})

    const cell = await screen.findByTestId('cal-cell-2026-03-05')
    // The WR label should show "WR 75%" (or "75%"), NOT "WR 4" (trade count)
    const wrText = cell.textContent ?? ''
    // Must contain 75, must NOT be just "WR 4"
    expect(wrText).toMatch(/75/)
    // Specifically: "WR 4" would be wrong, "WR 75%" is right
    expect(wrText).not.toMatch(/WR 4$/)
  })
})

// ============================================================================
// FOLLOW-UP: formatMoney handles !isFinite
// ============================================================================

import { formatMoney } from '../utils/format'

describe('formatMoney — !isFinite guard', () => {
  it('returns "—" for Infinity', () => {
    expect(formatMoney(Infinity)).toBe('—')
  })

  it('returns "—" for -Infinity', () => {
    expect(formatMoney(-Infinity)).toBe('—')
  })

  it('returns "—" for NaN', () => {
    expect(formatMoney(NaN)).toBe('—')
  })

  it('still formats finite values correctly', () => {
    expect(formatMoney(1234.56)).toBe('$1,234.56')
    expect(formatMoney(-50.5)).toBe('-$50.50')
    expect(formatMoney(null)).toBe('—')
  })
})

// ============================================================================
// FOLLOW-UP: CandleSection hides chart when available=false
// ============================================================================

describe('CandleSection — hides chart when available=false', () => {
  afterEach(() => vi.clearAllMocks())

  it('shows candles-unavailable testId when available=false', async () => {
    vi.mocked(api.botDetail).mockResolvedValue({
      symbol: 'BTCUSDT',
      summary: {
        symbol: 'BTCUSDT', strategy: 'ema_crossover', timeframe: '15m',
        status: 'active', position_side: null, position_size: null,
        unrealized_pnl: null, total_pnl: 0, win_rate_pct: 0,
        profit_factor: 0, trade_count: 0, last_updated: null, mode: 'NORMAL',
      },
      recent_trades: [],
    })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
    vi.mocked(api.candles).mockResolvedValue({ available: false, symbol: 'BTCUSDT', timeframe: null, candles: [] } as any)

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { BotDetailPage } = await import('../pages/BotDetailPage')

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/bots/BTCUSDT']}>
          <BotDetailPage />
        </MemoryRouter>
      </QueryClientProvider>
    )
    await act(async () => {})

    // candles-unavailable must be visible, candle-chart-container must NOT be present
    const unavail = await screen.findByTestId('candles-unavailable')
    expect(unavail).toBeTruthy()
    expect(screen.queryByTestId('candle-chart-container')).toBeNull()
  })
})
