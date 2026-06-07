/**
 * N10 — New panel components.
 *
 * Five panels, all data from API (no business math in TS):
 *   1. CloseReasonDonut — donut by exit mix; colour by PnL SIGN (must-fix #6)
 *   2. TradeGateTable   — trade-gate attribution; MIXED verdict; sample guard
 *   3. RiskAtStakeHeader — open risk summary; flags unprotected; no % gauge
 *   4. MonthlyCalendar  — CSS grid month view with PnL colouring
 *   5. ExpectancyHeatmap — hour×DOW heatmap; cells <20 trades masked grey
 *
 * Rules:
 *   - No business/financial math here — all numbers come from API.
 *   - Colours derived from per-row `pnl_positive` / `trade_count` flags
 *     that the API already returns.
 *   - Custom CSS grids (no nivo).
 */
import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  PieChart,
  Pie,
  Cell,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { api } from '../api/client'
import type {
  CloseReasonResponse,
  TradeGateResponse,
  TradeGateRow,
  OpenRiskResponse,
  CalendarResponse,
  HeatmapResponse,
} from '../api/client'

// ============================================================================
// Design helpers
// ============================================================================

const CL_PROFIT  = '#10b981' // emerald-500
const CL_LOSS    = '#ef4444' // red-500

// Section wrapper
function Section({
  title,
  children,
  testId,
}: {
  title: string
  children: React.ReactNode
  testId?: string
}) {
  return (
    <section
      data-testid={testId}
      className="bg-[#12161F] rounded-lg border border-[#1E2530] p-4 mb-4"
    >
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
        {title}
      </h2>
      {children}
    </section>
  )
}

// ============================================================================
// 1. CloseReasonDonut
// ============================================================================

/** Colour for a donut segment: derived from pnl_positive flag, NOT reason string. */
function segmentColour(pnlPositive: boolean): string {
  return pnlPositive ? CL_PROFIT : CL_LOSS
}

