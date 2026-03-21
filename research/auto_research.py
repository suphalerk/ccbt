#!/usr/bin/env python3
"""Auto-research pipeline: sweep → verify → report → generate configs.

Runs the full research pipeline using the real BacktestEngine for all steps,
eliminating the sweep/verify discrepancy that occurred with lightweight simulators.

Usage:
    python3 research/auto_research.py --strategy dual_supertrend --timeframe 4h
    python3 research/auto_research.py --strategy ichimoku --timeframe 1h --sweep-only
    python3 research/auto_research.py --all-strategies
    python3 research/auto_research.py --verify-only --input data/sweep_results.json
    python3 research/auto_research.py --generate-configs --input data/verified_winners.json
    python3 research/auto_research.py --strategy vol_expansion --compare-deployed
    python3 research/auto_research.py --strategy dual_supertrend --coins btcusdt,ethusdt,xrpusdt
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import logging
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES: Dict[str, dict] = {
    # Production strategies (already in engine)
    "ema_crossover": {
        "signal_key": "ema_crossover",
        "default_tf": "15m",
        "sl": 1.0,
        "tp": 3.0,
        "trail": 2.0,
        "config_suffix": "ema",
        "description": "EMA(9/21) crossover 15m with RSI + trend filter",
    },
    "ichimoku": {
        "signal_key": "ichimoku_cloud",
        "default_tf": "1h",
        "sl": 2.0,
        "tp": 5.0,
        "trail": 3.0,
        "config_suffix": "ichi",
        "description": "Ichimoku Cloud Tenkan/Kijun cross + cloud filter",
    },
    "ichimoku_4h": {
        "signal_key": "ichimoku_cloud",
        "default_tf": "4h",
        "sl": 2.5,
        "tp": 4.0,
        "trail": 3.0,
        "config_suffix": "ichi4h",
        "description": "Ichimoku Cloud 4H resampled — large-cap trending coins",
    },
    "ichimoku_4h_trail": {
        "signal_key": "ichimoku_cloud",
        "default_tf": "4h",
        "sl": 1.5,
        "tp": 0.0,
        "trail": 4.0,
        "config_suffix": "ichi4htrail",
        "description": "Ichimoku Cloud 4H with trailing stop only (no fixed TP)",
    },
    "supertrend": {
        "signal_key": "supertrend",
        "default_tf": "1h",
        "sl": 2.0,
        "tp": 3.0,
        "trail": 2.5,
        "config_suffix": "supertrend",
        "description": "ATR-adaptive Supertrend bands 1H",
    },
    "vol_expansion": {
        "signal_key": "vol_expansion",
        "default_tf": "1h",
        "sl": 2.5,
        "tp": 3.0,
        "trail": 2.5,
        "config_suffix": "volexp",
        "description": "Vol Expansion Breakout: ATR > rolling_mean×threshold + price breakout",
    },
    # New strategies (Round 7)
    "dual_supertrend": {
        "signal_key": "dual_supertrend",
        "default_tf": "4h",
        "sl": 2.5,
        "tp": 4.0,
        "trail": 3.0,
        "config_suffix": "dualst",
        "description": "Dual Supertrend: fast (10,1.0) × slow (20,3.0) agreement",
    },
    "alligator": {
        "signal_key": "alligator",
        "default_tf": "4h",
        "sl": 2.0,
        "tp": 4.0,
        "trail": 3.0,
        "config_suffix": "alligator",
        "description": "Williams Alligator: Jaw/Teeth/Lips ordered + spread expanding",
    },
    "ema_ichimoku_hybrid": {
        "signal_key": "ema_ichimoku_hybrid",
        "default_tf": "4h",
        "sl": 1.5,
        "tp": 4.0,
        "trail": 2.0,
        "config_suffix": "emaichi",
        "description": "EMA crossover inside Ichimoku cloud area — confluence entry",
    },
    "ichi_supertrend": {
        "signal_key": "ichi_supertrend",
        "default_tf": "4h",
        "sl": 2.0,
        "tp": 5.0,
        "trail": 3.0,
        "config_suffix": "ichist",
        "description": "Ichimoku cloud direction + Supertrend agreement",
    },
    "volexp_supertrend": {
        "signal_key": "volexp_supertrend",
        "default_tf": "1h",
        "sl": 2.5,
        "tp": 4.0,
        "trail": 3.0,
        "config_suffix": "volexpst",
        "description": "Vol Expansion Breakout + Supertrend direction confirmation",
    },
}

# Deployed coins — used for compare-deployed logic (excludes from new-coin discovery)
DEPLOYED_PREFIXES: frozenset = frozenset({
    "btcusdt", "dogeusdt", "arbusdt", "wifusdt",
    "avaxusdt", "nearusdt", "solusdt",
    "gunusdt", "berausdt", "athusdt", "zetausdt",
    "arcusdt", "animeusdt", "trumpusdt", "injusdt",
    "xlmusdt", "1000shibusdt", "trxusdt", "taousdt",
    "renderusdt", "hbarusdt", "polusdt", "polyxusdt",
    "fetusdt", "algousdt", "mstrusdt", "xagusdt", "saharausdt",
    "1000pepeusdt", "wldusdt",
})

# ---------------------------------------------------------------------------
# Base config template — all signals disabled; we flip one on per sweep
# ---------------------------------------------------------------------------

_BASE_CONFIG: dict = {
    "exchange": "binance",
    "symbol": "BTCUSDT",
    "timeframe_signal": "1h",
    "timeframe_trend": "1h",
    "leverage": 25,
    "risk_per_trade": 0.01,
    "max_daily_loss": 0.3,
    "max_positions": 2,
    "max_consecutive_losses": 5,
    "cooldown_hours": 1,
    "max_api_errors": 3,
    "use_testnet": True,
    "ema_fast": 9,
    "ema_slow": 21,
    "ema_trend": 50,
    "ema_fast2": 5,
    "ema_slow2": 13,
    "rsi_period": 14,
    "rsi_min": 45,
    "rsi_max": 65,
    "rsi_long_min": 45,
    "rsi_long_max": 65,
    "rsi_short_min": 35,
    "rsi_short_max": 55,
    "atr_period": 14,
    "atr_min": 0.0,
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 3.0,
    "atr_trail_mult": 3.0,
    "atr_trail_mult_trending": 3.0,
    "atr_trail_mult_ranging": 2.0,
    "atr_trail_mult_volatile": 4.0,
    "partial_tp_enabled": False,
    "partial_tp_pct": 0.3,
    "partial_tp_atr_mult": 2.0,
    "move_sl_to_be_after_tp1": False,
    "breakeven_buffer_atr_mult": 0.5,
    "atr_trail_mult_post_tp1": 4.0,
    "volume_mult": 1.0,
    "volume_max_mult": None,
    "cooldown_candles_after_close": 0,
    "cooldown_candles_after_sl": 0,
    "min_rr_ratio": 0,
    "commission_rate": 0.0004,
    "slippage_rate": 0.00015,
    "crossover_lookback": 2,
    "weekend_trading_enabled": False,
    "weekend_size_reduction": 0.5,
    "ema_slope_period": 5,
    "ema_slope_min": 0.02,
    "ichimoku_tenkan": 9,
    "ichimoku_kijun": 26,
    "ichimoku_senkou_b": 52,
    "supertrend_multiplier": 2.0,
    "vol_expansion_threshold": 1.8,
    "vol_expansion_lookback": 1,
    "vol_expansion_atr_ma_period": 20,
    "vol_expansion_ema_trend_period": 50,
    "body_dominance_min_body": 0.65,
    "body_dominance_min_mom": 0.02,
    "body_dominance_min_vol": 1.5,
    "squeeze_release_low": 0.7,
    "squeeze_release_high": 0.8,
    "squeeze_release_min_mom4": 0.0,
    "mr_rsi_oversold": 30,
    "mr_rsi_overbought": 70,
    "mr_atr_sl_mult": 1.0,
    "mr_atr_tp_mult": 1.5,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 0.3,
    "bb_volume_mult": 1.2,
    "swing_lookback": 5,
    "divergence_lookback": 20,
    "trading_hours": {
        "enabled": True,
        "start_utc": 3,
        "end_utc": 20,
    },
    "regime_filter": {
        "enabled": True,
        "skip_ranging": True,
    },
    "flexible_cooldown": {
        "enabled": False,
        "min_quality_score": 0.7,
        "cooldown_reduction_factor": 0.5,
        "log_overrides": True,
    },
    # All signals disabled by default
    "signals": {
        "ema_crossover": {"enabled": False},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": False},
        "supertrend": {"enabled": False},
        "vol_expansion": {"enabled": False},
        "dual_supertrend": {"enabled": False},
        "alligator": {"enabled": False},
        "ema_ichimoku_hybrid": {"enabled": False},
        "ichi_supertrend": {"enabled": False},
        "volexp_supertrend": {"enabled": False},
    },
    "adaptive_sizing": {"enabled": False},
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "signal_scorer": {"enabled": False},
    "ai_layer": {"enabled": False},
}


# ---------------------------------------------------------------------------
# Coin / data discovery
# ---------------------------------------------------------------------------

def discover_coins(
    data_dir: Path = DATA_DIR,
    timeframe: str = "1h",
    exclude_deployed: bool = False,
) -> List[str]:
    """Scan data/ for all coin prefixes with available OHLCV data.

    Looks for files matching {prefix}_{timeframe}_2y.csv or {prefix}_{timeframe}_5y.csv.
    When timeframe is '4h', falls back to 1h files (will be resampled at runtime).

    Args:
        data_dir: Directory containing OHLCV CSV files.
        timeframe: Target timeframe ('15m', '1h', '4h').
        exclude_deployed: If True, exclude already-deployed coin prefixes.

    Returns:
        Sorted list of coin prefixes (e.g. ['ethusdt', 'bnbusdt']).
    """
    # For 4h strategy, we use 1h data and resample — look for 1h files
    scan_tf = "1h" if timeframe == "4h" else timeframe

    prefixes: List[str] = []
    for period in ["2y", "5y"]:
        pattern = str(data_dir / f"*_{scan_tf}_{period}.csv")
        for fpath in sorted(glob.glob(pattern)):
            base = Path(fpath).name
            prefix = base.replace(f"_{scan_tf}_{period}.csv", "")
            if prefix not in prefixes:
                prefixes.append(prefix)

    if exclude_deployed:
        prefixes = [p for p in prefixes if p not in DEPLOYED_PREFIXES]

    return sorted(prefixes)


def get_deployed_coins() -> Dict[str, str]:
    """Scan all config*.json files and return {symbol_prefix: config_path}.

    Args: (none)

    Returns:
        Dict mapping lowercase prefix (e.g. 'btcusdt') to config file path.
    """
    deployed: Dict[str, str] = {}
    for fpath in sorted(glob.glob(str(PROJECT_ROOT / "config_*.json"))):
        try:
            with open(fpath) as f:
                cfg = json.load(f)
            symbol = cfg.get("symbol", "")
            if symbol:
                deployed[symbol.lower()] = fpath
        except Exception:
            pass
    return deployed


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_coin_data(
    prefix: str,
    timeframe: str,
    data_dir: Path = DATA_DIR,
) -> Optional[pd.DataFrame]:
    """Load OHLCV data for a coin prefix, resampling to 4H if needed.

    Args:
        prefix: Coin prefix (e.g. 'ethusdt').
        timeframe: Target timeframe. '4h' triggers resampling from 1h data.
        data_dir: Directory containing CSV files.

    Returns:
        OHLCV DataFrame or None if no file found.
    """
    # Determine which base files to look for
    scan_tf = "1h" if timeframe == "4h" else timeframe

    df: Optional[pd.DataFrame] = None
    for period in ["2y", "5y"]:
        fpath = data_dir / f"{prefix}_{scan_tf}_{period}.csv"
        if fpath.exists():
            try:
                df = load_ohlcv(str(fpath))
                break
            except Exception as e:
                print(f"  WARN: could not load {fpath}: {e}")

    if df is None:
        return None

    if timeframe == "4h":
        df = df.resample("4h").agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna(subset=["close"])

    return df


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def _build_config(
    strategy_name: str,
    prefix: str,
    timeframe: str,
    sl: float,
    tp: float,
    trail: float,
    extra_params: Optional[dict] = None,
) -> dict:
    """Build a BacktestEngine config for the given strategy and coin.

    Args:
        strategy_name: Key in STRATEGIES registry.
        prefix: Coin prefix (e.g. 'ethusdt').
        timeframe: Signal timeframe ('15m', '1h', '4h').
        sl: ATR stop-loss multiplier.
        tp: ATR take-profit multiplier (0 = trailing only).
        trail: ATR trailing stop multiplier.
        extra_params: Optional dict of additional config overrides.

    Returns:
        Complete config dict ready for BacktestEngine.
    """
    strat = STRATEGIES[strategy_name]
    signal_key = strat["signal_key"]
    symbol = prefix.upper()

    cfg = copy.deepcopy(_BASE_CONFIG)
    cfg["symbol"] = symbol
    cfg["timeframe_signal"] = timeframe
    cfg["timeframe_trend"] = timeframe
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    # For trailing-only mode (tp=0) — keep all trail_mult values consistent
    if tp == 0.0:
        cfg["atr_trail_mult_ranging"] = trail
        cfg["atr_trail_mult_volatile"] = trail

    # Enable only the target signal
    cfg["signals"] = copy.deepcopy(_BASE_CONFIG["signals"])
    if signal_key in cfg["signals"]:
        cfg["signals"][signal_key] = {"enabled": True}
    else:
        # Signal key not in default set — add it
        cfg["signals"][signal_key] = {"enabled": True}

    # Apply extra params
    if extra_params:
        for k, v in extra_params.items():
            cfg[k] = v

    return cfg


# ---------------------------------------------------------------------------
# Single-coin engine runner
# ---------------------------------------------------------------------------

def _run_engine_single(
    config: dict,
    signal_data: pd.DataFrame,
    balance: float = 10000.0,
) -> Optional[dict]:
    """Run BacktestEngine on one coin and return a compact metrics dict.

    Args:
        config: Full engine config.
        signal_data: OHLCV DataFrame for the signal timeframe.
        balance: Starting balance in USDT.

    Returns:
        Dict with pf, wr, trades, sharpe, dd, and trade_count_yr; or None on error.
    """
    try:
        engine = BacktestEngine(config, initial_balance=balance)
        metrics = engine.run(signal_data)

        # Estimate trades per year from the dataset span
        days = (signal_data.index[-1] - signal_data.index[0]).days
        yr = max(days / 365.25, 0.01)
        trades_yr = metrics.total_trades / yr

        return {
            "pf": round(metrics.profit_factor, 3),
            "wr": round(metrics.win_rate * 100, 1),
            "trades": metrics.total_trades,
            "trades_yr": round(trades_yr, 1),
            "sharpe": round(metrics.sharpe_ratio, 2),
            "dd": round(metrics.max_drawdown * 100, 2),
        }
    except Exception as e:
        logging.debug("engine_error coin=%s error=%s", config.get("symbol"), e)
        return None


# ---------------------------------------------------------------------------
# Core sweep function
# ---------------------------------------------------------------------------

def sweep_strategy(
    strategy_name: str,
    timeframe: Optional[str] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    trail: Optional[float] = None,
    coins: Optional[List[str]] = None,
    balance: float = 10000.0,
    min_pf: float = 1.0,
    min_trades: int = 4,
    extra_params: Optional[dict] = None,
) -> List[dict]:
    """Run BacktestEngine sweep for one strategy across all (or specified) coins.

    Uses the real BacktestEngine — no lightweight simulator. This ensures
    sweep results are directly usable for deployment without separate verification.

    Args:
        strategy_name: Key in STRATEGIES registry.
        timeframe: Override default timeframe (e.g. '4h'). Uses strategy default if None.
        sl: Override ATR SL multiplier.
        tp: Override ATR TP multiplier (0 = trailing only).
        trail: Override ATR trail multiplier.
        coins: Explicit list of coin prefixes. Discovers all if None.
        balance: Starting balance in USDT for the engine.
        min_pf: Minimum profit factor to include in output.
        min_trades: Minimum total trades to include in output.
        extra_params: Additional config overrides passed to the engine.

    Returns:
        List of result dicts sorted by PF descending, filtered by min_pf/min_trades.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Available: {', '.join(sorted(STRATEGIES))}"
        )

    strat = STRATEGIES[strategy_name]
    tf = timeframe or strat["default_tf"]
    sl_v = sl if sl is not None else strat["sl"]
    tp_v = tp if tp is not None else strat["tp"]
    trail_v = trail if trail is not None else strat["trail"]

    if coins is None:
        coins = discover_coins(DATA_DIR, tf)

    print(f"\nSweeping strategy '{strategy_name}' [{tf}] across {len(coins)} coin(s)")
    print(f"  SL={sl_v}×ATR  TP={tp_v}×ATR  Trail={trail_v}×ATR")
    print(f"  Filter: PF >= {min_pf}, trades >= {min_trades}")
    print(f"  Engine: BacktestEngine (real, not lightweight)\n")

    hdr = (
        f"{'Coin':<12} {'TF':<4} {'Trades':>7} {'Tr/yr':>6} "
        f"{'WR%':>6} {'PF':>7} {'DD%':>6} {'Sharpe':>7}"
    )
    print(hdr)
    print("-" * len(hdr))

    results: List[dict] = []

    for i, prefix in enumerate(coins):
        df = _load_coin_data(prefix, tf)
        if df is None or len(df) < 200:
            continue

        coin_name = prefix.replace("usdt", "").upper()
        cfg = _build_config(
            strategy_name=strategy_name,
            prefix=prefix,
            timeframe=tf,
            sl=sl_v,
            tp=tp_v,
            trail=trail_v,
            extra_params=extra_params,
        )

        r = _run_engine_single(cfg, df, balance=balance)
        if r is None:
            continue

        is_winner = r["pf"] >= min_pf and r["trades"] >= min_trades
        marker = " *" if is_winner else ""

        if r["trades"] >= 3:
            print(
                f"{coin_name:<12} {tf:<4} {r['trades']:>7} {r['trades_yr']:>5.0f}/yr"
                f" {r['wr']:>5.1f}% {r['pf']:>7.2f} {r['dd']:>5.1f}% {r['sharpe']:>7.2f}{marker}"
            )

        if is_winner:
            results.append({
                "coin": coin_name,
                "prefix": prefix,
                "strategy": strategy_name,
                "timeframe": tf,
                "pf": r["pf"],
                "wr_pct": r["wr"],
                "trades": r["trades"],
                "trades_yr": r["trades_yr"],
                "sharpe": r["sharpe"],
                "dd_pct": r["dd"],
                "params": {
                    "sl_mult": sl_v,
                    "tp_mult": tp_v,
                    "trail_mult": trail_v,
                },
            })

        if (i + 1) % 10 == 0:
            print(f"  ... processed {i + 1}/{len(coins)} coins ...")

    results.sort(key=lambda x: -x["pf"])
    print(f"\nSweep complete: {len(results)} winner(s) from {len(coins)} coin(s)")
    return results


