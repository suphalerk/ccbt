"""
Gold (XAU/USD) Strategy Research — 10 Strategies
Standalone simulators for rapid testing on 1H data.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional, Tuple, List

# ─── Data Loading ───────────────────────────────────────────────

def load_gold_1h():
    df = pd.read_csv("/Users/iceai/Work/ccbt/data/xauusd_1h_2y.csv", parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df

def load_gold_1d():
    df = pd.read_csv("/Users/iceai/Work/ccbt/data/xauusd_1d_10y.csv", parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df

# ─── Core Simulator ────────────────────────────────────────────

COMMISSION = 0.0002  # 0.02% round-trip approx for gold spread

@dataclass
class Trade:
    entry_time: object
    side: str
    entry_price: float
    sl: float
    tp: float
    size: float  # notional in USD
    exit_time: object = None
    exit_price: float = 0.0
    pnl: float = 0.0
    reason: str = ""


def sim(df: pd.DataFrame, signals: pd.DataFrame,
        sl_mult: float = 1.5, tp_mult: float = 3.0,
        trail_mult: float = 0.0,
        risk_pct: float = 0.02, initial_balance: float = 10000.0,
        commission: float = COMMISSION,
        atr_col: str = "atr",
        max_trades_per_day: int = 3,
        hours_filter: Tuple[int, int] = None,  # (start, end) UTC
        ) -> dict:
    """
    Generic event-driven simulator.

    signals DataFrame must have columns: 'signal' (1=long, -1=short, 0=none)
    df must have OHLCV + atr_col

    SL/TP are ATR-based multiples from entry.
    trail_mult > 0 enables trailing stop at trail_mult * ATR.
    """
    balance = initial_balance
    peak_balance = initial_balance
    max_dd = 0.0
    trades = []
    position = None
    daily_count = {}

    for i in range(1, len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        date_key = ts.date()

        # --- Check existing position ---
        if position is not None:
            high, low, close = row["high"], row["low"], row["close"]
            atr_now = row[atr_col] if pd.notna(row.get(atr_col)) else 0

            # Check SL
            if position.side == "long":
                if low <= position.sl:
                    _close(position, position.sl, ts, "stop_loss", balance, commission)
                    balance += position.pnl
                    trades.append(position)
                    position = None
                elif high >= position.tp:
                    _close(position, position.tp, ts, "take_profit", balance, commission)
                    balance += position.pnl
                    trades.append(position)
                    position = None
                elif trail_mult > 0 and atr_now > 0:
                    new_sl = close - trail_mult * atr_now
                    if new_sl > position.sl:
                        position.sl = new_sl
            else:  # short
                if high >= position.sl:
                    _close(position, position.sl, ts, "stop_loss", balance, commission)
                    balance += position.pnl
                    trades.append(position)
                    position = None
                elif low <= position.tp:
                    _close(position, position.tp, ts, "take_profit", balance, commission)
                    balance += position.pnl
                    trades.append(position)
                    position = None
                elif trail_mult > 0 and atr_now > 0:
                    new_sl = close + trail_mult * atr_now
                    if new_sl < position.sl:
                        position.sl = new_sl

            # Update drawdown
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance
            if dd > max_dd:
                max_dd = dd
            continue  # Don't open new position while one is open

        # --- Check for new entry ---
        if i >= len(signals):
            continue
        sig = signals.iloc[i]
        if sig.get("signal", 0) == 0:
            continue

        # Hours filter
        if hours_filter:
            h = ts.hour
            if hours_filter[0] < hours_filter[1]:
                if not (hours_filter[0] <= h < hours_filter[1]):
                    continue
            else:
                if hours_filter[1] <= h < hours_filter[0]:
                    continue

        # Weekend filter (gold doesn't trade Sat-Sun)
        if ts.dayofweek >= 5:
            continue

        # Daily trade limit
        daily_count[date_key] = daily_count.get(date_key, 0)
        if daily_count[date_key] >= max_trades_per_day:
            continue

        atr = row[atr_col] if pd.notna(row.get(atr_col)) else 0
        if atr <= 0:
            continue

        entry_price = row["close"]
        side = "long" if sig["signal"] == 1 else "short"

        if side == "long":
            sl = entry_price - sl_mult * atr
            tp = entry_price + tp_mult * atr
        else:
            sl = entry_price + sl_mult * atr
            tp = entry_price - tp_mult * atr

        # Position sizing: risk-based
        sl_dist = abs(entry_price - sl)
        if sl_dist <= 0:
            continue
        risk_amount = balance * risk_pct
        size = risk_amount / (sl_dist / entry_price)  # notional

        # Commission on entry
        comm = size * commission
        balance -= comm

        position = Trade(
            entry_time=ts, side=side, entry_price=entry_price,
            sl=sl, tp=tp, size=size
        )
        daily_count[date_key] += 1

    # Close any remaining position
    if position is not None:
        _close(position, df.iloc[-1]["close"], df.index[-1], "end", balance, commission)
        balance += position.pnl
        trades.append(position)

    return _summarize(trades, initial_balance, balance, max_dd, df)


def _close(trade: Trade, exit_price: float, exit_time, reason: str,
           balance: float, commission: float):
    if trade.side == "long":
        pnl_pct = (exit_price - trade.entry_price) / trade.entry_price
    else:
        pnl_pct = (trade.entry_price - exit_price) / trade.entry_price
    trade.pnl = trade.size * pnl_pct - trade.size * commission  # exit commission
    trade.exit_price = exit_price
    trade.exit_time = exit_time
    trade.reason = reason


def _summarize(trades: list, initial_balance: float, final_balance: float,
               max_dd: float, df: pd.DataFrame) -> dict:
    if not trades:
        return {"trades": 0, "final": initial_balance, "pf": 0, "wr": 0,
                "dd": 0, "ann_ret": 0, "trades_per_year": 0}

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in wins)
    gross_loss = abs(sum(t.pnl for t in losses))

    # Time span in years
    span_days = (df.index[-1] - df.index[0]).days
    years = max(span_days / 365.25, 0.01)

    pf = gross_profit / gross_loss if gross_loss > 0 else 999
    wr = len(wins) / len(trades) * 100 if trades else 0
    total_ret = (final_balance - initial_balance) / initial_balance * 100
    ann_ret = total_ret / years

    return {
        "trades": len(trades),
        "trades_per_year": len(trades) / years,
        "final": round(final_balance, 2),
        "total_ret": round(total_ret, 1),
        "ann_ret": round(ann_ret, 1),
        "wr": round(wr, 1),
        "pf": round(pf, 2),
        "dd": round(max_dd * 100, 1),
        "avg_win": round(np.mean([t.pnl for t in wins]), 2) if wins else 0,
        "avg_loss": round(np.mean([t.pnl for t in losses]), 2) if losses else 0,
    }


def print_result(name: str, config: str, r: dict):
    print(f"  {config}: ${r['final']:,.0f} ({r['ann_ret']:.0f}%/yr) | "
          f"{r['trades']}t ({r['trades_per_year']:.0f}/yr) | "
          f"WR {r['wr']:.0f}% | PF {r['pf']:.2f} | DD {r['dd']:.1f}%")


# ─── Indicators ─────────────────────────────────────────────────

def add_ema(df, col="close", period=9, name=None):
    name = name or f"ema_{period}"
    df[name] = df[col].ewm(span=period, adjust=False).mean()
    return df

def add_sma(df, col="close", period=20, name=None):
    name = name or f"sma_{period}"
    df[name] = df[col].rolling(period).mean()
    return df

def add_rsi(df, period=14, col="close"):
    delta = df[col].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df

def add_atr(df, period=14):
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=period, adjust=False).mean()
    return df

def add_bb(df, period=20, std_mult=2.0, col="close"):
    sma = df[col].rolling(period).mean()
    std = df[col].rolling(period).std()
    df["bb_upper"] = sma + std_mult * std
    df["bb_lower"] = sma - std_mult * std
    df["bb_mid"] = sma
    df["bb_pct"] = (df[col] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"])
    return df

def add_vwap_session(df):
    """Compute session VWAP (resets each day)."""
    df["vwap"] = np.nan
    for date, group in df.groupby(df.index.date):
        if "volume" in df.columns and (group["volume"] > 0).any():
            cum_vol = group["volume"].cumsum()
            cum_pv = (group["close"] * group["volume"]).cumsum()
            vwap = cum_pv / cum_vol.replace(0, np.nan)
            df.loc[group.index, "vwap"] = vwap
        else:
            # No volume: use rolling mean as proxy
            df.loc[group.index, "vwap"] = group["close"].expanding().mean()
    return df

def add_momentum(df, period=10, col="close"):
    df["momentum"] = df[col].pct_change(period) * 100
    df["roc"] = df[col].pct_change(period) * 100
    return df

def add_ichimoku(df):
    high9 = df["high"].rolling(9).max()
    low9 = df["low"].rolling(9).min()
    df["tenkan"] = (high9 + low9) / 2

    high26 = df["high"].rolling(26).max()
    low26 = df["low"].rolling(26).min()
    df["kijun"] = (high26 + low26) / 2

    df["senkou_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(26)

    high52 = df["high"].rolling(52).max()
    low52 = df["low"].rolling(52).min()
    df["senkou_b"] = ((high52 + low52) / 2).shift(26)

    df["chikou"] = df["close"].shift(-26)
    return df

def add_pivot_points(df):
    """Daily pivot points on hourly data."""
    df["pivot"] = np.nan
    df["r1"] = np.nan
    df["s1"] = np.nan
    df["r2"] = np.nan
    df["s2"] = np.nan

    dates = df.index.date
    unique_dates = sorted(set(dates))

    for i in range(1, len(unique_dates)):
        prev_date = unique_dates[i - 1]
        curr_date = unique_dates[i]

        prev_data = df[df.index.date == prev_date]
        if len(prev_data) == 0:
            continue

        h = prev_data["high"].max()
        l = prev_data["low"].min()
        c = prev_data["close"].iloc[-1]

        pivot = (h + l + c) / 3
        r1 = 2 * pivot - l
        s1 = 2 * pivot - h
        r2 = pivot + (h - l)
        s2 = pivot - (h - l)

        mask = df.index.date == curr_date
        df.loc[mask, "pivot"] = pivot
        df.loc[mask, "r1"] = r1
        df.loc[mask, "s1"] = s1
        df.loc[mask, "r2"] = r2
        df.loc[mask, "s2"] = s2

    return df


# ═══════════════════════════════════════════════════════════════
# STRATEGY 1: Session Breakout
# ═══════════════════════════════════════════════════════════════

def strategy_session_breakout(df, session_start=8, lookback_hours=4,
                               breakout_atr_mult=0.5):
    """
    Trade London/NY open breakout.
    - Compute range of previous N hours (Asian session for London, morning for NY)
    - If price breaks above range + ATR*mult: LONG
    - If price breaks below range - ATR*mult: SHORT
    """
    df = add_atr(df)
    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(lookback_hours + 1, len(df)):
        ts = df.index[i]
        if ts.hour != session_start:
            continue

        # Range of previous lookback_hours candles
        window = df.iloc[i-lookback_hours:i]
        range_high = window["high"].max()
        range_low = window["low"].min()
        atr = df.iloc[i]["atr"]

        if pd.isna(atr) or atr <= 0:
            continue

        close = df.iloc[i]["close"]
        threshold = breakout_atr_mult * atr

        if close > range_high + threshold:
            signals.iloc[i, 0] = 1
        elif close < range_low - threshold:
            signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 2: VWAP Reversion
# ═══════════════════════════════════════════════════════════════

def strategy_vwap_reversion(df, distance_atr_mult=1.5):
    """
    Mean reversion to VWAP.
    - When price is distance_atr_mult * ATR below VWAP: LONG
    - When price is distance_atr_mult * ATR above VWAP: SHORT
    """
    df = add_atr(df)
    df = add_vwap_session(df)
    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(1, len(df)):
        row = df.iloc[i]
        if pd.isna(row.get("vwap")) or pd.isna(row.get("atr")):
            continue

        dist = row["close"] - row["vwap"]
        threshold = distance_atr_mult * row["atr"]

        if dist < -threshold:
            signals.iloc[i, 0] = 1  # oversold, buy
        elif dist > threshold:
            signals.iloc[i, 0] = -1  # overbought, sell

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 3: Bollinger Band Bounce
# ═══════════════════════════════════════════════════════════════

def strategy_bb_bounce(df, bb_period=20, bb_std=2.0, rsi_filter=True):
    """
    Buy at lower BB, sell at upper BB.
    Optional RSI filter: only buy when RSI < 35, sell when RSI > 65.
    """
    df = add_atr(df)
    df = add_bb(df, period=bb_period, std_mult=bb_std)
    if rsi_filter:
        df = add_rsi(df)

    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if pd.isna(row.get("bb_lower")) or pd.isna(row.get("atr")):
            continue

        # Touch lower BB and bounce back
        if prev["low"] <= prev["bb_lower"] and row["close"] > row["bb_lower"]:
            if rsi_filter and pd.notna(row.get("rsi")):
                if row["rsi"] < 40:
                    signals.iloc[i, 0] = 1
            else:
                signals.iloc[i, 0] = 1

        # Touch upper BB and pull back
        elif prev["high"] >= prev["bb_upper"] and row["close"] < row["bb_upper"]:
            if rsi_filter and pd.notna(row.get("rsi")):
                if row["rsi"] > 60:
                    signals.iloc[i, 0] = -1
            else:
                signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 4: Ichimoku Cloud
# ═══════════════════════════════════════════════════════════════

def strategy_ichimoku(df):
    """
    Classic Ichimoku signals:
    - LONG: Tenkan crosses above Kijun, price above cloud
    - SHORT: Tenkan crosses below Kijun, price below cloud
    """
    df = add_atr(df)
    df = add_ichimoku(df)
    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        for col in ["tenkan", "kijun", "senkou_a", "senkou_b", "atr"]:
            if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
                break
        else:
            cloud_top = max(row["senkou_a"], row["senkou_b"])
            cloud_bottom = min(row["senkou_a"], row["senkou_b"])

            # Tenkan/Kijun cross
            tk_cross_up = prev["tenkan"] <= prev["kijun"] and row["tenkan"] > row["kijun"]
            tk_cross_dn = prev["tenkan"] >= prev["kijun"] and row["tenkan"] < row["kijun"]

            if tk_cross_up and row["close"] > cloud_top:
                signals.iloc[i, 0] = 1
            elif tk_cross_dn and row["close"] < cloud_bottom:
                signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 5: Pivot Point S/R
# ═══════════════════════════════════════════════════════════════

def strategy_pivot(df, bounce_mode=True):
    """
    Pivot point trading:
    - bounce_mode=True: Buy at S1, sell at R1
    - bounce_mode=False: Buy breakout above R1, sell breakdown below S1
    """
    df = add_atr(df)
    df = add_pivot_points(df)
    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if pd.isna(row.get("pivot")) or pd.isna(row.get("atr")):
            continue

        if bounce_mode:
            # Bounce off support
            if prev["low"] <= prev.get("s1", 0) and row["close"] > row.get("s1", 0):
                signals.iloc[i, 0] = 1
            # Bounce off resistance
            elif prev["high"] >= prev.get("r1", 99999) and row["close"] < row.get("r1", 99999):
                signals.iloc[i, 0] = -1
        else:
            # Breakout above R1
            if prev["close"] < prev.get("r1", 99999) and row["close"] > row.get("r1", 99999):
                signals.iloc[i, 0] = 1
            # Breakdown below S1
            elif prev["close"] > prev.get("s1", 0) and row["close"] < row.get("s1", 0):
                signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 6: Asian Range Breakout
# ═══════════════════════════════════════════════════════════════

def strategy_asian_range_breakout(df, asian_start=0, asian_end=7,
                                   breakout_start=8, breakout_end=16):
    """
    Asian session range, London breakout.
    - Compute high/low of 00:00-07:00 UTC (Asian session)
    - Trade breakout during 08:00-16:00 UTC (London/NY)
    """
    df = add_atr(df)
    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    dates = sorted(set(df.index.date))

    for date in dates:
        day_data = df[df.index.date == date]

        # Asian session
        asian = day_data[(day_data.index.hour >= asian_start) & (day_data.index.hour < asian_end)]
        if len(asian) < 3:
            continue

        range_high = asian["high"].max()
        range_low = asian["low"].min()

        # Breakout session
        breakout = day_data[(day_data.index.hour >= breakout_start) & (day_data.index.hour < breakout_end)]

        triggered = False
        for idx in breakout.index:
            if triggered:
                break
            row = df.loc[idx]
            if pd.isna(row.get("atr")) or row["atr"] <= 0:
                continue

            if row["close"] > range_high:
                signals.loc[idx, "signal"] = 1
                triggered = True
            elif row["close"] < range_low:
                signals.loc[idx, "signal"] = -1
                triggered = True

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 7: EMA Ribbon (Multiple EMAs)
# ═══════════════════════════════════════════════════════════════

def strategy_ema_ribbon(df, periods=(8, 13, 21, 34, 55)):
    """
    EMA ribbon: all EMAs aligned = strong trend.
    - LONG: All EMAs in bullish order (fast > slow) and price > all EMAs
    - SHORT: All EMAs in bearish order
    Signal fires on transition (wasn't aligned, now is).
    """
    df = add_atr(df)
    for p in periods:
        df = add_ema(df, period=p, name=f"ema_{p}")

    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    prev_bullish = False
    prev_bearish = False

    for i in range(max(periods) + 1, len(df)):
        row = df.iloc[i]

        ema_vals = [row[f"ema_{p}"] for p in periods]
        if any(pd.isna(v) for v in ema_vals):
            continue

        # Check bullish alignment: each shorter EMA > next longer EMA
        bullish = all(ema_vals[j] > ema_vals[j+1] for j in range(len(ema_vals)-1))
        bearish = all(ema_vals[j] < ema_vals[j+1] for j in range(len(ema_vals)-1))

        # Also require price above/below all EMAs
        bullish = bullish and row["close"] > ema_vals[0]
        bearish = bearish and row["close"] < ema_vals[0]

        if bullish and not prev_bullish:
            signals.iloc[i, 0] = 1
        elif bearish and not prev_bearish:
            signals.iloc[i, 0] = -1

        prev_bullish = bullish
        prev_bearish = bearish

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 8: RSI Trend (Buy oversold in uptrend only)
# ═══════════════════════════════════════════════════════════════

def strategy_rsi_trend(df, ema_period=50, rsi_buy=30, rsi_sell=70):
    """
    Buy RSI oversold ONLY when above EMA (uptrend).
    Sell RSI overbought ONLY when below EMA (downtrend).
    Gold has strong uptrend bias so long-only version also tested.
    """
    df = add_atr(df)
    df = add_rsi(df)
    df = add_ema(df, period=ema_period, name="ema_trend")

    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if pd.isna(row.get("rsi")) or pd.isna(row.get("ema_trend")) or pd.isna(row.get("atr")):
            continue

        # Buy: RSI crosses above oversold level while in uptrend
        if prev["rsi"] < rsi_buy and row["rsi"] >= rsi_buy and row["close"] > row["ema_trend"]:
            signals.iloc[i, 0] = 1

        # Sell: RSI crosses below overbought level while in downtrend
        elif prev["rsi"] > rsi_sell and row["rsi"] <= rsi_sell and row["close"] < row["ema_trend"]:
            signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 9: Momentum with Trailing Stop
# ═══════════════════════════════════════════════════════════════

def strategy_momentum_trail(df, mom_period=10, mom_threshold=1.0):
    """
    Enter when momentum (ROC) crosses threshold.
    - LONG: ROC > +threshold
    - SHORT: ROC < -threshold
    Relies heavily on trailing stop for exits (set in sim()).
    """
    df = add_atr(df)
    df = add_momentum(df, period=mom_period)

    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if pd.isna(row.get("momentum")) or pd.isna(row.get("atr")):
            continue
        if pd.isna(prev.get("momentum")):
            continue

        # Cross above threshold
        if prev["momentum"] <= mom_threshold and row["momentum"] > mom_threshold:
            signals.iloc[i, 0] = 1
        elif prev["momentum"] >= -mom_threshold and row["momentum"] < -mom_threshold:
            signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# STRATEGY 10: Dual EMA + Volume Breakout
# ═══════════════════════════════════════════════════════════════

def strategy_ema_volume_breakout(df, fast=9, slow=21, vol_mult=1.5):
    """
    EMA crossover only when volume confirms (volume > vol_mult * avg).
    This is the current BTC strategy adapted for gold.
    Add: require close strongly above/below both EMAs (body > 50% of ATR).
    """
    df = add_atr(df)
    df = add_ema(df, period=fast, name="ema_fast")
    df = add_ema(df, period=slow, name="ema_slow")
    df["vol_ma"] = df["volume"].rolling(20).mean()

    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(1, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        if pd.isna(row.get("ema_fast")) or pd.isna(row.get("ema_slow")) or pd.isna(row.get("atr")):
            continue
        if pd.isna(prev.get("ema_fast")) or pd.isna(prev.get("ema_slow")):
            continue

        # Volume check
        vol_ok = True
        if pd.notna(row.get("vol_ma")) and row["vol_ma"] > 0:
            vol_ok = row["volume"] > vol_mult * row["vol_ma"]

        if not vol_ok:
            continue

        # EMA crossover
        cross_up = prev["ema_fast"] <= prev["ema_slow"] and row["ema_fast"] > row["ema_slow"]
        cross_dn = prev["ema_fast"] >= prev["ema_slow"] and row["ema_fast"] < row["ema_slow"]

        # Body strength: close significantly on one side
        body = abs(row["close"] - row["open"])
        body_ok = body > 0.3 * row["atr"]

        if cross_up and body_ok:
            signals.iloc[i, 0] = 1
        elif cross_dn and body_ok:
            signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# RUN ALL STRATEGIES
# ═══════════════════════════════════════════════════════════════

def run_all():
    print("Loading XAU/USD 1H data...")
    df = load_gold_1h()
    print(f"  {len(df)} rows, {df.index[0]} to {df.index[-1]}")
    print(f"  Price: ${df['close'].iloc[0]:.0f} -> ${df['close'].iloc[-1]:.0f}")
    print()

    # ─── Strategy 1: Session Breakout ───────────────────────
    print("=" * 70)
    print("Strategy 1: Session Breakout")
    print("=" * 70)

    for session, lookback, bo_mult, sl, tp, trail in [
        (8, 4, 0.3, 1.5, 3.0, 0),      # London open, tight
        (8, 6, 0.5, 2.0, 4.0, 0),      # London open, wider
        (14, 4, 0.3, 1.5, 3.0, 0),     # NY open
        (8, 4, 0.3, 1.5, 0, 2.0),      # London + trail only (no TP)
    ]:
        signals = strategy_session_breakout(df, session_start=session,
                                             lookback_hours=lookback,
                                             breakout_atr_mult=bo_mult)
        label = f"S{session}h L{lookback} BO{bo_mult} SL{sl} TP{tp} TR{trail}"
        if trail > 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail,
                    hours_filter=(8, 20))
        else:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
        print_result("Session Breakout", label, r)

    # ─── Strategy 2: VWAP Reversion ─────────────────────────
    print()
    print("=" * 70)
    print("Strategy 2: VWAP Reversion")
    print("=" * 70)

    for dist, sl, tp in [
        (1.0, 1.0, 1.5),
        (1.5, 1.2, 2.0),
        (2.0, 1.5, 2.5),
        (1.0, 0.8, 1.2),
    ]:
        signals = strategy_vwap_reversion(df, distance_atr_mult=dist)
        label = f"Dist{dist} SL{sl} TP{tp}"
        r = sim(df, signals, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
        print_result("VWAP Reversion", label, r)

    # ─── Strategy 3: Bollinger Band Bounce ──────────────────
    print()
    print("=" * 70)
    print("Strategy 3: Bollinger Band Bounce")
    print("=" * 70)

    for bb_p, bb_s, rsi_f, sl, tp in [
        (20, 2.0, True, 1.0, 2.0),
        (20, 2.0, False, 1.5, 2.5),
        (30, 2.5, True, 1.2, 2.0),
        (20, 1.5, True, 1.0, 1.5),
    ]:
        signals = strategy_bb_bounce(df, bb_period=bb_p, bb_std=bb_s, rsi_filter=rsi_f)
        label = f"BB({bb_p},{bb_s}) RSI={'Y' if rsi_f else 'N'} SL{sl} TP{tp}"
        r = sim(df, signals, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
        print_result("BB Bounce", label, r)

    # ─── Strategy 4: Ichimoku Cloud ─────────────────────────
    print()
    print("=" * 70)
    print("Strategy 4: Ichimoku Cloud")
    print("=" * 70)

    for sl, tp, trail in [
        (2.0, 4.0, 0),
        (1.5, 3.0, 0),
        (2.0, 0, 2.5),   # trail only
        (1.5, 3.0, 2.0), # TP + trail
    ]:
        signals = strategy_ichimoku(df)
        label = f"SL{sl} TP{tp} TR{trail}"
        if trail > 0 and tp == 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail)
        elif trail > 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp, trail_mult=trail)
        else:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp)
        print_result("Ichimoku", label, r)

    # ─── Strategy 5: Pivot Point S/R ────────────────────────
    print()
    print("=" * 70)
    print("Strategy 5: Pivot Point S/R")
    print("=" * 70)

    for bounce, sl, tp in [
        (True, 1.0, 2.0),    # bounce
        (True, 1.5, 2.5),    # bounce wider
        (False, 1.5, 3.0),   # breakout
        (False, 2.0, 4.0),   # breakout wider
    ]:
        signals = strategy_pivot(df, bounce_mode=bounce)
        label = f"{'Bounce' if bounce else 'Breakout'} SL{sl} TP{tp}"
        r = sim(df, signals, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
        print_result("Pivot", label, r)

    # ─── Strategy 6: Asian Range Breakout ───────────────────
    print()
    print("=" * 70)
    print("Strategy 6: Asian Range Breakout")
    print("=" * 70)

    for sl, tp, trail in [
        (1.5, 3.0, 0),
        (1.0, 2.0, 0),
        (1.5, 0, 2.0),   # trail only
        (1.0, 2.5, 1.5), # TP + trail
    ]:
        signals = strategy_asian_range_breakout(df)
        label = f"SL{sl} TP{tp} TR{trail}"
        if trail > 0 and tp == 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail)
        elif trail > 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp, trail_mult=trail)
        else:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp)
        print_result("Asian Breakout", label, r)

    # ─── Strategy 7: EMA Ribbon ─────────────────────────────
    print()
    print("=" * 70)
    print("Strategy 7: EMA Ribbon")
    print("=" * 70)

    for periods, sl, tp, trail in [
        ((8, 13, 21, 34, 55), 2.0, 4.0, 0),
        ((5, 10, 20, 40), 1.5, 3.0, 0),
        ((8, 13, 21, 34, 55), 2.0, 0, 2.5),   # trail only
        ((8, 13, 21), 1.5, 3.0, 0),             # fewer EMAs = more signals
    ]:
        signals = strategy_ema_ribbon(df, periods=periods)
        pstr = "/".join(str(p) for p in periods)
        label = f"EMA({pstr}) SL{sl} TP{tp} TR{trail}"
        if trail > 0 and tp == 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail)
        else:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp, trail_mult=trail)
        print_result("EMA Ribbon", label, r)

    # ─── Strategy 8: RSI + Trend ────────────────────────────
    print()
    print("=" * 70)
    print("Strategy 8: RSI + Trend (Buy Oversold in Uptrend)")
    print("=" * 70)

    for ema_p, rsi_buy, rsi_sell, sl, tp in [
        (50, 30, 70, 1.5, 3.0),
        (100, 30, 70, 2.0, 4.0),
        (50, 35, 65, 1.2, 2.5),
        (200, 30, 70, 2.0, 5.0),
    ]:
        signals = strategy_rsi_trend(df, ema_period=ema_p, rsi_buy=rsi_buy, rsi_sell=rsi_sell)
        label = f"EMA{ema_p} RSI({rsi_buy}/{rsi_sell}) SL{sl} TP{tp}"
        r = sim(df, signals, sl_mult=sl, tp_mult=tp)
        print_result("RSI Trend", label, r)

    # ─── Strategy 9: Momentum + Trail ──────────────────────
    print()
    print("=" * 70)
    print("Strategy 9: Momentum + Trailing Stop")
    print("=" * 70)

    for mom_p, mom_t, sl, tp, trail in [
        (10, 1.0, 2.0, 4.0, 0),
        (10, 0.5, 1.5, 3.0, 0),
        (10, 1.0, 2.0, 0, 2.5),    # trail only
        (20, 1.5, 2.0, 5.0, 0),
        (10, 0.8, 1.5, 0, 2.0),    # trail, lower threshold
    ]:
        signals = strategy_momentum_trail(df, mom_period=mom_p, mom_threshold=mom_t)
        label = f"MOM({mom_p},{mom_t}) SL{sl} TP{tp} TR{trail}"
        if trail > 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail,
                    hours_filter=(8, 20))
        else:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
        print_result("Momentum", label, r)

    # ─── Strategy 10: EMA + Volume Breakout ─────────────────
    print()
    print("=" * 70)
    print("Strategy 10: Dual EMA + Volume Breakout")
    print("=" * 70)

    for fast, slow, vol, sl, tp, trail in [
        (9, 21, 1.5, 1.5, 3.0, 0),
        (9, 21, 1.0, 1.2, 2.5, 0),
        (5, 13, 1.5, 1.5, 3.0, 0),
        (9, 21, 1.5, 1.5, 0, 2.0),   # trail only
        (9, 21, 1.0, 1.5, 3.0, 1.8), # TP + trail
    ]:
        signals = strategy_ema_volume_breakout(df, fast=fast, slow=slow, vol_mult=vol)
        label = f"EMA({fast}/{slow}) Vol{vol}x SL{sl} TP{tp} TR{trail}"
        if trail > 0 and tp == 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail,
                    hours_filter=(8, 20))
        elif trail > 0:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp, trail_mult=trail,
                    hours_filter=(8, 20))
        else:
            r = sim(df, signals, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
        print_result("EMA+Vol", label, r)

    print()
    print("=" * 70)
    print("RESEARCH COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    run_all()
