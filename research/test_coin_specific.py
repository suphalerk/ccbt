"""Test coin-specific optimized strategies."""
import json, sys, os
import numpy as np
import pandas as pd
sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

with open("/Users/iceai/Work/ccbt/config.json") as f:
    base = json.load(f)

def run_backtest(sig_file, trend_file, overrides):
    c = json.loads(json.dumps(base))
    for k,v in overrides.items():
        if isinstance(v,dict) and k in c: c[k].update(v)
        else: c[k]=v
    sig = load_ohlcv(sig_file)
    trend = load_ohlcv(trend_file)
    e = BacktestEngine(c, initial_balance=1000.0)
    m = e.run(sig, trend)
    trades = e.state.trades
    days = (sig.index[-1] - sig.index[0]).days
    yr = days/365.25
    tot = getattr(m,'total_trades',0)

    src = {}
    for t in trades:
        s = t.signal_source
        if s not in src: src[s]={'n':0,'w':0,'gp':0,'gl':0}
        src[s]['n']+=1
        if t.pnl>0: src[s]['w']+=1; src[s]['gp']+=t.pnl
        else: src[s]['gl']+=abs(t.pnl)

    return {
        'trades': tot, 'tr_yr': tot/yr if yr>0 else 0,
        'wr': getattr(m,'win_rate',0)*100,
        'pf': getattr(m,'profit_factor',0),
        'dd': getattr(m,'max_drawdown_pct',0)*100,
        'sharpe': getattr(m,'sharpe_ratio',0),
        'sources': src
    }

# ===== DOGE OPTIMIZATION =====
print("="*80)
print("DOGE/USDT OPTIMIZATION")
print("="*80)

doge_tests = {
    "DOGE baseline": {"atr_min": 0},
    "DOGE vol0.7": {"atr_min": 0, "volume_mult": 0.7},
    "DOGE vol1.0": {"atr_min": 0, "volume_mult": 1.0},
    "DOGE SL1.5/TP4.5": {"atr_min": 0, "atr_sl_mult": 1.5, "atr_tp_mult": 4.5},
    "DOGE SL0.7/TP2.0": {"atr_min": 0, "atr_sl_mult": 0.7, "atr_tp_mult": 2.0},
    "DOGE fast EMA(5/13)": {"atr_min": 0, "ema_fast": 5, "ema_slow": 13},
    "DOGE fast EMA(7/17)": {"atr_min": 0, "ema_fast": 7, "ema_slow": 17},
    "DOGE slope0.01": {"atr_min": 0, "ema_slope_min": 0.01},
    "DOGE slope0": {"atr_min": 0, "ema_slope_min": 0},
    "DOGE no_weekend+vol0.7": {"atr_min": 0, "volume_mult": 0.7, "weekend_trading_enabled": False},
    "DOGE +PA pin_bar": {"atr_min": 0, "signals": {"pin_bar": {"enabled": True}}, "pin_bar_wick_ratio": 3.0, "pin_bar_ema_proximity_pct": 0.01, "pin_bar_volume_mult": 0.5},
    "DOGE +PA all": {"atr_min": 0, "signals": {"pin_bar": {"enabled": True}, "engulfing": {"enabled": True}, "inside_bar_breakout": {"enabled": True}}, "pin_bar_wick_ratio": 2.0, "pin_bar_ema_proximity_pct": 0.01, "pin_bar_volume_mult": 0.5, "engulfing_body_ratio": 1.2, "engulfing_volume_mult": 0.7, "inside_bar_breakout_volume_mult": 0.8},
    "DOGE RSI wider 40-70/30-60": {"atr_min": 0, "rsi_long_min": 40, "rsi_long_max": 70, "rsi_short_min": 30, "rsi_short_max": 60},
}

print(f"\n{'Config':<30} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Sharpe':>7}")
print("-"*70)
for name, ov in doge_tests.items():
    try:
        r = run_backtest("/Users/iceai/Work/ccbt/data/dogeusdt_15m_2y.csv", "/Users/iceai/Work/ccbt/data/dogeusdt_1h_2y.csv", ov)
        print(f"{name:<30} {r['trades']:>6} {r['tr_yr']:>5.0f} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}% {r['sharpe']:>7.2f}")
        if 'pin_bar' in name or 'PA' in name:
            for s, d in sorted(r['sources'].items()):
                spf = d['gp']/d['gl'] if d['gl']>0 else 0
                print(f"  └─{s}: {d['n']}t PF {spf:.2f}")
    except Exception as e:
        print(f"{name:<30} ERROR: {e}")

# ===== ARB OPTIMIZATION =====
print("\n" + "="*80)
print("ARB/USDT OPTIMIZATION")
print("="*80)

