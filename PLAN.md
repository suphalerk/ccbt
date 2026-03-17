# Dashboard & Deployment Plan

## Part A: Dashboard Implementation

### Tech Stack
- **Framework**: Streamlit (เหมาะกับ Python, deploy ง่าย, ไม่ต้องเขียน frontend แยก)
- **Charts**: Plotly (interactive, candlestick support)
- **Data**: SQLite (trades.db) + live exchange API
- **Auto-refresh**: Streamlit `st.rerun()` ทุก 30 วินาที

### Dashboard Layout (4 sections)

#### Section 1: Header — Account Overview
```
┌──────────────────────────────────────────────────────────────┐
│  Balance: $1,234.56    Daily PnL: +$12.30 (+1.0%)           │
│  Open Positions: 1/2   Consecutive Losses: 0                │
│  Mode: TESTNET         AI Layer: ON (threshold: 0.65)       │
│  Status: ● RUNNING     Uptime: 4h 32m                      │
└──────────────────────────────────────────────────────────────┘
```

#### Section 2: Charts — Price & Indicators
- Candlestick chart (15m) with EMA(9), EMA(21) overlay
- RSI subplot (14-period) with 45/65 zone highlighted
- ATR subplot
- Trade markers: green △ = long entry, red ▽ = short entry, × = exit
- Volume bars with MA(20) line

#### Section 3: Trade Log & Performance
- Table: recent trades (entry, exit, PnL, duration, AI decision, close_reason)
- Equity curve chart (cumulative PnL over time)
- Win rate / Profit Factor / Sharpe (rolling 7-day and all-time)
- AI accuracy analysis:
  - "AI SKIP but would have won" count
  - "AI EXECUTE but lost" count
  - Confidence vs actual outcome scatter plot

#### Section 4: Risk Monitor
- Daily loss bar (0% ──────█──── 2% limit)
- Leverage gauge (current vs max 5x)
- Circuit breaker status (daily loss / consecutive losses / API errors)
- Funding rate display
- Orderbook imbalance meter

### Files to Create
```
dashboard/
├── app.py           ← Streamlit main app
├── components.py    ← Reusable chart/widget functions
└── queries.py       ← SQLite query helpers for dashboard
```

### Dependencies to Add
```
streamlit>=1.30.0
plotly>=5.18.0
```

---

## Part B: Deployment Plan

### Architecture
```
┌─────────────────────────────────────────┐
│              VPS (Ubuntu 22.04)         │
│                                         │
│  ┌─────────────┐   ┌────────────────┐  │
│  │  trading-bot │   │   dashboard    │  │
│  │  (systemd)   │   │  (streamlit)   │  │
│  │  port: none  │   │  port: 8501    │  │
│  └──────┬───────┘   └───────┬────────┘  │
│         │                   │           │
│         └───── trades.db ───┘           │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │  Nginx reverse proxy (HTTPS)     │   │
│  │  → dashboard.yourdomain.com      │   │
│  └──────────────────────────────────┘   │
│                                         │
│  ┌──────────────────────────────────┐   │
│  │  Monitoring: healthcheck.io      │   │
│  │  Alerts: Telegram bot            │   │
│  └──────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

### Option A: VPS (Recommended for trading bot)
**Provider**: Hetzner / DigitalOcean / Vultr
**Spec**: 1 vCPU, 2GB RAM, 20GB SSD (~$5-10/mo)
**Location**: Singapore (ใกล้ Bybit servers)

**Setup Steps:**

1. **Server setup**
   ```bash
   # Create user, SSH keys, firewall
   ufw allow 22/tcp
   ufw allow 443/tcp
   ufw enable
   ```

2. **Install dependencies**
   ```bash
   apt update && apt install python3.11 python3.11-venv nginx certbot
   python3.11 -m venv /opt/trading-bot/venv
   source /opt/trading-bot/venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Environment variables**
   ```bash
   # /opt/trading-bot/.env
   API_KEY=xxx
   API_SECRET=xxx
   ANTHROPIC_API_KEY=xxx
   ```

