---
name: project_realtime_upnl
description: Realtime unrealized PnL panel shipped — UpnlPanel, useLiveSnapshot upnl state, TS build fixes
metadata:
  type: project
---

Realtime uPnL feature shipped on branch `dashboard-realtime-upnl`.

Backend delivered: `api/markprice.py` (MarkPriceClient, public Binance markPrice WS, uPnL computed in Python), wired into `api/main.py` lifespan, broadcasts `{type:'upnl'}` on snapshot_registry.

Frontend delivered:
- `web/src/ws-types.ts`: `PositionMark`, `FeedStatus`, `WSUpnlData`, `WSUpnlPayload`, union extended
- `web/src/hooks/useLiveSnapshot.ts`: `upnl: WSUpnlData | null` state, populated on `{type:'upnl'}` messages; snapshot state untouched
- `web/src/components/UpnlPanel.tsx`: per-position table (symbol/side/mark/entry/uPnL), total uPnL (green/red), feed status indicator (live/stale/offline), loading skeleton when null, "No open positions" empty state
- `web/src/pages/PortfolioPage.tsx` + `App.tsx`: upnl prop wired through App → PortfolioPage → UpnlPanel
- `web/src/__tests__/upnl.test.tsx`: 15 vitest tests (all pass)

Fix commit `f3e51b0`: removed unused `vi` import (noUnusedLocals) and replaced TypeScript constructor parameter property syntax with explicit field assignment — required by `erasableSyntaxOnly: true` in tsconfig.app.json. Build was blocked until this fix.

Round-1 review findings: (1) `key={pos.symbol}` in UpnlPanel row map — markprice.py keyed _positions by symbol, so at most one entry per symbol reaches the client, making duplicate keys impossible in practice, but the type allows it; minor nit. (2) Side-alias coloring (`buy` → emerald, `sell` → red) is correct in the component but the vitest suite has no test for the `buy`/`sell` aliases specifically (only `long`/`short` are tested in per-position rows). (3) `WSUpnlPayload.data` is typed non-nullable but guarded with `?? null` in the hook — harmless defensive code, minor type mismatch.

**Why:** `erasableSyntaxOnly` is set in tsconfig — TS-specific class syntax (constructor parameter properties) is disallowed. Must use plain field + assignment instead.

**How to apply:** When writing test mocks with class syntax, declare fields explicitly and assign in constructor body. Do not use `constructor(public fieldName: type)` pattern.
