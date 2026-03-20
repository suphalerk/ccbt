"""Mega sweep: PA + Ichimoku + EMA across all coins and timeframes."""
import json, sys, os
import numpy as np
import pandas as pd
sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_ema, compute_rsi, compute_atr, compute_volume_ma

DATA_DIR = "data"

def add_indicators(df, ema_f=9, ema_s=21):
    df = df.copy()
    df["ema_f"] = compute_ema(df["close"], ema_f)
    df["ema_s"] = compute_ema(df["close"], ema_s)
    df["rsi"] = compute_rsi(df["close"], 14)
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], 14)
    df["vol_ma"] = compute_volume_ma(df["volume"], 20)
    df["vol_ratio"] = df["volume"] / df["vol_ma"].clip(lower=1)

    # EMA crossover
    df["cross_up"] = (df["ema_f"] > df["ema_s"]) & (df["ema_f"].shift(1) <= df["ema_s"].shift(1))
    df["cross_down"] = (df["ema_f"] < df["ema_s"]) & (df["ema_f"].shift(1) >= df["ema_s"].shift(1))

    # Price Action patterns
    body = (df["close"] - df["open"]).abs()
    candle_range = (df["high"] - df["low"]).clip(lower=1e-10)
    lower_wick = df[["close", "open"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["close", "open"]].max(axis=1)

    # Pin bar (strict wick >= 2.5x body for quality)
    df["pin_bull"] = (lower_wick >= body * 2.5) & ((df["close"] - df["low"]) / candle_range >= 0.70) & (body > 0) & (candle_range >= df["atr"] * 0.5)
    df["pin_bear"] = (upper_wick >= body * 2.5) & ((df["high"] - df["close"]) / candle_range >= 0.70) & (body > 0) & (candle_range >= df["atr"] * 0.5)

    # Engulfing
    prev_body = body.shift(1)
    prev_close = df["close"].shift(1)
    prev_open = df["open"].shift(1)
    df["engulf_bull"] = (df["close"] > df["open"]) & (prev_close < prev_open) & (body >= prev_body * 1.3) & (df["close"] > prev_open) & (body / candle_range >= 0.4)
    df["engulf_bear"] = (df["close"] < df["open"]) & (prev_close > prev_open) & (body >= prev_body * 1.3) & (df["close"] < prev_open) & (body / candle_range >= 0.4)

    # Ichimoku
    df["tenkan"] = (df["high"].rolling(9).max() + df["low"].rolling(9).min()) / 2
    df["kijun"] = (df["high"].rolling(26).max() + df["low"].rolling(26).min()) / 2
    span_a = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    span_b = ((df["high"].rolling(52).max() + df["low"].rolling(52).min()) / 2).shift(26)
    df["cloud_top"] = pd.concat([span_a, span_b], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a, span_b], axis=1).min(axis=1)
    df["ichi_bull"] = (df["tenkan"] > df["kijun"]) & (df["tenkan"].shift(1) <= df["kijun"].shift(1)) & (df["close"] > df["cloud_top"])
    df["ichi_bear"] = (df["tenkan"] < df["kijun"]) & (df["tenkan"].shift(1) >= df["kijun"].shift(1)) & (df["close"] < df["cloud_bottom"])

    # Hour + weekend
    if hasattr(df.index, 'hour'):
        df["hour"] = df.index.hour
        df["dow"] = df.index.dayofweek

    return df

