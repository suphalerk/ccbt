"""
PM Questions Deep Analysis — Gold Ichimoku Strategy
=====================================================
Q1: Long-only vs both directions
Q2: Chikou Span confirmation filter
Q3: Trailing stop vs fixed TP vs hybrid (partial close)
"""
import sys
sys.path.insert(0, "/Users/iceai/Work/ccbt")
from research.gold_strategies import *
from research.gold_strategies import _close, _summarize

# ═══════════════════════════════════════════════════════════════
# Enhanced simulator with partial TP support
# ═══════════════════════════════════════════════════════════════

def sim_partial(df, signals, sl_mult=2.5, tp1_mult=3.0, tp1_pct=0.3,
                trail_mult=3.0, risk_pct=0.02, initial_balance=10000.0,
                commission=COMMISSION, atr_col="atr",
                long_only=False, short_only=False):
    """
    Enhanced sim with partial TP:
    - Close tp1_pct at tp1_mult * ATR
    - Trail remaining (1 - tp1_pct) with trail_mult * ATR
    - After TP1 hit, move SL to breakeven + 0.3*ATR buffer

    If tp1_mult == 0: pure trail (no partial TP)
    If trail_mult == 0 and tp1_mult > 0: fixed TP only at tp1_mult * ATR (full close)

    PnL tracking: partial TP profit is stored in tp1_profit and added to the
    trade's total PnL at close so _summarize sees the complete picture.
    """
    balance = initial_balance
    peak_balance = initial_balance
    max_dd = 0.0
    trades = []
    position = None
    tp1_hit = False
    tp1_profit = 0.0  # track partial close profit to include in trade PnL
    remaining_pct = 1.0
    entry_atr = 0.0

    for i in range(1, len(df)):
        row = df.iloc[i]
        ts = df.index[i]

        # --- Check existing position ---
        if position is not None:
            high, low, close = row["high"], row["low"], row["close"]
            atr_now = row[atr_col] if pd.notna(row.get(atr_col)) else entry_atr

            if position.side == "long":
                # Check SL
                if low <= position.sl:
                    # Calculate remaining portion PnL manually
                    pnl_pct = (position.sl - position.entry_price) / position.entry_price
                    remaining_size = position.size * remaining_pct
                    exit_pnl = remaining_size * pnl_pct - remaining_size * commission
                    position.pnl = tp1_profit + exit_pnl
                    position.exit_price = position.sl
                    position.exit_time = ts
                    position.reason = "stop_loss" if not tp1_hit else "trail_after_tp1"
                    balance += exit_pnl  # tp1_profit already in balance
                    trades.append(position)
                    position = None
                    tp1_hit = False
                    tp1_profit = 0.0
                    remaining_pct = 1.0
                # Check TP1 (partial close)
                elif not tp1_hit and tp1_mult > 0 and high >= position.entry_price + tp1_mult * entry_atr:
                    if trail_mult > 0:
                        tp1_price = position.entry_price + tp1_mult * entry_atr
                        partial_pnl = position.size * tp1_pct * ((tp1_price - position.entry_price) / position.entry_price) - position.size * tp1_pct * commission
                        balance += partial_pnl
                        tp1_profit = partial_pnl
                        tp1_hit = True
                        remaining_pct = 1.0 - tp1_pct
                        position.sl = position.entry_price + 0.3 * entry_atr
                    else:
                        tp_price = position.entry_price + tp1_mult * entry_atr
                        _close(position, tp_price, ts, "take_profit", balance, commission)
                        balance += position.pnl
                        trades.append(position)
                        position = None
                        tp1_hit = False
                        tp1_profit = 0.0
                        remaining_pct = 1.0
                elif trail_mult > 0 and atr_now > 0:
                    new_sl = close - trail_mult * atr_now
                    if new_sl > position.sl:
                        position.sl = new_sl

            else:  # short
                if high >= position.sl:
                    pnl_pct = (position.entry_price - position.sl) / position.entry_price
                    remaining_size = position.size * remaining_pct
                    exit_pnl = remaining_size * pnl_pct - remaining_size * commission
                    position.pnl = tp1_profit + exit_pnl
                    position.exit_price = position.sl
                    position.exit_time = ts
                    position.reason = "stop_loss" if not tp1_hit else "trail_after_tp1"
                    balance += exit_pnl
                    trades.append(position)
                    position = None
                    tp1_hit = False
                    tp1_profit = 0.0
                    remaining_pct = 1.0
                elif not tp1_hit and tp1_mult > 0 and low <= position.entry_price - tp1_mult * entry_atr:
                    if trail_mult > 0:
                        tp1_price = position.entry_price - tp1_mult * entry_atr
                        partial_pnl = position.size * tp1_pct * ((position.entry_price - tp1_price) / position.entry_price) - position.size * tp1_pct * commission
                        balance += partial_pnl
                        tp1_profit = partial_pnl
                        tp1_hit = True
                        remaining_pct = 1.0 - tp1_pct
                        position.sl = position.entry_price - 0.3 * entry_atr
                    else:
                        tp_price = position.entry_price - tp1_mult * entry_atr
                        _close(position, tp_price, ts, "take_profit", balance, commission)
                        balance += position.pnl
                        trades.append(position)
                        position = None
                        tp1_hit = False
                        tp1_profit = 0.0
                        remaining_pct = 1.0
                elif trail_mult > 0 and atr_now > 0:
                    new_sl = close + trail_mult * atr_now
                    if new_sl < position.sl:
                        position.sl = new_sl

            # Drawdown
            if balance > peak_balance:
                peak_balance = balance
            dd = (peak_balance - balance) / peak_balance
            if dd > max_dd:
                max_dd = dd
            continue

        # --- New entry ---
        if i >= len(signals):
            continue
        sig = signals.iloc[i]
        if sig.get("signal", 0) == 0:
            continue

        if long_only and sig["signal"] == -1:
            continue
        if short_only and sig["signal"] == 1:
            continue

        if ts.dayofweek >= 5:
            continue

        atr = row[atr_col] if pd.notna(row.get(atr_col)) else 0
        if atr <= 0:
            continue

        entry_price = row["close"]
        side = "long" if sig["signal"] == 1 else "short"
        entry_atr = atr

        if side == "long":
            sl = entry_price - sl_mult * atr
            tp = entry_price + 20 * atr
        else:
            sl = entry_price + sl_mult * atr
            tp = entry_price - 20 * atr

        sl_dist = abs(entry_price - sl)
        if sl_dist <= 0:
            continue
        risk_amount = balance * risk_pct
        size = risk_amount / (sl_dist / entry_price)

        balance -= size * commission

        position = Trade(
            entry_time=ts, side=side, entry_price=entry_price,
            sl=sl, tp=tp, size=size
        )
        tp1_hit = False
        tp1_profit = 0.0
        remaining_pct = 1.0

    # Close remaining
    if position is not None:
        pnl_pct = (df.iloc[-1]["close"] - position.entry_price) / position.entry_price if position.side == "long" else (position.entry_price - df.iloc[-1]["close"]) / position.entry_price
        remaining_size = position.size * remaining_pct
        exit_pnl = remaining_size * pnl_pct - remaining_size * commission
        position.pnl = tp1_profit + exit_pnl
        position.exit_price = df.iloc[-1]["close"]
        position.exit_time = df.index[-1]
        position.reason = "end"
        balance += exit_pnl
        trades.append(position)

    return _summarize(trades, initial_balance, balance, max_dd, df)


