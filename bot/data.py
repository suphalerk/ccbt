"""OHLCV data fetching and technical indicator calculations."""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """Compute Exponential Moving Average.

    Args:
        series: Price series.
        period: EMA period.

    Returns:
        EMA series.
    """
    return series.ewm(span=period, adjust=False).mean()


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Compute Relative Strength Index.

    Args:
        series: Price series (typically close).
        period: RSI lookback period.

    Returns:
        RSI series (0-100).
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    """Compute Average True Range.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: ATR lookback period.

    Returns:
        ATR series.
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=period, adjust=False).mean()


def compute_volume_ma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Compute Volume Moving Average.

    Args:
        volume: Volume series.
        period: MA period.

    Returns:
        Volume MA series.
    """
    return volume.rolling(window=period).mean()


def compute_dual_thrust(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    lookback: int = 20,
    k: float = 0.5,
) -> tuple[pd.Series, pd.Series]:
    """Compute Dual Thrust breakout bands.

    Upper band = prev_close + k * rolling_range(lookback)
    Lower band = prev_close - k * rolling_range(lookback)

    Uses shift(1) on both range and close to prevent look-ahead bias:
    the band at bar N uses only data from bars N-lookback to N-1.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        lookback: Rolling window for high/low range (default 20).
        k: Band width multiplier applied to range (default 0.5).

    Returns:
        Tuple of (upper_band, lower_band) Series.
    """
    range_n = high.rolling(lookback).max() - low.rolling(lookback).min()
    upper = close.shift(1) + k * range_n.shift(1)
    lower = close.shift(1) - k * range_n.shift(1)
    return upper, lower


def compute_awesome_oscillator(high: pd.Series, low: pd.Series) -> pd.Series:
    """Compute Bill Williams Awesome Oscillator.

    AO = SMA(5, midpoint) - SMA(34, midpoint)
    where midpoint = (high + low) / 2.

    Positive and increasing = bullish momentum.
    Negative and decreasing = bearish momentum.
    Zero-cross = trend change signal.

    Args:
        high: High price series.
        low: Low price series.

    Returns:
        Awesome Oscillator series.
    """
    midpoint = (high + low) / 2.0
    ao = midpoint.rolling(5).mean() - midpoint.rolling(34).mean()
    return ao


