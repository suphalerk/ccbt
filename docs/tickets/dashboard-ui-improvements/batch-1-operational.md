# Batch 1 — Operational controls (highest value, backend exists)

> ✅ **DONE + deployed 2026-06-08** (branch `ui-batch1-operational`, merged to main). Review caught 2 BLOCKERS
> (mode UPPER/lower case-mismatch → banner false-alarm + dead mode-highlight from `bot_detail` hardcoding
> `mode=None`) + 2 MAJORS (`error_count` not exposed on BotRow; dialog a11y) — all fixed. Backend now returns
> `mode.upper()` + `error_count` on BotRow; 203 vitest + 107 api tests pass.

> From [README.md](README.md) ADD_NOW #1, #2, #4. These wire EXISTING, token-gated backend endpoints
> into the React SPA — the backend (`api/routers/control.py`) + client (`web/src/api/client.ts`
> `setBotMode`/`setBulkMode`) already exist and are tested. This is pure frontend wiring + tests.
> Branch: `ui-batch1-operational`. TDD (vitest) + multi-lens review (the established flow).

## Auth model (verified)
- `verify_token` (api/deps.py): if `CCBT_DASH_TOKEN` env is SET, the `X-Dash-Token` header must match;
  if UNSET (current localhost-only setup) the POST passes with no token. So on 127.0.0.1:8501 the
  control POSTs work WITHOUT a token. To be future-proof, read an optional token from
  `localStorage.getItem('ccbt_dash_token')` (null when absent) and pass it to setBotMode/setBulkMode.

## Request/response shapes (verified — api/models.py)
- `ModeRequest`: `{ mode: 'NORMAL'|'GRACEFUL_STOP'|'TP_ONLY'|'PANIC', confirm_panic?: boolean }`
- `BulkModeRequest`: `{ symbols: string[], mode: string, confirm_panic?: boolean }` — **empty `symbols: []`
  targets the WHOLE roster** (server-side). Bulk PANIC is debounced server-side (2nd within window → 429).
- Responses: `ModeResponse {symbol, mode, accepted, message?}`, `BulkModeResponse {results: ModeResponse[]}`.

## Items
### #1 — Mode buttons on BotDetailPage
- Four buttons NORMAL / GRACEFUL_STOP / TP_ONLY / PANIC near the existing mode badge (BotDetailPage.tsx
  has `ModeBadge` ~line 88, header ~line 5). Clicking calls `client.setBotMode(symbol, {mode}, token)`.
- **PANIC requires a confirm dialog** → on confirm, send `{mode:'PANIC', confirm_panic:true}`.
- After success, invalidate/refetch the bot detail + the `['bots']` query so the badge updates. Show the
  returned `message` (toast/inline). Handle non-2xx (e.g. 429 debounce, 4xx token) with a visible error.
- Highlight the button matching the bot's current `mode`.

### #2 — Global STOP-ALL / PANIC-ALL on PortfolioPage header
- Add to the portfolio header (PortfolioPage.tsx `portfolio-header` ~line 70 region) two actions:
  - **STOP-ALL** (prominent, default): `setBulkMode({symbols:[], mode:'GRACEFUL_STOP'})` — safer, no forced
    market exits.
  - **PANIC-ALL** (less prominent, behind a confirm): `setBulkMode({symbols:[], mode:'PANIC', confirm_panic:true})`.
- Show a result summary (`results.length` accepted). Handle 429 (rapid 2nd PANIC) with a clear message.
- A **RESUME-ALL** (`mode:'NORMAL'`) is a sensible add for symmetry.

### #4 — Portfolio alert banner
- A banner at the top of PortfolioPage that aggregates the ALREADY-LOADED `/api/bots` list (no new API):
  count of bots with `error_count > 0`, count of bots in a non-NORMAL `mode`, and any circuit-breaker
  flag if present on the row. Hidden/green when all clear; amber/red with counts when not.
- Clicking a count could (optional) scroll to / filter the relevant bots — not required for this batch.

## Constraints
- No financial math in TS (none needed here — counts only).
- never-interfere: these POST to the bot's own mode files via the API; that is the INTENDED control path
  (not an exchange call). Fine.
- Don't break existing vitest tests; add tests for: button→client call (mocked), PANIC confirm gate,
  bulk empty-symbols payload, banner aggregation logic, error/429 surfaced.
- Rebuild bundle after: `npm --prefix web run build`.
