# N8 — Bot Control Endpoints + Auth (SECURITY)

**Phase B · est. M (1 day) · SECURITY-CRITICAL — do EARLY (N12 depends on its env model)**

## Goal
Port the dashboard's bot-control actions (per-bot + bulk mode changes) safely. These write
`data/mode_{symbol}.json`, which the engine reads each tick — **PANIC closes all positions at market**.
This is the only state-changing surface; it must be authenticated and guarded.

## 🔴 Canonical symbol derivation (must-fix #1 — path traversal)
- `bot/mode.py:_mode_path` does **zero** sanitization → `symbol='../../x'` escapes `data/`.
- Factor a single `sym_clean(symbol)` into `bot/mode.py`; **the engine AND the API both call it** (engine
  currently inlines `config['symbol'].replace('/','').replace(':','')` at engine.py:410/1593 — make it
  call `sym_clean`). One source of truth for the filename, exactly as the ADR demands for math.
- **Do NOT port app.py's BULK derivation** (`.replace('USDT','')+'USDT'`, app.py:410-429) — it's a
  divergent string and must not be the parity reference. (Configs store plain `1000BONKUSDT` so it doesn't
  misfire today, but it's a latent trap.)
- Validate `{symbol}` against an **exact-match roster allowlist** (from `get_bot_statuses`) AND a strict
  pattern `^[A-Z0-9]{2,20}$` BEFORE constructing any path. Reject `../`, `%2e%2e%2f`, not-in-roster.
- **Byte-for-byte invariant**: `sym_clean(config['symbol'])` MUST equal the engine's existing
  `_symbol_clean` (`config['symbol'].replace('/','').replace(':','')`) for every current config — else a
  live bot stops finding its own mode file. The round-trip test asserts this equality per roster symbol.

## Scope
- `POST /api/bots/{symbol}/mode` body `{mode: normal|graceful_stop|tp_only|panic}` →
  `write_bot_mode(sym_clean(symbol), BotMode(...), data_dir)`. Strict enum validation (422 otherwise).
- `POST /api/bots/mode/bulk` body `{mode}` → apply to all **roster** bots only.
- React: per-bot mode selector + bulk buttons. **PANIC (single & bulk) requires a confirm dialog**
  ("type PANIC") — no one-click mass close.
- New mode reflects immediately (N2 watches mode-file mtimes).

## Security rules (must-haves)
- Bind `127.0.0.1` by default; beyond localhost only behind the nginx basic-auth proxy (N12).
- Auth dependency on ALL POSTs: shared-secret `CCBT_DASH_TOKEN` (header). **Startup assertion: refuse to
  start if host≠127.0.0.1 and token unset** (behind nginx the localhost peer-check is vacuous — must-fix #10).
- **Secret hygiene (must-fix #10)**: `start-dashboard.sh` must NOT `set -a; source .env` wholesale
  (exposes MAINNET/ANTHROPIC/OANDA/Telegram to a read-only dashboard). Allowlist-export only `CCBT_DASH_*`
  + `BOT_DATA_DIR`; explicitly **unset `CCBT_SOCKS_PROXY`** so no endpoint can egress to the exchange.
- **Server-side PANIC debounce**: a rapid 2nd bulk PANIC within N seconds → 429.

## Tests (write first) — `tests/test_api_control.py`
- valid mode write creates the correct `data/mode_*.json` (temp data dir);
- **round-trip**: for every roster symbol, the API's computed mode-file path == the path the engine reads
  (`sym_clean` parity);
- invalid mode → 422; malicious/unknown symbol (`../`, `%2e%2e%2f`, not in roster) → 400 with **no file
  written**;
- POST without token (simulated non-local) → 401; startup refuses host≠localhost + no token;
- bulk applies to roster bots only; 2nd rapid bulk PANIC → 429.
- React: PANIC blocked until confirm; mode select posts and reflects new state.

## Acceptance
- Mode control parity (correct semantics, NOT the buggy bulk derivation); PANIC gated by confirm +
  debounce; no unauthenticated state change; no path-traversal write; no exchange secrets in env.

## Result
_(fill on completion)_
