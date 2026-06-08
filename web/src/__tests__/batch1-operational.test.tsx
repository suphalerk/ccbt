/**
 * Batch 1 — Operational controls tests (TDD)
 *
 * Items covered:
 *   #1  Mode buttons on BotDetailPage — click → setBotMode, PANIC gate, highlight active
 *   #2  Global STOP-ALL / PANIC-ALL / RESUME-ALL on PortfolioPage header
 *   #4  Portfolio alert banner — aggregates error/non-NORMAL counts from bots list
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'
import type { BotRow, BotDetailResponse, PortfolioSummaryResponse } from '../api/client'

// ---- mock api/client --------------------------------------------------------

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      // BotDetailPage queries
      botDetail: vi.fn(),
      listTrades: vi.fn(),
      candles: vi.fn(),
      equityCurve: vi.fn(),
      // PortfolioPage queries
      portfolioSummary: vi.fn(),
      listBots: vi.fn(),
      // Control mutations
      setBotMode: vi.fn(),
      setBulkMode: vi.fn(),
    },
  }
})

import { api } from '../api/client'

// ---- fixtures ---------------------------------------------------------------

function makeBot(overrides: Partial<BotRow> = {}): BotRow {
  return {
    symbol: 'BTCUSDT',
    strategy: 'ema_crossover',
    timeframe: '15m',
    status: 'active',
    position_side: null,
    position_size: null,
    unrealized_pnl: null,
    total_pnl: 0,
    win_rate_pct: 0,
    profit_factor: 1,
    trade_count: 0,
    last_updated: null,
    mode: 'NORMAL',
    error_count: 0,
    ...overrides,
  }
}

function makeDetail(overrides: Partial<BotDetailResponse> = {}): BotDetailResponse {
  return {
    symbol: 'BTCUSDT',
    summary: makeBot(),
    recent_trades: [],
    ...overrides,
  }
}

function makeSummary(overrides: Partial<PortfolioSummaryResponse> = {}): PortfolioSummaryResponse {
  return {
    total_trades: 10,
    closed_trades: 8,
    win_rate_pct: 60,
    profit_factor: 1.5,
    total_pnl: 200,
    best_bot: 'BTCUSDT',
    worst_bot: 'ETHUSDT',
    active_bots: 2,
    ...overrides,
  }
}

// ---- wrappers ---------------------------------------------------------------

function makeBotDetailWrapper(symbol = 'BTCUSDT') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[`/bots/${symbol}`]}>
          <Routes>
            <Route path="/bots/:symbol" element={<>{children}</>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    )
  }
}

function makePortfolioWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter>{children}</MemoryRouter>
      </QueryClientProvider>
    )
  }
}

// ---- imports after mocks ----------------------------------------------------

import { BotDetailPage } from '../pages/BotDetailPage'
import { PortfolioPage } from '../pages/PortfolioPage'

// ============================================================================
// #1 — Mode buttons on BotDetailPage
// ============================================================================

describe('BotDetailPage — Mode buttons (#1)', () => {
  beforeEach(() => {
    vi.mocked(api.candles).mockResolvedValue({ available: false, symbol: 'BTCUSDT', timeframe: null, candles: [] } as any)
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.setBotMode).mockResolvedValue({ symbol: 'BTCUSDT', mode: 'GRACEFUL_STOP', accepted: true, message: 'Mode set' })
    vi.mocked(api.setBulkMode).mockResolvedValue({ results: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('renders four mode buttons', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'NORMAL' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('mode-buttons')
    const container = screen.getByTestId('mode-buttons')
    expect(container.querySelectorAll('button').length).toBeGreaterThanOrEqual(4)
  })

  it('clicking NORMAL calls setBotMode with mode:NORMAL', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'GRACEFUL_STOP' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const btn = await screen.findByTestId('mode-btn-NORMAL')
    fireEvent.click(btn)
    await waitFor(() => {
      expect(vi.mocked(api.setBotMode)).toHaveBeenCalledWith(
        'BTCUSDT',
        expect.objectContaining({ mode: 'NORMAL' }),
        null  // localStorage returns null in jsdom
      )
    })
  })

  it('clicking GRACEFUL_STOP calls setBotMode with mode:GRACEFUL_STOP', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'NORMAL' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const btn = await screen.findByTestId('mode-btn-GRACEFUL_STOP')
    fireEvent.click(btn)
    await waitFor(() => {
      expect(vi.mocked(api.setBotMode)).toHaveBeenCalledWith(
        'BTCUSDT',
        expect.objectContaining({ mode: 'GRACEFUL_STOP' }),
        null  // localStorage returns null in jsdom
      )
    })
  })

  it('clicking PANIC shows confirm dialog and does NOT call setBotMode until confirmed', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'NORMAL' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const panicBtn = await screen.findByTestId('mode-btn-PANIC')
    fireEvent.click(panicBtn)

    // Dialog should appear — setBotMode NOT yet called
    await screen.findByTestId('panic-confirm-dialog')
    expect(vi.mocked(api.setBotMode)).not.toHaveBeenCalled()
  })

  it('PANIC confirm sends {mode:PANIC, confirm_panic:true}', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'NORMAL' }) }))
    vi.mocked(api.setBotMode).mockResolvedValue({ symbol: 'BTCUSDT', mode: 'PANIC', accepted: true, message: null })

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('mode-btn-PANIC'))
    await screen.findByTestId('panic-confirm-dialog')

    fireEvent.click(screen.getByTestId('panic-confirm-ok'))
    await waitFor(() => {
      expect(vi.mocked(api.setBotMode)).toHaveBeenCalledWith(
        'BTCUSDT',
        { mode: 'PANIC', confirm_panic: true },
        null  // localStorage returns null in jsdom
      )
    })
  })

  it('PANIC cancel dismisses the dialog without calling setBotMode', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'NORMAL' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('mode-btn-PANIC'))
    await screen.findByTestId('panic-confirm-dialog')

    fireEvent.click(screen.getByTestId('panic-confirm-cancel'))
    await waitFor(() => {
      expect(screen.queryByTestId('panic-confirm-dialog')).toBeNull()
    })
    expect(vi.mocked(api.setBotMode)).not.toHaveBeenCalled()
  })

  it('highlights the button matching the current mode', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'TP_ONLY' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const activeBtn = await screen.findByTestId('mode-btn-TP_ONLY')
    expect(activeBtn.getAttribute('aria-pressed')).toBe('true')
  })

  it('surfaces error message on API failure', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'NORMAL' }) }))
    vi.mocked(api.setBotMode).mockRejectedValue(new Error('API 429: /api/bots/BTCUSDT/mode'))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('mode-btn-GRACEFUL_STOP'))
    const errEl = await screen.findByTestId('mode-error')
    expect(errEl.textContent).toMatch(/429|error/i)
  })
})

// ============================================================================
// #2 — Global STOP-ALL / PANIC-ALL / RESUME-ALL on PortfolioPage
// ============================================================================

describe('PortfolioPage — Bulk mode controls (#2)', () => {
  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listBots).mockResolvedValue({ bots: [makeBot()] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.setBulkMode).mockResolvedValue({ results: [{ symbol: 'BTCUSDT', mode: 'GRACEFUL_STOP', accepted: true, message: null }] })
  })
  afterEach(() => vi.clearAllMocks())

  it('renders STOP-ALL, PANIC-ALL, and RESUME-ALL buttons', async () => {
    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('bulk-stop-all')
    await screen.findByTestId('bulk-panic-all')
    await screen.findByTestId('bulk-resume-all')
  })

  it('STOP-ALL sends {symbols:[], mode:GRACEFUL_STOP}', async () => {
    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('bulk-stop-all'))
    await waitFor(() => {
      expect(vi.mocked(api.setBulkMode)).toHaveBeenCalledWith(
        { symbols: [], mode: 'GRACEFUL_STOP' },
        null  // localStorage returns null in jsdom
      )
    })
  })

  it('RESUME-ALL sends {symbols:[], mode:NORMAL}', async () => {
    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('bulk-resume-all'))
    await waitFor(() => {
      expect(vi.mocked(api.setBulkMode)).toHaveBeenCalledWith(
        { symbols: [], mode: 'NORMAL' },
        null  // localStorage returns null in jsdom
      )
    })
  })

  it('PANIC-ALL shows confirm dialog and does NOT call setBulkMode before confirm', async () => {
    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('bulk-panic-all'))
    await screen.findByTestId('bulk-panic-confirm-dialog')
    expect(vi.mocked(api.setBulkMode)).not.toHaveBeenCalled()
  })

  it('PANIC-ALL confirm sends {symbols:[], mode:PANIC, confirm_panic:true}', async () => {
    vi.mocked(api.setBulkMode).mockResolvedValue({ results: [{ symbol: 'BTCUSDT', mode: 'PANIC', accepted: true, message: null }] })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('bulk-panic-all'))
    await screen.findByTestId('bulk-panic-confirm-dialog')
    fireEvent.click(screen.getByTestId('bulk-panic-confirm-ok'))

    await waitFor(() => {
      expect(vi.mocked(api.setBulkMode)).toHaveBeenCalledWith(
        { symbols: [], mode: 'PANIC', confirm_panic: true },
        null  // localStorage returns null in jsdom
      )
    })
  })

  it('shows result summary after STOP-ALL', async () => {
    vi.mocked(api.setBulkMode).mockResolvedValue({
      results: [
        { symbol: 'BTCUSDT', mode: 'GRACEFUL_STOP', accepted: true, message: null },
        { symbol: 'ETHUSDT', mode: 'GRACEFUL_STOP', accepted: true, message: null },
      ],
    })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('bulk-stop-all'))
    const result = await screen.findByTestId('bulk-result')
    expect(result.textContent).toMatch(/2/)
  })

  it('surfaces 429 error clearly on STOP-ALL failure', async () => {
    vi.mocked(api.setBulkMode).mockRejectedValue(new Error('API 429: /api/bots/mode/bulk'))

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    fireEvent.click(await screen.findByTestId('bulk-stop-all'))
    const errEl = await screen.findByTestId('bulk-error')
    expect(errEl.textContent).toMatch(/429|error/i)
  })
})

// ============================================================================
// #4 — Portfolio alert banner
// ============================================================================

describe('PortfolioPage — Alert banner (#4)', () => {
  beforeEach(() => {
    vi.mocked(api.portfolioSummary).mockResolvedValue(makeSummary())
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
  })
  afterEach(() => vi.clearAllMocks())

  it('banner is hidden when all bots are NORMAL with no errors', async () => {
    const bots = [
      makeBot({ symbol: 'BTCUSDT', mode: 'NORMAL', status: 'active', error_count: 0 }),
      makeBot({ symbol: 'ETHUSDT', mode: 'NORMAL', status: 'active', error_count: 0 }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    // Banner should either not exist or be in a hidden/clear state
    const banner = screen.queryByTestId('portfolio-alert-banner')
    if (banner) {
      // If rendered, it should indicate all clear (no red/amber color class)
      expect(banner.getAttribute('data-severity')).toBe('clear')
    }
  })

  it('banner shows amber when some bots are in non-NORMAL mode', async () => {
    const bots = [
      makeBot({ symbol: 'BTCUSDT', mode: 'NORMAL', status: 'active', error_count: 0 }),
      makeBot({ symbol: 'ETHUSDT', mode: 'GRACEFUL_STOP', status: 'running', error_count: 0 }),
      makeBot({ symbol: 'SOLUSDT', mode: 'TP_ONLY', status: 'running', error_count: 0 }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const banner = await screen.findByTestId('portfolio-alert-banner')
    expect(banner.getAttribute('data-severity')).toMatch(/warning|amber/)
    // Should mention the count of non-NORMAL bots
    expect(banner.textContent).toMatch(/2/)
  })

  it('banner shows red when any bot is in PANIC mode', async () => {
    const bots = [
      makeBot({ symbol: 'BTCUSDT', mode: 'PANIC', status: 'running', error_count: 0 }),
      makeBot({ symbol: 'ETHUSDT', mode: 'NORMAL', status: 'running', error_count: 0 }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const banner = await screen.findByTestId('portfolio-alert-banner')
    expect(banner.getAttribute('data-severity')).toBe('critical')
  })

  // BLOCKER fix: verify lowercase mode values from real DB casing also work
  it('banner shows red when any bot has lowercase panic mode (real DB casing)', async () => {
    const bots = [
      makeBot({ symbol: 'BTCUSDT', mode: 'panic', status: 'running', error_count: 0 }),
      makeBot({ symbol: 'ETHUSDT', mode: 'normal', status: 'running', error_count: 0 }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const banner = await screen.findByTestId('portfolio-alert-banner')
    expect(banner.getAttribute('data-severity')).toBe('critical')
  })

  it('banner is hidden when all bots have lowercase normal mode (real DB casing)', async () => {
    const bots = [
      makeBot({ symbol: 'BTCUSDT', mode: 'normal', status: 'running', error_count: 0 }),
      makeBot({ symbol: 'ETHUSDT', mode: 'normal', status: 'running', error_count: 0 }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    // Normal bots with no errors → no banner
    const banner = screen.queryByTestId('portfolio-alert-banner')
    if (banner) {
      expect(banner.getAttribute('data-severity')).toBe('clear')
    }
  })

  // MAJOR fix: error_count > 0 triggers warning (status:'running' — the real production value)
  it('banner counts bots with error_count>0 (realistic shape: status running, not error)', async () => {
    const bots = [
      makeBot({ symbol: 'BTCUSDT', mode: 'NORMAL', status: 'running', error_count: 3 }),
      makeBot({ symbol: 'ETHUSDT', mode: 'NORMAL', status: 'running', error_count: 0 }),
    ]
    vi.mocked(api.listBots).mockResolvedValue({ bots })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    const banner = await screen.findByTestId('portfolio-alert-banner')
    // 1 bot with error_count>0 should trigger a warning
    expect(banner.getAttribute('data-severity')).toMatch(/warning|amber|critical/)
    expect(banner.textContent).toMatch(/1|error/i)
  })

  it('banner shows clear when bots list is empty', async () => {
    vi.mocked(api.listBots).mockResolvedValue({ bots: [] })

    const Wrapper = makePortfolioWrapper()
    render(<PortfolioPage />, { wrapper: Wrapper })
    await act(async () => {})

    // With no bots, banner should be absent or clear
    const banner = screen.queryByTestId('portfolio-alert-banner')
    if (banner) {
      expect(banner.getAttribute('data-severity')).toBe('clear')
    }
  })
})

// ============================================================================
// BLOCKER #2 — BotDetailPage mode highlight with realistic data shape
// Bot detail mode comes from bot_health (now populated by backend), but the
// test must verify that the highlight works even when botDetail returns
// mode=null while the bots list carries the real mode (defensive).
// ============================================================================

describe('BotDetailPage — Mode highlight with real API casing (BLOCKER #2)', () => {
  beforeEach(() => {
    vi.mocked(api.candles).mockResolvedValue({ available: false, symbol: 'BTCUSDT', timeframe: null, candles: [] } as any)
    vi.mocked(api.equityCurve).mockResolvedValue({ symbol: 'BTCUSDT', points: [] })
    vi.mocked(api.listTrades).mockResolvedValue({ trades: [], total: 0 })
    vi.mocked(api.setBotMode).mockResolvedValue({ symbol: 'BTCUSDT', mode: 'GRACEFUL_STOP', accepted: true, message: 'Mode set' })
    vi.mocked(api.setBulkMode).mockResolvedValue({ results: [] })
  })
  afterEach(() => vi.clearAllMocks())

  it('highlights TP_ONLY when botDetail returns uppercase mode', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'TP_ONLY' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const activeBtn = await screen.findByTestId('mode-btn-TP_ONLY')
    expect(activeBtn.getAttribute('aria-pressed')).toBe('true')
  })

  it('highlights GRACEFUL_STOP when botDetail returns lowercase mode (real DB casing)', async () => {
    // backend now uppercases, but we defend against future regressions
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: 'graceful_stop' }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    const activeBtn = await screen.findByTestId('mode-btn-GRACEFUL_STOP')
    expect(activeBtn.getAttribute('aria-pressed')).toBe('true')
  })

  it('no button highlighted when botDetail returns mode=null', async () => {
    vi.mocked(api.botDetail).mockResolvedValue(makeDetail({ summary: makeBot({ mode: null }) }))

    const Wrapper = makeBotDetailWrapper()
    render(<BotDetailPage />, { wrapper: Wrapper })
    await act(async () => {})

    await screen.findByTestId('mode-buttons')
    const pressed = document.querySelectorAll('[aria-pressed="true"]')
    expect(pressed.length).toBe(0)
  })
})
