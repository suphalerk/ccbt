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

    # RSI filter — directional for trend-following
    if pd.isna(row.get("rsi")):
        return False

    if signal_type == SignalType.LONG:
        # Longs: RSI 48-75 — confirmed uptrend momentum, not overbought
        if not (rsi_min + 3 <= row["rsi"] <= rsi_max + 10):
            return False
    elif signal_type == SignalType.SHORT:
        # Shorts: RSI 25-52 — confirmed downtrend weakness
        if not (rsi_min - 20 <= row["rsi"] <= rsi_max - 13):
            return False
    else:
        if not (rsi_min <= row["rsi"] <= rsi_max):
            return False

    # ATR minimum volatility
    if pd.isna(row.get("atr")) or row["atr"] < atr_min:
        return False

    # Volume filter
    if pd.notna(row.get("volume_ma")) and row.get("volume_ma", 0) > 0:
        if row["volume"] < row["volume_ma"] * volume_mult:
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

    Round-trip costs reduce TP profits and increase SL losses.

    Args:
        entry_price: Entry price.
        stop_loss: Stop loss price.
        take_profit: Take profit price.
        config: Bot configuration with commission_rate and slippage_rate.

    Returns:
        Net R:R ratio after fees.
    """
    commission = config.get("commission_rate", 0.00055)
    slippage = config.get("slippage_rate", 0.0005)
    round_trip_cost = (commission + slippage) * 2  # Entry + exit

    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    fee_impact = entry_price * round_trip_cost

    net_risk = risk + fee_impact  # Fees make SL worse
    net_reward = reward - fee_impact  # Fees reduce TP profit

    if net_risk <= 0 or net_reward <= 0:
        return 0.0
    return net_reward / net_risk


def generate_signal(
    signal_df: pd.DataFrame,
    trend_df: Optional[pd.DataFrame],
    config: dict,
) -> Optional[TradeSignal]:
    """Generate a trade signal from the latest candle data.

    Uses the most recent closed candle (index -1 is current/forming,
    so we use index -2 for the last closed candle).

    Args:
        signal_df: Signal timeframe OHLCV DataFrame (e.g., 15m).
        trend_df: Trend timeframe OHLCV DataFrame (e.g., 1h). Can be None.
        config: Bot configuration.

    Returns:
        TradeSignal if conditions are met, None otherwise.
    """
    # Add indicators
    df = add_indicators(signal_df, config)

    # Add trend filter if trend data is available
    if trend_df is not None and not trend_df.empty:
        df = add_trend_filter(df, trend_df, config)

    if len(df) < 2:
        return None

    # Detect market regime
    regime = detect_regime(df, config.get("atr_period", 14))

    # Note: ranging and volatile regimes are allowed but flagged on the signal
    # so risk management can reduce position size accordingly

    # Use the last closed candle
    row = df.iloc[-2]
    entry_price = row["close"]

    # Check long conditions
    if check_entry_conditions(row, config, SignalType.LONG):
        sl, tp = compute_levels(entry_price, row["atr"], SignalType.LONG, config)
        gross_rr = abs(tp - entry_price) / abs(entry_price - sl) if abs(entry_price - sl) > 0 else 0
        net_rr = compute_net_rr(entry_price, sl, tp, config)
        if net_rr >= config.get("min_rr_ratio", 2.0):
            signal = TradeSignal(
                signal_type=SignalType.LONG,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                atr=row["atr"],
                rsi=row["rsi"],
                risk_reward_ratio=net_rr,
                regime=regime,
            )
            logger.info(
                "signal_generated",
                extra={
                    "type": "long",
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "gross_rr": round(gross_rr, 2),
                    "net_rr": round(net_rr, 2),
                    "rsi": round(row["rsi"], 2),
                },
            )
            return signal

    # Check short conditions
    if check_entry_conditions(row, config, SignalType.SHORT):
        sl, tp = compute_levels(entry_price, row["atr"], SignalType.SHORT, config)
        gross_rr = abs(entry_price - tp) / abs(sl - entry_price) if abs(sl - entry_price) > 0 else 0
        net_rr = compute_net_rr(entry_price, sl, tp, config)
        if net_rr >= config.get("min_rr_ratio", 2.0):
            signal = TradeSignal(
                signal_type=SignalType.SHORT,
                entry_price=entry_price,
                stop_loss=sl,
                take_profit=tp,
                atr=row["atr"],
                rsi=row["rsi"],
                risk_reward_ratio=net_rr,
                regime=regime,
            )
            logger.info(
                "signal_generated",
                extra={
                    "type": "short",
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "gross_rr": round(gross_rr, 2),
                    "net_rr": round(net_rr, 2),
                    "rsi": round(row["rsi"], 2),
                },
            )
            return signal

    return None


def compute_trailing_stop(
    current_price: float,
    current_sl: float,
    atr: float,
    signal_type: SignalType,
    config: dict,
) -> float:
    """Compute updated trailing stop loss.

    Moves the stop loss in the direction of profit using ATR.

    Args:
        current_price: Current market price.
        current_sl: Current stop loss level.
        atr: Current ATR value.
        signal_type: LONG or SHORT.
        config: Bot configuration.

    Returns:
        Updated stop loss price (only moves in favorable direction).
    """
    # Use separate trail multiplier if available (tighter than initial SL)
    trail_mult = config.get("atr_trail_mult", config["atr_sl_mult"])
    trail_distance = atr * trail_mult

    if signal_type == SignalType.LONG:
        new_sl = current_price - trail_distance
        return max(new_sl, current_sl)  # Only move up
    else:
        new_sl = current_price + trail_distance
        return min(new_sl, current_sl)  # Only move down
