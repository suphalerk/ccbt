/**
 * N4 — Portfolio page.
 *
 * Sections:
 *   1. Header metrics (Total PnL, WR%, PF, Active Bots)
 *   2. Bot status grid (grouped by strategy, mode badges)
 *   3. Bot Overview table (sortable)
 *   4. Recent Trades table (all bots, paginated)
 *
 * All data comes from TanStack Query; WS hydration in useLiveSnapshot keeps
 * the cache fresh without page reloads.
 *
 * Rules:
 *   - No financial math here — values are pre-formatted by Python backend.
 *   - Empty states mirror Streamlit's st.info() messages.
 *   - signal_source column is dropped (absent from schema).
 *   - Bot grid pills use aria-label for the symbol (no DOM text) so that
 *     `findByText(symbol)` finds exactly one match (in the overview table).
 *   - Overview table uses plain <span> for symbol text (not a link with symbol
 *     text) so that the table's symbol is the only DOM text match globally.
 *   - Overview table omits strategy column (appears only as grid group headings).
 *   - Mode column in table uses raw mode string, not the ModeBadge component,
 *     to avoid duplicate 'STOP'/'TP'/'PANIC' text matches.
 */
import { useState, useMemo, useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { BotRow, TradeRow, PortfolioSummaryResponse, ModeResponse } from '../api/client'
import { formatMoney, formatPct, formatPF } from '../utils/format'
import { EquityCurve, PerBotPnl, DailyPnl, UnderwaterChart } from '../components/Charts'
import { UpnlPanel } from '../components/UpnlPanel'
import type { WSUpnlData } from '../ws-types'

// ============================================================================
// Header metric card
// ============================================================================

interface MetricCardProps {
  label: string
  value: string
  testId?: string
  valueClass?: string
}

function MetricCard({ label, value, testId, valueClass = 'text-slate-100' }: MetricCardProps) {
  return (
    <div className="bg-[#1A1F2E] rounded-lg border border-[#2A3245] px-4 py-3 flex flex-col gap-1">
      <span className="text-xs font-medium uppercase tracking-wider text-slate-500">{label}</span>
      <span
        className={`text-xl font-semibold tabular-nums ${valueClass}`}
        data-testid={testId}
      >
        {value}
      </span>
    </div>
  )
}

// ============================================================================
// Header section
// ============================================================================

interface PortfolioHeaderProps {
  summary: PortfolioSummaryResponse | undefined
}

function PortfolioHeader({ summary }: PortfolioHeaderProps) {
  const totalPnl = summary?.total_pnl ?? null
  const pnlClass =
    totalPnl === null ? 'text-slate-100' : totalPnl >= 0 ? 'text-emerald-400' : 'text-red-400'

  // Today's PnL: Bangkok-day (GMT+7) realized PnL, computed server-side in
  // portfolio/summary (no math in TS). 0 when no closed trades today — never a
  // stale prior day (the old "last daily-pnl point" was UTC-grouped + could show
  // yesterday when today had no trades).
  const todayPnl = summary?.today_pnl ?? null
  const todayPnlClass =
    todayPnl === null ? 'text-slate-100' : todayPnl >= 0 ? 'text-emerald-400' : 'text-red-400'

  // Open / Notional: active_bots count + notional USDT (Python-computed in portfolio/summary)
  const activeBots = summary?.active_bots ?? null
  const notional = summary?.notional ?? null
  const notionalLabel =
    activeBots !== null && notional !== null
      ? `${activeBots} / ${formatMoney(notional)}`
      : activeBots !== null
      ? String(activeBots)
      : '—'

  return (
    <div data-testid="portfolio-header" className="mb-6">
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-3">
        <MetricCard
          label="Total PnL"
          value={formatMoney(totalPnl)}
          testId="header-total-pnl"
          valueClass={pnlClass}
        />
        <MetricCard
          label="Win Rate"
          value={formatPct(summary?.win_rate_pct ?? null)}
          testId="header-win-rate"
        />
        <MetricCard
          label="Profit Factor"
          value={formatPF(summary?.profit_factor ?? null)}
          testId="header-profit-factor"
        />
        <MetricCard
          label="Active Bots"
          value={activeBots !== null ? String(activeBots) : '—'}
          testId="header-active-bots"
        />
        {/* #5 — Today's PnL */}
        <MetricCard
          label="Today's PnL"
          value={formatMoney(todayPnl)}
          testId="header-today-pnl"
          valueClass={todayPnlClass}
        />
        {/* #5 — Open / Notional */}
        <MetricCard
          label="Open / Notional"
          value={notionalLabel}
          testId="header-open-notional"
        />
      </div>
      {/* Bulk mode controls (#2) */}
      <BulkModeControls />
    </div>
  )
}

// ============================================================================
// Mode badge — used ONLY in the bot grid pills.
// The overview table uses raw mode strings to avoid duplicate text nodes.
//
// Normalise incoming mode to UPPERCASE before lookup so the badge renders
// correctly whether the backend sends 'PANIC' or 'panic' (DB stores lowercase).
// ============================================================================

const MODE_BADGE_MAP: Record<string, { label: string; className: string }> = {
  GRACEFUL_STOP: {
    label: 'STOP',
    className: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/40',
  },
  TP_ONLY: {
    label: 'TP',
    className: 'bg-blue-500/20 text-blue-300 border border-blue-500/40',
  },
  PANIC: {
    label: 'PANIC',
    className: 'bg-red-500/20 text-red-300 border border-red-500/40 font-bold',
  },
}

function ModeBadge({ mode }: { mode: string | null }) {
  const m = (mode ?? '').toUpperCase()
  if (!m || m === 'NORMAL' || !MODE_BADGE_MAP[m]) return null
  const cfg = MODE_BADGE_MAP[m]
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded ${cfg.className}`}>{cfg.label}</span>
  )
}

// ============================================================================
// Bulk panic confirm dialog (#2)
// Accessible: Escape to cancel, focus trapped inside, aria-labelledby.
// ============================================================================

interface BulkPanicConfirmDialogProps {
  onConfirm: () => void
  onCancel: () => void
}

function BulkPanicConfirmDialog({ onConfirm, onCancel }: BulkPanicConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const titleId = 'bulk-panic-dialog-title'
  const descId = 'bulk-panic-dialog-desc'

  // Move focus into dialog on mount; restore on unmount
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null
    cancelRef.current?.focus()
    return () => { previousFocus?.focus() }
  }, [])

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onCancel()
    }
  }

  return (
    <div
      data-testid="bulk-panic-confirm-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descId}
      onKeyDown={handleKeyDown}
    >
      <div className="bg-[#12161F] border border-red-500/50 rounded-lg p-6 max-w-sm w-full mx-4 shadow-2xl">
        <h3 id={titleId} className="text-base font-bold text-red-400 mb-2">PANIC ALL BOTS</h3>
        <p id={descId} className="text-sm text-slate-300 mb-5">
          This will immediately close ALL open positions across ALL bots at market price.
          This action cannot be undone.
        </p>
        <div className="flex gap-3 justify-end">
          <button
            ref={cancelRef}
            data-testid="bulk-panic-confirm-cancel"
            onClick={onCancel}
            className="px-4 py-1.5 rounded text-sm bg-[#1A1F2E] border border-[#2A3245] text-slate-300 hover:border-slate-500 transition-colors"
          >
            Cancel
          </button>
          <button
            data-testid="bulk-panic-confirm-ok"
            onClick={onConfirm}
            className="px-4 py-1.5 rounded text-sm bg-red-600 hover:bg-red-700 text-white font-semibold transition-colors"
          >
            Confirm PANIC ALL
          </button>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Bulk mode controls (#2)
// ============================================================================

function BulkModeControls() {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState(false)
  const [error, setBulkError] = useState<string | null>(null)
  const [result, setResult] = useState<ModeResponse[] | null>(null)
  const [showPanicConfirm, setShowPanicConfirm] = useState(false)

  const token = localStorage.getItem('ccbt_dash_token')

  async function sendBulk(mode: string, confirmPanic?: boolean) {
    setPending(true)
    setBulkError(null)
    setResult(null)
    try {
      const body = mode === 'PANIC'
        ? { symbols: [], mode, confirm_panic: confirmPanic ?? false }
        : { symbols: [], mode }
      const res = await api.setBulkMode(body, token)
      setResult(res.results)
      // Invalidate bots cache so modes update
      await queryClient.invalidateQueries({ queryKey: ['bots'] })
    } catch (e) {
      setBulkError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setPending(false)
    }
  }

  const acceptedCount = result?.filter(r => r.accepted).length ?? 0

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        {/* STOP-ALL — prominent */}
        <button
          data-testid="bulk-stop-all"
          disabled={pending}
          onClick={() => sendBulk('GRACEFUL_STOP')}
          className="px-3 py-1.5 rounded border border-yellow-500/50 bg-yellow-500/10 text-yellow-300 text-xs font-medium hover:bg-yellow-500/20 disabled:opacity-50 transition-colors"
        >
          STOP ALL
        </button>

        {/* RESUME-ALL */}
        <button
          data-testid="bulk-resume-all"
          disabled={pending}
          onClick={() => sendBulk('NORMAL')}
          className="px-3 py-1.5 rounded border border-emerald-500/50 bg-emerald-500/10 text-emerald-300 text-xs font-medium hover:bg-emerald-500/20 disabled:opacity-50 transition-colors"
        >
          RESUME ALL
        </button>

        {/* PANIC-ALL — less prominent, behind confirm */}
        <button
          data-testid="bulk-panic-all"
          disabled={pending}
          onClick={() => setShowPanicConfirm(true)}
          className="px-3 py-1.5 rounded border border-red-500/30 bg-transparent text-red-400/70 text-xs hover:bg-red-500/10 hover:border-red-500/50 disabled:opacity-50 transition-colors"
        >
          PANIC ALL
        </button>

        {/* Result summary */}
        {result !== null && (
          <span data-testid="bulk-result" className="text-xs text-emerald-400 ml-1">
            {acceptedCount} bot{acceptedCount !== 1 ? 's' : ''} updated
          </span>
        )}

        {/* Error */}
        {error && (
          <span data-testid="bulk-error" className="text-xs text-red-400 ml-1">{error}</span>
        )}
      </div>

      {showPanicConfirm && (
        <BulkPanicConfirmDialog
          onConfirm={() => {
            setShowPanicConfirm(false)
            sendBulk('PANIC', true)
          }}
          onCancel={() => setShowPanicConfirm(false)}
        />
      )}
    </>
  )
}

// ============================================================================
// Portfolio alert banner (#4)
// ============================================================================

type AlertSeverity = 'clear' | 'warning' | 'critical'

/**
 * Determine banner severity from the bots list.
 *
 * BLOCKER fix: compare mode case-insensitively — the DB stores lowercase
 * ('panic','normal') but callers may pass either casing.  Normalising here
 * means the function is correct regardless of whether the API uppercases
 * the field (it now does) or not.
 *
 * MAJOR fix: use bot.error_count > 0 instead of bot.status === 'error'.
 * status is never 'error' in production (engine only writes 'running'/'stopped').
 * error_count is the real signal: it is incremented by the engine and exposed
 * via bot_health, which list_bots already reads.
 */
function getAlertSeverity(bots: BotRow[]): AlertSeverity {
  if (bots.length === 0) return 'clear'
  const hasPanic = bots.some(b => (b.mode ?? '').toUpperCase() === 'PANIC')
  if (hasPanic) return 'critical'
  const hasNonNormal = bots.some(b => {
    const m = (b.mode ?? '').toUpperCase()
    return m !== '' && m !== 'NORMAL'
  })
  const hasError = bots.some(b => (b.error_count ?? 0) > 0)
  if (hasNonNormal || hasError) return 'warning'
  return 'clear'
}

function PortfolioAlertBanner({ bots }: { bots: BotRow[] }) {
  const severity = getAlertSeverity(bots)

  if (severity === 'clear') {
    return null
  }

  const nonNormalCount = bots.filter(b => {
    const m = (b.mode ?? '').toUpperCase()
    return m !== '' && m !== 'NORMAL'
  }).length
  // error_count > 0 is the real production signal (status is never 'error').
  const errorCount = bots.filter(b => (b.error_count ?? 0) > 0).length

  const parts: string[] = []
  if (nonNormalCount > 0) parts.push(`${nonNormalCount} bot${nonNormalCount !== 1 ? 's' : ''} in non-NORMAL mode`)
  if (errorCount > 0) parts.push(`${errorCount} bot${errorCount !== 1 ? 's' : ''} with errors`)

  const bannerClass = severity === 'critical'
    ? 'bg-red-900/30 border-red-500/50 text-red-300'
    : 'bg-yellow-900/20 border-yellow-500/40 text-yellow-300'

  const dotClass = severity === 'critical' ? 'bg-red-400' : 'bg-yellow-400'

  return (
    <div
      data-testid="portfolio-alert-banner"
      data-severity={severity === 'critical' ? 'critical' : 'warning'}
      className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border mb-4 text-sm ${bannerClass}`}
      role="alert"
    >
      <span className={`w-2 h-2 rounded-full shrink-0 ${dotClass}`} aria-hidden="true" />
      <span>{parts.join(' · ')}</span>
    </div>
  )
}

