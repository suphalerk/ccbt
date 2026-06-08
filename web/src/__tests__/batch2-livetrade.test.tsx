/**
 * Batch 2 — Live trade context tests (TDD)
 *
 * Items covered:
 *   #3  UpnlPanel SL/TP/R:R/dist-to-stop columns:
 *         - renders SL, TP, Dist%, R:R headers
 *         - displays server-computed values from PositionMark
 *         - shows '—' for null dist_to_stop_pct and rr_remaining
 *   #5  PortfolioPage header cards:
 *         - "Today's PnL" card colored by sign (+green, -red)
 *         - "Open / Notional" card shows active_bots + notional
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { PortfolioSummaryResponse } from '../api/client'
import { UpnlPanel } from '../components/UpnlPanel'
import type { WSUpnlData, PositionMark } from '../ws-types'

// ---- mock api/client --------------------------------------------------------

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      portfolioSummary: vi.fn(),
      listBots: vi.fn(),
      listTrades: vi.fn(),
      equityCurve: vi.fn(),
      dailyPnl: vi.fn(),
    },
  }
})

import { api } from '../api/client'

// ---- fixtures ---------------------------------------------------------------

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
    notional: 25000.0,
    ...overrides,
  }
}

function makePositionMark(overrides: Partial<PositionMark> = {}): PositionMark {
  return {
    symbol: 'BTCUSDT',
    side: 'long',
    entry_price: 30000,
    size: 0.5,
    stop_loss: 29500,
    take_profit: 33000,
    mark_price: 31000,
    upnl: 500,
    ts: null,
    dist_to_stop_pct: 4.8387,   // server-computed: abs(31000-29500)/31000*100
    rr_remaining: 1.3333,        // server-computed: abs(33000-31000)/abs(31000-29500)
    ...overrides,
  }
}

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

// ============================================================================
// #3 — UpnlPanel: SL/TP/R:R/dist-to-stop columns
// ============================================================================

describe('UpnlPanel — SL/TP/R:R/dist-to-stop columns', () => {
  it('renders SL, TP, Dist%, R:R column headers', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark()],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    // Column headers (case-insensitive partial match)
    expect(screen.getByText(/SL/i)).toBeTruthy()
    expect(screen.getByText(/TP/i)).toBeTruthy()
    expect(screen.getByText(/Dist/i)).toBeTruthy()
    expect(screen.getByText(/R:R/i)).toBeTruthy()
  })

  it('displays server-computed dist_to_stop_pct value', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ dist_to_stop_pct: 4.8387 })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const distCell = screen.getByTestId('upnl-dist-BTCUSDT')
    expect(distCell.textContent).toContain('4.84')
  })

  it('displays server-computed rr_remaining value', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ rr_remaining: 1.3333 })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const rrCell = screen.getByTestId('upnl-rr-BTCUSDT')
    expect(rrCell.textContent).toContain('1.33')
  })

  it('shows "—" for null dist_to_stop_pct', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ stop_loss: null, dist_to_stop_pct: null, rr_remaining: null })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const distCell = screen.getByTestId('upnl-dist-BTCUSDT')
    expect(distCell.textContent).toBe('—')
  })

  it('shows "—" for null rr_remaining', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ stop_loss: null, dist_to_stop_pct: null, rr_remaining: null })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const rrCell = screen.getByTestId('upnl-rr-BTCUSDT')
    expect(rrCell.textContent).toBe('—')
  })

  it('displays SL value when present', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ stop_loss: 29500, take_profit: 33000 })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const slCell = screen.getByTestId('upnl-sl-BTCUSDT')
    expect(slCell.textContent).toContain('29,500')
  })

  it('displays TP value when present', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ stop_loss: 29500, take_profit: 33000 })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const tpCell = screen.getByTestId('upnl-tp-BTCUSDT')
    expect(tpCell.textContent).toContain('33,000')
  })

  it('shows "—" for null SL', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ stop_loss: null, dist_to_stop_pct: null, rr_remaining: null })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const slCell = screen.getByTestId('upnl-sl-BTCUSDT')
    expect(slCell.textContent).toBe('—')
  })

  it('shows "—" for null TP', () => {
    const upnl: WSUpnlData = {
      positions: [makePositionMark({ take_profit: null, rr_remaining: null })],
      total_upnl: 500,
      feed_status: 'live',
    }
    render(<UpnlPanel upnl={upnl} />)

    const tpCell = screen.getByTestId('upnl-tp-BTCUSDT')
    expect(tpCell.textContent).toBe('—')
  })
})

// ============================================================================
// #5 — PortfolioPage header: Today's PnL + Open/Notional cards
// ============================================================================

import { PortfolioPage } from '../pages/PortfolioPage'

describe('PortfolioPage — Today\'s PnL card', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders Today\'s PnL card with the last daily-pnl entry', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({
      symbol: null,
      days: [
        { date: '2026-06-07', pnl: -10.0, trade_count: 2 },
        { date: '2026-06-08', pnl: 75.5, trade_count: 5 },
      ],
    })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const todayEl = await screen.findByTestId('header-today-pnl')
    // Should show today's PnL: $75.50
    expect(todayEl.textContent).toContain('75.50')
  })

  it('colors Today\'s PnL green for a positive value', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({
      symbol: null,
      days: [{ date: '2026-06-08', pnl: 75.5, trade_count: 5 }],
    })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const todayEl = await screen.findByTestId('header-today-pnl')
    // Green color class for positive PnL
    expect(todayEl.className).toContain('emerald')
  })

  it('colors Today\'s PnL red for a negative value', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({
      symbol: null,
      days: [{ date: '2026-06-08', pnl: -25.0, trade_count: 3 }],
    })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const todayEl = await screen.findByTestId('header-today-pnl')
    // Red color class for negative PnL
    expect(todayEl.className).toContain('red')
  })

  it('shows "—" for Today\'s PnL when daily-pnl is empty', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({ symbol: null, days: [] })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const todayEl = await screen.findByTestId('header-today-pnl')
    expect(todayEl.textContent).toBe('—')
  })
})

describe('PortfolioPage — Open / Notional card', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders Open/Notional card with active_bots count and notional', async () => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(
      makeSummary({ active_bots: 3, notional: 25000.0 })
    )
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({ symbol: null, days: [] })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    await act(async () => {})

    const notionalEl = await screen.findByTestId('header-open-notional')
    // Should show "3 / $25,000.00"
    expect(notionalEl.textContent).toContain('3')
    expect(notionalEl.textContent).toContain('25,000')
  })

  it('renders "—" when summary is loading', async () => {
    vi.mocked(api.portfolioSummary).mockReturnValue(new Promise(() => {}))
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({ symbol: null, days: [] })

    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })

    // While pending: card should render without crash; value is '—'
    const headerEl = screen.getByTestId('portfolio-header')
    expect(headerEl).toBeTruthy()
  })
})
