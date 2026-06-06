#!/bin/bash
# Droplet side — create a login-less, port-forwarding-only tunnel user.
# Run once as root on the DigitalOcean droplet (e.g. via DO web console):
#   bash setup-droplet.sh
#
# Idempotent: safe to re-run (e.g. to rotate the key).

set -e

PROXY_USER="ccbtproxy"
# Public half of ~/.ssh/ccbt_do_proxy on the Mac. Rotate both together.
PUBKEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIC4N3FjIahFBFyTsP1pSId+fyrSwOr8tQprrgGZoG46S ccbt-do-proxy-tunnel"

echo "=== CCBT proxy droplet setup ==="

if ! id "$PROXY_USER" >/dev/null 2>&1; then
    useradd -m -s /usr/sbin/nologin "$PROXY_USER"
    echo "created user $PROXY_USER (nologin)"
else
    echo "user $PROXY_USER already exists"
fi

install -d -m 700 -o "$PROXY_USER" -g "$PROXY_USER" "/home/$PROXY_USER/.ssh"
# 'restrict' disables everything, then port-forwarding is added back. The user
# can ONLY forward ports (no shell, no pty, no agent/X11) — safe for a shared box.
echo "restrict,port-forwarding $PUBKEY" > "/home/$PROXY_USER/.ssh/authorized_keys"
chown "$PROXY_USER:$PROXY_USER" "/home/$PROXY_USER/.ssh/authorized_keys"
chmod 600 "/home/$PROXY_USER/.ssh/authorized_keys"

echo "authorized_keys installed:"
cat "/home/$PROXY_USER/.ssh/authorized_keys"

echo "--- sshd forwarding check ---"
sshd -T 2>/dev/null | grep -i allowtcpforwarding || echo "(default: yes)"

echo ""
echo "Droplet ready. Public IPv4 (whitelist at Binance):"
curl -4 -s --max-time 6 ifconfig.me || true
echo ""
