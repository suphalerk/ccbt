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
import os
import threading
from typing import Optional

import ccxt

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
