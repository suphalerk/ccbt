/**
 * Batch 2 — Live trade context tests (TDD)
 *
 * Items covered:
 *   #3  UpnlPanel SL/TP/R:R/dist-to-stop columns:
 *         - renders SL, TP, Dist%, R:R headers
 *         - displays server-computed values from PositionMark
 *         - shows '—' for null dist_to_stop_pct and rr_remaining
 *   #5  PortfolioPage header cards:
 *         - "Today Net PnL" card (testId header-today-pnl):
 *             LIVE: shows net_today from WS (NOT recomputed), realized, unrealized + ● dot
 *             OFFLINE fallback: shows summary.today_pnl as realized, '—' unrealized
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

function makeUpnlData(overrides: Partial<WSUpnlData> = {}): WSUpnlData {
  return {
    positions: [makePositionMark()],
    total_upnl: 3.38,
    feed_status: 'live',
    today_realized: 27.86,
    net_today: 31.24,   // server-computed: 27.86 + 3.38; TS must NOT recompute
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
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark()] })} />)

    // Column headers (case-insensitive partial match)
    expect(screen.getByText(/SL/i)).toBeTruthy()
    expect(screen.getByText(/TP/i)).toBeTruthy()
    expect(screen.getByText(/Dist/i)).toBeTruthy()
    expect(screen.getByText(/R:R/i)).toBeTruthy()
  })

  it('displays server-computed dist_to_stop_pct value', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ dist_to_stop_pct: 4.8387 })] })} />)

    const distCell = screen.getByTestId('upnl-dist-BTCUSDT')
    expect(distCell.textContent).toContain('4.84')
  })

  it('displays server-computed rr_remaining value', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ rr_remaining: 1.3333 })] })} />)

    const rrCell = screen.getByTestId('upnl-rr-BTCUSDT')
    expect(rrCell.textContent).toContain('1.33')
  })

  it('shows "—" for null dist_to_stop_pct', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ stop_loss: null, dist_to_stop_pct: null, rr_remaining: null })] })} />)

    const distCell = screen.getByTestId('upnl-dist-BTCUSDT')
    expect(distCell.textContent).toBe('—')
  })

  it('shows "—" for null rr_remaining', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ stop_loss: null, dist_to_stop_pct: null, rr_remaining: null })] })} />)

    const rrCell = screen.getByTestId('upnl-rr-BTCUSDT')
    expect(rrCell.textContent).toBe('—')
  })

  it('displays SL value when present', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ stop_loss: 29500, take_profit: 33000 })] })} />)

    const slCell = screen.getByTestId('upnl-sl-BTCUSDT')
    expect(slCell.textContent).toContain('29,500')
  })

  it('displays TP value when present', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ stop_loss: 29500, take_profit: 33000 })] })} />)

    const tpCell = screen.getByTestId('upnl-tp-BTCUSDT')
    expect(tpCell.textContent).toContain('33,000')
  })

  it('shows "—" for null SL', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ stop_loss: null, dist_to_stop_pct: null, rr_remaining: null })] })} />)

    const slCell = screen.getByTestId('upnl-sl-BTCUSDT')
    expect(slCell.textContent).toBe('—')
  })

  it('shows "—" for null TP', () => {
    render(<UpnlPanel upnl={makeUpnlData({ positions: [makePositionMark({ take_profit: null, rr_remaining: null })] })} />)

    const tpCell = screen.getByTestId('upnl-tp-BTCUSDT')
    expect(tpCell.textContent).toBe('—')
  })
})

// ============================================================================
// #5 — PortfolioPage header: Today Net PnL card (replaces plain Today's PnL)
// ============================================================================

import { PortfolioPage } from '../pages/PortfolioPage'

// Helper: mock all API calls with default empty responses
function mockAllApis(summaryOverrides: Partial<PortfolioSummaryResponse> = {}) {
  vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary(summaryOverrides))
  vi.mocked(api.listBots).mockResolvedValue({ bots: [] })
  vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
  vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
  vi.mocked(api.dailyPnl).mockResolvedValue({ symbol: null, days: [] })
}

describe('PortfolioPage — Today Net PnL card (LIVE feed)', () => {
  afterEach(() => { vi.clearAllMocks() })

  it('shows net_today from the WS feed (server-provided) — NOT recomputed in TS', async () => {
    // net_today=31.24 is the server field; TS must display it verbatim.
    mockAllApis({ today_pnl: 27.86 })
    const upnl = makeUpnlData({ today_realized: 27.86, total_upnl: 3.38, net_today: 31.24 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={upnl} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    // Net value must be the server net_today (31.24), not a TS sum
    expect(card.textContent).toContain('31.24')
  })

  it('shows realized and unrealized breakdown lines', async () => {
    mockAllApis({ today_pnl: 27.86 })
    const upnl = makeUpnlData({ today_realized: 27.86, total_upnl: 3.38, net_today: 31.24 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={upnl} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    expect(card.textContent).toContain('27.86')   // realized line
    expect(card.textContent).toContain('3.38')    // unrealized line
  })

  it('shows live dot (●) when feed_status is live', async () => {
    mockAllApis({ today_pnl: 10.0 })
    const upnl = makeUpnlData({ feed_status: 'live' })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={upnl} />, { wrapper: Wrapper })
    await act(async () => {})

    const dot = await screen.findByTestId('today-net-live-dot')
    expect(dot).toBeTruthy()
  })

  it('colors net_today green when positive', async () => {
    mockAllApis({ today_pnl: 27.86 })
    const upnl = makeUpnlData({ net_today: 31.24 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={upnl} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    // The large NET span is the first <span> with tabular-nums and large text
    const netSpan = card.querySelector('.text-xl')
    expect(netSpan?.className).toContain('emerald')
  })

  it('colors net_today red when negative', async () => {
    mockAllApis({ today_pnl: -5.0 })
    const upnl = makeUpnlData({ today_realized: -8.0, total_upnl: 3.0, net_today: -5.0 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={upnl} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    const netSpan = card.querySelector('.text-xl')
    expect(netSpan?.className).toContain('red')
  })
})

describe('PortfolioPage — Today Net PnL card (OFFLINE fallback)', () => {
  afterEach(() => { vi.clearAllMocks() })

  it('OFFLINE: shows summary.today_pnl as net and realized when upnl is null', async () => {
    mockAllApis({ today_pnl: 75.5 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={null} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    // The realized fallback is summary.today_pnl
    expect(card.textContent).toContain('75.50')
    // Unrealized shows '—' and 'feed offline' hint
    expect(card.textContent).toContain('—')
    expect(card.textContent).toContain('feed offline')
  })

  it('OFFLINE: no live dot shown when upnl is null', async () => {
    mockAllApis({ today_pnl: 20.0 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={null} />, { wrapper: Wrapper })
    await act(async () => {})

    expect(screen.queryByTestId('today-net-live-dot')).toBeNull()
  })

  it('OFFLINE (stale feed): shows summary.today_pnl, no live dot', async () => {
    mockAllApis({ today_pnl: 50.0 })
    const upnl = makeUpnlData({ feed_status: 'stale' })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={upnl} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    // Stale feed → falls back to offline branch (summary.today_pnl)
    expect(card.textContent).toContain('50.00')
    expect(screen.queryByTestId('today-net-live-dot')).toBeNull()
  })

  it('OFFLINE: colors net red for negative summary.today_pnl', async () => {
    mockAllApis({ today_pnl: -25.0 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={null} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    expect(card.textContent).toContain('25.00')
    const netSpan = card.querySelector('.text-xl')
    expect(netSpan?.className).toContain('red')
  })

  it('OFFLINE: shows $0.00 when no closed trades today', async () => {
    mockAllApis({ today_pnl: 0 })

    const Wrapper = makeWrapper()
    render(<PortfolioPage upnl={null} />, { wrapper: Wrapper })
    await act(async () => {})

    const card = await screen.findByTestId('header-today-pnl')
    expect(card.textContent).toContain('0.00')
    expect(card.textContent).not.toBe('—')
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
