# Deployment Guide for Crypto Trading Bot

Step-by-step instructions for deploying to a fresh Ubuntu 22.04 VPS.
Designed for automated execution via Claude Code.

## Prerequisites

- Fresh Ubuntu 22.04 VPS (minimum: 1 vCPU, 2GB RAM, 20GB SSD)
- Domain name with DNS A record pointing to the server IP
- SSH access as root or sudo user
- Bybit API keys (with NO withdrawal permission)
- Anthropic API key (for AI layer)
- Telegram bot token + chat ID (for monitoring alerts)

## Step 1: SSH into Server

```bash
ssh root@YOUR_SERVER_IP
```

## Step 2: Clone the Repository

```bash
# Clone into the deployment directory
git clone https://github.com/YOUR_USERNAME/ccbt.git /opt/trading-bot/app
cd /opt/trading-bot/app
```

## Step 3: Run the Setup Script

The setup script installs all system dependencies, creates the user, configures
the firewall, sets up nginx, and obtains an SSL certificate.

```bash
sudo bash deploy/setup.sh dashboard.yourdomain.com
```

This will:
- Install Docker, Docker Compose, nginx, certbot, fail2ban
- Create a `tradingbot` system user
- Configure UFW firewall (SSH + HTTP + HTTPS only)
- Generate nginx reverse proxy config with basic auth
- Obtain SSL certificate from Let's Encrypt
- Create systemd timers for backups (daily) and monitoring (every 5 min)

**Important**: Write down the generated basic auth password shown during setup.

## Step 4: Configure Environment Variables

```bash
# Copy the example environment file
sudo -u tradingbot cp .env.example .env

# Edit with your actual credentials
sudo -u tradingbot nano .env
```

Required variables to set:
- `API_KEY` and `API_SECRET` - Bybit API credentials
- `ANTHROPIC_API_KEY` - For AI analysis layer
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` - For monitoring alerts

See `.env.example` for all available variables and their descriptions.

## Step 5: Start Services with Docker Compose

```bash
cd /opt/trading-bot/app

# Build and start both services (bot + dashboard)
sudo -u tradingbot docker compose up -d --build

# Watch the logs to verify startup
sudo -u tradingbot docker compose logs -f --tail=50
# Press Ctrl+C to stop following logs
```

## Step 6: Verify Health

```bash
# Check container status
docker ps

# Check bot health
docker inspect --format='{{.State.Health.Status}}' tradingbot

# Check dashboard is responding
curl -s http://localhost:8501/_stcore/health

# Check nginx is proxying correctly
curl -sk https://dashboard.yourdomain.com/_stcore/health

# View bot logs
docker logs tradingbot --tail=20

# View dashboard logs
docker logs tradingbot-dashboard --tail=20
```

## Step 7: Set Up Monitoring

The monitoring script runs automatically via systemd timer (every 5 minutes).

```bash
# Verify the timer is active
systemctl status tradingbot-monitor.timer

# Run a manual check
sudo -u tradingbot python3 /opt/trading-bot/app/deploy/monitoring.py

# Force a daily report
sudo -u tradingbot python3 /opt/trading-bot/app/deploy/monitoring.py --daily-report

# Check timer logs
journalctl -u tradingbot-monitor.service --since "1 hour ago"
```

## Step 8: Change Dashboard Password

```bash
# Set a new password for dashboard access
sudo htpasswd -c /etc/nginx/.htpasswd admin
```

## Common Operations

### View Logs

```bash
# Bot logs (real-time)
docker logs -f tradingbot

# Dashboard logs
docker logs -f tradingbot-dashboard

# Nginx access logs
tail -f /var/log/nginx/tradingbot_access.log
```

### Restart Services

```bash
cd /opt/trading-bot/app

# Restart both services
sudo -u tradingbot docker compose restart

# Restart only the bot (dashboard stays up)
sudo -u tradingbot docker compose restart bot

# Full rebuild (after code changes)
sudo -u tradingbot docker compose up -d --build
```

### Update Code

```bash
cd /opt/trading-bot/app
sudo -u tradingbot git pull
sudo -u tradingbot docker compose up -d --build
```

### Manual Backup

```bash
sudo -u tradingbot /opt/trading-bot/app/deploy/backup.sh

# Backup with S3 push
S3_BUCKET=s3://my-bucket sudo -u tradingbot /opt/trading-bot/app/deploy/backup.sh --s3

# Backup with rsync to remote
sudo -u tradingbot /opt/trading-bot/app/deploy/backup.sh --remote user@backuphost:/backups
```

### Check Database

```bash
# Get trade count
docker exec tradingbot python -c "
import sqlite3
conn = sqlite3.connect('/app/data/trades.db')
count = conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0]
print(f'Total trades: {count}')
"
```

### Switch Between Testnet and Live

Edit `config.json`:
```bash
sudo -u tradingbot nano /opt/trading-bot/app/config.json
# Change "use_testnet": true  ->  "use_testnet": false
sudo -u tradingbot docker compose restart bot
```

### SSL Certificate Renewal

Certbot auto-renewal is configured via systemd timer. To manually renew:
```bash
sudo certbot renew
sudo systemctl reload nginx
```

## Troubleshooting

### Bot won't start
```bash
# Check logs for errors
docker logs tradingbot --tail=50

# Verify .env file exists and has correct permissions
ls -la /opt/trading-bot/app/.env

# Verify config.json is valid
python3 -c "import json; json.load(open('/opt/trading-bot/app/config.json'))"
```

### Dashboard not accessible
```bash
# Check if dashboard container is running
docker ps | grep dashboard

# Check nginx config
sudo nginx -t

# Check if port 8501 is listening
ss -tlnp | grep 8501

# Check nginx error log
tail -20 /var/log/nginx/tradingbot_error.log
```

### Database locked errors
```bash
# The bot and dashboard share the same SQLite database.
# If you see "database is locked" errors, check for stuck processes:
docker exec tradingbot python -c "
import sqlite3
conn = sqlite3.connect('/app/data/trades.db', timeout=30)
print('Database accessible')
"
```

### Container keeps restarting
```bash
# Check exit code and logs
docker inspect tradingbot --format='{{.State.ExitCode}}'
docker logs tradingbot --tail=100
```

## Architecture

```
VPS (Ubuntu 22.04)
├── Docker
│   ├── tradingbot         (main.py - trading engine)
│   └── tradingbot-dashboard (Streamlit - web UI)
│       └── shared volume: bot-data (trades.db, heartbeat, logs)
├── Nginx (reverse proxy)
│   ├── HTTPS termination (Let's Encrypt)
│   ├── Basic authentication
│   ├── WebSocket upgrade (Streamlit)
│   └── Rate limiting
├── Systemd Timers
│   ├── tradingbot-backup.timer   (daily at 03:00 UTC)
│   └── tradingbot-monitor.timer  (every 5 minutes)
├── fail2ban (brute force protection)
└── UFW firewall (22, 80, 443 only)
```

## Security Checklist

- [ ] API keys have NO withdrawal permission
- [ ] `.env` file is not committed to git
- [ ] Dashboard is behind basic auth
- [ ] UFW firewall is enabled
- [ ] fail2ban is protecting nginx
- [ ] SSL certificate is valid
- [ ] Bot runs as non-root user (inside container and on host)
- [ ] Telegram alerts are configured and tested
