# Product Requirements Document (PRD)
# CCBT — Crypto Trading Bot with Claude AI Advisor

**Version**: 1.0
**Date**: 2026-03-18
**Status**: Active Development (Testnet Phase)

---

## 1. Product Vision

สร้าง automated crypto perpetual futures trading bot ที่ใช้ technical analysis ร่วมกับ Claude AI advisor เพื่อเทรด BTC/USDT บน Bybit exchange อย่างมีระบบ มี risk management ที่เข้มงวด และมี dashboard สำหรับ monitoring แบบ real-time

### Goals
- สร้างรายได้จากการเทรด crypto futures อย่างอัตโนมัติ
- ใช้ AI เป็น advisor เพื่อเพิ่มความแม่นยำของ signal
- จำกัดความเสี่ยงด้วย circuit breakers และ risk management หลายชั้น
- ให้ transparency ผ่าน dashboard และ trade journal ที่บันทึกทุก decision

### Non-Goals
- ไม่ใช่ high-frequency trading (HFT)
- ไม่ใช่ multi-exchange arbitrage bot
- ไม่รองรับ spot trading (perpetual futures เท่านั้น)

### Autonomous Operation
Bot ออกแบบให้ทำงาน **fully autonomous** โดยไม่ต้องมี human oversight:
- AI Advisor + Calibration System ทำหน้าที่ตัดสินใจแทนคน
- Circuit breakers หลายชั้น (daily loss, consecutive losses, API errors) คือ safety net อัตโนมัติ
- Graceful shutdown ปิด positions เองเมื่อเกิดปัญหา
- Telegram alerts แจ้งเตือน แต่ไม่ต้องรอ human action
- Dashboard สำหรับ review ย้อนหลัง ไม่ใช่ real-time oversight

---

## 2. Target Users

| User | Description |
|------|-------------|
| **Primary** | Solo crypto trader ที่ต้องการ automate strategy ของตัวเอง |
| **Secondary** | Developer ที่สนใจ AI-assisted trading systems |

---

## 3. Core Features

### 3.1 Trading Engine (Complete)
| Feature | Description | Status |
|---------|-------------|--------|
| EMA Crossover Signal | EMA(9)/EMA(21) + EMA(5/13) fast crossover + EMA(50) trend filter | Done |
| Multi-Timeframe | 15m signal + 1h trend confirmation | Done |
| RSI Confirmation | RSI(14) directional ranges (long 48-68, short 30-52) + EMA slope filter | Done |
| ATR-Based SL/TP | Dynamic SL (1.2×), TP (3.0×), Trail (2.0×), post-TP1 trail (3.0×) | Done |
| Volume Filter | Volume > MA(20) confirmation | Done |
| Market Regime Detection | Trending/Ranging/Volatile classification with per-candle propagation | Done |
| Trailing Stop | ATR-based ratcheting trailing stop, regime-adaptive multipliers | Done |
| Commission-Adjusted R:R | Net R:R validation after fees (full TP distance, not blended) | Done |
| Flexible Cooldown | Signal quality scoring overrides rigid cooldown for high-quality signals | Done |
| Adaptive Sizing | Signal quality score (0-1) → tiered position sizing (A/B/C/D grades) | Done |
| Pyramiding | Up to 3 adds into winning positions at ATR profit levels with SL ratcheting | Done |
| Trading Hours Filter | Skip 00:00-02:59 UTC dead hours | Done |
| Regime Filter | Skip ranging markets for trend-following signals | Done |

### 3.2 AI Advisor Layer (Complete)
| Feature | Description | Status |
|---------|-------------|--------|
| Claude Integration | Anthropic SDK with structured prompts | Done |
| Advisor Mode | Nuanced adjustments (not binary gate) | Done |
| Position Size Modifier | AI adjusts size 0.5-1.5× | Done |
| SL/TP Adjustments | AI tweaks SL/TP multipliers | Done |
| Calibration System | Rolling accuracy tracking + influence auto-adjust | Done |
| Market Context | Candles, indicators, funding rate, OI, news, history | Done |
| Timeout Fallback | 10s timeout → execute without AI | Done |

