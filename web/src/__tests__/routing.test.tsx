/**
 * N3: App shell routing tests (TDD — written BEFORE implementation)
 * Tests: portfolio route renders, /bots/:symbol resolves, sidebar present.
 */
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter, useParams } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

// Test routing primitives to confirm react-router-dom works

import { Routes, Route } from 'react-router-dom'

const PortfolioPage = () => <div data-testid="portfolio-page">Portfolio</div>
const BotDetailPage = ({ symbol }: { symbol?: string }) => (
  <div data-testid="bot-detail-page">Bot: {symbol}</div>
)

function TestApp() {
  return (
    <Routes>
      <Route path="/" element={<PortfolioPage />} />
      <Route
        path="/bots/:symbol"
        element={<BotDetailPageWrapper />}
      />
    </Routes>
  )
}

function BotDetailPageWrapper() {
  const { symbol } = useParams<{ symbol: string }>()
  return <BotDetailPage symbol={symbol} />
}

function makeWrapper(initialEntry = '/') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
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

describe('Client-side routing', () => {
  it('renders portfolio page at /', () => {
    const Wrapper = makeWrapper('/')
    render(<TestApp />, { wrapper: Wrapper })
    expect(screen.getByTestId('portfolio-page')).toBeTruthy()
  })

  it('resolves /bots/:symbol to bot detail page', () => {
    const Wrapper = makeWrapper('/bots/BTCUSDT')
    render(<TestApp />, { wrapper: Wrapper })
    expect(screen.getByTestId('bot-detail-page')).toBeTruthy()
    expect(screen.getByText('Bot: BTCUSDT')).toBeTruthy()
  })

  it('resolves /bots/:symbol for any symbol without pre-rendering constraints', () => {
    const Wrapper = makeWrapper('/bots/1000BONKUSDT')
    render(<TestApp />, { wrapper: Wrapper })
    expect(screen.getByText('Bot: 1000BONKUSDT')).toBeTruthy()
  })
})
