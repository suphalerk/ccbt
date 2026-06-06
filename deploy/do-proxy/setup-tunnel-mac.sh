#!/bin/bash
# Mac side — install autossh + a launchd agent that keeps the SOCKS5 tunnel up
# (auto-reconnect, auto-start on login). Run once:
#   bash setup-tunnel-mac.sh

set -e

DROPLET_IP="${CCBT_PROXY_IP:-139.59.226.122}"
PROXY_USER="${CCBT_PROXY_USER:-ccbtproxy}"
PORT="${CCBT_PROXY_PORT:-1080}"
KEY="$HOME/.ssh/ccbt_do_proxy"
LABEL="com.ccbt.doproxy"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "=== CCBT proxy tunnel (Mac) setup ==="

# 1. autossh
if ! command -v autossh >/dev/null 2>&1; then
    echo "installing autossh..."
    brew install autossh
fi
AUTOSSH="$(command -v autossh)"

# 2. key must exist
if [ ! -f "$KEY" ]; then
    echo "❌ tunnel key missing: $KEY"
    echo "   generate it: ssh-keygen -t ed25519 -f $KEY -N '' -C ccbt-do-proxy-tunnel"
    echo "   then add the .pub to the droplet via setup-droplet.sh"
    exit 1
fi

# 3. seed known_hosts so autossh doesn't block on host-key prompt
ssh-keyscan -t ed25519 "$DROPLET_IP" >> "$HOME/.ssh/known_hosts" 2>/dev/null || true

# 4. launchd agent
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$AUTOSSH</string>
        <string>-M</string><string>0</string>
        <string>-N</string>
        <string>-D</string><string>127.0.0.1:$PORT</string>
        <string>-o</string><string>ServerAliveInterval=15</string>
        <string>-o</string><string>ServerAliveCountMax=3</string>
        <string>-o</string><string>ExitOnForwardFailure=yes</string>
        <string>-o</string><string>StrictHostKeyChecking=accept-new</string>
        <string>-o</string><string>BatchMode=yes</string>
        <string>-i</string><string>$KEY</string>
        <string>$PROXY_USER@$DROPLET_IP</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AUTOSSH_GATETIME</key><string>0</string>
    </dict>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>/tmp/ccbt-doproxy.log</string>
    <key>StandardErrorPath</key><string>/tmp/ccbt-doproxy.err</string>
</dict>
</plist>
EOF
echo "wrote $PLIST"

# 5. (re)load
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
echo "launchd agent loaded"

sleep 4
echo ""
echo "=== verify ==="
bash "$(dirname "$0")/check-proxy.sh"