### 3.3 Risk Management (Complete)
| Feature | Description | Status |
|---------|-------------|--------|
| Position Sizing | 3% base risk per trade, tiered by signal quality (A=2x, B=1x, C=0.5x, D=skip) | Done |
| Daily Loss Limit | 9% max daily loss → halt | Done |
| Consecutive Loss Limit | 5 losses → 1h cooldown (never overridden by flexible cooldown) | Done |
| Max Positions | 2 concurrent (long + short) | Done |
| API Error Circuit Breaker | 3 errors → halt | Done |
| Leverage Limit | Config max 10x, dynamic per tier (A=1.5x mult, C=0.8x mult) | Done |
| Pyramiding Risk Control | SL ratchets to breakeven/profit on each add; trend alignment required | Done |
| State Restoration | Resume risk state from DB on restart | Done |
| Graceful Shutdown | Close positions + cancel orders on SIGINT | Done |

### 3.4 Dashboard (Complete)
| Feature | Description | Status |
|---------|-------------|--------|
| Account Overview | Balance, PnL, positions, status | Done |
| Candlestick Charts | Interactive Plotly with EMA overlay | Done |
| RSI/ATR Subplots | Indicator visualization | Done |
| Trade Log | Full history with filters | Done |
| Equity Curve | Cumulative PnL chart | Done |
| Performance Metrics | Win rate, Sharpe, profit factor | Done |
| AI Accuracy Analysis | Confidence histograms, calibration curves | Done |
| Risk Monitor | Gauges, circuit breaker status | Done |
| Auto-Refresh | 30-second refresh cycle | Done |

### 3.5 Backtesting (Complete)
| Feature | Description | Status |
|---------|-------------|--------|
| Event-Driven Engine | Realistic simulation with fees | Done |
| Risk Compliance | Applies same risk rules as live | Done |
| Performance Metrics | Sharpe, max drawdown, profit factor | Done |

### 3.6 Deployment (Complete)
| Feature | Description | Status |
|---------|-------------|--------|
| Docker Setup | Multi-stage build, non-root user | Done |
| Nginx Reverse Proxy | HTTPS, basic auth, rate limiting | Done |
| SSL/Certbot | Let's Encrypt auto-renewal | Done |
| Monitoring | Health checks + Telegram alerts (5min) | Done |
| Backup | Daily SQLite backup with retention | Done |
| Security | fail2ban + UFW firewall | Done |

---

## 4. Planned Features (Roadmap)

### Phase 7 — Multi-Asset Support
| Feature | Description | Priority |
|---------|-------------|----------|
| Multi-Symbol | Support ETH, SOL, etc. simultaneously | High |
| Per-Symbol Config | Individual risk/strategy params per asset | High |
| Portfolio Correlation | Cross-asset risk management | Medium |
| Symbol Scanner | Auto-detect high-opportunity pairs | Low |

### Phase 8 — Advanced Strategies
| Feature | Description | Priority |
|---------|-------------|----------|
| Mean Reversion | Bollinger Band / RSI oversold-overbought (implemented, disabled — negative PF on BTC) | Done (disabled) |
| Adaptive Sizing | Signal quality tiered position sizing | Done |
| Pyramiding | Add to winners at ATR profit levels | Done |
| Breakout Strategy | Support/Resistance breakout detection | Medium |
| Order Flow Analysis | Orderbook depth + liquidation heatmap | Medium |
| Strategy Switching | Auto-switch based on market regime | Low |

### Phase 9 — Enhanced AI
| Feature | Description | Priority |
|---------|-------------|----------|
| Multi-Model Ensemble | Use multiple AI models for consensus | High |
| Sentiment Analysis | Social media + news NLP scoring | Medium |
| Pattern Recognition | CNN/LSTM for chart pattern detection | Medium |
| Self-Learning | AI learns from its own calibration data | Low |
| Market Narrative | AI generates daily market thesis | Low |

### Phase 10 — Advanced Risk & Portfolio
| Feature | Description | Priority |
|---------|-------------|----------|
| Kelly Criterion | Optimal position sizing from win rate + payoff (analyzed: half-Kelly ~2.1%, current 3% is near-optimal) | Done (analyzed) |
| Dynamic Leverage | Volatility/regime-based leverage adjustment | Medium |
| Correlation Risk | Reduce exposure when assets correlate | Medium |
| Drawdown Recovery | Progressive sizing after drawdowns | Medium |
| VaR Calculation | Value at Risk for portfolio | Low |

### Phase 11 — User Experience
| Feature | Description | Priority |
|---------|-------------|----------|
| Mobile Alerts | Push notifications via app | High |
| Trade Replay | Visual replay of past trades | Medium |
| Strategy Builder UI | No-code strategy configuration | Medium |
| Multi-User | Support multiple accounts/strategies | Low |
| API Endpoints | REST API for external integrations | Low |

