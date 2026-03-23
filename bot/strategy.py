"""Trading signal generation using EMA crossover + RSI + ATR + Volume filters."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from bot.data import add_indicators, add_trend_filter, detect_regime

logger = logging.getLogger(__name__)


class SignalType(Enum):
    """Trading signal direction."""

    LONG = "long"
    SHORT = "short"
    NONE = "none"


@dataclass
class TradeSignal:
    """A generated trade signal with entry, stop loss, and take profit levels."""

    signal_type: SignalType
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    rsi: float
    risk_reward_ratio: float
    regime: str = "trending"
    signal_source: str = "ema_crossover"  # Tracks which signal type generated this


def check_entry_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check if all entry conditions are met for a given signal direction.

    Uses directional RSI ranges: longs allow higher RSI (momentum),
    shorts allow lower RSI. This aligns RSI filtering with trend-following
    rather than mean-reversion.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if all conditions are satisfied.
    """
    rsi_min = config["rsi_min"]
    rsi_max = config["rsi_max"]
    atr_min = config.get("atr_min", 0.0)
    volume_mult = config.get("volume_mult", 1.2)
    volume_max_mult = config.get("volume_max_mult")  # None means no upper bound

    # RSI filter — directional for trend-following
    if pd.isna(row.get("rsi")):
        return False

    if signal_type == SignalType.LONG:
        # Longs: use explicit config keys with fallback to derived values
        long_rsi_min = config.get("rsi_long_min", rsi_min + 3)
        long_rsi_max = config.get("rsi_long_max", rsi_max + 10)
        if not (long_rsi_min <= row["rsi"] <= long_rsi_max):
            return False
    elif signal_type == SignalType.SHORT:
        # Shorts: use explicit config keys with fallback to derived values
        short_rsi_min = config.get("rsi_short_min", rsi_min - 20)
        short_rsi_max = config.get("rsi_short_max", rsi_max - 13)
        if not (short_rsi_min <= row["rsi"] <= short_rsi_max):
            return False
    else:
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    # ATR minimum volatility
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    # EMA slope filter — reject crossovers when slow EMA is flat (whipsaw territory)
    ema_slope_min = config.get("ema_slope_min", 0.02)
    if pd.notna(row.get("ema_slope")):
        if signal_type == SignalType.LONG and row["ema_slope"] < ema_slope_min:
            return False
        if signal_type == SignalType.SHORT and row["ema_slope"] > -ema_slope_min:
            return False

    # Volume filter — lower bound (minimum activity) and upper bound (exhaustion)
    if pd.notna(row.get("volume_ma")) and row.get("volume_ma", 0) > 0:
        if row["volume"] < row["volume_ma"] * volume_mult:
            return False
        if volume_max_mult is not None and row["volume"] > row["volume_ma"] * volume_max_mult:
            return False

    if signal_type == SignalType.LONG:
        # EMA crossover up
        if not row.get("ema_cross_up", False):
            return False
        # Trend filter: price above 1h EMA(50)
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False

    elif signal_type == SignalType.SHORT:
        # EMA crossover down
        if not row.get("ema_cross_down", False):
            return False
        # Trend filter: price below 1h EMA(50)
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False

    return True


def check_pullback_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check EMA pullback entry conditions.

    Looks for price pulling back to the slow EMA while the trend (fast > slow)
    remains intact. This catches continuation entries after a pullback, which are
    typically lower-risk than crossover entries.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if pullback conditions are satisfied.
    """
    if pd.isna(row.get("rsi")) or pd.isna(row.get("atr")):
        return False

    if row["atr"] < config.get("atr_min", 0.0):
        return False

    # EMA slope check — pullback must be in direction of trend momentum
    ema_slope_min = config.get("ema_slope_min", 0.02)
    if pd.notna(row.get("ema_slope")):
        if signal_type == SignalType.LONG and row["ema_slope"] < ema_slope_min:
            return False
        if signal_type == SignalType.SHORT and row["ema_slope"] > -ema_slope_min:
            return False

    # Volume confirmation on the pullback candle
    vol_mult = config.get("pullback_volume_mult", 1.2)
    if pd.notna(row.get("volume_ma")) and row.get("volume_ma", 0) > 0:
        if row["volume"] < row["volume_ma"] * vol_mult:
            return False

    if signal_type == SignalType.LONG:
        if not row.get("pullback_long", False):
            return False
        # Trend filter: price should be above the 1h EMA if available
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False
        # RSI: not overbought (pullbacks allow slightly wider range than crossovers)
        rsi_max = config.get("pullback_rsi_long_max", 68)
        if row["rsi"] > rsi_max:
            return False
        # Also require RSI is not extremely oversold (healthy pullback, not reversal)
        long_rsi_min = config.get("rsi_long_min", config.get("rsi_min", 45) + 3)
        if row["rsi"] < long_rsi_min:
            return False

    elif signal_type == SignalType.SHORT:
        if not row.get("pullback_short", False):
            return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False
        rsi_min = config.get("pullback_rsi_short_min", 30)
        if row["rsi"] < rsi_min:
            return False
        short_rsi_max = config.get("rsi_short_max", config.get("rsi_max", 65) - 13)
        if row["rsi"] > short_rsi_max:
            return False

    return True


def check_rsi_divergence_conditions(
    row: pd.Series,
    prev_rows: pd.DataFrame,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check RSI divergence conditions using recent swing points.

    Bullish divergence: price makes a lower low but RSI makes a higher low.
    Bearish divergence: price makes a higher high but RSI makes a lower high.

    Args:
        row: Current candle row with indicators.
        prev_rows: Recent rows of the DataFrame for finding swing points.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if divergence conditions are satisfied.
    """
    if pd.isna(row.get("rsi")) or pd.isna(row.get("atr")):
        return False

    if row["atr"] < config.get("atr_min", 0.0):
        return False

    div_lookback = config.get("divergence_lookback", 30)
    if len(prev_rows) < div_lookback:
        return False

    window = prev_rows.tail(div_lookback)

    # RSI extreme zone filter — divergence only matters in extreme territory
    if pd.isna(row.get("rsi")):
        return False
    if signal_type == SignalType.LONG and row["rsi"] > 45:
        return False  # Bullish divergence needs RSI to be lowish (oversold)
    if signal_type == SignalType.SHORT and row["rsi"] < 55:
        return False  # Bearish divergence needs RSI to be highish (overbought)

    min_rsi_div = config.get("divergence_min_rsi_diff", 5.0)
    min_price_div = config.get("divergence_min_price_pct", 0.005)

    if signal_type == SignalType.LONG:
        # Find last 2 swing lows in the window
        swing_lows = window[window["swing_low"] == True]  # noqa: E712
        if len(swing_lows) < 2:
            return False
        last_two = swing_lows.tail(2)
        # Bullish divergence: price lower low, RSI higher low
        price_lower = last_two.iloc[1]["low"] < last_two.iloc[0]["low"]
        rsi_higher = last_two.iloc[1]["rsi"] > last_two.iloc[0]["rsi"]
        if not (price_lower and rsi_higher):
            return False
        # Minimum divergence magnitude checks
        rsi_diff = last_two.iloc[1]["rsi"] - last_two.iloc[0]["rsi"]
        if rsi_diff < min_rsi_div:
            return False
        price_diff = (last_two.iloc[0]["low"] - last_two.iloc[1]["low"]) / last_two.iloc[0]["low"]
        if price_diff < min_price_div:
            return False
        # Trend filter
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False

    elif signal_type == SignalType.SHORT:
        swing_highs = window[window["swing_high"] == True]  # noqa: E712
        if len(swing_highs) < 2:
            return False
        last_two = swing_highs.tail(2)
        # Bearish divergence: price higher high, RSI lower high
        price_higher = last_two.iloc[1]["high"] > last_two.iloc[0]["high"]
        rsi_lower = last_two.iloc[1]["rsi"] < last_two.iloc[0]["rsi"]
        if not (price_higher and rsi_lower):
            return False
        # Minimum divergence magnitude checks
        rsi_diff = last_two.iloc[0]["rsi"] - last_two.iloc[1]["rsi"]
        if rsi_diff < min_rsi_div:
            return False
        price_diff = (last_two.iloc[1]["high"] - last_two.iloc[0]["high"]) / last_two.iloc[0]["high"]
        if price_diff < min_price_div:
            return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False

    return True


def check_fast_crossover_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check fast EMA(5/13) crossover conditions.

    Same as regular crossover but uses the faster EMA pair. Requires additional
    confirmation: the primary EMA(9/21) must already be aligned in the same direction.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if fast crossover conditions are satisfied.
    """
    if pd.isna(row.get("rsi")) or pd.isna(row.get("atr")):
        return False

    # ATR minimum
    if row["atr"] < config.get("atr_min", 50.0):
        return False

    # EMA slope check
    ema_slope_min = config.get("ema_slope_min", 0.02)
    if pd.notna(row.get("ema_slope")):
        if signal_type == SignalType.LONG and row["ema_slope"] < ema_slope_min:
            return False
        if signal_type == SignalType.SHORT and row["ema_slope"] > -ema_slope_min:
            return False

    if signal_type == SignalType.LONG:
        # Fast EMA(5/13) crossover up
        if not row.get("ema_cross_up2", False):
            return False
        # Primary EMAs must be aligned bullish (EMA9 > EMA21)
        if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
            if row["ema_fast"] <= row["ema_slow"]:
                return False
        # Trend filter
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False
        # RSI filter (same as regular crossover)
        rsi_min = config.get("rsi_long_min", 48)
        rsi_max = config.get("rsi_long_max", 68)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    elif signal_type == SignalType.SHORT:
        if not row.get("ema_cross_down2", False):
            return False
        if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
            if row["ema_fast"] >= row["ema_slow"]:
                return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False
        rsi_min = config.get("rsi_short_min", 30)
        rsi_max = config.get("rsi_short_max", 52)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    # Volume filter — stricter than main crossover to reduce noise
    vol_mult = config.get("fast_crossover_volume_mult", config.get("volume_mult", 1.3) * 1.5)
    if pd.notna(row.get("volume_ma")) and row.get("volume_ma", 0) > 0:
        if row["volume"] < row["volume_ma"] * vol_mult:
            return False

    return True


def check_mean_reversion_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check mean-reversion entry conditions for ranging markets.

    Buys when price bounces off lower BB with RSI oversold.
    Sells when price rejects upper BB with RSI overbought.
    Only valid in ranging regime.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if mean-reversion conditions are satisfied.
    """
    if pd.isna(row.get("rsi")) or pd.isna(row.get("atr")) or pd.isna(row.get("bb_lower")):
        return False

    if row["atr"] < config.get("atr_min", 50.0):
        return False

    # MUST be ranging regime — this signal is meaningless in trending/volatile
    regime = row.get("regime", "trending")
    if not isinstance(regime, str):
        regime = "trending"
    if regime != "ranging":
        return False

    mr_rsi_oversold = config.get("mr_rsi_oversold", 30)
    mr_rsi_overbought = config.get("mr_rsi_overbought", 70)

    if signal_type == SignalType.LONG:
        # Price touched or went below lower BB, then closed above it (bounce)
        if not (row["low"] <= row["bb_lower"] and row["close"] > row["bb_lower"]):
            return False
        # RSI must be oversold
        if row["rsi"] > mr_rsi_oversold:
            return False

    elif signal_type == SignalType.SHORT:
        # Price touched or went above upper BB, then closed below it (rejection)
        if pd.isna(row.get("bb_upper")):
            return False
        if not (row["high"] >= row["bb_upper"] and row["close"] < row["bb_upper"]):
            return False
        # RSI must be overbought
        if row["rsi"] < mr_rsi_overbought:
            return False

    return True


