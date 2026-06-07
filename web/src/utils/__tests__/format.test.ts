/**
 * N3: Formatting utility tests (TDD — written BEFORE implementation)
 * Tests: money / pct / inf / null / duration formatting.
 */
import { describe, it, expect } from 'vitest'
import {
  formatMoney,
  formatPct,
  formatPF,
  formatDuration,
  formatPriceChange,
} from '../format'

describe('formatMoney', () => {
  it('formats positive values with $ prefix and 2dp', () => {
    expect(formatMoney(1234.56)).toBe('$1,234.56')
  })

  it('formats negative values with minus sign', () => {
    expect(formatMoney(-50.5)).toBe('-$50.50')
  })

  it('formats zero', () => {
    expect(formatMoney(0)).toBe('$0.00')
  })

  it('returns "—" for null', () => {
    expect(formatMoney(null)).toBe('—')
  })

  it('returns "—" for undefined', () => {
    expect(formatMoney(undefined)).toBe('—')
  })

  it('handles large values', () => {
    expect(formatMoney(1234567.89)).toBe('$1,234,567.89')
  })
})

describe('formatPct', () => {
  it('formats percentage with 1dp and % suffix', () => {
    expect(formatPct(62.5)).toBe('62.5%')
  })

  it('formats 100%', () => {
    expect(formatPct(100)).toBe('100.0%')
  })

  it('returns "—" for null', () => {
    expect(formatPct(null)).toBe('—')
  })

  it('returns "—" for undefined', () => {
    expect(formatPct(undefined)).toBe('—')
  })
})

describe('formatPF', () => {
  it('formats profit factor with 2dp', () => {
    expect(formatPF(1.85)).toBe('1.85')
  })

  it('returns "∞" for Infinity', () => {
    expect(formatPF(Infinity)).toBe('∞')
  })

  it('returns "—" for null', () => {
    expect(formatPF(null)).toBe('—')
  })

  it('returns "—" for undefined', () => {
    expect(formatPF(undefined)).toBe('—')
  })

  it('returns "—" for NaN', () => {
    expect(formatPF(NaN)).toBe('—')
  })
})

describe('formatDuration', () => {
  it('formats seconds less than a minute', () => {
    expect(formatDuration(45)).toBe('45s')
  })

  it('formats minutes', () => {
    expect(formatDuration(90)).toBe('1m 30s')
  })

  it('formats hours', () => {
    expect(formatDuration(3661)).toBe('1h 1m')
  })

  it('formats days', () => {
    expect(formatDuration(90000)).toBe('1d 1h')
  })

  it('returns "—" for null', () => {
    expect(formatDuration(null)).toBe('—')
  })

  it('returns "—" for undefined', () => {
    expect(formatDuration(undefined)).toBe('—')
  })

  it('returns "—" for zero', () => {
    expect(formatDuration(0)).toBe('—')
  })
})

describe('formatPriceChange', () => {
  it('formats positive change with + prefix', () => {
    expect(formatPriceChange(5.5)).toBe('+5.50%')
  })

  it('formats negative change', () => {
    expect(formatPriceChange(-3.2)).toBe('-3.20%')
  })

  it('returns "—" for null', () => {
    expect(formatPriceChange(null)).toBe('—')
  })
})
