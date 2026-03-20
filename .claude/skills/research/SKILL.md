---
name: research
description: Iterative strategy optimization loop — agent team analyzes current performance, proposes improvements, implements changes, backtests, and repeats until target is met. Use when optimizing trading strategy parameters, testing new features, or hunting for higher returns.
user_invocable: true
---

# Strategy Research & Optimization Loop

Systematic iterative optimization methodology for the CCBT trading bot. Combines agent team expertise with automated backtesting to find optimal configurations.

## When to Use

- Optimizing strategy parameters (SL/TP/Trail, risk, leverage)
- Testing new signal types or features
- Hunting for higher returns or lower drawdown
- Validating changes after code modifications

## Methodology

### Phase 1: Baseline Measurement

Run the current config as baseline before any changes:

```python
from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv
import json

with open('config.json') as f:
    config = json.load(f)

signal_df = load_ohlcv('data/btcusdt_15m_2y.csv')
trend_df = load_ohlcv('data/btcusdt_1h_2y.csv')

engine = BacktestEngine(config, initial_balance=100.0)
result = engine.run(signal_df, trend_df)
```

Record: Trades, WR, PF, Sharpe, PnL, Max DD, per-signal-source breakdown.

### Phase 2: Agent Team Analysis

Spawn specialized agents in parallel:

1. **trader-expert**: Analyze current edge, identify weaknesses, propose parameter/strategy changes
2. **sa** (Solution Architect): Review code architecture, identify highest-impact code changes
3. **crypto-expert** (optional): Domain context if testing market-specific hypotheses

Key questions for agents:
- What's the bottleneck? (frequency? win rate? risk/reward? sizing?)
- What approaches have been tried and failed? (check conversation history and this skill doc)
- What's the highest-ROI change with lowest implementation risk?

### Phase 3: Hypothesis Testing (Parameter Sweep)

Test each change INDIVIDUALLY first, then in combination:

```python
import copy

tests = [
    ('Change A only', {param_a_overrides}),
    ('Change B only', {param_b_overrides}),
    ('A + B combined', {param_a_and_b}),
]

for name, overrides in tests:
    config = copy.deepcopy(base_config)
    config.update(overrides)
    engine = BacktestEngine(config, initial_balance=100.0)
    result = engine.run(signal_df, trend_df)
    # Compare vs baseline
```

Rules:
- **Isolate variables**: Test one change at a time to understand causality
- **No cherry-picking**: Use full 2-year dataset, not just favorable periods
- **Check per-source breakdown**: A combined PF can hide one good + one bad source
- **Watch trade count**: More trades with lower PF often = worse (fee drag)
- **DD matters**: High returns with high DD = fragile

### Phase 4: Implementation

If backtest shows improvement:
1. **backend-dev agent**: Implement code changes
2. Run `python3 -m pytest tests/ -v` — all tests must pass
3. Verify backtest with disabled feature = identical to baseline (no regression)
4. Verify backtest with enabled feature = improvement confirmed

### Phase 5: Iterate or Stop

**Stop criteria** (any of):
- Target return achieved
- Max rounds reached
- Diminishing returns (< 5% improvement per round)
- Overfitting risk (too many parameters tuned to same dataset)

**Continue criteria**:
- Target not met AND plausible hypotheses remain
- New approach fundamentally different from what was tried

### Phase 6: Deploy

Update config.json with best parameters, update CLAUDE.md and PRD.md.

## Key Lessons Learned

These findings from previous research rounds should guide future work:

### What Works on BTC 15m
- **Pyramiding into winners** is the single highest-impact feature (4x PnL improvement)
- **Compound pyramid adds from current balance** — NOT from original_size (5x more PnL vs fixed sizing)
- **MTD Accelerator** — scale up when winning month, scale down when losing (reduces DD 3%)
- **5 pyramid levels** better than 3 (adds at 2.8 and 3.5 ATR capture big runners)
- **Adaptive sizing** by signal quality — bet more on high-quality, skip low-quality
- **Higher leverage (10x)** is safe when combined with pyramiding SL ratcheting
- **EMA(9/21) + EMA(5/13) dual crossover** with RSI + volume confirmation
- **Regime-adaptive trailing** (wider in trending, tighter in ranging)
- **Trading hours filter** (skip 00-03 UTC dead hours)
- **TP extension HURTS** — extending TP on pyramided trades drops Sharpe from 2.01 to 0.30

