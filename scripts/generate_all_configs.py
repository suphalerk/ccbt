#!/usr/bin/env python3
"""
Generate all missing config files for 136-bot portfolio and rebuild docker-compose.yml.

Reads per_bot entries from data/full_portfolio_74.json and creates a config
for each bot that does not already have one. Generates a complete docker-compose.yml
with all bots grouped by strategy type.

Usage:
    python3 scripts/generate_all_configs.py
"""
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent  # /Users/iceai/Work/ccbt

# ---------------------------------------------------------------------------
# Base config template (shared fields across all strategies)
# ---------------------------------------------------------------------------
BASE_TEMPLATE = {
    "exchange": "binance",
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
    "rsi_period": 14,
    "rsi_min": 45,
    "rsi_max": 65,
    "atr_period": 14,
    "atr_min": 0.0,
    "partial_tp_enabled": False,
    "partial_tp_pct": 0.3,
    "partial_tp_atr_mult": 2.0,
    "move_sl_to_be_after_tp1": True,
    "breakeven_buffer_atr_mult": 0.5,
    "volume_mult": 1.0,
    "volume_max_mult": None,
    "rsi_long_min": 45,
    "rsi_long_max": 65,
    "rsi_short_min": 35,
    "rsi_short_max": 55,
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
    "trading_hours": {
        "enabled": True,
        "start_utc": 3,
        "end_utc": 20
    },
    "regime_filter": {
        "enabled": True,
        "skip_ranging": True
    },
    "flexible_cooldown": {
        "enabled": False,
        "min_quality_score": 0.7,
        "cooldown_reduction_factor": 0.5,
        "log_overrides": True
    },
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
        "adx_di_cross": {"enabled": False},
        "choppiness_ema": {"enabled": False},
        "williams_r_adx": {"enabled": False},
        "roc_momentum": {"enabled": False},
        "stoch_supertrend": {"enabled": False},
        "price_channel_vol": {"enabled": False},
        "ema_alligator": {"enabled": False},
        "supertrend_volume": {"enabled": False},
        "stoch_mtf": {"enabled": False},
        "zscore_meanrev": {"enabled": False},
        "ema_ribbon": {"enabled": False},
        "dual_thrust": {"enabled": False},
        "awesome_oscillator": {"enabled": False},
        "range_bounce": {"enabled": False},
        "ribbon_rsi_vol": {"enabled": False},
        "dualthrust_adx": {"enabled": False},
        "zscore_stoch": {"enabled": False},
        "ichi_adx": {"enabled": False},
        "ribbon_ao": {"enabled": False},
    },
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
    "ema_fast2": 5,
    "ema_slow2": 13,
    "bb_period": 20,
    "bb_std": 2.0,
    "bb_squeeze_percentile": 0.3,
    "bb_volume_mult": 1.2,
    "swing_lookback": 5,
    "divergence_lookback": 20,
    "adaptive_sizing": {"enabled": False},
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "signal_scorer": {"enabled": False},
    "ai_layer": {
        "enabled": False,
        "mode": "advisor",
        "confidence_threshold": 0.65,
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "timeout_seconds": 10,
        "fallback_on_timeout": "execute",
        "news_source": "rss",
        "news_lookback_hours": 12,
        "log_all_decisions": True,
        "calibration": {
            "enabled": True,
            "min_trades_for_accuracy": 5,
            "rolling_window": 30,
            "auto_adjust_influence": True,
        },
    },
}