def simulate(df, sl_mult=1.0, tp_mult=3.0, risk_pct=0.10, leverage=25,
             commission=0.00055, slippage=0.0002, trail_mult=0):
    """Simple simulator. df must have 'signal' column (1=long, -1=short, 0=none)."""
    balance = 1000.0; peak = 1000.0; max_dd = 0
    trades = []; position = None

    for i in range(2, len(df)):
        row = df.iloc[i]; sig_row = df.iloc[i-1]

        if position is not None:
            side, entry, sl, tp = position["side"], position["entry"], position["sl"], position["tp"]

            # Trailing stop
            if trail_mult > 0 and position.get("trail"):
                trail_dist = sig_row["atr"] * trail_mult
                if side == 1:
                    new_sl = row["high"] - trail_dist
                    if new_sl > sl: sl = new_sl; position["sl"] = sl
                else:
                    new_sl = row["low"] + trail_dist
                    if new_sl < sl: sl = new_sl; position["sl"] = sl

            hit_sl = (side==1 and row["low"]<=sl) or (side==-1 and row["high"]>=sl)
            hit_tp = (side==1 and row["high"]>=tp) or (side==-1 and row["low"]<=tp) if tp > 0 else False

            if hit_sl or hit_tp:
                exit_p = sl if hit_sl else tp
                pnl_pct = side * (exit_p - entry) / entry - (commission+slippage)*2
                pnl = balance * risk_pct * leverage * pnl_pct / sl_mult
                pnl = max(pnl, -balance * risk_pct * leverage)
                balance += pnl; peak = max(peak, balance)
                dd = (peak-balance)/peak if peak>0 else 0; max_dd = max(max_dd, dd)
                trades.append({"pnl": pnl, "reason": "sl" if hit_sl else "tp"})
                position = None
                if balance <= 0: break

        if position is None and sig_row.get("signal", 0) != 0:
            if hasattr(sig_row, "dow") and not pd.isna(sig_row.get("dow")) and sig_row["dow"] >= 5:
                continue
            atr = sig_row["atr"]
            if pd.isna(atr) or atr <= 0: continue
            entry_p = row["close"]; side = int(sig_row["signal"])
            sl_d = atr * sl_mult; tp_d = atr * tp_mult if tp_mult > 0 else 0
            if side == 1:
                sl_p = entry_p - sl_d; tp_p = entry_p + tp_d if tp_d > 0 else 0
            else:
                sl_p = entry_p + sl_d; tp_p = entry_p - tp_d if tp_d > 0 else 0
            position = {"side": side, "entry": entry_p, "sl": sl_p, "tp": tp_p, "trail": trail_mult > 0}

    total = len(trades); wins = sum(1 for t in trades if t["pnl"]>0)
    gp = sum(t["pnl"] for t in trades if t["pnl"]>0)
    gl = sum(abs(t["pnl"]) for t in trades if t["pnl"]<0)
    pf = gp/gl if gl>0 else 0
    wr = wins/total*100 if total>0 else 0
    days = (df.index[-1]-df.index[0]).days
    yr = days/365.25
    return {"trades": total, "tr_yr": total/yr if yr>0 else 0, "wr": wr, "pf": pf,
            "dd": max_dd*100, "balance": balance, "ret": (balance-1000)/10}

# ===== LOAD ALL COINS =====
coins_15m = {}
coins_1h = {}
coin_list = ["btcusdt","dogeusdt","avaxusdt","linkusdt","adausdt","xrpusdt",
             "dotusdt","nearusdt","aptusdt","arbusdt","opusdt","suiusdt","wifusdt"]

for prefix in coin_list:
    name = prefix.replace("usdt","").upper()
    f15 = f"data/{prefix}_15m_2y.csv"
    f1h = f"data/{prefix}_1h_2y.csv"
    if os.path.exists(f15) and os.path.exists(f1h):
        try:
            d15 = add_indicators(load_ohlcv(f15))
            d1h = add_indicators(load_ohlcv(f1h))
            coins_15m[name] = d15
            coins_1h[name] = d1h
        except Exception as e:
            print(f"Skip {name}: {e}")

# Also load 5yr data for ETH/SOL
for prefix, name in [("ethusdt","ETH"), ("solusdt","SOL")]:
    for suffix, store in [("15m_5y", coins_15m), ("1h_5y", coins_1h)]:
        fpath = f"data/{prefix}_{suffix}.csv"
        if os.path.exists(fpath):
            try:
                store[name] = add_indicators(load_ohlcv(fpath))
            except Exception as e:
                print(f"Skip {name} {suffix}: {e}")

