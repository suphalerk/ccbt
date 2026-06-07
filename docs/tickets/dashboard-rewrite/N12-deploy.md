# N12 — Deployment (Vite static bundle served by FastAPI + launchd)

**Phase D · est. M (1 day)**

## Goal
Run the new dashboard alongside the bot on the Mac Mini with the simplest robust topology, **without
colliding with the existing Streamlit service**, and keep the VPS path (nginx + basic auth) working.

## 🔴 Service/port collision (must-fix #9 — VERIFIED these already exist)
- `com.ccbt.dashboard.plist` + `deploy/macos/start-dashboard.sh` ALREADY run **Streamlit on port 8501**.
- Use a NEW service `com.ccbt.dashboard-v2` on a **distinct port** (e.g. 8601) during the N13 soak so both
  dashboards run in parallel. Reusing the name+port crash-loops and makes the soak impossible.
- Atomic `launchctl unload old / load new` ONLY at the N13 cutover. **Fail fast (named `lsof`/bind probe)
  if the port is already bound.**
- **Soak exposure (must-fix recommended)**: the existing Streamlit binds `0.0.0.0:8501` with NO auth and
  stays up during the soak. For the soak window, **rebind legacy Streamlit to `127.0.0.1`** (or firewall
  the Mac Mini) so it isn't network-exposed while v2 runs alongside.

## Scope
- Vite build → **`web/dist`** (static CSR bundle). FastAPI serves it via `StaticFiles` **+ /api + /ws on
  one port** → single process, no Node runtime in prod.
- `deploy/macos/start-dashboard-v2.sh`:
  - **Secret hygiene (must-fix #10)**: do NOT `set -a; source .env`. Allowlist-export only `CCBT_DASH_*`
    + `BOT_DATA_DIR`; explicitly **`unset CCBT_SOCKS_PROXY`** (dashboard must never egress to the exchange).
  - activate venv (**pinned Python 3.10** — single floor shared with N0, must-fix #8); `uvicorn api.main:app --host 127.0.0.1 --port 8601`.
  - the build step `npm --prefix web run build` runs in CI / a make target, NOT at launch; the script
    asserts `web/dist/index.html` exists (bundle-freshness check) and refuses to start otherwise.
- launchd plist `com.ccbt.dashboard-v2`; restart via `launchctl kickstart -k gui/$(id -u)/com.ccbt.dashboard-v2`.
- Env: `CCBT_DASH_HOST`, `CCBT_DASH_PORT`, `CCBT_DASH_POLL_S`, `CCBT_DASH_TOKEN`.
- VPS nginx (correction, must-fix recommended): `deploy/nginx.conf` ALREADY has `Upgrade/Connection`
  headers — the work is a **dedicated `/ws` location** with `proxy_read_timeout 0` + its own rate-limit
  zone, plus basic auth on the proxy (the localhost peer-check is vacuous behind nginx → token required).

## Tests / checks
- `npm run build` produces `web/dist`; FastAPI serves `index.html` at `/` + assets; `/api/health` 200.
- start-script dry run (no real keys) boots uvicorn, serves the bundle, and **does not export MAINNET/
  ANTHROPIC/OANDA/Telegram** into the process env (assert env allowlist); refuses to start if port bound
  or `web/dist` missing.
- nginx `/ws` snippet validated (`nginx -t` in CI or documented).

## Acceptance
- `com.ccbt.dashboard-v2` serves UI+API+WS on a distinct port under launchd alongside the bot + the old
  Streamlit; no exchange secrets in env; SOCKS proxy unset; VPS proxy upgrades WS on `/ws`.

## Result
Done. Commit `0525698` on `dashboard-rewrite-impl`.

Files written (no plist loaded — files only per scope):
- `deploy/macos/start-dashboard-v2.sh` — uvicorn on `127.0.0.1:8601`; allowlist-only
  env export (`CCBT_DASH_*` + `BOT_DATA_DIR`); explicit `unset CCBT_SOCKS_PROXY`;
  manual line-by-line `.env` parse (no `set -a; source`); pre-flight checks for
  `web/dist/index.html` and port-already-bound (`lsof`); `.venv-dash` activation.
- `deploy/macos/com.ccbt.dashboard-v2.plist` — distinct label (`com.ccbt.dashboard-v2`),
  log paths (`dashboard-v2.log`), and EnvironmentVariables allowlist; no exchange
  secrets; `ThrottleInterval=15`; NOT loaded (N13 cutover).
- `deploy/nginx-ws-v2.conf` — dedicated `/ws` location with `proxy_read_timeout 0`,
  separate `ws_v2` rate-limit zone, `X-Dash-Token` forwarded; upstream on `8601`;
  `/v2` proxy block with `auth_basic` for VPS soak.
- `tests/test_n12_deploy.py` — 34 tests, all passing.
