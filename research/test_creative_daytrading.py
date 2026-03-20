"""Creative day trading approaches - lightweight simulator."""
import json, sys, os
import numpy as np
import pandas as pd
sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.data_loader import load_ohlcv
from bot.data import compute_ema, compute_rsi, compute_atr, compute_volume_ma

# Load data
df5 = load_ohlcv("data/btcusdt_5m_2y.csv")
df1h = load_ohlcv("data/btcusdt_1h_2y.csv")

print(f"5m: {len(df5)} candles, {df5.index[0]} to {df5.index[-1]}")

# Add indicators to 5m
df5["ema9"] = compute_ema(df5["close"], 9)
df5["ema21"] = compute_ema(df5["close"], 21)
df5["ema50"] = compute_ema(df5["close"], 50)
df5["rsi"] = compute_rsi(df5["close"], 14)
df5["atr"] = compute_atr(df5["high"], df5["low"], df5["close"], 14)
df5["vol_ma"] = compute_volume_ma(df5["volume"], 20)
df5["vol_ratio"] = df5["volume"] / df5["vol_ma"].clip(lower=1)

# Add 1h trend (merge)
df1h["ema50_1h"] = compute_ema(df1h["close"], 50)
df1h["ema21_1h"] = compute_ema(df1h["close"], 21)
trend_feat = pd.DataFrame({
    "ema50_1h": df1h["ema50_1h"],
    "ema21_1h": df1h["ema21_1h"],
}, index=df1h.index).shift(1)  # Shift to prevent look-ahead

df5 = pd.merge_asof(df5.reset_index(), trend_feat.reset_index(),
                      on="timestamp", direction="backward").set_index("timestamp")
df5["above_trend"] = df5["close"] > df5["ema50_1h"]
df5["below_trend"] = df5["close"] < df5["ema50_1h"]

# Session info
df5["hour"] = df5.index.hour
df5["dayofweek"] = df5.index.dayofweek

# Previous day high/low (for session strategies)
df5["date"] = df5.index.date
daily = df5.groupby("date").agg({"high": "max", "low": "min"}).shift(1)
daily.columns = ["prev_day_high", "prev_day_low"]
df5 = df5.join(daily, on="date")

# Lightweight simulator
def simulate(signals_df, sl_atr_mult=1.0, tp_atr_mult=3.0, risk_pct=0.10,
             leverage=25, commission=0.00055, slippage=0.0002, name=""):
    """Simple event-driven simulator.
    signals_df must have 'signal' column: 1=long, -1=short, 0=none
    Uses iloc[-2] convention: signal at row i means entry at row i+1's close.
    """
    balance = 1000.0
    initial = balance
    peak = balance
    max_dd = 0
    trades = []
    position = None

    for i in range(2, len(signals_df)):
        row = signals_df.iloc[i]
        signal_row = signals_df.iloc[i-1]  # Signal candle (closed)

        # Check exits first
        if position is not None:
            side = position["side"]
            entry = position["entry"]
            sl = position["sl"]
            tp = position["tp"]

            hit_sl = (side == 1 and row["low"] <= sl) or (side == -1 and row["high"] >= sl)
            hit_tp = (side == 1 and row["high"] >= tp) or (side == -1 and row["low"] <= tp)

            if hit_sl or hit_tp:
                if hit_sl:
                    exit_price = sl
                    reason = "sl"
                else:
                    exit_price = tp
                    reason = "tp"

                pnl_pct = side * (exit_price - entry) / entry
                cost = (commission + slippage) * 2
                net_pnl_pct = pnl_pct - cost
                pnl = balance * risk_pct * leverage * net_pnl_pct / (sl_atr_mult if sl_atr_mult > 0 else 1)
                # Cap PnL to position size
                pos_size = balance * risk_pct * leverage
                pnl = max(pnl, -pos_size)

                balance += pnl
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)

                trades.append({"pnl": pnl, "reason": reason, "side": side})
                position = None

                if balance <= 0:
                    break

        # Check entries (no position)
        if position is None and signal_row["signal"] != 0:
            # Weekend filter
            if row["dayofweek"] >= 5:
                continue

            entry_price = row["close"]
            atr = signal_row["atr"]
            if pd.isna(atr) or atr <= 0:
                continue

            side = signal_row["signal"]
            sl_dist = atr * sl_atr_mult
            tp_dist = atr * tp_atr_mult

            if side == 1:
                sl = entry_price - sl_dist
                tp = entry_price + tp_dist
            else:
                sl = entry_price + sl_dist
                tp = entry_price - tp_dist

            position = {"side": side, "entry": entry_price, "sl": sl, "tp": tp}

    # Close any open position
    if position is not None:
        exit_price = signals_df.iloc[-1]["close"]
        side = position["side"]
        pnl_pct = side * (exit_price - position["entry"]) / position["entry"]
        cost = (commission + slippage) * 2
        net_pnl_pct = pnl_pct - cost
        pnl = balance * risk_pct * leverage * net_pnl_pct
        balance += pnl
        trades.append({"pnl": pnl, "reason": "close", "side": side})

    total = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gross_p = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_l = sum(abs(t["pnl"]) for t in trades if t["pnl"] < 0)
    pf = gross_p / gross_l if gross_l > 0 else 0
    wr = wins / total * 100 if total > 0 else 0
    ret = (balance - initial) / initial * 100
    days = (signals_df.index[-1] - signals_df.index[0]).days
    yr = days / 365.25

    return {
        "name": name, "trades": total, "wr": wr, "pf": pf,
        "ret": ret, "dd": max_dd * 100, "tr_yr": total/yr if yr>0 else 0,
        "balance": balance,
    }