print(f"Loaded {len(coins_15m)} coins for 15m, {len(coins_1h)} coins for 1h")

# ===== STRATEGY DEFINITIONS =====
def strategy_ema_cross(df, hours_start=3, hours_end=20, vol_mult=1.3):
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    vol_ok = df["vol_ratio"] >= vol_mult
    rsi_bull = (df["rsi"] >= 45) & (df["rsi"] <= 65)
    rsi_bear = (df["rsi"] >= 35) & (df["rsi"] <= 55)
    signals[df["cross_up"] & in_hours & not_we & vol_ok & rsi_bull] = 1
    signals[df["cross_down"] & in_hours & not_we & vol_ok & rsi_bear] = -1
    signals.iloc[:30] = 0
    df_s = df.copy(); df_s["signal"] = signals
    return df_s

def strategy_pa_trend(df, hours_start=3, hours_end=20, vol_mult=0.7):
    """PA in trend direction: pin bar or engulfing when EMA aligned."""
    signals = pd.Series(0, index=df.index)
    trend_bull = df["ema_f"] > df["ema_s"]
    trend_bear = df["ema_f"] < df["ema_s"]
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    vol_ok = df["vol_ratio"] >= vol_mult

    bull = ((df["pin_bull"] | df["engulf_bull"]) & trend_bull & in_hours & not_we & vol_ok)
    bear = ((df["pin_bear"] | df["engulf_bear"]) & trend_bear & in_hours & not_we & vol_ok)
    signals[bull] = 1; signals[bear] = -1
    signals.iloc[:30] = 0
    df_s = df.copy(); df_s["signal"] = signals
    return df_s

def strategy_ema_plus_pa(df, hours_start=3, hours_end=20):
    """EMA crossover + PA as additional signals."""
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    rsi_bull = (df["rsi"] >= 45) & (df["rsi"] <= 65)
    rsi_bear = (df["rsi"] >= 35) & (df["rsi"] <= 55)
    trend_bull = df["ema_f"] > df["ema_s"]
    trend_bear = df["ema_f"] < df["ema_s"]

    # EMA crossover
    ema_bull = df["cross_up"] & in_hours & not_we & (df["vol_ratio"] >= 1.3) & rsi_bull
    ema_bear = df["cross_down"] & in_hours & not_we & (df["vol_ratio"] >= 1.3) & rsi_bear
    # PA in trend
    pa_bull = (df["pin_bull"] | df["engulf_bull"]) & trend_bull & in_hours & not_we & (df["vol_ratio"] >= 0.7) & rsi_bull
    pa_bear = (df["pin_bear"] | df["engulf_bear"]) & trend_bear & in_hours & not_we & (df["vol_ratio"] >= 0.7) & rsi_bear

    signals[ema_bull | pa_bull] = 1
    signals[ema_bear | pa_bear] = -1
    signals.iloc[:30] = 0
    df_s = df.copy(); df_s["signal"] = signals
    return df_s

def strategy_ichimoku(df, hours_start=3, hours_end=20):
    """Ichimoku Tenkan/Kijun cross above/below cloud."""
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_we = df["dow"] < 5
    signals[df["ichi_bull"] & in_hours & not_we] = 1
    signals[df["ichi_bear"] & in_hours & not_we] = -1
    signals.iloc[:60] = 0
    df_s = df.copy(); df_s["signal"] = signals
    return df_s

def strategy_ichimoku_trail(df, hours_start=3, hours_end=20):
    """Same as ichimoku but we'll use trail instead of fixed TP."""
    return strategy_ichimoku(df, hours_start, hours_end)

