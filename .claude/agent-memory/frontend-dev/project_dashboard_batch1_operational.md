---
name: project_dashboard_batch1_operational
description: Batch 1 operational controls shipped — mode buttons on BotDetailPage, bulk stop/panic on PortfolioPage, alert banner
metadata:
  type: project
---

Batch 1 operational controls (branch: ui-batch1-operational) shipped in commit 71f8fc1.

## What was built

**#1 BotDetailPage — Mode buttons**: Four buttons (NORMAL / GRACEFUL_STOP / TP_ONLY / PANIC) added just below the header. PANIC goes through a `PanicConfirmDialog` before sending `{mode:PANIC, confirm_panic:true}`. Active mode is highlighted via `aria-pressed`. On success, invalidates `['bot', sym]` + `['bots']` TanStack Query keys so the badge updates. Error (including 429) shown inline via `data-testid="mode-error"`.

**#2 PortfolioPage — Bulk controls**: `BulkModeControls` component added inside `PortfolioHeader`. Three buttons: STOP-ALL (prominent, yellow), RESUME-ALL (emerald), PANIC-ALL (muted red, behind `BulkPanicConfirmDialog`). Uses `setBulkMode({symbols:[], mode:...})` — empty symbols targets all bots server-side. Result count shown via `data-testid="bulk-result"`, error via `data-testid="bulk-error"`.

**#4 PortfolioPage — Alert banner**: `PortfolioAlertBanner` component renders above the header. Reads already-loaded `bots` list (no new API call). Severity: `clear` → hidden; `warning` (amber) → non-NORMAL mode bots or status=error bots; `critical` (red) → any PANIC bot. Uses `data-severity` attribute for test assertions.

## Auth pattern
All control calls read `localStorage.getItem('ccbt_dash_token')` → passes `null` when absent (works on localhost without token). Tests assert `null` as the third arg (jsdom has no localStorage entries).

## Tests
24 new vitest tests in `web/src/__tests__/batch1-operational.test.tsx`. 198/198 total tests pass. Bundle builds clean (TS strict).

**Why:** `ModeResponse.message` is required (`string | null`) not optional — mock objects must include it. `expect.anything()` does NOT match `null` in vitest — use the literal `null` for the token arg in test assertions.

**How to apply:** When writing tests for API calls that pass a localStorage-sourced token, assert `null` (not `expect.anything()`) for the token parameter.