def check_body_dominance_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check body dominance entry conditions.

    Large-bodied candles (body > 65% of range) with momentum and volume surge
    signal strong directional conviction. The candle direction must match the
    signal direction.

    Args:
        row: Current candle row with indicators (body_pct, mom10, volume_ma).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if all body dominance conditions are satisfied.
    """
    if pd.isna(row.get("atr")):
        return False
    if row["atr"] < config.get("atr_min", 0.0):
        return False

    min_body = config.get("body_dominance_min_body", 0.65)
    min_mom = config.get("body_dominance_min_mom", 0.02)
    min_vol = config.get("body_dominance_min_vol", 1.5)

    # Prefer 1H indicators when available (signal discovered on 1H data)
    body = row.get("body_pct_1h") if pd.notna(row.get("body_pct_1h")) else row.get("body_pct")
    mom = row.get("mom10_1h") if pd.notna(row.get("mom10_1h")) else row.get("mom10")
    vol_r = row.get("vol_ratio_1h")
    close_h = row.get("close_1h") if pd.notna(row.get("close_1h")) else row.get("close")
    open_h = row.get("open_1h") if pd.notna(row.get("open_1h")) else row.get("open")

    if pd.isna(body) or pd.isna(mom):
        return False
    if body < min_body:
        return False

    if signal_type == SignalType.LONG:
        if close_h <= open_h:
            return False
        if mom < min_mom:
            return False
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False
    elif signal_type == SignalType.SHORT:
        if close_h >= open_h:
            return False
        if mom > -min_mom:
            return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False

    # Volume surge — prefer 1H ratio when available
    if pd.notna(vol_r) and vol_r > 0:
        if vol_r < min_vol:
            return False
    else:
        vol_ma = row.get("volume_ma")
        if pd.notna(vol_ma) and vol_ma > 0:
            if row["volume"] < vol_ma * min_vol:
                return False

    return True


def check_squeeze_release_conditions(
    row: pd.Series, prev_row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check squeeze release (ATR expansion) entry conditions.

    When ATR has been compressed (squeeze < low_threshold) and then expands
    (squeeze > high_threshold), a directional breakout is starting.

    Args:
        row: Current candle row with indicators (squeeze, ema_slow, mom4).
        prev_row: Previous candle row (for squeeze_prev check).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if squeeze release conditions are satisfied.
    """
    if pd.isna(row.get("atr")):
        return False
    if row["atr"] < config.get("atr_min", 0.0):
        return False

    squeeze_low = config.get("squeeze_release_low", 0.7)
    squeeze_high = config.get("squeeze_release_high", 0.8)
    min_mom4 = config.get("squeeze_release_min_mom4", 0.0)

    # Prefer 1H squeeze when available (signal discovered on 1H data)
    sq_now = row.get("squeeze_1h") if pd.notna(row.get("squeeze_1h")) else row.get("squeeze")
    sq_prev = prev_row.get("squeeze_1h") if pd.notna(prev_row.get("squeeze_1h")) else prev_row.get("squeeze")
    mom4 = row.get("mom4_1h") if pd.notna(row.get("mom4_1h")) else row.get("mom4")

    if pd.isna(sq_now) or pd.isna(sq_prev) or pd.isna(mom4):
        return False

    if sq_prev >= squeeze_low:
        return False
    if sq_now <= squeeze_high:
        return False

    if signal_type == SignalType.LONG:
        # Price above EMA21 (trend confirmation)
        ema_slow = row.get("ema_slow")
        if pd.notna(ema_slow) and row["close"] <= ema_slow:
            return False
        # Short-term momentum must be positive (use 1H mom4 if available)
        if mom4 <= min_mom4:
            return False
        # Trend alignment via higher timeframe if available
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False

    elif signal_type == SignalType.SHORT:
        # Price below EMA21
        ema_slow = row.get("ema_slow")
        if pd.notna(ema_slow) and row["close"] >= ema_slow:
            return False
        # Short-term momentum must be negative (use 1H mom4 if available)
        if mom4 >= -min_mom4:
            return False
        # Trend alignment
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False

    return True


def check_pin_bar_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check pin bar entry conditions at EMA pullback zone.

    Pin bar at EMA = stop-hunt reversal pattern. The wick sweeps past EMA
    (liquidity hunt) then closes back, signaling continuation.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if pin bar conditions are satisfied.
    """
    if pd.isna(row.get("rsi")) or pd.isna(row.get("atr")):
        return False
    if row["atr"] < config.get("atr_min", 0.0):
        return False

    # EMA proximity check — pin bar must be near EMA zone
    ema_prox = config.get("pin_bar_ema_proximity_pct", 0.003)  # 0.3%
    ema_slow = row.get("ema_slow")
    if pd.isna(ema_slow) or ema_slow == 0:
        return False

    if signal_type == SignalType.LONG:
        if not row.get("pin_bar_bull", False):
            return False
        # Wick must touch or pierce EMA zone (lower wick near EMA slow)
        distance = abs(row["low"] - ema_slow) / ema_slow
        if distance > ema_prox and row["low"] > ema_slow:
            return False  # Pin bar wick didn't reach EMA
        # EMA trend must be bullish
        if pd.notna(row.get("ema_fast")) and row["ema_fast"] <= row["ema_slow"]:
            return False
        # 1h trend filter
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False
        # RSI: not overbought (pin bars work best at moderate RSI)
        rsi_min = config.get("rsi_long_min", 45)
        rsi_max = config.get("pin_bar_rsi_long_max", 65)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    elif signal_type == SignalType.SHORT:
        if not row.get("pin_bar_bear", False):
            return False
        distance = abs(row["high"] - ema_slow) / ema_slow
        if distance > ema_prox and row["high"] < ema_slow:
            return False
        if pd.notna(row.get("ema_fast")) and row["ema_fast"] >= row["ema_slow"]:
            return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False
        rsi_min = config.get("pin_bar_rsi_short_min", 35)
        rsi_max = config.get("rsi_short_max", 55)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    # Volume: pin bars don't need high volume (stop hunt, not buying pressure)
    vol_mult = config.get("pin_bar_volume_mult", 0.7)
    if pd.notna(row.get("volume_ma")) and row.get("volume_ma", 0) > 0:
        if row["volume"] < row["volume_ma"] * vol_mult:
            return False

    return True


def check_engulfing_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check engulfing candle entry conditions in trend direction.

    Engulfing in trend = counter-trend traders get trapped. Their forced
    exits fuel the continuation move.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if engulfing conditions are satisfied.
    """
    if pd.isna(row.get("rsi")) or pd.isna(row.get("atr")):
        return False
    if row["atr"] < config.get("atr_min", 0.0):
        return False

    if signal_type == SignalType.LONG:
        if not row.get("engulfing_bull", False):
            return False
        # EMA trend must be bullish
        if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
            if row["ema_fast"] <= row["ema_slow"]:
                return False
        # 1h trend filter
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False
        # RSI filter
        rsi_min = config.get("rsi_long_min", 45)
        rsi_max = config.get("engulfing_rsi_long_max", 70)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    elif signal_type == SignalType.SHORT:
        if not row.get("engulfing_bear", False):
            return False
        if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
            if row["ema_fast"] >= row["ema_slow"]:
                return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False
        rsi_min = config.get("engulfing_rsi_short_min", 30)
        rsi_max = config.get("rsi_short_max", 55)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    # Volume: engulfing needs confirmation
    vol_mult = config.get("engulfing_volume_mult", 1.0)
    if pd.notna(row.get("volume_ma")) and row.get("volume_ma", 0) > 0:
        if row["volume"] < row["volume_ma"] * vol_mult:
            return False

    return True


def check_inside_bar_breakout_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check inside bar breakout conditions.

    Inside bar = consolidation/compression. Breakout in trend direction
    captures expansion phase. Tight SL from IB structure gives excellent R:R.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if inside bar breakout conditions are satisfied.
    """
    if pd.isna(row.get("rsi")) or pd.isna(row.get("atr")):
        return False
    if row["atr"] < config.get("atr_min", 0.0):
        return False

    if signal_type == SignalType.LONG:
        if not row.get("ib_breakout_bull", False):
            return False
        # EMA trend bullish
        if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
            if row["ema_fast"] <= row["ema_slow"]:
                return False
        # 1h trend filter
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False
        # RSI
        rsi_min = config.get("rsi_long_min", 45)
        rsi_max = config.get("rsi_long_max", 65)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    elif signal_type == SignalType.SHORT:
        if not row.get("ib_breakout_bear", False):
            return False
        if pd.notna(row.get("ema_fast")) and pd.notna(row.get("ema_slow")):
            if row["ema_fast"] >= row["ema_slow"]:
                return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False
        rsi_min = config.get("rsi_short_min", 35)
        rsi_max = config.get("rsi_short_max", 55)
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    # Volume: breakout needs volume confirmation
    vol_mult = config.get("inside_bar_breakout_volume_mult", 1.2)
    if pd.notna(row.get("volume_ma")) and row.get("volume_ma", 0) > 0:
        if row["volume"] < row["volume_ma"] * vol_mult:
            return False

    return True


def check_bb_breakout_conditions(
    row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check Bollinger Band squeeze breakout conditions.

    Requires the BB width to be in the bottom percentile (squeeze) and then
    price to break out above/below the upper/lower band with volume confirmation.

    Args:
        row: Current candle row with indicators.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if BB breakout conditions are satisfied.
    """
    if pd.isna(row.get("bb_width_pct")) or pd.isna(row.get("atr")):
        return False

    if row["atr"] < config.get("atr_min", 0.0):
        return False

    # Must be in squeeze (BB width in bottom percentile)
    squeeze_pct = config.get("bb_squeeze_percentile", 0.25)
    if row["bb_width_pct"] > squeeze_pct:
        return False

    if signal_type == SignalType.LONG:
        # Price breaks above upper BB
        bb_upper = row.get("bb_upper")
        if pd.isna(bb_upper) or row["close"] <= bb_upper:
            return False
        # Trend alignment
        if "above_trend" in row.index and pd.notna(row["above_trend"]):
            if not row["above_trend"]:
                return False
        # Volume confirmation
        vol_mult = config.get("bb_volume_mult", 1.5)
        vol_ma = row.get("volume_ma")
        if pd.notna(vol_ma) and vol_ma > 0:
            if row["volume"] < vol_ma * vol_mult:
                return False

    elif signal_type == SignalType.SHORT:
        # Price breaks below lower BB
        bb_lower = row.get("bb_lower")
        if pd.isna(bb_lower) or row["close"] >= bb_lower:
            return False
        if "below_trend" in row.index and pd.notna(row["below_trend"]):
            if not row["below_trend"]:
                return False
        vol_mult = config.get("bb_volume_mult", 1.5)
        vol_ma = row.get("volume_ma")
        if pd.notna(vol_ma) and vol_ma > 0:
            if row["volume"] < vol_ma * vol_mult:
                return False

    return True


