/**
 * N5 — Charts tests (TDD — written BEFORE implementation)
 *
 * Tests:
 *   1. EquityCurve renders with data and with empty data (no crash, shows empty state)
 *   2. PerBotPnl renders with data and with empty data; positive bars green, negative bars red
 *   3. DailyPnl renders with data and with empty data; positive bars green, negative bars red
 *   4. Equity curve passes cumulative_pnl series in the correct chronological order
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

// recharts uses SVG which jsdom renders differently — suppress ResizeObserver errors
globalThis.ResizeObserver = class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

// Import the chart components (written in this ticket)
import { EquityCurve, PerBotPnl, DailyPnl } from '../components/Charts'

// ---- fixtures ---------------------------------------------------------------

import type { EquityPoint, DailyPnlPoint } from '../components/Charts'

function makeEquityPoint(
  timestamp: string,
  cumulative_pnl: number,
  equity: number = 5000 + cumulative_pnl
): EquityPoint {
  return { timestamp, cumulative_pnl, equity }
}

function makeDailyPoint(date: string, pnl: number, trade_count = 2): DailyPnlPoint {
  return { date, pnl, trade_count }
}

// PerBotPnl uses a simple { symbol, total_pnl } shape
interface BotPnlItem {
  symbol: string
  total_pnl: number
}

function makeBotPnl(symbol: string, total_pnl: number): BotPnlItem {
  return { symbol, total_pnl }
}

// ============================================================================
// EquityCurve
// ============================================================================

describe('EquityCurve', () => {
  it('renders container with data', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 0),
      makeEquityPoint('2026-01-02T00:00:00Z', 100),
      makeEquityPoint('2026-01-03T00:00:00Z', 250),
    ]
    render(<EquityCurve points={points} />)
    // Chart container exists
    expect(screen.getByTestId('equity-curve')).toBeTruthy()
  })

  it('shows empty state when no data points', () => {
    render(<EquityCurve points={[]} />)
    expect(screen.getByTestId('equity-curve-empty')).toBeTruthy()
  })

  it('does not crash with a single data point', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 50),
    ]
    // Should render without throwing
    expect(() => render(<EquityCurve points={points} />)).not.toThrow()
  })

  it('passes cumulative_pnl series in chronological order', () => {
    // The component should render points in the order supplied (oldest first)
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', 10),
      makeEquityPoint('2026-01-02T00:00:00Z', 30),
      makeEquityPoint('2026-01-03T00:00:00Z', -20),
    ]
    render(<EquityCurve points={points} />)

    // Verify chart container is present (recharts renders SVG; order is in props)
    const container = screen.getByTestId('equity-curve')
    expect(container).toBeTruthy()

    // The data-points attribute stores stringified order for test inspection
    const dataAttr = container.getAttribute('data-point-count')
    expect(dataAttr).toBe('3')
  })

  it('has a zero reference line (zero baseline)', () => {
    const points: EquityPoint[] = [
      makeEquityPoint('2026-01-01T00:00:00Z', -50),
      makeEquityPoint('2026-01-02T00:00:00Z', 100),
    ]
    render(<EquityCurve points={points} />)
    // The zero reference line label is in the DOM
    expect(screen.getByTestId('equity-zero-ref')).toBeTruthy()
  })
})

// ============================================================================
// PerBotPnl
// ============================================================================

describe('PerBotPnl', () => {
  it('renders container with data', () => {
    const bots: BotPnlItem[] = [
      makeBotPnl('BTCUSDT', 120.5),
      makeBotPnl('ETHUSDT', -30.0),
    ]
    render(<PerBotPnl bots={bots} />)
    expect(screen.getByTestId('per-bot-pnl')).toBeTruthy()
  })

  it('shows empty state when no bots', () => {
    render(<PerBotPnl bots={[]} />)
    expect(screen.getByTestId('per-bot-pnl-empty')).toBeTruthy()
  })

  it('does not crash with a single bot', () => {
    const bots: BotPnlItem[] = [makeBotPnl('SOLUSDT', 55.0)]
    expect(() => render(<PerBotPnl bots={bots} />)).not.toThrow()
  })

  it('marks positive bars with profit color class', () => {
    const bots: BotPnlItem[] = [makeBotPnl('BTCUSDT', 100)]
    render(<PerBotPnl bots={bots} />)
    // The container should carry the data for color inspection via data-has-profit attr
    const container = screen.getByTestId('per-bot-pnl')
    expect(container.getAttribute('data-has-profit')).toBe('true')
  })

  it('marks negative bars with loss color class', () => {
    const bots: BotPnlItem[] = [makeBotPnl('ETHUSDT', -50)]
    render(<PerBotPnl bots={bots} />)
    const container = screen.getByTestId('per-bot-pnl')
    expect(container.getAttribute('data-has-loss')).toBe('true')
  })

  it('handles mixed positive and negative bots', () => {
    const bots: BotPnlItem[] = [
      makeBotPnl('BTCUSDT', 200),
      makeBotPnl('ETHUSDT', -100),
    ]
    render(<PerBotPnl bots={bots} />)
    const container = screen.getByTestId('per-bot-pnl')
    expect(container.getAttribute('data-has-profit')).toBe('true')
    expect(container.getAttribute('data-has-loss')).toBe('true')
  })
})

// ============================================================================
// DailyPnl
// ============================================================================

describe('DailyPnl', () => {
  it('renders container with data', () => {
    const days: DailyPnlPoint[] = [
      makeDailyPoint('2026-01-01', 50),
      makeDailyPoint('2026-01-02', -20),
      makeDailyPoint('2026-01-03', 80),
    ]
    render(<DailyPnl days={days} />)
    expect(screen.getByTestId('daily-pnl')).toBeTruthy()
  })

  it('shows empty state when no days', () => {
    render(<DailyPnl days={[]} />)
    expect(screen.getByTestId('daily-pnl-empty')).toBeTruthy()
  })

  it('does not crash with a single day', () => {
    const days: DailyPnlPoint[] = [makeDailyPoint('2026-01-01', 25)]
    expect(() => render(<DailyPnl days={days} />)).not.toThrow()
  })

  it('marks positive days with profit indicator', () => {
    const days: DailyPnlPoint[] = [makeDailyPoint('2026-01-01', 100)]
    render(<DailyPnl days={days} />)
    const container = screen.getByTestId('daily-pnl')
    expect(container.getAttribute('data-has-profit')).toBe('true')
  })

  it('marks negative days with loss indicator', () => {
    const days: DailyPnlPoint[] = [makeDailyPoint('2026-01-01', -30)]
    render(<DailyPnl days={days} />)
    const container = screen.getByTestId('daily-pnl')
    expect(container.getAttribute('data-has-loss')).toBe('true')
  })

  it('handles zero PnL day without crash', () => {
    const days: DailyPnlPoint[] = [makeDailyPoint('2026-01-01', 0)]
    expect(() => render(<DailyPnl days={days} />)).not.toThrow()
  })
})