# ---------------------------------------------------------------------------
# Verify / filter winners
# ---------------------------------------------------------------------------

def verify_winners(
    sweep_results: List[dict],
    min_pf: float = 1.3,
    min_trades: int = 6,
) -> List[dict]:
    """Apply stricter filters to sweep results.

    Since sweep_strategy uses the real BacktestEngine, these results are already
    engine-verified. This step just applies tighter deployment thresholds.

    Args:
        sweep_results: Output from sweep_strategy().
        min_pf: Stricter minimum profit factor for deployment readiness.
        min_trades: Stricter minimum trade count.

    Returns:
        Filtered list sorted by PF descending.
    """
    verified = [
        r for r in sweep_results
        if r["pf"] >= min_pf and r["trades"] >= min_trades
    ]
    verified.sort(key=lambda x: -x["pf"])
    print(f"\nVerify filter: {len(sweep_results)} → {len(verified)} winners "
          f"(PF >= {min_pf}, trades >= {min_trades})")
    return verified


# ---------------------------------------------------------------------------
# Compare against deployed portfolio
# ---------------------------------------------------------------------------

def compare_deployed(
    verified_results: List[dict],
    deployed_coins: Dict[str, str],
) -> List[dict]:
    """Compare verified results against the currently deployed portfolio.

    Tags each result as 'NEW' (not deployed), 'UPGRADE' (better PF than current),
    or 'EXISTING' (already deployed with similar/better PF).

    Args:
        verified_results: Output from verify_winners().
        deployed_coins: Output from get_deployed_coins() — {prefix: config_path}.

    Returns:
        Annotated list of results with 'deploy_action' and 'current_pf' keys.
    """
    annotated: List[dict] = []

    # Load current PF from each deployed config if recorded in _comment
    current_pf_map: Dict[str, float] = {}
    for prefix, cfg_path in deployed_coins.items():
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            comment = cfg.get("_comment", "")
            # Try to parse PF from comment like "PF 1.85"
            import re
            m = re.search(r"PF\s+([\d.]+)", comment)
            if m:
                current_pf_map[prefix] = float(m.group(1))
        except Exception:
            pass

    for r in verified_results:
        prefix = r["prefix"]
        r = dict(r)

        if prefix not in deployed_coins:
            r["deploy_action"] = "NEW"
            r["current_pf"] = None
        else:
            current_pf = current_pf_map.get(prefix, 0.0)
            r["current_pf"] = current_pf
            if r["pf"] > current_pf * 1.1:  # At least 10% better
                r["deploy_action"] = "UPGRADE"
            else:
                r["deploy_action"] = "EXISTING"

        annotated.append(r)

    return annotated