### YOLO-Specific Findings
- **Adaptive sizing OFF at high leverage** — A-grade 2x risk mult causes outsized losses that compound negatively; flat sizing = 5x more profit + lower DD
- **Lower volume threshold (0.7x)** — more trades (370 vs 260) while PF stays high; each extra trade compounds at 100x leverage
- **Wider SL (1.65 ATR)** — fewer stop-outs = higher PF; with 100x lev, the risk budget absorbs wider SL
- **TP 4.0 + trail 5.0 post-TP1** — let runners run even further; massive compounding effect
- **Slope filter OFF** — adds ~30 trades without hurting PF at all
- **Aggressive MTD downscale (0.7x/0.4x)** — key DD reducer; prevents losing months from compounding down
- **Max consecutive losses 20** — prevents premature circuit breaker in YOLO mode

### What Does NOT Work on BTC 15m
- **Mean reversion** (BB+RSI in ranging) — negative PF, bad on BTC trending markets
- **MACD momentum** — noisy, PF 0.71
- **EMA pullback** — structurally unprofitable
- **RSI divergence** — too few signals (2 in 2 years)
- **More trades via looser filters** — every added signal dilutes edge (but see YOLO findings: context-dependent)
- **Weekend trading** — negative edge on BTC
- **Higher risk alone** — circuit breakers trigger faster, net negative
- **Blended partial-TP R:R** — mathematically blocks most signals; use full TP distance
- **Wider long RSI (45-72)** — busts DD at YOLO levels
- **Regime filter OFF** — 1510 trades but DD 66.5%, PF 1.32

### Parameter Sensitivities (post-bugfix, verified)
- SL multiplier: **1.0 ATR optimal** (tested 0.7-2.0 range; tighter SL = better R:R)
- TP multiplier: **3.0 ATR optimal** (tested 2.0-6.0; 3:1 R:R is sweet spot)
- Risk per trade: 2% safe, 5% aggressive, 10% YOLO-lite (diminishing returns above 10%)
- Cooldown: **0/0 optimal** — no cooldown gives more trades without hurting PF
- Volume threshold: 1.3x MA optimal (lower = more noise, higher = too few trades)
- RSI: Tight ranges (45-65/35-55) dramatically improve PF (1.59 vs 1.03)
- Trading hours: 3-20 UTC best (skip dead hours + late US session)
- Pyramiding: **DISABLED** — hurts PF with correct weighted-avg entry PnL calculation
- Trail-only exit: **fails badly** (PF 0.35-0.88) — fixed TP is essential

### Critical Bugfix Note (2026-03-19)
Previous backtest results (PF 3.32-4.80, $4,436-$9.35e+28) were **all invalid** due to:
1. Pyramid PnL used original entry instead of weighted avg (overestimated 30-60%)
2. Pyramid sizing used ratcheted SL distance (created unrealistically large adds)
3. Phantom pyramid adds on TP candles (inflated PnL further)
4. Sharpe annualized with sqrt(365) on per-trade data (1.3-2.5x overestimate)
All profiles below are verified with the corrected engine.

### Reverse Engineering Methodology (tested, had look-ahead bias)
Approach: "find profitable moves → what conditions preceded them?"
1. Found all >2% moves with <1% adverse in 12h (16.5% of candles)
2. Compared indicator distributions → `ret_std` strongest predictor (+0.22σ)
3. Built Body Dominance + Squeeze Release signals
4. **RESULT**: PF 2.06 — but was look-ahead bias (1H candle not closed yet)
5. After lag fix (shift 1H data by 1 period): PF dropped to 0.90 (no edge)
6. **LESSON**: Any 1H indicator used on 15m MUST be shifted by 1 period

