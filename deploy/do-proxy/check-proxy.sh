#!/bin/bash
# Verify the SOCKS proxy is up and egressing the whitelisted IP.
# Run before starting mainnet bots. Exit 0 = OK, non-zero = DO NOT trade.
#
#   bash check-proxy.sh

PORT="${CCBT_PROXY_PORT:-1080}"
EXPECTED_IP="${CCBT_PROXY_IP:-139.59.226.122}"

# api.ipify.org is IPv4-only, so it reflects the true IPv4 egress through the
# tunnel (a dual-stack echo like ifconfig.me would return the droplet's IPv6).
CURRENT_IP=$(curl -s --max-time 8 --socks5-hostname "127.0.0.1:$PORT" https://api.ipify.org 2>/dev/null)

if [ -z "$CURRENT_IP" ]; then
    echo "❌ SOCKS proxy unreachable on 127.0.0.1:$PORT — tunnel down?"
    echo "   try: launchctl kickstart -k gui/\$(id -u)/com.ccbt.doproxy"
    exit 1
fi

if [ "$CURRENT_IP" != "$EXPECTED_IP" ]; then
    echo "❌ Egress IP mismatch! Expected $EXPECTED_IP, got $CURRENT_IP"
    echo "   Binance whitelist would reject — NOT safe to trade."
    exit 1
fi

# Confirm Binance futures is actually reachable through the proxy
HTTP=$(curl -s --max-time 10 --socks5-hostname "127.0.0.1:$PORT" \
    -o /dev/null -w "%{http_code}" https://fapi.binance.com/fapi/v1/ping 2>/dev/null)
if [ "$HTTP" != "200" ]; then
    echo "⚠️  Egress IP OK ($CURRENT_IP) but Binance fapi returned HTTP $HTTP"
    exit 1
fi

echo "✅ Proxy OK — egress $CURRENT_IP, Binance fapi reachable"
exit 0
