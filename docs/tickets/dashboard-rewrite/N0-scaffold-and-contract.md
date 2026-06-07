# N0 — Scaffold & API Contract

**Phase B · est. M (1 day) · no business logic yet**

## Goal
Lay the repo structure and a frozen API contract so backend (N1/N2) and frontend (N3+) can proceed in
parallel against the same shapes.

## Scope
- New top-level dirs:
  - `api/` — FastAPI app (`api/main.py`, `api/routers/`, `api/models.py`, `api/deps.py`). Reuses
    `dashboard/queries.py` (import, do not copy).
  - `web/` — **Vite + React + Tailwind** project (NOT Next.js — see README ADR Decision 2).
- **Dependencies (must-fix #8)**: add to `requirements.txt`: `fastapi`, `uvicorn[standard]`,
  `pydantic>=2`, `httpx`. **Require Python 3.10+** (system is 3.9.6; Dockerfile uses 3.11) — pin the venv
  and state ONE floor (3.10) in both N0 and N12. Note: `from __future__ import annotations` does NOT save
  PEP 585 builtin-generics in RUNTIME positions (Pydantic field types, `cast(...)`, `TypeAdapter`), so on
  3.9 those still crash — hence 3.10 is required, not optional. Web deps: vite, react,
  @tanstack/react-query, openapi-typescript, tailwindcss, recharts, lightweight-charts, vitest, @testing-library/react.
- **Shared `classify` util (no-op refactor — ordering matters)**: N9 (Phase A) imports `classify` directly
  from `research.forward_test_report` (it exists there at line 25). N0 later lifts it into a shared module
  both `forward_test_report.py` and the API import — a **behavior-preserving** move that keeps N9's import
  working (re-export from the old path). Add an import smoke test for both paths. N9 must NOT block on N0.
- **OpenAPI contract first**: define Pydantic response models for every endpoint (even before impl) so
  FastAPI auto-generates `/openapi.json`. **Commit a static `openapi.json`** via `scripts/export_openapi.py`
  (imports the app, dumps the spec) so `npm run generate-types` is reproducible without a running server.
  Frontend generates TS types from it (`openapi-typescript`) — derived, never hand-written.
  **Also define the WS envelope as Pydantic models** and generate WS TS types (OpenAPI doesn't cover WS).
- Dev runner: `scripts/dev_dashboard.sh` (or `npm run dev` via `concurrently`) starting uvicorn (reload)
  + `vite` together.
- Document the endpoint list (matches N1/N9) in `api/README.md`.

## Endpoint inventory (contract)
REST (GET): `/api/health`, `/api/portfolio/summary`, `/api/bots`, `/api/bots/{symbol}`,
`/api/trades?symbol=&limit=`, `/api/equity?symbol=`, `/api/daily-pnl?symbol=`, `/api/ai/calibration`,
`/api/logs?level=&search=&limit=`, plus N9: `/api/close-reasons?symbol=`, `/api/trade-gate`,
`/api/risk?symbol=`, `/api/calendar?symbol=&year=&month=`, `/api/heatmap?symbol=&bucket_hours=`.
WS: `/ws` (snapshots) **+ `/ws/logs`** (high-freq log tail — N2/N7); OR one backpressured `/ws` (decide in
N2 and keep N0/N2/N3/N7 consistent). Define **both WS envelopes as Pydantic models** (OpenAPI doesn't
cover WS) and generate WS TS types. POST: `/api/bots/{symbol}/mode` + `/api/bots/mode/bulk` (N8).

## Tests (write first)
- `tests/test_api_scaffold.py`: app imports; `/api/health` 200; `/openapi.json` lists every contracted path.
- `tests/test_classify_import.py`: the shared `classify` util imports cleanly from both the API package
  and `research/forward_test_report.py` (no circular import / path issue).
- `web`: `npm run build` (or typecheck) passes on the empty scaffold; Tailwind compiles.

## Acceptance
- `bash scripts/dev_dashboard.sh` brings up uvicorn + vite; `/openapi.json` reflects the full contract;
  the committed `openapi.json` matches the generated one (drift check).
- No business logic yet — only shapes + wiring.

## Result
_(fill on completion)_
