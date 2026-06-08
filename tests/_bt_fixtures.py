"""Deterministic OHLCV + config builders for backtest tests.

ALL helpers in this module are test-only utilities. They must never be imported
by production code.

Key exports
-----------
make_ohlcv(n, ...)
    Build a deterministic DataFrame of n closed candles with configurable
    price-trend, ATR, and funding-rate columns.

make_trading_ohlcv(n, ...)
    Build an alternating bull/bear raw OHLCV DataFrame that produces genuine
    EMA(9)/EMA(21) crossovers after add_indicators() runs inside run().
    Use this for end-to-end run()-based tests that need len(trades) > 0.

base_config(**overrides)
    Return a minimal BacktestEngine config dict with all optional features OFF.

trading_config(**overrides)
    Like base_config() but with RSI/volume/slope filters relaxed so
    ema_crossover signals fire on make_trading_ohlcv() fixtures.

open_position(engine, side, entry_price, stop_loss, take_profit, ...)
    Build a BacktestTrade, seed engine.state, and charge entry commission so
    the engine is in a self-consistent mid-trade state ready for _check_exit /
    _close_position tests.  A field rename here is the ONLY place to fix.
"""

from __future__ import annotations

import copy
import math
from typing import Optional

import pandas as pd

from backtest.engine import BacktestEngine, BacktestState, BacktestTrade


# ---------------------------------------------------------------------------
# OHLCV builder
# ---------------------------------------------------------------------------

def make_ohlcv(
    n: int = 20,
    *,
    base_price: float = 100.0,
    trend: float = 0.0,
    atr_value: float = 1.0,
    funding_rate: float = 0.0,
    start: str = "2024-01-01",
    freq: str = "1h",
    tz_naive: bool = True,
) -> pd.DataFrame:
    """Build a deterministic OHLCV DataFrame.

    Each candle i has:
        close  = base_price + i * trend
        open   = close - atr_value * 0.3   (mild bullish body)
        high   = close + atr_value * 0.5
        low    = close - atr_value * 0.5
        volume = 1000.0
        atr    = atr_value  (pre-computed, skips warmup)
        fundingRate = funding_rate

    tz_naive=True strips any timezone so pandas merges work cleanly.
    """
    idx = pd.date_range(start, periods=n, freq=freq)
    if tz_naive:
        idx = idx.tz_localize(None)

    closes = [base_price + i * trend for i in range(n)]
    rows = []
    for c in closes:
        rows.append({
            "open":  c - atr_value * 0.3,
            "high":  c + atr_value * 0.5,
            "low":   c - atr_value * 0.5,
            "close": c,
            "volume": 1000.0,
            "atr":   atr_value,
            "fundingRate": funding_rate,
        })

    df = pd.DataFrame(rows, index=idx)
    return df


def make_trading_ohlcv(
    n: int = 300,
    *,
    base_price: float = 1000.0,
    move_per_candle: float = 2.0,
    leg_length: int = 50,
    start: str = "2024-01-01",
    freq: str = "1h",
) -> pd.DataFrame:
    """Build an alternating bull/bear OHLCV fixture that produces real EMA crossovers.

    Design: price alternates between +move/candle (bull leg) and -move/candle (bear leg)
    for leg_length candles each.  A small sine wobble creates realistic candle bodies
    without disrupting the trend.  After add_indicators() runs inside engine.run(),
    EMA9 crosses EMA21 roughly 15-20 candles into each regime flip, producing
    at least 2 genuine entries per 100 candles.

    This is the same fixture used in test_backtest_snapshot.py (TestGoldenSnapshot).
    Use it for end-to-end run() tests that need len(trades) >= 2.

    Parameters
    ----------
    n:              Total candles (>=200 recommended for several crossovers).
    base_price:     Starting close price.
    move_per_candle: Absolute price move per candle in each leg.
    leg_length:     Candles per bull/bear leg (50 = several crossovers per 300 bars).
    start:          ISO date for index start.
    freq:           Candle frequency (e.g. "1h", "4h").
    """
    idx = pd.date_range(start, periods=n, freq=freq, tz=None)

    prices = []
    base = base_price
    for i in range(n):
        cycle_pos = i % (leg_length * 2)
        move = +move_per_candle if cycle_pos < leg_length else -move_per_candle
        base += move
        wobble = (move_per_candle * 0.25) * math.sin(2 * math.pi * i / 7)
        prices.append(base + wobble)

    rows = []
    for c in prices:
        half_atr = move_per_candle  # High/low bracket wider than wobble
        rows.append({
            "open":   c - move_per_candle * 0.5,
            "high":   c + half_atr,
            "low":    c - half_atr,
            "close":  c,
            "volume": 1000.0,
        })

    return pd.DataFrame(rows, index=idx)


