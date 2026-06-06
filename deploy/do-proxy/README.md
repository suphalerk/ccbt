# DigitalOcean SOCKS5 Proxy (fixed IP for Binance mainnet)

Routes **only the bot's ccxt traffic** through a DigitalOcean droplet so Binance
sees a fixed, whitelistable IP. The rest of the Mac is untouched. Uses an SSH
SOCKS5 tunnel (no proxy daemon to install/secure on the droplet) kept alive by
autossh + launchd.

```
bot (ccxt) ──socks5h://127.0.0.1:1080──> autossh ──SSH──> droplet ──> Binance
                                                          139.59.226.122  (whitelist this)
```

## Current deployment
- **Droplet**: `ard-tools` (shared), region `sgp1` (Singapore)
- **Egress IP to whitelist at Binance**: `139.59.226.122`
- **Tunnel user**: `ccbtproxy` (login-less, port-forwarding only)
- **Key**: `~/.ssh/ccbt_do_proxy` (dedicated, no passphrase)
- **Local SOCKS port**: `127.0.0.1:1080`

Binance API endpoints (`api.binance.com`, `fapi.binance.com`) are IPv4-only, so
egress is always the droplet's IPv4 — no IPv6 leak risk.

## One-time setup

### 1. Droplet side (run once, as root on the droplet)
```bash
bash setup-droplet.sh            # creates the ccbtproxy user + installs the tunnel key
```
The public key is embedded in the script. To rotate, regenerate
`~/.ssh/ccbt_do_proxy` on the Mac and update the script.

### 2. Mac side (run once)
```bash
bash setup-tunnel-mac.sh         # installs autossh + launchd agent, starts the tunnel
```

### 3. Verify
```bash
bash check-proxy.sh              # must print 139.59.226.122
```

### 4. Enable in the bot (mainnet only)
Add to `.env` (or the start script's env):
```
CCBT_SOCKS_PROXY=socks5h://127.0.0.1:1080
```
The bot reads this in `bot/exchange.py` and `bot/shared_exchange_pool.py` and sets
`exchange.socksProxy`. Leave it unset for testnet (direct connection).

Then whitelist `139.59.226.122` at Binance → API Management → Edit restrictions.

## Operations
```bash
# status
launchctl list | grep doproxy
# restart tunnel
launchctl kickstart -k gui/$(id -u)/com.ccbt.doproxy
# stop / start
launchctl bootout gui/$(id -u)/com.ccbt.doproxy
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ccbt.doproxy.plist
# logs
tail -f /tmp/ccbt-doproxy.log /tmp/ccbt-doproxy.err
```

## ⚠️ Before starting mainnet bots
`check-proxy.sh` should be run first — if the egress IP != 139.59.226.122 the
bot must NOT start (Binance would reject, or worse, leak the home IP). Wire it
into the start script.
