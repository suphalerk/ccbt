"""Test funding rate enhanced signal scorer.

Compares baseline (scorer OFF) against:
  - OHLCV scoring only
  - OHLCV + funding rate at various thresholds and weight profiles
  - Funding-rate-only scoring (pure contrarian)
  - DOGE with funding scorer (uses btcusdt funding as proxy — DOGE file absent)
"""
import json
import sys

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

signal_df = load_ohlcv("data/btcusdt_15m_2y.csv")
trend_df = load_ohlcv("data/btcusdt_1h_2y.csv")

with open("/Users/iceai/Work/ccbt/config.json") as f:
    base = json.load(f)


def run(overrides: dict, signal_df_=signal_df, trend_df_=trend_df):
    c = json.loads(json.dumps(base))
    for k, v in overrides.items():
        if isinstance(v, dict) and k in c and isinstance(c[k], dict):
            c[k].update(v)
        else:
            c[k] = v
    e = BacktestEngine(c, initial_balance=1000.0)
    m = e.run(signal_df_, trend_df_)
    return m, e.state.trades


# ------------------------------------------------------------------
# BTC sweep
# ------------------------------------------------------------------
tests = {
    "Scorer OFF (baseline)": {},
    "OHLCV scoring only (t=0.10)": {
        "signal_scorer": {
            "enabled": True,
            "entry_threshold": 0.10,
            "high_conviction": 0.5,
            "weights": {
                "ema_alignment": 0.25,
                "momentum_mtf": 0.20,
                "volume_surge": 0.15,
                "rsi_zone": 0.15,
                "atr_regime": 0.10,
                "candle_strength": 0.15,
                "funding_rate": 0.0,   # disabled
            },
        }
    },
    "Funding+OHLCV (t=0.05)": {
        "signal_scorer": {
            "enabled": True,
            "entry_threshold": 0.05,
            "high_conviction": 0.4,
            "weights": {
                "ema_alignment": 0.20,
                "momentum_mtf": 0.15,
                "volume_surge": 0.12,
                "rsi_zone": 0.12,
                "atr_regime": 0.08,
                "candle_strength": 0.13,
                "funding_rate": 0.20,
            },
        }
    },
    "Funding+OHLCV (t=0.10)": {
        "signal_scorer": {
            "enabled": True,
            "entry_threshold": 0.10,
            "high_conviction": 0.5,
            "weights": {
                "ema_alignment": 0.20,
                "momentum_mtf": 0.15,
                "volume_surge": 0.12,
                "rsi_zone": 0.12,
                "atr_regime": 0.08,
                "candle_strength": 0.13,
                "funding_rate": 0.20,
            },
        }
    },
    "Funding+OHLCV (t=0.15)": {
        "signal_scorer": {
            "enabled": True,
            "entry_threshold": 0.15,
            "high_conviction": 0.5,
            "weights": {
                "ema_alignment": 0.20,
                "momentum_mtf": 0.15,
                "volume_surge": 0.12,
                "rsi_zone": 0.12,
                "atr_regime": 0.08,
                "candle_strength": 0.13,
                "funding_rate": 0.20,
            },
        }
    },
    "Funding heavy (w=0.35, t=0.10)": {
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
    },
    "Funding only (w=1.0, t=0.10)": {
        "signal_scorer": {
            "enabled": True,
            "entry_threshold": 0.10,
            "high_conviction": 0.5,
            "weights": {
                "ema_alignment": 0.0,
                "momentum_mtf": 0.0,
                "volume_surge": 0.0,
                "rsi_zone": 0.0,
                "atr_regime": 0.0,
                "candle_strength": 0.0,
                "funding_rate": 1.0,
            },
        }
    },
}

print(f"\n{'Config':<35} {'Trades':>7} {'WR%':>6} {'PF':>6} {'DD%':>6} {'Sharpe':>7}")
print("-" * 70)

for name, ov in tests.items():
    try:
        m, trades = run(ov)
        tot = getattr(m, "total_trades", 0)
        wr = getattr(m, "win_rate", 0) * 100
        pf = getattr(m, "profit_factor", 0)
        dd = getattr(m, "max_drawdown_pct", 0) * 100
        sh = getattr(m, "sharpe_ratio", 0)
        print(f"{name:<35} {tot:>7} {wr:>5.1f}% {pf:>6.2f} {dd:>5.1f}% {sh:>7.2f}")
    except Exception as exc:
        print(f"{name:<35} ERROR: {exc}")
        import traceback
        traceback.print_exc()


# ------------------------------------------------------------------
# DOGE with funding scorer
# Note: dogeusdt_funding_rate.csv may not exist; engine falls back silently.
# BTC funding rate will still be absent for DOGE (different file path).
# ------------------------------------------------------------------
print("\n--- DOGE with Funding Rate ---")
try:
    doge_sig = load_ohlcv("data/dogeusdt_15m_2y.csv")
    doge_trend = load_ohlcv("data/dogeusdt_1h_2y.csv")

    for name, scorer_cfg in [
        ("DOGE baseline", {}),
        (
            "DOGE + funding scorer",
            {
                "signal_scorer": {
                    "enabled": True,
                    "entry_threshold": 0.10,
                    "high_conviction": 0.5,
                    "weights": {
                        "ema_alignment": 0.20,
                        "momentum_mtf": 0.15,
                        "volume_surge": 0.12,
                        "rsi_zone": 0.12,
                        "atr_regime": 0.08,
                        "candle_strength": 0.13,
                        "funding_rate": 0.20,
                    },
                }
            },
        ),
    ]:
        c = json.loads(json.dumps(base))
        c["atr_min"] = 0
        c["ema_slope_min"] = 0.01
        c["symbol"] = "DOGEUSDT"
        for k, v in scorer_cfg.items():
            c[k] = v
        e = BacktestEngine(c, initial_balance=1000.0)
        m = e.run(doge_sig, doge_trend)
        tot = getattr(m, "total_trades", 0)
        pf = getattr(m, "profit_factor", 0)
        sh = getattr(m, "sharpe_ratio", 0)
        print(f"  {name}: {tot}t, PF {pf:.2f}, Sharpe {sh:.2f}")

except FileNotFoundError as exc:
    print(f"  DOGE data not found, skipping: {exc}")