def check_supertrend_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Supertrend direction change signal.

    LONG: Supertrend direction flipped from bearish (-1) to bullish (1).
    SHORT: Supertrend direction flipped from bullish (1) to bearish (-1).

    Requires supertrend_dir column (added by compute_supertrend via add_indicators).

    Args:
        row: Current candle row with supertrend indicators.
        prev_row: Previous candle row (for direction change detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if a Supertrend direction change matches signal_type.
    """
    if not config.get("signals", {}).get("supertrend", {}).get("enabled", False):
        return False

    curr_dir = row.get("supertrend_dir", 0)
    prev_dir = prev_row.get("supertrend_dir", 0)

    if pd.isna(curr_dir) or pd.isna(prev_dir):
        return False

    # ATR minimum volatility check
    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    if signal_type == SignalType.LONG:
        # Bearish → Bullish flip
        return prev_dir <= 0 and curr_dir > 0
    elif signal_type == SignalType.SHORT:
        # Bullish → Bearish flip
        return prev_dir >= 0 and curr_dir < 0

    return False


def check_ichimoku_conditions(
    row: pd.Series, prev_row: pd.Series, config: dict, signal_type: SignalType
) -> bool:
    """Check Ichimoku Cloud entry conditions.

    LONG: Tenkan crosses above Kijun AND close is above the cloud top.
    SHORT: Tenkan crosses below Kijun AND close is below the cloud bottom.

    Requires Ichimoku columns (tenkan, kijun, cloud_top, cloud_bottom) to be
    present in the DataFrame (added by add_ichimoku_indicators()).

    Args:
        row: Current candle row with Ichimoku indicators.
        prev_row: Previous candle row (for crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Ichimoku conditions are satisfied.
    """
    # Guard: all required columns must be present and non-NaN
    required = ("tenkan", "kijun", "cloud_top", "cloud_bottom", "atr")
    for col in required:
        if pd.isna(row.get(col)):
            return False
        if pd.isna(prev_row.get(col)):
            return False

    # ATR minimum volatility check
    atr_min = config.get("atr_min", 0.0)
    if row["atr"] < atr_min:
        return False

    tenkan = row["tenkan"]
    kijun = row["kijun"]
    prev_tenkan = prev_row["tenkan"]
    prev_kijun = prev_row["kijun"]
    close = row["close"]
    cloud_top = row["cloud_top"]
    cloud_bottom = row["cloud_bottom"]

    if signal_type == SignalType.LONG:
        # Tenkan crosses above Kijun
        crosses_above = tenkan > kijun and prev_tenkan <= prev_kijun
        if not crosses_above:
            return False
        # Price must be above the cloud
        if close <= cloud_top:
            return False

    elif signal_type == SignalType.SHORT:
        # Tenkan crosses below Kijun
        crosses_below = tenkan < kijun and prev_tenkan >= prev_kijun
        if not crosses_below:
            return False
        # Price must be below the cloud
        if close >= cloud_bottom:
            return False

    return True


def check_vol_expansion_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Volatility Expansion Breakout entry conditions.

    LONG:  ATR expanding above its rolling mean  AND  close broke above recent high
           AND  close is above EMA(50) trend filter.
    SHORT: ATR expanding above its rolling mean  AND  close broke below recent low
           AND  close is below EMA(50) trend filter.

    Requires vol_expanding, vol_break_high, vol_break_low columns (added by
    add_indicators() when vol_expansion signal is enabled in config).

    Uses prev_row (closed candle) to avoid look-ahead bias.

    Args:
        row: Current candle row (unused, kept for consistent interface).
        prev_row: Previous closed candle row with vol_expansion indicators.
        config: Bot configuration with vol_expansion parameters.
        signal_type: LONG or SHORT.

    Returns:
        True if Volatility Expansion Breakout conditions are satisfied.
    """
    if not config.get("signals", {}).get("vol_expansion", {}).get("enabled", False):
        return False

    # All required columns must be present and non-NaN
    for col in ("vol_expanding", "vol_break_high", "vol_break_low", "atr"):
        val = prev_row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    # ATR expanding state — primary volatility gate
    if not prev_row.get("vol_expanding", False):
        return False

    # ATR minimum volatility check
    atr_min = config.get("atr_min", 0.0)
    if pd.isna(prev_row.get("atr")) or prev_row["atr"] < atr_min:
        return False

    # EMA(50) trend filter: use ema_trend (from add_trend_filter) or ema50 from data
    ema50 = prev_row.get("ema_trend", prev_row.get("ema_trend_1h", prev_row.get("ema50", 0)))
    if pd.isna(ema50) or ema50 == 0:
        return False

    if signal_type == SignalType.LONG:
        if not prev_row.get("vol_break_high", False):
            return False
        # Trend filter: close must be above EMA(50)
        if prev_row["close"] <= ema50:
            return False
        return True

    elif signal_type == SignalType.SHORT:
        if not prev_row.get("vol_break_low", False):
            return False
        # Trend filter: close must be below EMA(50)
        if prev_row["close"] >= ema50:
            return False
        return True

    return False


def check_dual_supertrend_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Dual Supertrend entry conditions.

    LONG:  Fast Supertrend flips to bullish (prev -1 → now 1) AND slow is bullish (1).
    SHORT: Fast Supertrend flips to bearish (prev 1 → now -1) AND slow is bearish (-1).

    Requires dst_fast_dir and dst_slow_dir columns (added by add_indicators when
    dual_supertrend signal is enabled).

    Args:
        row: Current candle row with dual supertrend columns.
        prev_row: Previous candle row (for fast direction flip detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if dual supertrend conditions are satisfied.
    """
    if not config.get("signals", {}).get("dual_supertrend", {}).get("enabled", False):
        return False

    fast_dir = row.get("dst_fast_dir", 0)
    prev_fast_dir = prev_row.get("dst_fast_dir", 0)
    slow_dir = row.get("dst_slow_dir", 0)

    if pd.isna(fast_dir) or pd.isna(prev_fast_dir) or pd.isna(slow_dir):
        return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    if signal_type == SignalType.LONG:
        # Fast flips to bullish AND slow already bullish
        return int(prev_fast_dir) <= 0 and int(fast_dir) > 0 and int(slow_dir) > 0
    elif signal_type == SignalType.SHORT:
        # Fast flips to bearish AND slow already bearish
        return int(prev_fast_dir) >= 0 and int(fast_dir) < 0 and int(slow_dir) < 0

    return False


def check_alligator_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Williams Alligator entry conditions.

    LONG:  Lips crosses above Teeth (prev lips <= teeth, now lips > teeth)
           AND Teeth > Jaw (alligator opening upward)
           AND close > Lips (price above alligator).
    SHORT: Lips crosses below Teeth (prev lips >= teeth, now lips < teeth)
           AND Teeth < Jaw (alligator opening downward)
           AND close < Lips (price below alligator).

    Requires alligator_jaw, alligator_teeth, alligator_lips columns (added by
    add_indicators when alligator signal is enabled).

    Args:
        row: Current candle row with alligator columns.
        prev_row: Previous candle row (for lips/teeth crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if alligator conditions are satisfied.
    """
    if not config.get("signals", {}).get("alligator", {}).get("enabled", False):
        return False

    jaw = row.get("alligator_jaw")
    teeth = row.get("alligator_teeth")
    lips = row.get("alligator_lips")
    prev_teeth = prev_row.get("alligator_teeth")
    prev_lips = prev_row.get("alligator_lips")
    close = row.get("close")

    for val in (jaw, teeth, lips, prev_teeth, prev_lips, close):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    if signal_type == SignalType.LONG:
        lips_crosses_above = prev_lips <= prev_teeth and lips > teeth
        teeth_above_jaw = teeth > jaw
        price_above_lips = close > lips
        return lips_crosses_above and teeth_above_jaw and price_above_lips

    elif signal_type == SignalType.SHORT:
        lips_crosses_below = prev_lips >= prev_teeth and lips < teeth
        teeth_below_jaw = teeth < jaw
        price_below_lips = close < lips
        return lips_crosses_below and teeth_below_jaw and price_below_lips

    return False


def check_ema_ichimoku_hybrid_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check EMA + Ichimoku hybrid entry conditions.

    LONG:  EMA(fast) crosses above EMA(slow) AND close > cloud_top.
    SHORT: EMA(fast) crosses below EMA(slow) AND close < cloud_bottom.

    Combines EMA crossover momentum with Ichimoku cloud trend confirmation.
    Requires ema_fast, ema_slow, cloud_top, cloud_bottom columns.

    Args:
        row: Current candle row with EMA and Ichimoku columns.
        prev_row: Previous candle row (for EMA crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if EMA+Ichimoku hybrid conditions are satisfied.
    """
    if not config.get("signals", {}).get("ema_ichimoku_hybrid", {}).get("enabled", False):
        return False

    ema_fast = row.get("ema_fast")
    ema_slow = row.get("ema_slow")
    prev_ema_fast = prev_row.get("ema_fast")
    prev_ema_slow = prev_row.get("ema_slow")
    cloud_top = row.get("cloud_top")
    cloud_bottom = row.get("cloud_bottom")
    close = row.get("close")

    for val in (ema_fast, ema_slow, prev_ema_fast, prev_ema_slow, cloud_top, cloud_bottom, close):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    if signal_type == SignalType.LONG:
        ema_cross_up = ema_fast > ema_slow and prev_ema_fast <= prev_ema_slow
        above_cloud = close > cloud_top
        return ema_cross_up and above_cloud

    elif signal_type == SignalType.SHORT:
        ema_cross_down = ema_fast < ema_slow and prev_ema_fast >= prev_ema_slow
        below_cloud = close < cloud_bottom
        return ema_cross_down and below_cloud

    return False


def check_ichi_supertrend_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Ichimoku + Supertrend confluence entry conditions.

    LONG:  Supertrend flips to bullish (prev -1 → now 1) AND close > cloud_top.
    SHORT: Supertrend flips to bearish (prev 1 → now -1) AND close < cloud_bottom.

    Requires supertrend_dir, cloud_top, cloud_bottom columns.
    The Supertrend flip provides the trigger; the Ichimoku cloud provides the
    trend filter — both must agree for a valid signal.

    Args:
        row: Current candle row with supertrend and Ichimoku columns.
        prev_row: Previous candle row (for supertrend flip detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Ichimoku + Supertrend conditions are satisfied.
    """
    if not config.get("signals", {}).get("ichi_supertrend", {}).get("enabled", False):
        return False

    curr_dir = row.get("supertrend_dir", 0)
    prev_dir = prev_row.get("supertrend_dir", 0)
    cloud_top = row.get("cloud_top")
    cloud_bottom = row.get("cloud_bottom")
    close = row.get("close")

    for val in (curr_dir, prev_dir, cloud_top, cloud_bottom, close):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    if signal_type == SignalType.LONG:
        st_flip_bull = int(prev_dir) <= 0 and int(curr_dir) > 0
        above_cloud = close > cloud_top
        return st_flip_bull and above_cloud

    elif signal_type == SignalType.SHORT:
        st_flip_bear = int(prev_dir) >= 0 and int(curr_dir) < 0
        below_cloud = close < cloud_bottom
        return st_flip_bear and below_cloud

    return False


def check_volexp_supertrend_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Vol Expansion + Supertrend confluence entry conditions.

    LONG:  Volatility expanding AND price broke above recent high AND Supertrend bullish.
    SHORT: Volatility expanding AND price broke below recent low AND Supertrend bearish.

    Uses the previous closed candle (prev_row) for vol expansion signals to avoid
    look-ahead bias — consistent with how check_vol_expansion_conditions works.
    Requires vol_expanding, vol_break_high, vol_break_low, supertrend_dir columns.

    Args:
        row: Current candle row (unused, kept for consistent interface).
        prev_row: Previous closed candle with vol expansion and supertrend columns.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Vol Expansion + Supertrend conditions are satisfied.
    """
    if not config.get("signals", {}).get("volexp_supertrend", {}).get("enabled", False):
        return False

    for col in ("vol_expanding", "vol_break_high", "vol_break_low", "supertrend_dir", "atr"):
        val = prev_row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    if not prev_row.get("vol_expanding", False):
        return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(prev_row.get("atr")) or prev_row["atr"] < atr_min:
        return False

    st_dir = int(prev_row.get("supertrend_dir", 0))

    if signal_type == SignalType.LONG:
        return bool(prev_row.get("vol_break_high", False)) and st_dir > 0

    elif signal_type == SignalType.SHORT:
        return bool(prev_row.get("vol_break_low", False)) and st_dir < 0

    return False


def check_adx_di_cross_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check ADX + DI crossover entry conditions.

    LONG:  DI+ crosses above DI- AND ADX > 20 AND ADX is rising (> prev ADX).
    SHORT: DI- crosses above DI+ AND ADX > 20 AND ADX is rising.

    Requires adx, di_plus, di_minus columns (added by add_indicators when
    adx_di_cross signal is enabled in config).

    Args:
        row: Current candle row with ADX/DI columns.
        prev_row: Previous candle row (for crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if ADX+DI conditions are satisfied.
    """
    if not config.get("signals", {}).get("adx_di_cross", {}).get("enabled", False):
        return False

    adx = row.get("adx")
    prev_adx = prev_row.get("adx")
    di_plus = row.get("di_plus")
    di_minus = row.get("di_minus")
    prev_di_plus = prev_row.get("di_plus")
    prev_di_minus = prev_row.get("di_minus")

    for val in (adx, prev_adx, di_plus, di_minus, prev_di_plus, prev_di_minus):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    adx_threshold = config.get("adx_di_threshold", 20.0)
    if float(adx) <= adx_threshold:
        return False
    # ADX must be rising
    if float(adx) <= float(prev_adx):
        return False

    if signal_type == SignalType.LONG:
        # DI+ crosses above DI-
        di_cross_up = float(di_plus) > float(di_minus) and float(prev_di_plus) <= float(prev_di_minus)
        return di_cross_up

    elif signal_type == SignalType.SHORT:
        # DI- crosses above DI+
        di_cross_down = float(di_minus) > float(di_plus) and float(prev_di_minus) <= float(prev_di_plus)
        return di_cross_down

    return False


def check_choppiness_ema_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Choppiness Index + EMA crossover entry conditions.

    LONG:  CI < trending_threshold AND EMA(fast) crosses above EMA(slow).
    SHORT: CI < trending_threshold AND EMA(fast) crosses below EMA(slow).

    A low CI value confirms the market is in a trend (not choppy), filtering
    out false EMA crossovers in ranging conditions.

    Requires choppiness, ema_fast, ema_slow columns.

    Args:
        row: Current candle row with choppiness and EMA columns.
        prev_row: Previous candle row (for EMA crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Choppiness+EMA conditions are satisfied.
    """
    if not config.get("signals", {}).get("choppiness_ema", {}).get("enabled", False):
        return False

    ci = row.get("choppiness")
    ema_fast = row.get("ema_fast")
    ema_slow = row.get("ema_slow")
    prev_ema_fast = prev_row.get("ema_fast")
    prev_ema_slow = prev_row.get("ema_slow")

    for val in (ci, ema_fast, ema_slow, prev_ema_fast, prev_ema_slow):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    # CI < threshold confirms trending (non-choppy) market
    ci_trend_threshold = config.get("choppiness_trend_threshold", 38.2)
    if float(ci) >= ci_trend_threshold:
        return False

    if signal_type == SignalType.LONG:
        # EMA fast crosses above EMA slow
        cross_up = float(ema_fast) > float(ema_slow) and float(prev_ema_fast) <= float(prev_ema_slow)
        return cross_up

    elif signal_type == SignalType.SHORT:
        # EMA fast crosses below EMA slow
        cross_down = float(ema_fast) < float(ema_slow) and float(prev_ema_fast) >= float(prev_ema_slow)
        return cross_down

    return False


def check_williams_r_adx_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Williams %R + ADX entry conditions.

    LONG:  Williams %R crosses above oversold level (-80) AND ADX > 25
           AND close > EMA(50) trend filter.
    SHORT: Williams %R crosses below overbought level (-20) AND ADX > 25
           AND close < EMA(50) trend filter.

    Requires williams_r, adx, ema50 columns.

    Args:
        row: Current candle row with Williams %R, ADX, EMA50 columns.
        prev_row: Previous candle row (for Williams %R crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Williams %R + ADX conditions are satisfied.
    """
    if not config.get("signals", {}).get("williams_r_adx", {}).get("enabled", False):
        return False

    wr = row.get("williams_r")
    prev_wr = prev_row.get("williams_r")
    adx = row.get("adx")

    for val in (wr, prev_wr, adx):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    adx_threshold = config.get("williams_r_adx_threshold", 25.0)
    if float(adx) <= adx_threshold:
        return False

    # EMA(50) trend filter
    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    oversold_level = config.get("williams_r_oversold", -80.0)
    overbought_level = config.get("williams_r_overbought", -20.0)

    if signal_type == SignalType.LONG:
        # WR crosses above oversold (from below -80 to above -80)
        wr_cross_up = float(wr) > oversold_level and float(prev_wr) <= oversold_level
        if not wr_cross_up:
            return False
        # Trend filter: close above EMA(50)
        if row.get("close", 0) <= float(ema50):
            return False
        return True

    elif signal_type == SignalType.SHORT:
        # WR crosses below overbought (from above -20 to below -20)
        wr_cross_down = float(wr) < overbought_level and float(prev_wr) >= overbought_level
        if not wr_cross_down:
            return False
        # Trend filter: close below EMA(50)
        if row.get("close", 0) >= float(ema50):
            return False
        return True

    return False


def check_roc_momentum_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check ROC (Rate of Change) momentum zero-cross entry conditions.

    LONG:  ROC crosses above 0 AND close > EMA(50) trend filter.
    SHORT: ROC crosses below 0 AND close < EMA(50) trend filter.

    Requires roc, ema50 columns.

    Args:
        row: Current candle row with ROC and EMA50 columns.
        prev_row: Previous candle row (for ROC zero-cross detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if ROC momentum conditions are satisfied.
    """
    if not config.get("signals", {}).get("roc_momentum", {}).get("enabled", False):
        return False

    roc = row.get("roc")
    prev_roc = prev_row.get("roc")

    for val in (roc, prev_roc):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    # EMA(50) trend filter
    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    if signal_type == SignalType.LONG:
        # ROC crosses above zero
        roc_cross_up = float(roc) > 0.0 and float(prev_roc) <= 0.0
        if not roc_cross_up:
            return False
        if row.get("close", 0) <= float(ema50):
            return False
        return True

    elif signal_type == SignalType.SHORT:
        # ROC crosses below zero
        roc_cross_down = float(roc) < 0.0 and float(prev_roc) >= 0.0
        if not roc_cross_down:
            return False
        if row.get("close", 0) >= float(ema50):
            return False
        return True

    return False


def check_stoch_supertrend_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Stochastic + Supertrend entry conditions.

    LONG:  Stochastic %K crosses above %D in oversold zone (K < 20)
           AND Supertrend direction is bullish (1).
    SHORT: Stochastic %K crosses below %D in overbought zone (K > 80)
           AND Supertrend direction is bearish (-1).

    Requires stoch_k, stoch_d, supertrend_dir columns.

    Args:
        row: Current candle row with stochastic and supertrend columns.
        prev_row: Previous candle row (for stochastic crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Stochastic + Supertrend conditions are satisfied.
    """
    if not config.get("signals", {}).get("stoch_supertrend", {}).get("enabled", False):
        return False

    k = row.get("stoch_k")
    d = row.get("stoch_d")
    prev_k = prev_row.get("stoch_k")
    prev_d = prev_row.get("stoch_d")
    st_dir = row.get("supertrend_dir")

    for val in (k, d, prev_k, prev_d, st_dir):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    oversold = config.get("stoch_oversold", 20.0)
    overbought = config.get("stoch_overbought", 80.0)

    if signal_type == SignalType.LONG:
        # K crosses above D in oversold territory
        k_cross_up = float(k) > float(d) and float(prev_k) <= float(prev_d)
        in_oversold = float(k) < oversold
        st_bullish = int(st_dir) > 0
        return k_cross_up and in_oversold and st_bullish

    elif signal_type == SignalType.SHORT:
        # K crosses below D in overbought territory
        k_cross_down = float(k) < float(d) and float(prev_k) >= float(prev_d)
        in_overbought = float(k) > overbought
        st_bearish = int(st_dir) < 0
        return k_cross_down and in_overbought and st_bearish

    return False


def check_price_channel_vol_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Price Channel breakout + volume spike entry conditions.

    LONG:  Close > highest high of last N bars (pre-shifted to avoid look-ahead)
           AND volume > 2 × volume MA(20).
    SHORT: Close < lowest low of last N bars (pre-shifted)
           AND volume > 2 × volume MA(20).

    Requires price_channel_high, price_channel_low, volume_ma columns.

    Args:
        row: Current candle row (unused — uses prev_row to avoid look-ahead).
        prev_row: Previous closed candle with price channel columns.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Price Channel + volume conditions are satisfied.
    """
    if not config.get("signals", {}).get("price_channel_vol", {}).get("enabled", False):
        return False

    pc_high = prev_row.get("price_channel_high")
    pc_low = prev_row.get("price_channel_low")
    close = prev_row.get("close")
    vol = prev_row.get("volume")
    vol_ma = prev_row.get("volume_ma")

    for val in (pc_high, pc_low, close, vol, vol_ma):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    if float(vol_ma) <= 0:
        return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(prev_row.get("atr")) or prev_row["atr"] < atr_min:
        return False

    pc_vol_mult = config.get("price_channel_vol_mult", 2.0)
    if float(vol) < float(vol_ma) * pc_vol_mult:
        return False

    if signal_type == SignalType.LONG:
        return float(close) > float(pc_high)

    elif signal_type == SignalType.SHORT:
        return float(close) < float(pc_low)

    return False


def check_ema_alligator_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check EMA crossover + Williams Alligator alignment entry conditions.

    LONG:  EMA(fast) crosses above EMA(slow) AND lips > teeth > jaw
           (alligator fully open upward = trend confirmed).
    SHORT: EMA(fast) crosses below EMA(slow) AND lips < teeth < jaw
           (alligator fully open downward = trend confirmed).

    Requires ema_fast, ema_slow, alligator_jaw, alligator_teeth, alligator_lips columns.

    Args:
        row: Current candle row with EMA and alligator columns.
        prev_row: Previous candle row (for EMA crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if EMA+Alligator conditions are satisfied.
    """
    if not config.get("signals", {}).get("ema_alligator", {}).get("enabled", False):
        return False

    ema_fast = row.get("ema_fast")
    ema_slow = row.get("ema_slow")
    prev_ema_fast = prev_row.get("ema_fast")
    prev_ema_slow = prev_row.get("ema_slow")
    jaw = row.get("alligator_jaw")
    teeth = row.get("alligator_teeth")
    lips = row.get("alligator_lips")

    for val in (ema_fast, ema_slow, prev_ema_fast, prev_ema_slow, jaw, teeth, lips):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    if signal_type == SignalType.LONG:
        ema_cross_up = float(ema_fast) > float(ema_slow) and float(prev_ema_fast) <= float(prev_ema_slow)
        alligator_bullish = float(lips) > float(teeth) > float(jaw)
        return ema_cross_up and alligator_bullish

    elif signal_type == SignalType.SHORT:
        ema_cross_down = float(ema_fast) < float(ema_slow) and float(prev_ema_fast) >= float(prev_ema_slow)
        alligator_bearish = float(lips) < float(teeth) < float(jaw)
        return ema_cross_down and alligator_bearish

    return False


def check_supertrend_volume_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Supertrend direction flip + volume spike entry conditions.

    LONG:  Supertrend flips to bullish (prev -1 → now 1)
           AND volume > 2 × volume MA(20).
    SHORT: Supertrend flips to bearish (prev 1 → now -1)
           AND volume > 2 × volume MA(20).

    The volume spike confirms that the trend flip is accompanied by real
    buying/selling pressure (not a low-conviction noise flip).

    Requires supertrend_dir, volume_ma columns.

    Args:
        row: Current candle row with supertrend and volume columns.
        prev_row: Previous candle row (for direction flip detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Supertrend + volume conditions are satisfied.
    """
    if not config.get("signals", {}).get("supertrend_volume", {}).get("enabled", False):
        return False

    curr_dir = row.get("supertrend_dir")
    prev_dir = prev_row.get("supertrend_dir")
    vol = row.get("volume")
    vol_ma = row.get("volume_ma")

    for val in (curr_dir, prev_dir, vol, vol_ma):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    if float(vol_ma) <= 0:
        return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    st_vol_mult = config.get("supertrend_volume_mult", 2.0)
    if float(vol) < float(vol_ma) * st_vol_mult:
        return False

    if signal_type == SignalType.LONG:
        # Bearish → Bullish flip
        return int(prev_dir) <= 0 and int(curr_dir) > 0

    elif signal_type == SignalType.SHORT:
        # Bullish → Bearish flip
        return int(prev_dir) >= 0 and int(curr_dir) < 0

    return False


def check_ribbon_rsi_vol_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check EMA Ribbon + RSI + Volume combo entry conditions.

    LONG:  EMA ribbon fully aligned bullish (ema8>13>21>34>55>89)
           AND RSI(14) > 50
           AND volume > 1.5 × volume_ma
           AND close > EMA(50)
           AND edge detect: previous row was NOT all conditions true.
    SHORT: EMA ribbon fully aligned bearish (ema8<13<21<34<55<89)
           AND RSI(14) < 50
           AND volume > 1.5 × volume_ma
           AND close < EMA(50)
           AND edge detect.

    Requires ema_ribbon_8/13/21/34/55/89, rsi, volume_ma, ema50 columns.

    Args:
        row: Current closed candle row with indicator columns.
        prev_row: Previous candle row (for edge detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if all ribbon + RSI + volume conditions are satisfied.
    """
    if not config.get("signals", {}).get("ribbon_rsi_vol", {}).get("enabled", False):
        return False

    required_cols = (
        "ema_ribbon_8", "ema_ribbon_13", "ema_ribbon_21",
        "ema_ribbon_34", "ema_ribbon_55", "ema_ribbon_89",
        "rsi", "volume_ma", "atr",
    )
    for col in required_cols:
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False
        val_prev = prev_row.get(col)
        if val_prev is None or (isinstance(val_prev, float) and pd.isna(val_prev)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if float(row["atr"]) < atr_min:
        return False

    vol_mult = config.get("ribbon_rsi_vol_volume_mult", 1.5)
    vol_ma = float(row["volume_ma"])
    if vol_ma <= 0 or float(row.get("volume", 0)) < vol_ma * vol_mult:
        return False

    # EMA(50) trend filter
    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    r8 = float(row["ema_ribbon_8"])
    r13 = float(row["ema_ribbon_13"])
    r21 = float(row["ema_ribbon_21"])
    r34 = float(row["ema_ribbon_34"])
    r55 = float(row["ema_ribbon_55"])
    r89 = float(row["ema_ribbon_89"])

    pr8 = float(prev_row["ema_ribbon_8"])
    pr13 = float(prev_row["ema_ribbon_13"])
    pr21 = float(prev_row["ema_ribbon_21"])
    pr34 = float(prev_row["ema_ribbon_34"])
    pr55 = float(prev_row["ema_ribbon_55"])
    pr89 = float(prev_row["ema_ribbon_89"])

    rsi = float(row["rsi"])
    close = float(row["close"])
    ema50_val = float(ema50)

    if signal_type == SignalType.LONG:
        ribbon_aligned = r8 > r13 > r21 > r34 > r55 > r89
        if not ribbon_aligned:
            return False
        if rsi <= 50.0:
            return False
        if close <= ema50_val:
            return False
        # Edge detect: previous bar was NOT all conditions true
        prev_ribbon_aligned = pr8 > pr13 > pr21 > pr34 > pr55 > pr89
        prev_rsi = float(prev_row.get("rsi", 0))
        prev_close = float(prev_row.get("close", 0))
        prev_vol = float(prev_row.get("volume", 0))
        prev_vol_ma = float(prev_row.get("volume_ma", 1))
        prev_ema50 = prev_row.get("ema_trend", prev_row.get("ema_trend_1h", prev_row.get("ema50", 0)))
        prev_ema50_val = float(prev_ema50) if prev_ema50 is not None and not pd.isna(prev_ema50) else 0.0
        prev_all_true = (
            prev_ribbon_aligned
            and prev_rsi > 50.0
            and prev_vol_ma > 0 and prev_vol >= prev_vol_ma * vol_mult
            and prev_close > prev_ema50_val
        )
        if prev_all_true:
            return False  # Already triggered; wait for a new edge
        return True

    elif signal_type == SignalType.SHORT:
        ribbon_aligned = r8 < r13 < r21 < r34 < r55 < r89
        if not ribbon_aligned:
            return False
        if rsi >= 50.0:
            return False
        if close >= ema50_val:
            return False
        # Edge detect
        prev_ribbon_aligned = pr8 < pr13 < pr21 < pr34 < pr55 < pr89
        prev_rsi = float(prev_row.get("rsi", 100))
        prev_close = float(prev_row.get("close", 0))
        prev_vol = float(prev_row.get("volume", 0))
        prev_vol_ma = float(prev_row.get("volume_ma", 1))
        prev_ema50 = prev_row.get("ema_trend", prev_row.get("ema_trend_1h", prev_row.get("ema50", 0)))
        prev_ema50_val = float(prev_ema50) if prev_ema50 is not None and not pd.isna(prev_ema50) else 0.0
        prev_all_true = (
            prev_ribbon_aligned
            and prev_rsi < 50.0
            and prev_vol_ma > 0 and prev_vol >= prev_vol_ma * vol_mult
            and prev_close < prev_ema50_val
        )
        if prev_all_true:
            return False
        return True

    return False


def check_dualthrust_adx_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Dual Thrust + ADX combo entry conditions.

    LONG:  close > dt_upper (breakout above)
           AND prev_close <= prev_dt_upper (fresh breakout, not continuation)
           AND ADX > 25 (trending)
           AND DI+ > DI- (bullish direction)
           AND close > EMA(50).
    SHORT: close < dt_lower
           AND prev_close >= prev_dt_lower
           AND ADX > 25
           AND DI- > DI+
           AND close < EMA(50).

    Requires dt_upper, dt_lower, adx, di_plus, di_minus, ema50 columns.

    Args:
        row: Current closed candle row with indicator columns.
        prev_row: Previous candle row (for fresh breakout detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if all Dual Thrust + ADX conditions are satisfied.
    """
    if not config.get("signals", {}).get("dualthrust_adx", {}).get("enabled", False):
        return False

    required = ("dt_upper", "dt_lower", "adx", "di_plus", "di_minus", "atr")
    for col in required:
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False
    # prev_row needs dt_upper and dt_lower for fresh-breakout check
    for col in ("dt_upper", "dt_lower"):
        val = prev_row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if float(row["atr"]) < atr_min:
        return False

    adx_threshold = config.get("dualthrust_adx_threshold", 25.0)
    if float(row["adx"]) <= adx_threshold:
        return False

    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    close = float(row["close"])
    dt_upper = float(row["dt_upper"])
    dt_lower = float(row["dt_lower"])
    prev_close = float(prev_row.get("close", close))
    prev_dt_upper = float(prev_row["dt_upper"])
    prev_dt_lower = float(prev_row["dt_lower"])
    di_plus = float(row["di_plus"])
    di_minus = float(row["di_minus"])

    if signal_type == SignalType.LONG:
        if close <= dt_upper:
            return False
        if prev_close > prev_dt_upper:
            return False  # Not a fresh breakout
        if di_plus <= di_minus:
            return False
        if close <= float(ema50):
            return False
        return True

    elif signal_type == SignalType.SHORT:
        if close >= dt_lower:
            return False
        if prev_close < prev_dt_lower:
            return False  # Not a fresh breakout
        if di_minus <= di_plus:
            return False
        if close >= float(ema50):
            return False
        return True

    return False


def check_zscore_stoch_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Z-Score + Stochastic mean-reversion combo entry conditions.

    LONG:  zscore < -2.0 (extreme below mean)
           AND (stoch_k < 20 OR stoch_k crosses above stoch_d)
           AND close > EMA(50) (structural uptrend only).
    SHORT: zscore > +2.0 (extreme above mean)
           AND (stoch_k > 80 OR stoch_k crosses below stoch_d)
           AND close < EMA(50).

    NOTE: This is mean reversion — place OUTSIDE trend_signals_gated in engine.

    Requires zscore, stoch_k, stoch_d, ema50 columns.

    Args:
        row: Current closed candle row with indicator columns.
        prev_row: Previous candle row (for stochastic cross detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if all Z-Score + Stochastic conditions are satisfied.
    """
    if not config.get("signals", {}).get("zscore_stoch", {}).get("enabled", False):
        return False

    required = ("zscore", "stoch_k", "stoch_d", "atr")
    for col in required:
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False
    for col in ("stoch_k", "stoch_d"):
        val = prev_row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if float(row["atr"]) < atr_min:
        return False

    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    zscore_threshold = config.get("zscore_stoch_threshold", 2.0)
    oversold_level = config.get("zscore_stoch_oversold", 20.0)
    overbought_level = config.get("zscore_stoch_overbought", 80.0)

    zscore = float(row["zscore"])
    k = float(row["stoch_k"])
    d = float(row["stoch_d"])
    prev_k = float(prev_row["stoch_k"])
    prev_d = float(prev_row["stoch_d"])
    close = float(row["close"])
    ema50_val = float(ema50)

    if signal_type == SignalType.LONG:
        if zscore >= -zscore_threshold:
            return False
        stoch_oversold = k < oversold_level
        stoch_cross_up = k > d and prev_k <= prev_d
        if not (stoch_oversold or stoch_cross_up):
            return False
        # Structural uptrend: close must be above EMA(50)
        if close <= ema50_val:
            return False
        return True

    elif signal_type == SignalType.SHORT:
        if zscore <= zscore_threshold:
            return False
        stoch_overbought = k > overbought_level
        stoch_cross_down = k < d and prev_k >= prev_d
        if not (stoch_overbought or stoch_cross_down):
            return False
        if close >= ema50_val:
            return False
        return True

    return False


def check_ichi_adx_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Ichimoku + ADX combo entry conditions.

    LONG:  Tenkan crosses above Kijun (TK cross bullish)
           AND close > cloud_top (above the cloud)
           AND ADX > 25 (trending)
           AND ADX rising (adx > prev_adx).
    SHORT: Tenkan crosses below Kijun
           AND close < cloud_bottom
           AND ADX > 25
           AND ADX rising.

    Requires tenkan, kijun, cloud_top, cloud_bottom, adx columns.

    Args:
        row: Current closed candle row with Ichimoku + ADX columns.
        prev_row: Previous candle row (for TK crossover and ADX direction).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if all Ichimoku + ADX conditions are satisfied.
    """
    if not config.get("signals", {}).get("ichi_adx", {}).get("enabled", False):
        return False

    required = ("tenkan", "kijun", "cloud_top", "cloud_bottom", "adx", "atr")
    for col in required:
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False
    for col in ("tenkan", "kijun", "adx"):
        val = prev_row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if float(row["atr"]) < atr_min:
        return False

    adx_threshold = config.get("ichi_adx_threshold", 25.0)
    adx = float(row["adx"])
    prev_adx = float(prev_row["adx"])

    if adx <= adx_threshold:
        return False
    if adx <= prev_adx:
        return False  # ADX must be rising

    tenkan = float(row["tenkan"])
    kijun = float(row["kijun"])
    prev_tenkan = float(prev_row["tenkan"])
    prev_kijun = float(prev_row["kijun"])
    cloud_top = float(row["cloud_top"])
    cloud_bottom = float(row["cloud_bottom"])
    close = float(row["close"])

    if signal_type == SignalType.LONG:
        # Tenkan crosses above Kijun
        tk_cross_up = tenkan > kijun and prev_tenkan <= prev_kijun
        if not tk_cross_up:
            return False
        if close <= cloud_top:
            return False
        return True

    elif signal_type == SignalType.SHORT:
        # Tenkan crosses below Kijun
        tk_cross_down = tenkan < kijun and prev_tenkan >= prev_kijun
        if not tk_cross_down:
            return False
        if close >= cloud_bottom:
            return False
        return True

    return False


def check_ribbon_ao_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check EMA Ribbon + Awesome Oscillator combo entry conditions.

    LONG:  EMA ribbon fully aligned bullish (ema8>13>21>34>55>89)
           AND AO > 0 (Awesome Oscillator positive)
           AND close > EMA(50)
           AND edge detect: previous bar was NOT both conditions true.
    SHORT: EMA ribbon fully aligned bearish (ema8<13<21<34<55<89)
           AND AO < 0
           AND close < EMA(50)
           AND edge detect.

    Requires ema_ribbon_8/13/21/34/55/89, ao, ema50 columns.

    Args:
        row: Current closed candle row with ribbon + AO columns.
        prev_row: Previous candle row (for edge detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if all EMA Ribbon + AO conditions are satisfied.
    """
    if not config.get("signals", {}).get("ribbon_ao", {}).get("enabled", False):
        return False

    required_cols = (
        "ema_ribbon_8", "ema_ribbon_13", "ema_ribbon_21",
        "ema_ribbon_34", "ema_ribbon_55", "ema_ribbon_89",
        "ao", "atr",
    )
    for col in required_cols:
        val = row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False
    for col in ("ema_ribbon_8", "ema_ribbon_13", "ema_ribbon_21",
                "ema_ribbon_34", "ema_ribbon_55", "ema_ribbon_89", "ao"):
        val = prev_row.get(col)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if float(row["atr"]) < atr_min:
        return False

    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    r8 = float(row["ema_ribbon_8"])
    r13 = float(row["ema_ribbon_13"])
    r21 = float(row["ema_ribbon_21"])
    r34 = float(row["ema_ribbon_34"])
    r55 = float(row["ema_ribbon_55"])
    r89 = float(row["ema_ribbon_89"])

    pr8 = float(prev_row["ema_ribbon_8"])
    pr13 = float(prev_row["ema_ribbon_13"])
    pr21 = float(prev_row["ema_ribbon_21"])
    pr34 = float(prev_row["ema_ribbon_34"])
    pr55 = float(prev_row["ema_ribbon_55"])
    pr89 = float(prev_row["ema_ribbon_89"])

    ao = float(row["ao"])
    prev_ao = float(prev_row["ao"])
    close = float(row["close"])
    prev_close = float(prev_row.get("close", close))
    ema50_val = float(ema50)
    prev_ema50 = prev_row.get("ema_trend", prev_row.get("ema_trend_1h", prev_row.get("ema50", 0)))
    prev_ema50_val = float(prev_ema50) if prev_ema50 is not None and not pd.isna(prev_ema50) else 0.0

    if signal_type == SignalType.LONG:
        ribbon_aligned = r8 > r13 > r21 > r34 > r55 > r89
        if not ribbon_aligned:
            return False
        if ao <= 0.0:
            return False
        if close <= ema50_val:
            return False
        # Edge detect: previous bar was NOT both conditions true
        prev_ribbon_aligned = pr8 > pr13 > pr21 > pr34 > pr55 > pr89
        prev_both_true = prev_ribbon_aligned and prev_ao > 0.0 and prev_close > prev_ema50_val
        if prev_both_true:
            return False
        return True

    elif signal_type == SignalType.SHORT:
        ribbon_aligned = r8 < r13 < r21 < r34 < r55 < r89
        if not ribbon_aligned:
            return False
        if ao >= 0.0:
            return False
        if close >= ema50_val:
            return False
        # Edge detect
        prev_ribbon_aligned = pr8 < pr13 < pr21 < pr34 < pr55 < pr89
        prev_both_true = prev_ribbon_aligned and prev_ao < 0.0 and prev_close < prev_ema50_val
        if prev_both_true:
            return False
        return True

    return False


def compute_levels(
    entry_price: float, atr: float, signal_type: SignalType, config: dict
) -> tuple[float, float]:
    """Compute stop loss and take profit levels.

    Args:
        entry_price: Entry price.
        atr: Current ATR value.
        signal_type: LONG or SHORT.
        config: Bot configuration.

    Returns:
        Tuple of (stop_loss, take_profit).
    """
    sl_distance = atr * config["atr_sl_mult"]
    tp_distance = atr * config["atr_tp_mult"]

    if signal_type == SignalType.LONG:
        stop_loss = entry_price - sl_distance
        take_profit = entry_price + tp_distance
    else:
        stop_loss = entry_price + sl_distance
        take_profit = entry_price - tp_distance

    return stop_loss, take_profit


def compute_net_rr(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    config: dict,
) -> float:
    """Compute net risk-reward ratio after accounting for fees and slippage.

    Always computes R:R based on the full TP distance. Partial TP is an
    execution strategy, not a signal quality filter — decoupling it here
    prevents partial TP from artificially suppressing good signals.
    Round-trip costs reduce TP profits and increase SL losses.

    Args:
        entry_price: Entry price.
        stop_loss: Stop loss price.
        take_profit: Take profit price.
        config: Bot configuration with commission_rate and slippage_rate.

    Returns:
        Net R:R ratio after fees based on full TP distance.
    """
    commission = config.get("commission_rate", 0.0004)
    slippage = config.get("slippage_rate", 0.00015)
    round_trip_cost = (commission + slippage) * 2  # Entry + exit

    risk = abs(entry_price - stop_loss)
    fee_impact = entry_price * round_trip_cost
    net_risk = risk + fee_impact  # Fees make SL worse

    if net_risk <= 0:
        return 0.0

    # Always compute R:R based on full TP distance.
    # Partial TP is an execution strategy, not a signal quality filter.
    reward = abs(take_profit - entry_price)
    net_reward = reward - fee_impact
    if net_reward <= 0:
        return 0.0
    return net_reward / net_risk


def generate_signal(
    signal_df: pd.DataFrame,
    trend_df: Optional[pd.DataFrame],
    config: dict,
) -> tuple[Optional[TradeSignal], pd.DataFrame]:
    """Generate a trade signal from the latest candle data.

    Tries signal types in priority order and returns the first valid one.
    Priority: EMA crossover → fast EMA crossover → EMA pullback → BB breakout → RSI divergence.

    Uses the most recent closed candle (index -1 is current/forming,
    so we use index -2 for the last closed candle).

    Returns the enriched DataFrame (with indicators already computed) so
    callers can reuse it instead of re-running add_indicators().

    Args:
        signal_df: Signal timeframe OHLCV DataFrame (e.g., 15m).
        trend_df: Trend timeframe OHLCV DataFrame (e.g., 1h). Can be None.
        config: Bot configuration.

    Returns:
        Tuple of (TradeSignal or None, enriched DataFrame with indicators).
    """
    # Add indicators
    df = add_indicators(signal_df, config)

    # Add trend filter if trend data is available
    if trend_df is not None and not trend_df.empty:
        df = add_trend_filter(df, trend_df, config)

    if len(df) < 2:
        return None, df

    # Detect market regime
    regime = detect_regime(df, config.get("atr_period", 14))

    # Note: ranging and volatile regimes are allowed but flagged on the signal
    # so risk management can reduce position size accordingly

    # Use the last closed candle
    row = df.iloc[-2]
    entry_price = row["close"]
    min_rr = config.get("min_rr_ratio", 2.0)
    signals_config = config.get("signals", {})

    # Signal checkers that only need the current row (fast path)
    row_only_checks: list[tuple[str, object]] = []
    if signals_config.get("ema_crossover", {}).get("enabled", True):
        row_only_checks.append(("ema_crossover", check_entry_conditions))
    if signals_config.get("ema_fast_crossover", {}).get("enabled", True):
        row_only_checks.append(("ema_fast_crossover", check_fast_crossover_conditions))
    if signals_config.get("ema_pullback", {}).get("enabled", True):
        row_only_checks.append(("ema_pullback", check_pullback_conditions))
    if signals_config.get("pin_bar", {}).get("enabled", False):
        row_only_checks.append(("pin_bar", check_pin_bar_conditions))
    if signals_config.get("engulfing", {}).get("enabled", False):
        row_only_checks.append(("engulfing", check_engulfing_conditions))
    if signals_config.get("inside_bar_breakout", {}).get("enabled", False):
        row_only_checks.append(("inside_bar_breakout", check_inside_bar_breakout_conditions))
    if signals_config.get("bb_breakout", {}).get("enabled", True):
        row_only_checks.append(("bb_breakout", check_bb_breakout_conditions))
    if signals_config.get("mean_reversion", {}).get("enabled", False):
        row_only_checks.append(("mean_reversion", check_mean_reversion_conditions))
    if signals_config.get("body_dominance", {}).get("enabled", False):
        row_only_checks.append(("body_dominance", check_body_dominance_conditions))

    for source, check_fn in row_only_checks:
        for signal_type in (SignalType.LONG, SignalType.SHORT):
            if not check_fn(row, config, signal_type):
                continue

            atr = row["atr"]
            # Mean-reversion uses tighter SL/TP — targets BB mid, not a big trend move
            if source == "mean_reversion":
                sl_mult = config.get("mr_atr_sl_mult", 1.0)
                tp_mult = config.get("mr_atr_tp_mult", 1.5)
                sl, tp = compute_levels(
                    entry_price, atr, signal_type,
                    {"atr_sl_mult": sl_mult, "atr_tp_mult": tp_mult},
                )
            else:
                sl, tp = compute_levels(entry_price, atr, signal_type, config)
            net_rr = compute_net_rr(entry_price, sl, tp, config)
            if net_rr < min_rr:
                continue

            signal = TradeSignal(
                signal_type=signal_type,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                atr=atr,
                rsi=row["rsi"],
                risk_reward_ratio=net_rr,
                regime=regime,
                signal_source=source,
            )
            gross_rr = abs(tp - entry_price) / abs(entry_price - sl) if abs(entry_price - sl) > 0 else 0
            logger.info(
                "signal_generated",
                extra={
                    "type": signal_type.value,
                    "source": source,
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "gross_rr": round(gross_rr, 2),
                    "net_rr": round(net_rr, 2),
                    "rsi": round(row["rsi"], 2),
                },
            )
            return signal, df

    # Supertrend: needs current row AND previous closed candle for direction-change detection
    if signals_config.get("supertrend", {}).get("enabled", False) and len(df) >= 3:
        prev_row = df.iloc[-3]
        for signal_type in (SignalType.LONG, SignalType.SHORT):
            if not check_supertrend_conditions(row, prev_row, config, signal_type):
                continue

            atr = row["atr"]
            tp_mult = config.get("atr_tp_mult", 3.0)
            tp_mult_effective = 100.0 if tp_mult == 0 else tp_mult
            sl, tp = compute_levels(
                entry_price, atr, signal_type,
                {**config, "atr_tp_mult": tp_mult_effective},
            )
            min_rr_check = config.get("min_rr_ratio", 2.0)
            if min_rr_check > 0:
                net_rr = compute_net_rr(entry_price, sl, tp, config)
                if net_rr < min_rr_check:
                    continue
            else:
                net_rr = 0.0

            signal = TradeSignal(
                signal_type=signal_type,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                atr=atr,
                rsi=row.get("rsi", 50.0) if not pd.isna(row.get("rsi", float("nan"))) else 50.0,
                risk_reward_ratio=net_rr,
                regime=regime,
                signal_source="supertrend",
            )
            gross_rr = abs(tp - entry_price) / abs(entry_price - sl) if abs(entry_price - sl) > 0 else 0
            logger.info(
                "signal_generated",
                extra={
                    "type": signal_type.value,
                    "source": "supertrend",
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "gross_rr": round(gross_rr, 2),
                    "net_rr": round(net_rr, 2),
                    "rsi": round(signal.rsi, 2),
                },
            )
            return signal, df

    # Ichimoku Cloud: needs current row AND previous closed candle for crossover detection
    if signals_config.get("ichimoku_cloud", {}).get("enabled", False) and len(df) >= 3:
        prev_row = df.iloc[-3]
        for signal_type in (SignalType.LONG, SignalType.SHORT):
            if not check_ichimoku_conditions(row, prev_row, config, signal_type):
                continue

            atr = row["atr"]
            tp_mult = config.get("atr_tp_mult", 3.0)
            # When atr_tp_mult is 0, use a very far TP so trailing stop is the effective exit
            if tp_mult == 0:
                tp_mult_effective = 100.0
            else:
                tp_mult_effective = tp_mult
            sl, tp = compute_levels(
                entry_price, atr, signal_type,
                {**config, "atr_tp_mult": tp_mult_effective},
            )
            min_rr_check = config.get("min_rr_ratio", 2.0)
            if min_rr_check > 0:
                net_rr = compute_net_rr(entry_price, sl, tp, config)
                if net_rr < min_rr_check:
                    continue
            else:
                net_rr = 0.0

            signal = TradeSignal(
                signal_type=signal_type,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                atr=atr,
                rsi=row.get("rsi", 50.0) if not pd.isna(row.get("rsi", float("nan"))) else 50.0,
                risk_reward_ratio=net_rr,
                regime=regime,
                signal_source="ichimoku_cloud",
            )
            gross_rr = abs(tp - entry_price) / abs(entry_price - sl) if abs(entry_price - sl) > 0 else 0
            logger.info(
                "signal_generated",
                extra={
                    "type": signal_type.value,
                    "source": "ichimoku_cloud",
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "gross_rr": round(gross_rr, 2),
                    "net_rr": round(net_rr, 2),
                    "rsi": round(signal.rsi, 2),
                },
            )
            return signal, df

    # Dual Supertrend, Alligator, EMA+Ichimoku Hybrid, Ichi+Supertrend, VolExp+Supertrend:
    # all require current row AND previous closed candle for flip/crossover detection.
    if len(df) >= 3:
        prev_row = df.iloc[-3]
        new_signals: list[tuple[str, object]] = []
        if signals_config.get("dual_supertrend", {}).get("enabled", False):
            new_signals.append(("dual_supertrend", check_dual_supertrend_conditions))
        if signals_config.get("alligator", {}).get("enabled", False):
            new_signals.append(("alligator", check_alligator_conditions))
        if signals_config.get("ema_ichimoku_hybrid", {}).get("enabled", False):
            new_signals.append(("ema_ichimoku_hybrid", check_ema_ichimoku_hybrid_conditions))
        if signals_config.get("ichi_supertrend", {}).get("enabled", False):
            new_signals.append(("ichi_supertrend", check_ichi_supertrend_conditions))
        if signals_config.get("volexp_supertrend", {}).get("enabled", False):
            new_signals.append(("volexp_supertrend", check_volexp_supertrend_conditions))
        if signals_config.get("vol_expansion", {}).get("enabled", False):
            new_signals.append(("vol_expansion", check_vol_expansion_conditions))
        if signals_config.get("dual_thrust", {}).get("enabled", False):
            new_signals.append(("dual_thrust", check_dual_thrust_conditions))
        if signals_config.get("stoch_mtf", {}).get("enabled", False):
            new_signals.append(("stoch_mtf", check_stoch_mtf_conditions))
        if signals_config.get("zscore_meanrev", {}).get("enabled", False):
            new_signals.append(("zscore_meanrev", check_zscore_meanrev_conditions))
        if signals_config.get("awesome_oscillator", {}).get("enabled", False):
            new_signals.append(("awesome_oscillator", check_awesome_oscillator_conditions))
        if signals_config.get("range_bounce", {}).get("enabled", False):
            new_signals.append(("range_bounce", check_range_bounce_conditions))
        if signals_config.get("ema_ribbon", {}).get("enabled", False):
            new_signals.append(("ema_ribbon", check_ema_ribbon_conditions))
        if signals_config.get("ichi_adx", {}).get("enabled", False):
            new_signals.append(("ichi_adx", check_ichi_adx_conditions))
        if signals_config.get("ribbon_ao", {}).get("enabled", False):
            new_signals.append(("ribbon_ao", check_ribbon_ao_conditions))
        if signals_config.get("zscore_stoch", {}).get("enabled", False):
            new_signals.append(("zscore_stoch", check_zscore_stoch_conditions))
        if signals_config.get("stoch_supertrend", {}).get("enabled", False):
            new_signals.append(("stoch_supertrend", check_stoch_supertrend_conditions))
        if signals_config.get("supertrend_volume", {}).get("enabled", False):
            new_signals.append(("supertrend_volume", check_supertrend_volume_conditions))
        if signals_config.get("dualthrust_adx", {}).get("enabled", False):
            new_signals.append(("dualthrust_adx", check_dualthrust_adx_conditions))

        # Functions that take (row, config, signal_type) — no prev_row
        _no_prev_row = {"zscore_meanrev"}

        for source, check_fn in new_signals:
            for signal_type in (SignalType.LONG, SignalType.SHORT):
                if source in _no_prev_row:
                    if not check_fn(row, config, signal_type):
                        continue
                else:
                    if not check_fn(row, prev_row, config, signal_type):
                        continue

                atr = row["atr"]
                tp_mult = config.get("atr_tp_mult", 3.0)
                tp_mult_effective = 100.0 if tp_mult == 0 else tp_mult
                sl, tp = compute_levels(
                    entry_price, atr, signal_type,
                    {**config, "atr_tp_mult": tp_mult_effective},
                )
                min_rr_check = config.get("min_rr_ratio", 2.0)
                if min_rr_check > 0:
                    net_rr = compute_net_rr(entry_price, sl, tp, config)
                    if net_rr < min_rr_check:
                        continue
                else:
                    net_rr = 0.0

                signal = TradeSignal(
                    signal_type=signal_type,
                    entry_price=entry_price,
                    stop_loss=sl,
                    take_profit=tp,
                    atr=atr,
                    rsi=row.get("rsi", 50.0) if not pd.isna(row.get("rsi", float("nan"))) else 50.0,
                    risk_reward_ratio=net_rr,
                    regime=regime,
                    signal_source=source,
                )
                gross_rr = abs(tp - entry_price) / abs(entry_price - sl) if abs(entry_price - sl) > 0 else 0
                logger.info(
                    "signal_generated",
                    extra={
                        "type": signal_type.value,
                        "source": source,
                        "entry": entry_price,
                        "sl": sl,
                        "tp": tp,
                        "gross_rr": round(gross_rr, 2),
                        "net_rr": round(net_rr, 2),
                        "rsi": round(signal.rsi, 2),
                    },
                )
                return signal, df

    # RSI divergence needs DataFrame access — check separately
    if signals_config.get("rsi_divergence", {}).get("enabled", True):
        for signal_type in (SignalType.LONG, SignalType.SHORT):
            if not check_rsi_divergence_conditions(
                row, df.iloc[:-1], config, signal_type
            ):
                continue

            atr = row["atr"]
            sl, tp = compute_levels(entry_price, atr, signal_type, config)
            net_rr = compute_net_rr(entry_price, sl, tp, config)
            if net_rr < min_rr:
                continue

            signal = TradeSignal(
                signal_type=signal_type,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                atr=atr,
                rsi=row["rsi"],
                risk_reward_ratio=net_rr,
                regime=regime,
                signal_source="rsi_divergence",
            )
            gross_rr = abs(tp - entry_price) / abs(entry_price - sl) if abs(entry_price - sl) > 0 else 0
            logger.info(
                "signal_generated",
                extra={
                    "type": signal_type.value,
                    "source": "rsi_divergence",
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "gross_rr": round(gross_rr, 2),
                    "net_rr": round(net_rr, 2),
                    "rsi": round(row["rsi"], 2),
                },
            )
            return signal, df

    # Squeeze release needs the previous closed candle (df.iloc[-3]) for squeeze_prev
    if signals_config.get("squeeze_release", {}).get("enabled", False) and len(df) >= 3:
        prev_row = df.iloc[-3]
        for signal_type in (SignalType.LONG, SignalType.SHORT):
            if not check_squeeze_release_conditions(row, prev_row, config, signal_type):
                continue

            atr = row["atr"]
            sl, tp = compute_levels(entry_price, atr, signal_type, config)
            net_rr = compute_net_rr(entry_price, sl, tp, config)
            if net_rr < min_rr:
                continue

            signal = TradeSignal(
                signal_type=signal_type,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                atr=atr,
                rsi=row["rsi"],
                risk_reward_ratio=net_rr,
                regime=regime,
                signal_source="squeeze_release",
            )
            gross_rr = abs(tp - entry_price) / abs(entry_price - sl) if abs(entry_price - sl) > 0 else 0
            logger.info(
                "signal_generated",
                extra={
                    "type": signal_type.value,
                    "source": "squeeze_release",
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "gross_rr": round(gross_rr, 2),
                    "net_rr": round(net_rr, 2),
                    "rsi": round(row["rsi"], 2),
                },
            )
            return signal, df

    return None, df


def compute_signal_quality_score(
    net_rr: float,
    rsi: float,
    volume_ratio: float,
    regime: str,
    signal_type: SignalType,
    config: dict,
) -> float:
    """Compute a quality score (0.0-1.0) for a trade signal.

    Used by flexible cooldown to decide whether a high-quality signal
    can override the normal cooldown period.

    Args:
        net_rr: Net risk-reward ratio after fees.
        rsi: RSI value of the signal candle.
        volume_ratio: volume / volume_ma (1.0 = average).
        regime: Market regime ('trending', 'volatile', 'ranging').
        signal_type: LONG or SHORT.
        config: Bot configuration.

    Returns:
        Quality score between 0.0 and 1.0.
    """
    min_rr = config.get("min_rr_ratio", 1.5)

    # R:R score: linear from min_rr (0) to 3.0 (1.0)
    rr_score = max(0.0, min((net_rr - min_rr) / (3.0 - min_rr), 1.0)) if 3.0 > min_rr else 0.0

    # RSI score: distance from optimal midpoint
    if signal_type == SignalType.LONG:
        optimal = 58.0
        half_range = 10.0  # 48-68 range
    else:
        optimal = 40.0
        half_range = 10.0  # 30-50 range
    rsi_score = max(0.0, 1.0 - abs(rsi - optimal) / half_range)

    # Volume score: 1x MA = 0, 2x+ MA = 1
    volume_score = max(0.0, min((volume_ratio - 1.0) / 1.0, 1.0))

    # Regime score
    regime_scores = {"trending": 1.0, "volatile": 0.5, "ranging": 0.3}
    regime_score = regime_scores.get(regime, 0.3)

    return (rr_score + rsi_score + volume_score + regime_score) / 4.0


def compute_trailing_stop(
    current_price: float,
    current_sl: float,
    atr: float,
    signal_type: SignalType,
    config: dict,
    post_tp1: bool = False,
) -> float:
    """Compute updated trailing stop loss.

    Moves the stop loss in the direction of profit using ATR.

    Args:
        current_price: Current market price.
        current_sl: Current stop loss level.
        atr: Current ATR value.
        signal_type: LONG or SHORT.
        config: Bot configuration.
        post_tp1: If True, use wider post-TP1 trail multiplier.

    Returns:
        Updated stop loss price (only moves in favorable direction).
    """
    # Use wider trail after TP1 if configured
    if post_tp1 and "atr_trail_mult_post_tp1" in config:
        trail_mult = config["atr_trail_mult_post_tp1"]
    else:
        trail_mult = config.get("atr_trail_mult", config["atr_sl_mult"])
    trail_distance = atr * trail_mult

    if signal_type == SignalType.LONG:
        new_sl = current_price - trail_distance
        return max(new_sl, current_sl)  # Only move up
    else:
        new_sl = current_price + trail_distance
        return min(new_sl, current_sl)  # Only move down


def check_dual_thrust_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Dual Thrust Breakout entry conditions.

    LONG:  close > dt_upper (prev close <= prev dt_upper) AND close > ema50 trend filter.
    SHORT: close < dt_lower (prev close >= prev dt_lower) AND close < ema50 trend filter.

    Dual Thrust is a breakout strategy: price breaks through a band built from the
    prior close ± k * N-bar rolling range (high-low).  The EMA(50) trend filter
    prevents counter-trend breakouts.

    Requires dt_upper, dt_lower, ema50 (or ema_trend) columns added by add_indicators().

    Args:
        row: Current candle row with dt_upper, dt_lower columns.
        prev_row: Previous candle row (for fresh breakout detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Dual Thrust breakout conditions are satisfied.
    """
    if not config.get("signals", {}).get("dual_thrust", {}).get("enabled", False):
        return False

    dt_upper = row.get("dt_upper")
    dt_lower = row.get("dt_lower")
    prev_dt_upper = prev_row.get("dt_upper")
    prev_dt_lower = prev_row.get("dt_lower")
    close = row.get("close")
    prev_close = prev_row.get("close")

    for val in (dt_upper, dt_lower, prev_dt_upper, prev_dt_lower, close, prev_close):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    # EMA(50) trend filter
    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    if signal_type == SignalType.LONG:
        # Fresh breakout above upper band
        if not (float(close) > float(dt_upper) and float(prev_close) <= float(prev_dt_upper)):
            return False
        # Trend filter: price above EMA(50)
        if float(close) <= float(ema50):
            return False
        return True

    elif signal_type == SignalType.SHORT:
        # Fresh breakout below lower band
        if not (float(close) < float(dt_lower) and float(prev_close) >= float(prev_dt_lower)):
            return False
        # Trend filter: price below EMA(50)
        if float(close) >= float(ema50):
            return False
        return True

    return False


def check_awesome_oscillator_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Awesome Oscillator zero-cross entry conditions.

    LONG:  AO crosses above 0 (prev_ao <= 0, current ao > 0) AND close > EMA(50).
    SHORT: AO crosses below 0 (prev_ao >= 0, current ao < 0) AND close < EMA(50).

    The Awesome Oscillator (AO = SMA5_midpoint - SMA34_midpoint) zero-cross
    signals a momentum shift.  The EMA(50) filter confirms the macro trend.

    Requires ao, ema50 (or ema_trend) columns added by add_indicators().

    Args:
        row: Current candle row with ao, ema50 columns.
        prev_row: Previous candle row (for zero-cross detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Awesome Oscillator conditions are satisfied.
    """
    if not config.get("signals", {}).get("awesome_oscillator", {}).get("enabled", False):
        return False

    ao = row.get("ao")
    prev_ao = prev_row.get("ao")
    close = row.get("close")

    for val in (ao, prev_ao, close):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    # EMA(50) trend filter
    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    if signal_type == SignalType.LONG:
        ao_cross_up = float(ao) > 0.0 and float(prev_ao) <= 0.0
        if not ao_cross_up:
            return False
        if float(close) <= float(ema50):
            return False
        return True

    elif signal_type == SignalType.SHORT:
        ao_cross_down = float(ao) < 0.0 and float(prev_ao) >= 0.0
        if not ao_cross_down:
            return False
        if float(close) >= float(ema50):
            return False
        return True

    return False


def check_range_bounce_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Range Bounce mean-reversion entry conditions.

    LONG:  rb_is_ranging AND close > rb_support (inside range)
           AND close < rb_support * (1 + proximity_pct) (near support)
           AND RSI < rsi_lo.
    SHORT: rb_is_ranging AND close < rb_resistance (inside range)
           AND close > rb_resistance * (1 - proximity_pct) (near resistance)
           AND RSI > rsi_hi.

    Range Bounce is a MEAN-REVERSION strategy — it intentionally fires in
    ranging markets and should NOT be gated by trend_signals_gated.  The
    rb_is_ranging flag provides its own ranging market filter.

    Requires rb_support, rb_resistance, rb_is_ranging columns added by add_indicators().

    Args:
        row: Current candle row with range bounce indicator columns.
        prev_row: Previous candle row (unused, kept for consistent interface).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Range Bounce conditions are satisfied.
    """
    if not config.get("signals", {}).get("range_bounce", {}).get("enabled", False):
        return False

    rb_support = row.get("rb_support")
    rb_resistance = row.get("rb_resistance")
    rb_is_ranging = row.get("rb_is_ranging")
    close = row.get("close")
    rsi = row.get("rsi")

    for val in (rb_support, rb_resistance, close, rsi):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    # Must be in a ranging market (narrow range relative to recent history)
    if not rb_is_ranging:
        return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    proximity_pct = config.get("range_bounce_proximity_pct", 0.01)
    rsi_lo = config.get("range_bounce_rsi_lo", 35)
    rsi_hi = config.get("range_bounce_rsi_hi", 65)

    if signal_type == SignalType.LONG:
        # Price near support (within proximity_pct above support) and RSI oversold
        near_support = float(close) < float(rb_support) * (1.0 + proximity_pct)
        above_support = float(close) > float(rb_support)
        rsi_oversold = float(rsi) < rsi_lo
        return near_support and above_support and rsi_oversold

    elif signal_type == SignalType.SHORT:
        # Price near resistance (within proximity_pct below resistance) and RSI overbought
        near_resistance = float(close) > float(rb_resistance) * (1.0 - proximity_pct)
        below_resistance = float(close) < float(rb_resistance)
        rsi_overbought = float(rsi) > rsi_hi
        return near_resistance and below_resistance and rsi_overbought


def check_stoch_mtf_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Stochastic Multi-Timeframe entry conditions.

    Uses stochastic oscillator for entry timing with EMA(50) as higher-timeframe
    direction filter (proxy for multi-timeframe bias when only one TF is available).

    LONG:  Stochastic %K crosses above %D in oversold zone (K < oversold threshold)
           AND EMA(50) direction is bullish (close > EMA50).
    SHORT: Stochastic %K crosses below %D in overbought zone (K > overbought threshold)
           AND EMA(50) direction is bearish (close < EMA50).

    Requires stoch_k, stoch_d columns and ema50 / ema_trend direction filter.

    Args:
        row: Current candle row with stochastic and ema50 columns.
        prev_row: Previous candle row (for %K/%D crossover detection).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Stochastic MTF conditions are satisfied.
    """
    if not config.get("signals", {}).get("stoch_mtf", {}).get("enabled", False):
        return False

    k = row.get("stoch_k")
    d = row.get("stoch_d")
    prev_k = prev_row.get("stoch_k")
    prev_d = prev_row.get("stoch_d")

    for val in (k, d, prev_k, prev_d):
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    oversold = config.get("stoch_oversold", 20.0)
    overbought = config.get("stoch_overbought", 80.0)

    # EMA(50) direction filter — higher-timeframe bias
    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    close = row.get("close", 0)

    if signal_type == SignalType.LONG:
        k_cross_up = float(k) > float(d) and float(prev_k) <= float(prev_d)
        in_oversold = float(k) < oversold
        htf_bullish = float(close) > float(ema50)
        return k_cross_up and in_oversold and htf_bullish

    elif signal_type == SignalType.SHORT:
        k_cross_down = float(k) < float(d) and float(prev_k) >= float(prev_d)
        in_overbought = float(k) > overbought
        htf_bearish = float(close) < float(ema50)
        return k_cross_down and in_overbought and htf_bearish

    return False


def check_zscore_meanrev_conditions(
    row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check Z-Score Mean Reversion entry conditions.

    Enters mean-reversion trades when price is statistically extreme relative
    to its recent mean, filtered by EMA(50) to ensure structural alignment.

    LONG:  Z-Score < -zscore_threshold (price significantly below mean)
           AND close > EMA(50) (price is in a structural uptrend — oversold dip).
    SHORT: Z-Score > +zscore_threshold (price significantly above mean)
           AND close < EMA(50) (price is in a structural downtrend — overbought rally).

    This is mean-reversion logic and must be placed OUTSIDE trend_signals_gated
    checks in the engine (like range_bounce and mean_reversion).

    Requires zscore column and ema50 / ema_trend direction filter.

    Args:
        row: Current candle row with zscore and ema50 columns.
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if Z-Score mean-reversion conditions are satisfied.
    """
    if not config.get("signals", {}).get("zscore_meanrev", {}).get("enabled", False):
        return False

    zscore = row.get("zscore")
    if zscore is None or (isinstance(zscore, float) and pd.isna(zscore)):
        return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    threshold = config.get("zscore_threshold", 2.0)

    # EMA(50) structural trend filter
    ema50 = row.get("ema_trend", row.get("ema_trend_1h", row.get("ema50")))
    if ema50 is None or (isinstance(ema50, float) and pd.isna(ema50)):
        return False

    close = row.get("close", 0)

    if signal_type == SignalType.LONG:
        # Oversold in an uptrend — expect mean reversion upward
        return float(zscore) < -threshold and float(close) > float(ema50)

    elif signal_type == SignalType.SHORT:
        # Overbought in a downtrend — expect mean reversion downward
        return float(zscore) > threshold and float(close) < float(ema50)

    return False


def check_ema_ribbon_conditions(
    row: pd.Series,
    prev_row: pd.Series,
    config: dict,
    signal_type: SignalType,
) -> bool:
    """Check EMA Ribbon full alignment entry conditions.

    Six EMAs (8, 13, 21, 34, 55, 89) must be fully aligned (trend-following).
    Signal fires on the FIRST candle where full alignment is achieved (edge detect).

    LONG:  ema8 > ema13 > ema21 > ema34 > ema55 > ema89
           AND on the previous candle full alignment was NOT present.
    SHORT: ema8 < ema13 < ema21 < ema34 < ema55 < ema89
           AND on the previous candle full alignment was NOT present.

    Requires ema_ribbon_8/13/21/34/55/89 columns (added by add_indicators
    when ema_ribbon signal is enabled).

    Args:
        row: Current candle row with EMA ribbon columns.
        prev_row: Previous candle row (for edge-detection — first aligned bar).
        config: Bot configuration.
        signal_type: LONG or SHORT.

    Returns:
        True if EMA ribbon fully aligns on this candle for the first time.
    """
    if not config.get("signals", {}).get("ema_ribbon", {}).get("enabled", False):
        return False

    periods = (8, 13, 21, 34, 55, 89)
    cols = [f"ema_ribbon_{p}" for p in periods]

    # All columns must be present and non-NaN in both rows
    for col in cols:
        v = row.get(col)
        pv = prev_row.get(col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return False
        if pv is None or (isinstance(pv, float) and pd.isna(pv)):
            return False

    atr_min = config.get("atr_min", 0.0)
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    emas = [float(row[c]) for c in cols]
    prev_emas = [float(prev_row[c]) for c in cols]

    if signal_type == SignalType.LONG:
        # Current: fully bullish aligned
        aligned_now = all(emas[i] > emas[i + 1] for i in range(len(emas) - 1))
        if not aligned_now:
            return False
        # Previous: NOT fully aligned (edge detect — first bar of alignment)
        prev_aligned = all(prev_emas[i] > prev_emas[i + 1] for i in range(len(prev_emas) - 1))
        return not prev_aligned

    elif signal_type == SignalType.SHORT:
        # Current: fully bearish aligned
        aligned_now = all(emas[i] < emas[i + 1] for i in range(len(emas) - 1))
        if not aligned_now:
            return False
        # Previous: NOT fully aligned (edge detect)
        prev_aligned = all(prev_emas[i] < prev_emas[i + 1] for i in range(len(prev_emas) - 1))
        return not prev_aligned

    return False

    return False
