/**
 * N7 — AI Analytics page.
 *
 * Sections:
 *   1. Metric cards: total decisions, decided count, avg accuracy, avg influence
 *   2. Accuracy bar chart (per-symbol accuracy %)
 *   3. Calibration detail table (symbol, total, correct, accuracy, influence)
 *
 * Rules:
 *   - No financial math in TS — all values pre-computed by Python backend.
 *   - Data from /api/ai/calibration.
 *   - Empty state when no calibration rows.
 *   - Charts via recharts (consistent with rest of dashboard).
 */
import { useQuery } from '@tanstack/react-query'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  ReferenceLine, Cell,
} from 'recharts'
import { api } from '../api/client'
import type { AICalibrationResponse } from '../api/client'

// ============================================================================
// Color tokens
// ============================================================================

const COLOR_PROFIT  = '#00C853'
const COLOR_LOSS    = '#FF1744'
const COLOR_WARN    = '#FFD600'
const COLOR_NEUTRAL = '#78909C'
const COLOR_GOOD    = '#00E5FF'

// ============================================================================
// Helpers
// ============================================================================

function influenceClass(influence: number): string {
  if (influence >= 1.25) return 'text-emerald-400'
  if (influence >= 1.0)  return 'text-slate-200'
  if (influence >= 0.75) return 'text-yellow-400'
  return 'text-red-400'
}

function accuracyColor(pct: number): string {
  if (pct >= 65) return COLOR_PROFIT
  if (pct >= 55) return COLOR_GOOD
  if (pct >= 45) return COLOR_WARN
  return COLOR_LOSS
}

// ============================================================================
// Metric card
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
      <span className={`text-xl font-semibold tabular-nums ${valueClass}`} data-testid={testId}>
        {value}
      </span>
    </div>
  )
}

// ============================================================================
// Aggregate stats from calibration rows
// ============================================================================

interface AggStats {
  totalDecisions: number
  decidedCount: number
  avgAccuracy: number
  avgInfluence: number
  correctCount: number
}

function computeAggStats(rows: AICalibrationResponse['rows']): AggStats {
  if (rows.length === 0) {
    return { totalDecisions: 0, decidedCount: 0, avgAccuracy: 0, avgInfluence: 1.0, correctCount: 0 }
  }
  const totalDecisions = rows.reduce((s, r) => s + r.total_decisions, 0)
  const correctCount   = rows.reduce((s, r) => s + r.correct, 0)
  const decidedCount   = rows.reduce((s, r) => {
    // Decided = rows where outcome is known; we use correct as a proxy
    // (total_decisions includes should_skip; for display we show total)
    return s + r.total_decisions
  }, 0)
  // Weighted average accuracy (weighted by total_decisions)
  const weightedAcc = rows.reduce((s, r) => s + r.accuracy_pct * r.total_decisions, 0) / totalDecisions
  const avgInfluence = rows.reduce((s, r) => s + r.influence_factor, 0) / rows.length
  return {
    totalDecisions,
    decidedCount,
    avgAccuracy: Math.round(weightedAcc * 10) / 10,
    avgInfluence: Math.round(avgInfluence * 100) / 100,
    correctCount,
  }
}

// ============================================================================
// Accuracy bar chart
// ============================================================================

