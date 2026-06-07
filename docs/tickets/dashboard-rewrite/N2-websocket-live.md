# N2 — WebSocket Live Layer

**Phase B · est. M (1 day)**

## Goal
Replace Streamlit's 30s full-page refresh with a real-time WS push, so the UI updates within a few
seconds of the bot writing a trade / heartbeat — without the bot knowing the web service exists.

## Design
- A FastAPI background task (`asyncio`) runs a **change detector** loop every `CCBT_DASH_POLL_S` (default 3s).
- **Complete watched-source list (must-fix #7)** — be explicit; nothing implicit:
  1. **DB**: `PRAGMA data_version` on a **process-lifetime** read-only connection (bumps on any committed
     write from any connection under WAL). VERIFIED to work cross-connection.
  2. **heartbeats**: `data/heartbeat_*` mtimes (bot online/offline — no DB write involved).
  3. **modes**: `data/mode_*.json` mtimes (mode changes reflect immediately).
  4. **logs**: `trading_bot.log` **size + inode** (appends DON'T touch the DB, so data_version misses them;
     inode catches log rotation — see N7). Drives the `/ws/logs` stream.
- On change → recompute affected snapshot(s) and broadcast. Envelope: `{type, ts, data}`. **Two channels:**
  `/ws` (low-freq snapshots) and `/ws/logs` (high-freq tail) — OR one channel with per-client backpressure
  so a slow log consumer can't stall trade snapshots (recommended).
- Client protocol: full snapshot on connect, then deltas/snapshots on change; client auto-reconnects
  with backoff (N3 hook).
- Connection registry with safe add/remove; broadcast tolerates dead sockets (drop on send error).

## Rules (WAL safety — must-fix #7)
- Read-only connection (`mode=ro`, `query_only=1`) — never a write lock, zero blast radius.
- **Open with `isolation_level=None`** (autocommit) so a bare SELECT / PRAGMA never opens a transaction.
  The persistent connection must stay out of any transaction BETWEEN AND WITHIN ticks — a lingering read
  txn pins the WAL and starves checkpoints with ~60 writing bots. Do PRAGMA + recompute, no `BEGIN`.
- **Graceful retry if the DB isn't yet in WAL** at startup (don't crash-loop before the bot creates it).
- No WS clients → still run the cheap data_version compare, skip recompute/broadcast.
- Coalesce: multiple changes within a tick → one snapshot, not N.

## Tests (write first) — `tests/test_api_ws.py`
- `PRAGMA data_version` increments after an insert on a second connection (the detection primitive);
- **the comparison survives across N ticks on the SAME persistent connection** (catches accidental
  per-tick reconnect that would reset the baseline);
- **`conn.in_transaction is False` after EVERY PRAGMA/recompute** (not just after a tick — catches a future
  edit that wraps recompute in BEGIN);
- **checkpoint advances under the live reader**: `PRAGMA wal_checkpoint(TRUNCATE)` succeeds while the
  detector loop runs (proves no WAL pin / checkpoint starvation);
- startup tolerates a DB not-yet-in-WAL (graceful retry, no crash);
- WS client receives an initial snapshot on connect; a simulated change → exactly one broadcast (coalesced);
- a slow `/ws/logs` consumer does not stall `/ws` snapshots; dead client removed without breaking others.

## Acceptance
- Two tabs update within ~poll interval of a new trade; CPU idle when nothing changes; checkpoints not
  starved (no lingering reader); log lines stream without stalling trade snapshots.

## Result
_(fill on completion)_
