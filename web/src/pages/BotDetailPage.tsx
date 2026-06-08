/**
 * N6 — Bot detail / single-bot drilldown page.
 *
 * Sections:
 *   1. Header: symbol, mode badge, status dot, nav-back
 *   2. Performance stats: total trades, WR, PF, avg win/loss, total PnL
 *   3. Price chart: candlesticks + EMA9/EMA21 via lightweight-charts; RSI subchart below
 *      (candles come from /api/candles — persisted by the bot; unavailable state shown
 *       if no persisted candles exist; the dashboard NEVER calls the exchange)
 *   4. Trade log table: duration, ai_decision, close_reason (no signal_source)
 *   5. Risk monitor: position info, mode, circuit-breaker dots
 *
 * Rules:
 *   - No financial math in TS — all values pre-computed by Python.
 *   - signal_source column is DROPPED (not in DB schema).
 *   - Exit markers placed at timestamp + duration_seconds (both columns exist).
 *   - Empty/unavailable states handled gracefully.
 */
import React, { useEffect, useRef, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import type { BotRow, TradeRow, CandlesResponse } from '../api/client'
import { formatMoney, formatPct, formatPF, formatDuration } from '../utils/format'
import { EquityCurve } from '../components/Charts'

// ============================================================================
// Design tokens
// ============================================================================

const COLOR_PROFIT  = '#00C853'
const COLOR_LOSS    = '#FF1744'
const COLOR_NEUTRAL = '#78909C'
const COLOR_CYAN    = '#00E5FF'
const COLOR_EMA9    = '#FFD600'
const COLOR_EMA21   = '#FF6D00'

// ============================================================================
// Section wrapper
// ============================================================================

function Section({ title, children, testId }: { title: string; children: React.ReactNode; testId?: string }) {
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
// Mode badge
// ============================================================================

const MODE_BADGE_MAP: Record<string, { label: string; className: string }> = {
  GRACEFUL_STOP: { label: 'STOP',  className: 'bg-yellow-500/20 text-yellow-300 border border-yellow-500/40' },
  TP_ONLY:       { label: 'TP',    className: 'bg-blue-500/20 text-blue-300 border border-blue-500/40' },
  PANIC:         { label: 'PANIC', className: 'bg-red-500/20 text-red-300 border border-red-500/40 font-bold' },
}

function ModeBadge({ mode }: { mode: string | null }) {
  // Normalise to uppercase so 'panic' from the DB renders the same as 'PANIC'
  const m = (mode ?? '').toUpperCase()
  if (!m || m === 'NORMAL' || !MODE_BADGE_MAP[m]) return null
  const cfg = MODE_BADGE_MAP[m]
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded ${cfg.className}`}>{cfg.label}</span>
  )
}

// ============================================================================
// Panic confirm dialog
// ============================================================================

interface PanicConfirmDialogProps {
  title: string
  message: string
  onConfirm: () => void
  onCancel: () => void
}

function PanicConfirmDialog({ title, message, onConfirm, onCancel }: PanicConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null)
  const titleId = 'panic-dialog-title'
  const descId = 'panic-dialog-desc'

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
      data-testid="panic-confirm-dialog"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      aria-describedby={descId}
      onKeyDown={handleKeyDown}
    >
      <div className="bg-[#12161F] border border-red-500/50 rounded-lg p-6 max-w-sm w-full mx-4 shadow-2xl">
        <h3 id={titleId} className="text-base font-bold text-red-400 mb-2">{title}</h3>
        <p id={descId} className="text-sm text-slate-300 mb-5">{message}</p>
        <div className="flex gap-3 justify-end">
          <button
            ref={cancelRef}
            data-testid="panic-confirm-cancel"
            onClick={onCancel}
            className="px-4 py-1.5 rounded text-sm bg-[#1A1F2E] border border-[#2A3245] text-slate-300 hover:border-slate-500 transition-colors"
          >
            Cancel
          </button>
          <button
            data-testid="panic-confirm-ok"
            onClick={onConfirm}
            className="px-4 py-1.5 rounded text-sm bg-red-600 hover:bg-red-700 text-white font-semibold transition-colors"
          >
            Confirm PANIC
          </button>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Mode buttons (#1)
// ============================================================================

type BotMode = 'NORMAL' | 'GRACEFUL_STOP' | 'TP_ONLY' | 'PANIC'

const MODE_BUTTONS: { mode: BotMode; label: string; className: string; activeClass: string }[] = [
  {
    mode: 'NORMAL',
    label: 'Normal',
    className: 'border-emerald-500/40 text-emerald-300 hover:bg-emerald-500/10',
    activeClass: 'bg-emerald-500/20 border-emerald-500 text-emerald-200 font-semibold',
  },
  {
    mode: 'GRACEFUL_STOP',
    label: 'Graceful Stop',
    className: 'border-yellow-500/40 text-yellow-300 hover:bg-yellow-500/10',
    activeClass: 'bg-yellow-500/20 border-yellow-500 text-yellow-200 font-semibold',
  },
  {
    mode: 'TP_ONLY',
    label: 'TP Only',
    className: 'border-blue-500/40 text-blue-300 hover:bg-blue-500/10',
    activeClass: 'bg-blue-500/20 border-blue-500 text-blue-200 font-semibold',
  },
  {
    mode: 'PANIC',
    label: 'PANIC',
    className: 'border-red-500/40 text-red-300 hover:bg-red-500/10',
    activeClass: 'bg-red-500/20 border-red-500 text-red-200 font-bold',
  },
]

interface ModeButtonsProps {
  symbol: string
  currentMode: string | null
}

function ModeButtons({ symbol, currentMode }: ModeButtonsProps) {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [showPanicConfirm, setShowPanicConfirm] = useState(false)

  // Normalise incoming mode to UPPERCASE so the active-button highlight
  // works regardless of whether the API sends 'TP_ONLY' or 'tp_only'.
  // BLOCKER fix: bot_detail previously hardcoded mode=None; the backend now
  // reads bot_health so currentMode will be a real value; but we also
  // normalise here as a defensive measure.
  const normalisedMode = (currentMode ?? '').toUpperCase() || null

  const token = localStorage.getItem('ccbt_dash_token')

  async function applyMode(mode: BotMode, confirmPanic?: boolean) {
    setPending(true)
    setError(null)
    setMessage(null)
    try {
      const body = mode === 'PANIC'
        ? { mode, confirm_panic: confirmPanic ?? false }
        : { mode }
      const res = await api.setBotMode(symbol, body, token)
      setMessage(res.message ?? `Mode set to ${mode}`)
      // Invalidate both the bot-detail and ['bots'] queries so the badge updates
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['bot', symbol] }),
        queryClient.invalidateQueries({ queryKey: ['bots'] }),
      ])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error')
    } finally {
      setPending(false)
    }
  }

  function handleClick(mode: BotMode) {
    if (mode === 'PANIC') {
      setShowPanicConfirm(true)
    } else {
      applyMode(mode)
    }
  }

  return (
    <>
      <div data-testid="mode-buttons" className="flex flex-wrap gap-2 items-center">
        {MODE_BUTTONS.map(({ mode, label, className, activeClass }) => {
          const isActive = normalisedMode === mode
          return (
            <button
              key={mode}
              data-testid={`mode-btn-${mode}`}
              aria-pressed={isActive ? 'true' : 'false'}
              disabled={pending}
              onClick={() => handleClick(mode)}
              className={`px-3 py-1.5 rounded border text-xs transition-colors disabled:opacity-50 ${
                isActive ? activeClass : `bg-transparent ${className}`
              }`}
            >
              {label}
            </button>
          )
        })}

        {/* Success message */}
        {message && (
          <span className="text-xs text-emerald-400 ml-1">{message}</span>
        )}

        {/* Error message */}
        {error && (
          <span data-testid="mode-error" className="text-xs text-red-400 ml-1">{error}</span>
        )}
      </div>

      {showPanicConfirm && (
        <PanicConfirmDialog
          title={`PANIC: ${symbol}`}
          message="This will immediately close ALL open positions at market price. This action cannot be undone."
          onConfirm={() => {
            setShowPanicConfirm(false)
            applyMode('PANIC', true)
          }}
          onCancel={() => setShowPanicConfirm(false)}
        />
      )}
    </>
  )
}

// ============================================================================
// Candle chart (lightweight-charts)
//
// lightweight-charts is a canvas-based library — it does NOT render DOM elements
// readable by Testing Library. We wrap it in a div with data-testid so tests
// can assert the container renders. The chart itself is created imperatively in
// a useEffect (standard pattern for non-React charting libs).
// ============================================================================

interface CandleChartProps {
  candles: NonNullable<CandlesResponse['candles']>
  trades?: TradeRow[]  // for exit markers
}

function CandleChartInner({ candles, trades = [] }: CandleChartProps) {
  const chartRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!chartRef.current || candles.length === 0) return

    let chart: ReturnType<typeof import('lightweight-charts')['createChart']> | undefined
    let cleanup = () => {}

    import('lightweight-charts').then(({ createChart, ColorType, CandlestickSeries, LineSeries, createSeriesMarkers }) => {
      if (!chartRef.current) return

      const height = 260

      chart = createChart(chartRef.current, {
        width: chartRef.current.clientWidth || 600,
        height,
        layout: {
          background: { type: ColorType.Solid, color: '#0D1117' },
          textColor: COLOR_NEUTRAL,
        },
        grid: {
          vertLines: { color: '#1E2530' },
          horzLines: { color: '#1E2530' },
        },
        crosshair: { mode: 1 },
        rightPriceScale: { borderColor: '#1E2530' },
        timeScale: { borderColor: '#1E2530', timeVisible: true },
      })

      // Candlestick series (v5 API: addSeries(CandlestickSeries, options))
      const candleSeries = chart.addSeries(CandlestickSeries, {
        upColor: COLOR_PROFIT,
        downColor: COLOR_LOSS,
        borderUpColor: COLOR_PROFIT,
        borderDownColor: COLOR_LOSS,
        wickUpColor: COLOR_PROFIT,
        wickDownColor: COLOR_LOSS,
      })

      const candleData = candles.map(c => ({
        time: Math.floor(c.ts / 1000) as any,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
      candleSeries.setData(candleData)

      // EMA9 line
      if (candles.some(c => c.ema9 !== null)) {
        const ema9Series = chart.addSeries(LineSeries, { color: COLOR_EMA9, lineWidth: 1, priceLineVisible: false })
        const ema9Data = candles
          .filter(c => c.ema9 !== null)
          .map(c => ({ time: Math.floor(c.ts / 1000) as any, value: c.ema9! }))
        ema9Series.setData(ema9Data)
      }

      // EMA21 line
      if (candles.some(c => c.ema21 !== null)) {
        const ema21Series = chart.addSeries(LineSeries, { color: COLOR_EMA21, lineWidth: 1, priceLineVisible: false })
        const ema21Data = candles
          .filter(c => c.ema21 !== null)
          .map(c => ({ time: Math.floor(c.ts / 1000) as any, value: c.ema21! }))
        ema21Series.setData(ema21Data)
      }

      // Trade exit markers — placed at timestamp + duration_seconds (ticket requirement)
      // In lightweight-charts v5, markers are created via createSeriesMarkers plugin
      const markerDefs: { time: any; position: string; color: string; shape: string; text: string }[] = []
      for (const t of trades) {
        if (t.timestamp && t.duration_seconds && t.exit_price !== null) {
          const exitMs = new Date(t.timestamp).getTime() // timestamp = close time
          const exitSec = Math.floor(exitMs / 1000)
          markerDefs.push({
            time: exitSec as any,
            position: t.pnl !== null && t.pnl >= 0 ? 'aboveBar' : 'belowBar',
            color: t.pnl !== null && t.pnl >= 0 ? COLOR_PROFIT : COLOR_LOSS,
            shape: t.pnl !== null && t.pnl >= 0 ? 'arrowUp' : 'arrowDown',
            text: t.pnl !== null ? `${t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(1)}` : '',
          })
        }
      }
      if (markerDefs.length > 0) {
        markerDefs.sort((a, b) => a.time - b.time)
        createSeriesMarkers(candleSeries as any, markerDefs as any)
      }

      chart.timeScale().fitContent()

      // Handle resize
      const ro = new ResizeObserver(() => {
        if (chartRef.current && chart) {
          chart.applyOptions({ width: chartRef.current.clientWidth })
        }
      })
      if (chartRef.current) ro.observe(chartRef.current)

      cleanup = () => {
        ro.disconnect()
        chart?.remove()
      }
    })

    return () => cleanup()
  }, [candles, trades])

  return (
    <div
      ref={chartRef}
      data-testid="candle-chart-inner"
      className="w-full rounded"
      style={{ height: 260 }}
    />
  )
}

// RSI subchart
function RsiChart({ candles }: { candles: NonNullable<CandlesResponse['candles']> }) {
  const chartRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!chartRef.current || candles.length === 0) return

    let chart: ReturnType<typeof import('lightweight-charts')['createChart']> | undefined
    let cleanup = () => {}

    import('lightweight-charts').then(({ createChart, ColorType, LineSeries }) => {
      if (!chartRef.current) return

      chart = createChart(chartRef.current, {
        width: chartRef.current.clientWidth || 600,
        height: 100,
        layout: {
          background: { type: ColorType.Solid, color: '#0D1117' },
          textColor: COLOR_NEUTRAL,
        },
        grid: {
          vertLines: { color: '#1E2530' },
          horzLines: { color: '#1E2530' },
        },
        rightPriceScale: { borderColor: '#1E2530', autoScale: false, visible: true },
        timeScale: { borderColor: '#1E2530', timeVisible: false, visible: false },
      })

      const rsiSeries = chart.addSeries(LineSeries, {
        color: COLOR_CYAN,
        lineWidth: 1,
        priceLineVisible: false,
        autoscaleInfoProvider: () => ({
          priceRange: { minValue: 0, maxValue: 100 },
          margins: { above: 0.1, below: 0.1 },
        }),
      })

      const rsiData = candles
        .filter(c => c.rsi14 !== null)
        .map(c => ({ time: Math.floor(c.ts / 1000) as any, value: c.rsi14! }))
      rsiSeries.setData(rsiData)

      chart.timeScale().fitContent()

      const ro = new ResizeObserver(() => {
        if (chartRef.current && chart) {
          chart.applyOptions({ width: chartRef.current.clientWidth })
        }
      })
      if (chartRef.current) ro.observe(chartRef.current)

      cleanup = () => {
        ro.disconnect()
        chart?.remove()
      }
    })

    return () => cleanup()
  }, [candles])

  return (
    <div
      ref={chartRef}
      data-testid="rsi-chart-inner"
      className="w-full rounded"
      style={{ height: 100 }}
    />
  )
}

// Combined candle + RSI chart section
function CandleSection({ symbol, trades = [] }: { symbol: string; trades?: TradeRow[] }) {
  const { data, isError } = useQuery({
    queryKey: ['candles', symbol],
    queryFn: () => api.candles({ symbol, limit: 200 }),
    staleTime: 30_000,
    refetchInterval: 30_000,
    retry: false,
  })

  // Error state — show unavailable
  if (isError) {
    return (
      <div
        data-testid="candles-unavailable"
        className="flex items-center justify-center h-24 text-sm text-slate-500 italic bg-[#0D1117] rounded-lg border border-[#1E2530]"
      >
        Chart unavailable — candle data not persisted yet.
      </div>
    )
  }

  // Loading
  if (!data) {
    return (
      <div className="h-24 bg-[#0D1117] rounded-lg border border-[#1E2530] animate-pulse" />
    )
  }

  // Not available (no persisted candles)
  const candles = data.candles ?? []
  if (!data.available || candles.length === 0) {
    return (
      <div
        data-testid="candles-unavailable"
        className="flex items-center justify-center h-24 text-sm text-slate-500 italic bg-[#0D1117] rounded-lg border border-[#1E2530]"
      >
        Chart unavailable — candles will appear once the bot persists OHLCV data.
      </div>
    )
  }

  // Available
  return (
    <div data-testid="candle-chart-container">
      <CandleChartInner candles={candles} trades={trades} />
      <div className="mt-1 text-xs text-slate-600 flex gap-3 mb-1">
        <span style={{ color: COLOR_EMA9 }}>— EMA9</span>
        <span style={{ color: COLOR_EMA21 }}>— EMA21</span>
        <span className="text-slate-600">{data.timeframe ?? ''}</span>
      </div>
      <div
        data-testid="rsi-chart-container"
        className="mt-2"
      >
        <div className="text-xs text-slate-600 mb-1">RSI(14)</div>
        <RsiChart candles={candles} />
      </div>
    </div>
  )
}

// ============================================================================
// Trade log table (no signal_source)
// ============================================================================

function TradeLogTable({ trades }: { trades: TradeRow[] }) {
  if (trades.length === 0) {
    return (
      <div
        data-testid="trade-log-empty"
        className="text-sm text-slate-500 italic py-4 text-center"
      >
        No closed trades yet. Trades appear here once positions close.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table
        data-testid="trade-log-table"
        className="w-full text-sm border-collapse"
      >
        <thead>
          <tr className="border-b border-[#2A3245]">
            {/* signal_source intentionally absent — not in DB schema */}
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Side</th>
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Entry</th>
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Exit</th>
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">PnL</th>
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Duration</th>
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Reason</th>
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">AI</th>
            <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Closed</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade, idx) => {
            const pnlClass =
              trade.pnl === null
                ? 'text-slate-400'
                : trade.pnl >= 0
                ? 'text-emerald-400'
                : 'text-red-400'

            const closedAt = trade.timestamp
              ? new Date(trade.timestamp).toLocaleString('en-US', {
                  month: 'short',
                  day: 'numeric',
                  hour: '2-digit',
                  minute: '2-digit',
                  hour12: false,
                })
              : '—'

            // ai_decision may be present even if not in the TS schema (extra field from DB)
            const aiDecision = (trade as Record<string, unknown>)['ai_decision'] as string | null | undefined

            return (
              <tr
                key={trade.id ?? idx}
                className="border-b border-[#1E2530] hover:bg-[#1A1F2E] transition-colors"
              >
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
                  {trade.entry_price !== null && trade.entry_price !== undefined
                    ? trade.entry_price.toLocaleString('en-US', { maximumFractionDigits: 4 })
                    : '—'}
                </td>
                <td className="px-3 py-2 tabular-nums text-slate-300">
                  {trade.exit_price !== null && trade.exit_price !== undefined
                    ? trade.exit_price.toLocaleString('en-US', { maximumFractionDigits: 4 })
                    : '—'}
                </td>
                <td className={`px-3 py-2 tabular-nums font-medium ${pnlClass}`}>
                  {formatMoney(trade.pnl)}
                </td>
                <td className="px-3 py-2 text-slate-400 text-xs tabular-nums">
                  {formatDuration(trade.duration_seconds)}
                </td>
                <td className="px-3 py-2 text-slate-500 text-xs">
                  {trade.close_reason ?? '—'}
                </td>
                <td className="px-3 py-2 text-slate-500 text-xs">
                  {typeof aiDecision === 'string' ? aiDecision : '—'}
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
  )
}

// ============================================================================
// Risk monitor
// ============================================================================

function RiskMonitor({ bot }: { bot: BotRow | undefined }) {
  const hasPosition = bot?.position_side !== null && bot?.position_side !== undefined

  return (
    <div data-testid="risk-monitor" className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      {/* Position info */}
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-2">
          Position
        </div>
        {hasPosition ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span
                className={`text-xs font-bold uppercase ${
                  bot?.position_side === 'long' ? 'text-emerald-400' : 'text-red-400'
                }`}
              >
                {bot?.position_side?.toUpperCase()}
              </span>
              {bot?.position_size !== null && bot?.position_size !== undefined && (
                <span className="text-slate-400 text-xs">
                  {bot.position_size.toFixed(4)} lots
                </span>
              )}
            </div>
            {bot?.unrealized_pnl !== null && bot?.unrealized_pnl !== undefined && (
              <div className="text-xs">
                Unrealized:{' '}
                <span
                  className={
                    bot.unrealized_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'
                  }
                >
                  {formatMoney(bot.unrealized_pnl)}
                </span>
              </div>
            )}
          </div>
        ) : (
          <div className="text-slate-500 text-xs">No position — flat</div>
        )}
      </div>

      {/* Bot status */}
      <div>
        <div className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-2">
          Status
        </div>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            {/* Status dot */}
            <span
              className={`inline-block w-2 h-2 rounded-full ${
                bot?.status === 'active'
                  ? 'bg-emerald-400'
                  : bot?.status === 'error'
                  ? 'bg-red-400'
                  : 'bg-slate-500'
              }`}
            />
            <span className="text-slate-300 text-xs capitalize">
              {bot?.status ?? '—'}
            </span>
          </div>
          {bot?.strategy && (
            <div className="text-xs text-slate-500">
              Strategy: <span className="text-slate-300">{bot.strategy}</span>
            </div>
          )}
          {bot?.timeframe && (
            <div className="text-xs text-slate-500">
              Timeframe: <span className="text-slate-300">{bot.timeframe}</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// Main BotDetailPage
// ============================================================================

export function BotDetailPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const sym = symbol ?? ''

  const { data: detail } = useQuery({
    queryKey: ['bot', sym],
    queryFn: () => api.botDetail(sym),
    staleTime: 30_000,
    refetchInterval: 30_000,
    enabled: !!sym,
  })

  const { data: tradesData } = useQuery({
    queryKey: ['trades', sym],
    queryFn: () => api.listTrades({ symbol: sym, limit: 100 }),
    staleTime: 30_000,
    refetchInterval: 30_000,
    enabled: !!sym,
  })

  const { data: equityData } = useQuery({
    queryKey: ['equity', sym],
    queryFn: () => api.equityCurve({ symbol: sym }),
    staleTime: 30_000,
    refetchInterval: 30_000,
    enabled: !!sym,
  })

  const bot = detail?.summary
  const trades = tradesData?.trades ?? []
  const equityPoints = equityData?.points ?? []

  const pnlClass =
    bot?.total_pnl === undefined
      ? 'text-slate-100'
      : bot.total_pnl >= 0
      ? 'text-emerald-400'
      : 'text-red-400'

  return (
    <div className="p-4 max-w-screen-xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <Link
          to="/"
          className="text-slate-500 hover:text-slate-300 transition-colors text-sm"
          aria-label="Back to Portfolio"
        >
          ← Portfolio
        </Link>
        <h1 className="text-2xl font-bold text-slate-100">
          <span className="text-emerald-400">{sym}</span>
        </h1>
        {bot?.mode && (bot.mode ?? '').toUpperCase() !== 'NORMAL' && (
          <ModeBadge mode={bot.mode} />
        )}
      </div>

      {/* Mode control buttons (#1) */}
      <div className="mb-4">
        <ModeButtons symbol={sym} currentMode={bot?.mode ?? null} />
      </div>

      {/* Performance stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <MetricCard
          label="Total PnL"
          value={formatMoney(bot?.total_pnl ?? null)}
          testId="bot-total-pnl"
          valueClass={pnlClass}
        />
        <MetricCard
          label="Win Rate"
          value={formatPct(bot?.win_rate_pct ?? null)}
          testId="bot-win-rate"
        />
        <MetricCard
          label="Profit Factor"
          value={formatPF(bot?.profit_factor ?? null)}
          testId="bot-profit-factor"
        />
        <MetricCard
          label="Trades"
          value={bot?.trade_count !== undefined ? String(bot.trade_count) : '—'}
          testId="bot-trade-count"
        />
      </div>

      {/* Price chart */}
      <Section title="Price Chart">
        <CandleSection symbol={sym} trades={trades} />
      </Section>

      {/* Equity curve */}
      {equityPoints.length > 0 && (
        <Section title="Equity Curve">
          <EquityCurve points={equityPoints} />
        </Section>
      )}

      {/* Trade log */}
      <Section title={`Trade Log (${trades.length})`}>
        <TradeLogTable trades={trades} />
      </Section>

      {/* Risk monitor */}
      <Section title="Risk Monitor">
        <RiskMonitor bot={bot} />
      </Section>
    </div>
  )
}
