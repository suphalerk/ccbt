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
- [ ] Verify: orders execute, SL/TP trigger, circuit breakers work autonomously
- [ ] Confirm Telegram alerts fire on circuit breaker events
- [ ] Review dashboard weekly to confirm AI decisions are reasonable
- [ ] Verify bot recovers from errors without manual intervention

#### Phase 4 — Go Live
- [ ] Switch `use_testnet: false`
- [ ] Start with $100-300
- [ ] Set Telegram alerts ON (critical events only)
- [ ] Bot runs fully autonomous — no daily monitoring required
- [ ] Weekly dashboard review for performance trends
- [ ] AI calibration auto-adjusts influence based on accuracy
- [ ] Scale only after 30 days of positive expectancy

### Cost Summary
| Item | Monthly Cost |
|------|-------------|
| VPS (Hetzner CX22) | $5-10 |
| Claude API (AI layer) | $1-6 |
| Domain + SSL | $1 |
| **Total** | **~$7-17/mo** |

---

## Part C: Development Tooling

### Claude Code Agent Team

7 specialized agents configured in `.claude/agents/` for team-based development:

| Agent | Model | Role | Skills |
|-------|-------|------|--------|
| `pm` | sonnet | Product Manager | scenario-analyzer, trader-memory-core |
| `sa` | opus | Solution Architect | backtest-expert, edge-pipeline-orchestrator |
| `backend-dev` | sonnet | Backend Developer | crypto-signal-validator, position-sizer, backtest-expert |
| `frontend-dev` | sonnet | Frontend Developer | technical-analyst |
| `devops` | sonnet | DevOps Engineer | — |
| `trader-expert` | opus | Trader Expert | 7 trading skills |
| `crypto-expert` | opus | Crypto Expert | 4 domain skills |

**Enable**: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (set in `.claude/settings.json`)

**Usage**:
```bash
# Create team for feature work
"Create an agent team with pm, sa, backend-dev, trader-expert to implement Phase 7"

# @mention for specific task
@trader-expert "optimize trailing stop parameters"
@crypto-expert "assess current market conditions"

# Run as single agent
claude --agent trader-expert
```

### Claude Code Skills

10 trading skills installed in `.claude/skills/`, adapted for crypto perpetual futures:

**Auto-Invoked**: technical-analyst, backtest-expert, position-sizer, macro-regime-detector, market-news-analyst, trader-memory-core, crypto-signal-validator

**Manual**: `/scenario-analyzer`, `/strategy-pivot-designer`, `/edge-pipeline-orchestrator`

