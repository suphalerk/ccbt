/**
 * N7 — Live Log Viewer page.
 *
 * Features:
 *   - REST fetch from /api/logs with level + search filters
 *   - WS push of new log lines via the /ws/logs channel (via onWsLine callback)
 *   - Level filter selector (ALL / INFO / WARNING / ERROR / CRITICAL)
 *   - Search input (debounced, server-side filtering)
 *   - Monospace colored log lines (INFO=slate, WARNING=yellow, ERROR=red, CRITICAL=red+bold)
 *   - Level count row showing INFO/WARNING/ERROR counts
 *   - Newest-first ordering (matches Streamlit)
 *   - Capped at 500 rendered lines for performance
 *
 * Rules:
 *   - Level/search filtering happens server-side (reuses get_recent_logs args).
 *   - TS only renders; no filtering logic here.
 *   - WS push prepends new lines, and the 500-line cap is enforced after each push.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

// ============================================================================
// Types
// ============================================================================

export interface LogLineData {
  level: string | null
  message: string
  timestamp: string | null
  raw: string
}

// onWsLine registers a callback that the parent (or WS hook) can call to push new lines
export interface LogViewerPageProps {
  onWsLine?: (register: (line: LogLineData) => void) => void
}

// ============================================================================
// Constants
// ============================================================================

const MAX_LINES = 500

const LEVELS = ['ALL', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const
type Level = (typeof LEVELS)[number]

// ============================================================================
// Level color palette
// ============================================================================

function levelClass(level: string | null): string {
  switch ((level ?? '').toUpperCase()) {
    case 'INFO':     return 'text-slate-300'
    case 'WARNING':  return 'text-yellow-400'
    case 'ERROR':    return 'text-red-400'
    case 'CRITICAL': return 'text-red-300 font-bold'
    case 'DEBUG':    return 'text-slate-500'
    default:         return 'text-slate-400'
  }
}

function levelBadgeClass(level: string | null): string {
  switch ((level ?? '').toUpperCase()) {
    case 'INFO':     return 'bg-blue-900/40 text-blue-300 border border-blue-700/40'
    case 'WARNING':  return 'bg-yellow-900/40 text-yellow-300 border border-yellow-700/40'
    case 'ERROR':    return 'bg-red-900/40 text-red-300 border border-red-700/40'
    case 'CRITICAL': return 'bg-red-900/60 text-red-200 border border-red-600/60 font-bold'
    case 'DEBUG':    return 'bg-slate-800/40 text-slate-500 border border-slate-700/40'
    default:         return 'bg-slate-800/40 text-slate-500 border border-slate-700/40'
  }
}

// ============================================================================
// Single log line component
// ============================================================================

function LogLine({ line }: { line: LogLineData }) {
  const ts = line.timestamp
    ? new Date(line.timestamp).toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : null

  const level = line.level?.toUpperCase() ?? ''

  return (
    <div
      data-testid="log-line"
      className={`flex items-start gap-2 px-3 py-1 font-mono text-xs border-b border-[#1A1F2E] ${levelClass(level)}`}
    >
      {ts && (
        <span className="text-slate-600 shrink-0 tabular-nums" style={{ minWidth: 76 }}>
          {ts}
        </span>
      )}
      <span className={`shrink-0 px-1 rounded text-[10px] uppercase ${levelBadgeClass(level)}`} style={{ minWidth: 52, textAlign: 'center' }}>
        {level || '—'}
      </span>
      <span className="break-all">{line.message}</span>
    </div>
  )
}

// ============================================================================
// Level counts summary row
// ============================================================================

function LevelCounts({ lines }: { lines: LogLineData[] }) {
  const counts: Record<string, number> = {}
  for (const ln of lines) {
    const lvl = (ln.level ?? 'UNKNOWN').toUpperCase()
    counts[lvl] = (counts[lvl] ?? 0) + 1
  }

  const items = [
    { key: 'INFO',     label: 'INFO',     cls: 'text-blue-400' },
    { key: 'WARNING',  label: 'WARN',     cls: 'text-yellow-400' },
    { key: 'ERROR',    label: 'ERROR',    cls: 'text-red-400' },
    { key: 'CRITICAL', label: 'CRIT',     cls: 'text-red-300 font-bold' },
  ]

  return (
    <div data-testid="log-level-counts" className="flex gap-4 text-xs text-slate-500 mb-2">
      {items.map(({ key, label, cls }) => (
        <span key={key} className={`font-mono ${cls}`}>
          {label}: {counts[key] ?? 0}
        </span>
      ))}
      <span className="ml-auto text-slate-600">{lines.length} lines</span>
    </div>
  )
}

// ============================================================================
// LogViewerPage
// ============================================================================

export function LogViewerPage({ onWsLine }: LogViewerPageProps = {}) {
  const [level, setLevel] = useState<Level>('ALL')
  const [search, setSearch] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')

  // Extra lines pushed via WS
  const [wsLines, setWsLines] = useState<LogLineData[]>([])

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 400)
    return () => clearTimeout(t)
  }, [search])

  // Reset WS lines when filter changes (stale WS lines may not match new filter)
  useEffect(() => {
    setWsLines([])
  }, [level, debouncedSearch])

  // REST fetch
  const { data } = useQuery({
    queryKey: ['logs', level, debouncedSearch],
    queryFn: () =>
      api.logs({
        level: level === 'ALL' ? null : level,
        search: debouncedSearch || null,
        limit: MAX_LINES,
      }),
    staleTime: 10_000,
    refetchInterval: 30_000,
  })

  const restLines: LogLineData[] = (data?.lines ?? []).map(ln => ({
    level: ln.level ?? null,
    message: ln.message,
    timestamp: ln.timestamp ?? null,
    raw: ln.raw,
  }))

  // Merge WS + REST, cap at MAX_LINES, newest first
  const allLines = [...wsLines, ...restLines].slice(0, MAX_LINES)

  // Register onWsLine callback so parent/WS integration can push new lines
  const appendLine = useCallback((line: LogLineData) => {
    setWsLines(prev => [line, ...prev].slice(0, MAX_LINES))
  }, [])

  // Call onWsLine with our appendLine function once on mount
  const registeredRef = useRef(false)
  useEffect(() => {
    if (!registeredRef.current && onWsLine) {
      registeredRef.current = true
      onWsLine(appendLine)
    }
  }, [onWsLine, appendLine])

  const hasLines = allLines.length > 0

  return (
    <div className="p-4 max-w-screen-xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-100 mb-4">Live Log</h1>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-3 mb-4 items-center">
        <select
          data-testid="log-level-filter"
          value={level}
          onChange={e => setLevel(e.target.value as Level)}
          className="bg-[#1A1F2E] border border-[#2A3245] rounded px-3 py-1.5 text-sm text-slate-300 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {LEVELS.map(l => (
            <option key={l} value={l}>{l}</option>
          ))}
        </select>

        <input
          data-testid="log-search-input"
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search logs…"
          className="bg-[#1A1F2E] border border-[#2A3245] rounded px-3 py-1.5 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 flex-1 min-w-[180px]"
        />
      </div>

      {/* Log viewer container */}
      <div
        data-testid="log-viewer"
        className="bg-[#0D1117] rounded-lg border border-[#1E2530] overflow-hidden"
      >
        {hasLines && <LevelCounts lines={allLines} />}

        {!hasLines ? (
          <div
            data-testid="log-empty"
            className="flex items-center justify-center py-12 text-sm text-slate-500 italic"
          >
            No log entries match the current filter.
          </div>
        ) : (
          <div className="overflow-y-auto" style={{ maxHeight: 600 }}>
            {allLines.map((line, idx) => (
              <LogLine key={idx} line={line} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
