"""Test EMA crossover on multiple coins."""
import json, sys
sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

with open("/Users/iceai/Work/ccbt/config.json") as f:
    base = json.load(f)

def run(signal_file, trend_file, name, overrides=None):
    c = json.loads(json.dumps(base))
    if overrides:
        for k,v in overrides.items():
            if isinstance(v,dict) and k in c: c[k].update(v)
            else: c[k]=v

    signal_df = load_ohlcv(signal_file)
    trend_df = load_ohlcv(trend_file)
    print(f"\n{name}: {len(signal_df)} candles, {signal_df.index[0]} to {signal_df.index[-1]}")

    e = BacktestEngine(c, initial_balance=1000.0)
    m = e.run(signal_df, trend_df)
    trades = e.state.trades

    # Per-source breakdown
    src = {}
    for t in trades:
        s = t.signal_source
        if s not in src: src[s]={'n':0,'w':0,'gp':0,'gl':0,'pnl':0}
        src[s]['n']+=1; src[s]['pnl']+=t.pnl
        if t.pnl>0: src[s]['w']+=1; src[s]['gp']+=t.pnl
        else: src[s]['gl']+=abs(t.pnl)

    days = (signal_df.index[-1] - signal_df.index[0]).days
    yr = days / 365.25
    tot = getattr(m,'total_trades',0)

    return {
        'name': name, 'trades': tot, 'tr_yr': tot/yr if yr>0 else 0,
        'wr': getattr(m,'win_rate',0)*100,
        'pf': getattr(m,'profit_factor',0),
        'ret': getattr(m,'total_return_pct',0),
        'dd': getattr(m,'max_drawdown_pct',0)*100,
        'sharpe': getattr(m,'sharpe_ratio',0),
        'sources': src, 'years': yr,
    }

# ===== TEST ALL COINS =====
coins = [
    # BTC baseline
    ("BTC 15m (baseline)", "data/btcusdt_15m_2y.csv", "data/btcusdt_1h_2y.csv", {}),
    ("BTC 15m 5yr", "data/btcusdt_15m_5y.csv", "data/btcusdt_1h_5y.csv", {}),

    # ETH with same config
    ("ETH 15m same config", "data/ethusdt_15m_5y.csv", "data/ethusdt_1h_5y.csv", {}),
    # ETH with adjusted ATR min (ETH has different price range)
    ("ETH 15m atr_min=0", "data/ethusdt_15m_5y.csv", "data/ethusdt_1h_5y.csv", {"atr_min": 0}),
    # ETH wider SL (ETH is more volatile)
    ("ETH 15m SL1.5/TP4.5", "data/ethusdt_15m_5y.csv", "data/ethusdt_1h_5y.csv",
     {"atr_sl_mult": 1.5, "atr_tp_mult": 4.5, "atr_min": 0}),
    # ETH with relaxed volume
    ("ETH 15m vol1.0", "data/ethusdt_15m_5y.csv", "data/ethusdt_1h_5y.csv",
     {"volume_mult": 1.0, "atr_min": 0}),
    # ETH optimized combo
    ("ETH 15m optimized", "data/ethusdt_15m_5y.csv", "data/ethusdt_1h_5y.csv",
     {"volume_mult": 1.0, "atr_min": 0, "atr_sl_mult": 1.5, "atr_tp_mult": 4.5}),

    # SOL with same config
    ("SOL 15m same config", "data/solusdt_15m_5y.csv", "data/solusdt_1h_5y.csv", {}),
    ("SOL 15m atr_min=0", "data/solusdt_15m_5y.csv", "data/solusdt_1h_5y.csv", {"atr_min": 0}),
    ("SOL 15m vol1.0", "data/solusdt_15m_5y.csv", "data/solusdt_1h_5y.csv",
     {"volume_mult": 1.0, "atr_min": 0}),
    ("SOL 15m SL1.5/TP4.5", "data/solusdt_15m_5y.csv", "data/solusdt_1h_5y.csv",
     {"atr_sl_mult": 1.5, "atr_tp_mult": 4.5, "atr_min": 0}),
    ("SOL 15m optimized", "data/solusdt_15m_5y.csv", "data/solusdt_1h_5y.csv",
     {"volume_mult": 1.0, "atr_min": 0, "atr_sl_mult": 1.5, "atr_tp_mult": 4.5}),

    # PAXG (gold token)
    ("PAXG 15m same config", "data/paxgusdt_15m_2y.csv", "data/paxgusdt_1h_2y.csv", {}),
    ("PAXG 15m atr_min=0", "data/paxgusdt_15m_2y.csv", "data/paxgusdt_1h_2y.csv", {"atr_min": 0}),
    ("PAXG 15m vol0.7", "data/paxgusdt_15m_2y.csv", "data/paxgusdt_1h_2y.csv",
     {"volume_mult": 0.7, "atr_min": 0}),
    ("PAXG 15m SL2.5/TP5.0", "data/paxgusdt_15m_2y.csv", "data/paxgusdt_1h_2y.csv",
     {"atr_sl_mult": 2.5, "atr_tp_mult": 5.0, "atr_min": 0, "volume_mult": 0.7}),
]

print("="*110)
print("MULTI-COIN EMA CROSSOVER TEST — 15m")
print("="*110)

results = []
for name, sig, trend, ov in coins:
    try:
        r = run(sig, trend, name, ov)
        results.append(r)
    except Exception as e:
        print(f"ERROR {name}: {e}")
        import traceback; traceback.print_exc()

print("\n" + "="*110)
print("COMPARISON TABLE")
print("="*110)
print(f"{'Coin':<30} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'Ret%':>9} {'DD%':>6} {'Sharpe':>7} {'Years':>5}")
print("-"*90)
for r in results:
    print(f"{r['name']:<30} {r['trades']:>6} {r['tr_yr']:>5.0f} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['ret']:>8.1f}% {r['dd']:>5.1f}% {r['sharpe']:>7.2f} {r['years']:>5.1f}")

# Per-source for best configs
for r in results:
    if r['pf'] > 1.2 and r['trades'] > 20:
        print(f"\n  {r['name']} per-source:")
        for s, d in sorted(r['sources'].items()):
            spf = d['gp']/d['gl'] if d['gl']>0 else 0
            swr = d['w']/d['n']*100 if d['n']>0 else 0
            print(f"    {s}: {d['n']}t, WR {swr:.0f}%, PF {spf:.2f}, PnL {d['pnl']:+.0f}")