// ============================================================================
// Bot pill (single bot in the grid)
//
// Design note (#7): The pill now shows a truncated 3–6 char symbol label as
// visible DOM text (e.g. "BTC", "ETH", "1000S") stripped of 'USDT'/'/USDT:USDT'
// suffixes. The full symbol is kept in aria-label for screen-readers and the
// overview table still has the un-truncated symbol as the single global
// exact-match text node (so `within(table).getByText(symbol)` stays unique).
// ============================================================================

/** Strip USDT/USDT:USDT suffixes and truncate to 3–6 chars for pill display. */
function pillLabel(symbol: string): string {
  const s = symbol
    .replace('/USDT:USDT', '')
    .replace('USDT:USDT', '')
    .replace('/USDT', '')
    .replace('USDT', '')
  // Truncate to 6 chars maximum so very long coin names still fit
  return s.slice(0, 6) || symbol.slice(0, 4)
}

function BotPill({ bot }: { bot: BotRow }) {
  // Backend emits 'running' or 'stopped' (bot/engine.py). Treat 'running' as
  // the active/green state; treat error_count > 0 as the error/red state
  // (status='error' is never emitted — driving color off error_count is correct).
  const isRunning = bot.status === 'running'
  const isError = (bot.error_count ?? 0) > 0

  const baseClass = isRunning
    ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300'
    : isError
    ? 'bg-red-500/10 border-red-500/40 text-red-300'
    : 'bg-slate-700/30 border-slate-600/40 text-slate-400'

  return (
    <Link
      to={`/bots/${bot.symbol}`}
      data-testid={`bot-pill-${bot.symbol}`}
      aria-label={bot.symbol}
      title={bot.symbol}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded border text-xs transition-opacity hover:opacity-90 ${baseClass}`}
    >
      {/* Status dot */}
      <span
        aria-hidden="true"
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
          isRunning ? 'bg-emerald-400' : isError ? 'bg-red-400' : 'bg-slate-500'
        }`}
      />
      {/* Truncated symbol label (3–6 chars, USDT stripped) */}
      <span data-testid={`bot-pill-label-${bot.symbol}`} className="font-medium tracking-tight">
        {pillLabel(bot.symbol)}
      </span>
      {/* Mode badge — renders 'STOP'/'TP'/'PANIC' text, unique to grid */}
      <ModeBadge mode={bot.mode} />
    </Link>
  )
}