# ---------------------------------------------------------------------------
# Per-signal SL/TP/trail defaults
# ---------------------------------------------------------------------------
SIGNAL_PARAMS = {
    # Trend-following
    "ichimoku_cloud":       {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "ichimoku_cloud_4h":    {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "ichimoku_cloud_trail": {"atr_sl_mult": 2.0, "atr_tp_mult": 0.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "dual_thrust":          {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "awesome_oscillator":   {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "ema_ribbon":           {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "stoch_mtf":            {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "ribbon_ao":            {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "dualthrust_adx":       {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    "ichi_adx":             {"atr_sl_mult": 2.0, "atr_tp_mult": 4.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
    # Mean reversion
    "zscore_meanrev":       {"atr_sl_mult": 1.5, "atr_tp_mult": 2.0, "atr_trail_mult": 2.0, "atr_trail_mult_trending": 2.0, "atr_trail_mult_ranging": 1.5, "atr_trail_mult_volatile": 3.0, "atr_trail_mult_post_tp1": 2.5},
    "range_bounce":         {"atr_sl_mult": 1.5, "atr_tp_mult": 2.0, "atr_trail_mult": 2.0, "atr_trail_mult_trending": 2.0, "atr_trail_mult_ranging": 1.5, "atr_trail_mult_volatile": 3.0, "atr_trail_mult_post_tp1": 2.5},
    "zscore_stoch":         {"atr_sl_mult": 1.5, "atr_tp_mult": 2.0, "atr_trail_mult": 2.0, "atr_trail_mult_trending": 2.0, "atr_trail_mult_ranging": 1.5, "atr_trail_mult_volatile": 3.0, "atr_trail_mult_post_tp1": 2.5},
    # EMA crossover
    "ema_crossover":        {"atr_sl_mult": 1.0, "atr_tp_mult": 3.0, "atr_trail_mult": 2.0, "atr_trail_mult_trending": 2.0, "atr_trail_mult_ranging": 1.5, "atr_trail_mult_volatile": 3.0, "atr_trail_mult_post_tp1": 3.0},
    # Vol expansion
    "vol_expansion":        {"atr_sl_mult": 2.5, "atr_tp_mult": 3.0, "atr_trail_mult": 3.0, "atr_trail_mult_trending": 3.0, "atr_trail_mult_ranging": 2.0, "atr_trail_mult_volatile": 4.0, "atr_trail_mult_post_tp1": 4.0},
}


def make_config(
    coin: str,
    signal_key: str,
    timeframe: str,
    comment: str,
    strategy_name: str,
    risk: float = 0.01,
    yolo_mode: bool = False,
) -> dict:
    """Build a complete config dict from the base template."""
    import copy
    cfg = copy.deepcopy(BASE_TEMPLATE)

    symbol = f"{coin.upper()}USDT"
    # Special case: XAUUSD (not USDT pair)
    if coin.upper() == "XAUUSD":
        symbol = "XAUUSDT"

    cfg["_comment"] = comment
    cfg["strategy_name"] = strategy_name
    cfg["symbol"] = symbol
    cfg["timeframe_signal"] = timeframe
    cfg["timeframe_trend"] = timeframe
    cfg["risk_per_trade"] = risk

    # SL/TP params
    sp = SIGNAL_PARAMS.get(signal_key, SIGNAL_PARAMS["dual_thrust"])
    cfg["atr_sl_mult"] = sp["atr_sl_mult"]
    cfg["atr_tp_mult"] = sp["atr_tp_mult"]
    cfg["atr_trail_mult"] = sp["atr_trail_mult"]
    cfg["atr_trail_mult_trending"] = sp["atr_trail_mult_trending"]
    cfg["atr_trail_mult_ranging"] = sp["atr_trail_mult_ranging"]
    cfg["atr_trail_mult_volatile"] = sp["atr_trail_mult_volatile"]
    cfg["atr_trail_mult_post_tp1"] = sp["atr_trail_mult_post_tp1"]

    # Enable the correct signal
    # Map signal_key to the signals dict key
    sig_map = {
        "ichimoku_cloud": "ichimoku_cloud",
        "ichimoku_cloud_4h": "ichimoku_cloud",
        "ichimoku_cloud_trail": "ichimoku_cloud",
        "ema_crossover": "ema_crossover",
        "dual_thrust": "dual_thrust",
        "awesome_oscillator": "awesome_oscillator",
        "range_bounce": "range_bounce",
        "zscore_meanrev": "zscore_meanrev",
        "stoch_mtf": "stoch_mtf",
        "ema_ribbon": "ema_ribbon",
        "ribbon_ao": "ribbon_ao",
        "dualthrust_adx": "dualthrust_adx",
        "zscore_stoch": "zscore_stoch",
        "ichi_adx": "ichi_adx",
        "vol_expansion": "vol_expansion",
    }
    sig_field = sig_map.get(signal_key, signal_key)
    if sig_field in cfg["signals"]:
        cfg["signals"][sig_field] = {"enabled": True}
    # EMA crossover also needs ema_fast_crossover
    if signal_key == "ema_crossover":
        cfg["signals"]["ema_fast_crossover"] = {"enabled": True}

    # 4H timeframe configs use ichimoku_cloud signal — no extra changes needed
    return cfg


def config_filename(coin: str, signal_suffix: str) -> str:
    """Return config file basename."""
    coin_lower = coin.lower()
    # XAUUSD maps to xauusd (not xauusdusdt)
    if coin_lower == "xauusd":
        return f"config_xauusd_{signal_suffix}.json"
    return f"config_{coin_lower}usdt_{signal_suffix}.json"


# ---------------------------------------------------------------------------
# Label → (signal_key, timeframe, signal_suffix) mapping
# ---------------------------------------------------------------------------
def parse_label(label: str) -> Optional[dict]:
    """
    Return a dict with keys: coin, signal_key, timeframe, signal_suffix,
    strategy_name, comment_tag.
    Returns None if the label should be skipped (already has a well-known config).
    """
    parts = label.split()
    coin = parts[0]

    # Already-handled bots with custom configs (non-USDT or complex)
    if label in ("BTC EMA 15m", "WIF EMA 15m", "ARC EMA 15m",
                 "AVAX Ichi 1H", "BERA Ichi 1H", "INJ Ichi 1H",
                 "ANIME Ichi 1H", "TRUMP Ichi 1H", "IP Ichi 1H",
                 "POL Ichi Trail", "1000PEPE VolExp"):
        return None  # handled by pre-existing configs

    sig_raw = parts[1] if len(parts) > 1 else ""
    round_tag = parts[2] if len(parts) > 2 else ""

    # --- Map sig_raw + round_tag to signal_key + timeframe ---
    # Ichimoku 4H
    if sig_raw == "Ichi" and round_tag == "4H":
        return dict(coin=coin, signal_key="ichimoku_cloud_4h", timeframe="4h",
                    signal_suffix="ichi4h", strategy_name="Ichi 4H",
                    comment=f"{coin} Ichimoku 4H — auto-generated 1% risk")
    # Ichimoku 1H (legacy labels like "BERA Ichi 1H" already excluded above)
    if sig_raw == "Ichi" and round_tag == "1H":
        return dict(coin=coin, signal_key="ichimoku_cloud", timeframe="1h",
                    signal_suffix="ichi", strategy_name="Ichi 1H",
                    comment=f"{coin} Ichimoku 1H — auto-generated 1% risk")
    # Round 10/11/12/M100 signals
    # dual_thr / dual_thrust R10
    if sig_raw in ("dual_thr",) and round_tag in ("R10", "M100"):
        return dict(coin=coin, signal_key="dual_thrust", timeframe="1h",
                    signal_suffix="dualthrust",
                    strategy_name="Dual Thrust 1H",
                    comment=f"{coin} Dual Thrust 1H — {round_tag}")
    if sig_raw == "dualthru" and round_tag == "R12":
        return dict(coin=coin, signal_key="dual_thrust", timeframe="1h",
                    signal_suffix="dualthrust",
                    strategy_name="Dual Thrust 1H",
                    comment=f"{coin} Dual Thrust 1H — R12")
    if sig_raw == "awesome_" and round_tag in ("R10", "M100"):
        return dict(coin=coin, signal_key="awesome_oscillator", timeframe="1h",
                    signal_suffix="awesome",
                    strategy_name="Awesome Osc 1H",
                    comment=f"{coin} Awesome Oscillator 1H — {round_tag}")
    if sig_raw == "range_bo" and round_tag in ("R10", "M100"):
        return dict(coin=coin, signal_key="range_bounce", timeframe="1h",
                    signal_suffix="rangebounce",
                    strategy_name="Range Bounce 1H",
                    comment=f"{coin} Range Bounce 1H — {round_tag}")
    if sig_raw == "zscore_m" and round_tag in ("R11", "M100"):
        return dict(coin=coin, signal_key="zscore_meanrev", timeframe="1h",
                    signal_suffix="zscore",
                    strategy_name="ZScore MeanRev 1H",
                    comment=f"{coin} ZScore MeanRev 1H — {round_tag}")
    if sig_raw == "zscore_s" and round_tag in ("R12",):
        return dict(coin=coin, signal_key="zscore_stoch", timeframe="1h",
                    signal_suffix="zscoresto",
                    strategy_name="ZScore Stoch 1H",
                    comment=f"{coin} ZScore Stoch 1H — {round_tag}")
    if sig_raw == "stoch_mt" and round_tag in ("R11", "M100"):
        # Some coins are 4H, some 1H — default 1H here (engine will decide)
        return dict(coin=coin, signal_key="stoch_mtf", timeframe="1h",
                    signal_suffix="stochmtf",
                    strategy_name="Stoch MTF 1H",
                    comment=f"{coin} Stoch MTF 1H — {round_tag}")
    if sig_raw == "ema_ribb" and round_tag in ("R11", "M100"):
        return dict(coin=coin, signal_key="ema_ribbon", timeframe="1h",
                    signal_suffix="emaribbon",
                    strategy_name="EMA Ribbon 1H",
                    comment=f"{coin} EMA Ribbon 1H — {round_tag}")
    if sig_raw == "ema_cros" and round_tag in ("M100",):
        return dict(coin=coin, signal_key="ema_crossover", timeframe="1h",
                    signal_suffix="ema1h",
                    strategy_name="EMA Crossover 1H",
                    comment=f"{coin} EMA Crossover 1H — {round_tag}")
    if sig_raw == "ichi_adx" and round_tag == "R12":
        return dict(coin=coin, signal_key="ichi_adx", timeframe="1h",
                    signal_suffix="ichiadx",
                    strategy_name="Ichi+ADX 1H",
                    comment=f"{coin} Ichi+ADX 1H — R12")
    if sig_raw == "ribbon_a" and round_tag == "R12":
        return dict(coin=coin, signal_key="ribbon_ao", timeframe="1h",
                    signal_suffix="ribbonao",
                    strategy_name="Ribbon+AO 1H",
                    comment=f"{coin} Ribbon+AO 1H — R12")

    # PHA dual_thr R10 is 4H per the task description
    if coin == "PHA" and sig_raw == "dual_thr":
        return dict(coin=coin, signal_key="dual_thrust", timeframe="4h",
                    signal_suffix="dualthrust4h",
                    strategy_name="Dual Thrust 4H",
                    comment=f"{coin} Dual Thrust 4H — R10")

    # DASH awesome_ R10 is 4H
    if coin == "DASH" and sig_raw == "awesome_":
        return dict(coin=coin, signal_key="awesome_oscillator", timeframe="4h",
                    signal_suffix="awesome4h",
                    strategy_name="Awesome Osc 4H",
                    comment=f"{coin} Awesome Oscillator 4H — R10")

    # Fallback: if we get here, log and skip
    print(f"  [SKIP] Unknown label pattern: {repr(label)}", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# Docker compose service block builder
# ---------------------------------------------------------------------------
def service_block(service_name: str, container_name: str, config_file: str,
                  yolo: bool = False) -> str:
    env_lines = f"      - CONFIG_FILE={config_file}\n      - BOT_DATA_DIR=/app/data"
    if yolo:
        env_lines += "\n      - YOLO_MODE=1"
    return f"""  {service_name}:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: {container_name}
    restart: on-failure:5
    env_file:
      - .env
    environment:
{env_lines}
    volumes:
      - ./data:/app/data
      - ./{config_file}:/app/{config_file}:ro
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: "0.5"
        reservations:
          memory: 128M
          cpus: "0.1"
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
"""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    with open(BASE_DIR / "data" / "full_portfolio_74.json") as f:
        data = json.load(f)

    per_bot: dict = data["portfolio_summary"]["per_bot"]
    total_bots = len(per_bot)
    print(f"Total bots in portfolio: {total_bots}")

    # Existing config files
    existing_configs = {p.name for p in BASE_DIR.glob("config_*.json")}
    print(f"Existing config files: {len(existing_configs)}")

    # Track what we create / already have
    created: list[str] = []
    already_existing: list[str] = []
    skipped: list[str] = []

    # We'll also accumulate all services for docker-compose
    # Group: (strategy_group_label, list of (service_name, container_name, config_file, yolo, comment))
    services: list[tuple] = []

    # ---------------------------------------------------------------------------
    # Pre-existing well-known bots (already have configs)
    # ---------------------------------------------------------------------------
    KNOWN_SERVICES = [
        # (label_in_per_bot, service_name, container_name, config_file, yolo, group)
        ("BTC EMA 15m",      "bot-btc",           "tradingbot-btc",           "config.json",                        True,  "EMA Crossover 15m"),
        ("WIF EMA 15m",      "bot-wif",           "tradingbot-wif",           "config_wif.json",                    True,  "EMA Crossover 15m"),
        ("ARC EMA 15m",      "bot-arc-ema",       "tradingbot-arc-ema",       "config_arcusdt_ema.json",            False, "EMA Crossover 15m"),
        ("AVAX Ichi 1H",     "bot-avax-ichi",     "tradingbot-avax-ichi",     "config_avax_ichi.json",              False, "Ichimoku 1H"),
        ("BERA Ichi 1H",     "bot-bera-ichi",     "tradingbot-bera-ichi",     "config_berausdt_ichi.json",          False, "Ichimoku 1H"),
        ("INJ Ichi 1H",      "bot-inj-ichi",      "tradingbot-inj-ichi",      "config_injusdt_ichi.json",           False, "Ichimoku 1H"),
        ("ANIME Ichi 1H",    "bot-anime-ichi",    "tradingbot-anime-ichi",    "config_animeusdt_ichi.json",         False, "Ichimoku 1H"),
        ("TRUMP Ichi 1H",    "bot-trump-ichi",    "tradingbot-trump-ichi",    "config_trumpusdt_ichi.json",         False, "Ichimoku 1H"),
        ("IP Ichi 1H",       "bot-ip-ichi",       "tradingbot-ip-ichi",       "config_ipusdt_ichi.json",            False, "Ichimoku 1H"),
        ("POL Ichi Trail",   "bot-pol-ichi4htrail","tradingbot-pol-ichi4htrail","config_polusdt_ichi4htrail.json",  False, "4H Ichimoku Trail"),
        ("1000PEPE VolExp",  "bot-1kpepe-volexp", "tradingbot-1kpepe-volexp", "config_1000pepeusdt_volexp.json",    False, "Vol Expansion"),
    ]
    known_labels = {s[0] for s in KNOWN_SERVICES}

    for label, svc_name, ctr_name, cfg_file, yolo, group in KNOWN_SERVICES:
        already_existing.append(cfg_file)
        stats = per_bot.get(label, {})
        pnl = stats.get("pnl", 0)
        trades = stats.get("trades", 0)
        services.append((group, svc_name, ctr_name, cfg_file, yolo, f"{label} trades={trades} pnl=${pnl:.2f}"))

    # ---------------------------------------------------------------------------
    # Process all remaining labels
    # ---------------------------------------------------------------------------
    # Track container names to catch duplicates
    used_containers: set[str] = set(s[2] for s in services)
    used_service_names: set[str] = set(s[1] for s in services)

    for label in sorted(per_bot.keys()):
        if label in known_labels:
            continue

        info = parse_label(label)
        if info is None:
            skipped.append(label)
            continue

        coin = info["coin"]
        signal_key = info["signal_key"]
        timeframe = info["timeframe"]
        signal_suffix = info["signal_suffix"]
        strategy_name = info["strategy_name"]
        comment = info["comment"]

        cfg_filename = config_filename(coin, signal_suffix)
        cfg_path = BASE_DIR / cfg_filename

        # Determine group for docker-compose
        if "ichi4h" in signal_suffix:
            group = "Ichimoku 4H"
        elif "ichi" in signal_suffix and "adx" not in signal_suffix:
            group = "Ichimoku 1H"
        elif "dualthrust4h" in signal_suffix:
            group = "Dual Thrust 4H"
        elif "dualthrust" in signal_suffix or "dual_thrust" in signal_key:
            group = "Dual Thrust 1H"
        elif "awesome" in signal_suffix:
            group = "Awesome Oscillator"
        elif "rangebounce" in signal_suffix:
            group = "Range Bounce"
        elif "zscore" in signal_suffix or "zscoresto" in signal_suffix:
            group = "ZScore MeanRev"
        elif "stochmtf" in signal_suffix:
            group = "Stoch MTF"
        elif "emaribbon" in signal_suffix or "ribbon" in signal_suffix:
            group = "EMA Ribbon"
        elif "ema1h" in signal_suffix:
            group = "EMA Crossover 1H"
        elif "ichiadx" in signal_suffix:
            group = "Ichi+ADX"
        elif "ribbonao" in signal_suffix:
            group = "Ribbon+AO"
        elif "volexp" in signal_suffix:
            group = "Vol Expansion"
        else:
            group = "Other"

        # Generate config if missing
        if cfg_filename not in existing_configs and not cfg_path.exists():
            cfg = make_config(
                coin=coin,
                signal_key=signal_key,
                timeframe=timeframe,
                comment=comment,
                strategy_name=strategy_name,
            )
            with open(cfg_path, "w") as f:
                json.dump(cfg, f, indent=2)
            created.append(cfg_filename)
            existing_configs.add(cfg_filename)
            print(f"  [CREATE] {cfg_filename}")
        else:
            already_existing.append(cfg_filename)

        # Build service name from coin + suffix (sanitize for docker)
        svc_coin = coin.lower().replace("1000", "1k").replace("xauusd", "xauusd")
        svc_suffix = signal_suffix.replace("_", "")
        svc_name = f"bot-{svc_coin}-{svc_suffix}"
        ctr_name = f"tradingbot-{svc_coin}-{svc_suffix}"

        # Dedup container names
        base_svc = svc_name
        base_ctr = ctr_name
        idx = 2
        while ctr_name in used_containers:
            svc_name = f"{base_svc}-{idx}"
            ctr_name = f"{base_ctr}-{idx}"
            idx += 1

        used_containers.add(ctr_name)
        used_service_names.add(svc_name)

        stats = per_bot.get(label, {})
        pnl = stats.get("pnl", 0)
        trades = stats.get("trades", 0)
        services.append((group, svc_name, ctr_name, cfg_filename, False,
                         f"{label} trades={trades} pnl=${pnl:.2f}"))

    # ---------------------------------------------------------------------------
    # Build docker-compose.yml
    # ---------------------------------------------------------------------------
    # Sort services by group then name
    group_order = [
        "EMA Crossover 15m", "EMA Crossover 1H",
        "Ichimoku 1H", "Ichimoku 4H", "4H Ichimoku Trail",
        "Dual Thrust 1H", "Dual Thrust 4H",
        "Awesome Oscillator", "Range Bounce",
        "ZScore MeanRev", "Stoch MTF", "EMA Ribbon",
        "Ichi+ADX", "Ribbon+AO", "Vol Expansion", "Other",
    ]

    def group_sort_key(svc_tuple):
        g = svc_tuple[0]
        if g in group_order:
            return (group_order.index(g), svc_tuple[1])
        return (len(group_order), svc_tuple[1])

    services.sort(key=group_sort_key)

    lines = []
    lines.append("# =============================================================================")
    lines.append(f"# Docker Compose for Crypto Trading Bot — {len(services)}-bot portfolio")
    lines.append("# Services: {count} trading bots + dashboard (Vite SPA + FastAPI v2)".format(count=len(services)))
    lines.append("# Shared volume for SQLite database persistence")
    lines.append("#")
    lines.append("# Config modes:")
    lines.append("#   Standard:  docker compose up -d")
    lines.append("#   YOLO:      YOLO_MODE=1 docker compose up -d")
    lines.append("# =============================================================================")
    lines.append("")
    lines.append("services:")
    lines.append("")

    current_group = None
    for group, svc_name, ctr_name, cfg_file, yolo, comment in services:
        if group != current_group:
            current_group = group
            lines.append(f"  # {'='*73}")
            lines.append(f"  # {group}")
            lines.append(f"  # {'='*73}")
            lines.append("")
        lines.append(f"  # {comment}")
        lines.append(service_block(svc_name, ctr_name, cfg_file, yolo).rstrip())
        lines.append("")

    # All config file mount lines for dashboard
    all_configs = sorted({s[3] for s in services})
    vol_lines = ["      - ./data:/app/data"]
    for cf in all_configs:
        vol_lines.append(f"      - ./{cf}:/app/{cf}:ro")

    lines.append("  # ===========================================================================")
    lines.append("  # Dashboard Service")
    lines.append("  # Runs Streamlit web UI for monitoring all bots")
    lines.append("  # ===========================================================================")
    lines.append("  dashboard:")
    lines.append("    build:")
    lines.append("      context: .")
    lines.append("      dockerfile: Dockerfile")
    lines.append("    container_name: tradingbot-dashboard")
    lines.append("    restart: on-failure:5")
    lines.append("    command: >")
    lines.append("      uvicorn api.main:app")
    lines.append("      --host=0.0.0.0")
    lines.append("      --port=8501")
    lines.append("      --server.headless=true")
    lines.append("      --browser.gatherUsageStats=false")
    lines.append("      --server.enableCORS=false")
    lines.append("      --server.enableXsrfProtection=false")
    lines.append("    env_file:")
    lines.append("      - .env")
    lines.append("    environment:")
    lines.append("      - CONFIG_FILE=${CONFIG_FILE:-config.json}")
    lines.append("      - BOT_DATA_DIR=/app/data")
    lines.append("    ports:")
    lines.append('      - "127.0.0.1:8501:8501"')
    lines.append("    volumes:")
    for vl in vol_lines:
        lines.append(vl)
    lines.append("    deploy:")
    lines.append("      resources:")
    lines.append("        limits:")
    lines.append("          memory: 512M")
    lines.append('          cpus: "0.5"')
    lines.append("        reservations:")
    lines.append("          memory: 128M")
    lines.append('          cpus: "0.1"')
    lines.append("    depends_on:")
    lines.append("      - bot-btc")
    lines.append("    healthcheck:")
    lines.append('      test: ["CMD", "curl", "-f", "http://localhost:8501/_stcore/health"]')
    lines.append("      interval: 30s")
    lines.append("      timeout: 10s")
    lines.append("      start_period: 15s")
    lines.append("      retries: 3")
    lines.append("    logging:")
    lines.append("      driver: json-file")
    lines.append("      options:")
    lines.append('        max-size: "10m"')
    lines.append('        max-file: "3"')
    lines.append("")
    lines.append("# Data directory bind-mounted from host (./data) so local dashboard")
    lines.append("# and monitoring tools can access trades.db and heartbeat files directly.")

    compose_path = BASE_DIR / "docker-compose.yml"
    with open(compose_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 60)
    print("DEPLOY SUMMARY")
    print("=" * 60)
    print(f"Total bots in portfolio:    {total_bots}")
    print(f"Total services in compose:  {len(services)} bots + 1 dashboard = {len(services)+1}")
    print(f"New configs created:        {len(created)}")
    print(f"Already existing configs:   {len(set(already_existing))}")
    print(f"Labels skipped/pre-mapped:  {len(skipped)}")
    if skipped:
        print(f"  Skipped: {skipped}")

    # Duplicate check
    all_ctrs = [s[2] for s in services]
    dupes = [c for c in set(all_ctrs) if all_ctrs.count(c) > 1]
    if dupes:
        print(f"\nWARNING: Duplicate container names: {dupes}")
    else:
        print("Duplicate container check:  PASS (no duplicates)")

    print(f"\ndocker-compose.yml written: {compose_path}")


if __name__ == "__main__":
    main()
