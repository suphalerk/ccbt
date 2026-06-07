/**
 * N3: useLiveSnapshot hook tests (TDD — written BEFORE implementation)
 * Tests: connects, applies WS message to cache, reconnects after drop.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import { useLiveSnapshot } from '../useLiveSnapshot'
import type { WSSnapshotPayload } from '../../ws-types'

// ---- WebSocket class mock --------------------------------------------------
// Must be a real class so `new WebSocket()` works.

type WsCloseEvt = { code: number }
type WsMsgEvt = { data: string }

export class MockWebSocket {
  url: string
  readyState: number = 0 // CONNECTING
  onopen: (() => void) | null = null
  onmessage: ((evt: WsMsgEvt) => void) | null = null
  onclose: ((evt: WsCloseEvt) => void) | null = null
  onerror: ((err: unknown) => void) | null = null

  static instances: MockWebSocket[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    // Simulate async open on the next timer tick
    setTimeout(() => {
      if (this.readyState !== 3) {
        this.readyState = 1 // OPEN
        this.onopen?.()
      }
    }, 0)
  }

  close(code = 1000) {
    this.readyState = 3 // CLOSED
    this.onclose?.({ code })
  }

  /** Test helper: simulate receiving a JSON message */
  _emit(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) })
  }

  /** Test helper: simulate an abnormal disconnect */
  _drop(code = 1006) {
    this.readyState = 3
    const handler = this.onclose
    this.onclose = null // prevent re-entrant close
    handler?.({ code })
  }
}

// ---- test wrapper ----------------------------------------------------------
function makeWrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        {children}
      </QueryClientProvider>
    )
  }
}

describe('useLiveSnapshot', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    MockWebSocket.instances = []
    vi.stubGlobal('WebSocket', MockWebSocket)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.clearAllMocks()
  })

  it('connects to the WS endpoint', async () => {
    const wrapper = makeWrapper()
    renderHook(() => useLiveSnapshot(), { wrapper })

    // flush the open timeout
    await act(async () => { vi.runAllTimers() })

    expect(MockWebSocket.instances).toHaveLength(1)
    expect(MockWebSocket.instances[0].url).toMatch(/\/ws/)
    expect(MockWebSocket.instances[0].readyState).toBe(1)
  })

  it('applies an incoming snapshot message and exposes status=connected', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })

    await act(async () => { vi.runAllTimers() })

    const msg: WSSnapshotPayload = {
      type: 'snapshot',
      portfolio: {
        total_trades: 10,
        closed_trades: 8,
        win_rate_pct: 62.5,
        profit_factor: 1.8,
        total_pnl: 150.0,
        best_bot: 'BTCUSDT',
        worst_bot: 'ETHUSDT',
        active_bots: 2,
      },
      bots: [],
      timestamp: '2026-01-01T00:00:00Z',
    }

    act(() => {
      MockWebSocket.instances[0]._emit(msg)
    })

    // status is already 'connected' after onopen (timers were flushed above)
    expect(result.current.status).toBe('connected')
    // The snapshot data should be accessible via the hook
    expect(result.current.snapshot?.portfolio?.total_trades).toBe(10)
  })

  it('reconnects after a WebSocket drop with exponential backoff', async () => {
    const wrapper = makeWrapper()
    renderHook(() => useLiveSnapshot(), { wrapper })

    await act(async () => { vi.runAllTimers() })

    expect(MockWebSocket.instances).toHaveLength(1)

    // Drop the connection (abnormal close)
    act(() => { MockWebSocket.instances[0]._drop(1006) })

    // First reconnect attempt fires after base_backoff_ms = 1000ms
    await act(async () => { vi.advanceTimersByTime(2000) })

    // Second WS should have been created
    expect(MockWebSocket.instances.length).toBeGreaterThanOrEqual(2)
  })

  it('marks status as connecting initially', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })

    // Before the open event fires (fake timers not flushed)
    expect(result.current.status).toBe('connecting')
  })

  it('marks status as reconnecting after drop', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })

    await act(async () => { vi.runAllTimers() })

    expect(result.current.status).toBe('connected')

    act(() => { MockWebSocket.instances[0]._drop(1006) })

    expect(result.current.status).toBe('reconnecting')
  })

  it('ignores heartbeat messages (status stays connected)', async () => {
    const wrapper = makeWrapper()
    const { result } = renderHook(() => useLiveSnapshot(), { wrapper })
    await act(async () => { vi.runAllTimers() })

    // status is already 'connected' after onopen (timers were flushed above)
    expect(result.current.status).toBe('connected')

    // Now send a heartbeat
    act(() => {
      MockWebSocket.instances[0]._emit({ type: 'heartbeat', timestamp: '2026-01-01T00:00:01Z' })
    })

    expect(result.current.status).toBe('connected')
  })
})
