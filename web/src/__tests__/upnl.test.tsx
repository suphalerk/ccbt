/**
 * Tests for the realtime uPnL feature:
 *  - UpnlPanel renders correctly for null / empty / live / stale / offline states
 *  - useLiveSnapshot correctly parses {type:'upnl'} WS messages and exposes upnl state
 */
import { render, screen, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { UpnlPanel } from '../components/UpnlPanel'
import type { WSUpnlData, PositionMark } from '../ws-types'

// ---------------------------------------------------------------------------
// UpnlPanel component tests
// ---------------------------------------------------------------------------

describe('UpnlPanel', () => {
  it('renders loading state when upnl is null', () => {
    render(<UpnlPanel upnl={null} />)
    expect(screen.getByTestId('upnl-panel-loading')).toBeTruthy()
    expect(screen.queryByTestId('upnl-panel')).toBeNull()
  })

  it('renders panel when upnl data is provided', () => {
    const upnl: WSUpnlData = {
      positions: [], total_upnl: 0, feed_status: 'live', today_realized: 0, net_today: 0,
    }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.getByTestId('upnl-panel')).toBeTruthy()
    expect(screen.queryByTestId('upnl-panel-loading')).toBeNull()
  })

  it('displays total_upnl with correct sign prefix', () => {
    const upnl: WSUpnlData = {
      positions: [], total_upnl: 123.45, feed_status: 'live', today_realized: 0, net_today: 123.45,
    }
    render(<UpnlPanel upnl={upnl} />)
    const el = screen.getByTestId('upnl-total')
    expect(el.textContent).toMatch(/\+123\.45/)
  })

  it('displays negative total_upnl without extra plus sign', () => {
    const upnl: WSUpnlData = {
      positions: [], total_upnl: -55.0, feed_status: 'live', today_realized: 0, net_today: -55.0,
    }
    render(<UpnlPanel upnl={upnl} />)
    const el = screen.getByTestId('upnl-total')
    expect(el.textContent).toMatch(/-55\.00/)
    expect(el.textContent).not.toMatch(/\+/)
  })

  it('renders per-position rows for each open position', () => {
    const positions: PositionMark[] = [
      { symbol: 'BTCUSDT', side: 'long', entry_price: 30000, size: 0.5, stop_loss: null, take_profit: null, mark_price: 31000, upnl: 500, ts: null, dist_to_stop_pct: null, rr_remaining: null },
      { symbol: 'ETHUSDT', side: 'short', entry_price: 2000, size: 1.0, stop_loss: null, take_profit: null, mark_price: 1800, upnl: 200, ts: null, dist_to_stop_pct: null, rr_remaining: null },
    ]
    const upnl: WSUpnlData = {
      positions, total_upnl: 700, feed_status: 'live', today_realized: 0, net_today: 700,
    }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.getByTestId('upnl-row-BTCUSDT')).toBeTruthy()
    expect(screen.getByTestId('upnl-row-ETHUSDT')).toBeTruthy()
  })

  it('shows "No open positions" when positions array is empty', () => {
    const upnl: WSUpnlData = {
      positions: [], total_upnl: 0, feed_status: 'live', today_realized: 0, net_today: 0,
    }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.getByText(/No open positions/i)).toBeTruthy()
  })

  it('shows "Mark live" indicator when feed_status=live', () => {
    const upnl: WSUpnlData = { positions: [], total_upnl: 0, feed_status: 'live', today_realized: 0, net_today: 0 }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.getByText(/Mark live/i)).toBeTruthy()
  })

  it('shows "Mark stale" indicator when feed_status=stale', () => {
    const upnl: WSUpnlData = { positions: [], total_upnl: 0, feed_status: 'stale', today_realized: 0, net_today: 0 }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.getByText(/Mark stale/i)).toBeTruthy()
  })

  it('shows "Mark offline" indicator when feed_status=offline', () => {
    const upnl: WSUpnlData = { positions: [], total_upnl: 0, feed_status: 'offline', today_realized: 0, net_today: 0 }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.getByText(/Mark offline/i)).toBeTruthy()
  })

  it('shows "last known" note when feed is not live', () => {
    const upnl: WSUpnlData = { positions: [], total_upnl: 0, feed_status: 'offline', today_realized: 0, net_today: 0 }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.getByText(/last known/i)).toBeTruthy()
  })

  it('does NOT show "last known" note when feed is live', () => {
    const upnl: WSUpnlData = { positions: [], total_upnl: 0, feed_status: 'live', today_realized: 0, net_today: 0 }
    render(<UpnlPanel upnl={upnl} />)
    expect(screen.queryByText(/last known/i)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// useLiveSnapshot — upnl message handling
// ---------------------------------------------------------------------------

import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useLiveSnapshot } from '../hooks/useLiveSnapshot'

// Mock WebSocket
class MockWebSocket {
  static instances: MockWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((evt: { data: string }) => void) | null = null
  onclose: ((evt: { code: number }) => void) | null = null
  onerror: (() => void) | null = null
  readyState = 0 // CONNECTING
  url: string

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
  }

  send(_data: string) {}
  close(_code?: number) {
    this.onclose?.({ code: 1000 })
  }

  // Test helper: simulate server sending a message
  _receive(data: string) {
    this.onmessage?.({ data })
  }

  // Test helper: simulate successful connection
  _connect() {
    this.readyState = 1 // OPEN
    this.onopen?.()
  }
}

describe('useLiveSnapshot — upnl message handling', () => {
  let origWebSocket: typeof WebSocket
  let queryClient: QueryClient

  beforeEach(() => {
    MockWebSocket.instances = []
    origWebSocket = globalThis.WebSocket
    // @ts-expect-error — partial mock
    globalThis.WebSocket = MockWebSocket
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
  })

  afterEach(() => {
    globalThis.WebSocket = origWebSocket
  })

  function wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children)
  }

  it('upnl is null before any upnl message received', () => {
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })
    expect(result.current.upnl).toBeNull()
  })

  it('upnl is populated after receiving a {type:upnl} WS message', async () => {
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })

    // Get the mock WS instance
    const ws = MockWebSocket.instances[0]
    expect(ws).toBeTruthy()

    // Simulate WS connect + upnl message
    act(() => {
      ws._connect()
    })

    const upnlMsg = {
      type: 'upnl',
      ts: '2026-06-08T00:00:00Z',
      data: {
        positions: [
          { symbol: 'BTCUSDT', side: 'long', entry_price: 30000, size: 0.5, mark_price: 31000, upnl: 500, ts: null },
        ],
        total_upnl: 500,
        feed_status: 'live',
      },
    }

    act(() => {
      ws._receive(JSON.stringify(upnlMsg))
    })

    expect(result.current.upnl).not.toBeNull()
    expect(result.current.upnl?.total_upnl).toBe(500)
    expect(result.current.upnl?.feed_status).toBe('live')
    expect(result.current.upnl?.positions).toHaveLength(1)
    expect(result.current.upnl?.positions[0].symbol).toBe('BTCUSDT')
  })

  it('upnl reflects feed_status=offline from server', async () => {
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })
    const ws = MockWebSocket.instances[0]

    act(() => { ws._connect() })

    const offlineMsg = {
      type: 'upnl',
      ts: '2026-06-08T00:00:00Z',
      data: {
        positions: [],
        total_upnl: 0,
        feed_status: 'offline',
      },
    }

    act(() => {
      ws._receive(JSON.stringify(offlineMsg))
    })

    expect(result.current.upnl?.feed_status).toBe('offline')
  })

  it('upnl update does NOT affect snapshot state', async () => {
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })
    const ws = MockWebSocket.instances[0]

    act(() => { ws._connect() })

    // Send a snapshot first
    const snapshotMsg = {
      type: 'snapshot',
      ts: '2026-06-08T00:00:00Z',
      data: { portfolio: { total_pnl: 100, total_trades: 5, closed_trades: 5, win_rate_pct: 60, profit_factor: 1.5, best_bot: null, worst_bot: null, active_bots: 1 }, bots: [] },
    }
    act(() => { ws._receive(JSON.stringify(snapshotMsg)) })
    const snapshotBefore = result.current.snapshot

    // Send upnl
    act(() => {
      ws._receive(JSON.stringify({
        type: 'upnl',
        ts: '2026-06-08T00:00:01Z',
        data: { positions: [], total_upnl: 99, feed_status: 'live' },
      }))
    })

    // snapshot must not change
    expect(result.current.snapshot).toBe(snapshotBefore)
    // upnl must update
    expect(result.current.upnl?.total_upnl).toBe(99)
  })
})