# ═══════════════════════════════════════════════════════════════
# Ichimoku with Chikou Span confirmation
# ═══════════════════════════════════════════════════════════════

def strategy_ichimoku_chikou(df, require_chikou=True):
    """
    Ichimoku with optional Chikou Span confirmation:
    - LONG: TK cross up + price > cloud + Chikou > cloud (26 bars ago)
    - SHORT: TK cross down + price < cloud + Chikou < cloud (26 bars ago)
    """
    df = add_atr(df)
    df = add_ichimoku(df)
    signals = pd.DataFrame(index=df.index, data={"signal": 0})

    for i in range(27, len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1]

        for col in ["tenkan", "kijun", "senkou_a", "senkou_b", "atr"]:
            if pd.isna(row.get(col)) or pd.isna(prev.get(col)):
                break
        else:
            cloud_top = max(row["senkou_a"], row["senkou_b"])
            cloud_bottom = min(row["senkou_a"], row["senkou_b"])

            tk_cross_up = prev["tenkan"] <= prev["kijun"] and row["tenkan"] > row["kijun"]
            tk_cross_dn = prev["tenkan"] >= prev["kijun"] and row["tenkan"] < row["kijun"]

            if require_chikou and i >= 26:
                # Chikou is close shifted back 26 bars
                # So chikou at bar i is close[i] plotted at bar i-26
                # We check: is the current close > cloud at bar i-26?
                past_row = df.iloc[i - 26]
                if pd.isna(past_row.get("senkou_a")) or pd.isna(past_row.get("senkou_b")):
                    continue
                past_cloud_top = max(past_row["senkou_a"], past_row["senkou_b"])
                past_cloud_bottom = min(past_row["senkou_a"], past_row["senkou_b"])

                chikou_val = row["close"]  # current close = chikou plotted 26 bars back

                if tk_cross_up and row["close"] > cloud_top and chikou_val > past_cloud_top:
                    signals.iloc[i, 0] = 1
                elif tk_cross_dn and row["close"] < cloud_bottom and chikou_val < past_cloud_bottom:
                    signals.iloc[i, 0] = -1
            else:
                if tk_cross_up and row["close"] > cloud_top:
                    signals.iloc[i, 0] = 1
                elif tk_cross_dn and row["close"] < cloud_bottom:
                    signals.iloc[i, 0] = -1

    return signals


