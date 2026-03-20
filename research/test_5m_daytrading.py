"""Test EMA crossover on 5m for day trading."""
import json, sys
sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

# Load 5m data
signal_df = load_ohlcv("data/btcusdt_5m_2y.csv")
trend_df = load_ohlcv("data/btcusdt_1h_2y.csv")

print(f"5m data: {len(signal_df)} candles, from {signal_df.index[0]} to {signal_df.index[-1]}")

with open("config.json") as f:
    base = json.load(f)

def run(name, overrides):
    c = json.loads(json.dumps(base))
    for k,v in overrides.items():
        if isinstance(v,dict) and k in c: c[k].update(v)
        else: c[k]=v
    e = BacktestEngine(c, initial_balance=1000.0)
    m = e.run(signal_df, trend_df)
    trades = e.state.trades
    src = {}
    for t in trades:
        s = t.signal_source
        if s not in src: src[s]={'n':0,'w':0,'gp':0,'gl':0,'pnl':0}
        src[s]['n']+=1; src[s]['pnl']+=t.pnl
        if t.pnl>0: src[s]['w']+=1; src[s]['gp']+=t.pnl
        else: src[s]['gl']+=abs(t.pnl)
    return m, src, trades

tests = {
    # Test 1: Exact same config as 15m (baseline comparison)
    "5m Exact Same Config": {},

    # Test 2: Adjusted for 5m noise — tighter RSI
    "5m Tight RSI": {
        "rsi_long_min": 48, "rsi_long_max": 62,
        "rsi_short_min": 38, "rsi_short_max": 52,
    },

    # Test 3: Higher volume filter for 5m (more noise)
    "5m High Volume": {"volume_mult": 1.5},

    # Test 4: Wider SL for 5m noise
    "5m Wider SL": {"atr_sl_mult": 1.5, "atr_tp_mult": 4.5},

    # Test 5: Faster EMAs for 5m (catch trends quicker)
    "5m Fast EMA(5/13)": {"ema_fast": 5, "ema_slow": 13},

    # Test 6: Very fast EMAs
    "5m Turbo EMA(3/8)": {"ema_fast": 3, "ema_slow": 8},

    # Test 7: Longer EMAs (filter noise)
    "5m Slow EMA(15/35)": {"ema_fast": 15, "ema_slow": 35},

    # Test 8: US Session Only (13-21 UTC)
    "5m US Session Only": {
        "trading_hours": {"enabled": True, "start_utc": 13, "end_utc": 21},
    },

    # Test 9: Kill Zones only (London+NY open)
    "5m Kill Zones": {
        "trading_hours": {"enabled": True, "start_utc": 7, "end_utc": 16},
    },

    # Test 10: Low leverage for 5m (reduce fee impact)
    "5m Low Lev 7x": {"leverage": 7, "risk_per_trade": 0.02},

    # Test 11: Scalping - tight SL/TP
    "5m Scalp SL0.5 TP1.5": {"atr_sl_mult": 0.5, "atr_tp_mult": 1.5, "min_rr_ratio": 1.5},

    # Test 12: Let runners run - wider TP
    "5m Wide TP6.0": {"atr_tp_mult": 6.0},

    # Test 13: Combined best ideas
    "5m Optimized": {
        "ema_fast": 5, "ema_slow": 13,
        "rsi_long_min": 48, "rsi_long_max": 62,
        "rsi_short_min": 38, "rsi_short_max": 52,
        "volume_mult": 1.5,
        "atr_sl_mult": 1.5, "atr_tp_mult": 4.5,
        "trading_hours": {"enabled": True, "start_utc": 7, "end_utc": 21},
    },

    # Test 14: Triple Screen (1h trend + 15m mid + 5m entry)
    # Use 1h trend filter (already in engine) + standard EMAs
    "5m Triple Screen": {
        "ema_fast": 9, "ema_slow": 21,
        "ema_slope_min": 0.03,  # Stronger trend requirement
        "volume_mult": 1.5,
        "trading_hours": {"enabled": True, "start_utc": 7, "end_utc": 21},
    },
}

print("\n" + "="*100)
print("5m DAY TRADING TESTS")
print("="*100)
print(f"{'Config':<30} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Ret%':>9} {'DD%':>6} {'Sharpe':>7} {'Tr/yr':>6}")
print("-"*80)

for name, ov in tests.items():
    try:
        m, src, trades = run(name, ov)
        tot = getattr(m,'total_trades',0)
        wr = getattr(m,'win_rate',0)*100
        pf = getattr(m,'profit_factor',0)
        ret = getattr(m,'total_return_pct',0)
        dd = getattr(m,'max_drawdown_pct',0)*100
        sh = getattr(m,'sharpe_ratio',0)
        # Estimate trades per year
        days = (signal_df.index[-1] - signal_df.index[0]).days
        yr = days / 365.25
        tr_yr = tot / yr if yr > 0 else 0
        print(f"{name:<30} {tot:>6} {wr:>5.1f}% {pf:>6.2f} {ret:>8.1f}% {dd:>5.1f}% {sh:>7.2f} {tr_yr:>5.0f}")

        # Per-source for interesting configs
        if name in ["5m Exact Same Config", "5m Optimized", "5m Triple Screen"]:
            for s, d in sorted(src.items()):
                spf = d['gp']/d['gl'] if d['gl']>0 else 0
                swr = d['w']/d['n']*100 if d['n']>0 else 0
                print(f"  └─ {s}: {d['n']}t, WR {swr:.0f}%, PF {spf:.2f}, PnL {d['pnl']:+.0f}")
    except Exception as e:
        print(f"{name:<30} ERROR: {e}")
        import traceback; traceback.print_exc()