arb_tests = {
    "ARB baseline": {"atr_min": 0},
    "ARB vol0.7": {"atr_min": 0, "volume_mult": 0.7},
    "ARB vol1.0": {"atr_min": 0, "volume_mult": 1.0},
    "ARB SL1.5/TP4.5": {"atr_min": 0, "atr_sl_mult": 1.5, "atr_tp_mult": 4.5},
    "ARB slope0": {"atr_min": 0, "ema_slope_min": 0},
    "ARB +PA pin_bar": {"atr_min": 0, "signals": {"pin_bar": {"enabled": True}}, "pin_bar_wick_ratio": 2.5, "pin_bar_ema_proximity_pct": 0.01, "pin_bar_volume_mult": 0.5},
    "ARB RSI wider": {"atr_min": 0, "rsi_long_min": 40, "rsi_long_max": 70, "rsi_short_min": 30, "rsi_short_max": 60},
}

print(f"\n{'Config':<30} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Sharpe':>7}")
print("-"*70)
for name, ov in arb_tests.items():
    try:
        r = run_backtest("/Users/iceai/Work/ccbt/data/arbusdt_15m_2y.csv", "/Users/iceai/Work/ccbt/data/arbusdt_1h_2y.csv", ov)
        print(f"{name:<30} {r['trades']:>6} {r['tr_yr']:>5.0f} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}% {r['sharpe']:>7.2f}")
    except Exception as e:
        print(f"{name:<30} ERROR: {e}")

# ===== WIF OPTIMIZATION =====
print("\n" + "="*80)
print("WIF/USDT OPTIMIZATION")
print("="*80)

wif_tests = {
    "WIF baseline": {"atr_min": 0},
    "WIF vol0.7": {"atr_min": 0, "volume_mult": 0.7},
    "WIF vol1.0": {"atr_min": 0, "volume_mult": 1.0},
    "WIF SL1.5/TP4.5": {"atr_min": 0, "atr_sl_mult": 1.5, "atr_tp_mult": 4.5},
    "WIF slope0": {"atr_min": 0, "ema_slope_min": 0},
    "WIF +PA pin_bar": {"atr_min": 0, "signals": {"pin_bar": {"enabled": True}}, "pin_bar_wick_ratio": 2.5, "pin_bar_ema_proximity_pct": 0.01, "pin_bar_volume_mult": 0.5},
    "WIF RSI wider": {"atr_min": 0, "rsi_long_min": 40, "rsi_long_max": 70, "rsi_short_min": 30, "rsi_short_max": 60},
}

print(f"\n{'Config':<30} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Sharpe':>7}")
print("-"*70)
for name, ov in wif_tests.items():
    try:
        r = run_backtest("/Users/iceai/Work/ccbt/data/wifusdt_15m_2y.csv", "/Users/iceai/Work/ccbt/data/wifusdt_1h_2y.csv", ov)
        print(f"{name:<30} {r['trades']:>6} {r['tr_yr']:>5.0f} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}% {r['sharpe']:>7.2f}")
    except Exception as e:
        print(f"{name:<30} ERROR: {e}")

# ===== COMBINED PORTFOLIO SIMULATION =====
print("\n" + "="*80)
print("MULTI-COIN PORTFOLIO (BTC + DOGE + ARB + WIF)")
print("="*80)

portfolio_coins = [
    ("BTC", "btcusdt_15m_2y.csv", "btcusdt_1h_2y.csv", {}),
    ("DOGE", "dogeusdt_15m_2y.csv", "dogeusdt_1h_2y.csv", {"atr_min": 0}),
    ("ARB", "arbusdt_15m_2y.csv", "arbusdt_1h_2y.csv", {"atr_min": 0}),
    ("WIF", "wifusdt_15m_2y.csv", "wifusdt_1h_2y.csv", {"atr_min": 0}),
]

total_trades = 0
total_pnl = 0
for coin, sig_f, trend_f, ov in portfolio_coins:
    try:
        r = run_backtest(f"/Users/iceai/Work/ccbt/data/{sig_f}", f"/Users/iceai/Work/ccbt/data/{trend_f}", ov)
        total_trades += r['trades']
        print(f"  {coin}: {r['trades']}t, PF {r['pf']:.2f}, Tr/yr {r['tr_yr']:.0f}")
    except Exception as e:
        print(f"  {coin}: ERROR: {e}")

print(f"\n  PORTFOLIO TOTAL: {total_trades} trades across all coins")
print(f"  Estimated trades/year: {total_trades / 2:.0f}")
print(f"  Estimated trades/day: {total_trades / 2 / 365:.1f}")
