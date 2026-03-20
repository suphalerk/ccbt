"""PA parameter sweeps and day trading exploration."""
import json
import sys
sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

signal_df = load_ohlcv("data/btcusdt_15m_2y.csv")
trend_df = load_ohlcv("data/btcusdt_1h_2y.csv")

with open("config.json") as f:
    base_config = json.load(f)

def run_test(config_overrides):
    config = json.loads(json.dumps(base_config))
    for key, value in config_overrides.items():
        if isinstance(value, dict) and key in config:
            config[key].update(value)
        else:
            config[key] = value
    engine = BacktestEngine(config, initial_balance=1000.0)
    metrics = engine.run(signal_df, trend_df)
    trades = engine.state.trades
    return metrics, trades

# ============= PART 1: Pin Bar Parameter Sweep =============
print("="*90)
print("PART 1: PIN BAR PARAMETER SWEEP")
print("="*90)

pb_base = {
    "signals": {
        "ema_crossover": {"enabled": True},
        "ema_fast_crossover": {"enabled": True},
        "ema_pullback": {"enabled": True},
        "pin_bar": {"enabled": True},
        "engulfing": {"enabled": False},
        "inside_bar_breakout": {"enabled": False},
    },
}

pb_results = []
for wick_ratio in [1.5, 2.0, 2.5, 3.0]:
    for ema_prox in [0.002, 0.003, 0.005, 0.01]:
        overrides = {**pb_base, "pin_bar_wick_ratio": wick_ratio, "pin_bar_ema_proximity_pct": ema_prox}
        try:
            m, trades = run_test(overrides)
            # Count PA trades only
            pa_trades = [t for t in trades if t.signal_source == "pin_bar"]
            pa_pnl = sum(t.pnl for t in pa_trades)
            pa_wins = sum(1 for t in pa_trades if t.pnl > 0)
            pa_gross_p = sum(t.pnl for t in pa_trades if t.pnl > 0)
            pa_gross_l = sum(abs(t.pnl) for t in pa_trades if t.pnl < 0)
            pa_pf = pa_gross_p / pa_gross_l if pa_gross_l > 0 else float('inf')
            pb_results.append({
                "wick": wick_ratio, "prox": ema_prox,
                "total_trades": getattr(m, 'total_trades', 0),
                "pf": getattr(m, 'profit_factor', 0),
                "ret": getattr(m, 'total_return_pct', 0),
                "dd": getattr(m, 'max_drawdown_pct', 0) * 100,
                "pa_trades": len(pa_trades),
                "pa_pf": pa_pf,
                "pa_pnl": pa_pnl,
            })
        except Exception as e:
            print(f"ERROR wick={wick_ratio} prox={ema_prox}: {e}")

print(f"\n{'Wick':>5} {'Prox':>6} {'Total':>6} {'PF':>6} {'Ret%':>8} {'DD%':>6} {'PA#':>4} {'PA_PF':>6} {'PA_PnL':>8}")
print("-"*60)
for r in pb_results:
    print(f"{r['wick']:>5.1f} {r['prox']:>6.3f} {r['total_trades']:>6} {r['pf']:>6.2f} {r['ret']:>7.1f}% {r['dd']:>5.1f}% {r['pa_trades']:>4} {r['pa_pf']:>6.2f} {r['pa_pnl']:>+8.1f}")

# ============= PART 2: Engulfing Parameter Sweep =============
print("\n" + "="*90)
print("PART 2: ENGULFING PARAMETER SWEEP")
print("="*90)

eng_base = {
    "signals": {
        "ema_crossover": {"enabled": True},
        "ema_fast_crossover": {"enabled": True},
        "ema_pullback": {"enabled": True},
        "pin_bar": {"enabled": False},
        "engulfing": {"enabled": True},
        "inside_bar_breakout": {"enabled": False},
    },
}

eng_results = []
for body_ratio in [1.0, 1.2, 1.3, 1.5, 2.0]:
    for vol_mult in [0.5, 0.7, 1.0, 1.3]:
        overrides = {**eng_base, "engulfing_body_ratio": body_ratio, "engulfing_volume_mult": vol_mult}
        try:
            m, trades = run_test(overrides)
            pa_trades = [t for t in trades if t.signal_source == "engulfing"]
            pa_pnl = sum(t.pnl for t in pa_trades)
            pa_gross_p = sum(t.pnl for t in pa_trades if t.pnl > 0)
            pa_gross_l = sum(abs(t.pnl) for t in pa_trades if t.pnl < 0)
            pa_pf = pa_gross_p / pa_gross_l if pa_gross_l > 0 else float('inf')
            eng_results.append({
                "ratio": body_ratio, "vol": vol_mult,
                "total_trades": getattr(m, 'total_trades', 0),
                "pf": getattr(m, 'profit_factor', 0),
                "ret": getattr(m, 'total_return_pct', 0),
                "dd": getattr(m, 'max_drawdown_pct', 0) * 100,
                "pa_trades": len(pa_trades),
                "pa_pf": pa_pf,
                "pa_pnl": pa_pnl,
            })
        except Exception as e:
            print(f"ERROR ratio={body_ratio} vol={vol_mult}: {e}")

print(f"\n{'Ratio':>6} {'Vol':>5} {'Total':>6} {'PF':>6} {'Ret%':>8} {'DD%':>6} {'PA#':>4} {'PA_PF':>6} {'PA_PnL':>8}")
print("-"*60)
for r in eng_results:
    print(f"{r['ratio']:>6.1f} {r['vol']:>5.1f} {r['total_trades']:>6} {r['pf']:>6.2f} {r['ret']:>7.1f}% {r['dd']:>5.1f}% {r['pa_trades']:>4} {r['pa_pf']:>6.2f} {r['pa_pnl']:>+8.1f}")