### Strategies Tested & Failed
| Strategy | PF | Why it failed |
|----------|-----|---------------|
| Donchian Breakout | 0.70 | Too many false breakouts |
| Momentum Breakout | 0.42-0.62 | Noise on 15m |
| BB Squeeze Breakout | 0.63-0.73 | No edge on BTC |
| RSI Extreme + Trend | 0.87-0.93 | Near-breakeven |
| VWAP Deviation | 0.77-0.87 | Mean reversion fails on BTC |
| EMA Fan | 0.66-0.82 | Too late entry |
| ATR Expansion | varies | Marginal |
| Body Dominance (lagged) | 0.90 | No edge after bias fix |
| Squeeze Release (lagged) | 1.02 | Minimal edge |
| AI/ML Filter | same | RSI filter already does the job |
| Multi-asset (ETH/SOL) | 0.90-1.25 | BTC has best edge |

### What Actually Works
- EMA(9/21) + EMA(5/13) crossover = PF 1.42-1.62
- Tight RSI (45-65/35-55) = key quality filter
- SL 1.0 ATR / TP 3.0 ATR = optimal R:R
- No cooldown = more trades
- Hours 3-20 UTC, weekend off
- R10%/L25x = max return sweet spot
- Strategy is BTC-specific trend-follower (fails in sideways)

## Available Profiles (verified, 5yr $1K start, no bias)

| Profile | File | Risk | Lev | 5yr | /yr | Last 6mo | PF | DD | Trades |
|---------|------|------|-----|-----|-----|----------|-----|-----|--------|
| **Safe** | `config.json` | 2% | 7x | +72% | ~11% | +154% | 1.62 | 17% | 103 |
| **Aggressive** | `config_aggressive.json` | 5% | 10x | +158% | ~21% | +154% | 1.53 | 15% | 103 |
| **YOLO** | `config_yolo.json` | 10% | 25x | +421% | ~40% | +461% | 1.42 | 29% | 103 |
| **MAX** | `config_max.json` | 15% | 25x | +300% | ~33% | +441% | 1.29 | 36% | 103 |
| **Sniper** | `config_sniper.json` | 2% | 7x | +36% | ~6% | — | 1.58 | 8% | 58 |

## Gold (XAU/USD) Research Findings

10 strategies tested on 2.4yr 1H data ($1,999→$4,720):

### Winners
| Strategy | PF | Trades/yr | Return/yr | DD |
|----------|-----|----------|----------|-----|
| **Ichimoku + Trail + Hours(8-20)** | **2.02** | 44 | +46% | 9% |
| **Ichimoku Long-Only + Trail** | 2.01 | 49 | +56% | 12% |
| **Momentum Long-Only ROC(10,1.2%)** | 2.70 | 29 | +62% | 14% |
| Momentum both sides | 1.74 | 44 | +44% | 20% |
| EMA(12/26) + Volume + Trail | 1.52 | 40 | +18% | 24% |
| RSI(35/65) + EMA200 | 1.34 | 43 | +18% | 27% |

### Failures
Session Breakout (PF 0.94), VWAP Reversion (PF 0.75), BB Bounce (PF 0.77),
Pivot Points (PF 0.84), Asian Range (PF 0.93), EMA Ribbon (PF 1.05)

### Gold-Specific Rules
- Trailing stop >> fixed TP (gold trends run far)
- Long-only bias works (+136% in 2.4yr uptrend)
- SL 2.5 ATR (wider than BTC's 1.0 ATR)
- Hours 8-20 UTC = free edge (skip Asian noise)
- Ichimoku Cloud = natural trend filter for gold
- Mean reversion fails completely on gold

### Gold Data Files
- data/xauusd_1h_2y.csv (13,691 rows, Yahoo Finance)
- data/xauusd_1d_10y.csv (2,566 rows, 10yr daily)
- data/xauusd_15m_60d.csv (4,369 rows, 60 days)
- research/gold_strategies.py (10 strategy scripts)
- research/gold_strategies_deep.py (deep parameter sweeps)

## Invocation

User says `/research` with optional context:
- `/research` — full optimization loop from current baseline
- `/research "test lower SL multiplier"` — targeted hypothesis test
- `/research "compare profiles"` — backtest all 3 profiles side by side
- `/research "gold ichimoku"` — gold-specific strategy research
