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
import type { WSEnvelope, WSSnapshotPayload, BotRow, PortfolioSummary } from '../ws-types'

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
  if (typeof window === 'undefined') return 'ws://localhost:8502/ws'
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}/ws`
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
        setSnapshot(snap)
        setLastUpdated(snap.timestamp ?? null)

        // Hydrate TanStack Query caches
        if (snap.portfolio) {
          queryClient.setQueryData<PortfolioSummary>(QUERY_KEYS.portfolio, snap.portfolio)
        }
        if (snap.bots) {
          queryClient.setQueryData<BotRow[]>(QUERY_KEYS.bots, snap.bots)
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
