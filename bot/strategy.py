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
    if signals_config.get("bb_breakout", {}).get("enabled", True):
        row_only_checks.append(("bb_breakout", check_bb_breakout_conditions))
    if signals_config.get("mean_reversion", {}).get("enabled", False):
        row_only_checks.append(("mean_reversion", check_mean_reversion_conditions))

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