# ---------------------------------------------------------------------------
# Config generation
# ---------------------------------------------------------------------------

# Strategy-to-config-suffix and signal key mapping
_STRATEGY_CONFIG_MAP: Dict[str, dict] = {
    s_name: {
        "suffix": s_info["config_suffix"],
        "signal_key": s_info["signal_key"],
    }
    for s_name, s_info in STRATEGIES.items()
}

# Signals that require extra specific config keys
_SIGNAL_EXTRA_KEYS: Dict[str, dict] = {
    "ichimoku_cloud": {
        "ichimoku_tenkan": 9,
        "ichimoku_kijun": 26,
        "ichimoku_senkou_b": 52,
    },
    "supertrend": {
        "supertrend_multiplier": 2.0,
    },
    "dual_supertrend": {
        "supertrend_multiplier": 2.0,
    },
    "vol_expansion": {
        "vol_expansion_threshold": 1.8,
        "vol_expansion_lookback": 1,
        "vol_expansion_atr_ma_period": 20,
        "vol_expansion_ema_trend_period": 50,
    },
    "volexp_supertrend": {
        "vol_expansion_threshold": 1.8,
        "vol_expansion_lookback": 1,
        "vol_expansion_atr_ma_period": 20,
        "vol_expansion_ema_trend_period": 50,
        "supertrend_multiplier": 2.0,
    },
}


