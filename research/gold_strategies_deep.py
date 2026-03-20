"""
Deep-dive on promising gold strategies.
Focus on: Ichimoku trail, Momentum, EMA+Vol trail, RSI+Trend
"""
import sys
sys.path.insert(0, "/Users/iceai/Work/ccbt")
from research.gold_strategies import *

def run_deep():
    print("Loading XAU/USD 1H data...")
    df = load_gold_1h()
    print(f"  {len(df)} rows, {df.index[0]} to {df.index[-1]}")
    print()

    # ═══ DEEP DIVE 1: Ichimoku + Trail (best so far: PF 1.37, 92/yr) ═══
    print("=" * 70)
    print("DEEP DIVE: Ichimoku Cloud + Trailing Stop")
    print("=" * 70)

    signals_ichi = strategy_ichimoku(df)
    sig_count = (signals_ichi["signal"] != 0).sum()
    print(f"  Raw signals: {sig_count}")

    for sl, trail in [
        (2.0, 2.0), (2.0, 2.5), (2.0, 3.0), (2.0, 3.5),
        (2.5, 2.5), (2.5, 3.0), (2.5, 3.5),
        (3.0, 2.5), (3.0, 3.0), (3.0, 3.5),
        (1.5, 2.0), (1.5, 2.5), (1.5, 3.0),
    ]:
        r = sim(df, signals_ichi, sl_mult=sl, tp_mult=999, trail_mult=trail)
        pf_mark = " ***" if r["pf"] >= 1.3 else (" **" if r["pf"] >= 1.2 else "")
        print_result("Ichi", f"SL{sl} TR{trail}", r)
        if pf_mark:
            print(f"    {pf_mark}")

    # With hours filter
    print("\n  --- With hours filter (8-20 UTC) ---")
    for sl, trail in [(2.0, 2.5), (2.0, 3.0), (2.5, 3.0), (3.0, 3.0)]:
        r = sim(df, signals_ichi, sl_mult=sl, tp_mult=999, trail_mult=trail,
                hours_filter=(8, 20))
        print_result("Ichi+Hrs", f"SL{sl} TR{trail}", r)

    # ═══ DEEP DIVE 2: Momentum (PF 1.14, 83/yr) ═══
    print()
    print("=" * 70)
    print("DEEP DIVE: Momentum with Higher Threshold")
    print("=" * 70)

    for mom_p, mom_t in [(10, 0.8), (10, 1.0), (10, 1.2), (10, 1.5),
                          (14, 1.0), (14, 1.2), (14, 1.5),
                          (7, 0.8), (7, 1.0), (7, 1.2)]:
        signals = strategy_momentum_trail(df, mom_period=mom_p, mom_threshold=mom_t)
        for sl, tp, trail in [(2.0, 4.0, 0), (2.0, 5.0, 0), (2.5, 5.0, 0),
                               (2.0, 999, 2.5), (2.5, 999, 3.0)]:
            label = f"MOM({mom_p},{mom_t}) SL{sl} TP{tp} TR{trail}"
            if trail > 0:
                r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail,
                        hours_filter=(8, 20))
            else:
                r = sim(df, signals, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
            if r["pf"] >= 1.1 and r["trades"] >= 30:
                print_result("MOM", label, r)

    # ═══ DEEP DIVE 3: EMA+Vol trail (PF 1.29, 53/yr) ═══
    print()
    print("=" * 70)
    print("DEEP DIVE: EMA + Volume + Trail")
    print("=" * 70)

    for fast, slow, vol in [(9, 21, 1.0), (9, 21, 1.5), (9, 21, 2.0),
                             (12, 26, 1.0), (12, 26, 1.5),
                             (7, 21, 1.0), (7, 21, 1.5)]:
        signals = strategy_ema_volume_breakout(df, fast=fast, slow=slow, vol_mult=vol)
        for sl, trail in [(1.5, 2.0), (2.0, 2.5), (2.0, 3.0), (2.5, 3.0),
                           (1.5, 2.5), (1.5, 3.0)]:
            label = f"EMA({fast}/{slow}) V{vol} SL{sl} TR{trail}"
            r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail,
                    hours_filter=(8, 20))
            if r["pf"] >= 1.1 and r["trades"] >= 20:
                print_result("EMA+V", label, r)

    # ═══ DEEP DIVE 4: RSI+Trend extended ═══
    print()
    print("=" * 70)
    print("DEEP DIVE: RSI + Trend (more generous RSI levels)")
    print("=" * 70)

    for ema_p, rsi_buy, rsi_sell in [
        (100, 25, 75), (100, 30, 70), (100, 35, 65), (100, 40, 60),
        (200, 25, 75), (200, 30, 70), (200, 35, 65), (200, 40, 60),
        (50, 25, 75), (50, 40, 60), (50, 45, 55),
    ]:
        signals = strategy_rsi_trend(df, ema_period=ema_p, rsi_buy=rsi_buy, rsi_sell=rsi_sell)
        sig_count = (signals["signal"] != 0).sum()
        if sig_count < 5:
            continue
        for sl, tp, trail in [(2.0, 4.0, 0), (2.0, 5.0, 0), (2.0, 999, 3.0), (1.5, 3.0, 0)]:
            label = f"EMA{ema_p} RSI({rsi_buy}/{rsi_sell}) SL{sl} TP{tp} TR{trail} [{sig_count}sig]"
            if trail > 0:
                r = sim(df, signals, sl_mult=sl, tp_mult=999, trail_mult=trail)
            else:
                r = sim(df, signals, sl_mult=sl, tp_mult=tp)
            if r["trades"] >= 5:
                print_result("RSI+T", label, r)

    # ═══ DEEP DIVE 5: Long-only versions (gold uptrend bias) ═══
    print()
    print("=" * 70)
    print("DEEP DIVE: Long-Only Versions of Top Strategies")
    print("=" * 70)

    # Ichimoku long-only
    signals_ichi_long = signals_ichi.copy()
    signals_ichi_long.loc[signals_ichi_long["signal"] == -1, "signal"] = 0
    for sl, trail in [(2.0, 2.5), (2.0, 3.0), (2.5, 3.0), (3.0, 3.5)]:
        r = sim(df, signals_ichi_long, sl_mult=sl, tp_mult=999, trail_mult=trail)
        print_result("Ichi LONG", f"SL{sl} TR{trail}", r)

    # Momentum long-only
    for mom_t in [0.8, 1.0, 1.2]:
        signals_mom = strategy_momentum_trail(df, mom_period=10, mom_threshold=mom_t)
        signals_mom_long = signals_mom.copy()
        signals_mom_long.loc[signals_mom_long["signal"] == -1, "signal"] = 0
        for sl, tp in [(2.0, 4.0), (2.0, 5.0), (2.5, 5.0)]:
            r = sim(df, signals_mom_long, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
            if r["pf"] >= 1.1 and r["trades"] >= 15:
                print_result("MOM LONG", f"T{mom_t} SL{sl} TP{tp}", r)

    # ═══ DEEP DIVE 6: Hybrid — Ichimoku direction + Momentum confirmation ═══
    print()
    print("=" * 70)
    print("DEEP DIVE: Hybrid Strategies")
    print("=" * 70)

    # Ichimoku direction + BB mean reversion for entry
    df_hybrid = df.copy()
    df_hybrid = add_atr(df_hybrid)
    df_hybrid = add_ichimoku(df_hybrid)
    df_hybrid = add_bb(df_hybrid, period=20, std_mult=2.0)
    df_hybrid = add_rsi(df_hybrid)

    signals_hybrid = pd.DataFrame(index=df_hybrid.index, data={"signal": 0})
    for i in range(52, len(df_hybrid)):
        row = df_hybrid.iloc[i]
        prev = df_hybrid.iloc[i-1]

        if any(pd.isna(row.get(c)) for c in ["tenkan", "kijun", "senkou_a", "senkou_b", "bb_lower", "rsi", "atr"]):
            continue

        cloud_top = max(row["senkou_a"], row["senkou_b"])
        cloud_bottom = min(row["senkou_a"], row["senkou_b"])

        # Ichimoku bullish bias + BB oversold
        if row["close"] > cloud_top and row["rsi"] < 40 and prev["low"] <= prev["bb_lower"]:
            signals_hybrid.iloc[i, 0] = 1

        # Ichimoku bearish bias + BB overbought
        elif row["close"] < cloud_bottom and row["rsi"] > 60 and prev["high"] >= prev["bb_upper"]:
            signals_hybrid.iloc[i, 0] = -1

    for sl, tp, trail in [(1.5, 3.0, 0), (2.0, 4.0, 0), (2.0, 999, 2.5), (1.5, 2.5, 0)]:
        label = f"Ichi+BB SL{sl} TP{tp} TR{trail}"
        if trail > 0:
            r = sim(df_hybrid, signals_hybrid, sl_mult=sl, tp_mult=999, trail_mult=trail)
        else:
            r = sim(df_hybrid, signals_hybrid, sl_mult=sl, tp_mult=tp)
        print_result("Hybrid", label, r)

    # Hybrid 2: Momentum entry + EMA trend filter
    df_h2 = df.copy()
    df_h2 = add_atr(df_h2)
    df_h2 = add_momentum(df_h2, period=10)
    df_h2 = add_ema(df_h2, period=50, name="ema_50")
    df_h2 = add_ema(df_h2, period=200, name="ema_200")

    signals_h2 = pd.DataFrame(index=df_h2.index, data={"signal": 0})
    for i in range(1, len(df_h2)):
        row = df_h2.iloc[i]
        prev = df_h2.iloc[i-1]

        if pd.isna(row.get("momentum")) or pd.isna(row.get("ema_200")) or pd.isna(row.get("atr")):
            continue
        if pd.isna(prev.get("momentum")):
            continue

        # Long: price > EMA200 + momentum crosses up
        if row["close"] > row["ema_200"] and prev["momentum"] <= 1.0 and row["momentum"] > 1.0:
            signals_h2.iloc[i, 0] = 1
        # Short: price < EMA200 + momentum crosses down
        elif row["close"] < row["ema_200"] and prev["momentum"] >= -1.0 and row["momentum"] < -1.0:
            signals_h2.iloc[i, 0] = -1

    for sl, tp, trail in [(2.0, 4.0, 0), (2.0, 5.0, 0), (2.0, 999, 2.5), (2.5, 999, 3.0)]:
        label = f"MOM+EMA200 SL{sl} TP{tp} TR{trail}"
        if trail > 0:
            r = sim(df_h2, signals_h2, sl_mult=sl, tp_mult=999, trail_mult=trail,
                    hours_filter=(8, 20))
        else:
            r = sim(df_h2, signals_h2, sl_mult=sl, tp_mult=tp, hours_filter=(8, 20))
        if r["trades"] >= 10:
            print_result("Hybrid2", label, r)

    # Hybrid 3: Session breakout + Ichimoku direction filter
    df_h3 = df.copy()
    df_h3 = add_atr(df_h3)
    df_h3 = add_ichimoku(df_h3)

    signals_h3 = pd.DataFrame(index=df_h3.index, data={"signal": 0})
    for i in range(52, len(df_h3)):
        ts = df_h3.index[i]
        if ts.hour != 8:
            continue

        row = df_h3.iloc[i]
        if any(pd.isna(row.get(c)) for c in ["senkou_a", "senkou_b", "atr"]):
            continue

        # Asian session range
        window = df_h3.iloc[max(0,i-8):i]
        range_high = window["high"].max()
        range_low = window["low"].min()
        atr = row["atr"]

        cloud_top = max(row["senkou_a"], row["senkou_b"])
        cloud_bottom = min(row["senkou_a"], row["senkou_b"])

        # Only break UP if above cloud, DOWN if below
        if row["close"] > range_high and row["close"] > cloud_top:
            signals_h3.iloc[i, 0] = 1
        elif row["close"] < range_low and row["close"] < cloud_bottom:
            signals_h3.iloc[i, 0] = -1

    for sl, tp, trail in [(2.0, 4.0, 0), (2.0, 999, 3.0), (2.5, 999, 3.0), (2.0, 5.0, 0)]:
        label = f"SessBO+Ichi SL{sl} TP{tp} TR{trail}"
        if trail > 0:
            r = sim(df_h3, signals_h3, sl_mult=sl, tp_mult=999, trail_mult=trail)
        else:
            r = sim(df_h3, signals_h3, sl_mult=sl, tp_mult=tp)
        if r["trades"] >= 5:
            print_result("Hybrid3", label, r)


    print()
    print("=" * 70)
    print("DEEP DIVE COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    run_deep()