4. **Systemd service for trading bot**
   ```ini
   # /etc/systemd/system/trading-bot.service
   [Unit]
   Description=Crypto Trading Bot
   After=network.target

   [Service]
   Type=simple
   User=tradingbot
   WorkingDirectory=/opt/trading-bot
   EnvironmentFile=/opt/trading-bot/.env
   ExecStart=/opt/trading-bot/venv/bin/python main.py
   Restart=on-failure
   RestartSec=30

   [Install]
   WantedBy=multi-user.target
   ```

5. **Systemd service for dashboard**
   ```ini
   # /etc/systemd/system/trading-dashboard.service
   [Unit]
   Description=Trading Dashboard
   After=network.target

   [Service]
   Type=simple
   User=tradingbot
   WorkingDirectory=/opt/trading-bot
   ExecStart=/opt/trading-bot/venv/bin/streamlit run dashboard/app.py --server.port 8501 --server.address 127.0.0.1
   Restart=on-failure

   [Install]
   WantedBy=multi-user.target
   ```

6. **Nginx + HTTPS**
   ```nginx
   server {
       listen 443 ssl;
       server_name dashboard.yourdomain.com;

       ssl_certificate /etc/letsencrypt/live/dashboard.yourdomain.com/fullchain.pem;
       ssl_certificate_key /etc/letsencrypt/live/dashboard.yourdomain.com/privkey.pem;

       # Basic auth for security
       auth_basic "Trading Dashboard";
       auth_basic_user_file /etc/nginx/.htpasswd;

       location / {
           proxy_pass http://127.0.0.1:8501;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
       }
   }
   ```

### Option B: Docker (Alternative)
```yaml
# docker-compose.yml
services:
  bot:
    build: .
    env_file: .env
    volumes:
      - ./data:/app/data    # SQLite persistence
    restart: unless-stopped

  dashboard:
    build: .
    command: streamlit run dashboard/app.py --server.port 8501
    ports:
      - "8501:8501"
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

### Monitoring & Alerts

1. **Healthcheck** — bot writes heartbeat to file every loop iteration
   ```python
   # Add to main.py loop
   Path("/tmp/trading-bot-heartbeat").write_text(str(time.time()))
   ```
   External monitor (healthchecks.io / UptimeRobot) pings every 5 min.

2. **Telegram Alerts** — send on critical events:
   - Trading halted (daily loss / API errors)
   - Trade executed / closed
   - Bot restart / crash
   - Leverage exceeded

3. **Log rotation**
   ```ini
   # /etc/logrotate.d/trading-bot
   /opt/trading-bot/trading_bot.log {
       daily
       rotate 30
       compress
       missingok
   }
   ```

### Deployment Checklist

#### Pre-deploy
- [ ] All tests passing (`pytest tests/ -v`)
- [ ] Backtest completed on 12 months data
- [ ] Config reviewed: `use_testnet: true` for Phase 3
- [ ] API keys have NO withdrawal permission
- [ ] `.env` not in git

#### Phase 3 — Testnet (2 weeks)
- [ ] Deploy to VPS with `use_testnet: true`
- [ ] Run dashboard, monitor daily
- [ ] Verify: orders execute, SL/TP trigger, circuit breakers work
- [ ] Check AI layer decisions make sense

#### Phase 4 — Go Live
- [ ] Switch `use_testnet: false`
- [ ] Start with $100-300
- [ ] Set Telegram alerts ON
- [ ] Monitor daily for 30 days
- [ ] Review AI accuracy weekly (skip vs would-have-won)
- [ ] Scale only after 30 days of positive expectancy

### Cost Summary
| Item | Monthly Cost |
|------|-------------|
| VPS (Hetzner CX22) | $5-10 |
| Claude API (AI layer) | $1-6 |
| Domain + SSL | $1 |
| **Total** | **~$7-17/mo** |
