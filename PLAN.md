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

## Part B: Deployment

Deployment is fully automated via Docker and shell scripts. See the deployment files:

### Deployment Files
```
Dockerfile              ← Python 3.11-slim, non-root user, health check
.dockerignore           ← Excludes .git, tests, .env from Docker builds
docker-compose.yml      ← Two services: bot + dashboard, shared volume
.env.example            ← Template for all environment variables
deploy/
├── setup.sh            ← Full VPS setup (Docker, nginx, SSL, firewall, timers)
├── nginx.conf          ← Reverse proxy with auth, WebSocket, rate limiting
├── monitoring.py       ← Health checks + Telegram alerts (runs every 5 min)
├── backup.sh           ← SQLite backup with 30-day retention, optional S3/rsync
└── README-DEPLOY.md    ← Step-by-step deployment instructions
```

### Quick Deploy (Ubuntu 22.04 VPS)
```bash
# 1. Clone repo
git clone <repo-url> /opt/trading-bot/app && cd /opt/trading-bot/app

# 2. Run setup (installs Docker, nginx, SSL, firewall, creates user)
sudo bash deploy/setup.sh dashboard.yourdomain.com

# 3. Configure credentials
sudo -u tradingbot cp .env.example .env
sudo -u tradingbot nano .env

# 4. Start services
sudo -u tradingbot docker compose up -d --build

# 5. Verify
docker ps && curl -s http://localhost:8501/_stcore/health
```

For detailed instructions, see `deploy/README-DEPLOY.md`.

### Architecture
```
VPS (Ubuntu 22.04)
├── Docker
│   ├── tradingbot           (main.py — trading engine)
│   └── tradingbot-dashboard (Streamlit — web UI, port 8501)
│       └── shared volume: bot-data (trades.db, heartbeat)
├── Nginx (reverse proxy, HTTPS, basic auth, rate limiting)
├── Systemd Timers
│   ├── tradingbot-backup.timer   (daily at 03:00 UTC)
│   └── tradingbot-monitor.timer  (every 5 minutes)
├── fail2ban (brute force protection)
└── UFW firewall (SSH + HTTP + HTTPS only)
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