def make_candle(
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    atr: float = 1.0,
    volume: float = 1000.0,
    timestamp: str = "2024-01-02 00:00",
) -> pd.Series:
    """Build a single candle Series (for white-box _check_exit tests)."""
    return pd.Series(
        {
            "open":  open_price,
            "high":  high,
            "low":   low,
            "close": close,
            "volume": volume,
            "atr":   atr,
            "fundingRate": 0.0,
            "regime": "trending",
        },
        name=pd.Timestamp(timestamp),
    )


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

_BASE_CFG: dict = {
    # Exchange / risk
    "exchange": "binance",
    "symbol": "BTCUSDT",
    "leverage": 3,
    "risk_per_trade": 0.01,
    "max_daily_loss": 0.30,
    "max_positions": 2,
    "max_consecutive_losses": 20,
    "cooldown_hours": 0,
    "max_api_errors": 99,
    "use_testnet": True,
    "timeframe_signal": "1h",
    "timeframe_trend": "1h",
    # Indicators
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
    "atr_sl_mult": 1.5,
    "atr_tp_mult": 3.0,
    "atr_trail_mult": 2.0,
    "atr_trail_mult_trending": 2.0,
    "atr_trail_mult_ranging": 1.5,
    "atr_trail_mult_volatile": 2.5,
    "volume_mult": 1.0,
    "volume_max_mult": None,
    "min_rr_ratio": 0,
    "commission_rate": 0.00055,
    "slippage_rate": 0.0002,
    "crossover_lookback": 2,
    "ema_slope_period": 5,
    "ema_slope_min": 0.0,
    "regime_lookback": 20,
    # Weekend
    "weekend_trading_enabled": True,
    "weekend_size_reduction": 0.5,
    # All optional features OFF
    "partial_tp_enabled": False,
    "partial_tp_pct": 0.5,
    "partial_tp_atr_mult": 2.0,
    "move_sl_to_be_after_tp1": True,
    "breakeven_buffer_atr_mult": 0.0,
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "adaptive_sizing": {"enabled": False},
    "flexible_cooldown": {"enabled": False},
    "signal_scorer": {"enabled": False},
    "regime_filter": {"enabled": False},
    "regime_adaptive_exit": False,
    "cooldown_candles_after_close": 0,
    "cooldown_candles_after_sl": 0,
    # Trading hours (disable filtering in tests)
    "trading_hours": {"enabled": False},
    # Signals: all OFF except ema_crossover which callers can flip
    "signals": {
        "ema_crossover": {"enabled": True},
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
        "dual_thrust": {"enabled": False},
        "stoch_mtf": {"enabled": False},
        "zscore_meanrev": {"enabled": False},
        "awesome_oscillator": {"enabled": False},
        "range_bounce": {"enabled": False},
        "ema_ribbon": {"enabled": False},
        "ichi_adx": {"enabled": False},
        "ribbon_ao": {"enabled": False},
        "zscore_stoch": {"enabled": False},
        "stoch_supertrend": {"enabled": False},
        "supertrend_volume": {"enabled": False},
        "dualthrust_adx": {"enabled": False},
        "pin_bar": {"enabled": False},
        "engulfing": {"enabled": False},
        "inside_bar_breakout": {"enabled": False},
        "adx_di_cross": {"enabled": False},
        "choppiness_ema": {"enabled": False},
        "williams_r_adx": {"enabled": False},
        "roc_momentum": {"enabled": False},
        "price_channel_vol": {"enabled": False},
        "ema_alligator": {"enabled": False},
        "ribbon_rsi_vol": {"enabled": False},
    },
}