---

## 5. Technical Requirements

### Performance
- Signal generation: < 1 second per candle
- Order execution: < 2 seconds from signal
- AI advisor response: < 10 seconds (with fallback)
- Dashboard refresh: Every 30 seconds
- Uptime target: 99.5% (allows for maintenance)

### Security
- API keys: NO withdrawal permission
- Environment variables: Never in git
- Docker: Non-root user
- Network: UFW + fail2ban + nginx rate limiting
- Dashboard: Basic auth + HTTPS
- SL verification: 3 retries on every order

### Data Integrity
- SQLite WAL mode for concurrent reads
- Every trade decision logged (including AI skips)
- Calibration history retained for accuracy analysis
- Daily backups with 30-day retention

### Infrastructure
- VPS: Ubuntu 22.04 (Hetzner CX22, 2vCPU/4GB)
- Docker Compose: bot + dashboard services
- Monthly budget: $7-17

---

## 6. Success Metrics

### Testnet Phase (Current)
- [ ] Bot runs 14+ days without crashes or manual intervention
- [ ] All circuit breakers trigger and recover autonomously
- [ ] AI calibration accuracy > 50% after 30 trades
- [ ] Dashboard displays all data correctly
- [ ] Backup/restore works end-to-end
- [ ] Telegram alerts fire correctly on critical events
- [ ] Bot recovers from API errors without human restart

### Live Phase (Next)
- [ ] Positive expectancy over 30 days (fully unattended)
- [ ] Max drawdown < 15%
- [ ] Profit factor > 1.5
- [ ] Pyramiding adds executing correctly (SL ratcheting verified)
- [ ] Adaptive sizing tiers distributing as expected (A ~15%, B ~35%, C ~35%, D ~15% skip)
- [ ] AI influence multiplier > 0.75 (accuracy > 45%)
- [ ] Zero manual interventions required over 30-day period

### Scale Phase (Future)
- [ ] 3+ months profitable with annualized return > 100%
- [ ] Multi-asset support live
- [ ] Capital scaled to $1,000+

---

## 7. Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| API outage | Missed trades/stuck positions | Circuit breaker, graceful shutdown, SL on exchange |
| AI hallucination | Bad trade adjustments | Calibration system, influence dampening, hard limits on modifiers (0.5-1.5x) |
| Flash crash | Large losses | ATR-based SL, max position limits, daily loss halt (3%) |
| Bug in signal logic | Incorrect entries | Unit tests, backtesting, testnet phase |
| Exchange liquidation | Total position loss | 10x leverage with tiered sizing, SL ratcheting on pyramids, 9% daily loss halt |
| Data feed errors | Stale signals | Timestamp validation, API error counting, 3-error halt |
| Unattended failure | Bot stuck/frozen without human notice | Heartbeat monitoring (5min), Telegram alerts, Docker auto-restart |
| Prolonged losing streak | Equity erosion while unmonitored | 5-loss cooldown, 3% daily halt, dynamic size reduction on low win rate |
| Market regime shift | Strategy mismatch over days/weeks | Regime detection auto-adjusts sizing (50-100%), AI calibration tracks accuracy |
| Config drift | Suboptimal params running too long | AI calibration influence auto-adjusts, weekly automated performance reports |

---

## 8. Claude Code Skills (Installed)

