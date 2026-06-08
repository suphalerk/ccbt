/**
 * UpnlPanel — realtime unrealized PnL display.
 *
 * Renders the latest uPnL data from api/markprice.py via the /ws stream.
 * Python computes all values; this component only renders.
 *
 * Feed status indicators:
 *  live    — mark prices updating in real-time
 *  stale   — feed connected but no tick for >15s (amber warning)
 *  offline — WS disconnected / reconnecting (grey, last-known values shown)
 */
import type { WSUpnlData, FeedStatus } from '../ws-types'

interface UpnlPanelProps {
  /** null before the first upnl message is received */
  upnl: WSUpnlData | null
}

const FEED_STATUS_CONFIG: Record<FeedStatus, { label: string; dotClass: string; textClass: string }> = {
  live: {
    label: 'Mark live',
    dotClass: 'bg-emerald-400',
    textClass: 'text-emerald-400',
  },
  stale: {
    label: 'Mark stale',
    dotClass: 'bg-yellow-400 animate-pulse',
    textClass: 'text-yellow-400',
  },
  offline: {
    label: 'Mark offline',
    dotClass: 'bg-slate-500',
    textClass: 'text-slate-500',
  },
}

function fmt(v: number): string {
  const sign = v >= 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}`
}

function upnlClass(v: number): string {
  return v >= 0 ? 'text-emerald-400' : 'text-red-400'
}

export function UpnlPanel({ upnl }: UpnlPanelProps) {
  // While waiting for the first message, show a loading skeleton
  if (upnl === null) {
    return (
      <div
        data-testid="upnl-panel-loading"
        className="bg-[#1A1F2E] rounded-lg border border-[#2A3245] px-4 py-3 flex items-center gap-2 text-xs text-slate-500"
      >
        <span className="w-2 h-2 rounded-full bg-slate-600 animate-pulse" />
        Waiting for mark prices…
      </div>
    )
  }

  const feedCfg = FEED_STATUS_CONFIG[upnl.feed_status]
  const hasPositions = upnl.positions.length > 0

  return (
    <div data-testid="upnl-panel" className="bg-[#1A1F2E] rounded-lg border border-[#2A3245] px-4 py-3">
      {/* Header row */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">
          Unrealized PnL
        </span>
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${feedCfg.dotClass}`} aria-hidden="true" />
          <span className={`text-[10px] ${feedCfg.textClass}`}>{feedCfg.label}</span>
          {upnl.feed_status !== 'live' && (
            <span className="text-[10px] text-slate-600">(last known)</span>
          )}
        </div>
      </div>

      {/* Total uPnL — big number */}
      <div
        data-testid="upnl-total"
        className={`text-2xl font-semibold tabular-nums mb-3 ${upnlClass(upnl.total_upnl)}`}
      >
        {fmt(upnl.total_upnl)} USDT
      </div>

      {/* Per-position rows */}
      {hasPositions ? (
        <table className="w-full text-xs border-collapse">
          <thead>
            <tr className="border-b border-[#2A3245]">
              <th className="px-1 py-1 text-left font-medium text-slate-500">Symbol</th>
              <th className="px-1 py-1 text-left font-medium text-slate-500">Side</th>
              <th className="px-1 py-1 text-right font-medium text-slate-500">Mark</th>
              <th className="px-1 py-1 text-right font-medium text-slate-500">Entry</th>
              <th className="px-1 py-1 text-right font-medium text-slate-500">uPnL</th>
            </tr>
          </thead>
          <tbody>
            {upnl.positions.map((pos) => (
              <tr
                key={pos.symbol}
                data-testid={`upnl-row-${pos.symbol}`}
                className="border-b border-[#1E2530]"
              >
                <td className="px-1 py-1 font-medium text-slate-200">{pos.symbol}</td>
                <td className="px-1 py-1">
                  <span className={pos.side === 'long' || pos.side === 'buy' ? 'text-emerald-400' : 'text-red-400'}>
                    {pos.side.toUpperCase()}
                  </span>
                </td>
                <td className="px-1 py-1 text-right tabular-nums text-slate-300">
                  {pos.mark_price > 0 ? pos.mark_price.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '—'}
                </td>
                <td className="px-1 py-1 text-right tabular-nums text-slate-400">
                  {pos.entry_price > 0 ? pos.entry_price.toLocaleString('en-US', { maximumFractionDigits: 4 }) : '—'}
                </td>
                <td className={`px-1 py-1 text-right tabular-nums font-medium ${upnlClass(pos.upnl)}`}>
                  {fmt(pos.upnl)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-xs text-slate-500 italic">No open positions.</p>
      )}
    </div>
  )
}
