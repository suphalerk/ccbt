"""Shared ccxt exchange pool for multi-bot single-process mode.

When running many bots in one process (main_multi.py), all bots that trade
on the same exchange + testnet flag share ONE underlying ccxt exchange object.
This avoids calling load_markets() 137 times (~40-80 MB and several seconds
each) and instead calls it once.

The pool is process-global and thread-safe for reads once populated.
Write access (initial creation) is protected by a threading.Lock.

Usage::

    from bot.shared_exchange_pool import get_shared_exchange

    # First call builds the ccxt instance; subsequent calls return the same one.
    ccxt_exchange = get_shared_exchange("binance", use_testnet=True)
    # Pass it to BybitClient via the shared_exchange parameter.
"""

import logging
import math
import os
import threading
import time
from typing import Callable, Optional, Tuple

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)

# Pool key: (exchange_name: str, use_testnet: bool)
_pool: dict[tuple[str, bool], object] = {}
_lock = threading.Lock()

# Binance futures testnet base URL (duplicated here to avoid circular import)
_BINANCE_TESTNET_BASE = "https://testnet.binancefuture.com"


def get_shared_exchange(exchange_name: str, use_testnet: bool) -> object:
    """Return a shared ccxt exchange instance, creating it on first call.

    The exchange has load_markets() already called.  All BybitClient
    instances that share this object will skip their own load_markets()
    call, cutting per-bot startup cost from ~60 MB to ~5 MB.

    Args:
        exchange_name: "binance" or "bybit".
        use_testnet: Whether to point at testnet endpoints.

    Returns:
        A fully initialised ccxt exchange instance with markets loaded.
    """
    key = (exchange_name.lower(), use_testnet)

    # Fast path: already built
    if key in _pool:
        return _pool[key]

    with _lock:
        # Re-check under lock to avoid double-creation
        if key in _pool:
            return _pool[key]

        # Use separate API keys for testnet vs mainnet
        if use_testnet:
            api_key = os.getenv("API_KEY", "")
            api_secret = os.getenv("API_SECRET", "")
        else:
            api_key = os.getenv("MAINNET_API_KEY", "")
            api_secret = os.getenv("MAINNET_SECRET_KEY", "")

        exchange_params = {
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        }

        name = exchange_name.lower()
        if name == "binance":
            exchange_params["options"] = {"defaultType": "future"}
            exchange = ccxt.binance(exchange_params)
            if use_testnet:
                base = _BINANCE_TESTNET_BASE
                exchange.urls["api"]["fapiPublic"] = f"{base}/fapi/v1"
                exchange.urls["api"]["fapiPublicV2"] = f"{base}/fapi/v2"
                exchange.urls["api"]["fapiPublicV3"] = f"{base}/fapi/v3"
                exchange.urls["api"]["fapiPrivate"] = f"{base}/fapi/v1"
                exchange.urls["api"]["fapiPrivateV2"] = f"{base}/fapi/v2"
                exchange.urls["api"]["fapiPrivateV3"] = f"{base}/fapi/v3"
                exchange.has["fetchCurrencies"] = False
                exchange.has["fetchMarginMarkets"] = False
                exchange.urls["api"]["sapi"] = f"{base}/sapi/v1"
                exchange.urls["api"]["sapiV2"] = f"{base}/sapi/v2"
                exchange.urls["api"]["sapiV3"] = f"{base}/sapi/v3"
                exchange.urls["api"]["sapiV4"] = f"{base}/sapi/v4"
        else:
            exchange_params["options"] = {"defaultType": "swap"}
            exchange = ccxt.bybit(exchange_params)
            if use_testnet:
                exchange.set_sandbox_mode(True)

        # Route all bots' exchange traffic through a SOCKS proxy when configured
        # (fixed egress IP for the Binance API whitelist on mainnet). One env var
        # covers every bot sharing this pooled exchange instance.
        proxy = os.getenv("CCBT_SOCKS_PROXY", "").strip()
        if proxy:
            exchange.socksProxy = proxy
            logger.info("shared_exchange_pool_proxy", extra={"socks_proxy": proxy})

        logger.info(
            "shared_exchange_pool_loading_markets",
            extra={"exchange": name, "testnet": use_testnet},
        )
        exchange.load_markets()
        logger.info(
            "shared_exchange_pool_ready",
            extra={
                "exchange": name,
                "testnet": use_testnet,
                "market_count": len(exchange.markets),
            },
        )

        _pool[key] = exchange
        return exchange


def clear_pool() -> None:
    """Remove all cached exchange instances.  Useful in tests."""
    with _lock:
        _pool.clear()


# ---------------------------------------------------------------------------
# SharedMarketData — T1: balance + OHLCV cache
# ---------------------------------------------------------------------------

# Timeframe string → duration in seconds
_TF_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "8h": 28_800,
    "12h": 43_200,
    "1d": 86_400,
}

_BALANCE_TTL_S: float = 30.0


def _floor_to_tf(now_utc_s: float, tf: str) -> int:
    """Return the open-time (seconds, UTC) of the currently-forming candle."""
    period = _TF_SECONDS[tf]
    return int(math.floor(now_utc_s / period) * period)


