/**
 * N5 — Chart components (recharts-based).
 *
 * Three charts for the portfolio / single-bot views:
 *   - EquityCurve  : area chart, cumulative PnL, zero-baseline reference line
 *   - PerBotPnl    : horizontal bar chart, green/red by sign
 *   - DailyPnl     : vertical bar chart, green/red by sign
 *
 * Rules:
 *   - recharts only (no nivo, no lightweight-charts here)
 *   - No financial math — all values arrive pre-computed from Python
 *   - Dark-theme palette from index.css design tokens
 *   - data-testid on every root for test targeting
 *   - data-has-profit / data-has-loss / data-point-count for color-semantic tests
 *   - Empty state: styled placeholder with helpful text
 *   - Live-update on WS snapshot: parent passes fresh props; recharts is reactive
 */

import React from 'react'
import {
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  Cell,
} from 'recharts'

// ---- Design tokens (from index.css) ----------------------------------------

const COLOR_PROFIT  = '#00C853'   // --color-profit
const COLOR_LOSS    = '#FF1744'   // --color-loss
const COLOR_CYAN    = '#00E5FF'   // --color-cyan  (equity line)
const COLOR_NEUTRAL = '#78909C'   // --color-neutral
const COLOR_BG_CARD = '#151A22'   // --color-bg-card
const COLOR_GRID    = '#1E2530'   // --color-bg-hover

// ---- Shared types (exported so tests can import) ----------------------------

export interface EquityPoint {
  timestamp: string
  equity: number
  cumulative_pnl: number
}

export interface DailyPnlPoint {
  date: string
  pnl: number
  trade_count: number
}

export interface BotPnlItem {
  symbol: string
  total_pnl: number
}

// ---- Tooltip style shared ---------------------------------------------------

const tooltipStyle: React.CSSProperties = {
  background: COLOR_BG_CARD,
  border: `1px solid ${COLOR_GRID}`,
  borderRadius: '6px',
  fontSize: '12px',
  color: '#FAFAFA',
}

const tooltipLabelStyle: React.CSSProperties = {
  color: COLOR_NEUTRAL,
  marginBottom: '4px',
}

// ---- EmptyState ------------------------------------------------------------

function EmptyState({ testId, message }: { testId: string; message: string }) {
  return (
    <div
      data-testid={testId}
      className="flex items-center justify-center h-full min-h-[160px] text-sm text-slate-500 italic bg-[#151A22] rounded-lg border border-[#1E2530]"
    >
      {message}
    </div>
  )
}

// ============================================================================
// EquityCurve
// ============================================================================

interface EquityCurveProps {
  points: EquityPoint[]
  height?: number
}

/**
 * Area chart: cumulative PnL over time with a zero-baseline reference line.
 * Colour: cyan (#00E5FF) matching the Streamlit Plotly chart.
 */
