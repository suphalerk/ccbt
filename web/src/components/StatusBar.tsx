/**
 * N3 — Top status bar.
 * Shows WS connection status, last-updated time, and API health.
 */
import type { WsStatus } from '../hooks/useLiveSnapshot'

interface StatusBarProps {
  wsStatus: WsStatus
  lastUpdated: string | null
}

const WS_STATUS_CONFIG: Record<WsStatus, { label: string; color: string; dot: string }> = {
  connected: { label: 'Live', color: 'text-emerald-400', dot: 'bg-emerald-400' },
  connecting: { label: 'Connecting…', color: 'text-yellow-400', dot: 'bg-yellow-400 animate-pulse' },
  reconnecting: { label: 'Reconnecting…', color: 'text-yellow-400', dot: 'bg-yellow-400 animate-pulse' },
  closed: { label: 'Offline', color: 'text-red-500', dot: 'bg-red-500' },
}

export function StatusBar({ wsStatus, lastUpdated }: StatusBarProps) {
  const cfg = WS_STATUS_CONFIG[wsStatus]

  const formattedTime = lastUpdated
    ? new Date(lastUpdated).toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : null

  return (
    <header className="flex items-center justify-between px-4 h-10 bg-[#0E1117] border-b border-[#1E2530] shrink-0">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">CCBT Dashboard</span>
        <span className="text-xs text-slate-600">v2</span>
      </div>

      <div className="flex items-center gap-4 text-xs">
        {formattedTime && (
          <span className="text-slate-500">
            Updated {formattedTime}
          </span>
        )}

        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
          <span className={cfg.color}>{cfg.label}</span>
        </div>
      </div>
    </header>
  )
}
