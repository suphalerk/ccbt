"""
Portfolio backtest — 47 bots, 1-year window, $200/bot.

Generates three reports:
  1. Per-bot summary (PF, WR, trades, PnL, ROI, DD)
  2. Monthly portfolio returns
  3. Daily returns for the last 2 months

Output: stdout + data/portfolio_backtest_47.json
"""

import copy
import json
import logging
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Bootstrap paths
# ---------------------------------------------------------------------------
ROOT = Path("/Users/iceai/Work/ccbt")
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.WARNING)
logging.getLogger("backtest").setLevel(logging.WARNING)
logging.getLogger("bot").setLevel(logging.WARNING)

from backtest.data_loader import load_ohlcv          # noqa: E402
from backtest.engine import BacktestEngine            # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
INITIAL_BALANCE = 200.0
DATA_DIR = ROOT / "data"
CONFIG_DIR = ROOT

# ---------------------------------------------------------------------------
# Load template config once
# ---------------------------------------------------------------------------
with open(CONFIG_DIR / "config_avax_ichi.json") as _f:
    _ICHI_TEMPLATE: dict = json.load(_f)

# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

SIGNAL_DEFAULTS: dict = {
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
}


def build_config(
    coin: str,
    strategy_key: str,
    tf_signal: str,
    sl: float,
    tp: float,
    trail: float,
    leverage: int = 25,
    risk: float = 0.01,
) -> dict:
    """Build an inline config from the AVAX template."""
    cfg = copy.deepcopy(_ICHI_TEMPLATE)
    cfg["symbol"] = f"{coin}USDT"
    cfg["timeframe_signal"] = tf_signal
    cfg["timeframe_trend"] = tf_signal
    cfg["risk_per_trade"] = risk
    cfg["leverage"] = leverage
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = trail
    cfg["atr_trail_mult_volatile"] = trail
    cfg["atr_min"] = 0.0
    cfg["volume_mult"] = 1.0
    cfg["volume_max_mult"] = None
    cfg["min_rr_ratio"] = 0
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"]["enabled"] = False
    # Disable all signals, enable only the selected one
    cfg["signals"] = copy.deepcopy(SIGNAL_DEFAULTS)
    cfg["signals"][strategy_key] = {"enabled": True}
    # Ichimoku params
    if strategy_key in ("ichimoku_cloud", "ema_ichimoku_hybrid", "ichi_supertrend"):
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52
    return cfg


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _symbol_prefix(coin: str) -> str:
    """Return lowercase data file prefix, e.g. 'BTC' -> 'btcusdt'."""
    return f"{coin.lower()}usdt"


