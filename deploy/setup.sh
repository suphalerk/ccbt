#!/usr/bin/env bash
# =============================================================================
# Server Setup Script for Crypto Trading Bot
# Target: Fresh Ubuntu 22.04 VPS
#
# Usage: sudo bash setup.sh <domain-name>
# Example: sudo bash setup.sh dashboard.example.com
#
# This script:
#   1. Installs Docker, Docker Compose, nginx, certbot
#   2. Creates a dedicated 'tradingbot' user
#   3. Configures UFW firewall
#   4. Creates directory structure
#   5. Generates nginx config with SSL
#   6. Sets up systemd timers for log rotation and backups
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Color output helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------
if [ "$#" -lt 1 ]; then
    log_error "Usage: sudo bash setup.sh <domain-name>"
    log_error "Example: sudo bash setup.sh dashboard.example.com"
    exit 1
fi

DOMAIN="$1"
BOT_USER="tradingbot"
BOT_HOME="/opt/trading-bot"
REPO_DIR="${BOT_HOME}/app"

# ---------------------------------------------------------------------------
# SSH access restriction (optional but recommended)
# Set SSH_ALLOWED_IP to your static IP or CIDR to restrict SSH access.
# Leave empty to allow SSH from any IP (less secure).
# Example: SSH_ALLOWED_IP="203.0.113.42"
#          SSH_ALLOWED_IP="203.0.113.0/24"
# ---------------------------------------------------------------------------
SSH_ALLOWED_IP=""

# Must run as root
if [ "$(id -u)" -ne 0 ]; then
    log_error "This script must be run as root (use sudo)"
    exit 1
fi

log_info "Starting setup for domain: ${DOMAIN}"

# ---------------------------------------------------------------------------
# Step 1: System updates and base packages
# ---------------------------------------------------------------------------
log_info "Step 1/9: Updating system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    software-properties-common \
    ufw \
    fail2ban \
    htop \
    unzip \
    apache2-utils \
    sqlite3 \
    jq

# ---------------------------------------------------------------------------
# Step 2: Install Docker
# ---------------------------------------------------------------------------
log_info "Step 2/9: Installing Docker..."

if ! command -v docker &> /dev/null; then
    # Add Docker's official GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # Add Docker repository
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null

    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Enable and start Docker
    systemctl enable docker
    systemctl start docker
    log_info "Docker installed successfully"
else
    log_info "Docker already installed, skipping"
fi

# ---------------------------------------------------------------------------
# Step 3: Install Nginx and Certbot
# ---------------------------------------------------------------------------
log_info "Step 3/9: Installing Nginx and Certbot..."
apt-get install -y -qq nginx certbot python3-certbot-nginx
systemctl enable nginx

# ---------------------------------------------------------------------------
# Step 4: Create tradingbot user
# ---------------------------------------------------------------------------
log_info "Step 4/9: Creating '${BOT_USER}' user..."

if ! id "${BOT_USER}" &> /dev/null; then
    useradd --system --create-home --home-dir "${BOT_HOME}" --shell /bin/bash "${BOT_USER}"
    # Add to docker group so the user can run docker commands
    usermod -aG docker "${BOT_USER}"
    log_info "User '${BOT_USER}' created"
else
    log_info "User '${BOT_USER}' already exists, skipping"
fi

# ---------------------------------------------------------------------------
# Step 5: Create directory structure
# ---------------------------------------------------------------------------
log_info "Step 5/9: Creating directory structure..."

mkdir -p "${REPO_DIR}"
mkdir -p "${BOT_HOME}/backups"
mkdir -p "${BOT_HOME}/logs"
mkdir -p "${BOT_HOME}/ssl"

# Set ownership
chown -R "${BOT_USER}:${BOT_USER}" "${BOT_HOME}"

log_info "Directory structure created at ${BOT_HOME}"
log_info "  ${REPO_DIR}        - Application code (clone repo here)"
log_info "  ${BOT_HOME}/backups - Database backups"
log_info "  ${BOT_HOME}/logs    - Application logs"

# ---------------------------------------------------------------------------
# Step 6: Configure UFW Firewall
# ---------------------------------------------------------------------------
log_info "Step 6/9: Configuring firewall (UFW)..."

# Reset UFW to defaults
ufw --force reset > /dev/null 2>&1

# Default policies: deny incoming, allow outgoing
ufw default deny incoming
ufw default allow outgoing

# Allow SSH (essential - do this first!)
# Restrict to a specific IP/CIDR if SSH_ALLOWED_IP is set; otherwise allow all.
# WARNING: Allowing SSH from all IPs exposes port 22 to brute-force attacks.
#          Set SSH_ALLOWED_IP at the top of this script to restrict access.
if [ -n "${SSH_ALLOWED_IP}" ]; then
    ufw allow from "${SSH_ALLOWED_IP}" to any port 22 proto tcp comment "SSH (restricted)"
    log_info "SSH access restricted to ${SSH_ALLOWED_IP}"