class SharedMarketData:
    """Thread-safe cache for balance and OHLCV data shared across all bots.

    Design rules (from plan v3):
    - Lock is NON-REENTRANT (threading.Lock).
    - Lock scope is TINY: acquire → read/decide-miss → RELEASE; the blocking
      ccxt fetch runs UNLOCKED; re-acquire → write-through → release.
    - Never hold the lock across a fetch call.
    - Never nest SharedMarketData method calls.

    OHLCV freshness is wall-clock driven:
        A cache entry is FRESH iff:
            served_limit >= requested_limit
            AND floor(now_utc, tf) == stored_last_closed_open_time

        stored_last_closed_open_time is the open-time of the LAST CLOSED candle
        (index[-2] of the fetched DataFrame), NOT the forming candle (index[-1]).

    served_limit is monotonic: a smaller-limit fetch never shrinks a larger
    cached frame; larger-limit requests do re-fetch.

    The returned DataFrame has the forming candle (index[-1] of the raw fetch)
    stripped so that callers always get only closed candles.

    Args:
        exchange: An object that exposes:
            - fetch_balance() → dict with free["USDT"] float
            - fetch_ohlcv(symbol, timeframe, limit) → pd.DataFrame (indexed by UTC Timestamps)
        clock: Callable returning current POSIX time as a float (seconds).
               Defaults to time.time.  Injectable for tests.
    """

    def __init__(
        self,
        exchange: object,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._exchange = exchange
        self._clock: Callable[[], float] = clock if clock is not None else time.time
        self._lock = threading.Lock()

        # Balance cache
        self._balance_value: Optional[float] = None
        self._balance_ts: float = -_BALANCE_TTL_S  # forces miss on first call

        # OHLCV cache: key → (df, last_closed_open_time_s, served_limit)
        self._ohlcv_cache: dict[Tuple[str, str], Tuple[pd.DataFrame, int, int]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_balance(self, fresh: bool = False) -> float:
        """Return the cached USDT free balance.

        Fetches from the exchange on the first call, after TTL expiry, or when
        fresh=True.  The exchange is NOT called while holding the lock.

        Args:
            fresh: If True, bypass the cache and write-through the result.

        Returns:
            USDT free balance as a float.
        """
        now = self._clock()

        # --- fast path: check under lock ---
        if not fresh:
            with self._lock:
                if (
                    self._balance_value is not None
                    and (now - self._balance_ts) <= _BALANCE_TTL_S
                ):
                    return self._balance_value

        # --- slow path: fetch WITHOUT holding the lock ---
        raw = self._exchange.fetch_balance()
        balance = float(raw["free"]["USDT"])

        # --- write-through under lock ---
        with self._lock:
            self._balance_value = balance
            self._balance_ts = self._clock()  # refresh timestamp after fetch

        return balance

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        fresh: bool = False,
    ) -> pd.DataFrame:
        """Return a DataFrame of closed OHLCV candles.

        Freshness criteria (wall-clock candle boundary):
            1. served_limit >= requested limit (we already have enough rows)
            2. floor(now_utc, tf) == stored_last_closed_open_time
               (the currently-forming candle epoch matches what was stored)

        The forming candle (the last row of the exchange response) is stripped
        before storing and returning so callers always see only closed candles.

        served_limit is monotonic: a smaller-limit request never shrinks a
        larger cached frame.

        Args:
            symbol: Normalised symbol, e.g. "BTC/USDT:USDT".
            timeframe: CCXT timeframe string, e.g. "1h" or "4h".
            limit: Minimum number of closed candles required.
            fresh: If True, bypass the cache and write-through.

        Returns:
            DataFrame of closed candles (forming candle excluded).
        """
        if timeframe not in _TF_SECONDS:
            raise ValueError(
                f"Unknown timeframe '{timeframe}'; "
                f"supported: {list(_TF_SECONDS.keys())}"
            )

        key = (symbol, timeframe)
        now = self._clock()
        forming_open_s = _floor_to_tf(now, timeframe)

        if not fresh:
            # --- fast path: check under lock ---
            with self._lock:
                entry = self._ohlcv_cache.get(key)
                if entry is not None:
                    cached_df, cached_closed_open_s, cached_served_limit = entry
                    cache_is_fresh = (
                        cached_served_limit >= limit
                        and cached_closed_open_s == forming_open_s - _TF_SECONDS[timeframe]
                    )
                    if cache_is_fresh:
                        # Return a slice respecting the requested limit
                        return cached_df.iloc[-limit:].copy() if limit < len(cached_df) else cached_df.copy()

        # --- slow path: fetch WITHOUT holding the lock ---
        # Request one extra row so we have a forming candle to strip
        raw_df = self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit + 1)

        # Strip the forming candle (last row)
        closed_df = raw_df.iloc[:-1].copy() if len(raw_df) > 0 else raw_df.copy()

        # Determine the last-closed candle's open-time
        if len(closed_df) == 0:
            last_closed_open_s = forming_open_s - _TF_SECONDS[timeframe]
        else:
            last_closed_open_s = int(closed_df.index[-1].timestamp())

        # --- write-through under lock ---
        with self._lock:
            # served_limit is monotonic: keep the max
            existing = self._ohlcv_cache.get(key)
            old_served = existing[2] if existing is not None else 0
            new_served = max(old_served, len(closed_df))

            # If existing cache has MORE rows for the same closed-candle epoch,
            # keep it but update served_limit.  Otherwise overwrite.
            if (
                existing is not None
                and len(existing[0]) > len(closed_df)
                and existing[1] == last_closed_open_s
            ):
                # Keep larger df, just ensure served_limit recorded correctly
                self._ohlcv_cache[key] = (existing[0], existing[1], max(existing[2], new_served))
                stored_df = existing[0]
            else:
                self._ohlcv_cache[key] = (closed_df, last_closed_open_s, new_served)
                stored_df = closed_df

        # Return a slice respecting the requested limit
        return stored_df.iloc[-limit:].copy() if limit < len(stored_df) else stored_df.copy()
