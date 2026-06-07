/**
 * N3 — Formatting utilities.
 * The ONLY place TypeScript touches financial values — pure display formatting.
 * All financial math is done server-side in Python (queries.py).
 */

type Nullable<T> = T | null | undefined

/**
 * Format a money value in USDT with $ prefix, 2dp, comma thousands separator.
 * Returns "—" for null/undefined.
 *
 * Examples:
 *   formatMoney(1234.56)  → "$1,234.56"
 *   formatMoney(-50.5)    → "-$50.50"
 *   formatMoney(null)     → "—"
 */
export function formatMoney(value: Nullable<number>): string {
  if (value === null || value === undefined) return '—'
  if (!isFinite(value)) return '—'
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

/**
 * Format a percentage value with 1dp and % suffix.
 * Returns "—" for null/undefined.
 *
 * Examples:
 *   formatPct(62.5) → "62.5%"
 *   formatPct(null) → "—"
 */
export function formatPct(value: Nullable<number>): string {
  if (value === null || value === undefined) return '—'
  return `${value.toFixed(1)}%`
}

/**
 * Format a profit factor value with 2dp.
 * Returns "∞" for Infinity, "—" for null/undefined/NaN.
 *
 * Examples:
 *   formatPF(1.85)     → "1.85"
 *   formatPF(Infinity) → "∞"
 *   formatPF(null)     → "—"
 */
export function formatPF(value: Nullable<number>): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number' && isNaN(value)) return '—'
  if (!isFinite(value)) return '∞'
  return value.toFixed(2)
}

/**
 * Format a duration in seconds to a human-readable string.
 * Returns "—" for null/undefined/0.
 *
 * Examples:
 *   formatDuration(45)    → "45s"
 *   formatDuration(90)    → "1m 30s"
 *   formatDuration(3661)  → "1h 1m"
 *   formatDuration(90000) → "1d 1h"
 *   formatDuration(null)  → "—"
 */
export function formatDuration(seconds: Nullable<number>): string {
  if (seconds === null || seconds === undefined || seconds === 0) return '—'
  const s = Math.floor(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) {
    const rem = s % 60
    return rem > 0 ? `${m}m ${rem}s` : `${m}m`
  }
  const h = Math.floor(m / 60)
  if (h < 24) {
    const remM = m % 60
    return remM > 0 ? `${h}h ${remM}m` : `${h}h`
  }
  const d = Math.floor(h / 24)
  const remH = h % 24
  return remH > 0 ? `${d}d ${remH}h` : `${d}d`
}

/**
 * Format a price change percentage with +/- prefix and 2dp.
 * Returns "—" for null/undefined.
 *
 * Examples:
 *   formatPriceChange(5.5)  → "+5.50%"
 *   formatPriceChange(-3.2) → "-3.20%"
 *   formatPriceChange(null) → "—"
 */
export function formatPriceChange(value: Nullable<number>): string {
  if (value === null || value === undefined) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}
