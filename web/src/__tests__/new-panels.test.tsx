/**
 * N10 — New Panels UI tests (TDD — written BEFORE implementation)
 *
 * Tests:
 *   1. CloseReasonDonut — positive-PnL `sl` reason renders GREEN (must-fix #6)
 *   2. TradeGateTable — MIXED verdict; all 5 classify verdicts render;
 *      sub-threshold rows greyed; "N of M meet min" header;
 *      sort order DROP→MARGINAL→KEEP_TESTING→READY_TO_AUDIT→MIXED
 *   3. RiskAtStakeHeader — flags unprotected; % hidden when no balance
 *   4. MonthlyCalendar — populated month; empty month (no crash)
 *   5. ExpectancyHeatmap — cells count<20 are grey/uncoloured; diagnostic text present
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, within, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type {
  CloseReasonResponse,
  TradeGateResponse,
  OpenRiskResponse,
  CalendarResponse,
  HeatmapResponse,
} from '../api/client'

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
    },
  }
})

import { api } from '../api/client'

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

// ---- imports (after mocks) -------------------------------------------------

import {
  CloseReasonDonut,
  TradeGateTable,
  RiskAtStakeHeader,
  MonthlyCalendar,
  ExpectancyHeatmap,
} from '../components/NewPanels'

// ============================================================================
// CloseReasonDonut
// ============================================================================

describe('CloseReasonDonut', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders without crashing on empty data', async () => {
    vi.mocked(api.closeReasons).mockResolvedValue({
      symbol: null,
      breakdown: [],
    } as CloseReasonResponse)

    const Wrapper = makeWrapper()
    render(<CloseReasonDonut />, { wrapper: Wrapper })
    await act(async () => {})
    // should not throw
  })

  it('renders a reason label in the legend', async () => {
    vi.mocked(api.closeReasons).mockResolvedValue({
      symbol: null,
      breakdown: [
        { reason: 'take_profit', count: 10, total_pnl: 50.0, pnl_positive: true },
      ],
    } as CloseReasonResponse)

    const Wrapper = makeWrapper()
    render(<CloseReasonDonut />, { wrapper: Wrapper })
    await act(async () => {})

    // legend entry for take_profit should appear
    await screen.findByText(/take_profit/i)
  })

  it('MUST-FIX #6: sl reason with positive total_pnl renders green colour indicator', async () => {
    // 39/192 "sl" rows are actually profitable trail exits — donut must colour
    // by pnl_positive flag (server-computed), NOT by the reason string.
    vi.mocked(api.closeReasons).mockResolvedValue({
      symbol: null,
      breakdown: [
        { reason: 'sl', count: 39, total_pnl: 120.5, pnl_positive: true },
      ],
    } as CloseReasonResponse)

    const Wrapper = makeWrapper()
    render(<CloseReasonDonut />, { wrapper: Wrapper })
    await act(async () => {})

    // The legend entry for "sl" must have a green colour indicator
    const entry = await screen.findByTestId('donut-legend-sl')
    const dot = within(entry).getByTestId('donut-legend-dot-sl')
    // The dot must NOT be red when pnl_positive=true
    expect(dot.className).toMatch(/emerald|green/)
    expect(dot.className).not.toMatch(/red/)
  })

  it('sl reason with negative total_pnl renders red colour indicator', async () => {
    vi.mocked(api.closeReasons).mockResolvedValue({
      symbol: null,
      breakdown: [
        { reason: 'sl', count: 50, total_pnl: -200.0, pnl_positive: false },
      ],
    } as CloseReasonResponse)

    const Wrapper = makeWrapper()
    render(<CloseReasonDonut />, { wrapper: Wrapper })
    await act(async () => {})

    const entry = await screen.findByTestId('donut-legend-sl')
    const dot = within(entry).getByTestId('donut-legend-dot-sl')
    expect(dot.className).toMatch(/red/)
    expect(dot.className).not.toMatch(/emerald|green/)
  })

  it('shows count and percentage in legend label', async () => {
    vi.mocked(api.closeReasons).mockResolvedValue({
      symbol: null,
      breakdown: [
        { reason: 'take_profit', count: 10, total_pnl: 50.0, pnl_positive: true },
        { reason: 'sl', count: 5, total_pnl: -20.0, pnl_positive: false },
      ],
    } as CloseReasonResponse)

    const Wrapper = makeWrapper()
    render(<CloseReasonDonut />, { wrapper: Wrapper })
    await act(async () => {})

    // Should show count (10 or 5 appear somewhere in labels)
    await screen.findByText(/take_profit/)
    const legend = screen.getByTestId('donut-legend')
    expect(legend.textContent).toMatch(/10/)
  })
})

// ============================================================================
// TradeGateTable
// ============================================================================

function makeGateResponse(overrides: Partial<TradeGateResponse> = {}): TradeGateResponse {
  return {
    summary: { n_meeting_min: 3, n_total: 48, min_trades_threshold: 15 },
    rows: [],
    ...overrides,
  }
}

describe('TradeGateTable', () => {
  afterEach(() => vi.clearAllMocks())

  it('shows "N of M meet the 15-trade min" header', async () => {
    vi.mocked(api.tradeGate).mockResolvedValue(
      makeGateResponse({ summary: { n_meeting_min: 3, n_total: 48, min_trades_threshold: 15 } })
    )

    const Wrapper = makeWrapper()
    render(<TradeGateTable />, { wrapper: Wrapper })
    await act(async () => {})

    // Should show something like "3 of 48 meet the 15-trade min"
    const header = await screen.findByTestId('gate-sample-header')
    expect(header.textContent).toMatch(/3/)
    expect(header.textContent).toMatch(/48/)
    expect(header.textContent).toMatch(/15/)
  })

  it('renders all 5 classify verdicts', async () => {
    vi.mocked(api.tradeGate).mockResolvedValue(
      makeGateResponse({
        rows: [
          { symbol: 'AAA', config_count: 1, trade_count: 20, profit_factor: 2.0, win_rate_pct: 60, reward_to_avgloss: 2.0, real_r: null, verdict: 'KEEP_TESTING', meets_min_trades: true },
          { symbol: 'BBB', config_count: 1, trade_count: 20, profit_factor: 2.5, win_rate_pct: 65, reward_to_avgloss: 2.5, real_r: null, verdict: 'READY_TO_AUDIT', meets_min_trades: true },
          { symbol: 'CCC', config_count: 1, trade_count: 15, profit_factor: 1.1, win_rate_pct: 45, reward_to_avgloss: 1.0, real_r: null, verdict: 'MARGINAL', meets_min_trades: true },
          { symbol: 'DDD', config_count: 1, trade_count: 18, profit_factor: 0.8, win_rate_pct: 35, reward_to_avgloss: 0.5, real_r: null, verdict: 'DROP', meets_min_trades: true },
          { symbol: 'EEE', config_count: 3, trade_count: 20, profit_factor: 1.5, win_rate_pct: 55, reward_to_avgloss: 1.5, real_r: null, verdict: 'MIXED', meets_min_trades: true },
        ],
      })
    )

    const Wrapper = makeWrapper()
    render(<TradeGateTable />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByText('KEEP_TESTING')
    expect(screen.getByText('READY_TO_AUDIT')).toBeTruthy()
    expect(screen.getByText('MARGINAL')).toBeTruthy()
    expect(screen.getByText('DROP')).toBeTruthy()
    expect(screen.getByText('MIXED')).toBeTruthy()
  })

  it('greys sub-threshold rows (meets_min_trades=false)', async () => {
    vi.mocked(api.tradeGate).mockResolvedValue(
      makeGateResponse({
        rows: [
          {
            symbol: 'LOWTRADEUSDT',
            config_count: 1,
            trade_count: 3,
            profit_factor: 1.5,
            win_rate_pct: 55,
            reward_to_avgloss: 1.0,
            real_r: null,
            verdict: 'KEEP_TESTING',
            meets_min_trades: false,
          },
        ],
      })
    )

    const Wrapper = makeWrapper()
    render(<TradeGateTable />, { wrapper: Wrapper })
    await act(async () => {})

    const row = await screen.findByTestId('gate-row-LOWTRADEUSDT')
    // Sub-threshold rows should have a grey/dim class
    expect(row.className).toMatch(/opacity|slate-5|text-slate-5/)
  })

  it('renders MIXED for a multi-config symbol', async () => {
    vi.mocked(api.tradeGate).mockResolvedValue(
      makeGateResponse({
        rows: [
          {
            symbol: 'AXSUSDT',
            config_count: 4,
            trade_count: 20,
            profit_factor: 1.5,
            win_rate_pct: 55,
            reward_to_avgloss: 1.5,
            real_r: null,
            verdict: 'MIXED',
            meets_min_trades: true,
          },
        ],
      })
    )

    const Wrapper = makeWrapper()
    render(<TradeGateTable />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByText('AXSUSDT')
    expect(screen.getByText('MIXED')).toBeTruthy()
  })

  it('renders config_count in the row for MIXED symbol (BUG 2 regression)', async () => {
    // BUG 2: config_count was never emitted by get_trade_gate() rows_out,
    // so the API always returned config_count=1. Frontend was masking this
    // because tests had config_count:4 in mock data but never asserted it
    // was actually rendered in the DOM.
    vi.mocked(api.tradeGate).mockResolvedValue(
      makeGateResponse({
        rows: [
          {
            symbol: 'AAVEUSDT',
            config_count: 3,
            trade_count: 20,
            profit_factor: 1.5,
            win_rate_pct: 55,
            reward_to_avgloss: 1.5,
            real_r: null,
            verdict: 'MIXED',
            meets_min_trades: true,
          },
        ],
      })
    )

    const Wrapper = makeWrapper()
    render(<TradeGateTable />, { wrapper: Wrapper })
    await act(async () => {})

    const row = await screen.findByTestId('gate-row-AAVEUSDT')
    // config_count must appear in the row DOM so users can see it is MIXED
    // because of 3 deployed configs (not because of PnL sign or any other reason).
    // A row showing config_count=1 when the server says 3 is the bug.
    expect(row.textContent).toMatch(/3 cfg|3 config|cfgs?: 3/i)
  })

  it('sort order: DROP → MARGINAL → KEEP_TESTING → READY_TO_AUDIT → MIXED', async () => {
    vi.mocked(api.tradeGate).mockResolvedValue(
      makeGateResponse({
        rows: [
          { symbol: 'MIX1', config_count: 2, trade_count: 20, profit_factor: 1.5, win_rate_pct: 55, reward_to_avgloss: 1.5, real_r: null, verdict: 'MIXED', meets_min_trades: true },
          { symbol: 'RTA1', config_count: 1, trade_count: 20, profit_factor: 2.5, win_rate_pct: 65, reward_to_avgloss: 2.5, real_r: null, verdict: 'READY_TO_AUDIT', meets_min_trades: true },
          { symbol: 'KT1', config_count: 1, trade_count: 20, profit_factor: 2.0, win_rate_pct: 60, reward_to_avgloss: 2.0, real_r: null, verdict: 'KEEP_TESTING', meets_min_trades: true },
          { symbol: 'DROP1', config_count: 1, trade_count: 18, profit_factor: 0.8, win_rate_pct: 35, reward_to_avgloss: 0.5, real_r: null, verdict: 'DROP', meets_min_trades: true },
          { symbol: 'MAR1', config_count: 1, trade_count: 15, profit_factor: 1.1, win_rate_pct: 45, reward_to_avgloss: 1.0, real_r: null, verdict: 'MARGINAL', meets_min_trades: true },
        ],
      })
    )

    const Wrapper = makeWrapper()
    render(<TradeGateTable />, { wrapper: Wrapper })
    await act(async () => {})

    const table = await screen.findByTestId('gate-table')
    const rows = within(table).getAllByTestId(/^gate-row-/)
    const symbols = rows.map(r => r.getAttribute('data-testid')?.replace('gate-row-', ''))

    // Expected order by verdict rank: DROP(0) → MARGINAL(1) → KEEP_TESTING(2) → READY_TO_AUDIT(3) → MIXED(4)
    expect(symbols[0]).toBe('DROP1')
    expect(symbols[1]).toBe('MAR1')
    expect(symbols[2]).toBe('KT1')
    expect(symbols[3]).toBe('RTA1')
    expect(symbols[4]).toBe('MIX1')
  })

  it('shows empty state when no rows', async () => {
    vi.mocked(api.tradeGate).mockResolvedValue(makeGateResponse({ rows: [] }))

    const Wrapper = makeWrapper()
    render(<TradeGateTable />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('gate-empty')
  })
})

// ============================================================================
// RiskAtStakeHeader
// ============================================================================

function makeRiskResponse(overrides: Partial<OpenRiskResponse> = {}): OpenRiskResponse {
  return {
    symbol: null,
    rows: [],
    unprotected_count: 0,
    ...overrides,
  }
}

describe('RiskAtStakeHeader', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders without crashing when no open positions', async () => {
    vi.mocked(api.openRisk).mockResolvedValue(makeRiskResponse())

    const Wrapper = makeWrapper()
    render(<RiskAtStakeHeader />, { wrapper: Wrapper })
    await act(async () => {})
    // should not throw
  })

  it('flags unprotected positions in red', async () => {
    vi.mocked(api.openRisk).mockResolvedValue(
      makeRiskResponse({
        unprotected_count: 2,
        rows: [
          { symbol: 'BTCUSDT', side: 'long', entry_price: 50000, stop_loss: null, position_size: 0.01, stop_distance_pct: null, unprotected: true },
          { symbol: 'ETHUSDT', side: 'long', entry_price: 3000, stop_loss: null, position_size: 0.1, stop_distance_pct: null, unprotected: true },
        ],
      })
    )

    const Wrapper = makeWrapper()
    render(<RiskAtStakeHeader />, { wrapper: Wrapper })
    await act(async () => {})

    const unprotEl = await screen.findByTestId('risk-unprotected')
    expect(unprotEl.className).toMatch(/red/)
  })

  it('does NOT show percentage gauge (no real balance available)', async () => {
    vi.mocked(api.openRisk).mockResolvedValue(
      makeRiskResponse({
        rows: [
          { symbol: 'BTCUSDT', side: 'long', entry_price: 50000, stop_loss: 49000, position_size: 0.01, stop_distance_pct: null, unprotected: false },
        ],
      })
    )

    const Wrapper = makeWrapper()
    render(<RiskAtStakeHeader />, { wrapper: Wrapper })
    await act(async () => {})

    // There must be no % gauge element (only absolute $ shown)
    expect(screen.queryByTestId('risk-pct-gauge')).toBeNull()
  })

  it('shows open count and notional info', async () => {
    vi.mocked(api.openRisk).mockResolvedValue(
      makeRiskResponse({
        rows: [
          { symbol: 'BTCUSDT', side: 'long', entry_price: 50000, stop_loss: 49000, position_size: 0.01, stop_distance_pct: null, unprotected: false },
          { symbol: 'ETHUSDT', side: 'short', entry_price: 3000, stop_loss: 3100, position_size: 0.1, stop_distance_pct: null, unprotected: false },
        ],
        unprotected_count: 0,
      })
    )

    const Wrapper = makeWrapper()
    render(<RiskAtStakeHeader />, { wrapper: Wrapper })
    await act(async () => {})

    // Should show "2 open" or similar
    const caption = await screen.findByTestId('risk-caption')
    expect(caption.textContent).toMatch(/2/)
  })
})

// ============================================================================
// MonthlyCalendar
// ============================================================================

describe('MonthlyCalendar', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders calendar grid for a populated month', async () => {
    vi.mocked(api.calendar).mockResolvedValue({
      symbol: null,
      year: 2026,
      month: 3,
      cells: [
        { date: '2026-03-01', pnl: 10.5, trade_count: 3 },
        { date: '2026-03-15', pnl: -5.0, trade_count: 2 },
        { date: '2026-03-31', pnl: 20.0, trade_count: 5 },
      ],
    } as CalendarResponse)

    const Wrapper = makeWrapper()
    render(<MonthlyCalendar />, { wrapper: Wrapper })
    await act(async () => {})

    // Calendar container should render
    await screen.findByTestId('monthly-calendar')
  })

  it('renders without crashing for empty month (no trades)', async () => {
    vi.mocked(api.calendar).mockResolvedValue({
      symbol: null,
      year: 2026,
      month: 1,
      cells: [],
    } as CalendarResponse)

    const Wrapper = makeWrapper()
    render(<MonthlyCalendar />, { wrapper: Wrapper })
    await act(async () => {})

    // Should not crash — calendar renders with empty cells
    await screen.findByTestId('monthly-calendar')
  })

  it('shows positive pnl day in green', async () => {
    vi.mocked(api.calendar).mockResolvedValue({
      symbol: null,
      year: 2026,
      month: 3,
      cells: [{ date: '2026-03-05', pnl: 15.0, trade_count: 2 }],
    } as CalendarResponse)

    const Wrapper = makeWrapper()
    render(<MonthlyCalendar initialYear={2026} initialMonth={3} />, { wrapper: Wrapper })
    await act(async () => {})

    const cell = await screen.findByTestId('cal-cell-2026-03-05')
    expect(cell.className).toMatch(/emerald|green/)
  })

  it('shows negative pnl day in red', async () => {
    vi.mocked(api.calendar).mockResolvedValue({
      symbol: null,
      year: 2026,
      month: 3,
      cells: [{ date: '2026-03-10', pnl: -8.0, trade_count: 1 }],
    } as CalendarResponse)

    const Wrapper = makeWrapper()
    render(<MonthlyCalendar initialYear={2026} initialMonth={3} />, { wrapper: Wrapper })
    await act(async () => {})

    const cell = await screen.findByTestId('cal-cell-2026-03-10')
    expect(cell.className).toMatch(/red/)
  })

  it('shows month selector controls', async () => {
    vi.mocked(api.calendar).mockResolvedValue({
      symbol: null,
      year: 2026,
      month: 3,
      cells: [],
    } as CalendarResponse)

    const Wrapper = makeWrapper()
    render(<MonthlyCalendar />, { wrapper: Wrapper })
    await act(async () => {})

    // Prev / Next buttons or selectors should be present
    await screen.findByTestId('cal-month-selector')
  })
})

// ============================================================================
// ExpectancyHeatmap
// ============================================================================

describe('ExpectancyHeatmap', () => {
  afterEach(() => vi.clearAllMocks())

  it('renders heatmap container', async () => {
    vi.mocked(api.heatmap).mockResolvedValue({
      symbol: null,
      bucket_hours: 4,
      cells: [],
    } as HeatmapResponse)

    const Wrapper = makeWrapper()
    render(<ExpectancyHeatmap />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('expectancy-heatmap')
  })

  it('shows diagnostic text label', async () => {
    vi.mocked(api.heatmap).mockResolvedValue({
      symbol: null,
      bucket_hours: 4,
      cells: [],
    } as HeatmapResponse)

    const Wrapper = makeWrapper()
    render(<ExpectancyHeatmap />, { wrapper: Wrapper })
    await act(async () => {})

    // Must show "Diagnostic" label per ticket spec
    await screen.findByTestId('heatmap-diagnostic-label')
  })

  it('MUST-FIX #4: cells with trade_count < 20 are grey (uncoloured)', async () => {
    vi.mocked(api.heatmap).mockResolvedValue({
      symbol: null,
      bucket_hours: 4,
      cells: [
        // Low-sample cell (< 20) — must be grey
        { hour: 0, dow: 1, pnl: 50.0, trade_count: 5, win_rate_pct: 60 },
        // High-sample cell (>= 20) — should be coloured
        { hour: 4, dow: 2, pnl: 80.0, trade_count: 25, win_rate_pct: 65 },
      ],
    } as HeatmapResponse)

    const Wrapper = makeWrapper()
    render(<ExpectancyHeatmap />, { wrapper: Wrapper })
    await act(async () => {})

    // Low-sample cell must be grey/slate (uncoloured)
    const lowCell = await screen.findByTestId('heatmap-cell-0-1')
    expect(lowCell.className).toMatch(/slate|gray/)
    // Should NOT have emerald/green/red colouring
    expect(lowCell.className).not.toMatch(/emerald/)
    expect(lowCell.className).not.toMatch(/\bred-[0-9]/)

    // High-sample cell should be coloured (emerald for positive pnl)
    const highCell = await screen.findByTestId('heatmap-cell-4-2')
    expect(highCell.className).toMatch(/emerald|green/)
  })

  it('shows trade count in each cell', async () => {
    vi.mocked(api.heatmap).mockResolvedValue({
      symbol: null,
      bucket_hours: 4,
      cells: [
        { hour: 8, dow: 3, pnl: 40.0, trade_count: 12, win_rate_pct: 55 },
      ],
    } as HeatmapResponse)

    const Wrapper = makeWrapper()
    render(<ExpectancyHeatmap />, { wrapper: Wrapper })
    await act(async () => {})

    const cell = await screen.findByTestId('heatmap-cell-8-3')
    expect(cell.textContent).toContain('12')
  })

  it('shows bucket-size selector', async () => {
    vi.mocked(api.heatmap).mockResolvedValue({
      symbol: null,
      bucket_hours: 4,
      cells: [],
    } as HeatmapResponse)

    const Wrapper = makeWrapper()
    render(<ExpectancyHeatmap />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('heatmap-bucket-selector')
  })
})