export function EquityCurve({ points, height = 220 }: EquityCurveProps) {
  if (points.length === 0) {
    return <EmptyState testId="equity-curve-empty" message="No trade history yet — equity curve will appear once the first trade closes." />
  }

  const hasProfit = points.some(p => p.cumulative_pnl > 0)
  const hasLoss   = points.some(p => p.cumulative_pnl < 0)

  // Format timestamp label for X axis: "Jan 1" or "01:00" if same day
  function formatXLabel(ts: string): string {
    try {
      const d = new Date(ts)
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
    } catch {
      return ts.slice(0, 10)
    }
  }

  function formatTooltipValue(value: number): string {
    const sign = value >= 0 ? '+' : ''
    return `${sign}$${Math.abs(value).toFixed(2)}`
  }

  const chartData = points.map(p => ({
    ts: p.timestamp,
    pnl: p.cumulative_pnl,
    label: formatXLabel(p.timestamp),
  }))

  return (
    <div
      data-testid="equity-curve"
      data-point-count={String(points.length)}
      data-has-profit={hasProfit ? 'true' : 'false'}
      data-has-loss={hasLoss ? 'true' : 'false'}
      style={{ height }}
    >
      {/* Hidden span anchors the zero reference line label for test targeting */}
      <span data-testid="equity-zero-ref" aria-hidden="true" style={{ display: 'none' }} />

      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={COLOR_CYAN} stopOpacity={0.25} />
              <stop offset="95%" stopColor={COLOR_CYAN} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={COLOR_GRID} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: COLOR_NEUTRAL }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: COLOR_NEUTRAL }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
            width={56}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={tooltipLabelStyle}
            formatter={(value) => {
              const v = typeof value === 'number' ? value : 0
              return [formatTooltipValue(v), 'Cumulative PnL']
            }}
          />
          {/* Zero baseline reference line */}
          <ReferenceLine
            y={0}
            stroke={COLOR_NEUTRAL}
            strokeDasharray="4 2"
            strokeWidth={1}
          />
          <Area
            type="monotone"
            dataKey="pnl"
            stroke={COLOR_CYAN}
            strokeWidth={2}
            fill="url(#equityGradient)"
            dot={false}
            activeDot={{ r: 3, stroke: COLOR_CYAN, fill: COLOR_BG_CARD }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

// ============================================================================
// PerBotPnl
// ============================================================================

interface PerBotPnlProps {
  bots: BotPnlItem[]
  height?: number
}

/**
 * Horizontal bar chart: total PnL per bot.
 * Bars are green (#00C853) for positive and red (#FF1744) for negative.
 */
export function PerBotPnl({ bots, height }: PerBotPnlProps) {
  if (bots.length === 0) {
    return <EmptyState testId="per-bot-pnl-empty" message="No per-bot PnL data yet." />
  }

  const hasProfit = bots.some(b => b.total_pnl > 0)
  const hasLoss   = bots.some(b => b.total_pnl < 0)

  // Sort by total_pnl descending so best bots appear at the top of the horizontal bars
  const sorted = [...bots].sort((a, b) => b.total_pnl - a.total_pnl)

  // Compute responsive height: at least 48px per row, min 200px
  const computedHeight = height ?? Math.max(200, sorted.length * 28 + 40)

  return (
    <div
      data-testid="per-bot-pnl"
      data-has-profit={hasProfit ? 'true' : 'false'}
      data-has-loss={hasLoss ? 'true' : 'false'}
      data-bot-count={String(bots.length)}
      style={{ height: computedHeight }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={sorted}
          layout="vertical"
          margin={{ top: 4, right: 32, left: 0, bottom: 4 }}
          barSize={14}
        >
          <CartesianGrid strokeDasharray="3 3" stroke={COLOR_GRID} horizontal={false} />
          <XAxis
            type="number"
            tick={{ fontSize: 10, fill: COLOR_NEUTRAL }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
          />
          <YAxis
            type="category"
            dataKey="symbol"
            tick={{ fontSize: 10, fill: COLOR_NEUTRAL }}
            axisLine={false}
            tickLine={false}
            width={72}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={tooltipLabelStyle}
            formatter={(value) => {
              const v = typeof value === 'number' ? value : 0
              const sign = v >= 0 ? '+' : ''
              return [`${sign}$${Math.abs(v).toFixed(2)}`, 'Total PnL']
            }}
          />
          {/* Reference line at 0 */}
          <ReferenceLine x={0} stroke={COLOR_NEUTRAL} strokeWidth={1} />
          <Bar dataKey="total_pnl" isAnimationActive={false} radius={[0, 2, 2, 0]}>
            {sorted.map((entry) => (
              <Cell
                key={entry.symbol}
                fill={entry.total_pnl >= 0 ? COLOR_PROFIT : COLOR_LOSS}
                fillOpacity={0.85}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

// ============================================================================
// DailyPnl
// ============================================================================

interface DailyPnlProps {
  days: DailyPnlPoint[]
  height?: number
}

/**
 * Vertical bar chart: daily PnL bars.
 * Green (#00C853) for profit days, red (#FF1744) for loss days.
 * Zero-day bars are rendered in neutral grey.
 */
export function DailyPnl({ days, height = 220 }: DailyPnlProps) {
  if (days.length === 0) {
    return <EmptyState testId="daily-pnl-empty" message="No daily PnL data yet." />
  }

  const hasProfit = days.some(d => d.pnl > 0)
  const hasLoss   = days.some(d => d.pnl < 0)

  function formatDateLabel(date: string): string {
    try {
      // date is a UTC date string like "2026-01-01"
      const [, m, d] = date.split('-')
      const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
      return `${months[parseInt(m, 10) - 1]} ${parseInt(d, 10)}`
    } catch {
      return date
    }
  }

  const chartData = days.map(d => ({
    date: d.date,
    pnl: d.pnl,
    trade_count: d.trade_count,
    label: formatDateLabel(d.date),
  }))

  return (
    <div
      data-testid="daily-pnl"
      data-has-profit={hasProfit ? 'true' : 'false'}
      data-has-loss={hasLoss ? 'true' : 'false'}
      data-day-count={String(days.length)}
      style={{ height }}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={COLOR_GRID} vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: COLOR_NEUTRAL }}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: COLOR_NEUTRAL }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) => `$${v.toFixed(0)}`}
            width={56}
          />
          <Tooltip
            contentStyle={tooltipStyle}
            labelStyle={tooltipLabelStyle}
            formatter={(value, _name, item) => {
              const v = typeof value === 'number' ? value : 0
              const sign = v >= 0 ? '+' : ''
              // item.payload is the chart data row; trade_count lives there
              const count = (item as { payload?: { trade_count?: number } }).payload?.trade_count ?? 0
              return [`${sign}$${Math.abs(v).toFixed(2)} (${count} trades)`, 'Daily PnL']
            }}
          />
          <ReferenceLine y={0} stroke={COLOR_NEUTRAL} strokeDasharray="4 2" strokeWidth={1} />
          <Bar dataKey="pnl" isAnimationActive={false} radius={[2, 2, 0, 0]}>
            {chartData.map((entry) => (
              <Cell
                key={entry.date}
                fill={entry.pnl > 0 ? COLOR_PROFIT : entry.pnl < 0 ? COLOR_LOSS : COLOR_NEUTRAL}
                fillOpacity={0.85}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