def add_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add all required technical indicators to an OHLCV DataFrame.

    Args:
        df: DataFrame with open, high, low, close, volume columns.
        config: Bot configuration with indicator parameters.

    Returns:
        DataFrame with added indicator columns.
    """
    df = df.copy()

    # EMAs
    df["ema_fast"] = compute_ema(df["close"], config["ema_fast"])
    df["ema_slow"] = compute_ema(df["close"], config["ema_slow"])

    # RSI
    df["rsi"] = compute_rsi(df["close"], config["rsi_period"])

    # ATR
    df["atr"] = compute_atr(df["high"], df["low"], df["close"], config["atr_period"])

    # Volume MA
    df["volume_ma"] = compute_volume_ma(df["volume"])

    # EMA slope: rate of change of slow EMA over last N candles (%)
    # Used to filter crossovers when the slow EMA is flat (whipsaw risk)
    ema_slope_period = config.get("ema_slope_period", 5)
    df["ema_slope"] = (
        df["ema_slow"].diff(ema_slope_period)
        / df["ema_slow"].shift(ema_slope_period)
        * 100
    )

    # Bollinger Bands
    bb_period = config.get("bb_period", 20)
    bb_std = config.get("bb_std", 2.0)
    df["bb_mid"] = df["close"].rolling(window=bb_period).mean()
    df["bb_std_val"] = df["close"].rolling(window=bb_period).std()
    df["bb_upper"] = df["bb_mid"] + bb_std * df["bb_std_val"]
    df["bb_lower"] = df["bb_mid"] - bb_std * df["bb_std_val"]
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    # BB width percentile: is current squeeze unusually tight?
    df["bb_width_pct"] = df["bb_width"].rolling(window=120).rank(pct=True)

    # Swing high/low detection for RSI divergence
    swing_lookback = config.get("swing_lookback", 5)
    window_size = 2 * swing_lookback + 1
    df["swing_low"] = df["low"] == df["low"].rolling(window=window_size, center=True).min()
    df["swing_high"] = df["high"] == df["high"].rolling(window=window_size, center=True).max()

    # EMA pullback detection — tighter proximity threshold (0.1% default, configurable)
    pullback_pct = config.get("pullback_proximity_pct", 0.001)
    long_pullback = (
        (df["low"] <= df["ema_slow"] * (1 + pullback_pct)) &  # Price touched within pct of EMA slow
        (df["close"] > df["ema_slow"]) &                       # But closed above
        (df["ema_fast"] > df["ema_slow"])                      # Trend is bullish
    )
    short_pullback = (
        (df["high"] >= df["ema_slow"] * (1 - pullback_pct)) &  # Price touched within pct of EMA slow
        (df["close"] < df["ema_slow"]) &                        # But closed below
        (df["ema_fast"] < df["ema_slow"])                       # Trend is bearish
    )

    # Require trend established for N candles before pullback is valid
    pullback_trend_bars = config.get("pullback_trend_bars", 10)
    ema_bull_trend = (df["ema_fast"] > df["ema_slow"]).rolling(window=pullback_trend_bars).min().astype(bool)
    ema_bear_trend = (df["ema_fast"] < df["ema_slow"]).rolling(window=pullback_trend_bars).min().astype(bool)
    df["pullback_long"] = long_pullback & ema_bull_trend
    df["pullback_short"] = short_pullback & ema_bear_trend

    # Secondary fast EMA pair for more crossover opportunities
    ema_fast2_period = config.get("ema_fast2", 5)
    ema_slow2_period = config.get("ema_slow2", 13)
    if ema_fast2_period and ema_slow2_period:
        df["ema_fast2"] = compute_ema(df["close"], ema_fast2_period)
        df["ema_slow2"] = compute_ema(df["close"], ema_slow2_period)

        crossover_lookback2 = config.get("crossover_lookback", 2)
        exact_cross_up2 = (df["ema_fast2"] > df["ema_slow2"]) & (
            df["ema_fast2"].shift(1) <= df["ema_slow2"].shift(1)
        )
        exact_cross_down2 = (df["ema_fast2"] < df["ema_slow2"]) & (
            df["ema_fast2"].shift(1) >= df["ema_slow2"].shift(1)
        )
        df["ema_cross_up2"] = (
            exact_cross_up2.rolling(window=crossover_lookback2, min_periods=1).max().astype(bool)
            & (df["ema_fast2"] > df["ema_slow2"])
        )
        df["ema_cross_down2"] = (
            exact_cross_down2.rolling(window=crossover_lookback2, min_periods=1).max().astype(bool)
            & (df["ema_fast2"] < df["ema_slow2"])
        )
        warmup2 = max(ema_fast2_period, ema_slow2_period) + 10
        df.iloc[:warmup2, df.columns.get_loc("ema_cross_up2")] = False
        df.iloc[:warmup2, df.columns.get_loc("ema_cross_down2")] = False

    # EMA crossover detection with lookback window
    # Suppress crossovers during EMA warmup period to avoid spurious signals
    # Add buffer beyond ema_slow for better EMA convergence
    warmup = max(config["ema_fast"], config["ema_slow"]) + 10
    crossover_lookback = config.get("crossover_lookback", 3)

    exact_cross_up = (df["ema_fast"] > df["ema_slow"]) & (
        df["ema_fast"].shift(1) <= df["ema_slow"].shift(1)
    )
    exact_cross_down = (df["ema_fast"] < df["ema_slow"]) & (
        df["ema_fast"].shift(1) >= df["ema_slow"].shift(1)
    )

    df["ema_cross_up"] = (
        exact_cross_up.rolling(window=crossover_lookback, min_periods=1).max().astype(bool)
        & (df["ema_fast"] > df["ema_slow"])
    )
    df["ema_cross_down"] = (
        exact_cross_down.rolling(window=crossover_lookback, min_periods=1).max().astype(bool)
        & (df["ema_fast"] < df["ema_slow"])
    )
    # Zero out crossovers in warmup rows
    df.iloc[:warmup, df.columns.get_loc("ema_cross_up")] = False
    df.iloc[:warmup, df.columns.get_loc("ema_cross_down")] = False

    # Body dominance indicator: ratio of candle body to total range
    # Guards against zero-range doji candles with epsilon floor
    candle_range = (df["high"] - df["low"]).clip(lower=1e-10)
    df["body_pct"] = (df["close"] - df["open"]).abs() / candle_range

    # Momentum indicators
    df["mom10"] = df["close"] / df["close"].shift(10) - 1
    df["mom4"] = df["close"] / df["close"].shift(4) - 1

    # Squeeze indicator: current ATR relative to its 50-period mean
    # Values < 0.7 = compressed (squeeze), values > 0.8 = expanding (release)
    atr_ma50 = df["atr"].rolling(window=50).mean()
    df["squeeze"] = df["atr"] / atr_ma50.clip(lower=1e-10)

    # Supertrend — computed when supertrend signal is enabled OR when a composite
    # signal that depends on supertrend_dir is enabled (ichi_supertrend, volexp_supertrend).
    _sigs = config.get("signals", {})
    _needs_supertrend = (
        _sigs.get("supertrend", {}).get("enabled", False)
        or _sigs.get("ichi_supertrend", {}).get("enabled", False)
        or _sigs.get("volexp_supertrend", {}).get("enabled", False)
    )
    if _needs_supertrend:
        st_mult = config.get("supertrend_multiplier", 2.0)
        st_atr_period = config.get("supertrend_atr_period", 14)
        st, st_dir = compute_supertrend(df["high"], df["low"], df["close"], st_atr_period, st_mult)
        df["supertrend"] = st
        df["supertrend_dir"] = st_dir

    # Ichimoku Cloud — only computed when ichimoku_tenkan key is present in config
    if "ichimoku_tenkan" in config:
        df = add_ichimoku_indicators(df, config)

    # Volatility Expansion Breakout — computed when vol_expansion signal is enabled
    # OR when volexp_supertrend is enabled (composite signal that depends on vol columns).
    _needs_volexp = (
        config.get("signals", {}).get("vol_expansion", {}).get("enabled", False)
        or config.get("signals", {}).get("volexp_supertrend", {}).get("enabled", False)
    )
    if _needs_volexp:
        atr = df["atr"] if "atr" in df.columns else compute_atr(
            df["high"], df["low"], df["close"], config.get("atr_period", 14)
        )
        atr_ma_period = config.get("vol_expansion_atr_ma_period", 20)
        threshold = config.get("vol_expansion_threshold", 1.5)
        lookback = config.get("vol_expansion_lookback", 1)
        df["atr_ma_20"] = atr.rolling(atr_ma_period).mean()
        df["vol_expanding"] = atr > df["atr_ma_20"] * threshold
        df["vol_break_high"] = df["close"] > df["high"].shift(lookback)
        df["vol_break_low"] = df["close"] < df["low"].shift(lookback)
        # EMA(50) trend filter for vol_expansion — computed here when not already
        # present via add_trend_filter (i.e., when running on 1H signal data directly).
        ema50_period = config.get("vol_expansion_ema_trend_period", 50)
        if "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns and "ema50" not in df.columns:
            df["ema50"] = compute_ema(df["close"], ema50_period)

    # Alligator — computed when alligator signal is enabled
    if config.get("signals", {}).get("alligator", {}).get("enabled", False):
        jaw, teeth, lips = compute_alligator(df["close"])
        df["alligator_jaw"] = jaw
        df["alligator_teeth"] = teeth
        df["alligator_lips"] = lips

    # Dual Supertrend — computed when dual_supertrend signal is enabled
    if config.get("signals", {}).get("dual_supertrend", {}).get("enabled", False):
        dst_fast_period = config.get("dual_supertrend_fast_period", 7)
        dst_fast_mult = config.get("dual_supertrend_fast_mult", 2.0)
        dst_slow_period = config.get("dual_supertrend_slow_period", 14)
        dst_slow_mult = config.get("dual_supertrend_slow_mult", 3.0)
        fast_dir, slow_dir = compute_dual_supertrend(
            df["high"], df["low"], df["close"],
            dst_fast_period, dst_fast_mult,
            dst_slow_period, dst_slow_mult,
        )
        df["dst_fast_dir"] = fast_dir
        df["dst_slow_dir"] = slow_dir

    # Price Action patterns — computed when any PA signal is enabled
    if (
        config.get("signals", {}).get("pin_bar", {}).get("enabled", False)
        or config.get("signals", {}).get("engulfing", {}).get("enabled", False)
        or config.get("signals", {}).get("inside_bar_breakout", {}).get("enabled", False)
    ):
        df = add_price_action_patterns(df, config)

    # ADX + DI — computed when adx_di_cross, williams_r_adx, or any signal needing ADX is enabled
    _needs_adx = (
        _sigs.get("adx_di_cross", {}).get("enabled", False)
        or _sigs.get("williams_r_adx", {}).get("enabled", False)
    )
    if _needs_adx:
        adx_period = config.get("adx_period", 14)
        adx, di_plus, di_minus = compute_adx_di(df["high"], df["low"], df["close"], adx_period)
        df["adx"] = adx
        df["di_plus"] = di_plus
        df["di_minus"] = di_minus

    # Choppiness Index — computed when choppiness_ema signal is enabled
    if _sigs.get("choppiness_ema", {}).get("enabled", False):
        ci_period = config.get("choppiness_period", 14)
        df["choppiness"] = compute_choppiness(df["high"], df["low"], df["close"], ci_period)

    # Williams %R — computed when williams_r_adx signal is enabled
    if _sigs.get("williams_r_adx", {}).get("enabled", False):
        wr_period = config.get("williams_r_period", 14)
        df["williams_r"] = compute_williams_r(df["high"], df["low"], df["close"], wr_period)
        # EMA(50) trend filter for williams_r_adx
        if "ema50" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("williams_r_ema_trend_period", 50))

    # ROC — computed when roc_momentum signal is enabled
    if _sigs.get("roc_momentum", {}).get("enabled", False):
        roc_period = config.get("roc_period", 10)
        df["roc"] = compute_roc(df["close"], roc_period)
        # EMA(50) trend filter for roc_momentum
        if "ema50" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("roc_ema_trend_period", 50))

    # Stochastic Oscillator — computed when stoch_supertrend signal is enabled
    if _sigs.get("stoch_supertrend", {}).get("enabled", False):
        stoch_k_period = config.get("stoch_k_period", 14)
        stoch_d_period = config.get("stoch_d_period", 3)
        stoch_k, stoch_d = compute_stochastic(
            df["high"], df["low"], df["close"], stoch_k_period, stoch_d_period
        )
        df["stoch_k"] = stoch_k
        df["stoch_d"] = stoch_d
        # Ensure supertrend is computed (stoch_supertrend depends on supertrend_dir)
        if "supertrend_dir" not in df.columns:
            st_mult = config.get("supertrend_multiplier", 2.0)
            st_atr_period = config.get("supertrend_atr_period", 14)
            st, st_dir = compute_supertrend(df["high"], df["low"], df["close"], st_atr_period, st_mult)
            df["supertrend"] = st
            df["supertrend_dir"] = st_dir

    # Price Channel — computed when price_channel_vol signal is enabled
    if _sigs.get("price_channel_vol", {}).get("enabled", False):
        pc_period = config.get("price_channel_period", 20)
        # Shift by 1 to avoid look-ahead: the channel uses data from closed candles before signal
        df["price_channel_high"] = df["high"].rolling(window=pc_period).max().shift(1)
        df["price_channel_low"] = df["low"].rolling(window=pc_period).min().shift(1)

    # EMA+Alligator confluence — ensure alligator columns are present when enabled
    if _sigs.get("ema_alligator", {}).get("enabled", False):
        if "alligator_jaw" not in df.columns:
            jaw, teeth, lips = compute_alligator(df["close"])
            df["alligator_jaw"] = jaw
            df["alligator_teeth"] = teeth
            df["alligator_lips"] = lips

    # Supertrend+Volume — ensure supertrend columns are present when enabled
    if _sigs.get("supertrend_volume", {}).get("enabled", False):
        if "supertrend_dir" not in df.columns:
            st_mult = config.get("supertrend_multiplier", 2.0)
            st_atr_period = config.get("supertrend_atr_period", 14)
            st, st_dir = compute_supertrend(df["high"], df["low"], df["close"], st_atr_period, st_mult)
            df["supertrend"] = st
            df["supertrend_dir"] = st_dir

    # Dual Thrust Breakout — computed when dual_thrust signal is enabled
    if _sigs.get("dual_thrust", {}).get("enabled", False):
        dt_lookback = config.get("dual_thrust_lookback", 20)
        dt_k = config.get("dual_thrust_k", 0.5)
        dt_upper, dt_lower = compute_dual_thrust(
            df["high"], df["low"], df["close"], dt_lookback, dt_k
        )
        df["dt_upper"] = dt_upper
        df["dt_lower"] = dt_lower
        # EMA(50) trend filter for dual_thrust
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("dual_thrust_ema_trend_period", 50))

    # Awesome Oscillator — computed when awesome_oscillator signal is enabled
    if _sigs.get("awesome_oscillator", {}).get("enabled", False):
        df["ao"] = compute_awesome_oscillator(df["high"], df["low"])
        # EMA(50) trend filter for awesome_oscillator
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("ao_ema_trend_period", 50))

    # Range Bounce — computed when range_bounce signal is enabled
    if _sigs.get("range_bounce", {}).get("enabled", False):
        rb_lookback = config.get("range_bounce_lookback", 20)
        rb_range_percentile_period = config.get("range_bounce_percentile_period", 50)
        rb_range_narrow_quantile = config.get("range_bounce_narrow_quantile", 0.3)
        df["rb_support"] = df["low"].rolling(rb_lookback).min()
        df["rb_resistance"] = df["high"].rolling(rb_lookback).max()
        rb_range_width = (df["rb_resistance"] - df["rb_support"]) / df["close"].clip(lower=1e-10)
        df["rb_is_ranging"] = rb_range_width < rb_range_width.rolling(rb_range_percentile_period).quantile(rb_range_narrow_quantile)

    # Stochastic MTF — computed when stoch_mtf signal is enabled
    if _sigs.get("stoch_mtf", {}).get("enabled", False):
        stoch_k_period = config.get("stoch_k_period", 14)
        stoch_d_period = config.get("stoch_d_period", 3)
        stoch_k, stoch_d = compute_stochastic(
            df["high"], df["low"], df["close"], stoch_k_period, stoch_d_period
        )
        df["stoch_k"] = stoch_k
        df["stoch_d"] = stoch_d
        # EMA(50) direction filter — used as higher-timeframe bias when no separate 4H data
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("stoch_mtf_ema_trend_period", 50))

    # Z-Score Mean Reversion — computed when zscore_meanrev signal is enabled
    if _sigs.get("zscore_meanrev", {}).get("enabled", False):
        zscore_period = config.get("zscore_period", 20)
        df["zscore"] = compute_zscore(df["close"], zscore_period)
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("zscore_ema_trend_period", 50))

    # EMA Ribbon — computed when ema_ribbon signal is enabled
    if _sigs.get("ema_ribbon", {}).get("enabled", False):
        for period in (8, 13, 21, 34, 55, 89):
            df[f"ema_ribbon_{period}"] = compute_ema(df["close"], period)

    # --- Combo signals (require multiple indicators to agree) ---

    # ribbon_rsi_vol: EMA Ribbon + RSI + Volume
    # Needs ema_ribbon_{8,13,21,34,55,89}, rsi (already computed), volume_ma (already computed),
    # plus an EMA(50) trend filter.
    if _sigs.get("ribbon_rsi_vol", {}).get("enabled", False):
        for period in (8, 13, 21, 34, 55, 89):
            col = f"ema_ribbon_{period}"
            if col not in df.columns:
                df[col] = compute_ema(df["close"], period)
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("ribbon_rsi_vol_ema_trend_period", 50))

    # dualthrust_adx: Dual Thrust + ADX
    # Needs dt_upper/dt_lower (from dual_thrust), adx/di_plus/di_minus, ema50 trend filter.
    if _sigs.get("dualthrust_adx", {}).get("enabled", False):
        if "dt_upper" not in df.columns:
            dt_lookback = config.get("dual_thrust_lookback", 20)
            dt_k = config.get("dual_thrust_k", 0.5)
            dt_upper, dt_lower = compute_dual_thrust(
                df["high"], df["low"], df["close"], dt_lookback, dt_k
            )
            df["dt_upper"] = dt_upper
            df["dt_lower"] = dt_lower
        if "adx" not in df.columns:
            adx_period = config.get("adx_period", 14)
            adx, di_plus, di_minus = compute_adx_di(df["high"], df["low"], df["close"], adx_period)
            df["adx"] = adx
            df["di_plus"] = di_plus
            df["di_minus"] = di_minus
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("dualthrust_adx_ema_trend_period", 50))

    # zscore_stoch: Z-Score + Stochastic
    # Needs zscore (from zscore_meanrev), stoch_k/stoch_d, ema50 trend filter.
    if _sigs.get("zscore_stoch", {}).get("enabled", False):
        if "zscore" not in df.columns:
            zscore_period = config.get("zscore_period", 20)
            df["zscore"] = compute_zscore(df["close"], zscore_period)
        if "stoch_k" not in df.columns:
            stoch_k_period = config.get("stoch_k_period", 14)
            stoch_d_period = config.get("stoch_d_period", 3)
            stoch_k, stoch_d = compute_stochastic(
                df["high"], df["low"], df["close"], stoch_k_period, stoch_d_period
            )
            df["stoch_k"] = stoch_k
            df["stoch_d"] = stoch_d
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("zscore_stoch_ema_trend_period", 50))

    # ichi_adx: Ichimoku + ADX
    # Needs ichimoku columns (tenkan, kijun, cloud_top, cloud_bottom) + adx.
    # Ichimoku is already computed when "ichimoku_tenkan" key is present.
    if _sigs.get("ichi_adx", {}).get("enabled", False):
        if "ichimoku_tenkan" not in config:
            # Inject default Ichimoku params so add_ichimoku_indicators can run
            _ichi_cfg = {**config, "ichimoku_tenkan": 9, "ichimoku_kijun": 26, "ichimoku_senkou_b": 52}
            df = add_ichimoku_indicators(df, _ichi_cfg)
        elif "tenkan" not in df.columns:
            df = add_ichimoku_indicators(df, config)
        if "adx" not in df.columns:
            adx_period = config.get("adx_period", 14)
            adx, di_plus, di_minus = compute_adx_di(df["high"], df["low"], df["close"], adx_period)
            df["adx"] = adx
            df["di_plus"] = di_plus
            df["di_minus"] = di_minus

    # ribbon_ao: EMA Ribbon + Awesome Oscillator
    # Needs ema_ribbon_{8,13,21,34,55,89} + ao + ema50 trend filter.
    if _sigs.get("ribbon_ao", {}).get("enabled", False):
        for period in (8, 13, 21, 34, 55, 89):
            col = f"ema_ribbon_{period}"
            if col not in df.columns:
                df[col] = compute_ema(df["close"], period)
        if "ao" not in df.columns:
            df["ao"] = compute_awesome_oscillator(df["high"], df["low"])
        if "ema50" not in df.columns and "ema_trend" not in df.columns and "ema_trend_1h" not in df.columns:
            df["ema50"] = compute_ema(df["close"], config.get("ribbon_ao_ema_trend_period", 50))

    logger.info("indicators_computed", extra={"rows": len(df)})
    return df


def detect_regime(df: pd.DataFrame, atr_period: int = 14, lookback: int = 20) -> str:
    """Detect market regime based on ATR ratio and price structure.

    Uses only closed candles (excludes the forming candle at iloc[-1])
    to be consistent with signal generation which uses iloc[-2].

    Args:
        df: DataFrame with high, low, close columns (and ideally pre-computed ATR).
        atr_period: Period for ATR calculation.
        lookback: Lookback window for ATR SMA and directional analysis.

    Returns:
        "trending", "ranging", or "volatile".
    """
    # Exclude the forming candle to avoid look-ahead bias
    df_closed = df.iloc[:-1] if len(df) > 1 else df

    if len(df_closed) < atr_period + lookback:
        return "ranging"  # Not enough data, be conservative

    # Compute ATR if not already present
    if "atr" in df_closed.columns:
        atr = df_closed["atr"]
    else:
        atr = compute_atr(df_closed["high"], df_closed["low"], df_closed["close"], atr_period)

    current_atr = atr.iloc[-1]
    if pd.isna(current_atr) or current_atr == 0:
        return "ranging"

    atr_sma = atr.rolling(window=lookback).mean()
    current_atr_sma = atr_sma.iloc[-1]
    if pd.isna(current_atr_sma) or current_atr_sma == 0:
        return "ranging"

    atr_ratio = current_atr / current_atr_sma

    # Directional movement: check for consistent higher highs/higher lows
    # or lower highs/lower lows over the lookback window
    recent = df_closed.iloc[-lookback:]
    highs = recent["high"].values
    lows = recent["low"].values

    higher_highs = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i - 1])
    higher_lows = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1])
    lower_highs = sum(1 for i in range(1, len(highs)) if highs[i] < highs[i - 1])
    lower_lows = sum(1 for i in range(1, len(lows)) if lows[i] < lows[i - 1])

    total_comparisons = len(highs) - 1
    if total_comparisons == 0:
        return "ranging"

    # Clear direction if >60% of bars show consistent HH/HL or LH/LL
    up_score = (higher_highs + higher_lows) / (2 * total_comparisons)
    down_score = (lower_highs + lower_lows) / (2 * total_comparisons)
    has_clear_direction = up_score > 0.6 or down_score > 0.6

    if atr_ratio > 1.5:
        return "volatile"
    elif atr_ratio < 0.8 and not has_clear_direction:
        return "ranging"
    elif 0.8 <= atr_ratio <= 1.5 and has_clear_direction:
        return "trending"
    else:
        # Edge cases: moderate ATR but no direction, or low ATR with direction
        if has_clear_direction:
            return "trending"
        return "ranging"


def compute_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    atr_period: int = 14,
    multiplier: float = 2.0,
) -> tuple[pd.Series, pd.Series]:
    """Compute Supertrend indicator.

    The Supertrend band tracks price action — it is the lower band when
    bullish (acting as support) and the upper band when bearish (resistance).
    Direction flips when price crosses through the active band.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        atr_period: ATR lookback period (default 14).
        multiplier: Band width multiplier applied to ATR (default 2.0).

    Returns:
        Tuple of (supertrend, direction) where:
            supertrend — Series of band values (lower band when bullish, upper when bearish).
            direction  — Series of 1 (bullish) or -1 (bearish).
    """
    atr = compute_atr(high, low, close, atr_period)
    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    direction = pd.Series(1, index=close.index, dtype=int)
    final_upper = upper_band.copy()
    final_lower = lower_band.copy()

    for i in range(1, len(close)):
        # Lower band ratchets up only (acts as rising support)
        if lower_band.iloc[i] > final_lower.iloc[i - 1] or close.iloc[i - 1] < final_lower.iloc[i - 1]:
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i - 1]

        # Upper band ratchets down only (acts as falling resistance)
        if upper_band.iloc[i] < final_upper.iloc[i - 1] or close.iloc[i - 1] > final_upper.iloc[i - 1]:
            final_upper.iloc[i] = upper_band.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i - 1]

        # Direction flip logic
        if direction.iloc[i - 1] == 1:  # was bullish
            direction.iloc[i] = -1 if close.iloc[i] < final_lower.iloc[i] else 1
        else:  # was bearish
            direction.iloc[i] = 1 if close.iloc[i] > final_upper.iloc[i] else -1

    # Supertrend value: lower band when bullish, upper band when bearish
    supertrend = pd.Series(np.nan, index=close.index)
    supertrend[direction == 1] = final_lower[direction == 1]
    supertrend[direction == -1] = final_upper[direction == -1]

    return supertrend, direction


def add_ichimoku_indicators(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add Ichimoku Cloud indicators to the DataFrame.

    Columns added:
        tenkan    — Tenkan-sen (conversion line): (highest_high + lowest_low) / 2 over tenkan period
        kijun     — Kijun-sen (base line): same over kijun period
        span_a    — Senkou Span A raw (before display shift): (tenkan + kijun) / 2
        span_b    — Senkou Span B raw (before display shift): (H+L)/2 over senkou_b period
        cloud_top    — max(span_a_shifted_back, span_b_shifted_back) at current bar
        cloud_bottom — min(span_a_shifted_back, span_b_shifted_back) at current bar

    Signal logic uses the cloud at current time, which equals span_a/span_b values
    from 26 bars ago (i.e. span_a.shift(26) and span_b.shift(26) shifted backward).

    Args:
        df: DataFrame with high, low, close columns.
        config: Bot configuration with ichimoku_tenkan, ichimoku_kijun, ichimoku_senkou_b keys.

    Returns:
        DataFrame with Ichimoku columns added.
    """
    tenkan_period = config.get("ichimoku_tenkan", 9)
    kijun_period = config.get("ichimoku_kijun", 26)
    senkou_b_period = config.get("ichimoku_senkou_b", 52)
    cloud_shift = kijun_period  # Standard Ichimoku: cloud is projected forward by kijun bars

    # Tenkan-sen: midpoint of highest high and lowest low over tenkan period
    df["tenkan"] = (
        df["high"].rolling(window=tenkan_period).max()
        + df["low"].rolling(window=tenkan_period).min()
    ) / 2

    # Kijun-sen: midpoint of highest high and lowest low over kijun period
    df["kijun"] = (
        df["high"].rolling(window=kijun_period).max()
        + df["low"].rolling(window=kijun_period).min()
    ) / 2

    # Senkou Span A (raw, unshifted): average of tenkan and kijun
    span_a_raw = (df["tenkan"] + df["kijun"]) / 2

    # Senkou Span B (raw, unshifted): midpoint of highest high and lowest low over senkou_b period
    span_b_raw = (
        df["high"].rolling(window=senkou_b_period).max()
        + df["low"].rolling(window=senkou_b_period).min()
    ) / 2

    # Store raw spans for reference
    df["span_a"] = span_a_raw
    df["span_b"] = span_b_raw

    # For SIGNAL LOGIC: cloud at current bar = span_a/span_b from cloud_shift bars ago.
    # The Ichimoku cloud is projected forward by cloud_shift bars in display, so to see
    # where the cloud is NOW we look at the span values from cloud_shift bars back.
    span_a_current = span_a_raw.shift(cloud_shift)
    span_b_current = span_b_raw.shift(cloud_shift)

    df["cloud_top"] = pd.concat([span_a_current, span_b_current], axis=1).max(axis=1)
    df["cloud_bottom"] = pd.concat([span_a_current, span_b_current], axis=1).min(axis=1)

    logger.debug(
        "ichimoku_indicators_computed",
        extra={
            "tenkan_period": tenkan_period,
            "kijun_period": kijun_period,
            "senkou_b_period": senkou_b_period,
            "rows": len(df),
        },
    )
    return df