def _find_data_file(coin: str, tf: str) -> Optional[Path]:
    """Return the best available data file for coin + timeframe."""
    prefix = _symbol_prefix(coin)
    candidates = [
        DATA_DIR / f"{prefix}_{tf}_2y.csv",
        DATA_DIR / f"{prefix}_{tf}_5y.csv",
        DATA_DIR / f"{prefix}_{tf}_1y.csv",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def load_signal_and_trend(
    coin: str,
    tf_signal: str,
    tf_trend: Optional[str] = None,
) -> tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Load signal + trend DataFrames, resampling to 4h when needed."""
    sig_file = _find_data_file(coin, "1h") if tf_signal == "4h" else _find_data_file(coin, tf_signal)
    if sig_file is None:
        return None, None

    sig_df = load_ohlcv(str(sig_file))

    # Resample 1h -> 4h when strategy timeframe is 4h
    if tf_signal == "4h":
        sig_df = _resample_4h(sig_df)

    # Trend data: same as signal for non-EMA strategies; 1h for EMA 15m
    trend_df: Optional[pd.DataFrame] = None
    if tf_trend and tf_trend != tf_signal:
        trend_file = _find_data_file(coin, tf_trend)
        if trend_file:
            trend_df = load_ohlcv(str(trend_file))

    return sig_df, trend_df


def _resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1h OHLCV to 4h bars."""
    ohlcv = df[["open", "high", "low", "close", "volume"]].resample("4h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return ohlcv


def filter_last_n_years(df: pd.DataFrame, years: float) -> pd.DataFrame:
    """Return only the last N years of a DataFrame."""
    if df.empty:
        return df
    cutoff = df.index[-1] - pd.DateOffset(years=years)
    return df[df.index >= cutoff].copy()


# ---------------------------------------------------------------------------
# Bot registry: (label, coin, config_path_or_None, inline_config_or_None)
# ---------------------------------------------------------------------------

class BotSpec:
    """Describes a single bot to be backtested."""

    def __init__(
        self,
        label: str,
        coin: str,
        strategy_label: str,
        config_file: Optional[str] = None,
        inline_cfg: Optional[dict] = None,
    ) -> None:
        self.label = label
        self.coin = coin
        self.strategy_label = strategy_label
        self.config_file = config_file        # relative path under ROOT
        self.inline_cfg = inline_cfg          # or inline dict


def _load_config_file(rel_path: str) -> Optional[dict]:
    """Load a config JSON; return None if file doesn't exist."""
    p = CONFIG_DIR / rel_path
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Build the full bot list
# ---------------------------------------------------------------------------

def _make_bots() -> list[BotSpec]:
    bots: list[BotSpec] = []

    # ------------------------------------------------------------------
    # EXISTING BOTS — prefer config files, fall back to inline
    # ------------------------------------------------------------------

    # --- EMA 15m ---
    for coin, cfg_file, strat_label in [
        ("BTC",  "config.json",           "EMA 15m"),
        ("WIF",  "config_wif.json",        "EMA 15m"),
        ("DOGE", "config_doge.json",       "EMA 15m"),
        ("ARB",  "config_arb.json",        "EMA 15m"),
        ("ARC",  "config_arcusdt_ema.json","EMA 15m"),
    ]:
        cfg = _load_config_file(cfg_file)
        if cfg:
            bots.append(BotSpec(coin, coin, strat_label, config_file=cfg_file))
        else:
            # Fallback inline EMA 15m
            ic = build_config(coin, "ema_crossover", "15m", sl=1.0, tp=3.0, trail=2.0, risk=0.03)
            bots.append(BotSpec(coin, coin, strat_label, inline_cfg=ic))

    # --- Ichimoku 1H (original + mass expansion) ---
    ichi1h_coins = [
        ("AVAX",     "config_avax_ichi.json",          "Ichi 1H"),
        ("NEAR",     "config_near_ichi.json",           "Ichi 1H"),
        ("SOL",      "config_sol_ichi.json",            "Ichi 1H"),
        ("GUN",      "config_gunusdt_ichi.json",        "Ichi 1H"),
        ("BERA",     "config_berausdt_ichi.json",       "Ichi 1H"),
        ("ATH",      "config_athusdt_ichi.json",        "Ichi 1H"),
        ("ZETA",     "config_zetausdt_ichi.json",       "Ichi 1H"),
        ("ARC_ICHI", "config_arcusdt_ichi.json",        "Ichi 1H"),
        ("ANIME",    "config_animeusdt_ichi.json",      "Ichi 1H"),
        ("TRUMP",    "config_trumpusdt_ichi.json",      "Ichi 1H"),
        ("INJ",      "config_injusdt_ichi.json",        "Ichi 1H"),
        ("XLM",      "config_xlmusdt_ichi.json",        "Ichi 1H"),
        ("1000SHIB", "config_1000shibusdt_ichi.json",   "Ichi 1H"),
        ("TRX",      "config_trxusdt_ichi.json",        "Ichi 1H"),
    ]
    for coin, cfg_file, strat_label in ichi1h_coins:
        cfg = _load_config_file(cfg_file)
        real_coin = coin.replace("_ICHI", "")
        if cfg:
            bots.append(BotSpec(coin, real_coin, strat_label, config_file=cfg_file))
        else:
            ic = build_config(real_coin, "ichimoku_cloud", "1h", sl=2.0, tp=5.0, trail=3.0)
            bots.append(BotSpec(coin, real_coin, strat_label, inline_cfg=ic))

    # --- Ichimoku 4H fixed TP ---
    ichi4h_coins = [
        ("TAO",    "config_taousdt_ichi4h.json",    "Ichi 4H"),
        ("RENDER", "config_renderusdt_ichi4h.json", "Ichi 4H"),
        ("HBAR",   "config_hbarusdt_ichi4h.json",   "Ichi 4H"),
    ]
    for coin, cfg_file, strat_label in ichi4h_coins:
        cfg = _load_config_file(cfg_file)
        if cfg:
            bots.append(BotSpec(coin, coin, strat_label, config_file=cfg_file))
        else:
            ic = build_config(coin, "ichimoku_cloud", "4h", sl=2.0, tp=5.0, trail=3.0)
            bots.append(BotSpec(coin, coin, strat_label, inline_cfg=ic))

    # --- 4H Ichimoku Trailing ---
    trail4h_coins = [
        ("ALGO",  "config_algousdt_ichi4htrail.json",  "4H Trail"),
        ("FET",   "config_fetusdt_ichi4htrail.json",   "4H Trail"),
        ("POL",   "config_polusdt_ichi4htrail.json",   "4H Trail"),
        ("POLYX", "config_polyxusdt_ichi4htrail.json", "4H Trail"),
    ]
    for coin, cfg_file, strat_label in trail4h_coins:
        cfg = _load_config_file(cfg_file)
        if cfg:
            bots.append(BotSpec(coin, coin, strat_label, config_file=cfg_file))
        else:
            ic = build_config(coin, "ichimoku_cloud", "4h", sl=1.5, tp=0, trail=4.0)
            bots.append(BotSpec(coin, coin, strat_label, inline_cfg=ic))

    # --- Supertrend 1H ---
    st_coins = [
        ("MSTR",   "config_mstrusdt_supertrend.json",  "Supertrend"),
        ("XAG",    "config_xagusdt_supertrend.json",   "Supertrend"),
        ("SAHARA", "config_saharausdt_supertrend.json","Supertrend"),
    ]
    for coin, cfg_file, strat_label in st_coins:
        cfg = _load_config_file(cfg_file)
        if cfg:
            bots.append(BotSpec(coin, coin, strat_label, config_file=cfg_file))
        else:
            ic = build_config(coin, "supertrend", "1h", sl=2.5, tp=0, trail=3.0)
            bots.append(BotSpec(coin, coin, strat_label, inline_cfg=ic))

    # --- Vol Expansion Breakout 1H ---
    ve_coins = [
        ("1000PEPE", "config_1000pepeusdt_volexp.json", "VolExp"),
        ("WLD",      "config_wldusdt_volexp.json",      "VolExp"),
    ]
    for coin, cfg_file, strat_label in ve_coins:
        cfg = _load_config_file(cfg_file)
        if cfg:
            bots.append(BotSpec(coin, coin, strat_label, config_file=cfg_file))
        else:
            ic = build_config(coin, "vol_expansion", "1h", sl=2.5, tp=3.0, trail=3.0)
            bots.append(BotSpec(coin, coin, strat_label, inline_cfg=ic))

    # ------------------------------------------------------------------
    # NEW BOTS — prefer auto-generated config files, then inline
    # ------------------------------------------------------------------
    new_coins = [
        # (coin, strategy_key, tf_signal, sl, tp, trail, label, try_config)
        ("APT",   "ema_ichimoku_hybrid", "4h", 1.5, 4.0, 2.0, "EMA+Ichi 4H",  "config_aptusdt_emaichi4h.json"),
        ("LTC",   "ichi_supertrend",     "4h", 2.0, 5.0, 3.0, "Ichi+ST 4H",   "config_ltcusdt_ichist4h.json"),
        ("LIGHT", "alligator",           "1h", 2.0, 4.0, 3.0, "Alligator 1H", "config_lightusdt_alligator.json"),
        ("QNT",   "alligator",           "4h", 2.0, 4.0, 3.0, "Alligator 4H", "config_qntusdt_alligator4h.json"),
        ("LYN",   "dual_supertrend",     "1h", 2.5, 4.0, 3.0, "Dual ST 1H",   "config_lynusdt_dualst.json"),
        ("SIGN",  "ichimoku_cloud",      "4h", 2.0, 4.0, 3.0, "Ichi 4H",      "config_signusdt_ichi4h.json"),
        ("ENJ",   "dual_supertrend",     "4h", 2.5, 4.0, 3.0, "Dual ST 4H",   "config_enjusdt_dualst4h.json"),
        ("TIA",   "ichimoku_cloud",      "4h", 2.0, 4.0, 3.0, "Ichi 4H",      "config_tiausdt_ichi4h.json"),
        ("XPL",   "dual_supertrend",     "4h", 2.5, 4.0, 3.0, "Dual ST 4H",   None),
        ("IP",    "ichimoku_cloud",      "1h", 2.0, 4.0, 3.0, "Ichi 1H",      "config_ipusdt_ichi.json"),
        ("ONDO",  "ichimoku_cloud",      "4h", 2.0, 4.0, 3.0, "Ichi 4H",      "config_ondousdt_ichi4h.json"),
        ("LINK",  "alligator",           "4h", 2.0, 4.0, 3.0, "Alligator 4H", "config_linkusdt_alligator4h.json"),
        ("XRP",   "dual_supertrend",     "4h", 2.5, 4.0, 3.0, "Dual ST 4H",   None),
        ("SUI",   "alligator",           "4h", 2.0, 4.0, 3.0, "Alligator 4H", "config_suiusdt_alligator4h.json"),
        ("AKT",   "dual_supertrend",     "4h", 2.5, 4.0, 3.0, "Dual ST 4H",   "config_aktusdt_dualst4h.json"),
        ("H",     "ichimoku_cloud",      "4h", 2.0, 4.0, 3.0, "Ichi 4H",      "config_husdt_ichi4h.json"),
        ("HUMA",  "dual_supertrend",     "1h", 2.5, 4.0, 3.0, "Dual ST 1H",   "config_humausdt_dualst.json"),
        ("AXS",   "ichimoku_cloud",      "4h", 2.0, 4.0, 3.0, "Ichi 4H",      "config_axsusdt_ichi4h.json"),
    ]

    for coin, strat_key, tf, sl, tp, trail, strat_label, try_cfg in new_coins:
        cfg = _load_config_file(try_cfg) if try_cfg else None
        if cfg:
            bots.append(BotSpec(coin, coin, strat_label, config_file=try_cfg))
        else:
            ic = build_config(coin, strat_key, tf, sl=sl, tp=tp, trail=trail)
            bots.append(BotSpec(coin, coin, strat_label, inline_cfg=ic))

    return bots


# ---------------------------------------------------------------------------
# Run a single bot backtest
# ---------------------------------------------------------------------------

def run_bot(spec: BotSpec) -> Optional[dict]:
    """
    Run a 1-year backtest for a single bot.

    Returns a result dict with metrics + trade list, or None on data failure.
    """
    # Resolve config
    if spec.config_file:
        cfg = _load_config_file(spec.config_file)
        if cfg is None:
            print(f"  [SKIP] {spec.label}: config file not found ({spec.config_file})")
            return None
    else:
        cfg = spec.inline_cfg

    # Determine timeframes from config
    tf_signal = cfg.get("timeframe_signal", "1h")
    tf_trend  = cfg.get("timeframe_trend", tf_signal)

    # Map SOL special case (only 5y file, prefix differs slightly)
    load_tf_signal = tf_signal
    load_tf_trend  = tf_trend

    # Load signal data
    if tf_signal == "4h":
        # We always resample from 1h
        sig_file = _find_data_file(spec.coin, "1h")
    else:
        sig_file = _find_data_file(spec.coin, tf_signal)

    if sig_file is None:
        print(f"  [SKIP] {spec.label}: no signal data ({spec.coin} {tf_signal})")
        return None

    sig_df_raw = load_ohlcv(str(sig_file))

    if tf_signal == "4h":
        sig_df = _resample_4h(sig_df_raw)
    else:
        sig_df = sig_df_raw.copy()

    # Load trend data (only for EMA 15m bots where trend != signal TF)
    trend_df: Optional[pd.DataFrame] = None
    if tf_trend != tf_signal and tf_trend != "4h":
        trend_file = _find_data_file(spec.coin, tf_trend)
        if trend_file:
            trend_df = load_ohlcv(str(trend_file))

    if sig_df.empty or len(sig_df) < 100:
        print(f"  [SKIP] {spec.label}: insufficient data ({len(sig_df)} rows)")
        return None

    # Filter to last 1 year
    sig_1y = filter_last_n_years(sig_df, 1.0)
    trend_1y = filter_last_n_years(trend_df, 1.0) if trend_df is not None else None

    if sig_1y.empty or len(sig_1y) < 50:
        print(f"  [SKIP] {spec.label}: insufficient 1yr data ({len(sig_1y)} rows)")
        return None

    # Run engine — suppress its stdout print
    import io
    from contextlib import redirect_stdout

    engine = BacktestEngine(cfg, initial_balance=INITIAL_BALANCE)

    with redirect_stdout(io.StringIO()):
        metrics = engine.run(sig_1y, trend_data=trend_1y)

    # Collect trades with timestamps for report generation
    trades_raw = [vars(t) for t in engine.state.trades]

    return {
        "label":           spec.label,
        "coin":            spec.coin,
        "strategy":        spec.strategy_label,
        "profit_factor":   metrics.profit_factor,
        "win_rate":        metrics.win_rate,
        "total_trades":    metrics.total_trades,
        "sharpe":          metrics.sharpe_ratio,
        "max_dd":          metrics.max_drawdown,
        "monthly_returns": metrics.monthly_returns,
        "trades":          trades_raw,
        "final_balance":   engine.state.balance,
        "pnl":             engine.state.balance - INITIAL_BALANCE,
        "roi_pct":         (engine.state.balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100,
    }


# ---------------------------------------------------------------------------
# Report generators
# ---------------------------------------------------------------------------

def report_per_bot(results: list[dict]) -> str:
    """Report 1: per-bot summary table."""
    lines = [
        "",
        "=" * 90,
        f"PORTFOLIO BACKTEST — 1 Year, ${INITIAL_BALANCE:.0f}/bot, {len(results)} bots",
        f"(${INITIAL_BALANCE * len(results):,.0f} total deployed capital)",
        "=" * 90,
        f"{'Bot':<12} {'Strategy':<16} {'PF':>5} {'WR%':>6} {'Tr':>4} {'PnL$':>8} {'ROI%':>7} {'DD%':>6} {'Sharpe':>7}",
        "-" * 90,
    ]

    total_pnl = 0.0
    total_trades = 0
    total_initial = 0.0
    all_wins: list[float] = []
    all_losses: list[float] = []

    for r in sorted(results, key=lambda x: -x["pnl"]):
        pf_str = f"{r['profit_factor']:.2f}" if r["profit_factor"] < 100 else "inf"
        lines.append(
            f"{r['label']:<12} {r['strategy']:<16} {pf_str:>5} "
            f"{r['win_rate']*100:>5.1f}% {r['total_trades']:>4} "
            f"{r['pnl']:>+8.2f} {r['roi_pct']:>+6.1f}% "
            f"{r['max_dd']*100:>5.1f}% {r['sharpe']:>7.2f}"
        )
        total_pnl += r["pnl"]
        total_trades += r["total_trades"]
        total_initial += INITIAL_BALANCE
        for t in r["trades"]:
            if t["pnl"] > 0:
                all_wins.append(t["pnl"])
            elif t["pnl"] < 0:
                all_losses.append(t["pnl"])

    # Portfolio aggregates
    total_equity = total_initial + total_pnl
    portfolio_roi = total_pnl / total_initial * 100
    total_wr = len(all_wins) / (len(all_wins) + len(all_losses)) * 100 if (all_wins or all_losses) else 0
    gross_profit = sum(all_wins)
    gross_loss = abs(sum(all_losses))
    portfolio_pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    lines += [
        "-" * 90,
        f"{'TOTAL':<12} {'':<16} {portfolio_pf:>5.2f} {total_wr:>5.1f}% "
        f"{total_trades:>4} {total_pnl:>+8.2f} {portfolio_roi:>+6.1f}%",
        f"",
        f"Starting capital: ${total_initial:,.0f}  |  Final equity: ${total_equity:,.2f}  |  Net PnL: ${total_pnl:+,.2f}",
        "=" * 90,
    ]
    return "\n".join(lines)


def report_monthly(results: list[dict]) -> str:
    """Report 2: monthly portfolio returns."""
    # Aggregate PnL by month across all bots
    monthly_totals: dict[str, float] = defaultdict(float)
    monthly_bot_wins: dict[str, int] = defaultdict(int)
    monthly_bot_count: dict[str, int] = defaultdict(int)

    for r in results:
        for m in r["monthly_returns"]:
            key = m["month"]
            monthly_totals[key] += m["pnl"]
            monthly_bot_count[key] += 1
            if m["pnl"] >= 0:
                monthly_bot_wins[key] += 1

    if not monthly_totals:
        return "\nNo monthly data available."

    sorted_months = sorted(monthly_totals.keys())
    cumul = 0.0
    total_initial = INITIAL_BALANCE * len(results)

    lines = [
        "",
        "=" * 70,
        "MONTHLY PORTFOLIO RETURNS",
        "=" * 70,
        f"{'Month':<10} {'PnL$':>10} {'Cumul$':>10} {'ROI%':>7} {'WinBots':>8} {'TotalBots':>10}",
        "-" * 70,
    ]

    for month in sorted_months:
        pnl = monthly_totals[month]
        cumul += pnl
        roi = pnl / total_initial * 100
        wins = monthly_bot_wins[month]
        total = monthly_bot_count[month]
        lines.append(
            f"{month:<10} {pnl:>+10.2f} {cumul:>+10.2f} {roi:>+6.2f}% {wins:>8}/{total:<10}"
        )

    lines.append("=" * 70)
    return "\n".join(lines)


def report_daily(results: list[dict]) -> str:
    """Report 3: daily returns for the last 2 months."""
    # Build per-date PnL map from trade exit_time
    daily_pnl: dict[str, float] = defaultdict(float)
    daily_trades: dict[str, int] = defaultdict(int)
    daily_best: dict[str, tuple[float, str]] = {}   # date -> (pnl, label)
    daily_worst: dict[str, tuple[float, str]] = {}  # date -> (pnl, label)

    all_dates: set[str] = set()

    for r in results:
        for t in r["trades"]:
            exit_time = t.get("exit_time", "")
            if not exit_time:
                continue
            try:
                date_str = str(pd.Timestamp(exit_time).date())
            except Exception:
                continue

            pnl = t["pnl"]
            daily_pnl[date_str] += pnl
            daily_trades[date_str] += 1
            all_dates.add(date_str)

            if date_str not in daily_best or pnl > daily_best[date_str][0]:
                daily_best[date_str] = (pnl, r["label"])
            if date_str not in daily_worst or pnl < daily_worst[date_str][0]:
                daily_worst[date_str] = (pnl, r["label"])

    if not all_dates:
        return "\nNo daily trade data available."

    # Last 2 months
    sorted_dates = sorted(all_dates)
    cutoff_date = pd.Timestamp(sorted_dates[-1]) - pd.DateOffset(months=2)
    recent_dates = [d for d in sorted_dates if pd.Timestamp(d) >= cutoff_date]

    cumul = sum(v for d, v in daily_pnl.items() if pd.Timestamp(d) < cutoff_date)

    lines = [
        "",
        "=" * 100,
        "DAILY RETURNS (Last 2 months)",
        "=" * 100,
        f"{'Date':<12} {'PnL$':>9} {'Cumul$':>10} {'Tr':>4}  {'Best Bot':^24}  {'Worst Bot':^24}",
        "-" * 100,
    ]

    for date in recent_dates:
        pnl = daily_pnl[date]
        cumul += pnl
        tr = daily_trades[date]
        best_pnl, best_label = daily_best.get(date, (0.0, "-"))
        worst_pnl, worst_label = daily_worst.get(date, (0.0, "-"))
        best_str  = f"{best_label}(+{best_pnl:.2f})"
        worst_str = f"{worst_label}({worst_pnl:+.2f})"
        lines.append(
            f"{date:<12} {pnl:>+9.2f} {cumul:>+10.2f} {tr:>4}  {best_str:<24}  {worst_str:<24}"
        )

    lines.append("=" * 100)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    bots = _make_bots()
    print(f"\nRunning {len(bots)}-bot portfolio backtest ({INITIAL_BALANCE:.0f} USDT/bot)...")

    results: list[dict] = []
    skipped: list[str] = []

    for i, spec in enumerate(bots):
        if i > 0 and i % 5 == 0:
            print(f"  Progress: {i}/{len(bots)} bots completed...")

        result = run_bot(spec)
        if result is None:
            skipped.append(spec.label)
        else:
            results.append(result)

    print(f"\nCompleted {len(results)}/{len(bots)} bots. Skipped: {skipped or 'none'}")

    if not results:
        print("ERROR: No results — check data files.")
        return

    # --- Reports ---
    print(report_per_bot(results))
    print(report_monthly(results))
    print(report_daily(results))

    # --- Save JSON ---
    out_path = DATA_DIR / "portfolio_backtest_47.json"

    # Convert trade dicts — strip non-serialisable types
    def _clean(obj):
        if isinstance(obj, (pd.Timestamp, pd.Period)):
            return str(obj)
        if isinstance(obj, float) and (np.isnan(obj) or np.isinf(obj)):
            return None
        return obj

    cleaned_results = []
    for r in results:
        cr = {k: v for k, v in r.items() if k != "trades"}
        cr["trade_count"] = len(r["trades"])
        # Summarise trades (don't dump all trade records to keep JSON small)
        cr["trades_summary"] = [
            {
                "entry_time": str(t.get("entry_time", "")),
                "exit_time":  str(t.get("exit_time",  "")),
                "side":       t.get("side", ""),
                "pnl":        round(t.get("pnl", 0.0), 4),
                "close_reason": t.get("close_reason", ""),
            }
            for t in r["trades"]
        ]
        cleaned_results.append(cr)

    output = {
        "meta": {
            "bots_run":        len(results),
            "bots_skipped":    len(skipped),
            "initial_per_bot": INITIAL_BALANCE,
            "total_initial":   INITIAL_BALANCE * len(results),
            "total_pnl":       round(sum(r["pnl"] for r in results), 2),
            "skipped_labels":  skipped,
        },
        "results": cleaned_results,
    }

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=_clean)

    print(f"\nPortfolio summary saved to {out_path}")


if __name__ == "__main__":
    main()
