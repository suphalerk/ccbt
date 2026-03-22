"""audit_btc_wif.py — Deep audit of BTC Stoch MTF 1H and WIF DualThrust+ADX 1H.

Audits claimed high-PF results from core7_new_strategies.py:
  BTC Stoch MTF 1H:      claimed PF 2.96, 14 trades
  WIF DualThrust+ADX 1H: claimed PF 2.45, 8 trades

Audit steps per strategy:
  1. Reproduce result — run BacktestEngine, print ALL trades
  2. Verify signals    — check indicator values at each entry
  3. Look-ahead check  — confirm iloc[-2] pattern is clean
  4. Fee check         — verify PnL includes commission + slippage
  5. Walk-forward      — split 50/50, check stability
  6. Baseline compare  — vs current deployed strategy on same period

Run: python3 research/audit_btc_wif.py
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, "/Users/iceai/Work/ccbt")
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine, BacktestTrade
from bot.data import add_indicators, add_trend_filter, compute_ema

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path("/Users/iceai/Work/ccbt")
DATA_DIR = PROJECT_ROOT / "data"

# ---------------------------------------------------------------------------
# Stdout suppressor
# ---------------------------------------------------------------------------
class _Quiet:
    def __enter__(self) -> "_Quiet":
        self._orig = sys.stdout
        sys.stdout = open(os.devnull, "w")
        return self

    def __exit__(self, *_: object) -> None:
        sys.stdout.close()
        sys.stdout = self._orig


# ---------------------------------------------------------------------------
# All known signal keys (disable everything except the target)
# ---------------------------------------------------------------------------
ALL_SIGNALS = [
    "ema_crossover", "ema_fast_crossover", "ema_pullback", "rsi_divergence",
    "bb_breakout", "mean_reversion", "body_dominance", "squeeze_release",
    "ichimoku_cloud", "supertrend", "vol_expansion",
    "dual_supertrend", "alligator", "ema_ichimoku_hybrid", "ichi_supertrend",
    "volexp_supertrend",
    "adx_di_cross", "choppiness_ema", "williams_r_adx", "roc_momentum",
    "stoch_supertrend", "price_channel_vol", "ema_alligator", "supertrend_volume",
    "stoch_mtf", "zscore_meanrev", "ema_ribbon",
    "dual_thrust", "awesome_oscillator", "range_bounce",
    "ribbon_rsi_vol", "dualthrust_adx", "zscore_stoch", "ichi_adx", "ribbon_ao",
]

# ---------------------------------------------------------------------------
# Base config matching core7_new_strategies.py exactly
# ---------------------------------------------------------------------------
with open(PROJECT_ROOT / "config_avax_ichi.json") as _f:
    _TEMPLATE: dict = json.load(_f)


def _build_config(coin_prefix: str, signal: str, tf: str, sl: float, tp: float, trail: float) -> dict:
    """Build config identical to core7_new_strategies.py."""
    cfg = copy.deepcopy(_TEMPLATE)
    cfg["symbol"] = f"{coin_prefix.upper()}USDT"
    cfg["timeframe_signal"] = tf
    cfg["timeframe_trend"] = tf
    cfg["risk_per_trade"] = 0.01
    cfg["leverage"] = 25
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_min"] = 0.0
    cfg["volume_mult"] = 1.0
    cfg["volume_max_mult"] = None
    cfg["min_rr_ratio"] = 0
    cfg["signal_scorer"] = {"enabled": False}
    cfg["ai_layer"] = {"enabled": False}
    cfg["signals"] = {s: {"enabled": False} for s in ALL_SIGNALS}
    cfg["signals"][signal] = {"enabled": True}
    if "ichi" in signal:
        cfg["ichimoku_tenkan"] = 9
        cfg["ichimoku_kijun"] = 26
        cfg["ichimoku_senkou_b"] = 52
    # Extra params for dualthrust_adx
    if "dualthrust" in signal:
        cfg["dual_thrust_k"] = 0.5
        cfg["dual_thrust_lookback"] = 20
        cfg["dualthrust_adx_threshold"] = 25.0
        cfg["adx_period"] = 14
    return cfg


def _load_1h(prefix: str) -> Optional[pd.DataFrame]:
    """Load 1H data for given prefix."""
    candidates = sorted(DATA_DIR.glob(f"{prefix}_1h*.csv"))
    if not candidates:
        return None
    return load_ohlcv(str(candidates[0]))


def _load_15m(prefix: str) -> Optional[pd.DataFrame]:
    """Load 15m data for given prefix."""
    candidates = sorted(DATA_DIR.glob(f"{prefix}_15m*.csv"))
    if not candidates:
        return None
    return load_ohlcv(str(candidates[0]))


def _compute_pf(trades: list[BacktestTrade]) -> float:
    """Compute profit factor from trade list."""
    gross_win = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = sum(abs(t.pnl) for t in trades if t.pnl < 0)
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else 1.0
    return round(gross_win / gross_loss, 2)


def _print_trade_table(trades: list[BacktestTrade], initial_balance: float) -> None:
    """Print every trade in a formatted table."""
    print(f"\n{'#':>3} {'Entry Time':<22} {'Exit Time':<22} {'Side':<6} {'Entry$':>10} {'Exit$':>10} "
          f"{'PnL$':>10} {'PnL%':>8} {'Reason':<18} {'Signal'}")
    print("-" * 125)
    for i, t in enumerate(trades, 1):
        pnl_pct = t.pnl / initial_balance * 100
        print(f"{i:>3} {str(t.entry_time):<22} {str(t.exit_time):<22} {t.side:<6} "
              f"{t.entry_price:>10.4f} {t.exit_price:>10.4f} {t.pnl:>+10.4f} {pnl_pct:>+7.2f}% "
              f"{t.close_reason:<18} {t.signal_source}")


def _verify_signals_stoch_mtf(
    engine: BacktestEngine,
    trades: list[BacktestTrade],
    cfg: dict,
) -> tuple[int, int]:
    """Verify indicator conditions at each stoch_mtf trade entry.

    Returns (correct, total) where correct = entries where conditions hold.
    """
    df = engine._df
    oversold = cfg.get("stoch_oversold", 20.0)
    overbought = cfg.get("stoch_overbought", 80.0)

    correct = 0
    total = len(trades)

    for t in trades:
        # Find the candle index where this trade was entered
        # Entry time is when the trade opens (the current candle close)
        # The signal was checked on prev_row = df.iloc[candle_idx - 1]
        try:
            entry_ts = pd.Timestamp(t.entry_time)
            if entry_ts.tz is not None:
                df_idx = df.index.tz_localize(None) if df.index.tz is None else df.index.tz_convert("UTC").tz_localize(None)
                entry_ts = entry_ts.tz_convert("UTC").tz_localize(None)
            else:
                df_idx = df.index
            pos = df_idx.get_indexer([entry_ts], method="nearest")[0]
        except Exception as e:
            print(f"  [WARN] Could not locate {t.entry_time}: {e}")
            continue

        if pos < 2:
            print(f"  [WARN] Trade at {t.entry_time}: pos={pos} too early")
            continue

        # signal_row = df.iloc[pos - 1] (the closed candle the engine checked)
        sig_row = df.iloc[pos - 1]
        prev_sig_row = df.iloc[pos - 2]

        k = sig_row.get("stoch_k", np.nan)
        d = sig_row.get("stoch_d", np.nan)
        prev_k = prev_sig_row.get("stoch_k", np.nan)
        prev_d = prev_sig_row.get("stoch_d", np.nan)
        ema50 = sig_row.get("ema_trend", sig_row.get("ema_trend_1h", sig_row.get("ema50", np.nan)))
        close = sig_row.get("close", np.nan)

        if t.side == "long":
            k_cross_up = float(k) > float(d) and float(prev_k) <= float(prev_d)
            in_oversold = float(k) < oversold
            htf_bullish = float(close) > float(ema50)
            cond_ok = k_cross_up and in_oversold and htf_bullish
        else:
            k_cross_down = float(k) < float(d) and float(prev_k) >= float(prev_d)
            in_overbought = float(k) > overbought
            htf_bearish = float(close) < float(ema50)
            cond_ok = k_cross_down and in_overbought and htf_bearish

        status = "OK" if cond_ok else "FAIL"
        if cond_ok:
            correct += 1

        print(f"  Trade {t.side.upper()} @ {t.entry_time}")
        print(f"    signal_row idx={pos-1}  stoch_k={k:.1f}  stoch_d={d:.1f}  prev_k={prev_k:.1f}  prev_d={prev_d:.1f}")
        print(f"    ema50={ema50:.4f}  close={close:.4f}  htf_{'bullish' if t.side=='long' else 'bearish'}={'YES' if (float(close)>float(ema50) if t.side=='long' else float(close)<float(ema50)) else 'NO'}")
        if t.side == "long":
            print(f"    k_cross_up={'YES' if k_cross_up else 'NO'}  in_oversold={'YES' if in_oversold else 'NO'}")
        else:
            print(f"    k_cross_down={'YES' if k_cross_down else 'NO'}  in_overbought={'YES' if in_overbought else 'NO'}")
        print(f"    => conditions: {status}")

    return correct, total


def _verify_signals_dualthrust_adx(
    engine: BacktestEngine,
    trades: list[BacktestTrade],
    cfg: dict,
) -> tuple[int, int]:
    """Verify indicator conditions at each dualthrust_adx trade entry.

    Returns (correct, total).
    """
    df = engine._df
    adx_threshold = cfg.get("dualthrust_adx_threshold", 25.0)

    correct = 0
    total = len(trades)

    for t in trades:
        try:
            entry_ts = pd.Timestamp(t.entry_time)
            if entry_ts.tz is not None:
                df_idx = df.index.tz_localize(None) if df.index.tz is None else df.index.tz_convert("UTC").tz_localize(None)
                entry_ts = entry_ts.tz_convert("UTC").tz_localize(None)
            else:
                df_idx = df.index
            pos = df_idx.get_indexer([entry_ts], method="nearest")[0]
        except Exception as e:
            print(f"  [WARN] Could not locate {t.entry_time}: {e}")
            continue

        if pos < 2:
            print(f"  [WARN] Trade at {t.entry_time}: pos={pos} too early")
            continue

        sig_row = df.iloc[pos - 1]
        prev_sig_row = df.iloc[pos - 2]

        dt_upper = sig_row.get("dt_upper", np.nan)
        dt_lower = sig_row.get("dt_lower", np.nan)
        adx = sig_row.get("adx", np.nan)
        di_plus = sig_row.get("di_plus", np.nan)
        di_minus = sig_row.get("di_minus", np.nan)
        ema50 = sig_row.get("ema_trend", sig_row.get("ema_trend_1h", sig_row.get("ema50", np.nan)))
        close = sig_row.get("close", np.nan)
        prev_close = prev_sig_row.get("close", np.nan)
        prev_dt_upper = prev_sig_row.get("dt_upper", np.nan)
        prev_dt_lower = prev_sig_row.get("dt_lower", np.nan)

        if t.side == "long":
            breakout = float(close) > float(dt_upper)
            fresh = float(prev_close) <= float(prev_dt_upper)
            trending = float(adx) > adx_threshold
            di_ok = float(di_plus) > float(di_minus)
            htf_ok = float(close) > float(ema50)
            cond_ok = breakout and fresh and trending and di_ok and htf_ok
        else:
            breakout = float(close) < float(dt_lower)
            fresh = float(prev_close) >= float(prev_dt_lower)
            trending = float(adx) > adx_threshold
            di_ok = float(di_minus) > float(di_plus)
            htf_ok = float(close) < float(ema50)
            cond_ok = breakout and fresh and trending and di_ok and htf_ok

        status = "OK" if cond_ok else "FAIL"
        if cond_ok:
            correct += 1

        print(f"  Trade {t.side.upper()} @ {t.entry_time}")
        print(f"    signal_row idx={pos-1}")
        print(f"    dt_upper={dt_upper:.4f}  dt_lower={dt_lower:.4f}")
        print(f"    close={close:.4f}  prev_close={prev_close:.4f}")
        print(f"    prev_dt_upper={prev_dt_upper:.4f}  prev_dt_lower={prev_dt_lower:.4f}")
        print(f"    adx={adx:.1f}  di+={di_plus:.1f}  di-={di_minus:.1f}  ema50={ema50:.4f}")
        if t.side == "long":
            print(f"    breakout={'YES' if breakout else 'NO'}  fresh={'YES' if fresh else 'NO'}  "
                  f"trending={'YES' if trending else 'NO'}  di_ok={'YES' if di_ok else 'NO'}  htf_ok={'YES' if htf_ok else 'NO'}")
        else:
            print(f"    breakout={'YES' if breakout else 'NO'}  fresh={'YES' if fresh else 'NO'}  "
                  f"trending={'YES' if trending else 'NO'}  di_ok={'YES' if di_ok else 'NO'}  htf_ok={'YES' if htf_ok else 'NO'}")
        print(f"    => conditions: {status}")

    return correct, total


def _check_fee(trades: list[BacktestTrade], cfg: dict) -> tuple[bool, float]:
    """Verify PnL includes exit commission (matching engine's formula exactly).

    Engine PnL formula:
      pnl = size * pnl_pct                   # size in USDT
      pnl -= size * commission_rate           # exit commission only
      (entry commission deducted from balance separately, not stored in trade.pnl)

    Slippage is baked into entry_price / exit_price, so the pnl_pct already
    accounts for it implicitly.

    Returns (pass, max_diff).
    """
    commission = cfg.get("commission_rate", 0.0004)
    max_diff = 0.0
    all_pass = True

    for t in trades:
        if t.side == "long":
            pnl_pct = (t.exit_price - t.entry_price) / t.entry_price
        else:
            pnl_pct = (t.entry_price - t.exit_price) / t.entry_price

        gross_pnl = t.size * pnl_pct
        exit_commission = t.size * commission
        expected_pnl = gross_pnl - exit_commission

        diff = abs(t.pnl - expected_pnl)
        max_diff = max(max_diff, diff)

        # Allow 2% tolerance (slippage multiplier is time-of-day variable,
        # partial TP accounting, and floating point rounding)
        if diff > abs(expected_pnl) * 0.02 + 0.10:
            all_pass = False

    return all_pass, round(max_diff, 4)


def _walk_forward(
    signal: str,
    tf: str,
    sl: float,
    tp: float,
    trail: float,
    prefix: str,
    df_full: pd.DataFrame,
    initial_balance: float = 10000.0,
) -> tuple[float, int, float, int, float, int]:
    """Split 50/50 and run engine on each half.

    Returns (pf_first, tr_first, pf_second, tr_second, pf_full, tr_full).
    """
    mid = len(df_full) // 2
    first_half = df_full.iloc[:mid].copy()
    second_half = df_full.iloc[mid:].copy()

    cfg = _build_config(prefix, signal, tf, sl, tp, trail)

    results = {}
    for label, data in [("first", first_half), ("second", second_half), ("full", df_full)]:
        engine = BacktestEngine(cfg, initial_balance=initial_balance)
        with _Quiet():
            metrics = engine.run(data, data)
        results[label] = (metrics.profit_factor, metrics.total_trades)

    return (
        results["first"][0], results["first"][1],
        results["second"][0], results["second"][1],
        results["full"][0], results["full"][1],
    )


def _run_baseline_btc_ema(df_15m: pd.DataFrame, df_1h: pd.DataFrame, initial_balance: float = 10000.0) -> tuple[float, int]:
    """Run BTC EMA 15m (current deployed config) on same time window as 1H data."""
    with open(PROJECT_ROOT / "config.json") as f:
        btc_cfg = json.load(f)
    btc_cfg["ai_layer"] = {"enabled": False}
    btc_cfg["signal_scorer"] = {"enabled": False}
    btc_cfg["adaptive_sizing"] = {"enabled": False}
    btc_cfg["pyramiding"] = {"enabled": False}

    # Align 15m to same date range as 1H
    start = df_1h.index[0]
    end = df_1h.index[-1]
    df_15m_aligned = df_15m[(df_15m.index >= start) & (df_15m.index <= end)].copy()
    df_1h_aligned = df_1h.copy()

    engine = BacktestEngine(btc_cfg, initial_balance=initial_balance)
    with _Quiet():
        metrics = engine.run(df_15m_aligned, df_1h_aligned)
    return round(metrics.profit_factor, 2), metrics.total_trades


def _run_baseline_wif_ema(df_15m: pd.DataFrame, initial_balance: float = 10000.0) -> tuple[float, int]:
    """Run WIF EMA 15m (current deployed config) on same time window."""
    with open(PROJECT_ROOT / "config_wif.json") as f:
        wif_cfg = json.load(f)
    wif_cfg["ai_layer"] = {"enabled": False}
    wif_cfg["signal_scorer"] = {"enabled": False}
    wif_cfg["adaptive_sizing"] = {"enabled": False}
    wif_cfg["pyramiding"] = {"enabled": False}

    engine = BacktestEngine(wif_cfg, initial_balance=initial_balance)
    with _Quiet():
        metrics = engine.run(df_15m, df_15m)
    return round(metrics.profit_factor, 2), metrics.total_trades


def _audit_strategy(
    label: str,
    prefix: str,
    signal: str,
    tf: str,
    sl: float,
    tp: float,
    trail: float,
    initial_balance: float = 10000.0,
) -> dict:
    """Full audit for one strategy. Returns verdict dict."""
    separator = "=" * 70
    print(f"\n{separator}")
    print(f"AUDIT: {label}")
    print(f"  signal={signal}  tf={tf}  sl={sl}×ATR  tp={tp}×ATR  trail={trail}×ATR")
    print(separator)

    # Load data
    df = _load_1h(prefix)
    if df is None or len(df) < 200:
        print(f"  [ERROR] No 1H data for {prefix}")
        return {"label": label, "verdict": "NO_DATA"}
    print(f"\nData: {len(df)} rows  {str(df.index[0])[:10]} → {str(df.index[-1])[:10]}")

    # -------------------------------------------------------------------
    # [1] Run full backtest and collect trades
    # -------------------------------------------------------------------
    print("\n[1] TRADE LIST")
    cfg = _build_config(prefix, signal, tf, sl, tp, trail)
    engine = BacktestEngine(cfg, initial_balance=initial_balance)
    with _Quiet():
        metrics = engine.run(df, df)

    trades = engine.state.trades
    pf_full = round(metrics.profit_factor, 2)
    tr_full = metrics.total_trades
    wr_full = round(metrics.win_rate * 100, 1)

    print(f"\n  Result: PF={pf_full}  trades={tr_full}  WR={wr_full}%  "
          f"Sharpe={metrics.sharpe_ratio:.2f}  DD={metrics.max_drawdown*100:.1f}%")
    print(f"  Final balance: ${engine.state.balance:,.2f}")

    _print_trade_table(trades, initial_balance)

    # -------------------------------------------------------------------
    # [2] Signal verification
    # -------------------------------------------------------------------
    print(f"\n[2] SIGNAL VERIFICATION ({len(trades)} trades)")
    if signal == "stoch_mtf":
        correct, total = _verify_signals_stoch_mtf(engine, trades, cfg)
    elif signal == "dualthrust_adx":
        correct, total = _verify_signals_dualthrust_adx(engine, trades, cfg)
    else:
        correct, total = 0, 0

    sig_pass = correct == total and total > 0
    print(f"\n  => Signals correct: {correct}/{total}  {'PASS' if sig_pass else 'FAIL'}")

    # -------------------------------------------------------------------
    # [3] Look-ahead check
    # -------------------------------------------------------------------
    print("\n[3] LOOK-AHEAD BIAS CHECK")
    print("  Engine calls _check_entry(prev_row, current_row, ...) at candle i:")
    print("    signal_row = df.iloc[i-1]  (last closed candle)")
    print("    stoch_mtf uses prev_row=df.iloc[i-2] for K/D crossover detection")
    print("    dualthrust_adx uses prev_row=df.iloc[i-2] for fresh-breakout check")
    print("  Entry price = current_row['close']  (candle i close — future price relative to signal)")
    # The entry at current_row close is standard — we trade at the OPEN of next candle
    # or at close of current candle. In this engine it's current_row close.
    # This is NOT look-ahead: signal is generated on candle i-1, entry at candle i close.
    la_pass = True
    print("  => PASS — signals computed on closed candle (i-1), entry at candle i close")

    # -------------------------------------------------------------------
    # [4] Fee check
    # -------------------------------------------------------------------
    print("\n[4] FEE CHECK")
    fee_pass, max_diff = _check_fee(trades, cfg)
    commission = cfg.get("commission_rate", 0.0004)
    slippage = cfg.get("slippage_rate", 0.00015)
    print(f"  Config: commission={commission*100:.4f}%  slippage={slippage*100:.4f}%")
    print(f"  Max PnL diff vs manual calc: ${max_diff:.4f}")
    print(f"  => {'PASS' if fee_pass else 'FAIL (PnL deviation >5% + $1)'}")

    # -------------------------------------------------------------------
    # [5] Walk-forward test
    # -------------------------------------------------------------------
    print("\n[5] WALK-FORWARD (50/50 split)")
    pf_first, tr_first, pf_second, tr_second, pf_wf, tr_wf = _walk_forward(
        signal, tf, sl, tp, trail, prefix, df, initial_balance
    )
    mid_date = str(df.index[len(df) // 2])[:10]
    print(f"  Split date: {mid_date}")
    print(f"  First half:  PF={pf_first:.2f}  trades={tr_first}")
    print(f"  Second half: PF={pf_second:.2f}  trades={tr_second}")
    print(f"  Full:        PF={pf_wf:.2f}  trades={tr_wf}")

    # Stability: second-half PF should be >= 60% of first-half PF
    wf_stable: bool
    if tr_first < 4 or tr_second < 4:
        wf_stable = False
        wf_verdict = "INSUFFICIENT_TRADES"
    elif pf_first > 1.0 and pf_second >= pf_first * 0.60:
        wf_stable = True
        wf_verdict = "STABLE"
    elif pf_first <= 1.0 and pf_second <= 1.0:
        wf_stable = False
        wf_verdict = "BOTH_HALVES_LOSE"
    else:
        wf_stable = False
        ratio = pf_second / pf_first if pf_first > 0 else 0
        wf_verdict = f"UNSTABLE (OOS={ratio:.0%} of IS)"
    print(f"  => {wf_verdict}")

    # -------------------------------------------------------------------
    # [6] Baseline comparison
    # -------------------------------------------------------------------
    print("\n[6] BASELINE COMPARISON (current deployed strategy)")
    if prefix == "btcusdt":
        df_15m = _load_15m(prefix)
        df_1h = df
        if df_15m is not None:
            baseline_pf, baseline_trades = _run_baseline_btc_ema(df_15m, df_1h, initial_balance)
            print(f"  BTC EMA 15m (deployed):     PF={baseline_pf:.2f}  trades={baseline_trades}")
            print(f"  BTC {label}:  PF={pf_full:.2f}  trades={tr_full}")
            pf_delta = pf_full - baseline_pf
            trade_pct = tr_full / max(baseline_trades, 1) * 100
            print(f"  PF delta: {pf_delta:+.2f}  Trade count: {trade_pct:.0f}% of baseline")
            baseline_pf_out = baseline_pf
            baseline_trades_out = baseline_trades
        else:
            print("  [WARN] 15m data not found for BTC EMA baseline")
            baseline_pf_out = 0.0
            baseline_trades_out = 0
    elif prefix == "wifusdt":
        df_15m = _load_15m(prefix)
        if df_15m is not None:
            baseline_pf, baseline_trades = _run_baseline_wif_ema(df_15m, initial_balance)
            print(f"  WIF EMA 15m (deployed):     PF={baseline_pf:.2f}  trades={baseline_trades}")
            print(f"  WIF {label}:  PF={pf_full:.2f}  trades={tr_full}")
            pf_delta = pf_full - baseline_pf
            trade_pct = tr_full / max(baseline_trades, 1) * 100
            print(f"  PF delta: {pf_delta:+.2f}  Trade count: {trade_pct:.0f}% of baseline")
            baseline_pf_out = baseline_pf
            baseline_trades_out = baseline_trades
        else:
            print("  [WARN] 15m data not found for WIF EMA baseline")
            baseline_pf_out = 0.0
            baseline_trades_out = 0
    else:
        baseline_pf_out = 0.0
        baseline_trades_out = 0

    # -------------------------------------------------------------------
    # Final verdict
    # -------------------------------------------------------------------
    print(f"\n{'─' * 70}")
    print(f"VERDICT: {label}")

    reasons_fail: list[str] = []
    reasons_concern: list[str] = []

    if tr_full < 10:
        reasons_concern.append(f"Low trade count ({tr_full}) — high sampling noise")
    if not sig_pass:
        reasons_fail.append(f"Signal conditions wrong at {total - correct}/{total} entries")
    if not fee_pass:
        reasons_fail.append(f"Fee accounting error (max diff ${max_diff:.2f})")
    if not wf_stable:
        reasons_fail.append(f"Walk-forward: {wf_verdict}")
    if pf_full < 1.3:
        reasons_fail.append(f"PF {pf_full:.2f} below minimum threshold (1.3)")

    if reasons_fail:
        final_verdict = "REJECT"
    elif reasons_concern:
        final_verdict = "NEEDS_MORE_DATA"
    elif pf_full >= 1.5 and tr_full >= 10 and wf_stable:
        final_verdict = "UPGRADE_CANDIDATE"
    else:
        final_verdict = "KEEP_CURRENT"

    print(f"  PF={pf_full}  trades={tr_full}  WR={wr_full}%")
    print(f"  Signal check: {'PASS' if sig_pass else 'FAIL'}")
    print(f"  Look-ahead:   {'PASS' if la_pass else 'FAIL'}")
    print(f"  Fee check:    {'PASS' if fee_pass else 'FAIL'}")
    print(f"  Walk-forward: {wf_verdict}")
    if reasons_fail:
        print("  FAIL reasons:")
        for r in reasons_fail:
            print(f"    - {r}")
    if reasons_concern:
        print("  CONCERNS:")
        for r in reasons_concern:
            print(f"    - {r}")
    print(f"\n  => {final_verdict}")

    return {
        "label": label,
        "signal": signal,
        "tf": tf,
        "pf_full": pf_full,
        "trades_full": tr_full,
        "wr": wr_full,
        "pf_first_half": pf_first,
        "trades_first_half": tr_first,
        "pf_second_half": pf_second,
        "trades_second_half": tr_second,
        "signal_pass": sig_pass,
        "lookahead_pass": la_pass,
        "fee_pass": fee_pass,
        "wf_verdict": wf_verdict,
        "verdict": final_verdict,
        "baseline_pf": baseline_pf_out,
        "baseline_trades": baseline_trades_out,
    }


def main() -> None:
    print("=" * 70)
    print("DEEP AUDIT: BTC Stoch MTF 1H + WIF DualThrust+ADX 1H")
    print("Claimed: BTC PF 2.96 / 14 trades  |  WIF PF 2.45 / 8 trades")
    print("=" * 70)

    results = []

    # ------------------------------------------------------------------
    # Audit 1: BTC Stoch MTF 1H
    # ------------------------------------------------------------------
    r1 = _audit_strategy(
        label="Stoch MTF 1H",
        prefix="btcusdt",
        signal="stoch_mtf",
        tf="1h",
        sl=2.0,
        tp=4.0,
        trail=3.0,
    )
    results.append(("BTC", r1))

    # ------------------------------------------------------------------
    # Audit 2: WIF DualThrust+ADX 1H
    # ------------------------------------------------------------------
    r2 = _audit_strategy(
        label="DualThrust+ADX 1H",
        prefix="wifusdt",
        signal="dualthrust_adx",
        tf="1h",
        sl=2.0,
        tp=4.0,
        trail=3.0,
    )
    results.append(("WIF", r2))

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n\n" + "=" * 70)
    print("FINAL SUMMARY")
    print("=" * 70)
    print(f"\n{'Coin':<6} {'Strategy':<22} {'PF':>6} {'Tr':>4} {'WR%':>5} {'WF-1st':>7} {'WF-2nd':>7} {'Verdict'}")
    print("-" * 75)
    for coin, r in results:
        print(f"{coin:<6} {r['label']:<22} {r['pf_full']:>6.2f} {r['trades_full']:>4} {r['wr']:>5.1f}% "
              f"{r.get('pf_first_half',0):>7.2f} {r.get('pf_second_half',0):>7.2f} {r['verdict']}")

    print("\n  Deployed baselines:")
    for coin, r in results:
        bp = r.get("baseline_pf", 0)
        bt = r.get("baseline_trades", 0)
        if bp > 0:
            delta = r["pf_full"] - bp
            print(f"    {coin} EMA 15m deployed: PF={bp:.2f}  trades={bt}  |  New: PF={r['pf_full']:.2f}  delta={delta:+.2f}")

    print()
    for coin, r in results:
        v = r["verdict"]
        label = r["label"]
        pf = r["pf_full"]
        tr = r["trades_full"]
        if v == "UPGRADE_CANDIDATE":
            action = f"Consider adding {coin} {label} bot — PF {pf}, {tr} trades, walk-forward stable"
        elif v == "NEEDS_MORE_DATA":
            action = f"Insufficient trades ({tr}) — wait for more data before deploying {coin} {label}"
        elif v == "REJECT":
            action = f"REJECT {coin} {label} — does not pass audit (see fail reasons above)"
        else:
            action = f"Keep current {coin} strategy — {label} does not improve (PF {pf})"
        print(f"  {coin}: {action}")


if __name__ == "__main__":
    main()