export function CloseReasonDonut({ symbol }: { symbol?: string | null } = {}) {
  const { data, isLoading } = useQuery({
    queryKey: ['close-reasons', symbol ?? null],
    queryFn: () => api.closeReasons({ symbol }),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const breakdown = (data as CloseReasonResponse | undefined)?.breakdown ?? []
  const total = breakdown.reduce((s, r) => s + r.count, 0)

  // Donut segments
  const chartData = breakdown.map(r => ({
    name: r.reason,
    value: r.count,
    pnlPositive: r.pnl_positive,
    totalPnl: r.total_pnl,
  }))

  if (isLoading) {
    return (
      <Section title="Exit Mix">
        <div className="h-48 animate-pulse bg-[#1A1F2E] rounded" />
      </Section>
    )
  }

  if (breakdown.length === 0) {
    return (
      <Section title="Exit Mix">
        <div className="text-sm text-slate-500 italic py-4 text-center">
          No closed trades yet.
        </div>
      </Section>
    )
  }

  return (
    <Section title="Exit Mix">
      <div className="flex flex-col sm:flex-row gap-4 items-start">
        {/* Donut chart */}
        <div className="w-full sm:w-48 shrink-0" style={{ height: 180 }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={chartData}
                cx="50%"
                cy="50%"
                innerRadius={45}
                outerRadius={75}
                paddingAngle={2}
                dataKey="value"
                strokeWidth={0}
              >
                {chartData.map((entry, idx) => (
                  <Cell
                    key={`cell-${idx}`}
                    fill={segmentColour(entry.pnlPositive)}
                  />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: '#1A1F2E',
                  border: '1px solid #2A3245',
                  borderRadius: 6,
                  fontSize: 12,
                }}
                formatter={(value, name) => [`${value} trades`, name]}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>

        {/* Legend — one row per reason */}
        <div data-testid="donut-legend" className="flex flex-col gap-1.5 text-xs min-w-0">
          {breakdown.map(r => {
            const pct = total > 0 ? ((r.count / total) * 100).toFixed(0) : '0'
            return (
              <div
                key={r.reason}
                data-testid={`donut-legend-${r.reason}`}
                className="flex items-center gap-2"
              >
                {/* Colour dot — derived from PnL sign, NOT reason string */}
                <span
                  data-testid={`donut-legend-dot-${r.reason}`}
                  className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${
                    r.pnl_positive ? 'bg-emerald-500' : 'bg-red-500'
                  }`}
                />
                <span className="text-slate-300 truncate">
                  {r.reason} · {r.count} · {pct}%
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </Section>
  )
}

// ============================================================================
// 2. TradeGateTable
// ============================================================================

// Verdict sort rank: DROP=0, MARGINAL=1, KEEP_TESTING=2, READY_TO_AUDIT=3, MIXED=4
const VERDICT_RANK: Record<string, number> = {
  DROP: 0,
  MARGINAL: 1,
  KEEP_TESTING: 2,
  READY_TO_AUDIT: 3,
  MIXED: 4,
}

const VERDICT_STYLE: Record<string, { label: string; cls: string }> = {
  DROP:           { label: 'DROP',           cls: 'bg-red-500/20 text-red-300' },
  MARGINAL:       { label: 'MARGINAL',       cls: 'bg-yellow-500/20 text-yellow-300' },
  KEEP_TESTING:   { label: 'KEEP_TESTING',   cls: 'bg-blue-500/20 text-blue-300' },
  READY_TO_AUDIT: { label: 'READY_TO_AUDIT', cls: 'bg-emerald-500/20 text-emerald-300' },
  MIXED:          { label: 'MIXED',          cls: 'bg-purple-500/20 text-purple-300' },
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const cfg = VERDICT_STYLE[verdict] ?? { label: verdict, cls: 'bg-slate-700 text-slate-300' }
  return (
    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${cfg.cls}`}>
      {cfg.label}
    </span>
  )
}

function sortGateRows(rows: TradeGateRow[]): TradeGateRow[] {
  return [...rows].sort((a, b) => {
    const ra = VERDICT_RANK[a.verdict] ?? 99
    const rb = VERDICT_RANK[b.verdict] ?? 99
    if (ra !== rb) return ra - rb
    // Within same verdict: sort by PnL desc (server provides trade_count, use profit_factor as proxy)
    return b.profit_factor - a.profit_factor
  })
}

export function TradeGateTable() {
  const { data, isLoading } = useQuery({
    queryKey: ['trade-gate'],
    queryFn: () => api.tradeGate(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const gateData = data as TradeGateResponse | undefined
  const summary = gateData?.summary
  const rawRows = gateData?.rows ?? []
  const sorted = useMemo(() => sortGateRows(rawRows), [rawRows])

  if (isLoading) {
    return (
      <Section title="Trade Gate">
        <div className="h-32 animate-pulse bg-[#1A1F2E] rounded" />
      </Section>
    )
  }

  return (
    <Section title="Trade Gate">
      {/* Sample header — must-fix #4 */}
      {summary && (
        <div
          data-testid="gate-sample-header"
          className="text-xs text-slate-400 mb-3 flex items-center gap-1"
        >
          <span className="font-medium text-slate-200">{summary.n_meeting_min}</span>
          <span>of</span>
          <span className="font-medium text-slate-200">{summary.n_total}</span>
          <span>symbols meet the {summary.min_trades_threshold}-trade min</span>
        </div>
      )}

      {/* Legend */}
      <div className="text-[10px] text-slate-600 mb-2">
        gate (READY_TO_AUDIT) = ≥{summary?.min_trades_threshold ?? 15} trades &amp; PF≥graduate_pf &amp;
        single-config symbol · MIXED = multiple configs share the netted position
      </div>

      {sorted.length === 0 ? (
        <div
          data-testid="gate-empty"
          className="text-sm text-slate-500 italic py-4 text-center"
        >
          No trade data available yet.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table
            data-testid="gate-table"
            className="w-full text-sm border-collapse"
          >
            <thead>
              <tr className="border-b border-[#2A3245]">
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Symbol</th>
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Trades</th>
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">WR%</th>
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">PF</th>
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Reward/$Avg-Loss</th>
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(row => {
                const isSubThreshold = !row.meets_min_trades
                const rowClass = isSubThreshold
                  ? 'opacity-40 text-slate-500 border-b border-[#1E2530]'
                  : 'border-b border-[#1E2530] hover:bg-[#1A1F2E] transition-colors'

                const pfDisplay =
                  !isFinite(row.profit_factor) || row.profit_factor > 999
                    ? '∞'
                    : row.profit_factor.toFixed(2)

                const rewardDisplay =
                  row.reward_to_avgloss === null
                    ? '—'
                    : `$${row.reward_to_avgloss.toFixed(2)}`

                return (
                  <tr
                    key={row.symbol}
                    data-testid={`gate-row-${row.symbol}`}
                    className={rowClass}
                  >
                    <td className="px-3 py-2 font-medium text-slate-200">{row.symbol}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-400">{row.trade_count}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-400">
                      {row.win_rate_pct.toFixed(0)}%
                    </td>
                    <td className="px-3 py-2 tabular-nums text-slate-400">{pfDisplay}</td>
                    <td className="px-3 py-2 tabular-nums text-slate-400">{rewardDisplay}</td>
                    <td className="px-3 py-2">
                      <VerdictBadge verdict={row.verdict} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  )
}

// ============================================================================
// 3. RiskAtStakeHeader
// ============================================================================

export function RiskAtStakeHeader({ symbol }: { symbol?: string | null } = {}) {
  const { data, isLoading } = useQuery({
    queryKey: ['risk', symbol ?? null],
    queryFn: () => api.openRisk({ symbol }),
    staleTime: 15_000,
    refetchInterval: 15_000,
  })

  const riskData = data as OpenRiskResponse | undefined
  const rows = riskData?.rows ?? []
  const unprotectedCount = riskData?.unprotected_count ?? 0
  const openCount = rows.length

  // Consume pre-computed server fields (api/risk.py get_open_risk() computes these).
  // Never recompute financial math in TS — server is authoritative.
  const maxSlLoss: number = (riskData as any)?.max_sl_loss ?? 0
  const notional: number = (riskData as any)?.notional ?? 0

  if (isLoading) {
    return (
      <Section title="Risk at Stake">
        <div className="h-16 animate-pulse bg-[#1A1F2E] rounded" />
      </Section>
    )
  }

  return (
    <Section title="Risk at Stake">
      {/* Big number: max SL loss in $ */}
      <div className="flex items-baseline gap-2 mb-2">
        <span
          data-testid="risk-max-sl-loss"
          className="text-3xl font-bold tabular-nums text-slate-100"
        >
          ${maxSlLoss.toFixed(2)}
        </span>
        <span className="text-xs text-slate-500">max SL loss</span>
      </div>

      {/* Caption: N open · M unprotected · $notional */}
      <div
        data-testid="risk-caption"
        className="flex items-center gap-2 text-xs text-slate-500"
      >
        <span>{openCount} open</span>
        <span>·</span>
        <span
          data-testid="risk-unprotected"
          className={unprotectedCount > 0 ? 'text-red-400 font-medium' : 'text-slate-500'}
        >
          {unprotectedCount} unprotected
        </span>
        <span>·</span>
        <span>${notional.toFixed(0)} notional</span>
      </div>

      {/*
       * NO percentage gauge — ships degraded until bot persists a real balance.
       * The *50 hack is explicitly banned by the ticket.
       * data-testid="risk-pct-gauge" intentionally absent.
       */}

      {/* Per-position rows */}
      {rows.length > 0 && (
        <div className="mt-3 space-y-1">
          {rows.map(r => (
            <div
              key={r.symbol}
              className="flex items-center gap-2 text-xs"
            >
              <span
                className={`inline-block w-2 h-2 rounded-full shrink-0 ${
                  r.unprotected ? 'bg-red-400' : 'bg-emerald-400'
                }`}
              />
              <span className="text-slate-300 font-medium">{r.symbol}</span>
              <span
                className={`uppercase text-[10px] font-semibold ${
                  r.side === 'long' ? 'text-emerald-400' : 'text-red-400'
                }`}
              >
                {r.side}
              </span>
              {r.unprotected && (
                <span className="text-red-400 text-[10px]">NO SL</span>
              )}
            </div>
          ))}
        </div>
      )}

      {rows.length === 0 && (
        <div className="mt-2 text-xs text-slate-600 italic">No open positions.</div>
      )}
    </Section>
  )
}

// ============================================================================
// 4. MonthlyCalendar
// ============================================================================

const MONTH_NAMES = [
  'January','February','March','April','May','June',
  'July','August','September','October','November','December',
]

const DOW_LABELS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate()
}

function firstDayOfMonth(year: number, month: number): number {
  // Returns 0=Sun, 1=Mon, ...
  return new Date(year, month - 1, 1).getDay()
}

function pad2(n: number): string {
  return n.toString().padStart(2, '0')
}

export function MonthlyCalendar({
  symbol,
  initialYear,
  initialMonth,
}: {
  symbol?: string | null
  initialYear?: number
  initialMonth?: number
} = {}) {
  const now = new Date()
  const [year, setYear] = useState(initialYear ?? now.getFullYear())
  const [month, setMonth] = useState(initialMonth ?? now.getMonth() + 1) // 1-indexed

  const { data, isLoading } = useQuery({
    queryKey: ['calendar', symbol ?? null, year, month],
    queryFn: () => api.calendar({ symbol, year, month }),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  const calData = data as CalendarResponse | undefined
  const cells = calData?.cells ?? []

  // Build a date → cell map for O(1) lookups
  const cellMap = useMemo(() => {
    const m: Record<string, { pnl: number; trade_count: number; win_rate_pct: number }> = {}
    for (const c of cells) {
      m[c.date] = { pnl: c.pnl, trade_count: c.trade_count, win_rate_pct: c.win_rate_pct ?? 0 }
    }
    return m
  }, [cells])

  function prevMonth() {
    if (month === 1) { setYear(y => y - 1); setMonth(12) }
    else setMonth(m => m - 1)
  }
  function nextMonth() {
    if (month === 12) { setYear(y => y + 1); setMonth(1) }
    else setMonth(m => m + 1)
  }

  const totalDays = daysInMonth(year, month)
  const startDow  = firstDayOfMonth(year, month) // 0=Sun

  // Build calendar grid array: null = empty slot, number = day
  const gridSlots: (number | null)[] = [
    ...Array(startDow).fill(null),
    ...Array.from({ length: totalDays }, (_, i) => i + 1),
  ]
  // Pad to complete last week
  while (gridSlots.length % 7 !== 0) gridSlots.push(null)

  return (
    <Section title="Monthly PnL Calendar (Diagnostic)" testId="monthly-calendar">
      {/* Month selector */}
      <div
        data-testid="cal-month-selector"
        className="flex items-center gap-3 mb-3"
      >
        <button
          onClick={prevMonth}
          className="px-2 py-1 rounded bg-[#1A1F2E] border border-[#2A3245] text-xs text-slate-400 hover:text-slate-200 hover:border-slate-500"
          aria-label="Previous month"
        >
          ←
        </button>
        <span className="text-sm font-medium text-slate-200">
          {MONTH_NAMES[month - 1]} {year}
        </span>
        <button
          onClick={nextMonth}
          className="px-2 py-1 rounded bg-[#1A1F2E] border border-[#2A3245] text-xs text-slate-400 hover:text-slate-200 hover:border-slate-500"
          aria-label="Next month"
        >
          →
        </button>
      </div>

      {isLoading ? (
        <div className="h-40 animate-pulse bg-[#1A1F2E] rounded" />
      ) : (
        <>
          {/* DOW headers */}
          <div
            style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 2 }}
          >
            {DOW_LABELS.map(d => (
              <div
                key={d}
                className="text-center text-[10px] font-semibold text-slate-600 uppercase py-1"
              >
                {d}
              </div>
            ))}
          </div>

          {/* Calendar grid — custom CSS grid */}
          <div
            style={{ display: 'grid', gridTemplateColumns: 'repeat(7, minmax(0, 1fr))', gap: 2 }}
          >
            {gridSlots.map((day, idx) => {
              if (day === null) {
                return <div key={`empty-${idx}`} />
              }
              const dateStr = `${year}-${pad2(month)}-${pad2(day)}`
              const cell = cellMap[dateStr]
              const hasTrades = cell !== undefined
              const pnl = cell?.pnl ?? 0
              const winRatePct = cell?.win_rate_pct ?? 0

              const cellColour = !hasTrades
                ? 'bg-[#1A1F2E] text-slate-700'
                : pnl > 0
                ? 'bg-emerald-900/30 text-emerald-300 border border-emerald-800/40'
                : 'bg-red-900/30 text-red-300 border border-red-800/40'

              return (
                <div
                  key={dateStr}
                  data-testid={`cal-cell-${dateStr}`}
                  className={`rounded p-1 min-h-[52px] text-[10px] flex flex-col ${cellColour}`}
                >
                  <span className="font-semibold">{day}</span>
                  {hasTrades && (
                    <>
                      <span className="tabular-nums">
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(1)}
                      </span>
                      <span className="text-[9px] opacity-70">WR {winRatePct.toFixed(0)}%</span>
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </Section>
  )
}

// ============================================================================
// 5. ExpectancyHeatmap
// ============================================================================

const BUCKET_OPTIONS = [1, 2, 4, 6, 8, 12, 24]
const MIN_SAMPLES = 20 // must-fix #4: colour only cells with ≥20 trades

// pandas day-of-week: Mon=0 … Sun=6 (NOT JS Sun=0)
const DOW_LABEL_MAP: Record<number, string> = {
  0: 'Mon',
  1: 'Tue',
  2: 'Wed',
  3: 'Thu',
  4: 'Fri',
  5: 'Sat',
  6: 'Sun',
}

export function ExpectancyHeatmap({ symbol }: { symbol?: string | null } = {}) {
  const [bucketHours, setBucketHours] = useState(4)

  const { data, isLoading } = useQuery({
    queryKey: ['heatmap', symbol ?? null, bucketHours],
    queryFn: () => api.heatmap({ symbol, bucket_hours: bucketHours }),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  const hmData = data as HeatmapResponse | undefined
  const cells = hmData?.cells ?? []

  // Collect unique hours and DOWs from the data
  const hours = useMemo(() => {
    const hs = new Set(cells.map(c => c.hour))
    return Array.from(hs).sort((a, b) => a - b)
  }, [cells])

  const dows = useMemo(() => {
    const ds = new Set(cells.map(c => c.dow))
    return Array.from(ds).sort((a, b) => a - b)
  }, [cells])

  // Build cell lookup map
  const cellMap = useMemo(() => {
    const m: Record<string, typeof cells[0]> = {}
    for (const c of cells) {
      m[`${c.hour}-${c.dow}`] = c
    }
    return m
  }, [cells])

  function cellColourClass(c: typeof cells[0] | undefined): string {
    if (!c || c.trade_count < MIN_SAMPLES) return 'bg-slate-700/40 text-slate-600'
    // Colour by PnL sign
    if (c.pnl > 0) return 'bg-emerald-900/40 text-emerald-300 border border-emerald-800/40'
    if (c.pnl < 0) return 'bg-red-900/40 text-red-300 border border-red-800/40'
    return 'bg-slate-700/40 text-slate-400'
  }

  return (
    <Section title="Expectancy Heatmap (Diagnostic)" testId="expectancy-heatmap">
      {/* Diagnostic label — required by ticket */}
      <div
        data-testid="heatmap-diagnostic-label"
        className="text-[10px] text-amber-500/80 mb-2"
      >
        Diagnostic — not a trading rule. Low-sample cells are masked.
      </div>

      {/* Bucket selector */}
      <div data-testid="heatmap-bucket-selector" className="flex items-center gap-2 mb-3 text-xs text-slate-400">
        <span>Bucket size:</span>
        {BUCKET_OPTIONS.map(b => (
          <button
            key={b}
            onClick={() => setBucketHours(b)}
            className={`px-2 py-0.5 rounded border text-[11px] transition-colors ${
              b === bucketHours
                ? 'bg-emerald-700/30 border-emerald-600/40 text-emerald-300'
                : 'bg-[#1A1F2E] border-[#2A3245] text-slate-500 hover:text-slate-300'
            }`}
          >
            {b}h
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="h-40 animate-pulse bg-[#1A1F2E] rounded" />
      ) : cells.length === 0 ? (
        <div className="text-sm text-slate-500 italic py-4 text-center">
          Not enough trade data for heatmap.
        </div>
      ) : (
        <div className="overflow-x-auto">
          {/* DOW header row */}
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: `48px repeat(${dows.length}, minmax(48px, 1fr))`,
              gap: 2,
              marginBottom: 2,
            }}
          >
            <div /> {/* empty corner */}
            {dows.map(d => (
              <div
                key={d}
                className="text-center text-[10px] font-semibold text-slate-600 uppercase py-1"
              >
                {DOW_LABEL_MAP[d] ?? d}
              </div>
            ))}
          </div>

          {/* Hour rows */}
          <div className="flex flex-col gap-0.5">
            {hours.map(h => (
              <div
                key={h}
                style={{
                  display: 'grid',
                  gridTemplateColumns: `48px repeat(${dows.length}, minmax(48px, 1fr))`,
                  gap: 2,
                }}
              >
                {/* Hour label */}
                <div className="text-[10px] text-slate-600 flex items-center justify-end pr-2">
                  {h.toString().padStart(2,'0')}h
                </div>

                {/* DOW cells */}
                {dows.map(d => {
                  const c = cellMap[`${h}-${d}`]
                  const colCls = cellColourClass(c)
                  const isMasked = !c || c.trade_count < MIN_SAMPLES

                  return (
                    <div
                      key={`${h}-${d}`}
                      data-testid={`heatmap-cell-${h}-${d}`}
                      className={`rounded p-1 min-h-[44px] text-[10px] flex flex-col items-center justify-center ${colCls}`}
                      title={
                        isMasked
                          ? `${c?.trade_count ?? 0} trades (masked — need ≥${MIN_SAMPLES})`
                          : `PnL: ${c?.pnl.toFixed(2)} · ${c?.trade_count} trades`
                      }
                    >
                      {c && (
                        <>
                          {!isMasked && (
                            <span className="tabular-nums font-medium">
                              {c.pnl >= 0 ? '+' : ''}{c.pnl.toFixed(1)}
                            </span>
                          )}
                          <span className="opacity-70">{c.trade_count}</span>
                        </>
                      )}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </Section>
  )
}
