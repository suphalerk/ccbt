/**
 * N3 — useLiveSnapshot hook.
 *
 * Connects to /ws (low-frequency snapshot WebSocket). On every snapshot message,
 * hydrates the TanStack Query cache so components read from useQuery as normal.
 *
 * Features:
 * - Auto-connect on mount, close on unmount
 * - Exponential-backoff reconnect (base 1s, max 30s) after any non-clean close
 * - Heartbeat messages are silently consumed (keep-alive, no cache update)
 * - Falls back to REST polling if WS drops (via TanStack Query's own staleTime/refetch)
 * - WS message types come from ws-types.ts (Pydantic-derived) — hydration is type-checked
 */
import { useEffect, useRef, useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import type { WSEnvelope, WSSnapshotPayload, BotRow, WSSnapshotData } from '../ws-types'
import type { BotListResponse, PortfolioSummaryResponse } from '../api/client'

export type WsStatus = 'connecting' | 'connected' | 'reconnecting' | 'closed'

export interface LiveSnapshotState {
  status: WsStatus
  snapshot: WSSnapshotPayload | null
  lastUpdated: string | null
}

// Query keys used by the REST endpoints — we update these caches from WS
export const QUERY_KEYS = {
  portfolio: ['portfolio', 'summary'] as const,
  bots: ['bots'] as const,
} as const

const BASE_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000
const CLEAN_CLOSE_CODE = 1000

function buildWsUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost:8501/ws'
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  const base = `${protocol}//${host}/ws`
  // Append ?token= when the dashboard token is injected at build-time or via
  // the index.html bootstrap script (window.__CCBT_TOKEN__).
  // Without this the backend rejects the WS with code 4403 when CCBT_DASH_TOKEN
  // is set — which is mandatory for any non-localhost deployment.
  const token = (typeof window !== 'undefined' && (window as unknown as Record<string, unknown>).__CCBT_TOKEN__)
    ? String((window as unknown as Record<string, unknown>).__CCBT_TOKEN__)
    : undefined
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}

export function useLiveSnapshot(): LiveSnapshotState {
  const queryClient = useQueryClient()
  const wsRef = useRef<WebSocket | null>(null)
  const retryCountRef = useRef(0)
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const unmountedRef = useRef(false)

  const [status, setStatus] = useState<WsStatus>('connecting')
  const [snapshot, setSnapshot] = useState<WSSnapshotPayload | null>(null)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)

  const connect = useCallback(() => {
    if (unmountedRef.current) return

    setStatus(retryCountRef.current > 0 ? 'reconnecting' : 'connecting')

    const url = buildWsUrl()
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      if (unmountedRef.current) { ws.close(); return }
      retryCountRef.current = 0
      setStatus('connected')
    }

    ws.onmessage = (evt) => {
      if (unmountedRef.current) return
      let envelope: WSEnvelope
      try {
        envelope = JSON.parse(evt.data as string) as WSEnvelope
      } catch {
        return
      }

      if (envelope.type === 'heartbeat') {
        // keep-alive — no cache update needed
        return
      }

      if (envelope.type === 'snapshot') {
        const snap = envelope as WSSnapshotPayload

        // api/ws.py sends nested shape: {type, ts, data:{portfolio, bots}}
        // Fall back to legacy flat shape for backward compat.
        const data: WSSnapshotData | null | undefined = snap.data
        const portfolio = data?.portfolio ?? snap.portfolio ?? null
        const bots = data?.bots ?? snap.bots ?? null
        // Timestamp: prefer "ts" (py field), fall back to legacy "timestamp"
        const ts = snap.ts ?? snap.timestamp ?? null

        setSnapshot(snap)
        setLastUpdated(ts)

        // Hydrate TanStack Query caches
        if (portfolio) {
          queryClient.setQueryData<PortfolioSummaryResponse>(QUERY_KEYS.portfolio, portfolio as PortfolioSummaryResponse)
        }
        // Wrap bots array in BotListResponse shape { bots: [...] } so the cache
        // entry matches what api.listBots() returns and consumers reading
        // data?.bots always get the array (not undefined).  The WS snapshot now
        // returns the full BotRow shape (see api/ws.py _build_snapshot), so no
        // data is lost by this update.
        if (bots) {
          queryClient.setQueryData<BotListResponse>(QUERY_KEYS.bots, { bots: bots as BotRow[] })
        }
      }
    }

    ws.onclose = (evt) => {
      wsRef.current = null
      if (unmountedRef.current) return
      if (evt.code === CLEAN_CLOSE_CODE) {
        setStatus('closed')
        return
      }
      // Abnormal close — schedule reconnect with exponential backoff
      setStatus('reconnecting')
      const delay = Math.min(BASE_BACKOFF_MS * Math.pow(2, retryCountRef.current), MAX_BACKOFF_MS)
      retryCountRef.current += 1
      retryTimerRef.current = setTimeout(() => {
        if (!unmountedRef.current) connect()
      }, delay)
    }

    ws.onerror = () => {
      // onclose fires after onerror — handled there
    }
  }, [queryClient])

  useEffect(() => {
    unmountedRef.current = false
    connect()
    return () => {
      unmountedRef.current = true
      if (retryTimerRef.current !== null) clearTimeout(retryTimerRef.current)
      if (wsRef.current) {
        wsRef.current.onclose = null  // prevent reconnect on unmount close
        wsRef.current.close(CLEAN_CLOSE_CODE)
      }
    }
  }, [connect])

  return { status, snapshot, lastUpdated }
}
