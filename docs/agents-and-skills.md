# Agent Team & Skills

## Agent Team

11 specialized agents configured as teammates that can collaborate via shared task lists and direct messaging. Enable via `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (already set in `.claude/settings.json`).

### Team Structure
```
                        ┌──────────────┐
                        │ ORCHESTRATOR │ Research loop coordinator
                        │    opus      │ Discover→Test→Verify→Deploy
                        └──────┬───────┘
                               │
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
         ┌──────────┐  ┌──────────┐  ┌──────────────┐
         │    PM    │  │ Strategy │  │    Quant      │
         │  sonnet  │  │  Scout   │  │  Researcher   │
         │ Planning │  │  Ideas   │  │  Statistics   │
         └────┬─────┘  └──────────┘  └──────────────┘
              │
    ┌─────────┼─────────┬─────────────┐
    ▼         ▼         ▼             ▼
┌────────┐┌────────┐┌──────────┐┌──────────┐
│   SA   ││ Trader ││  Crypto  ││    QA    │
│  opus  ││ Expert ││  Expert  ││ Verifier │
│ Arch.  ││Strategy││ Domain   ││ Bugs/Bias│
└───┬────┘└────────┘└──────────┘└──────────┘
    │
┌───┼─────────┬──────────┐
▼   ▼         ▼          ▼
┌────────┐┌────────┐┌────────┐
│Backend ││Frontend││ DevOps │
│  Dev   ││  Dev   ││        │
│ sonnet ││ sonnet ││ sonnet │
└────────┘└────────┘└────────┘
```

### Research Flow (agents communicate in this order)
```
1. DISCOVER:  orchestrator → [strategy-scout + crypto-expert + quant-researcher] (parallel)
2. DESIGN:    orchestrator → [quant-researcher + trader-expert] (parallel)
3. SWEEP:     orchestrator → backend-dev → quant-researcher (sequential)
4. VERIFY:    orchestrator → [qa-verifier + trader-expert] (parallel, NEVER skip)
5. IMPLEMENT: orchestrator → backend-dev → qa-verifier → sa (sequential)
6. DEPLOY:    orchestrator → devops → frontend-dev (parallel)
```

### Agent Files: `.claude/agents/`
| Agent | Model | Role | Skills (12 total) |
|-------|-------|------|-------------------|
| `orchestrator` | opus | Research Loop Coordinator | team-orchestrator, research, trader-memory-core, scenario-analyzer, backtest-expert |
| `pm` | sonnet | Product Manager | scenario-analyzer, trader-memory-core, team-orchestrator |
| `strategy-scout` | sonnet | Strategy Idea Discovery | strategy-pivot-designer, edge-pipeline-orchestrator, technical-analyst, market-news-analyst, scenario-analyzer |
| `quant-researcher` | opus | Experiment Design & Statistics | backtest-expert, position-sizer, technical-analyst, macro-regime-detector, crypto-signal-validator |
| `qa-verifier` | opus | Bug/Bias Detection & QA | backtest-expert, crypto-signal-validator, position-sizer, technical-analyst |
| `sa` | opus | Solution Architect | backtest-expert, edge-pipeline-orchestrator |
| `trader-expert` | opus | Trading Strategy Authority | technical-analyst, backtest-expert, position-sizer, macro-regime-detector, strategy-pivot-designer, crypto-signal-validator, trader-memory-core |
| `crypto-expert` | opus | Crypto Domain Authority | macro-regime-detector, market-news-analyst, scenario-analyzer, technical-analyst |
| `backend-dev` | sonnet | Backend Developer | crypto-signal-validator, position-sizer, backtest-expert |
| `frontend-dev` | sonnet | Frontend Developer | technical-analyst |
| `devops` | sonnet | DevOps Engineer | (infra focused) |

### Usage
```bash
# Run a full research round (orchestrator manages everything)
claude --agent orchestrator "Run research round 11: find 10 new strategies, sweep, verify, deploy"

# PM for non-research complex tasks
claude --agent pm "Plan the gold bot deployment to OANDA"

# Direct specialist access
claude --agent trader-expert "analyze trailing stop performance"
claude --agent strategy-scout "find 10 new strategy ideas from GitHub repos with >1K stars"
claude --agent quant-researcher "analyze portfolio correlation and find gaps"
claude --agent qa-verifier "verify round 10 backtest results for look-ahead bias"
```

## Skills (`.claude/skills/`)

10 installed skills adapted for crypto perpetual futures:

### Auto-Invoked (Claude uses when relevant)
| Skill | Command | Purpose |
|-------|---------|---------|
| technical-analyst | `/technical-analyst` | Chart analysis with EMA/RSI/ATR |
| backtest-expert | `/backtest-expert` | Strategy validation + stress testing |
| position-sizer | `/position-sizer` | Position sizing for futures + leverage |
| macro-regime-detector | `/macro-regime-detector` | Cross-asset macro regime detection |
| market-news-analyst | `/market-news-analyst` | Crypto news impact analysis |
| trader-memory-core | `/trader-memory-core` | Thesis lifecycle tracking |
| crypto-signal-validator | `/crypto-signal-validator` | Multi-layer signal validation (custom) |

### Manual-Only (invoke with /command)
| Skill | Command | Purpose |
|-------|---------|---------|
| research | `/research` | Iterative strategy optimization loop (agent team + backtest) |
| scenario-analyzer | `/scenario-analyzer "event"` | 18-month scenario projections |
| strategy-pivot-designer | `/strategy-pivot-designer` | Strategy stagnation diagnosis + pivots |
| edge-pipeline-orchestrator | `/edge-pipeline-orchestrator` | Full strategy development pipeline |

Sources: [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills), [SkillsMP](https://skillsmp.com/)
