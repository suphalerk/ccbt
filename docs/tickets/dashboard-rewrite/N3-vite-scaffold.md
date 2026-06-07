# N3 — Vite + React SPA Scaffold & Data Layer

**Phase C · est. M (1 day)**

## Goal
A Vite + React + Tailwind shell with the dark trading theme, typed API access, and a resilient WS hook —
the foundation every page (N4–N10) builds on. (Vite, NOT Next.js — README ADR Decision 2: the prod
artifact is a static CSR bundle served by FastAPI; Vite gives the identical artifact with no App-Router
static-export footguns and a smaller dep surface.)

## Scope
- Vite React-TS app in `web/`; client-side routing (`react-router`) for `/` (portfolio) and
  `/bots/:symbol` (drilldown) — plain client routes, no pre-render constraints.
- Tailwind config with the existing palette (port `COLORS` from `dashboard/components.py`: profit
  #00C853, loss #FF1744, bg #0E1117, grid #1E2530, etc.) as theme tokens.
- App shell: sidebar (bot selector: "Portfolio" + symbol list from `/api/bots`), top status bar,
  content area. Responsive; dark by default.
- Typed API client generated from the committed `openapi.json` (`openapi-typescript`) — committed types,
  regenerated via an npm script. **No hand-written response types.**
- `TanStack Query` for REST (snapshot + cache); `useLiveSnapshot()` hook wrapping the WS:
  auto-connect, exponential-backoff reconnect, falls back to REST polling if WS down, hydrates the
  Query cache from WS messages so components just read from Query. **WS message types come from the
  Pydantic-derived WS types (N0)** — the hydration boundary is type-checked, not `any`.
- Number/money/%/duration formatting utils (the ONLY place TS touches values — pure formatting; data is
  already computed server-side).

## Tests (write first) — vitest + RTL
- `useLiveSnapshot` connects, applies an incoming (typed) WS message to cache, reconnects after a drop (mocked WS);
- generated client typechecks against the committed `openapi.json`;
- formatting utils (money/pct/inf/null/duration) unit-tested.

## Acceptance
- `npm run build` → `web/dist`; shell renders with live bot list from the API; WS hook reconnects on drop;
  `/bots/:symbol` resolves via client routing for any symbol.

## Result
_(fill on completion)_
