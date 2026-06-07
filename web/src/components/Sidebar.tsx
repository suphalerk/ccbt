/**
 * N3 — App sidebar.
 * Displays "Portfolio" link + live bot list from /api/bots.
 * Hydrated from TanStack Query (populated by useLiveSnapshot WS hook or REST).
 */
import { NavLink } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export function Sidebar() {
  const { data, isLoading } = useQuery({
    queryKey: ['bots'],
    queryFn: () => api.listBots(),
    staleTime: 30_000,
    refetchInterval: 30_000,
  })

  const bots = data?.bots ?? []

  return (
    <nav
      className="flex flex-col w-48 shrink-0 bg-[#0E1117] border-r border-[#1E2530] h-full overflow-y-auto"
      aria-label="Bot navigation"
    >
      <div className="px-3 py-4 border-b border-[#1E2530]">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          CCBT v2
        </span>
      </div>

      <ul className="flex flex-col gap-0.5 p-2">
        <li>
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded text-sm transition-colors ${
                isActive
                  ? 'bg-[#1E2530] text-emerald-400 font-medium'
                  : 'text-slate-300 hover:bg-[#1E2530] hover:text-slate-100'
              }`
            }
          >
            <span className="text-base">▤</span>
            Portfolio
          </NavLink>
        </li>
        <li>
          <NavLink
            to="/ai"
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded text-sm transition-colors ${
                isActive
                  ? 'bg-[#1E2530] text-emerald-400 font-medium'
                  : 'text-slate-300 hover:bg-[#1E2530] hover:text-slate-100'
              }`
            }
          >
            <span className="text-base">&#129302;</span>
            AI Analytics
          </NavLink>
        </li>
        <li>
          <NavLink
            to="/logs"
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded text-sm transition-colors ${
                isActive
                  ? 'bg-[#1E2530] text-emerald-400 font-medium'
                  : 'text-slate-300 hover:bg-[#1E2530] hover:text-slate-100'
              }`
            }
          >
            <span className="text-base">&#128220;</span>
            Live Logs
          </NavLink>
        </li>
        <li>
          <NavLink
            to="/panels"
            className={({ isActive }) =>
              `flex items-center gap-2 px-3 py-2 rounded text-sm transition-colors ${
                isActive
                  ? 'bg-[#1E2530] text-emerald-400 font-medium'
                  : 'text-slate-300 hover:bg-[#1E2530] hover:text-slate-100'
              }`
            }
          >
            <span className="text-base">&#128202;</span>
            Analytics
          </NavLink>
        </li>
      </ul>

      <div className="px-3 py-2 border-t border-[#1E2530] mt-1">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          Bots {isLoading ? '…' : `(${bots.length})`}
        </span>
      </div>

      <ul className="flex flex-col gap-0.5 p-2 flex-1">
        {bots.map((bot) => (
          <li key={bot.symbol}>
            <NavLink
              to={`/bots/${bot.symbol}`}
              className={({ isActive }) =>
                `flex items-center gap-2 px-3 py-1.5 rounded text-sm transition-colors ${
                  isActive
                    ? 'bg-[#1E2530] text-emerald-400 font-medium'
                    : 'text-slate-400 hover:bg-[#1E2530] hover:text-slate-200'
                }`
              }
            >
              {/* Status dot */}
              <span
                className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  bot.status === 'active'
                    ? 'bg-emerald-400'
                    : bot.status === 'error'
                    ? 'bg-red-500'
                    : 'bg-slate-600'
                }`}
              />
              <span className="truncate text-xs">{bot.symbol}</span>
            </NavLink>
          </li>
        ))}

        {!isLoading && bots.length === 0 && (
          <li className="px-3 py-2 text-xs text-slate-600 italic">
            No bots yet
          </li>
        )}
      </ul>
    </nav>
  )
}
