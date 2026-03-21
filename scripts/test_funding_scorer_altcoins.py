"""Test funding scorer on altcoins with their own funding data."""
import json
import sys
import logging

logging.disable(logging.CRITICAL)

sys.path.insert(0, "/Users/iceai/Work/ccbt")
from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

with open("/Users/iceai/Work/ccbt/config.json") as f:
    base = json.load(f)

# Coins with both OHLCV (2y) and funding rate data
coins = [
    ("BTC",  "btcusdt",  {}),
    ("DOGE", "dogeusdt", {"atr_min": 0, "ema_slope_min": 0.01}),
    ("ARB",  "arbusdt",  {"atr_min": 0}),
    ("WIF",  "wifusdt",  {"atr_min": 0}),
    ("AVAX", "avaxusdt", {"atr_min": 0}),
    ("NEAR", "nearusdt", {"atr_min": 0}),
    ("XRP",  "xrpusdt",  {"atr_min": 0}),
    ("LINK", "linkusdt", {"atr_min": 0}),
    ("OP",   "opusdt",   {"atr_min": 0}),
    ("SUI",  "suiusdt",  {"atr_min": 0}),
    ("APT",  "aptusdt",  {"atr_min": 0}),
]

scorer_configs = [
    ("Scorer OFF", {"signal_scorer": {"enabled": False}}),
    ("Funding w=0.35 t=0.10", {
        "signal_scorer": {
            "enabled": True,
            "entry_threshold": 0.10,
            "high_conviction": 0.5,
            "weights": {
                "ema_alignment": 0.15,
                "momentum_mtf": 0.10,
                "volume_surge": 0.10,
                "rsi_zone": 0.10,
                "atr_regime": 0.05,
                "candle_strength": 0.15,
                "funding_rate": 0.35,
            },
        }
    }),
]

print(f"\n{'Coin':<6} {'Scorer':<25} {'Trades':>6} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Sharpe':>7} {'PnL%':>7}")
print("-" * 75)

for coin, prefix, coin_ov in coins:
    sig_path = f"/Users/iceai/Work/ccbt/data/{prefix}_15m_2y.csv"
    trend_path = f"/Users/iceai/Work/ccbt/data/{prefix}_1h_2y.csv"

    try:
        sig = load_ohlcv(sig_path)
        trend = load_ohlcv(trend_path)
    except FileNotFoundError as e:
        print(f"{coin:<6} {'(no OHLCV data)':<25}")
        continue

    for scorer_name, scorer_ov in scorer_configs:
        c = json.loads(json.dumps(base))
        # Set symbol so engine auto-picks the right funding file
        c["symbol"] = f"{prefix.upper().replace('usdt', '').upper()}USDT"
        for k, v in coin_ov.items():
            c[k] = v
        for k, v in scorer_ov.items():
            if isinstance(v, dict) and k in c and isinstance(c[k], dict):
                c[k] = {**c[k], **v}
            else:
                c[k] = v

        try:
            e = BacktestEngine(c, initial_balance=1000.0)
            m = e.run(sig, trend)
            tot = getattr(m, "total_trades", 0)
            wr = getattr(m, "win_rate", 0) * 100
            pf = getattr(m, "profit_factor", 0)
            dd = getattr(m, "max_drawdown_pct", 0) * 100
            sh = getattr(m, "sharpe_ratio", 0)
            pnl = getattr(m, "total_pnl_pct", 0) * 100
            print(f"{coin:<6} {scorer_name:<25} {tot:>6} {wr:>5.1f}% {pf:>6.2f} {dd:>5.1f}% {sh:>7.2f} {pnl:>6.1f}%")
        except Exception as ex:
            print(f"{coin:<6} {scorer_name:<25} ERROR: {ex}")

    print()
