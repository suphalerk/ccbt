import './index.css'

/**
 * CCBT Dashboard v2 — scaffold (N0)
 *
 * Real pages land in N3 (portfolio), N4 (bot detail), N6 (charts), N7 (AI/logs).
 * This stub confirms Vite + Tailwind + React render correctly.
 */
function App() {
  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-8">
      <header className="mb-8">
        <h1 className="text-3xl font-bold text-emerald-400">CCBT Dashboard v2</h1>
        <p className="text-slate-400 mt-1">Crypto trading bot dashboard — scaffold (N0)</p>
      </header>

      <main className="grid gap-6">
        <section className="rounded-lg border border-slate-700 bg-slate-800 p-6">
          <h2 className="text-lg font-semibold text-slate-200 mb-2">Status</h2>
          <p className="text-slate-400">
            FastAPI backend + WebSocket integration coming in N1 / N2.
          </p>
        </section>
      </main>
    </div>
  )
}

export default App