else
    ufw allow 22/tcp comment "SSH (open - consider setting SSH_ALLOWED_IP)"
    log_warn "SSH is open to all IPs. Set SSH_ALLOWED_IP in setup.sh to restrict access."
fi

# Allow HTTP and HTTPS for nginx
ufw allow 80/tcp comment "HTTP (certbot challenges)"
ufw allow 443/tcp comment "HTTPS (dashboard)"

# Enable UFW (non-interactive)
ufw --force enable

log_info "Firewall configured: SSH(22), HTTP(80), HTTPS(443) allowed"

# ---------------------------------------------------------------------------
# Step 7: Generate Nginx configuration
# ---------------------------------------------------------------------------
log_info "Step 7/9: Generating Nginx configuration..."

# Create basic auth password file (user will set password later)
if [ ! -f /etc/nginx/.htpasswd ]; then
    # Generate a random initial password
    INITIAL_PASSWORD=$(openssl rand -base64 12)
    htpasswd -cb /etc/nginx/.htpasswd admin "${INITIAL_PASSWORD}"
    log_warn "Basic auth created with user 'admin' and password: ${INITIAL_PASSWORD}"
    log_warn "Change this password with: sudo htpasswd -c /etc/nginx/.htpasswd admin"
fi

# Copy nginx config template (initially without SSL for certbot to work)
cat > /etc/nginx/sites-available/tradingbot <<NGINX_EOF
# ============================================================================
# Nginx config for Trading Bot Dashboard
# Generated by setup.sh
# ============================================================================

# Rate limiting zone: 10 requests/second per IP
limit_req_zone \$binary_remote_addr zone=dashboard:10m rate=10r/s;

# Upstream for Streamlit
upstream streamlit_backend {
    server 127.0.0.1:8501;
    keepalive 32;
}

# HTTP -> HTTPS redirect
server {
    listen 80;
    server_name ${DOMAIN};

    # Allow certbot ACME challenges
    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    # Redirect all other traffic to HTTPS
    location / {
        return 301 https://\$host\$request_uri;
    }
}

# HTTPS server
server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    # SSL certificates (will be created by certbot)
    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    # SSL configuration (modern)
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # OCSP stapling
    ssl_stapling on;
    ssl_stapling_verify on;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;

    # Basic authentication
    auth_basic "Trading Dashboard";
    auth_basic_user_file /etc/nginx/.htpasswd;

    # Apply rate limiting
    limit_req zone=dashboard burst=20 nodelay;

    # Streamlit main application
    location / {
        proxy_pass http://streamlit_backend;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket support (required by Streamlit)
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # Timeouts
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
    }

    # Streamlit static assets (no auth needed)
    location /static {
        auth_basic off;
        proxy_pass http://streamlit_backend/static;
    }

    # Streamlit health endpoint (no auth needed)
    location /_stcore/health {
        auth_basic off;
        proxy_pass http://streamlit_backend/_stcore/health;
    }

    # Deny access to hidden files
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
}
NGINX_EOF

# Enable the site
ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/tradingbot
rm -f /etc/nginx/sites-enabled/default

# Test nginx configuration (will fail on SSL certs, that's OK for now)
log_info "Nginx config written to /etc/nginx/sites-available/tradingbot"

# ---------------------------------------------------------------------------
# Step 8: Obtain SSL certificate with Let's Encrypt
# ---------------------------------------------------------------------------
log_info "Step 8/9: Setting up SSL with Let's Encrypt..."

# First, set up a temporary HTTP-only config for certbot
cat > /etc/nginx/sites-available/tradingbot-temp <<TEMP_EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 200 'Setting up SSL...';
        add_header Content-Type text/plain;
    }
}
TEMP_EOF

ln -sf /etc/nginx/sites-available/tradingbot-temp /etc/nginx/sites-enabled/tradingbot
nginx -t && systemctl reload nginx

# Obtain certificate
log_info "Requesting SSL certificate for ${DOMAIN}..."
log_info "Make sure DNS A record points to this server's IP before proceeding."

if certbot certonly --webroot -w /var/www/html -d "${DOMAIN}" --non-interactive --agree-tos --email "admin@${DOMAIN}" 2>/dev/null; then
    log_info "SSL certificate obtained successfully"
    # Switch back to full config
    ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/tradingbot
    rm -f /etc/nginx/sites-available/tradingbot-temp
    nginx -t && systemctl reload nginx
else
    log_warn "SSL certificate request failed (DNS may not be configured yet)"
    log_warn "After DNS is configured, run:"
    log_warn "  sudo certbot certonly --webroot -w /var/www/html -d ${DOMAIN} --agree-tos --email admin@${DOMAIN}"
    log_warn "  sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/tradingbot"
    log_warn "  sudo systemctl reload nginx"