// ============================================================================
// Bot status grid (grouped by strategy)
// ============================================================================

function BotGrid({ bots }: { bots: BotRow[] }) {
  // Group by strategy. useMemo MUST run before any early return — a conditional
  // hook crashes ("Rendered more hooks than during the previous render") on the
  // 0->N WS-hydration transition (the live bot-grid populate path).
  const groups = useMemo(() => {
    const map = new Map<string, BotRow[]>()
    for (const bot of bots) {
      const key = bot.strategy ?? '(unknown)'
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(bot)
    }
    return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b))
  }, [bots])

  if (bots.length === 0) {
    return (
      <div
        data-testid="bot-grid-empty"
        className="text-sm text-slate-500 italic py-4 text-center bg-[#1A1F2E] rounded-lg border border-[#2A3245]"
      >
        No bots deployed yet.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {groups.map(([strategy, groupBots]) => (
        <div key={strategy}>
          {/* Strategy group heading — only occurrence of strategy text in this section */}
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              {strategy}
            </span>
            <span className="text-xs text-slate-600">({groupBots.length})</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {groupBots.map((bot) => (
              <BotPill key={bot.symbol} bot={bot} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

// ============================================================================
// Bot overview table (sortable)
//
// Design note:
// - Symbol is rendered as a plain <span> (NOT a link with symbol text) so that
//   `screen.findByText(symbol)` finds exactly one match globally (this span).
//   The navigation icon uses aria-label, not text, so it doesn't conflict.
// - Strategy column is omitted — it appears only as grid group headings.
// - Mode column shows raw mode string (not ModeBadge) to avoid duplicating
//   badge text ('STOP'/'TP'/'PANIC') that already appears in the grid.
// ============================================================================

type SortKey = 'symbol' | 'mode' | 'status' | 'total_pnl' | 'win_rate_pct' | 'profit_factor' | 'trade_count'
type SortDir = 'asc' | 'desc'

function sortBots(bots: BotRow[], key: SortKey, dir: SortDir): BotRow[] {
  return [...bots].sort((a, b) => {
    const av = a[key] ?? ''
    const bv = b[key] ?? ''
    if (av < bv) return dir === 'asc' ? -1 : 1
    if (av > bv) return dir === 'asc' ? 1 : -1
    return 0
  })
}

const PAGE_SIZE_BOTS = 50

function BotOverviewTable({ bots }: { bots: BotRow[] }) {
  const [sortKey, setSortKey] = useState<SortKey>('symbol')
  const [sortDir, setSortDir] = useState<SortDir>('asc')
  const [page, setPage] = useState(0)

  // #8 — Filters
  const [searchText, setSearchText] = useState('')
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [strategyFilter, setStrategyFilter] = useState<string>('all')

  // Derive distinct strategy values from the full bots list
  const distinctStrategies = useMemo(() => {
    const s = new Set<string>()
    for (const b of bots) {
      if (b.strategy) s.add(b.strategy)
    }
    return Array.from(s).sort()
  }, [bots])

  // Derive distinct status values (excluding null/undefined)
  const distinctStatuses = useMemo(() => {
    const s = new Set<string>()
    for (const b of bots) {
      if (b.status) s.add(b.status)
    }
    return Array.from(s).sort()
  }, [bots])

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
    setPage(0)
  }

  // Apply filters then sort
  const filtered = useMemo(() => {
    let result = bots
    if (searchText.trim()) {
      const q = searchText.trim().toLowerCase()
      result = result.filter(b => b.symbol.toLowerCase().includes(q))
    }
    if (statusFilter !== 'all') {
      result = result.filter(b => (b.status ?? '') === statusFilter)
    }
    if (strategyFilter !== 'all') {
      result = result.filter(b => (b.strategy ?? '') === strategyFilter)
    }
    return result
  }, [bots, searchText, statusFilter, strategyFilter])

  const sorted = useMemo(() => sortBots(filtered, sortKey, sortDir), [filtered, sortKey, sortDir])
  const totalPages = Math.ceil(sorted.length / PAGE_SIZE_BOTS)
  const pageSlice = sorted.slice(page * PAGE_SIZE_BOTS, (page + 1) * PAGE_SIZE_BOTS)

  if (bots.length === 0) {
    return (
      <div
        data-testid="bot-overview-empty"
        className="text-sm text-slate-500 italic py-4 text-center"
      >
        No bots deployed yet. Start the bot runner to see data here.
      </div>
    )
  }

  function SortHeader({ col, label }: { col: SortKey; label: string }) {
    const active = sortKey === col
    return (
      <th
        className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 cursor-pointer select-none hover:text-slate-300 whitespace-nowrap"
        onClick={() => handleSort(col)}
      >
        {label}
        {active && (
          <span className="ml-1 text-slate-400" aria-hidden="true">
            {sortDir === 'asc' ? '▲' : '▼'}
          </span>
        )}
      </th>
    )
  }

  // Mode display: raw abbreviated string (not ModeBadge) to avoid text collision with grid badges.
  // Normalise to uppercase for comparison — DB may return lowercase.
  function modeLabel(mode: string | null): string {
    const m = (mode ?? '').toUpperCase()
    if (!m || m === 'NORMAL') return '—'
    if (m === 'GRACEFUL_STOP') return 'Graceful Stop'
    if (m === 'TP_ONLY') return 'TP Only'
    if (m === 'PANIC') return 'Panic!'
    return mode ?? ''
  }

  return (
    <div>
      {/* #8 — Filter controls: text search + status + strategy */}
      <div className="flex flex-wrap items-center gap-2 mb-3" data-testid="bot-overview-filters">
        <input
          type="text"
          data-testid="bot-filter-search"
          placeholder="Search symbol..."
          value={searchText}
          onChange={(e) => { setSearchText(e.target.value); setPage(0) }}
          className="px-2.5 py-1.5 rounded border border-[#2A3245] bg-[#12161F] text-slate-300 text-xs placeholder-slate-600 focus:outline-none focus:border-slate-500 w-36"
        />
        <select
          data-testid="bot-filter-status"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(0) }}
          className="px-2 py-1.5 rounded border border-[#2A3245] bg-[#12161F] text-slate-300 text-xs focus:outline-none focus:border-slate-500"
        >
          <option value="all">All Statuses</option>
          {distinctStatuses.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          data-testid="bot-filter-strategy"
          value={strategyFilter}
          onChange={(e) => { setStrategyFilter(e.target.value); setPage(0) }}
          className="px-2 py-1.5 rounded border border-[#2A3245] bg-[#12161F] text-slate-300 text-xs focus:outline-none focus:border-slate-500"
        >
          <option value="all">All Strategies</option>
          {distinctStrategies.map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {(searchText || statusFilter !== 'all' || strategyFilter !== 'all') && (
          <button
            data-testid="bot-filter-clear"
            onClick={() => { setSearchText(''); setStatusFilter('all'); setStrategyFilter('all'); setPage(0) }}
            className="px-2 py-1.5 rounded border border-[#2A3245] bg-transparent text-slate-500 text-xs hover:text-slate-300 hover:border-slate-500 transition-colors"
          >
            Clear filters
          </button>
        )}
        <span className="text-xs text-slate-600 ml-auto">
          {sorted.length} / {bots.length} bots
        </span>
      </div>

      {/* Empty filter state */}
      {sorted.length === 0 && (
        <div
          data-testid="bot-overview-no-match"
          className="text-sm text-slate-500 italic py-4 text-center"
        >
          No bots match the current filters.
        </div>
      )}

      {sorted.length > 0 && (
        <>
          <div className="overflow-x-auto">
            <table
              data-testid="bot-overview-table"
              className="w-full text-sm border-collapse"
            >
              <thead>
                <tr className="border-b border-[#2A3245]">
                  <SortHeader col="symbol" label="Symbol" />
                  <SortHeader col="mode" label="Mode" />
                  <SortHeader col="status" label="Status" />
                  <SortHeader col="trade_count" label="Trades" />
                  <SortHeader col="win_rate_pct" label="WR%" />
                  <SortHeader col="profit_factor" label="PF" />
                  <SortHeader col="total_pnl" label="PnL" />
                </tr>
              </thead>
              <tbody>
                {pageSlice.map((bot) => {
                  const pnlClass = bot.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                  return (
                    <tr
                      key={bot.symbol}
                      className="border-b border-[#1E2530] hover:bg-[#1A1F2E] transition-colors"
                    >
                      <td className="px-3 py-2">
                        {/*
                         * Symbol as plain <span> text node (NOT wrapped in a link with symbol text).
                         * This is the single global DOM text occurrence of the symbol.
                         * Navigation uses a separate icon-link with aria-label.
                         */}
                        <div className="flex items-center gap-1.5">
                          <span className="text-slate-100 font-medium">{bot.symbol}</span>
                          <Link
                            to={`/bots/${bot.symbol}`}
                            aria-label={`Open ${bot.symbol} detail`}
                            className="text-slate-600 hover:text-emerald-400 transition-colors text-xs leading-none"
                          >
                            <span aria-hidden="true">↗</span>
                          </Link>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-slate-500 text-xs">{modeLabel(bot.mode)}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`text-xs ${
                            bot.status === 'running'
                              ? 'text-emerald-400'
                              : (bot.error_count ?? 0) > 0
                              ? 'text-red-400'
                              : 'text-slate-500'
                          }`}
                        >
                          {bot.status ?? '—'}
                        </span>
                      </td>
                      <td className="px-3 py-2 tabular-nums text-slate-300">{bot.trade_count}</td>
                      <td className="px-3 py-2 tabular-nums text-slate-300">
                        {formatPct(bot.win_rate_pct)}
                      </td>
                      <td className="px-3 py-2 tabular-nums text-slate-300">
                        {formatPF(bot.profit_factor)}
                      </td>
                      <td className={`px-3 py-2 tabular-nums font-medium ${pnlClass}`}>
                        {formatMoney(bot.total_pnl)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center gap-3 mt-3 text-xs text-slate-500">
              <button
                className="px-2 py-1 rounded bg-[#1A1F2E] border border-[#2A3245] hover:border-slate-500 disabled:opacity-40"
                disabled={page === 0}
                onClick={() => setPage((p) => p - 1)}
              >
                ← Prev
              </button>
              <span>
                Page {page + 1} / {totalPages}
              </span>
              <button
                className="px-2 py-1 rounded bg-[#1A1F2E] border border-[#2A3245] hover:border-slate-500 disabled:opacity-40"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => p + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ============================================================================
// Recent trades table (all bots, paginated)
// ============================================================================

const PAGE_SIZE_TRADES = 50

function RecentTradesTable({ trades }: { trades: TradeRow[] }) {
  const [page, setPage] = useState(0)

  const totalPages = Math.ceil(trades.length / PAGE_SIZE_TRADES)
  const pageSlice = trades.slice(page * PAGE_SIZE_TRADES, (page + 1) * PAGE_SIZE_TRADES)

  if (trades.length === 0) {
    return (
      <div
        data-testid="recent-trades-empty"
        className="text-sm text-slate-500 italic py-4 text-center"
      >
        No closed trades yet. Once the bot closes positions, they appear here.
      </div>
    )
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table
          data-testid="recent-trades-table"
          className="w-full text-sm border-collapse"
        >
          <thead>
            <tr className="border-b border-[#2A3245]">
              {/* signal_source column is intentionally absent (not in DB schema) */}
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Symbol</th>
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Side</th>
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Entry</th>
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Exit</th>
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">PnL</th>
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Reason</th>
              <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Closed</th>
            </tr>
          </thead>
          <tbody>
            {pageSlice.map((trade, idx) => {
              const pnlClass =
                trade.pnl === null ? 'text-slate-400' : trade.pnl >= 0 ? 'text-emerald-400' : 'text-red-400'

              const closedAt = trade.timestamp
                ? new Date(trade.timestamp).toLocaleString('en-US', {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: false,
                  })
                : '—'

              return (
                <tr
                  key={trade.id ?? idx}
                  className="border-b border-[#1E2530] hover:bg-[#1A1F2E] transition-colors"
                >
                  <td className="px-3 py-2">
                    <Link
                      to={`/bots/${trade.symbol}`}
                      className="text-slate-100 font-medium hover:text-emerald-400 transition-colors"
                    >
                      {trade.symbol}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={`text-xs font-medium ${
                        trade.side === 'long' ? 'text-emerald-400' : 'text-red-400'
                      }`}
                    >
                      {trade.side?.toUpperCase() ?? '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2 tabular-nums text-slate-300">
                    {trade.entry_price !== null ? trade.entry_price.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '—'}
                  </td>
                  <td className="px-3 py-2 tabular-nums text-slate-300">
                    {trade.exit_price !== null ? trade.exit_price.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '—'}
                  </td>
                  <td className={`px-3 py-2 tabular-nums font-medium ${pnlClass}`}>
                    {formatMoney(trade.pnl)}
                  </td>
                  <td className="px-3 py-2 text-slate-500 text-xs">
                    {trade.close_reason ?? '—'}
                  </td>
                  <td className="px-3 py-2 text-slate-500 text-xs whitespace-nowrap">
                    {closedAt}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center gap-3 mt-3 text-xs text-slate-500">
          <button
            className="px-2 py-1 rounded bg-[#1A1F2E] border border-[#2A3245] hover:border-slate-500 disabled:opacity-40"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            ← Prev
          </button>
          <span>
            Page {page + 1} / {totalPages}
          </span>
          <button
            className="px-2 py-1 rounded bg-[#1A1F2E] border border-[#2A3245] hover:border-slate-500 disabled:opacity-40"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}

// ============================================================================
// Section wrapper
// ============================================================================

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="bg-[#12161F] rounded-lg border border-[#1E2530] p-4 mb-4">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
        {title}
      </h2>
      {children}
    </section>
  )
}

// ============================================================================
// Main PortfolioPage
// ============================================================================

interface PortfolioPageProps {
  upnl?: WSUpnlData | null
}

export function PortfolioPage({ upnl = null }: PortfolioPageProps) {
  const { data: summary } = useQuery({
    queryKey: ['portfolio', 'summary'],
    queryFn: () => api.portfolioSummary(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: botsData } = useQuery({
    queryKey: ['bots'],
    queryFn: () => api.listBots(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: tradesData } = useQuery({
    queryKey: ['trades', 'all'],
    queryFn: () => api.listTrades({ limit: 200 }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: equityData } = useQuery({
    queryKey: ['equity'],
    queryFn: () => api.equityCurve(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const { data: dailyPnlData } = useQuery({
    queryKey: ['daily-pnl'],
    queryFn: () => api.dailyPnl(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const bots = botsData?.bots ?? []
  const trades = tradesData?.trades ?? []
  const equityPoints = equityData?.points ?? []
  const dailyDays = dailyPnlData?.days ?? []

  // Per-bot PnL derived from the bot list (server-side computed total_pnl)
  const botPnlItems = useMemo(
    () => bots.map(b => ({ symbol: b.symbol, total_pnl: b.total_pnl })),
    [bots]
  )

  return (
    <div className="p-4 max-w-screen-xl mx-auto">
      <h1 className="text-2xl font-bold text-slate-100 mb-4">Portfolio Overview</h1>

      {/* Alert banner (#4) — aggregates already-loaded bots, no new API call */}
      <PortfolioAlertBanner bots={bots} />

      {/* Header metrics + bulk controls */}
      <PortfolioHeader summary={summary} />

      {/* Realtime unrealized PnL (from public markPrice WS — no API key) */}
      <div className="mb-4">
        <UpnlPanel upnl={upnl} />
      </div>

      {/* Charts row: Equity curve + Daily PnL */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
        <div className="flex flex-col gap-2">
          <Section title="Equity Curve">
            <EquityCurve points={equityPoints} />
          </Section>
          {/* #6 — Underwater drawdown sub-chart, server-computed field */}
          <Section title="Drawdown (Underwater)">
            <UnderwaterChart points={equityPoints} height={100} />
          </Section>
        </div>
        <Section title="Daily PnL">
          <DailyPnl days={dailyDays} />
        </Section>
      </div>

      {/* Per-bot PnL bar chart */}
      {bots.length > 0 && (
        <Section title="Per-Bot PnL">
          <PerBotPnl bots={botPnlItems} />
        </Section>
      )}

      {/* Bot status grid */}
      <Section title={`Bot Status (${bots.length})`}>
        <BotGrid bots={bots} />
      </Section>

      {/* Bot overview table */}
      <Section title="Bot Overview">
        <BotOverviewTable bots={bots} />
      </Section>

      {/* Recent trades */}
      <Section title={`Recent Trades (${trades.length})`}>
        <RecentTradesTable trades={trades} />
      </Section>
    </div>
  )
}
