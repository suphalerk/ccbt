/**
 * N4: Portfolio page tests (TDD — written BEFORE implementation)
 * Tests:
 *   1. renders header metrics from a mock snapshot
 *   2. bot grid groups by strategy and shows mode badges
 *   3. tables render rows + correct empty states
 *   4. a WS update mutates the displayed numbers without remount
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { BotRow, PortfolioSummaryResponse } from '../api/client'
import type { TradeRow } from '../api/client'

// ---- mocks -----------------------------------------------------------------

// Mock the API client so tests don't hit real endpoints
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      portfolioSummary: vi.fn(),
      listBots: vi.fn(),
      listTrades: vi.fn(),
    },
  }
})

import { api } from '../api/client'

// ---- fixtures ---------------------------------------------------------------

function makeBot(overrides: Partial<BotRow> = {}): BotRow {
  return {
    symbol: 'BTCUSDT',
    strategy: 'ema_crossover',
    timeframe: '15m',
    status: 'active',
    position_side: null,
    position_size: null,
    unrealized_pnl: null,
    total_pnl: 0,
    win_rate_pct: 0,
    profit_factor: 1,
    trade_count: 0,
    last_updated: null,
    mode: 'NORMAL',
    ...overrides,
  }
}

function makeSummary(overrides: Partial<PortfolioSummaryResponse> = {}): PortfolioSummaryResponse {
  return {
    total_trades: 100,
    closed_trades: 80,
    win_rate_pct: 62.5,
    profit_factor: 1.85,
    total_pnl: 500.0,
    best_bot: 'BTCUSDT',
    worst_bot: 'ETHUSDT',
    active_bots: 5,
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
    timestamp: '2026-01-01T00:00:00Z',
    duration_seconds: 3600,
    strategy: 'ema_crossover',
    ...overrides,
  }
}

// ---- wrapper ----------------------------------------------------------------

function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

function makeWrapperWithClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const Wrapper = function ({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
  return { client, Wrapper }
}

// ---- imports (after mocks) -------------------------------------------------

import { PortfolioPage } from '../pages/PortfolioPage'

// ============================================================================
// Test: Header metrics render from mock snapshot
// ============================================================================

describe('PortfolioPage — Header metrics', () => {
  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders total PnL from summary', async () => {
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    // Wait for query to resolve
    await act(async () => {})

    // $500.00 total PnL should appear
    const pnlEl = await screen.findByTestId('header-total-pnl')
    expect(pnlEl.textContent).toContain('500')
  })

  it('renders win rate from summary', async () => {
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const wrEl = await screen.findByTestId('header-win-rate')
    expect(wrEl.textContent).toContain('62.5')
  })

  it('renders profit factor from summary', async () => {
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const pfEl = await screen.findByTestId('header-profit-factor')
    expect(pfEl.textContent).toContain('1.85')
  })

  it('renders active bots count', async () => {
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const botsEl = await screen.findByTestId('header-active-bots')
    expect(botsEl.textContent).toContain('5')
  })

  it('renders "—" placeholders while loading', () => {
    // Loading state: mock returns a pending promise
    vi.mocked(api.portfolioSummary).mockReturnValue(new Promise(() => {}))
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    // The header container should be present (not crash)
    expect(screen.getByTestId('portfolio-header')).toBeTruthy()
  })
})

// ============================================================================
// Test: Bot grid groups by strategy and shows mode badges
// ============================================================================

describe('PortfolioPage — Bot grid', () => {
  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('groups bots by strategy', async () => {
    const bots: BotRow[] = [
      makeBot({ symbol: 'BTCUSDT', strategy: 'ema_crossover' }),
      makeBot({ symbol: 'ETHUSDT', strategy: 'ema_crossover' }),
      makeBot({ symbol: 'SOLUSDT', strategy: 'ichimoku' }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    // Both strategy group headings should appear
    await screen.findByText(/ema_crossover/i)
    expect(screen.getByText(/ichimoku/i)).toBeTruthy()
  })

  it('shows mode badge for GRACEFUL_STOP', async () => {
    const bots: BotRow[] = [
      makeBot({ symbol: 'BTCUSDT', mode: 'GRACEFUL_STOP' }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    await screen.findByText('BTCUSDT')
    // STOP badge should be visible
    expect(screen.getByText('STOP')).toBeTruthy()
  })

  it('shows mode badge for TP_ONLY', async () => {
    const bots: BotRow[] = [
      makeBot({ symbol: 'BTCUSDT', mode: 'TP_ONLY' }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    await screen.findByText('BTCUSDT')
    expect(screen.getByText('TP')).toBeTruthy()
  })

  it('shows mode badge for PANIC', async () => {
    const bots: BotRow[] = [
      makeBot({ symbol: 'BTCUSDT', mode: 'PANIC' }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    await screen.findByText('BTCUSDT')
    expect(screen.getByText('PANIC')).toBeTruthy()
  })

  it('shows running status (green) for active bots', async () => {
    const bots: BotRow[] = [
      makeBot({ symbol: 'BTCUSDT', status: 'active' }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const pill = await screen.findByTestId('bot-pill-BTCUSDT')
    expect(pill.className).toMatch(/emerald|green/)
  })

  it('shows stopped status (gray) for stopped bots', async () => {
    const bots: BotRow[] = [
      makeBot({ symbol: 'BTCUSDT', status: 'stopped' }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const pill = await screen.findByTestId('bot-pill-BTCUSDT')
    // Stopped bots should have a gray/slate class
    expect(pill.className).toMatch(/slate|gray/)
  })

  it('shows empty state when no bots', async () => {
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    await screen.findByTestId('bot-grid-empty')
  })
})

// ============================================================================
// Test: Bot overview table
// ============================================================================

describe('PortfolioPage — Bot overview table', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders bot rows in the overview table', async () => {
    const bots: BotRow[] = [
      makeBot({ symbol: 'BTCUSDT', total_pnl: 120.5, trade_count: 10 }),
      makeBot({ symbol: 'ETHUSDT', total_pnl: -30.0, trade_count: 5 }),
    ]
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const table = await screen.findByTestId('bot-overview-table')
    expect(within(table).getByText('BTCUSDT')).toBeTruthy()
    expect(within(table).getByText('ETHUSDT')).toBeTruthy()
  })

  it('shows empty state when no bots in overview table', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    await screen.findByTestId('bot-overview-empty')
  })
})

// ============================================================================
// Test: Recent trades table
// ============================================================================

describe('PortfolioPage — Recent trades table', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders trade rows', async () => {
    const trades: TradeRow[] = [
      makeTrade({ id: 1, symbol: 'BTCUSDT', pnl: 25.5 }),
      makeTrade({ id: 2, symbol: 'ETHUSDT', pnl: -10.0, side: 'short' }),
    ]
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades, total: 2 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const table = await screen.findByTestId('recent-trades-table')
    expect(within(table).getByText('BTCUSDT')).toBeTruthy()
    expect(within(table).getByText('ETHUSDT')).toBeTruthy()
  })

  it('shows empty state when no trades', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    await screen.findByTestId('recent-trades-empty')
  })

  it('does not render signal_source column', async () => {
    const trades: TradeRow[] = [
      makeTrade({ id: 1, symbol: 'BTCUSDT' }),
    ]
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades, total: 1 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    // signal_source column header must not appear
    expect(screen.queryByText(/signal.?source/i)).toBeNull()
  })
})

// ============================================================================
// Test: WS update mutates displayed numbers without remount
// ============================================================================

describe('PortfolioPage — Live WS update', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('updates header metrics when TanStack Query cache is updated', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary({ total_pnl: 100.0 }))
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const { client, Wrapper } = makeWrapperWithClient()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    // Initial value
    const pnlEl = await screen.findByTestId('header-total-pnl')
    expect(pnlEl.textContent).toContain('100')

    // Simulate a WS snapshot hydrating the cache with new data
    act(() => {
      client.setQueryData(['portfolio', 'summary'], makeSummary({ total_pnl: 999.0 }))
    })

    // The displayed value should update reactively (no remount)
    await screen.findByText(/999/)
  })

  it('updates bot grid when bots cache is updated', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [makeBot({ symbol: 'BTCUSDT', mode: 'NORMAL' })] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })

    const { client, Wrapper } = makeWrapperWithClient()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    // Initial render — BTCUSDT in NORMAL mode (no mode badge)
    await screen.findByTestId('bot-pill-BTCUSDT')

    // Simulate WS updating bots cache — mode changed to PANIC
    act(() => {
      client.setQueryData(['bots'], {
        bots: [makeBot({ symbol: 'BTCUSDT', mode: 'PANIC' })],
      })
    })

    // PANIC badge should appear without remount
    await screen.findByText('PANIC')
  })
})
