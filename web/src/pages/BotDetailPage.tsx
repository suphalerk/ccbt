/**
 * N3 — Bot detail page stub.
 * Real content added in N6 (single-bot drilldown).
 */
import { useParams } from 'react-router-dom'

export function BotDetailPage() {
  const { symbol } = useParams<{ symbol: string }>()

  return (
    <div className="p-6">
      <h1 className="text-2xl font-bold text-slate-100 mb-4">
        Bot: <span className="text-emerald-400">{symbol}</span>
      </h1>
      <p className="text-slate-400">
        Single-bot drilldown panels coming in N6.
      </p>
    </div>
  )
}