# ====== STRATEGY 1: Opening Range Breakout (ORB) ======
# First 30 min of US session (13:00-13:30 UTC), trade breakout
def strategy_orb(df, session_start=13, range_bars=6, min_range_atr=0.5):
    """Opening Range Breakout: Mark first 30min range of US session, trade breakout."""
    signals = pd.Series(0, index=df.index)
    range_high = pd.Series(np.nan, index=df.index)
    range_low = pd.Series(np.nan, index=df.index)

    for date in df["date"].unique():
        day_mask = df["date"] == date
        day_data = df[day_mask]

        # Find the first bar at session_start
        session_bars = day_data[(day_data["hour"] == session_start)]
        if len(session_bars) < range_bars:
            continue

        # Range = first N bars high/low
        first_bars = session_bars.iloc[:range_bars]
        rh = first_bars["high"].max()
        rl = first_bars["low"].min()
        r_size = rh - rl

        # After range period, look for breakout
        after_range = day_data[day_data.index > first_bars.index[-1]]
        if len(after_range) == 0:
            continue

        atr_at_range = first_bars["atr"].iloc[-1]
        if pd.isna(atr_at_range) or r_size < atr_at_range * min_range_atr:
            continue

        # Only trade until end_hour
        after_range = after_range[after_range["hour"] < 21]

        # Use 1h trend filter for direction
        trend_up = first_bars.iloc[-1].get("above_trend", True)
        trend_down = first_bars.iloc[-1].get("below_trend", True)

        for idx in after_range.index:
            row = df.loc[idx]
            if trend_up and row["close"] > rh:
                signals.loc[idx] = 1
                break
            elif trend_down and row["close"] < rl:
                signals.loc[idx] = -1
                break

    df_sig = df.copy()
    df_sig["signal"] = signals
    return df_sig

# ====== STRATEGY 2: Momentum Ignition ======
# Sudden volume spike (>3x) in trend direction = enter
def strategy_momentum_ignition(df, vol_spike=3.0, min_body_pct=0.5,
                                 hours_start=7, hours_end=21):
    """Momentum ignition: volume spike + strong body in trend direction."""
    body = (df["close"] - df["open"])
    candle_range = (df["high"] - df["low"]).clip(lower=1e-10)
    body_pct = body.abs() / candle_range

    is_bull = (body > 0) & (body_pct >= min_body_pct)
    is_bear = (body < 0) & (body_pct >= min_body_pct)

    vol_spike_mask = df["vol_ratio"] >= vol_spike
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_weekend = df["dayofweek"] < 5

    # RSI filter
    rsi_bull = (df["rsi"] >= 45) & (df["rsi"] <= 70)
    rsi_bear = (df["rsi"] >= 30) & (df["rsi"] <= 55)

    signals = pd.Series(0, index=df.index)
    signals[is_bull & vol_spike_mask & in_hours & not_weekend & rsi_bull & df["above_trend"]] = 1
    signals[is_bear & vol_spike_mask & in_hours & not_weekend & rsi_bear & df["below_trend"]] = -1

    df_sig = df.copy()
    df_sig["signal"] = signals
    return df_sig

