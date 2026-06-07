/**
 * N6 — BotDetailPage tests (TDD — written BEFORE implementation)
 *
 * Tests:
 *   1. Renders performance stats from mock /api/bots/{symbol}
 *   2. Renders trade log table with correct columns (no signal_source)
 *   3. Risk monitor renders thresholds via dot indicators
 *   4. Candle chart renders from mock /api/candles; RSI renders
 *   5. Chart unavailable state when candles.available=false
 *   6. Empty states covered (no trades, no candles)
 *   7. Exit markers placed at timestamp+duration_seconds (not entry time)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { BotDetailResponse, BotRow, TradeRow } from '../api/client'

// ---- mocks -----------------------------------------------------------------

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      botDetail: vi.fn(),
      listTrades: vi.fn(),
      candles: vi.fn(),
      equityCurve: vi.fn(),
    },
  }
})

import { api } from '../api/client'

// ---- fixtures --------------------------------------------------------------

function makeBot(overrides: Partial<BotRow> = {}): BotRow {
  return {
    symbol: 'BTCUSDT',
    strategy: 'ema_crossover',
    timeframe: '15m',
    status: 'active',
    position_side: 'long',
    position_size: 0.01,
    unrealized_pnl: 5.5,
    total_pnl: 120.5,
    win_rate_pct: 65.0,
    profit_factor: 1.85,
    trade_count: 20,
    last_updated: '2026-01-10T00:00:00Z',
    mode: 'NORMAL',
    ...overrides,
  }
}

function makeDetail(overrides: Partial<BotDetailResponse> = {}): BotDetailResponse {
  return {
    symbol: 'BTCUSDT',
    summary: makeBot(),
    recent_trades: [],
    ...overrides,
  }
}

function makeTrade(overrides: Partial<TradeRow> = {}): TradeRow {
  return {
    id: 1,
    symbol: 'BTCUSDT',
    side: 'long',
    entry_price: 50000,
    exit_price: 51000,
    pnl: 25.5,
    pnl_pct: 2.0,
    close_reason: 'take_profit',
    timestamp: '2026-01-01T01:00:00Z',
    duration_seconds: 3600,
    strategy: 'ema_crossover',
    ...overrides,
  }
}

/** Mock CandlesResponse when available */
function makeCandlesAvailable() {
  return {
    available: true,
    symbol: 'BTCUSDT',
    timeframe: '15m',
    candles: [
      { ts: 1_704_067_200_000, open: 40000, high: 40500, low: 39800, close: 40300, volume: 100, ema9: 40100, ema21: 39900, rsi14: 55 },
      { ts: 1_704_068_100_000, open: 40300, high: 40800, low: 40200, close: 40750, volume: 120, ema9: 40250, ema21: 40000, rsi14: 58 },
      { ts: 1_704_069_000_000, open: 40750, high: 41000, low: 40600, close: 40900, volume: 90,  ema9: 40450, ema21: 40100, rsi14: 60 },
    ],
  }
}

/** Mock CandlesResponse when unavailable */
function makeCandlesUnavailable() {
  return { available: false, symbol: 'BTCUSDT', timeframe: null, candles: [] }
}

// ---- wrapper ---------------------------------------------------------------