fi

# Set up auto-renewal
systemctl enable certbot.timer 2>/dev/null || true

# ---------------------------------------------------------------------------
# Step 9: Create systemd timers for log rotation and backups
# ---------------------------------------------------------------------------
log_info "Step 9/9: Setting up systemd timers..."

# --- Log rotation config ---
cat > /etc/logrotate.d/trading-bot <<LOGROTATE_EOF
${BOT_HOME}/logs/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 ${BOT_USER} ${BOT_USER}
    sharedscripts
    postrotate
        # Signal Docker to rotate its internal logs if needed
        docker kill --signal=USR1 tradingbot 2>/dev/null || true
    endscript
}
LOGROTATE_EOF

# --- Backup systemd service ---
cat > /etc/systemd/system/tradingbot-backup.service <<SERVICE_EOF
[Unit]
Description=Trading Bot Database Backup
After=docker.service

[Service]
Type=oneshot
User=${BOT_USER}
ExecStart=${REPO_DIR}/deploy/backup.sh
StandardOutput=journal
StandardError=journal
SERVICE_EOF

# --- Backup systemd timer (runs daily at 03:00 UTC) ---
cat > /etc/systemd/system/tradingbot-backup.timer <<TIMER_EOF
[Unit]
Description=Daily backup for Trading Bot

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
TIMER_EOF

# --- Monitoring systemd service ---
cat > /etc/systemd/system/tradingbot-monitor.service <<MONITOR_SVC_EOF
[Unit]
Description=Trading Bot Health Monitor
After=docker.service

[Service]
Type=oneshot
User=${BOT_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/python3 ${REPO_DIR}/deploy/monitoring.py
StandardOutput=journal
StandardError=journal
MONITOR_SVC_EOF

# --- Monitoring systemd timer (runs every 5 minutes) ---
cat > /etc/systemd/system/tradingbot-monitor.timer <<MONITOR_TIMER_EOF
[Unit]
Description=Trading Bot Health Monitor (every 5 minutes)

[Timer]
OnCalendar=*:0/5
Persistent=true

[Install]
WantedBy=timers.target
MONITOR_TIMER_EOF

# Enable timers
systemctl daemon-reload
systemctl enable tradingbot-backup.timer
systemctl start tradingbot-backup.timer
systemctl enable tradingbot-monitor.timer
systemctl start tradingbot-monitor.timer

log_info "Systemd timers configured:"
log_info "  - tradingbot-backup.timer  : daily at 03:00 UTC"
log_info "  - tradingbot-monitor.timer : every 5 minutes"

# ---------------------------------------------------------------------------
# Configure fail2ban for nginx
# ---------------------------------------------------------------------------
log_info "Configuring fail2ban..."

cat > /etc/fail2ban/jail.local <<FAIL2BAN_EOF
[nginx-http-auth]
enabled = true
port = http,https
filter = nginx-http-auth
logpath = /var/log/nginx/error.log
maxretry = 5
bantime = 3600
FAIL2BAN_EOF

systemctl enable fail2ban
systemctl restart fail2ban

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "============================================================================"
echo "  SETUP COMPLETE"
echo "============================================================================"
echo ""
echo "  Domain:     ${DOMAIN}"
echo "  Bot user:   ${BOT_USER}"
echo "  App dir:    ${REPO_DIR}"
echo "  Backups:    ${BOT_HOME}/backups"
echo ""
echo "  NEXT STEPS:"
echo "  1. Clone your repo into ${REPO_DIR}:"
echo "       sudo -u ${BOT_USER} git clone <your-repo-url> ${REPO_DIR}"
echo ""
echo "  2. Create the .env file:"
echo "       sudo -u ${BOT_USER} cp ${REPO_DIR}/.env.example ${REPO_DIR}/.env"
echo "       sudo -u ${BOT_USER} nano ${REPO_DIR}/.env"
echo ""
echo "  3. Start the bot:"
echo "       cd ${REPO_DIR} && sudo -u ${BOT_USER} docker compose up -d --build"
echo ""
echo "  4. Change the dashboard password:"
echo "       sudo htpasswd -c /etc/nginx/.htpasswd admin"
echo ""
echo "  5. If SSL failed, configure DNS and re-run certbot:"
echo "       sudo certbot certonly --webroot -w /var/www/html -d ${DOMAIN}"
echo "       sudo ln -sf /etc/nginx/sites-available/tradingbot /etc/nginx/sites-enabled/tradingbot"
echo "       sudo systemctl reload nginx"
echo ""
echo "  6. Set up monitoring (edit Telegram token in .env first):"
echo "       systemctl status tradingbot-monitor.timer"
echo ""
echo "============================================================================"
