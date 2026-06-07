/**
 * N3 App shell test — confirms the app shell renders with routing, sidebar,
 * and status bar without errors.
 * (Replaces the N0 scaffold stub test which tested a now-removed stub component.)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

// Mock the WS hook so tests don't actually open a WebSocket
vi.mock('./hooks/useLiveSnapshot', () => ({
  useLiveSnapshot: () => ({ status: 'connecting', snapshot: null, lastUpdated: null }),
}))

// Mock the API client so tests don't make fetch calls
vi.mock('./api/client', () => ({
  api: {
    listBots: () => Promise.resolve({ bots: [] }),
  },
}))

function makeWrapper(initialEntry = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  // App already wraps BrowserRouter — use MemoryRouter wrapper via a custom render
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialEntry]}>
          {children}
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

// Re-export App without its own BrowserRouter for testing
// (The real App uses BrowserRouter from main.tsx; we wrap in MemoryRouter here)
import { Routes, Route } from 'react-router-dom'
import { Sidebar } from './components/Sidebar'
import { StatusBar } from './components/StatusBar'
import { PortfolioPage } from './pages/PortfolioPage'
import { BotDetailPage } from './pages/BotDetailPage'
import { useLiveSnapshot } from './hooks/useLiveSnapshot'

function TestApp() {
  const { status: wsStatus, lastUpdated } = useLiveSnapshot()
  return (
    <div className="flex flex-col h-screen">
      <StatusBar wsStatus={wsStatus} lastUpdated={lastUpdated} />
      <div className="flex flex-1">
        <Sidebar />
        <main>
          <Routes>
            <Route path="/" element={<PortfolioPage />} />
            <Route path="/bots/:symbol" element={<BotDetailPage />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}

describe('App shell (N3)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders the CCBT Dashboard heading', () => {
    const Wrapper = makeWrapper('/')
    render(<TestApp />, { wrapper: Wrapper })
    expect(screen.getByText('CCBT Dashboard')).toBeTruthy()
  })

  it('renders the portfolio page at /', () => {
    const Wrapper = makeWrapper('/')
    render(<TestApp />, { wrapper: Wrapper })
    expect(screen.getByText('Portfolio Overview')).toBeTruthy()
  })

  it('renders bot detail page at /bots/:symbol', () => {
    const Wrapper = makeWrapper('/bots/BTCUSDT')
    render(<TestApp />, { wrapper: Wrapper })
    expect(screen.getByText('BTCUSDT')).toBeTruthy()
  })

  it('shows WS status indicator', () => {
    const Wrapper = makeWrapper('/')
    render(<TestApp />, { wrapper: Wrapper })
    // "Connecting…" from the mocked hook returning status='connecting'
    expect(screen.getByText('Connecting…')).toBeTruthy()
  })

  it('renders Portfolio nav link in sidebar', () => {
    const Wrapper = makeWrapper('/')
    render(<TestApp />, { wrapper: Wrapper })
    expect(screen.getByText('Portfolio')).toBeTruthy()
  })
})