# ═══════════════════════════════════════════════════════════════
# Regime-split analysis helper
# ═══════════════════════════════════════════════════════════════

def split_by_regime(df, signals):
    """Split data into uptrend and downtrend periods using EMA200."""
    df_tmp = df.copy()
    df_tmp = add_ema(df_tmp, period=200, name="ema_200")

    # Uptrend: close > EMA200
    up_mask = df_tmp["close"] > df_tmp["ema_200"]
    dn_mask = df_tmp["close"] <= df_tmp["ema_200"]

    up_bars = up_mask.sum()
    dn_bars = dn_mask.sum()

    return up_bars, dn_bars, up_mask, dn_mask


def run_analysis():
    print("Loading XAU/USD 1H data...")
    df = load_gold_1h()
    print(f"  {len(df)} rows, {df.index[0]} to {df.index[-1]}")
    price_start = df['close'].iloc[0]
    price_end = df['close'].iloc[-1]
    print(f"  Price: ${price_start:.0f} -> ${price_end:.0f} ({(price_end/price_start-1)*100:.0f}%)")

    # Check regime split
    up_bars, dn_bars, up_mask, dn_mask = split_by_regime(df, None)
    print(f"  Bars above EMA200: {up_bars} ({up_bars/len(df)*100:.0f}%)")
    print(f"  Bars below EMA200: {dn_bars} ({dn_bars/len(df)*100:.0f}%)")
    print()

    # ═══════════════════════════════════════════════════════════════
    # Q1: LONG-ONLY vs BOTH DIRECTIONS
    # ═══════════════════════════════════════════════════════════════
    print("=" * 80)
    print("Q1: LONG-ONLY vs BOTH DIRECTIONS")
    print("=" * 80)
    print()

    signals_ichi = strategy_ichimoku(df)
    sig_long = (signals_ichi["signal"] == 1).sum()
    sig_short = (signals_ichi["signal"] == -1).sum()
    print(f"  Signal counts: {sig_long} long, {sig_short} short")
    print()

    # Baseline configs from PM's data
    configs = [
        ("SL2.5 TR3.0", 2.5, 3.0),
        ("SL2.0 TR2.5", 2.0, 2.5),
        ("SL2.0 TR3.0", 2.0, 3.0),
        ("SL3.0 TR3.0", 3.0, 3.0),
        ("SL3.0 TR3.5", 3.0, 3.5),
    ]

    print("  --- Both Directions ---")
    for label, sl, trail in configs:
        r = sim(df, signals_ichi, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("Both", f"{label}", r)

    print()
    print("  --- Long-Only ---")
    signals_long = signals_ichi.copy()
    signals_long.loc[signals_long["signal"] == -1, "signal"] = 0
    for label, sl, trail in configs:
        r = sim(df, signals_long, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("Long", f"{label}", r)

    print()
    print("  --- Short-Only (to see edge in shorts) ---")
    signals_short = signals_ichi.copy()
    signals_short.loc[signals_short["signal"] == 1, "signal"] = 0
    for label, sl, trail in configs:
        r = sim(df, signals_short, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("Short", f"{label}", r)

    # Now test: what if gold had a downtrend?
    # Use second half vs first half to see if there were any periods shorts worked
    print()
    print("  --- Period Analysis (check if shorts ever work) ---")
    mid = len(df) // 2
    # First half
    df_h1 = df.iloc[:mid].copy()
    sig_h1 = signals_ichi.iloc[:mid].copy()
    r1_both = sim(df_h1, sig_h1, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    r1_long = sim(df_h1, sig_h1[sig_h1["signal"] >= 0].reindex(df_h1.index, fill_value=0).fillna(0), sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    # Fix: create proper long-only signals for first half
    sig_h1_long = sig_h1.copy()
    sig_h1_long.loc[sig_h1_long["signal"] == -1, "signal"] = 0
    r1_long = sim(df_h1, sig_h1_long, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    print(f"  H1 ({df_h1.index[0].date()} to {df_h1.index[-1].date()}) ${df_h1['close'].iloc[0]:.0f}->${df_h1['close'].iloc[-1]:.0f}")
    print_result("  H1 Both", "SL2.5 TR3.0", r1_both)
    print_result("  H1 Long", "SL2.5 TR3.0", r1_long)

    df_h2 = df.iloc[mid:].copy()
    sig_h2 = signals_ichi.iloc[mid:].copy()
    r2_both = sim(df_h2, sig_h2, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    sig_h2_long = sig_h2.copy()
    sig_h2_long.loc[sig_h2_long["signal"] == -1, "signal"] = 0
    r2_long = sim(df_h2, sig_h2_long, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    print(f"  H2 ({df_h2.index[0].date()} to {df_h2.index[-1].date()}) ${df_h2['close'].iloc[0]:.0f}->${df_h2['close'].iloc[-1]:.0f}")
    print_result("  H2 Both", "SL2.5 TR3.0", r2_both)
    print_result("  H2 Long", "SL2.5 TR3.0", r2_long)

    # Quarterly analysis for shorts
    print()
    print("  --- Quarterly Short-Only PnL ---")
    quarters = df.resample('Q')
    for q_name, q_df in quarters:
        if len(q_df) < 100:
            continue
        q_sig = signals_short.reindex(q_df.index).fillna(0)
        # Ensure signal column exists
        q_sig_df = pd.DataFrame(index=q_df.index, data={"signal": 0})
        for idx in q_sig.index:
            if idx in signals_short.index:
                q_sig_df.loc[idx, "signal"] = signals_short.loc[idx, "signal"]
        r = sim(q_df, q_sig_df, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
        if r["trades"] > 0:
            pnl_sign = "+" if r["total_ret"] > 0 else ""
            print(f"  {q_name.strftime('%Y-Q%q') if hasattr(q_name, 'quarter') else str(q_name)[:7]}: "
                  f"{r['trades']}t, {pnl_sign}{r['total_ret']:.1f}%, PF {r['pf']:.2f}")

    # ═══════════════════════════════════════════════════════════════
    # Q2: CHIKOU SPAN CONFIRMATION
    # ═══════════════════════════════════════════════════════════════
    print()
    print()
    print("=" * 80)
    print("Q2: CHIKOU SPAN CONFIRMATION")
    print("=" * 80)
    print()

    signals_no_chikou = strategy_ichimoku_chikou(df, require_chikou=False)
    signals_with_chikou = strategy_ichimoku_chikou(df, require_chikou=True)

    nc_long = (signals_no_chikou["signal"] == 1).sum()
    nc_short = (signals_no_chikou["signal"] == -1).sum()
    wc_long = (signals_with_chikou["signal"] == 1).sum()
    wc_short = (signals_with_chikou["signal"] == -1).sum()

    print(f"  Without Chikou: {nc_long} long + {nc_short} short = {nc_long + nc_short} total")
    print(f"  With Chikou:    {wc_long} long + {wc_short} short = {wc_long + wc_short} total")
    print(f"  Filtered out:   {(nc_long+nc_short) - (wc_long+wc_short)} signals ({((nc_long+nc_short) - (wc_long+wc_short))/(nc_long+nc_short)*100:.0f}%)")
    print()

    print("  --- Without Chikou (Both) ---")
    for label, sl, trail in configs:
        r = sim(df, signals_no_chikou, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("NoChikou", f"{label}", r)

    print()
    print("  --- With Chikou (Both) ---")
    for label, sl, trail in configs:
        r = sim(df, signals_with_chikou, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("Chikou", f"{label}", r)

    print()
    print("  --- Without Chikou (Long-Only) ---")
    sig_nc_long = signals_no_chikou.copy()
    sig_nc_long.loc[sig_nc_long["signal"] == -1, "signal"] = 0
    for label, sl, trail in configs:
        r = sim(df, sig_nc_long, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("NoChikou-L", f"{label}", r)

    print()
    print("  --- With Chikou (Long-Only) ---")
    sig_wc_long = signals_with_chikou.copy()
    sig_wc_long.loc[sig_wc_long["signal"] == -1, "signal"] = 0
    for label, sl, trail in configs:
        r = sim(df, sig_wc_long, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("Chikou-L", f"{label}", r)

    # ═══════════════════════════════════════════════════════════════
    # Q3: TRAILING STOP vs FIXED TP vs HYBRID
    # ═══════════════════════════════════════════════════════════════
    print()
    print()
    print("=" * 80)
    print("Q3: TRAILING STOP vs FIXED TP vs HYBRID (PARTIAL CLOSE)")
    print("=" * 80)
    print()

    signals_best = strategy_ichimoku(df)

    # A) Pure fixed TP (no trail)
    print("  --- A) Fixed TP Only ---")
    for sl, tp in [(2.5, 3.0), (2.5, 4.0), (2.5, 5.0), (2.5, 6.0), (2.0, 3.0), (2.0, 4.0)]:
        r = sim(df, signals_best, sl_mult=sl, tp_mult=tp, trail_mult=0)
        print_result("FixedTP", f"SL{sl} TP{tp}", r)

    print()
    print("  --- B) Pure Trailing Stop (no TP) ---")
    for sl, trail in [(2.5, 2.5), (2.5, 3.0), (2.5, 3.5), (2.5, 4.0), (2.0, 2.5), (2.0, 3.0), (3.0, 3.0), (3.0, 3.5)]:
        r = sim(df, signals_best, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("Trail", f"SL{sl} TR{trail}", r)

    print()
    print("  --- C) Hybrid: Partial Close at TP1, Trail Rest ---")
    print("  (Close 30% at TP1, trail remaining 70%)")
    for sl, tp1, trail in [
        (2.5, 2.0, 3.0),  # conservative TP1
        (2.5, 3.0, 3.0),  # TP1 = trail distance
        (2.5, 3.0, 3.5),  # wide trail after TP1
        (2.5, 3.0, 4.0),  # very wide trail
        (2.5, 2.0, 3.5),  # early TP1 + wide trail
        (2.5, 2.0, 4.0),  # early TP1 + very wide trail
        (2.0, 2.0, 3.0),  # tighter SL
        (2.0, 3.0, 3.0),
        (2.0, 3.0, 3.5),
        (3.0, 3.0, 3.5),  # wider SL
        (3.0, 3.0, 4.0),
    ]:
        r = sim_partial(df, signals_best, sl_mult=sl, tp1_mult=tp1, tp1_pct=0.3, trail_mult=trail)
        print_result("Hybrid30%", f"SL{sl} TP1@{tp1} TR{trail}", r)

    print()
    print("  --- D) Hybrid: 50% Partial Close ---")
    for sl, tp1, trail in [
        (2.5, 2.0, 3.0),
        (2.5, 3.0, 3.5),
        (2.5, 2.0, 3.5),
        (2.5, 3.0, 4.0),
    ]:
        r = sim_partial(df, signals_best, sl_mult=sl, tp1_mult=tp1, tp1_pct=0.5, trail_mult=trail)
        print_result("Hybrid50%", f"SL{sl} TP1@{tp1} TR{trail}", r)

    print()
    print("  --- E) Hybrid Long-Only: 30% Partial Close ---")
    for sl, tp1, trail in [
        (2.5, 2.0, 3.0),
        (2.5, 3.0, 3.0),
        (2.5, 3.0, 3.5),
        (2.5, 2.0, 3.5),
        (2.5, 3.0, 4.0),
    ]:
        r = sim_partial(df, signals_best, sl_mult=sl, tp1_mult=tp1, tp1_pct=0.3,
                        trail_mult=trail, long_only=True)
        print_result("HybL30%", f"SL{sl} TP1@{tp1} TR{trail}", r)

    # ═══════════════════════════════════════════════════════════════
    # SUMMARY: Best config comparison
    # ═══════════════════════════════════════════════════════════════
    print()
    print()
    print("=" * 80)
    print("FINAL COMPARISON: BEST FROM EACH CATEGORY")
    print("=" * 80)
    print()

    # Both directions, no chikou, pure trail
    r1 = sim(df, signals_ichi, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    print_result("BOTH+Trail", "SL2.5 TR3.0", r1)

    # Long-only, no chikou, pure trail
    r2 = sim(df, signals_long, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    print_result("LONG+Trail", "SL2.5 TR3.0", r2)

    # Both + chikou, pure trail
    r3 = sim(df, signals_with_chikou, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    print_result("BOTH+Chikou+Trail", "SL2.5 TR3.0", r3)

    # Long + chikou, pure trail
    r4 = sim(df, sig_wc_long, sl_mult=2.5, tp_mult=999, trail_mult=3.0)
    print_result("LONG+Chikou+Trail", "SL2.5 TR3.0", r4)

    # Both, hybrid 30%
    r5 = sim_partial(df, signals_ichi, sl_mult=2.5, tp1_mult=3.0, tp1_pct=0.3, trail_mult=3.5)
    print_result("BOTH+Hybrid30%", "SL2.5 TP1@3 TR3.5", r5)

    # Long, hybrid 30%
    r6 = sim_partial(df, signals_long, sl_mult=2.5, tp1_mult=3.0, tp1_pct=0.3, trail_mult=3.5)
    print_result("LONG+Hybrid30%", "SL2.5 TP1@3 TR3.5", r6)

    # Long + chikou + hybrid
    r7 = sim_partial(df, sig_wc_long, sl_mult=2.5, tp1_mult=3.0, tp1_pct=0.3, trail_mult=3.5)
    print_result("LONG+Chikou+Hyb30%", "SL2.5 TP1@3 TR3.5", r7)

    print()
    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    run_analysis()