ติดตั้งแล้ว 10 skills ใน `.claude/skills/` ปรับสำหรับ crypto perpetual futures จาก [SkillsMP](https://skillsmp.com/) และ [claude-trading-skills](https://github.com/tradermonty/claude-trading-skills):

### Auto-Invoked Skills (Claude เรียกใช้เองเมื่อเกี่ยวข้อง)
| Skill | Command | หน้าที่ | Status |
|-------|---------|---------|--------|
| **Technical Analyst** | `/technical-analyst` | วิเคราะห์ chart ด้วย EMA/RSI/ATR | Installed |
| **Backtest Expert** | `/backtest-expert` | Stress-test trading strategies | Installed |
| **Position Sizer** | `/position-sizer` | คำนวณ position size สำหรับ futures + leverage | Installed |
| **Macro Regime Detector** | `/macro-regime-detector` | ตรวจจับ macro regime จาก cross-asset analysis | Installed |
| **Market News Analyst** | `/market-news-analyst` | วิเคราะห์ข่าว crypto + impact scoring | Installed |
| **Trader Memory Core** | `/trader-memory-core` | Track thesis lifecycle (IDEA → CLOSED) | Installed |
| **Crypto Signal Validator** | `/crypto-signal-validator` | Multi-layer signal validation (custom) | Installed |

### Manual-Only Skills (ต้องเรียกเอง)
| Skill | Command | หน้าที่ | Status |
|-------|---------|---------|--------|
| **Scenario Analyzer** | `/scenario-analyzer "event"` | สร้าง 18-month scenarios จากข่าว | Installed |
| **Strategy Pivot Designer** | `/strategy-pivot-designer` | วินิจฉัย stagnation + เสนอ strategy ใหม่ | Installed |
| **Edge Pipeline Orchestrator** | `/edge-pipeline-orchestrator` | Pipeline สร้าง strategy ตั้งแต่ต้นจนจบ | Installed |

### ยังไม่ได้ติดตั้ง (สำหรับ Future Phases)
| Skill | Source | เหตุผล |
|-------|--------|--------|
| **Options Strategy Advisor** | tradermonty | สำหรับขยายไป options ในอนาคต |
| **Pair Trade Screener** | tradermonty | Statistical arbitrage — สำหรับ multi-asset phase |
| **Portfolio Manager** | tradermonty | Portfolio rebalancing — สำหรับ multi-asset phase |
| **Market Top/Bottom Detectors** | tradermonty | Market timing — enhance entry/exit timing |

---

## 9. Agent Team

7 specialized agents ที่ทำงานเป็นทีมผ่าน shared task lists และ direct messaging (experimental feature):

### Team Structure
```
              ┌──────────┐
              │    PM     │ Product Owner
              │  sonnet   │
              └─────┬─────┘
         ┌──────────┼──────────┐
         ▼          ▼          ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │    SA    │ │  Trader  │ │  Crypto  │
   │   opus   │ │  Expert  │ │  Expert  │
   └────┬─────┘ └──────────┘ └──────────┘
   ┌────┼──────────┬──────────┐
   ▼    ▼          ▼          ▼
┌──────────┐ ┌──────────┐ ┌──────────┐
│ Backend  │ │ Frontend │ │  DevOps  │
│   Dev    │ │   Dev    │ │          │
│  sonnet  │ │  sonnet  │ │  sonnet  │
└──────────┘ └──────────┘ └──────────┘
```

### Agent Details
| Agent | Model | Role | Preloaded Skills | Memory |
|-------|-------|------|------------------|--------|
| **pm** | sonnet | Product Manager — roadmap, features, prioritization | scenario-analyzer, trader-memory-core | project |
| **sa** | opus | Solution Architect — architecture, technical decisions | backtest-expert, edge-pipeline-orchestrator | project |
| **backend-dev** | sonnet | Backend Developer — Python code, trading engine, tests | crypto-signal-validator, position-sizer, backtest-expert | project |
| **frontend-dev** | sonnet | Frontend Developer — Streamlit dashboard, Plotly charts | technical-analyst | project |
| **devops** | sonnet | DevOps Engineer — Docker, deployment, monitoring, security | (none) | project |
| **trader-expert** | opus | Trader Expert — strategy, risk management, backtesting | 7 skills | project |
| **crypto-expert** | opus | Crypto Expert — crypto markets, on-chain, exchange mechanics | 4 skills | project |

### Model Selection Rationale
| Model | Agents | เหตุผล |
|-------|--------|--------|
| **Opus** | SA, Trader Expert, Crypto Expert | Deep reasoning สำหรับ architecture, strategy, domain expertise |
| **Sonnet** | PM, Backend Dev, Frontend Dev, DevOps | Speed + quality balance สำหรับ execution tasks |

### Cross-Agent Communication Protocol
| From | To | When |
|------|----|------|
| Backend Dev | Frontend Dev | DB schema changes |
| Backend Dev | DevOps | New dependencies or config changes |
| Trader Expert | All | Strategy parameter changes |
| Crypto Expert | All | Market risk alerts (broadcast) |
| PM | All | Priority changes, roadmap updates |
| SA | Backend/Frontend/DevOps | Architecture decisions |

### Usage Examples
```bash
# สร้างทีมสำหรับ task
"Create an agent team with pm, sa, backend-dev, trader-expert to implement Phase 7"

# @mention agent เฉพาะ
@trader-expert "analyze trailing stop performance"
@crypto-expert "assess current funding rate environment"

# รัน session ทั้งหมดเป็น agent
claude --agent trader-expert

# Debug ด้วยหลาย hypotheses
"Spawn trader-expert and crypto-expert to investigate why win rate dropped"
```