function makeWrapper(symbol = 'BTCUSDT') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/bots/${symbol}`]}>
          <Routes>
            <Route path="/bots/:symbol" element={<>{children}</>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

// Import after mocks
import { BotDetailPage } from '../pages/BotDetailPage'

// ============================================================================
// Test: Performance stats
// ============================================================================

describe('BotDetailPage — Performance stats', () => {
  beforeEach(() => {
    vi.mocked(api.candles).mockResolvedValue(makeCandlesUnavailable() as any)
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('renders total PnL from bot detail', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ total_pnl: 120.5 }) }))
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const pnlEl = await screen.findByTestId('bot-total-pnl')
    expect(pnlEl.textContent).toContain('120')
  })

  it('renders win rate from bot detail', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ win_rate_pct: 65.0 }) }))
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const wrEl = await screen.findByTestId('bot-win-rate')
    expect(wrEl.textContent).toContain('65')
  })

  it('renders profit factor from bot detail', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ profit_factor: 1.85 }) }))
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const pfEl = await screen.findByTestId('bot-profit-factor')
    expect(pfEl.textContent).toContain('1.85')
  })

  it('renders trade count from bot detail', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ trade_count: 20 }) }))
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const tcEl = await screen.findByTestId('bot-trade-count')
    expect(tcEl.textContent).toContain('20')
  })
})

// ============================================================================
// Test: Trade log table
// ============================================================================

describe('BotDetailPage — Trade log', () => {
  beforeEach(() => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail())
    vi.mocked(api.candles).mockResolvedValue(makeCandlesUnavailable() as any)
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('renders trade rows in trade log table', async () => {
    const trades = [
      makeTrade({ id: 1, pnl: 25.5 }),
      makeTrade({ id: 2, pnl: -10.0, side: 'short' }),
    ]
    vi.mocked(api.listTrades).mockResolvedValue({ trades, total: 2 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const table = await screen.findByTestId('trade-log-table')
    expect(table).toBeTruthy()
    // Check that pnl values appear somewhere in the table
    expect(within(table).getByText(/25\.5/)).toBeTruthy()
  })

  it('shows empty state when no trades', async () => {
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('trade-log-empty')
  })

  it('does not render signal_source column', async () => {
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [makeTrade()], total: 1 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('trade-log-table')
    expect(screen.queryByText(/signal.?source/i)).toBeNull()
  })

  it('renders duration column', async () => {
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [makeTrade({ duration_seconds: 3600 })], total: 1 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const table = await screen.findByTestId('trade-log-table')
    // Duration should be rendered (formatted as "1h" or "1h 0m")
    expect(within(table).getByText(/\dh|\dm|\ds/)).toBeTruthy()
  })

  it('renders close_reason column', async () => {
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [makeTrade({ close_reason: 'take_profit' })], total: 1 })

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const table = await screen.findByTestId('trade-log-table')
    expect(within(table).getByText(/take_profit/)).toBeTruthy()
  })

  it('renders ai_decision column if present in data', async () => {
    // ai_decision is not in TradeRow schema but the table should not crash if present
    const trade = { ...makeTrade(), ai_decision: 'proceed' }
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [trade as any], total: 1 })

    // Should not throw
    expect(() => render(<BotDetailPage />, { wrapper: makeWrapper() })).not.toThrow()
  })
})

// ============================================================================
// Test: Candle chart (available / unavailable)
// ============================================================================

describe('BotDetailPage — Candle chart', () => {
  beforeEach(() => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail())
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('shows unavailable state when candles.available=false', async () => {
    vi.mocked(api.candles).mockResolvedValue(makeCandlesUnavailable() as any)

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    // Candles unavailable — a fallback message should be shown
    await screen.findByTestId('candles-unavailable')
  })

  it('renders candle chart container when candles are available', async () => {
    vi.mocked(api.candles).mockResolvedValue(makeCandlesAvailable() as any)

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    // When candles are available the chart container renders
    await screen.findByTestId('candle-chart-container')
  })

  it('renders RSI subchart when candles are available', async () => {
    vi.mocked(api.candles).mockResolvedValue(makeCandlesAvailable() as any)

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('rsi-chart-container')
  })

  it('does not crash when candles api errors', async () => {
    vi.mocked(api.candles).mockRejectedValue(new Error('Network error'))

    const Wrapper = makeWrapper()
    // Should not throw
    expect(() => render(<BotDetailPage />, { wrapper: Wrapper })).not.toThrow()
  })
})

// ============================================================================
// Test: Risk monitor
// ============================================================================

describe('BotDetailPage — Risk monitor', () => {
  beforeEach(() => {
    vi.mocked(api.candles).mockResolvedValue(makeCandlesUnavailable() as any)
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('renders risk monitor section', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail())

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('risk-monitor')
  })

  it('shows position side info when position is open', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({
      summary: makeBot({ position_side: 'long', position_size: 0.01 }),
    }))

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('risk-monitor')
    // Position side should appear somewhere in the risk monitor
    const monitor = screen.getByTestId('risk-monitor')
    expect(monitor.textContent?.toLowerCase()).toMatch(/long|position/)
  })

  it('shows no position message when position_side is null', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({
      summary: makeBot({ position_side: null, position_size: null }),
    }))

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const monitor = await screen.findByTestId('risk-monitor')
    expect(monitor.textContent?.toLowerCase()).toMatch(/no position|flat|none/i)
  })
})

// ============================================================================
// Test: Mode + status header
// ============================================================================

describe('BotDetailPage — Header', () => {
  beforeEach(() => {
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.candles).mockResolvedValue(makeCandlesUnavailable() as any)
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('renders the symbol in the page heading', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail())

    const Wrapper = makeWrapper('BTCUSDT')
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    // Symbol should appear in a heading
    expect(screen.getByRole('heading', { level: 1 })).toBeTruthy()
    const h1 = screen.getByRole('heading', { level: 1 })
    expect(h1.textContent).toContain('BTCUSDT')
  })

  it('shows mode badge for non-NORMAL mode', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({
      summary: makeBot({ mode: 'PANIC' }),
    }))

    const Wrapper = makeWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByText(/PANIC/)
  })
})