# ===== RUN ALL COMBINATIONS =====
strategies = {
    "EMA(9/21)": (strategy_ema_cross, {"sl_mult": 1.0, "tp_mult": 3.0}),
    "PA_Trend":  (strategy_pa_trend,  {"sl_mult": 1.0, "tp_mult": 3.0}),
    "EMA+PA":    (strategy_ema_plus_pa, {"sl_mult": 1.0, "tp_mult": 3.0}),
    "Ichimoku":  (strategy_ichimoku,  {"sl_mult": 2.0, "tp_mult": 5.0}),
    "Ichi+Trail":(strategy_ichimoku_trail, {"sl_mult": 2.5, "tp_mult": 0, "trail_mult": 3.0}),
}

timeframes = {"15m": coins_15m, "1h": coins_1h}

print("\n" + "="*80)
print("MEGA SWEEP: All Coins x All Strategies x 15m + 1h")
print("="*80)
print(f"\n{'Coin':<6} {'TF':<4} {'Strategy':<12} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}")
print("-"*60)

best_results = []
all_results = []

for tf_name, coin_data in timeframes.items():
    for coin_name, df in sorted(coin_data.items()):
        for strat_name, (strat_fn, sim_params) in strategies.items():
            try:
                df_sig = strat_fn(df)
                r = simulate(df_sig, **sim_params)
                all_results.append((coin_name, tf_name, strat_name, r))
                if r["trades"] >= 5:
                    print(f"{coin_name:<6} {tf_name:<4} {strat_name:<12} {r['trades']:>6} {r['tr_yr']:>5.0f} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%")
                    if r["pf"] > 1.15:
                        best_results.append((coin_name, tf_name, strat_name, r))
            except Exception as e:
                print(f"  ERROR {coin_name} {tf_name} {strat_name}: {e}")

print("\n" + "="*80)
print("TOP RESULTS (PF > 1.15)")
print("="*80)
print(f"{'Coin':<6} {'TF':<4} {'Strategy':<12} {'Trades':>6} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'DD%':>6}")
print("-"*60)
for coin, tf, strat, r in sorted(best_results, key=lambda x: -x[3]["pf"]):
    print(f"{coin:<6} {tf:<4} {strat:<12} {r['trades']:>6} {r['tr_yr']:>5.0f} {r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}%")

# ===== SUMMARY BY STRATEGY =====
print("\n" + "="*80)
print("AVERAGE PF BY STRATEGY (across all coins, min 5 trades)")
print("="*80)
for strat_name in strategies:
    for tf_name in ["15m", "1h"]:
        vals = [r["pf"] for (c, tf, s, r) in all_results if s==strat_name and tf==tf_name and r["trades"]>=5]
        if vals:
            avg = sum(vals)/len(vals)
            above1 = sum(1 for v in vals if v > 1.0)
            print(f"  {strat_name:<12} {tf_name:<4}: avg PF={avg:.2f}  coins>1.0: {above1}/{len(vals)}")

# ===== SUMMARY BY COIN =====
print("\n" + "="*80)
print("BEST STRATEGY PER COIN (1h)")
print("="*80)
for coin_name in sorted(coins_1h.keys()):
    coin_res = [(s, r) for (c, tf, s, r) in all_results if c==coin_name and tf=="1h" and r["trades"]>=5]
    if coin_res:
        best = max(coin_res, key=lambda x: x[1]["pf"])
        s, r = best
        print(f"  {coin_name:<6}: best={s:<12} PF={r['pf']:.2f} trades={r['trades']} WR={r['wr']:.1f}% DD={r['dd']:.1f}%")

print("\n" + "="*80)
print("BEST STRATEGY PER COIN (15m)")
print("="*80)
for coin_name in sorted(coins_15m.keys()):
    coin_res = [(s, r) for (c, tf, s, r) in all_results if c==coin_name and tf=="15m" and r["trades"]>=5]
    if coin_res:
        best = max(coin_res, key=lambda x: x[1]["pf"])
        s, r = best
        print(f"  {coin_name:<6}: best={s:<12} PF={r['pf']:.2f} trades={r['trades']} WR={r['wr']:.1f}% DD={r['dd']:.1f}%")