def add_price_action_patterns(df: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Add Price Action pattern boolean columns to the DataFrame.

    All patterns are computed using fully vectorized pandas operations with no loops.
    Uses only closed candle OHLCV data — no forming candle look-ahead bias.

    Columns added:
        pin_bar_bull      — Bullish pin bar (long lower wick, close in upper portion)
        pin_bar_bear      — Bearish pin bar (long upper wick, close in lower portion)
        engulfing_bull    — Bullish engulfing candle (current body engulfs prior bear body)
        engulfing_bear    — Bearish engulfing candle (current body engulfs prior bull body)
        inside_bar        — Inside bar (current H/L within prior candle's H/L range)
        ib_breakout_bull  — Inside bar bullish breakout (close above mother bar high)
        ib_breakout_bear  — Inside bar bearish breakout (close below mother bar low)

    Args:
        df: DataFrame with open, high, low, close, atr columns already computed.
        config: Bot configuration with PA-specific parameter overrides.

    Returns:
        DataFrame with PA boolean columns added.
    """
    df = df.copy()

    # --- Shared helper series ---
    body = (df["close"] - df["open"]).abs()
    candle_range = (df["high"] - df["low"]).clip(lower=1e-10)
    lower_wick = df[["close", "open"]].min(axis=1) - df["low"]
    upper_wick = df["high"] - df[["close", "open"]].max(axis=1)

    # --- Pin Bar parameters ---
    wick_ratio = config.get("pin_bar_wick_ratio", 2.0)
    close_position_thresh = config.get("pin_bar_close_position", 0.70)
    max_upper_wick_pct = config.get("pin_bar_max_upper_wick_pct", 0.30)
    min_range_atr = config.get("pin_bar_min_range_atr_mult", 0.5)

    # Bullish pin bar: long lower wick, close in upper portion of range
    df["pin_bar_bull"] = (
        (lower_wick >= body * wick_ratio)  # Lower wick >= 2x body
        & ((df["close"] - df["low"]) / candle_range >= close_position_thresh)  # Close in upper 30%
        & (upper_wick / candle_range <= max_upper_wick_pct)  # Small upper wick
        & (body > 0)  # Not a perfect doji
        & (candle_range >= df["atr"] * min_range_atr)  # Minimum size
    )

    # Bearish pin bar: long upper wick, close in lower portion of range
    df["pin_bar_bear"] = (
        (upper_wick >= body * wick_ratio)  # Upper wick >= 2x body
        & ((df["high"] - df["close"]) / candle_range >= close_position_thresh)  # Close in lower 30%
        & (lower_wick / candle_range <= max_upper_wick_pct)  # Small lower wick
        & (body > 0)  # Not a perfect doji
        & (candle_range >= df["atr"] * min_range_atr)  # Minimum size
    )

    # --- Engulfing parameters ---
    engulfing_ratio = config.get("engulfing_body_ratio", 1.3)
    min_body_pct = config.get("engulfing_min_body_pct", 0.40)

    prev_body = body.shift(1)
    prev_close = df["close"].shift(1)
    prev_open = df["open"].shift(1)
    is_bull = df["close"] > df["open"]
    is_bear = df["close"] < df["open"]
    prev_is_bear = prev_close < prev_open
    prev_is_bull = prev_close > prev_open

    # Bullish engulfing: current bull body fully engulfs prior bear body.
    # For a bearish prior candle (prev_open > prev_close):
    #   - current close must exceed the TOP of the prior bear body (prev_open)
    #   - current open must be BELOW the TOP of the prior bear body (prev_open)
    #     i.e. open is somewhere inside or below the prior candle.
    # Note: prev_close is the BOTTOM of a bear candle so requiring open < prev_close
    # is too strict (almost never hit in a gap-less crypto market). Using prev_open instead.
    df["engulfing_bull"] = (
        is_bull
        & prev_is_bear
        & (body >= prev_body * engulfing_ratio)  # Current body >= 1.3x prev body
        & (df["close"] > prev_open)   # Current close > prior bear open (top of bear body)
        & (df["open"] < prev_open)    # Current open < prior bear open (inside/below bear body)
        & (body / candle_range >= min_body_pct)  # Min body-to-range ratio
    )

    # Bearish engulfing: current bear body fully engulfs prior bull body.
    # For a bullish prior candle (prev_close > prev_open):
    #   - current open must exceed the BOTTOM of the prior bull body (prev_open)
    #     i.e. open is somewhere inside or above the prior candle.
    #   - current close must fall BELOW the BOTTOM of the prior bull body (prev_open)
    # Note: prev_close is the TOP of a bull candle so requiring open > prev_close
    # is too strict (almost never hit in a gap-less crypto market). Using prev_open instead.
    df["engulfing_bear"] = (
        is_bear
        & prev_is_bull
        & (body >= prev_body * engulfing_ratio)  # Current body >= 1.3x prev body
        & (df["open"] > prev_open)    # Current open > prior bull open (inside/above bull body)
        & (df["close"] < prev_open)   # Current close < prior bull open (bottom of bull body)
        & (body / candle_range >= min_body_pct)  # Min body-to-range ratio
    )

    # --- Inside Bar ---
    df["inside_bar"] = (df["high"] < df["high"].shift(1)) & (df["low"] > df["low"].shift(1))

    # --- Inside Bar Breakout parameters ---
    min_mother_atr = config.get("inside_bar_min_mother_range_atr_mult", 0.7)

    # Mother bar is 2 bars back, inside bar is 1 bar back, current = breakout candle
    mother_high = df["high"].shift(2)
    mother_low = df["low"].shift(2)
    mother_range = (mother_high - mother_low).clip(lower=1e-10)

    # Bullish IB breakout: previous bar was inside bar, current closes above mother high
    df["ib_breakout_bull"] = (
        df["inside_bar"].shift(1)  # Previous bar was an inside bar
        & (df["close"] > mother_high)  # Current closes above mother high
        & (mother_range >= df["atr"] * min_mother_atr)  # Mother bar was significant
    )

    # Bearish IB breakout: previous bar was inside bar, current closes below mother low
    df["ib_breakout_bear"] = (
        df["inside_bar"].shift(1)  # Previous bar was an inside bar
        & (df["close"] < mother_low)  # Current closes below mother low
        & (mother_range >= df["atr"] * min_mother_atr)  # Mother bar was significant
    )

    logger.debug(
        "price_action_patterns_computed",
        extra={
            "rows": len(df),
            "pin_bar_bull": int(df["pin_bar_bull"].sum()),
            "pin_bar_bear": int(df["pin_bar_bear"].sum()),
            "engulfing_bull": int(df["engulfing_bull"].sum()),
            "engulfing_bear": int(df["engulfing_bear"].sum()),
            "inside_bar": int(df["inside_bar"].sum()),
            "ib_breakout_bull": int(df["ib_breakout_bull"].sum()),
            "ib_breakout_bear": int(df["ib_breakout_bear"].sum()),
        },
    )
    return df


def compute_alligator(
    close: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute Williams Alligator: Jaw(13,8), Teeth(8,5), Lips(5,3).

    Uses Smoothed Moving Averages (SMMA) then shifts each line forward
    to model the "sleeping alligator" displacement.

    Returns:
        Tuple of (jaw, teeth, lips) — each a pd.Series aligned to close.index.
        jaw   — 13-period SMMA shifted forward 8 bars (Blue line)
        teeth — 8-period SMMA shifted forward 5 bars (Red line)
        lips  — 5-period SMMA shifted forward 3 bars (Green line)
    """

    def smma(series: pd.Series, period: int) -> pd.Series:
        """Smoothed Moving Average (Wilder's MA)."""
        result = pd.Series(np.nan, index=series.index, dtype=float)
        if len(series) < period:
            return result
        result.iloc[period - 1] = series.iloc[:period].mean()
        for i in range(period, len(series)):
            result.iloc[i] = (result.iloc[i - 1] * (period - 1) + series.iloc[i]) / period
        return result

    jaw = smma(close, 13).shift(8)
    teeth = smma(close, 8).shift(5)
    lips = smma(close, 5).shift(3)
    return jaw, teeth, lips


def compute_dual_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    fast_period: int = 7,
    fast_mult: float = 2.0,
    slow_period: int = 14,
    slow_mult: float = 3.0,
) -> tuple[pd.Series, pd.Series]:
    """Compute dual Supertrend directions (fast and slow).

    Uses the existing compute_supertrend for each band pair.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        fast_period: ATR period for fast Supertrend (default 7).
        fast_mult: Multiplier for fast Supertrend (default 2.0).
        slow_period: ATR period for slow Supertrend (default 14).
        slow_mult: Multiplier for slow Supertrend (default 3.0).

    Returns:
        Tuple of (fast_dir, slow_dir) where 1=bullish, -1=bearish.
    """
    _, fast_dir = compute_supertrend(high, low, close, fast_period, fast_mult)
    _, slow_dir = compute_supertrend(high, low, close, slow_period, slow_mult)
    return fast_dir, slow_dir


def compute_adx_di(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute ADX, DI+, and DI- using Wilder's smoothing.

    +DM = max(high - prev_high, 0) if high - prev_high > prev_low - low, else 0
    -DM = max(prev_low - low, 0) if prev_low - low > high - prev_high, else 0
    TR  = max(high-low, |high-prev_close|, |low-prev_close|)
    DI+ = 100 * smoothed(+DM) / smoothed(TR)
    DI- = 100 * smoothed(-DM) / smoothed(TR)
    DX  = 100 * |DI+ - DI-| / (DI+ + DI-)
    ADX = Wilder smooth of DX

    Wilder smoothing: first value = rolling sum of `period` bars;
    subsequent values = prev * (period-1)/period + current.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Smoothing period (default 14).

    Returns:
        Tuple of (adx, di_plus, di_minus) as pd.Series.
    """
    n = len(close)
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    up_move = high - prev_high
    down_move = prev_low - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    plus_dm_s = pd.Series(plus_dm, index=close.index, dtype=float)
    minus_dm_s = pd.Series(minus_dm, index=close.index, dtype=float)

    # Wilder smoothing via ewm with alpha = 1/period (adjust=False)
    # This is equivalent to: first = sum of first `period` values;
    # then prev * (period-1)/period + current.
    smoothed_tr = true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() * period
    smoothed_plus = plus_dm_s.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() * period
    smoothed_minus = minus_dm_s.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() * period

    di_plus = 100.0 * smoothed_plus / smoothed_tr.replace(0, np.nan)
    di_minus = 100.0 * smoothed_minus / smoothed_tr.replace(0, np.nan)

    dx_num = (di_plus - di_minus).abs()
    dx_denom = (di_plus + di_minus).replace(0, np.nan)
    dx = 100.0 * dx_num / dx_denom

    adx = dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    return adx, di_plus.fillna(0.0), di_minus.fillna(0.0)


def compute_choppiness(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute Choppiness Index.

    CI = 100 * log10(sum of 1-period ATR over `period` bars / (HH - LL)) / log10(period)
    CI < 38.2 = trending (directional), CI > 61.8 = ranging (choppy).

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Lookback period (default 14).

    Returns:
        Choppiness index series (bounded [0, 100]).
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    atr1 = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    sum_atr = atr1.rolling(window=period).sum()
    hh = high.rolling(window=period).max()
    ll = low.rolling(window=period).min()
    range_hl = (hh - ll).replace(0, np.nan)

    ci = 100.0 * np.log10(sum_atr / range_hl) / np.log10(period)
    return ci.clip(0.0, 100.0)


def compute_williams_r(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute Williams %R oscillator.

    WR = -100 * (HH - close) / (HH - LL)
    Range: -100 (most oversold) to 0 (most overbought).
    -80 to -100 = oversold zone, 0 to -20 = overbought zone.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Lookback period (default 14).

    Returns:
        Williams %R series (range [-100, 0]).
    """
    hh = high.rolling(window=period).max()
    ll = low.rolling(window=period).min()
    wr = -100.0 * (hh - close) / (hh - ll).replace(0, np.nan)
    return wr.clip(-100.0, 0.0)


def compute_roc(close: pd.Series, period: int = 10) -> pd.Series:
    """Compute Rate of Change (momentum).

    ROC = (close - close_n_bars_ago) / close_n_bars_ago * 100

    Args:
        close: Close price series.
        period: Lookback period (default 10).

    Returns:
        ROC series as percentage.
    """
    prev_close = close.shift(period)
    return (close - prev_close) / prev_close.replace(0, np.nan) * 100.0


def compute_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    """Compute Stochastic Oscillator (%K and %D).

    %K = 100 * (close - LL) / (HH - LL)
    %D = SMA(%K, d_period)
    %K < 20 = oversold, %K > 80 = overbought.

    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        k_period: Lookback period for %K (default 14).
        d_period: Smoothing period for %D (default 3).

    Returns:
        Tuple of (stoch_k, stoch_d) series (range [0, 100]).
    """
    ll = low.rolling(window=k_period).min()
    hh = high.rolling(window=k_period).max()
    k = 100.0 * (close - ll) / (hh - ll).replace(0, np.nan)
    k = k.clip(0.0, 100.0)
    d = k.rolling(window=d_period).mean()
    return k, d


def compute_zscore(close: pd.Series, period: int = 20) -> pd.Series:
    """Compute rolling Z-Score of close price.

    Z-Score = (close - rolling_mean) / rolling_std

    Values above +2.0 indicate overbought (price far above mean).
    Values below -2.0 indicate oversold (price far below mean).

    Args:
        close: Close price series.
        period: Rolling window period (default 20).

    Returns:
        Z-Score series (typically in range [-4, +4]).
    """
    rolling_mean = close.rolling(window=period).mean()
    rolling_std = close.rolling(window=period).std()
    return (close - rolling_mean) / rolling_std.replace(0, np.nan)


def add_funding_rate(
    df: pd.DataFrame, funding_file: str = "data/btcusdt_funding_rate.csv"
) -> pd.DataFrame:
    """Merge funding rate data with signal DataFrame.

    Funding rate updates every 8h (00:00, 08:00, 16:00 UTC).
    Forward-fill to match 15m candles.
    Uses shift(1) to prevent look-ahead bias — only the last SETTLED rate
    is visible at any candle, never the rate being determined right now.

    Args:
        df: Signal DataFrame with a DatetimeIndex or 'timestamp' column.
        funding_file: Path to a CSV with columns ``timestamp`` and ``fundingRate``.

    Returns:
        DataFrame with a ``fundingRate`` column added (0.0 where data unavailable).
    """
    import os

    if not os.path.exists(funding_file):
        logger.debug("funding_file_not_found", extra={"path": funding_file})
        return df

    funding = pd.read_csv(funding_file, parse_dates=["timestamp"])
    funding = funding.set_index("timestamp").sort_index()

    # shift(1) so that the rate visible at settlement time T is the
    # *previous* settled rate — not the one that just settled at T.
    funding["fundingRate"] = funding["fundingRate"].shift(1)

    df = df.copy()
    df_reset = df.reset_index()

    # Determine the timestamp column name
    ts_col = "timestamp" if "timestamp" in df_reset.columns else df_reset.columns[0]

    df_merged = pd.merge_asof(
        df_reset.sort_values(ts_col),
        funding.reset_index().rename(columns={"timestamp": ts_col}),
        on=ts_col,
        direction="backward",
    )

    if ts_col == "timestamp":
        df_merged = df_merged.set_index("timestamp")
    else:
        df_merged = df_merged.set_index(ts_col)

    df_merged["fundingRate"] = df_merged["fundingRate"].ffill().fillna(0.0)

    logger.debug(
        "funding_rate_merged",
        extra={
            "rows": len(df_merged),
            "funding_rows": len(funding),
            "non_zero": int((df_merged["fundingRate"] != 0).sum()),
        },
    )
    return df_merged


def add_trend_filter(
    df: pd.DataFrame, trend_df: pd.DataFrame, config: dict
) -> pd.DataFrame:
    """Add trend filter from higher timeframe data.

    Merges the 1h EMA(50) trend filter into the signal timeframe DataFrame
    using forward-fill alignment.

    Args:
        df: Signal timeframe DataFrame (e.g., 15m).
        trend_df: Trend timeframe DataFrame (e.g., 1h) with OHLCV data.
        config: Bot configuration.

    Returns:
        Signal DataFrame with 'ema_trend' and 'above_trend' columns added.
    """
    df = df.copy()
    trend_df = trend_df.copy()

    trend_df["ema_trend"] = compute_ema(trend_df["close"], config["ema_trend"])

    # Compute 1H indicators for body_dominance and squeeze_release signals
    ema9_1h = compute_ema(trend_df["close"], 9)
    ema21_1h = compute_ema(trend_df["close"], 21)
    hl_range = trend_df["high"] - trend_df["low"]
    body = abs(trend_df["close"] - trend_df["open"])
    atr_1h = compute_atr(trend_df["high"], trend_df["low"], trend_df["close"], config.get("atr_period", 14))

    trend_feat = pd.DataFrame({
        "ema_trend_1h": trend_df["ema_trend"],
        "ema9_1h": ema9_1h,
        "ema21_1h": ema21_1h,
        "body_pct_1h": body / hl_range.replace(0, np.nan).ffill().clip(lower=0.01),
        "mom10_1h": trend_df["close"] / trend_df["close"].shift(10) - 1,
        "mom4_1h": trend_df["close"] / trend_df["close"].shift(4) - 1,
        "squeeze_1h": atr_1h / atr_1h.rolling(50).mean(),
        "vol_ratio_1h": trend_df["volume"] / trend_df["volume"].rolling(20).mean(),
        "close_1h": trend_df["close"],
        "open_1h": trend_df["open"],
    }, index=trend_df.index)

    # CRITICAL: Shift all 1H features by 1 period to prevent look-ahead bias.
    # Without this shift, a 15m candle at 15:15 would see the 1H candle for
    # 15:00-16:00 which hasn't closed yet. By shifting, we only see the LAST
    # COMPLETED 1H candle (e.g., 14:00-15:00 at time 15:15).
    # EMA-based columns (ema_trend, ema9, ema21) are lagging by nature so the
    # shift has minimal impact, but body_pct, mom, squeeze, vol_ratio are
    # entirely dependent on the candle's close — shift is essential.
    trend_feat = trend_feat.shift(1)

    # Merge with backward-looking alignment
    df = pd.merge_asof(
        df.reset_index(), trend_feat.reset_index(),
        on="timestamp" if "timestamp" in df.reset_index().columns else df.reset_index().columns[0],
        direction="backward",
    )
    if "timestamp" in df.columns:
        df = df.set_index("timestamp")
    for col in trend_feat.columns:
        df[col] = df[col].ffill()
    df["above_trend"] = df["close"] > df["ema_trend_1h"]
    df["below_trend"] = df["close"] < df["ema_trend_1h"]

    return df