def base_config(**overrides) -> dict:
    """Return a minimal engine config with all optional features OFF.

    Pass keyword overrides to adjust individual keys.  Nested dicts are
    shallow-merged at the top level only — pass a complete replacement for
    nested dicts like ``signals`` or ``pyramiding`` if you need to change them.

    Example::
        cfg = base_config(risk_per_trade=0.02, partial_tp_enabled=True)
    """
    cfg = copy.deepcopy(_BASE_CFG)
    cfg.update(overrides)
    return cfg


def trading_config(**overrides) -> dict:
    """Like base_config() but RSI/volume/slope filters relaxed so signals fire.

    Use with make_trading_ohlcv() in end-to-end run() tests that need
    len(trades) >= 2.  The relaxed filters match those in test_backtest_snapshot.py.

    Relaxed defaults:
        rsi_min=0, rsi_max=100 (all RSI values pass)
        rsi_long_min=0, rsi_long_max=100
        rsi_short_min=0, rsi_short_max=100
        volume_mult=0.0 (no volume requirement)
        ema_slope_min=0.0 (no slope requirement)
    """
    base = base_config(
        rsi_min=0,
        rsi_max=100,
        rsi_long_min=0,
        rsi_long_max=100,
        rsi_short_min=0,
        rsi_short_max=100,
        volume_mult=0.0,
        ema_slope_min=0.0,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# open_position helper
# ---------------------------------------------------------------------------

def open_position(
    engine: BacktestEngine,
    *,
    side: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    size: float = 100.0,
    tp1_price: Optional[float] = None,
    tp1_hit: bool = False,
    entry_time: str = "2024-01-01 12:00",
    risk_amount: Optional[float] = None,
    signal_source: str = "ema_crossover",
    avg_entry_price: Optional[float] = None,
    partial_pnl: float = 0.0,
) -> BacktestTrade:
    """Build a BacktestTrade and seed engine.state so _check_exit/_close_position work.

    Also charges entry commission against engine.state.balance to keep the
    accounting self-consistent (mirrors what _check_entry does at line 946).

    Parameters
    ----------
    engine:       The BacktestEngine instance whose state to seed.
    side:         "long" or "short".
    entry_price:  Fill price (post-slippage, as the engine stores it).
    stop_loss:    Initial SL price.
    take_profit:  Initial TP price.
    size:         Position size in USDT-notional.
    tp1_price:    Optional partial TP price.  Defaults to None (partial OFF).
    tp1_hit:      Whether partial TP has already been taken.
    entry_time:   ISO-format timestamp string for the trade.
    risk_amount:  Override risk_amount; defaults to size * 0.01.
    signal_source: Signal key string.
    avg_entry_price: Weighted-avg entry for pyramid trades; defaults to entry_price.
    partial_pnl:  PnL already booked from TP1 (used in full-exit PnL formula).

    Returns
    -------
    The BacktestTrade that was placed into engine.state.position.
    """
    if risk_amount is None:
        risk_amount = size * engine.config.get("risk_per_trade", 0.01)

    trade = BacktestTrade(
        entry_time=entry_time,
        side=side,
        entry_price=entry_price,
        avg_entry_price=avg_entry_price if avg_entry_price is not None else entry_price,
        size=size,
        original_size=size,
        stop_loss=stop_loss,
        take_profit=take_profit,
        tp1_price=tp1_price if tp1_price is not None else 0.0,
        tp1_hit=tp1_hit,
        risk_amount=risk_amount,
        signal_source=signal_source,
        partial_pnl=partial_pnl,
    )

    # Charge entry commission (mirrors engine.py:946-947)
    commission = size * engine.commission_rate
    engine.state.balance -= commission

    engine.state.position = trade
    return trade
