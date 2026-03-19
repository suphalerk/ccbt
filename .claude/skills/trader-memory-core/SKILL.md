---
name: trader-memory-core
description: Track trading theses and position lifecycle from idea through closure. Use when managing trade ideas, tracking active positions, reviewing trade outcomes, or generating postmortem analysis. Complements the bot's SQLite trade journal with higher-level thesis tracking.
---

# Trader Memory Core (Crypto-Adapted)

Adapted from [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills).

## Overview

Persistent tracking for trading theses across their complete lifecycle. While the bot's `logger.py` tracks individual trades, this skill tracks higher-level theses — the reasoning behind why you entered, what you expected, and what actually happened.

## Thesis Lifecycle

```
IDEA → ENTRY_READY → ACTIVE → CLOSED
                  ↘ INVALIDATED
```

### States

- **IDEA**: Initial observation or signal hypothesis
- **ENTRY_READY**: Conditions met, waiting for bot signal alignment
- **ACTIVE**: Position opened (bot has live trade)
- **CLOSED**: Position closed, PnL recorded
- **INVALIDATED**: Thesis no longer valid (conditions changed)

## Workflow

### Step 1: Register Thesis

When a new trade idea forms:

```yaml
# thesis_BTC_20260318.yaml
thesis_id: BTC_20260318_001
symbol: BTCUSDT
direction: long
status: IDEA
created: 2026-03-18T10:00:00Z

hypothesis: "BTC forming higher lows with EMA(9) about to cross above EMA(21) on 15m,
             1h trend filter bullish. Expecting continuation to $68K resistance."

entry_conditions:
  - EMA(9) > EMA(21) crossover confirmed
  - RSI between 48-75
  - Volume spike on breakout
  - Price above EMA(50) on 1h

target: 68000
stop: 64500
risk_reward: 2.0

macro_context: "Risk-on regime, DXY weakening, funding rates neutral"
catalyst: "Institutional buying pressure, ETF inflows increasing"
```

### Step 2: Transition to ENTRY_READY

When conditions align but bot hasn't triggered yet:

```yaml
status: ENTRY_READY
entry_ready_at: 2026-03-18T14:00:00Z
notes: "EMA crossover forming, RSI at 52, volume picking up.
        Waiting for candle close confirmation."
```

### Step 3: Activate (Bot Enters Trade)

Link to bot's trade record:

```yaml
status: ACTIVE
activated_at: 2026-03-18T14:15:00Z
bot_trade_id: 42  # from trades.db
entry_price: 65500
position_size: 0.1026
leverage: 3
ai_decision: "execute"
ai_confidence: 0.72
ai_modifiers:
  position_size: 1.1
  sl_adjustment: 0.95
  tp_adjustment: 1.05
```

### Step 4: Monitor

Track key events during the trade:

```yaml
events:
  - time: 2026-03-18T16:00:00Z
    type: trailing_stop_update
    detail: "Trailing stop moved to $65,200"
  - time: 2026-03-18T18:00:00Z
    type: market_event
    detail: "FOMC minutes released, brief dip then recovery"
```

### Step 5: Close and Postmortem

```yaml
status: CLOSED
closed_at: 2026-03-19T02:30:00Z
exit_price: 67200
close_reason: trailing_stop
pnl: +$174.50
pnl_pct: +1.74%
duration_hours: 12.25

postmortem:
  thesis_correct: true
  entry_timing: good  # (good/early/late/bad)
  exit_timing: acceptable  # trailing stop captured 60% of move
  what_worked: "EMA crossover + trend filter alignment, AI confidence was high"
  what_didnt: "Could have held longer, TP was not reached"
  lesson: "Trailing stop at 1.8x ATR exits too early in strong trends.
           Consider 2.0x for trending regime."
  would_repeat: true
  config_suggestion: "Consider atr_trail_mult: 2.0 in trending regime"
```

## Integration with Bot

### Reading from trades.db

```python
# Query recent trades for thesis tracking
from dashboard.queries import get_recent_trades, get_open_trades
trades = get_recent_trades(limit=20)
```

### Linking Theses to Bot Trades

Each thesis references `bot_trade_id` from the trades table. This allows:
- Matching thesis hypothesis with actual bot execution
- Comparing expected vs actual entry/exit prices
- Evaluating AI advisor accuracy in thesis context
- Building pattern database of successful vs failed theses

### Postmortem Metrics

Track over time:
- Thesis accuracy rate (correct direction prediction)
- Entry timing score distribution
- Average PnL by thesis quality rating
- AI alignment rate (when AI agrees with thesis)
- Config suggestion hit rate (did suggested changes improve results?)

## File Storage

```
theses/
├── active/
│   └── BTC_20260318_001.yaml
├── closed/
│   ├── BTC_20260315_001.yaml
│   └── BTC_20260312_002.yaml
├── invalidated/
│   └── BTC_20260310_001.yaml
└── index.json  # Quick lookup index
```