**Sources**: [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills), [SkillsMP](https://skillsmp.com/)

### Key Documents
| File | Purpose | Owner |
|------|---------|-------|
| `CLAUDE.md` | Project guide for Claude Code | All |
| `PRD.md` | Product requirements + roadmap | PM |
| `PLAN.md` | Implementation plan (this file) | SA |
| `features.js` | Feature registry + status | PM |
| `config.json` | Trading parameters | Trader Expert |

---

## Part D: Next Phase — Optimization & Production Readiness

> **Created**: 2026-03-18 by Agent Team (SA + Trader Expert + Crypto Expert + Backend Dev + DevOps)
> **Status**: DRAFT — awaiting review

5 agents วิเคราะห์ codebase พร้อมกัน พบ **45+ findings** รวมจากทุกด้าน จัดเป็น 4 phases ตามลำดับความสำคัญ

---

### Phase 5A: Critical Fixes (ก่อน Testnet Deploy)

> **เป้าหมาย**: แก้บั๊กที่อาจทำให้เสียเงิน หรือทำให้ระบบทำงานผิดพลาด
> **ประมาณ**: 15 items | Owner: Backend Dev + DevOps

#### 5A.1 — Correctness (Financial Loss Risk)

| # | Issue | File | Severity | Detail |
|---|-------|------|----------|--------|
| 1 | **Side-based position matching** — 2 trades same side ถูก mark closed พร้อมกัน ทำ PnL ผิด | `main.py:188-201` | P0 | Enforce 1 trade per side (Bybit net position mode constraint) |
| 2 | **Blocking I/O ใน async loop** — `time.sleep()` block event loop ทั้งหมด | `exchange.py:128,160,173` | P0 | Wrap exchange calls in `asyncio.to_thread()`, ใช้ `asyncio.sleep()` |
| 3 | **SL verification failure = unprotected position** — bare `except: pass` แล้วเดินหน้าต่อ | `main.py:727-735` | P0 | ถ้า SL verify + close_all fail → halt bot |
| 4 | **Balance fallback เมื่อ free=0.0** — `free or total` คืน total เมื่อ margin ใช้หมด | `exchange.py:187` | P0 | ใช้ `free if free is not None else total` |
| 5 | **Shutdown ไม่ทำงานระหว่าง long sleep** — `asyncio.sleep(900)` ไม่ check shutdown_event | `main.py:804` | P1 | ใช้ `asyncio.wait_for(shutdown_event.wait(), timeout=sleep_time)` |
| 6 | **Stale ATR หลัง restart** — restored trades มี `atr: 0` → trailing stop ไม่ทำงาน | `main.py:429,472` | P1 | Fetch ATR จาก candle data เมื่อ restore |
| 7 | **SL/TP ไม่ recompute จาก fill price** — SL/TP ใช้ candle close แต่ actual fill อาจต่างกัน | `strategy.py:203` vs `main.py:740` | P1 | Recompute SL/TP distance จาก actual fill price |
| 8 | **`reset_daily()` clear consecutive_losses** — 4 losses ต่อกันถูก reset ตอนเที่ยงคืน | `risk.py:316` | P2 | เอา `consecutive_losses = 0` ออกจาก `reset_daily()` |
| 9 | **Timeframe parsing broken** — `"4h"` parse เป็น 60 แทน 240 | `main.py:799` | P2 | Parse ให้ถูก: extract number + multiply by 60 for hours |
| 10 | **`datetime.utcnow()` deprecated** — ใช้ inconsistent กับ `datetime.now(timezone.utc)` | `logger.py` | P3 | Standardize ทั้ง codebase |

#### 5A.2 — Performance Quick Wins

| # | Issue | File | Impact | Fix |
|---|-------|------|--------|-----|
| 11 | **Duplicate OHLCV fetches** — context_builder fetch ซ้ำกับ main loop | `main.py:517-524` vs `context_builder.py:105-115` | -200ms/signal | Pass DataFrames เข้า `build()` |
| 12 | **Redundant indicator computation** — `add_indicators()` ถูกเรียก 2 ครั้ง | `strategy.py` + `context_builder.py:209` | CPU waste | Return enriched DataFrame จาก `generate_signal()` |
| 13 | **SQLite connection-per-query** — 9+ connections per trade cycle | `logger.py` | Latency | ใช้ persistent connection per instance |
| 14 | **Missing database indexes** — full table scan ทุก query | `logger.py` | Slow dashboard | Add indexes on `status`, `timestamp`, `outcome` |

#### 5A.3 — Deployment Blockers

| # | Issue | File | Risk | Fix |
|---|-------|------|------|-----|
| 15 | **Heartbeat file never written** — monitoring + Docker healthcheck broken ตั้งแต่ day 1 | `main.py` (missing) | P0 | เพิ่ม `Path("heartbeat").write_text(str(time.time()))` ท้าย loop |
| 16 | **Backup tar.gz world-readable** — API keys อยู่ใน backup ที่ใครก็อ่านได้ | `deploy/backup.sh` | P1 | `chmod 600` on tar.gz หรือไม่ backup `.env` |
| 17 | **Monitor state in /tmp** — reboot ลบ state → alert flood + duplicate notifications | `deploy/monitoring.py` | P2 | Move state file to persistent volume |

---

### Phase 5B: Profitability Improvements

> **เป้าหมาย**: เพิ่มโอกาสทำกำไร ลด false signals ปรับ risk/reward
> **ประมาณ**: 10 items | Owner: Backend Dev + Trader Expert

#### 5B.1 — Signal Quality

| # | Improvement | Current | Proposed | Expected Impact |
|---|------------|---------|----------|----------------|
| 1 | **Config-driven RSI ranges** — magic number offsets ใน code | RSI long: 48-75, short: 25-52 (hardcoded offset) | Explicit config keys, tighten short to 30-50 | +10-15% short signal quality |
| 2 | **Volume filter ไม่ทำงาน** — `volume_mult: 1.0` = ไม่ filter | Always passes | Set 1.2x min, add 3.0x max (exhaustion filter) | +3-5% win rate |
| 3 | **Same-side cooldown** — re-entry ทันทีหลัง SL hit (whipsaw) | No cooldown | 1hr after close, 2hr after SL | +2-3% win rate |
| 4 | **Slippage rate inconsistent** — 3 ค่าต่างกันใน 3 files | 0.02%, 0.05%, 0.15% (backtest default) | Standardize จาก config, single source of truth | Backtest accuracy |

#### 5B.2 — Exit Optimization

| # | Improvement | Current | Proposed | Expected Impact |
|---|------------|---------|----------|----------------|
| 5 | **Partial take-profit** — all-or-nothing exit | 100% at SL or TP | 50% at TP1 (2x ATR), trail rest, move SL to BE | +0.2-0.4 profit factor |
| 6 | **Dynamic ATR for trailing stop** — ใช้ ATR ตอน entry ตลอด | Static ATR from entry | Update ATR ทุก cycle จาก fresh candles | +0.1-0.2 profit factor |
| 7 | **Regime-adaptive trailing stop** — fixed multiplier ทุกสภาพตลาด | `atr_trail_mult: 1.8` always | Trending: 2.0, Ranging: 1.2, Volatile: 2.5 | +0.1-0.3 profit factor |

#### 5B.3 — Risk Optimization

| # | Improvement | Current | Proposed | Expected Impact |
|---|------------|---------|----------|----------------|
| 8 | **Weekend/low-liquidity filter** — trade 24/7 ไม่สนใจ liquidity | Full size always | 50% size on weekends | +5-10% risk-adjusted return |
| 9 | **Continuous dynamic risk factor** — step function กระโดด | 3 levels: 0.7/0.85/1.0 | Linear scale: smooth transition | Smoother equity curve |
| 10 | **Backtest SL/TP same-candle bias** — SL always checked first | Pessimistic bias | Use candle close direction to resolve | More accurate backtest |

---

### Phase 5C: AI Advisor Enhancement

> **เป้าหมาย**: ทำให้ AI layer มีคุณค่ามากขึ้น ใช้ข้อมูลที่มีอยู่แล้วให้ครบ
> **ประมาณ**: 10 items | Owner: Backend Dev + Crypto Expert

#### 5C.1 — AI ได้ข้อมูลไม่ครบ (P0-P1)

| # | Issue | Detail | Fix |
|---|-------|--------|-----|
| 1 | **40% ของข้อมูลไม่ถูกส่งให้ AI** | 1h candles, orderbook imbalance, volume ratio, bid/ask spread — fetch แล้วแต่ไม่ใส่ prompt | เพิ่มใน `build_user_prompt()` |
| 2 | **AI regime classification ไม่ถูกใช้** | AI บอก "ranging" แต่ risk manager ไม่ฟัง — ใช้แค่ ATR-based regime | Wire AI regime → risk manager (ใช้ conservative ตัว) |
| 3 | **Keyword sentiment ผิดบ่อย** | "crash predictions prove wrong" = negative | ลบ `_estimate_sentiment()`, ให้ Claude อ่าน headlines เอง |
| 4 | **News lookback 2hr สั้นเกินไป** | ลืม SEC lawsuit หลัง 2 ชม. | เพิ่มเป็น 12-24hr, เก็บ critical news 48hr |

#### 5C.2 — AI Calibration ไม่แม่น (P1-P2)

| # | Issue | Detail | Fix |
|---|-------|--------|-----|
| 5 | **Binary win/loss calibration** | AI size down แล้ว trade แพ้ = AI ผิด (แต่จริงๆ AI ถูก) | Track AI value-add: compare with-AI vs without-AI PnL |
| 6 | **Influence multiplier step function** | 55.1% → 1.0x, 54.9% → 0.75x (กระโดด 33%) | ใช้ linear interpolation |
| 7 | **Calibration bucket asymmetric** | Edge buckets กว้างกว่า mid-range | ใช้ fixed buckets (0-0.2, 0.2-0.4, etc.) |

#### 5C.3 — Market Context Gaps (P2-P3)

| # | Issue | Detail | Fix |
|---|-------|--------|-----|
| 8 | **Price action ใช้ forming candle** | AI เห็น candle ที่ยังไม่ปิด (ไม่ตรงกับ signal logic) | ใช้ `candles[-2]` ให้ตรงกับ signal |
| 9 | **ไม่มี liquidation level context** | ไม่รู้ว่า entry อยู่ใกล้ liquidation cluster | Estimate จาก OI + leverage, flag ใน prompt |
| 10 | **CryptoPanic votes ไม่ถูกใช้** | Free crowd sentiment data ถูกทิ้ง | Extract vote data (bullish/bearish counts) |

---

### Phase 5D: Architecture & Infrastructure

> **เป้าหมาย**: Refactor ให้ maintainable, เตรียม production
> **ประมาณ**: 8 items | Owner: SA + Backend Dev + DevOps

#### Architecture

| # | Improvement | Detail | Effort |
|---|------------|--------|--------|
| 1 | **Refactor `trading_loop` God function** — 540 lines | แยกเป็น state machine: INIT → MONITOR → EVALUATE → EXECUTE → WAIT | Large |
| 2 | **Async exchange client** — full async conversion | `AsyncBybitClient` wrapper with `asyncio.to_thread()` | Medium |
| 3 | **Exchange abstraction layer (TradingPort)** — decouple from Bybit | Protocol/interface for testability + multi-exchange support | Medium |
| 4 | **Backtest/live parity audit** — backtest ไม่ model AI adjustments | Compare backtest vs testnet results, add AI replay mode | Large |

#### Infrastructure

| # | Improvement | Detail | Effort |
|---|------------|--------|--------|
| 5 | **Disk space monitoring** | เพิ่ม disk check ใน `monitoring.py` | 20 min |
| 6 | **Streamlit WebSocket auth bypass** | `/_stcore/stream` มี `auth_basic off` | 2 min |
| 7 | **Crash-loop backoff** | Bot crash ทันที = tight restart loop | Add `restart_policy` with delay |
| 8 | **SSH hardening** | Port 22 open to world | Restrict to specific IP or non-standard port |

---

### Implementation Order (Recommended)

```
Week 1-2: Phase 5A (Critical Fixes)
├── 5A.1 Correctness — items 1-7 (P0/P1)
├── 5A.2 Performance — items 11-14
└── 5A.3 Deployment — items 15-17

Week 3-4: Phase 5B (Profitability)
├── 5B.1 Signal Quality — items 1-4 (low effort, high impact)
├── 5B.2 Exit Optimization — items 5-7
└── 5B.3 Risk Optimization — items 8-10

Week 5-6: Phase 5C (AI Enhancement)
├── 5C.1 Missing Data — items 1-4 (biggest bang for buck)
├── 5C.2 Calibration — items 5-7
└── 5C.3 Context Gaps — items 8-10

Week 7+: Phase 5D (Architecture)
├── Architecture refactoring
└── Infrastructure hardening
```

### Validation Plan

- **ทุก Phase**: Run `pytest tests/ -v` ก่อนและหลัง
- **Phase 5A**: ต้อง pass ก่อน deploy testnet
- **Phase 5B**: Backtest เปรียบเทียบ before/after config changes
- **Phase 5C**: Monitor AI accuracy rolling 30 days on testnet
- **Phase 5D**: Minimum 7 days testnet ก่อน live

### Agent Assignments

| Phase | Lead Agent | Support |
|-------|-----------|---------|
| 5A | Backend Dev | SA (review), DevOps (items 15-17) |
| 5B | Trader Expert | Backend Dev (implement) |
| 5C | Crypto Expert | Backend Dev (implement) |
| 5D | SA | Backend Dev + DevOps |