# ============= PART 3: Inside Bar Breakout Sweep =============
print("\n" + "="*90)
print("PART 3: INSIDE BAR BREAKOUT PARAMETER SWEEP")
print("="*90)

ib_base = {
    "signals": {
        "ema_crossover": {"enabled": True},
        "ema_fast_crossover": {"enabled": True},
        "ema_pullback": {"enabled": True},
        "pin_bar": {"enabled": False},
        "engulfing": {"enabled": False},
        "inside_bar_breakout": {"enabled": True},
    },
}

ib_results = []
for mother_atr in [0.3, 0.5, 0.7, 1.0]:
    for vol_mult in [0.5, 0.8, 1.0, 1.2]:
        overrides = {**ib_base, "inside_bar_min_mother_range_atr_mult": mother_atr, "inside_bar_breakout_volume_mult": vol_mult}
        try:
            m, trades = run_test(overrides)
            pa_trades = [t for t in trades if t.signal_source == "inside_bar_breakout"]
            pa_pnl = sum(t.pnl for t in pa_trades)
            pa_gross_p = sum(t.pnl for t in pa_trades if t.pnl > 0)
            pa_gross_l = sum(abs(t.pnl) for t in pa_trades if t.pnl < 0)
            pa_pf = pa_gross_p / pa_gross_l if pa_gross_l > 0 else float('inf')
            ib_results.append({
                "mother": mother_atr, "vol": vol_mult,
                "total_trades": getattr(m, 'total_trades', 0),
                "pf": getattr(m, 'profit_factor', 0),
                "ret": getattr(m, 'total_return_pct', 0),
                "dd": getattr(m, 'max_drawdown_pct', 0) * 100,
                "pa_trades": len(pa_trades),
                "pa_pf": pa_pf,
                "pa_pnl": pa_pnl,
            })
        except Exception as e:
            print(f"ERROR mother={mother_atr} vol={vol_mult}: {e}")

print(f"\n{'Mother':>7} {'Vol':>5} {'Total':>6} {'PF':>6} {'Ret%':>8} {'DD%':>6} {'PA#':>4} {'PA_PF':>6} {'PA_PnL':>8}")
print("-"*60)
for r in ib_results:
    print(f"{r['mother']:>7.1f} {r['vol']:>5.1f} {r['total_trades']:>6} {r['pf']:>6.2f} {r['ret']:>7.1f}% {r['dd']:>5.1f}% {r['pa_trades']:>4} {r['pa_pf']:>6.2f} {r['pa_pnl']:>+8.1f}")

# ============= PART 4: Best Combined Configuration =============
print("\n" + "="*90)
print("PART 4: BEST COMBINED CONFIGURATION")
print("="*90)

# Based on sweep results, test the best parameters for each PA pattern combined
# (Will use defaults first since we don't know optimal yet — adjust after seeing Part 1-3)
best_combined = {
    "signals": {
        "ema_crossover": {"enabled": True},
        "ema_fast_crossover": {"enabled": True},
        "ema_pullback": {"enabled": True},
        "pin_bar": {"enabled": True},
        "engulfing": {"enabled": True},
        "inside_bar_breakout": {"enabled": True},
    },
    # Will be tuned based on Part 1-3 results
    "pin_bar_wick_ratio": 2.0,
    "pin_bar_ema_proximity_pct": 0.005,
    "pin_bar_volume_mult": 0.7,
    "engulfing_body_ratio": 1.3,
    "engulfing_volume_mult": 1.0,
    "inside_bar_min_mother_range_atr_mult": 0.7,
    "inside_bar_breakout_volume_mult": 1.0,
}

print("\n--- Best Combined (EMA + All PA) ---")
m, trades = run_test(best_combined)

# Per-source breakdown
sources = {}
for t in trades:
    src = t.signal_source
    if src not in sources:
        sources[src] = {"count": 0, "wins": 0, "pnl": 0, "gross_profit": 0, "gross_loss": 0}
    sources[src]["count"] += 1
    sources[src]["pnl"] += t.pnl
    if t.pnl > 0:
        sources[src]["wins"] += 1
        sources[src]["gross_profit"] += t.pnl
    else:
        sources[src]["gross_loss"] += abs(t.pnl)

print(f"\n{'Source':<25} {'Trades':>7} {'WR%':>6} {'PF':>6} {'PnL':>10}")
print("-"*60)
for src, data in sorted(sources.items()):
    wr = (data["wins"] / data["count"] * 100) if data["count"] > 0 else 0
    pf = (data["gross_profit"] / data["gross_loss"]) if data["gross_loss"] > 0 else float("inf")
    print(f"{src:<25} {data['count']:>7} {wr:>5.1f}% {pf:>6.2f} {data['pnl']:>+10.2f}")

total = getattr(m, 'total_trades', 0)
pf = getattr(m, 'profit_factor', 0)
ret = getattr(m, 'total_return_pct', 0)
dd = getattr(m, 'max_drawdown_pct', 0) * 100
sharpe = getattr(m, 'sharpe_ratio', 0)
print(f"\nTOTAL: {total} trades | PF {pf:.2f} | Return {ret:.1f}% | DD {dd:.1f}% | Sharpe {sharpe:.2f}")

# ============= PART 5: Check 5m data availability =============
print("\n" + "="*90)
print("PART 5: 5m DATA CHECK")
print("="*90)
import os
import glob as glob_mod
data_files = glob_mod.glob("/Users/iceai/Work/ccbt/data/btcusdt_5m*")
print(f"5m data files found: {data_files}")
if not data_files:
    print("No 5m data available — day trading test skipped")
    print("Would need to download: btcusdt_5m_2y.csv (~210k candles)")
else:
    print("5m data found! Running day trading test...")
    # Would run test here
