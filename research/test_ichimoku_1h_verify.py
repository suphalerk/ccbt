"""Verify Ichimoku 1H on altcoins with FULL backtest engine.

Runs the real BacktestEngine (not the lightweight simulator) to confirm
whether the PF figures from the mega sweep hold up under realistic
commission, slippage, regime filtering, and trailing-stop logic.
"""

import json
import os
import sys

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

with open("/Users/iceai/Work/ccbt/config.json") as f:
    base = json.load(f)


def make_ichimoku_config(symbol: str, sl: float, tp: float, risk: float = 0.03) -> dict:
    """Build an Ichimoku 1H config derived from base config.

    Disables all EMA/RSI/BB signals, enables only ichimoku_cloud.
    Uses same 1H data for both signal and trend timeframes — the Ichimoku
    cloud IS the trend filter, so a separate HTF trend pass is redundant.
    """
    c = json.loads(json.dumps(base))
    c["symbol"] = symbol
    c["timeframe_signal"] = "1h"
    c["timeframe_trend"] = "1h"
    c["risk_per_trade"] = risk
    c["leverage"] = 25
    c["atr_min"] = 0.0  # No ATR floor — cloud quality is the gate

    # Ichimoku standard params
    c["ichimoku_tenkan"] = 9
    c["ichimoku_kijun"] = 26
    c["ichimoku_senkou_b"] = 52

    # SL/TP from caller
    c["atr_sl_mult"] = sl
    c["atr_tp_mult"] = tp
    # Trailing stop proportional to SL width
    c["atr_trail_mult"] = sl * 1.5
    c["atr_trail_mult_trending"] = sl * 1.5
    c["atr_trail_mult_ranging"] = sl
    c["atr_trail_mult_volatile"] = sl * 2.0

    # Bypass R:R gate — cloud provides the quality signal
    c["min_rr_ratio"] = 0

    # Disable all other signals
    c["signals"] = {
        "ema_crossover": {"enabled": False},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": True},
    }

    # Trading hours — keep London+NY session (same as BTC default)
    c["trading_hours"] = {"enabled": True, "start_utc": 3, "end_utc": 20}

    # Regime filter — keep skip_ranging to avoid choppy markets
    c["regime_filter"] = {"enabled": True, "skip_ranging": True}

    # Disable all optional extras for clean signal isolation
    c["signal_scorer"] = {"enabled": False}
    c["adaptive_sizing"] = {"enabled": False}
    c["pyramiding"] = {"enabled": False}
    c["mtd_accelerator"] = {"enabled": False}
    c["partial_tp_enabled"] = False
    c["weekend_trading_enabled"] = False

    return c


# Coins to test — name, data file prefix
COINS = [
    ("BTC",  "btcusdt",  "btcusdt_1h_5y.csv"),
    ("SOL",  "solusdt",  "solusdt_1h_5y.csv"),
    ("AVAX", "avaxusdt", "avaxusdt_1h_2y.csv"),
    ("ARB",  "arbusdt",  "arbusdt_1h_2y.csv"),
    ("NEAR", "nearusdt", "nearusdt_1h_2y.csv"),
    ("WIF",  "wifusdt",  "wifusdt_1h_2y.csv"),
    ("XRP",  "xrpusdt",  "xrpusdt_1h_2y.csv"),
    ("LINK", "linkusdt", "linkusdt_1h_2y.csv"),
    ("OP",   "opusdt",   "opusdt_1h_2y.csv"),
    ("DOGE", "dogeusdt", "dogeusdt_1h_2y.csv"),
]

# SL/TP configurations to sweep
SL_TP_CONFIGS = [
    (2.0, 5.0, "SL2.0/TP5.0"),
    (2.5, 5.0, "SL2.5/TP5.0"),
    (1.5, 4.0, "SL1.5/TP4.0"),
    (2.0, 6.0, "SL2.0/TP6.0"),
]

