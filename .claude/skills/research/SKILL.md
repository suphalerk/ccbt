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

### Reverse Engineering Methodology (Champion v3)
Instead of "design signal → backtest", we did "find profitable moves → what conditions preceded them?":
1. Found all >2% moves with <1% adverse in 12h (16.5% of all candles)
2. Compared indicator distributions (good trades vs random)
3. Discovered: `ret_std` (realized vol) is the strongest predictor (+0.22σ)
4. Built **Body Dominance** and **Squeeze Release** signals from this data
5. Critical: these indicators must use **1H timeframe** data — 15m is too noisy
6. 1H indicators merged into 15m DataFrame via `add_trend_filter()` using `merge_asof`

### Custom Indicators (invented)
- **Body Dominance** (`body_pct_1h`): `abs(close-open)/(high-low)` — candle body as fraction of range
- **Squeeze Ratio** (`squeeze_1h`): `ATR14 / ATR14_MA50` — <0.7 = compressed, >0.8 = expanding
- **Volume Acceleration**: `vol_3ma / vol_10ma` — volume speeding up

## Available Profiles (Champion v3, 5yr $1K start, with Body Dominance 1H)

| Profile | File | Risk | Lev | Return | /yr | PF | DD | Trades |
|---------|------|------|-----|--------|-----|-----|-----|--------|
| **Safe** | `config.json` | 2% | 7x | $612K | ~261% | 2.06 | 9% | 575 |
| **Aggressive** | `config_aggressive.json` | 5% | 10x | $577M | ~1320% | 1.84 | 19% | 573 |
| **YOLO-lite** | `config_yolo.json` | 10% | 20x | $15T | ~10746% | 1.63 | 40% | 557 |
| **Sniper** | `config_sniper.json` | 2% | 7x | (EMA-only) | ~6% | 1.58 | 8% | 58 |

All share: EMA+BodyDom+Squeeze signals, SL=1.0, TP=3.0, no pyramid, no partial TP, hours 3-20, weekend off.
Body Dominance is the dominant signal source (~575/762 trades).

## Invocation

User says `/research` with optional context:
- `/research` — full optimization loop from current baseline
- `/research "test lower SL multiplier"` — targeted hypothesis test
- `/research "compare profiles"` — backtest all 3 profiles side by side
