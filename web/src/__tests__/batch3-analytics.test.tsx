/**
 * Batch 3 — Analytics tests (TDD)
 *
 * #6 UnderwaterChart: renders from server-supplied `underwater` points
 * #7 BotPill: shows truncated symbol label + keeps full aria-label
 * #8 BotOverviewTable: text/status/strategy filters compose with sort; empty state
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, fireEvent, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { BotRow, PortfolioSummaryResponse } from '../api/client'

// ---- mocks -----------------------------------------------------------------

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      portfolioSummary: vi.fn(),
      listBots: vi.fn(),
      listTrades: vi.fn(),
      equityCurve: vi.fn().mockResolvedValue({ symbol: null, points: [] }),
      dailyPnl: vi.fn().mockResolvedValue({ symbol: null, days: [] }),
    },
  }
})

import { api } from '../api/client'

// recharts uses ResizeObserver which jsdom doesn't have
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// ---- fixtures ---------------------------------------------------------------

import type { EquityPoint } from '../components/Charts'

function makeEquityPoint(
  timestamp: string,
  cumulative_pnl: number,
  underwater: number = 0,
): EquityPoint {
  return { timestamp, cumulative_pnl, equity: 5000 + cumulative_pnl, underwater }
}

function makeBot(overrides: Partial<BotRow> = {}): BotRow {
  // Default status matches what bot/engine.py actually emits ('running'/'stopped').
  // Do NOT use 'active'/'error' — those are never sent by the backend.
  return {
    symbol: 'BTCUSDT',
    strategy: 'ema_crossover',
    timeframe: '15m',
    status: 'running',
    position_side: null,
    position_size: null,
    unrealized_pnl: null,
    total_pnl: 0,
    win_rate_pct: 0,
    profit_factor: 1,
    trade_count: 0,
    last_updated: null,
    mode: 'NORMAL',
    error_count: 0,
    ...overrides,
  }
}

function makeSummary(overrides: Partial<PortfolioSummaryResponse> = {}): PortfolioSummaryResponse {
  return {
    total_trades: 10,
    closed_trades: 10,
    win_rate_pct: 60,
    profit_factor: 1.5,
    total_pnl: 100,
    best_bot: 'BTCUSDT',
    worst_bot: null,
    active_bots: 1,
    notional: 0,
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
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
}

// ============================================================================
// #6 — UnderwaterChart component
// ============================================================================

import { UnderwaterChart } from '../components/Charts'

describe('UnderwaterChart', () => {
  it('renders container when given points with underwater values', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 10, 0),
      makeEquityPoint('2026-01-02T00:00:00Z', 5, -5),
      makeEquityPoint('2026-01-03T00:00:00Z', 20, 0),
      makeEquityPoint('2026-01-04T00:00:00Z', 12, -8),
    ]
    render(<UnderwaterChart points={points} />)
    expect(screen.getByTestId('underwater-chart')).toBeTruthy()
  })

  it('shows empty state when no points', () => {
    render(<UnderwaterChart points={[]} />)
    expect(screen.getByTestId('underwater-chart-empty')).toBeTruthy()
  })

  it('sets data-has-drawdown=true when any point has underwater < 0', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 10, 0),
      makeEquityPoint('2026-01-02T00:00:00Z', 5, -5),
    ]
    render(<UnderwaterChart points={points} />)
    const container = screen.getByTestId('underwater-chart')
    expect(container.getAttribute('data-has-drawdown')).toBe('true')
  })

  it('exposes data-max-depth with the correct minimum underwater value', () => {
    // MAJOR 3: pin the actual magnitude the chart consumes.
    // A 10x corruption of the value (underwater * 10) must fail this assertion.
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 10, 0),
      makeEquityPoint('2026-01-02T00:00:00Z', 5, -5),
      makeEquityPoint('2026-01-03T00:00:00Z', 20, 0),
      makeEquityPoint('2026-01-04T00:00:00Z', 12, -8),
    ]
    render(<UnderwaterChart points={points} />)
    const container = screen.getByTestId('underwater-chart')
    // min([0, -5, 0, -8]) === -8
    expect(container.getAttribute('data-max-depth')).toBe('-8')
  })

  it('sets data-has-drawdown=false when all underwater values are 0', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 10, 0),
      makeEquityPoint('2026-01-02T00:00:00Z', 20, 0),
      makeEquityPoint('2026-01-03T00:00:00Z', 30, 0),
    ]
    render(<UnderwaterChart points={points} />)
    const container = screen.getByTestId('underwater-chart')
    expect(container.getAttribute('data-has-drawdown')).toBe('false')
  })

  it('reports the correct point count', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 10, 0),
      makeEquityPoint('2026-01-02T00:00:00Z', 5, -5),
      makeEquityPoint('2026-01-03T00:00:00Z', 20, 0),
    ]
    render(<UnderwaterChart points={points} />)
    const container = screen.getByTestId('underwater-chart')
    expect(container.getAttribute('data-point-count')).toBe('3')
  })

  it('handles points with missing underwater field (defaults to 0)', () => {
    // EquityPoint without underwater field — backward compat
    const points: EquityPoint[] = [
      { timestamp: '2026-01-01T00:00:00Z', cumulative_pnl: 10, equity: 5010 },
      { timestamp: '2026-01-02T00:00:00Z', cumulative_pnl: 20, equity: 5020 },
    ]
    expect(() => render(<UnderwaterChart points={points} />)).not.toThrow()
  })

  it('does not crash when rendered with a single point', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 10, 0),
    ]
    expect(() => render(<UnderwaterChart points={points} />)).not.toThrow()
    expect(screen.getByTestId('underwater-chart')).toBeTruthy()
  })
})

// ============================================================================
// #7 — BotPill: visible symbol label
// ============================================================================

describe('BotPill — visible symbol label (#7)', () => {
  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({ symbol: null, days: [] })
  })

  afterEach(() => { vi.clearAllMocks() })

  it('shows truncated label "BTC" for BTCUSDT', async () => {
    vi.mocked(api.listBots).mockResolvedValue({
      bots: [makeBot({ symbol: 'BTCUSDT' })],
    })
    const { PortfolioPage } = await import('../pages/PortfolioPage')
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const labelEl = await screen.findByTestId('bot-pill-label-BTCUSDT')
    expect(labelEl.textContent).toBe('BTC')
  })

  it('shows truncated label "ETH" for ETHUSDT', async () => {
    vi.mocked(api.listBots).mockResolvedValue({
      bots: [makeBot({ symbol: 'ETHUSDT' })],
    })
    const { PortfolioPage } = await import('../pages/PortfolioPage')
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const labelEl = await screen.findByTestId('bot-pill-label-ETHUSDT')
    expect(labelEl.textContent).toBe('ETH')
  })

  it('shows truncated label (max 6 chars) for a long symbol', async () => {
    vi.mocked(api.listBots).mockResolvedValue({
      bots: [makeBot({ symbol: '1000SHIBUSDT' })],
    })
    const { PortfolioPage } = await import('../pages/PortfolioPage')
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const labelEl = await screen.findByTestId('bot-pill-label-1000SHIBUSDT')
    expect(labelEl.textContent!.length).toBeLessThanOrEqual(6)
    expect(labelEl.textContent!.length).toBeGreaterThanOrEqual(3)
  })

  it('keeps full symbol in aria-label for accessibility', async () => {
    vi.mocked(api.listBots).mockResolvedValue({
      bots: [makeBot({ symbol: 'BTCUSDT' })],
    })
    const { PortfolioPage } = await import('../pages/PortfolioPage')
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const pill = await screen.findByTestId('bot-pill-BTCUSDT')
    expect(pill.getAttribute('aria-label')).toBe('BTCUSDT')
  })

  it('shows truncated label for /USDT:USDT format', async () => {
    vi.mocked(api.listBots).mockResolvedValue({
      bots: [makeBot({ symbol: 'BTC/USDT:USDT' })],
    })
    const { PortfolioPage } = await import('../pages/PortfolioPage')
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const labelEl = await screen.findByTestId('bot-pill-label-BTC/USDT:USDT')
    // BTC/USDT:USDT stripped → BTC
    expect(labelEl.textContent).toBe('BTC')
  })
})

// ============================================================================
// #8 — BotOverviewTable: filter controls
// ============================================================================

import { PortfolioPage } from '../pages/PortfolioPage'

describe('BotOverviewTable — filters (#8)', () => {
  // Statuses use only values the backend actually emits: 'running' or 'stopped'.
  // 'active' and 'error' are NEVER sent by bot/engine.py — using them gives
  // false confidence that color branches exercise in prod.
  const bots: BotRow[] = [
    makeBot({ symbol: 'BTCUSDT', status: 'running', strategy: 'ema_crossover', total_pnl: 100 }),
    makeBot({ symbol: 'ETHUSDT', status: 'stopped', strategy: 'ichimoku', total_pnl: -20 }),
    makeBot({ symbol: 'SOLUSDT', status: 'running', strategy: 'ema_crossover', total_pnl: 30 }),
    makeBot({ symbol: 'AVAXUSDT', status: 'stopped', strategy: 'supertrend', total_pnl: 5 }),
  ]

  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: null, points: [] })
    vi.mocked(api.dailyPnl).mockResolvedValue({ symbol: null, days: [] })
  })

  afterEach(() => { vi.clearAllMocks() })

  async function renderPage() {
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})
    // Wait for table to appear (bots loaded)
    await screen.findByTestId('bot-overview-filters')
  }

  it('shows filter controls (search, status, strategy)', async () => {
    await renderPage()
    expect(screen.getByTestId('bot-filter-search')).toBeTruthy()
    expect(screen.getByTestId('bot-filter-status')).toBeTruthy()
    expect(screen.getByTestId('bot-filter-strategy')).toBeTruthy()
  })

  it('text search filters by symbol substring (case-insensitive)', async () => {
    await renderPage()

    const searchInput = screen.getByTestId('bot-filter-search')
    fireEvent.change(searchInput, { target: { value: 'eth' } })

    const table = screen.getByTestId('bot-overview-table')
    expect(within(table).getByText('ETHUSDT')).toBeTruthy()
    expect(within(table).queryByText('BTCUSDT')).toBeNull()
    expect(within(table).queryByText('SOLUSDT')).toBeNull()
  })

  it('status filter shows only matching bots', async () => {
    await renderPage()

    const statusSelect = screen.getByTestId('bot-filter-status')
    // Filter by 'running' — the value bot/engine.py actually emits
    fireEvent.change(statusSelect, { target: { value: 'running' } })

    const table = screen.getByTestId('bot-overview-table')
    expect(within(table).getByText('BTCUSDT')).toBeTruthy()
    expect(within(table).getByText('SOLUSDT')).toBeTruthy()
    expect(within(table).queryByText('ETHUSDT')).toBeNull()
    expect(within(table).queryByText('AVAXUSDT')).toBeNull()
  })

  it('strategy filter shows only matching bots', async () => {
    await renderPage()

    const stratSelect = screen.getByTestId('bot-filter-strategy')
    fireEvent.change(stratSelect, { target: { value: 'ichimoku' } })

    const table = screen.getByTestId('bot-overview-table')
    expect(within(table).getByText('ETHUSDT')).toBeTruthy()
    expect(within(table).queryByText('BTCUSDT')).toBeNull()
  })

  it('composing search + status filter narrows results correctly', async () => {
    await renderPage()

    // Search "SOL", then status=running → only SOLUSDT (SOL is running; AVAX is stopped)
    const searchInput = screen.getByTestId('bot-filter-search')
    fireEvent.change(searchInput, { target: { value: 'SOL' } })

    const statusSelect = screen.getByTestId('bot-filter-status')
    fireEvent.change(statusSelect, { target: { value: 'running' } })

    const table = screen.getByTestId('bot-overview-table')
    expect(within(table).getByText('SOLUSDT')).toBeTruthy()
    expect(within(table).queryByText('BTCUSDT')).toBeNull()
  })

  it('strategy filter uses exact-match not substring (ema vs ema_crossover)', async () => {
    // MAJOR 2: pin === semantics — a substring mutation .includes() must fail this test.
    // 'ema' is a strict prefix of 'ema_crossover'; exact-match must exclude 'ema_crossover'.
    vi.mocked(api.listBots).mockResolvedValue({
      bots: [
        makeBot({ symbol: 'BTCUSDT', strategy: 'ema', total_pnl: 10 }),
        makeBot({ symbol: 'ETHUSDT', strategy: 'ema_crossover', total_pnl: 20 }),
      ],
    })
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})
    await screen.findByTestId('bot-overview-filters')

    const stratSelect = screen.getByTestId('bot-filter-strategy')
    fireEvent.change(stratSelect, { target: { value: 'ema' } })

    const table = screen.getByTestId('bot-overview-table')
    // Only 'ema' exact match — 'ema_crossover' must be excluded
    expect(within(table).getByText('BTCUSDT')).toBeTruthy()
    expect(within(table).queryByText('ETHUSDT')).toBeNull()
  })

  it('status filter uses exact-match not substring (run vs running)', async () => {
    // MAJOR 2: same pin for status — 'run' is a prefix of 'running'.
    // Distinct statuses come from the data, so we inject 'run' as a real status.
    vi.mocked(api.listBots).mockResolvedValue({
      bots: [
        makeBot({ symbol: 'BTCUSDT', status: 'run', total_pnl: 10 }),
        makeBot({ symbol: 'ETHUSDT', status: 'running', total_pnl: 20 }),
      ],
    })
    const Wrapper = makeWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})
    await screen.findByTestId('bot-overview-filters')

    const statusSelect = screen.getByTestId('bot-filter-status')
    fireEvent.change(statusSelect, { target: { value: 'run' } })

    const table = screen.getByTestId('bot-overview-table')
    // Only 'run' exact match — 'running' must be excluded
    expect(within(table).getByText('BTCUSDT')).toBeTruthy()
    expect(within(table).queryByText('ETHUSDT')).toBeNull()
  })

  it('shows empty "no match" state when filters exclude all rows', async () => {
    await renderPage()

    const searchInput = screen.getByTestId('bot-filter-search')
    fireEvent.change(searchInput, { target: { value: 'ZZZZZ' } })

    await screen.findByTestId('bot-overview-no-match')
    expect(screen.queryByTestId('bot-overview-table')).toBeNull()
  })

  it('clear-filters button resets all filters', async () => {
    await renderPage()

    // Apply a filter to make the clear button appear
    const searchInput = screen.getByTestId('bot-filter-search')
    fireEvent.change(searchInput, { target: { value: 'BTC' } })

    // Clear button should appear when any filter is active
    const clearBtn = await screen.findByTestId('bot-filter-clear')
    fireEvent.click(clearBtn)

    // All 4 bots should be visible again in the table
    const table = await screen.findByTestId('bot-overview-table')
    expect(within(table).getByText('BTCUSDT')).toBeTruthy()
    expect(within(table).getByText('ETHUSDT')).toBeTruthy()
    expect(within(table).getByText('SOLUSDT')).toBeTruthy()
    expect(within(table).getByText('AVAXUSDT')).toBeTruthy()
  })

  it('sort still works after applying a filter', async () => {
    await renderPage()

    // Filter to just running bots (BTC + SOL)
    const statusSelect = screen.getByTestId('bot-filter-status')
    fireEvent.change(statusSelect, { target: { value: 'running' } })

    const table = screen.getByTestId('bot-overview-table')
    // Both BTC and SOL should be in the filtered table
    expect(within(table).getByText('BTCUSDT')).toBeTruthy()
    expect(within(table).getByText('SOLUSDT')).toBeTruthy()
    // ETH is stopped, not in running
    expect(within(table).queryByText('ETHUSDT')).toBeNull()
  })

  it('shows count badge X/Y bots in filter bar', async () => {
    await renderPage()

    // With no filter, counter should show all bots
    const filtersBar = screen.getByTestId('bot-overview-filters')
    expect(filtersBar.textContent).toMatch(/4\s*\/\s*4 bots/)

    // Apply status filter — 'running' matches BTC + SOL (2 of 4)
    const statusSelect = screen.getByTestId('bot-filter-status')
    fireEvent.change(statusSelect, { target: { value: 'running' } })

    // Counter should update to 2/4
    expect(filtersBar.textContent).toMatch(/2\s*\/\s*4 bots/)
  })
})
