"""Load historical OHLCV data from Bybit for backtesting."""

import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


def download_ohlcv(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    months: int = 12,
    end_date: Optional[datetime] = None,
) -> pd.DataFrame:
    """Download historical OHLCV data from Bybit.

    Args:
        symbol: Trading pair symbol.
        timeframe: Candle timeframe.
        months: Number of months of history to download.
        end_date: End date. Defaults to now.

    Returns:
        DataFrame with OHLCV data indexed by timestamp.
    """
    exchange = ccxt.bybit({"options": {"defaultType": "swap"}})
    exchange.load_markets()

    if end_date is None:
        end_date = datetime.utcnow()

    start_date = end_date - timedelta(days=months * 30)
    since = int(start_date.timestamp() * 1000)

    all_candles = []
    timeframe_ms = _timeframe_to_ms(timeframe)
    limit = 200  # Bybit max per request

    logger.info(
        "download_started",
        extra={
            "symbol": symbol,
            "timeframe": timeframe,
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
    )

    while since < int(end_date.timestamp() * 1000):
        try:
            candles = exchange.fetch_ohlcv(
                symbol, timeframe, since=since, limit=limit
            )
            if not candles:
                break

            all_candles.extend(candles)
            since = candles[-1][0] + timeframe_ms

            # Rate limiting
            time.sleep(0.15)

        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
            logger.warning("download_retry", extra={"error": str(e)})
            time.sleep(2)
            continue

    df = pd.DataFrame(
        all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp")
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    logger.info("download_complete", extra={"rows": len(df)})
    return df


def save_ohlcv(df: pd.DataFrame, path: str) -> None:
    """Save OHLCV data to CSV.

    Args:
        df: OHLCV DataFrame.
        path: Output file path.
    """
    df.to_csv(path)
    logger.info("data_saved", extra={"path": path, "rows": len(df)})


def load_ohlcv(path: str) -> pd.DataFrame:
    """Load OHLCV data from CSV.

    Args:
        path: Input file path.

    Returns:
        OHLCV DataFrame.
    """
    df = pd.read_csv(path, index_col="timestamp", parse_dates=True)
    logger.info("data_loaded", extra={"path": path, "rows": len(df)})
    return df


def _timeframe_to_ms(timeframe: str) -> int:
    """Convert timeframe string to milliseconds.

    Args:
        timeframe: Timeframe string (e.g., '1m', '15m', '1h', '1d').

    Returns:
        Duration in milliseconds.
    """
    multipliers = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    unit = timeframe[-1]
    value = int(timeframe[:-1])
    return value * multipliers[unit]
