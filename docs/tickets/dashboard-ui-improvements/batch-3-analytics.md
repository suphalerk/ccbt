# Batch 3 — Analytics polish

> From [README.md](README.md) ADD_NOW #6, #7, #8. Branch: `ui-batch3-analytics`. TDD + review.
> Pure frontend — no API change.

## #6 — Underwater drawdown sub-chart below the equity curve
- New panel below the equity curve: running-max − cumulative_pnl over the series `/api/equity` already
  returns (DD over time, always ≤0). Reuse `Charts.tsx` (lightweight-charts). The running-max/underwater
  transform is a presentation derivation of an already-computed PnL series (allowed — not financial math
  that affects decisions); compute it in a small util with a unit test, or expose it server-side if cleaner.

## #7 — Visible symbol labels in BotGrid pills
- Show a truncated 3-6 char symbol on each pill (currently symbol is in `aria-label` only → 59 dots are
  unscannable). Fix any test selector that relied on dot-only.

## #8 — Search + status + strategy filter on the Bot Overview Table
- Client-side filter over the already-loaded rows: text search (symbol), status filter (e.g. error/running),
  strategy filter. No API change. Sort already exists; this adds filtering.

## Constraints
- No new API calls; keep existing vitest tests green; add tests for the underwater transform (hand-computed
  on a small series), BotGrid label rendering, and the table filter logic. Rebuild bundle.