DATA_DIR = "/Users/iceai/Work/ccbt/data"

print("=" * 80)
print("ICHIMOKU 1H — FULL ENGINE VERIFICATION")
print("=" * 80)
print(
    f"\n{'Coin':<6} {'SL/TP Config':<22} {'Trades':>6} {'Tr/yr':>6} "
    f"{'WR%':>6} {'PF':>6} {'DD%':>6} {'Sharpe':>7}"
)
print("-" * 75)

best_per_coin: dict = {}

for coin_name, prefix, filename in COINS:
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        print(f"{coin_name:<6} SKIP — no data: {filename}")
        continue

    try:
        sig_df = load_ohlcv(filepath)
    except Exception as ex:
        print(f"{coin_name:<6} SKIP — load error: {ex}")
        continue

    days = (sig_df.index[-1] - sig_df.index[0]).days
    yr = max(days / 365.25, 0.01)  # avoid division by zero

    for sl, tp, sltp_name in SL_TP_CONFIGS:
        symbol = f"{prefix.upper().replace('USDT', '')}/USDT:USDT"
        c = make_ichimoku_config(symbol, sl=sl, tp=tp, risk=0.03)

        try:
            engine = BacktestEngine(c, initial_balance=1000.0)
            # Pass 1H data as both signal and trend — Ichimoku IS the trend filter
            m = engine.run(sig_df, sig_df)

            tot = getattr(m, "total_trades", 0)
            wr = getattr(m, "win_rate", 0) * 100
            pf = getattr(m, "profit_factor", 0)
            # max_drawdown is already a fraction (e.g. 0.08 = 8%)
            dd = getattr(m, "max_drawdown", 0) * 100
            sh = getattr(m, "sharpe_ratio", 0)
            tr_yr = tot / yr if yr > 0 else 0

            print(
                f"{coin_name:<6} {sltp_name:<22} {tot:>6} {tr_yr:>5.0f} "
                f"{wr:>5.1f}% {pf:>6.2f} {dd:>5.1f}% {sh:>7.2f}"
            )

            if coin_name not in best_per_coin or pf > best_per_coin[coin_name]["pf"]:
                best_per_coin[coin_name] = {
                    "pf": pf,
                    "trades": tot,
                    "tr_yr": tr_yr,
                    "wr": wr,
                    "dd": dd,
                    "sh": sh,
                    "sl": sl,
                    "tp": tp,
                    "sltp": sltp_name,
                    "yr": yr,
                }

        except Exception as ex:
            print(f"{coin_name:<6} {sltp_name:<22} ERROR: {ex}")

    print()  # blank line between coins

# Summary table
print("\n" + "=" * 80)
print("BEST CONFIG PER COIN")
print("=" * 80)
print(
    f"\n{'Coin':<6} {'Best SL/TP':<22} {'Trades':>6} {'Tr/yr':>6} "
    f"{'WR%':>6} {'PF':>6} {'DD%':>6} {'Sharpe':>7}"
)
print("-" * 75)

profitable = []
for coin, r in sorted(best_per_coin.items(), key=lambda x: -x[1]["pf"]):
    flag = " <-- DEPLOY" if r["pf"] >= 1.15 else ""
    print(
        f"{coin:<6} {r['sltp']:<22} {r['trades']:>6} {r['tr_yr']:>5.0f} "
        f"{r['wr']:>5.1f}% {r['pf']:>6.2f} {r['dd']:>5.1f}% {r['sh']:>7.2f}{flag}"
    )
    if r["pf"] >= 1.15:
        profitable.append((coin, r))

print()
print(f"Coins with PF >= 1.15: {[c for c, _ in profitable]}")
total_tr_yr = sum(r["tr_yr"] for _, r in profitable)
print(f"Combined trades/year from profitable coins: {total_tr_yr:.0f}")
print(f"Trades per day (portfolio): {total_tr_yr / 365:.1f}")
