"""Async wrapper around BybitClient for non-blocking exchange access.

This module provides:
  - AsyncBybitClient: async wrapper delegating to BybitClient via asyncio.to_thread()
  - TradingPort: Protocol defining the exchange interface for testability / multi-exchange support

The sync BybitClient in exchange.py is left unchanged so the backtest engine
can continue to call it directly without an event loop.
"""

import asyncio
from typing import Optional, Protocol, runtime_checkable

import pandas as pd

from bot.exchange import BybitClient, OrderResult


@runtime_checkable
class TradingPort(Protocol):
    """Abstract exchange interface for trading operations.

    Any class that implements these async methods satisfies the protocol.
    Use this for:
      - Injecting mock exchanges in unit tests
      - Swapping in a backtest paper-trading implementation
      - Future multi-exchange support (Binance, OKX, …)
    """

    async def get_balance(self) -> float:
        """Return available USDT balance."""
        ...

    async def get_positions(self) -> list:
        """Return list of active position dicts."""
        ...

    async def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> pd.DataFrame:
        """Return OHLCV DataFrame with DatetimeIndex."""
        ...

    async def place_order(
        self,
        side: str,
        size: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> OrderResult:
        """Place a market order. Returns OrderResult."""
        ...

    async def modify_sl(
        self,
        symbol: Optional[str] = None,
        side: str = "buy",
        new_sl: float = 0.0,
    ) -> bool:
        """Modify the stop-loss price of an open position. Returns True on success."""
        ...

    async def set_leverage(
        self, leverage: int, symbol: Optional[str] = None
    ) -> int:
        """Set leverage multiplier for the given symbol."""
        ...

    async def get_ticker_price(self, symbol: Optional[str] = None) -> float:
        """Return current last price."""
        ...

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> None:
        """Cancel all open orders for the given symbol."""
        ...

    async def close_all_positions(self, symbol: Optional[str] = None) -> None:
        """Market-close all open positions for the given symbol."""
        ...

    async def get_closed_pnl(
        self,
        symbol: Optional[str] = None,
        since_ms: Optional[int] = None,
    ) -> list:
        """Return recently closed trade records with PnL data."""
        ...

    async def get_funding_rate(self, symbol: Optional[str] = None) -> float:
        """Return current perpetual funding rate."""
        ...


class AsyncBybitClient:
    """Async wrapper that delegates every call to BybitClient via asyncio.to_thread().

    This keeps the synchronous BybitClient intact for backtest use while
    providing non-blocking access for the live async trading loop.

    Usage::

        client = AsyncBybitClient(config)
        balance = await client.get_balance()

        # Access the underlying sync client if needed (e.g., backtest)
        sync = client.sync_client
    """

    def __init__(self, config: dict) -> None:
        """Initialise the async client.

        Args:
            config: Bot configuration dictionary (same as BybitClient expects).
        """
        self._sync = BybitClient(config)

    @property
    def sync_client(self) -> BybitClient:
        """Access the underlying synchronous BybitClient.

        Useful for backtest code or one-off synchronous calls that do not
        need to run inside an event loop.
        """
        return self._sync

    # ------------------------------------------------------------------
    # Account / Market data
    # ------------------------------------------------------------------

    async def get_balance(self) -> float:
        """Return available USDT balance (non-blocking)."""
        return await asyncio.to_thread(self._sync.get_balance)

    async def get_positions(self) -> list:
        """Return list of active position dicts (non-blocking)."""
        return await asyncio.to_thread(self._sync.get_positions)

    async def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> pd.DataFrame:
        """Fetch OHLCV candlestick data (non-blocking).

        Args:
            symbol: Trading pair symbol (e.g., 'BTC/USDT:USDT').
            timeframe: Candle timeframe (e.g., '15m', '1h').
            limit: Number of candles to fetch.

        Returns:
            DataFrame with DatetimeIndex and OHLCV columns.
        """
        return await asyncio.to_thread(self._sync.get_ohlcv, symbol, timeframe, limit)

    async def get_ticker_price(self, symbol: Optional[str] = None) -> float:
        """Return current last price (non-blocking).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
        """
        return await asyncio.to_thread(self._sync.get_ticker_price, symbol)

    async def get_funding_rate(self, symbol: Optional[str] = None) -> float:
        """Return current perpetual funding rate (non-blocking).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
        """
        return await asyncio.to_thread(self._sync.get_funding_rate, symbol)

    async def get_closed_pnl(
        self,
        symbol: Optional[str] = None,
        since_ms: Optional[int] = None,
    ) -> list:
        """Return recently closed trade records (non-blocking).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
            since_ms: Fetch trades since this timestamp (ms). Defaults to last 24h.
        """
        return await asyncio.to_thread(self._sync.get_closed_pnl, symbol, since_ms)

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_order(
        self,
        side: str,
        size: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> OrderResult:
        """Place a market order with optional stop loss and take profit (non-blocking).

        Args:
            side: 'buy' or 'sell'.
            size: Position size in base currency.
            sl: Stop loss price.
            tp: Take profit price.

        Returns:
            OrderResult with order details.
        """
        return await asyncio.to_thread(self._sync.place_order, side, size, sl, tp)

    async def modify_sl(
        self,
        symbol: Optional[str] = None,
        side: str = "buy",
        new_sl: float = 0.0,
    ) -> bool:
        """Modify the stop-loss of an open position (non-blocking).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
            side: Position side ('buy' for long, 'sell' for short).
            new_sl: New stop loss price.

        Returns:
            True if successful.
        """
        return await asyncio.to_thread(self._sync.modify_sl, symbol, side, new_sl)

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> None:
        """Cancel all open orders for a symbol (non-blocking).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
        """
        return await asyncio.to_thread(self._sync.cancel_all_orders, symbol)

    async def close_all_positions(self, symbol: Optional[str] = None) -> None:
        """Market-close all open positions for a symbol (non-blocking).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
        """
        return await asyncio.to_thread(self._sync.close_all_positions, symbol)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    async def set_leverage(
        self, leverage: int, symbol: Optional[str] = None
    ) -> int:
        """Set leverage multiplier (non-blocking), with auto-reduction.

        Args:
            leverage: Leverage multiplier.
            symbol: Trading pair. Defaults to configured symbol.

        Returns:
            The leverage value actually set on the exchange.
        """
        return await asyncio.to_thread(self._sync.set_leverage, leverage, symbol)