def generate_configs(
    verified_results: List[dict],
    output_dir: Path = PROJECT_ROOT,
    dry_run: bool = False,
) -> List[str]:
    """Generate config JSON files for all verified winners.

    Naming convention: config_{coin_lower}usdt_{strategy_suffix}.json
    (e.g. config_ethusdt_ichi.json, config_bnbusdt_dualst.json)

    Args:
        verified_results: Output from verify_winners() or compare_deployed().
        output_dir: Directory to write config files (default: project root).
        dry_run: If True, print configs but don't write files.

    Returns:
        List of created (or would-be-created) file paths.
    """
    created: List[str] = []

    for r in verified_results:
        strategy_name = r["strategy"]
        if strategy_name not in STRATEGIES:
            print(f"  SKIP {r['coin']}: unknown strategy '{strategy_name}'")
            continue

        strat = STRATEGIES[strategy_name]
        suffix = strat["config_suffix"]
        signal_key = strat["signal_key"]
        prefix = r["prefix"]
        symbol = prefix.upper()
        tf = r["timeframe"]
        params = r.get("params", {})
        sl_v = params.get("sl_mult", strat["sl"])
        tp_v = params.get("tp_mult", strat["tp"])
        trail_v = params.get("trail_mult", strat["trail"])
        pf = r["pf"]
        trades_yr = r.get("trades_yr", r.get("trades", 0))
        sharpe = r.get("sharpe", 0.0)
        dd = r.get("dd_pct", 0.0)

        filename = f"config_{prefix}_{suffix}.json"
        output_path = output_dir / filename

        # Build the config
        cfg = copy.deepcopy(_BASE_CONFIG)
        cfg["_comment"] = (
            f"{r['coin']} {strategy_name.replace('_', ' ').title()} {tf.upper()} — "
            f"auto-generated, PF {pf:.2f}, Sharpe {sharpe:.2f}, "
            f"DD {dd:.1f}%, {trades_yr:.0f} trades/yr"
        )
        cfg["symbol"] = symbol
        cfg["timeframe_signal"] = tf
        cfg["timeframe_trend"] = tf
        cfg["atr_sl_mult"] = sl_v
        cfg["atr_tp_mult"] = tp_v
        cfg["atr_trail_mult"] = trail_v
        cfg["atr_trail_mult_trending"] = trail_v
        if tp_v == 0.0:
            cfg["atr_trail_mult_ranging"] = trail_v
            cfg["atr_trail_mult_volatile"] = trail_v

        # Enable only the target signal
        signals = copy.deepcopy(_BASE_CONFIG["signals"])
        if signal_key in signals:
            signals[signal_key] = {"enabled": True}
        else:
            signals[signal_key] = {"enabled": True}
        cfg["signals"] = signals

        # Add any signal-specific extra keys
        for k, v in _SIGNAL_EXTRA_KEYS.get(signal_key, {}).items():
            cfg[k] = v

        # Override with any extra params from research
        extra = r.get("extra_params", {})
        for k, v in extra.items():
            cfg[k] = v

        if dry_run:
            print(f"  [DRY-RUN] Would write {filename}")
        else:
            with open(output_path, "w") as f:
                json.dump(cfg, f, indent=2)
            print(f"  Written: {output_path}")

        created.append(str(output_path))

    return created


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(results: List[dict], title: str) -> None:
    """Pretty-print a results table with performance metrics.

    Args:
        results: List of result dicts (from sweep or verify).
        title: Section title to display above the table.
    """
    if not results:
        print(f"\n{title}: No results.\n")
        return

    sep = "=" * 90
    print(f"\n{sep}")
    print(f"  {title}")
    print(sep)

    hdr = (
        f"  {'Coin':<12} {'TF':<4} {'Strategy':<18} {'SL':>4} {'TP':>4} {'Trail':>5}"
        f" {'PF':>7} {'Trades':>7} {'Tr/yr':>6} {'WR%':>6} {'DD%':>6} {'Sharpe':>7}"
        f" {'Action':<10}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for r in sorted(results, key=lambda x: -x["pf"]):
        params = r.get("params", {})
        sl_v = params.get("sl_mult", "-")
        tp_v = params.get("tp_mult", "-")
        trail_v = params.get("trail_mult", "-")
        action = r.get("deploy_action", "")
        current_pf = r.get("current_pf")
        action_str = ""
        if action == "NEW":
            action_str = "[NEW]"
        elif action == "UPGRADE":
            action_str = f"[UP from {current_pf:.2f}]"
        elif action == "EXISTING":
            action_str = "[deployed]"

        print(
            f"  {r['coin']:<12} {r['timeframe']:<4} {r['strategy']:<18}"
            f" {sl_v:>4} {tp_v:>4} {trail_v:>5}"
            f" {r['pf']:>7.2f} {r['trades']:>7} {r.get('trades_yr', 0):>5.0f}/yr"
            f" {r.get('wr_pct', 0):>5.1f}% {r.get('dd_pct', 0):>5.1f}%"
            f" {r.get('sharpe', 0):>7.2f} {action_str:<10}"
        )

    print(f"\n  Total: {len(results)} result(s)")

    # Grouped breakdown by strategy
    by_strategy: Dict[str, List[dict]] = defaultdict(list)
    for r in results:
        by_strategy[r["strategy"]].append(r)
    if len(by_strategy) > 1:
        print("\n  Breakdown by strategy:")
        for strat, items in sorted(by_strategy.items()):
            avg_pf = sum(x["pf"] for x in items) / len(items)
            print(f"    {strat:<22} {len(items):>3} result(s), avg PF {avg_pf:.2f}")
    print()


# ---------------------------------------------------------------------------
# JSON I/O helpers
# ---------------------------------------------------------------------------

def _save_results(results: List[dict], path: str) -> None:
    """Save results list to JSON file.

    Args:
        results: List of result dicts.
        path: Output file path.
    """
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved {len(results)} result(s) to {path}")


def _load_results(path: str) -> List[dict]:
    """Load results list from JSON file.

    Args:
        path: Input file path.

    Returns:
        List of result dicts.
    """
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser."""
    parser = argparse.ArgumentParser(
        description="Auto-research pipeline: sweep → verify → report → generate configs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Available strategies:
{chr(10).join(f'  {name:<22} {info["description"]}' for name, info in sorted(STRATEGIES.items()))}

Examples:
  # Full pipeline (sweep + verify + report) on a 4H strategy
  python3 research/auto_research.py --strategy dual_supertrend --timeframe 4h

  # Test only a few coins (fast check)
  python3 research/auto_research.py --strategy ichimoku --coins ethusdt,bnbusdt,solusdt

  # Sweep only, save results for later
  python3 research/auto_research.py --strategy alligator --sweep-only --output data/alligator_sweep.json

  # Generate configs from saved results
  python3 research/auto_research.py --generate-configs --input data/alligator_sweep.json

  # Run all strategies (mega sweep)
  python3 research/auto_research.py --all-strategies --min-pf 1.4 --min-trades 8
""",
    )

    parser.add_argument(
        "--strategy",
        type=str,
        help="Strategy name (from registry). See available strategies below.",
    )
    parser.add_argument(
        "--all-strategies",
        action="store_true",
        help="Run sweep for ALL strategies in the registry.",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        help="Override default timeframe (e.g. '1h', '4h', '15m').",
    )
    parser.add_argument(
        "--sl",
        type=float,
        help="Override ATR stop-loss multiplier.",
    )
    parser.add_argument(
        "--tp",
        type=float,
        help="Override ATR take-profit multiplier (0 = trailing only).",
    )
    parser.add_argument(
        "--trail",
        type=float,
        help="Override ATR trailing stop multiplier.",
    )
    parser.add_argument(
        "--coins",
        type=str,
        help="Comma-separated coin prefixes, e.g. btcusdt,ethusdt,xrpusdt",
    )
    parser.add_argument(
        "--sweep-only",
        action="store_true",
        help="Only run sweep, skip the stricter verify filter step.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Load previous sweep results from --input and apply verify filters only.",
    )
    parser.add_argument(
        "--compare-deployed",
        action="store_true",
        help="Compare verified winners against currently deployed portfolio.",
    )
    parser.add_argument(
        "--generate-configs",
        action="store_true",
        help="Generate config JSON files for winners (can combine with other flags).",
    )
    parser.add_argument(
        "--input",
        type=str,
        help="Input JSON file for --verify-only or --generate-configs.",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output JSON file to save results (default: data/{strategy}_results.json).",
    )
    parser.add_argument(
        "--min-pf",
        type=float,
        default=1.3,
        help="Minimum profit factor for verify/deploy threshold (default: 1.3).",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=6,
        help="Minimum total trades for verify/deploy threshold (default: 6).",
    )
    parser.add_argument(
        "--balance",
        type=float,
        default=10000.0,
        help="Starting balance in USDT for BacktestEngine (default: 10000).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="For --generate-configs: print what would be written without writing.",
    )
    parser.add_argument(
        "--exclude-deployed",
        action="store_true",
        help="Skip already-deployed coins when discovering coins.",
    )

    return parser