function AccuracyChart({ rows }: { rows: AICalibrationResponse['rows'] }) {
  const data = rows.map(r => ({
    symbol: r.symbol.replace('/USDT:USDT', '').replace('USDT', ''),
    accuracy_pct: r.accuracy_pct,
    influence_factor: r.influence_factor,
  }))

  return (
    <div
      data-testid="ai-accuracy-chart"
      className="w-full"
      style={{ height: 200 }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 40 }}>
          <XAxis
            dataKey="symbol"
            tick={{ fill: COLOR_NEUTRAL, fontSize: 10 }}
            angle={-40}
            textAnchor="end"
            interval={0}
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: COLOR_NEUTRAL, fontSize: 10 }}
            tickFormatter={(v: number) => `${v}%`}
          />
          <Tooltip
            contentStyle={{ backgroundColor: '#1A1F2E', border: '1px solid #2A3245', fontSize: 12 }}
            formatter={(v) => [v !== undefined ? `${v}%` : '', 'Accuracy']}
          />
          <ReferenceLine y={65} stroke={COLOR_PROFIT} strokeDasharray="4 2" label={{ value: '65%', fill: COLOR_PROFIT, fontSize: 10 }} />
          <ReferenceLine y={45} stroke={COLOR_LOSS}   strokeDasharray="4 2" label={{ value: '45%', fill: COLOR_LOSS,   fontSize: 10 }} />
          <Bar dataKey="accuracy_pct" radius={[3, 3, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={accuracyColor(d.accuracy_pct)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ============================================================================
// Calibration detail table
// ============================================================================

function CalibrationTable({ rows }: { rows: AICalibrationResponse['rows'] }) {
  return (
    <div data-testid="ai-calibration-table" className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-[#2A3245]">
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Symbol</th>
            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider text-slate-500">Total</th>
            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider text-slate-500">Correct</th>
            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider text-slate-500">Accuracy</th>
            <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wider text-slate-500">Influence</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr
              key={row.symbol ?? idx}
              className="border-b border-[#1E2530] hover:bg-[#1A1F2E] transition-colors"
            >
              <td className="px-3 py-2 text-slate-300 text-xs font-mono">
                {row.symbol.replace('/USDT:USDT', '').replace('USDT', '')}
              </td>
              <td className="px-3 py-2 text-right text-slate-400 tabular-nums text-xs">
                {row.total_decisions}
              </td>
              <td className="px-3 py-2 text-right text-slate-400 tabular-nums text-xs">
                {row.correct}
              </td>
              <td className="px-3 py-2 text-right tabular-nums text-xs">
                <span style={{ color: accuracyColor(row.accuracy_pct) }}>
                  {row.accuracy_pct.toFixed(1)}%
                </span>
              </td>
              <td className={`px-3 py-2 text-right tabular-nums text-xs font-medium ${influenceClass(row.influence_factor)}`}>
                {row.influence_factor.toFixed(2)}×
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ============================================================================
// AIAnalyticsPage
// ============================================================================

export function AIAnalyticsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['ai', 'calibration'],
    queryFn: () => api.aiCalibration(),
    staleTime: 60_000,
    refetchInterval: 60_000,
  })

  const rows = data?.rows ?? []
  // Prefer server-provided aggregate; fall back to browser-computed stats if not present
  const serverAgg = (data as any)?.aggregate as {
    total_decisions: number
    decided_trades: number
    weighted_accuracy_pct: number
    avg_influence_factor: number
  } | null | undefined
  const browserStats = computeAggStats(rows)
  const stats = serverAgg
    ? {
        totalDecisions: serverAgg.total_decisions,
        decidedCount: serverAgg.decided_trades,
        avgAccuracy: Math.round(serverAgg.weighted_accuracy_pct * 10) / 10,
        avgInfluence: Math.round(serverAgg.avg_influence_factor * 100) / 100,
        correctCount: browserStats.correctCount,
      }
    : browserStats
  // Only show empty state once we have data (not while still loading).
  // If the server returned an aggregate object, we have data (even if decisions=0).
  const hasData = rows.length > 0 || serverAgg !== null && serverAgg !== undefined
  const showEmpty = !isLoading && data !== undefined && !hasData

  return (
    <div
      data-testid="ai-analytics"
      className="p-4 max-w-screen-xl mx-auto"
    >
      <h1 className="text-2xl font-bold text-slate-100 mb-4">AI Analytics</h1>

      {isLoading && (
        /* Loading skeleton */
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6 animate-pulse">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="h-16 bg-[#1A1F2E] rounded-lg border border-[#2A3245]" />
          ))}
        </div>
      )}

      {showEmpty && (
        /* Empty state */
        <div
          data-testid="ai-empty"
          className="flex flex-col items-center justify-center py-16 text-slate-500"
        >
          <span className="text-4xl mb-4">&#129302;</span>
          <p className="text-sm italic">
            No AI calibration data yet. Decisions appear here once the bot has traded with the AI advisor enabled.
          </p>
        </div>
      )}

      {hasData && (
        <>
          {/* Metric cards */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
            <MetricCard
              label="Total Decisions"
              value={String(stats.totalDecisions)}
              testId="ai-total-decisions"
            />
            <MetricCard
              label="Avg Accuracy"
              value={`${stats.avgAccuracy}%`}
              testId="ai-accuracy-pct"
              valueClass={
                stats.avgAccuracy >= 65
                  ? 'text-emerald-400'
                  : stats.avgAccuracy >= 45
                  ? 'text-yellow-400'
                  : 'text-red-400'
              }
            />
            <MetricCard
              label="Correct"
              value={String(stats.correctCount)}
              testId="ai-correct-count"
            />
            <MetricCard
              label="Avg Influence"
              value={`${stats.avgInfluence.toFixed(2)}×`}
              testId="ai-influence-factor"
              valueClass={influenceClass(stats.avgInfluence)}
            />
          </div>

          {/* Accuracy chart */}
          <section className="bg-[#12161F] rounded-lg border border-[#1E2530] p-4 mb-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
              Accuracy by Symbol
            </h2>
            <AccuracyChart rows={rows} />
          </section>

          {/* Detail table */}
          <section className="bg-[#12161F] rounded-lg border border-[#1E2530] p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3">
              Calibration Detail
            </h2>
            <CalibrationTable rows={rows} />
          </section>
        </>
      )}
    </div>
  )
}
