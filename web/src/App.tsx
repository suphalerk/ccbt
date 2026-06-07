/**
 * N3 — CCBT Dashboard v2 App shell.
 *
 * Layout: sidebar (bot list) + top status bar + content area with client routing.
 *   /              → PortfolioPage
 *   /bots/:symbol  → BotDetailPage
 *
 * WS live updates via useLiveSnapshot — hydrates TanStack Query cache;
 * components just useQuery as normal and get live data automatically.
 */
import { Routes, Route } from 'react-router-dom'
import './index.css'
import { Sidebar } from './components/Sidebar'
import { StatusBar } from './components/StatusBar'
import { PortfolioPage } from './pages/PortfolioPage'
import { BotDetailPage } from './pages/BotDetailPage'
import { AIAnalyticsPage } from './pages/AIAnalyticsPage'
import { LogViewerPage } from './pages/LogViewerPage'
import { NewPanelsPage } from './pages/NewPanelsPage'
import { useLiveSnapshot } from './hooks/useLiveSnapshot'

function App() {
  const { status: wsStatus, lastUpdated } = useLiveSnapshot()

  return (
    <div className="flex flex-col h-screen bg-[#0E1117] text-slate-100 overflow-hidden">
      {/* Top status bar */}
      <StatusBar wsStatus={wsStatus} lastUpdated={lastUpdated} />

      {/* Main content area: sidebar + page */}
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />

        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<PortfolioPage />} />
            <Route path="/bots/:symbol" element={<BotDetailPage />} />
            <Route path="/ai" element={<AIAnalyticsPage />} />
            <Route path="/logs" element={<LogViewerPage />} />
            <Route path="/panels" element={<NewPanelsPage />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default App