# ====== STRATEGY 3: Previous Day High/Low Breakout ======
def strategy_prev_day_breakout(df, hours_start=7, hours_end=21):
    """Break above previous day high = long, break below = short."""
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_weekend = df["dayofweek"] < 5

    bull = (df["close"] > df["prev_day_high"]) & in_hours & not_weekend & df["above_trend"]
    bear = (df["close"] < df["prev_day_low"]) & in_hours & not_weekend & df["below_trend"]

    signals[bull] = 1
    signals[bear] = -1

    # Only take first signal per day per direction
    df_sig = df.copy()
    df_sig["signal"] = signals

    # Deduplicate: only first signal per day
    last_signal_date = None
    for i in range(len(df_sig)):
        if df_sig.iloc[i]["signal"] != 0:
            d = df_sig.iloc[i]["date"]
            if d == last_signal_date:
                df_sig.iloc[i, df_sig.columns.get_loc("signal")] = 0
            else:
                last_signal_date = d

    return df_sig

# ====== STRATEGY 4: Mean Reversion Scalp (5m RSI extreme in trend) ======
def strategy_mr_scalp(df, rsi_oversold=25, rsi_overbought=75, hours_start=7, hours_end=21):
    """RSI extreme on 5m while 1h trend intact = scalp entry."""
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_weekend = df["dayofweek"] < 5
    trend_bull = df["above_trend"] & (df["ema9"] > df["ema21"])
    trend_bear = df["below_trend"] & (df["ema9"] < df["ema21"])

    # Oversold in uptrend = buy dip
    bull = (df["rsi"] < rsi_oversold) & trend_bull & in_hours & not_weekend
    # Overbought in downtrend = sell rally
    bear = (df["rsi"] > rsi_overbought) & trend_bear & in_hours & not_weekend

    signals[bull] = 1
    signals[bear] = -1

    df_sig = df.copy()
    df_sig["signal"] = signals
    return df_sig

# ====== STRATEGY 5: EMA Crossover on 5m (baseline for comparison) ======
def strategy_5m_ema_cross(df, fast=9, slow=21, hours_start=3, hours_end=20):
    """Standard EMA crossover on 5m."""
    ema_f = compute_ema(df["close"], fast)
    ema_s = compute_ema(df["close"], slow)

    cross_up = (ema_f > ema_s) & (ema_f.shift(1) <= ema_s.shift(1))
    cross_down = (ema_f < ema_s) & (ema_f.shift(1) >= ema_s.shift(1))

    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_weekend = df["dayofweek"] < 5
    vol_ok = df["vol_ratio"] >= 1.3
    rsi_bull = (df["rsi"] >= 45) & (df["rsi"] <= 65)
    rsi_bear = (df["rsi"] >= 35) & (df["rsi"] <= 55)

    signals = pd.Series(0, index=df.index)
    signals[cross_up & in_hours & not_weekend & vol_ok & rsi_bull & df["above_trend"]] = 1
    signals[cross_down & in_hours & not_weekend & vol_ok & rsi_bear & df["below_trend"]] = -1

    warmup = max(fast, slow) + 10
    signals.iloc[:warmup] = 0

    df_sig = df.copy()
    df_sig["signal"] = signals
    return df_sig

# ====== STRATEGY 6: Volatility Breakout (Larry Williams) ======
def strategy_vol_breakout(df, atr_mult=0.6, hours_start=7, hours_end=21):
    """Enter when price moves ATR_mult * ATR from open of session."""
    signals = pd.Series(0, index=df.index)
    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_weekend = df["dayofweek"] < 5

    # Session open price (first bar of trading hours)
    session_open = pd.Series(np.nan, index=df.index)
    for date in df["date"].unique():
        day_data = df[(df["date"] == date) & (df["hour"] >= hours_start)]
        if len(day_data) > 0:
            session_open.loc[day_data.index] = day_data.iloc[0]["open"]

    breakout_up = (df["close"] > session_open + df["atr"] * atr_mult) & in_hours & not_weekend & df["above_trend"]
    breakout_down = (df["close"] < session_open - df["atr"] * atr_mult) & in_hours & not_weekend & df["below_trend"]

    signals[breakout_up] = 1
    signals[breakout_down] = -1

    # Only first signal per day
    df_sig = df.copy()
    df_sig["signal"] = signals
    last_date = None
    for i in range(len(df_sig)):
        if df_sig.iloc[i]["signal"] != 0:
            d = df_sig.iloc[i]["date"]
            if d == last_date:
                df_sig.iloc[i, df_sig.columns.get_loc("signal")] = 0
            else:
                last_date = d

    return df_sig

