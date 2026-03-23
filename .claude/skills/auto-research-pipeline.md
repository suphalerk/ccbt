---
name: auto-research-pipeline
description: AI-driven research pipeline — Claude thinks up strategy ideas, then auto_pipeline.py executes sweep/verify/audit/backtest. Claude reviews results and decides to deploy.
autoInvoke: false
---

# Auto Research Pipeline (AI + Auto)

## When to Use
User says: "research หาแผนใหม่", "หาเหรียญเพิ่ม", "improve portfolio", "optimize", or any research request.

## Flow

### Step 1: AI IDEATE (Claude thinks)

Claude analyzes the current portfolio, identifies gaps, and generates strategy ideas.

**Sources to consider (ranked by success rate):**
1. GitHub open-source bots (je-suis-tm, FMZQuant, OctoBot, NostalgiaForInfinity)
2. TradingView/FMZQuant strategy collections
3. Indicator combinations (2-3 indicators AND)
4. Parameter optimization on weak bots
5. Academic papers / quant blogs

**What NOT to suggest (proven failures):**
- 15m/5m scalping (fees > edge)
- Candlestick patterns (pin bar, engulfing, inside bar)
- Mean reversion without range detection
- Multi-confluence 4-5 indicators (too selective)
- MACD standalone, EMA pullback, RSI divergence

**Output:** Write strategy ideas to a JSON file:
```python
# Example: research/ideas_round13.json
[
    {"signal": "new_signal_name", "tf": "4h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    ...
]
```

Or if ideas use EXISTING engine signals, write directly:
```python
strategies = [
    {"signal": "dual_thrust", "tf": "1h", "sl": 2.0, "tp": 4.0, "trail": 3.0},
    {"signal": "ichi_adx", "tf": "4h", "sl": 2.5, "tp": 5.0, "trail": 3.0},
]
```

If ideas need NEW indicators/signals not in engine:
1. Implement in bot/data.py + bot/strategy.py + backtest/engine.py first
2. Run pytest to verify
3. Then proceed to Step 2

### Step 2: AUTO EXECUTE (auto_pipeline.py)

Run the automated pipeline:

```bash
# If using existing signals:
python3 research/auto_pipeline.py --full --ideate all

# If using custom strategy file:
python3 research/auto_pipeline.py --full --strategies research/ideas_round13.json

# If optimizing weak bots:
python3 research/auto_pipeline.py --improve-weak

# If testing specific coins:
python3 research/auto_pipeline.py --full --coins btcusdt,ethusdt
```

The pipeline automatically:
- Sweeps all coins × strategies (BacktestEngine)
- Filters PF >= 1.2, trades >= 6
- Walk-forward audit (OOS >= 60% of IS)
- Shared wallet backtest ($200, R-multiple FIXED, max 5 concurrent)
- Generates monthly + daily reports

### Step 3: AI REVIEW (Claude analyzes results)

Claude reads the pipeline output and:

1. **Check for red flags:**
   - Any PF > 10 with < 10 trades → likely overfitting
   - Z-Score strategies with < 5 engine trades → reject
   - BTC/WIF trades: verify 1% risk × R = dollar PnL (sanity check)
   - DD > 30% → too risky
   - January/November spikes → check if one big trade dominates

2. **Compare with current portfolio:**
   - Does new strategy beat current for this coin?
   - Does it add diversification (different strategy type)?
   - Does it have enough trades (>= 15 for deploy)?

3. **Decide: DEPLOY / REJECT / NEED MORE DATA**
   - DEPLOY: PF >= 1.3, trades >= 15, walk-forward stable, beats current
   - REJECT: walk-forward fails, low trades, or worse than current
   - NEED MORE DATA: promising but < 12 months data

### Step 4: AI DEPLOY (Claude executes)

If deploying:
```bash
# Generate configs
python3 research/auto_pipeline.py --deploy --input data/pipeline_xxx.json

# Restart docker
docker compose -f docker-compose-multi.yml up -d --build

# Update docs
# Claude updates CLAUDE.md and memory
```

## Key Rules (learned from 12 rounds)

1. **R-multiple MUST use actual engine risk** — BTC=5%, WIF=3%, others=1%
2. **Walk-forward is NON-NEGOTIABLE** — OOS PF >= 60% of IS PF
3. **Min 15 trades for deployment** — under 15 = luck not edge
4. **4H > 1H** for most strategies
5. **ADX confirmation** boosts any strategy
6. **Always audit** when results look too good (>500%/yr)
7. **Shared wallet always 1% risk** regardless of bot config
8. **Max 5 concurrent positions**
9. **Data >= 12 months** required

## Example Conversation Flow

User: "research หาแผนใหม่"

Claude:
1. Searches GitHub/TradingView for new strategy ideas
2. Designs 5-10 strategies based on what's trending
3. Implements new signals if needed (bot/data.py + bot/strategy.py)
4. Runs: `python3 research/auto_pipeline.py --full --strategies research/ideas_roundN.json`
5. Reviews results, audits suspicious numbers
6. Reports to user: "พบ X เหรียญที่ผ่าน audit, ต้องการ deploy ไหม?"
7. If yes: generates configs, deploys, updates docs