def main() -> int:
    """Main entry point for the auto-research pipeline.

    Returns:
        Exit code (0 = success, 1 = error).
    """
    parser = _build_parser()
    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # Mode: --verify-only — load previous results and filter
    # -----------------------------------------------------------------------
    if args.verify_only:
        if not args.input:
            print("ERROR: --verify-only requires --input <sweep_results.json>")
            return 1
        print(f"Loading sweep results from {args.input}")
        sweep_results = _load_results(args.input)
        verified = verify_winners(sweep_results, min_pf=args.min_pf, min_trades=args.min_trades)

        if args.compare_deployed:
            deployed = get_deployed_coins()
            verified = compare_deployed(verified, deployed)

        print_report(verified, f"Verified Winners (PF >= {args.min_pf}, trades >= {args.min_trades})")

        if args.generate_configs:
            generate_configs(verified, dry_run=args.dry_run)

        if args.output:
            _save_results(verified, args.output)

        return 0

    # -----------------------------------------------------------------------
    # Mode: --generate-configs only — load results and write configs
    # -----------------------------------------------------------------------
    if args.generate_configs and not args.strategy and not args.all_strategies:
        if not args.input:
            print("ERROR: --generate-configs (standalone) requires --input <winners.json>")
            return 1
        print(f"Loading results from {args.input}")
        results = _load_results(args.input)
        created = generate_configs(results, dry_run=args.dry_run)
        print(f"\nGenerated {len(created)} config file(s)")
        return 0

    # -----------------------------------------------------------------------
    # Determine coin list
    # -----------------------------------------------------------------------
    coin_list: Optional[List[str]] = None
    if args.coins:
        coin_list = [c.lower().strip() for c in args.coins.split(",") if c.strip()]

    # -----------------------------------------------------------------------
    # Mode: --all-strategies — sweep every registered strategy
    # -----------------------------------------------------------------------
    if args.all_strategies:
        all_results: List[dict] = []
        print(f"\nMega sweep: {len(STRATEGIES)} strategies")

        for strat_name in sorted(STRATEGIES):
            strat_info = STRATEGIES[strat_name]
            tf = args.timeframe or strat_info["default_tf"]
            coins_for_strat = coin_list or discover_coins(
                DATA_DIR, tf, exclude_deployed=args.exclude_deployed
            )
            results = sweep_strategy(
                strategy_name=strat_name,
                timeframe=tf,
                sl=args.sl,
                tp=args.tp,
                trail=args.trail,
                coins=coins_for_strat,
                balance=args.balance,
                min_pf=1.0,
                min_trades=args.min_trades,
            )
            all_results.extend(results)

        verified = verify_winners(all_results, min_pf=args.min_pf, min_trades=args.min_trades)

        if args.compare_deployed:
            deployed = get_deployed_coins()
            verified = compare_deployed(verified, deployed)

        print_report(verified, f"All-Strategy Mega Sweep — Winners (PF >= {args.min_pf})")

        output_path = args.output or str(DATA_DIR / "all_strategies_results.json")
        _save_results(verified, output_path)

        if args.generate_configs:
            generate_configs(verified, dry_run=args.dry_run)

        return 0

    # -----------------------------------------------------------------------
    # Mode: single strategy sweep (default)
    # -----------------------------------------------------------------------
    if not args.strategy:
        parser.print_help()
        return 0

    if args.strategy not in STRATEGIES:
        print(f"ERROR: Unknown strategy '{args.strategy}'")
        print(f"Available: {', '.join(sorted(STRATEGIES))}")
        return 1

    strat_info = STRATEGIES[args.strategy]
    tf = args.timeframe or strat_info["default_tf"]
    coins_for_run = coin_list or discover_coins(
        DATA_DIR, tf, exclude_deployed=args.exclude_deployed
    )

    sweep_results = sweep_strategy(
        strategy_name=args.strategy,
        timeframe=tf,
        sl=args.sl,
        tp=args.tp,
        trail=args.trail,
        coins=coins_for_run,
        balance=args.balance,
        min_pf=1.0,   # Broad sweep filter; stricter verify step below
        min_trades=args.min_trades,
    )

    if args.sweep_only:
        # Skip stricter verification pass
        print_report(sweep_results, f"Sweep Results — {args.strategy} [{tf}]")
        output_path = args.output or str(DATA_DIR / f"{args.strategy}_sweep.json")
        _save_results(sweep_results, output_path)
        if args.generate_configs:
            generate_configs(sweep_results, dry_run=args.dry_run)
        return 0

    # Apply stricter deployment filter
    verified = verify_winners(sweep_results, min_pf=args.min_pf, min_trades=args.min_trades)

    if args.compare_deployed:
        deployed = get_deployed_coins()
        verified = compare_deployed(verified, deployed)

    print_report(verified, f"Verified Winners — {args.strategy} [{tf}] (PF >= {args.min_pf})")

    output_path = args.output or str(DATA_DIR / f"{args.strategy}_verified.json")
    _save_results(verified, output_path)

    if args.generate_configs:
        generate_configs(verified, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