# ====== STRATEGY 7: Triple Screen (Elder) ======
def strategy_triple_screen(df, hours_start=7, hours_end=21):
    """1h trend (EMA50) + 5m pullback to EMA21 + RSI oversold."""
    # Pullback: price touches EMA21 from above in uptrend
    near_ema = (df["low"] <= df["ema21"] * 1.002) & (df["close"] > df["ema21"])
    trend_bull = df["above_trend"] & (df["ema9"] > df["ema21"])

    near_ema_short = (df["high"] >= df["ema21"] * 0.998) & (df["close"] < df["ema21"])
    trend_bear = df["below_trend"] & (df["ema9"] < df["ema21"])

    in_hours = (df["hour"] >= hours_start) & (df["hour"] < hours_end)
    not_weekend = df["dayofweek"] < 5

    # RSI should show mild oversold for longs (pullback entry)
    rsi_bull = (df["rsi"] >= 30) & (df["rsi"] <= 50)
    rsi_bear = (df["rsi"] >= 50) & (df["rsi"] <= 70)

    signals = pd.Series(0, index=df.index)
    signals[near_ema & trend_bull & in_hours & not_weekend & rsi_bull] = 1
    signals[near_ema_short & trend_bear & in_hours & not_weekend & rsi_bear] = -1

    df_sig = df.copy()
    df_sig["signal"] = signals
    return df_sig

# ====== RUN ALL STRATEGIES ======
print("\n" + "="*100)
print("CREATIVE DAY TRADING STRATEGIES ON BTC 5m")
print("="*100)

strategies = [
    ("ORB (US Session)", lambda: strategy_orb(df5)),
    ("Momentum Ignition 3x", lambda: strategy_momentum_ignition(df5, vol_spike=3.0)),
    ("Momentum Ignition 2x", lambda: strategy_momentum_ignition(df5, vol_spike=2.0)),
    ("Prev Day H/L Break", lambda: strategy_prev_day_breakout(df5)),
    ("MR Scalp RSI25/75", lambda: strategy_mr_scalp(df5, 25, 75)),
    ("MR Scalp RSI30/70", lambda: strategy_mr_scalp(df5, 30, 70)),
    ("5m EMA(9/21) Cross", lambda: strategy_5m_ema_cross(df5)),
    ("5m EMA(5/13) Cross", lambda: strategy_5m_ema_cross(df5, fast=5, slow=13)),
    ("Vol Breakout 0.6ATR", lambda: strategy_vol_breakout(df5, 0.6)),
    ("Vol Breakout 1.0ATR", lambda: strategy_vol_breakout(df5, 1.0)),
    ("Triple Screen", lambda: strategy_triple_screen(df5)),
]

sl_tp_configs = [
    (1.0, 3.0, "SL1.0/TP3.0"),
    (1.5, 4.5, "SL1.5/TP4.5"),
    (0.7, 2.0, "SL0.7/TP2.0"),
]

print(f"\n{'Strategy':<30} {'SL/TP':>12} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'PF':>6} {'Ret%':>9} {'DD%':>6}")
print("-"*90)

for strat_name, strat_fn in strategies:
    try:
        df_sig = strat_fn()
        sig_count = (df_sig["signal"] != 0).sum()

        for sl, tp, sltp_name in sl_tp_configs:
            result = simulate(df_sig, sl_atr_mult=sl, tp_atr_mult=tp, name=f"{strat_name} {sltp_name}")
            print(f"{strat_name:<30} {sltp_name:>12} {result['trades']:>7} {result['tr_yr']:>5.0f} {result['wr']:>5.1f}% {result['pf']:>6.2f} {result['ret']:>8.1f}% {result['dd']:>5.1f}%")
        print()  # Blank line between strategies
    except Exception as e:
        print(f"{strat_name:<30} ERROR: {e}")
        import traceback; traceback.print_exc()
