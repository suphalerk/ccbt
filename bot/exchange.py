"""Bybit exchange wrapper using ccxt for order management and data fetching."""

import os
import time
import logging
from dataclasses import dataclass
from typing import Optional

import ccxt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """Result of an order placement."""

    order_id: str
    symbol: str
    side: str
    size: float
    price: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    status: str
    raw: dict


class BybitClient:
    """Bybit Perpetual Futures client wrapping ccxt with retry logic and rate limiting."""

    MAX_RETRIES = 3
    RATE_LIMIT_DELAY = 0.1  # 10 req/sec = 100ms between requests

    def __init__(self, config: dict) -> None:
        """Initialize the Bybit client.

        Args:
            config: Bot configuration dictionary.
        """
        self.config = config
        self.symbol = config["symbol"]
        self._last_request_time = 0.0

        exchange_params = {
            "apiKey": os.getenv("API_KEY", ""),
            "secret": os.getenv("API_SECRET", ""),
            "options": {
                "defaultType": "swap",
            },
            "enableRateLimit": True,
        }

        if config.get("use_testnet", True):
            self.exchange = ccxt.bybit(exchange_params)
            self.exchange.set_sandbox_mode(True)
            logger.info("exchange_init", extra={"mode": "testnet"})
        else:
            self.exchange = ccxt.bybit(exchange_params)
            logger.info("exchange_init", extra={"mode": "live"})

        self.exchange.load_markets()

    def _rate_limit(self) -> None:
        """Enforce rate limiting between API calls."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _retry(self, func, *args, **kwargs):
        """Execute a function with retry logic and exponential backoff.

        Args:
            func: The function to execute.

        Returns:
            The function result.

        Raises:
            The last exception if all retries fail.
        """
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                self._rate_limit()
                return func(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                last_error = e
                wait_time = 2 ** attempt
                logger.warning(
                    "api_retry",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self.MAX_RETRIES,
                        "wait_seconds": wait_time,
                        "error": str(e),
                    },
                )
                time.sleep(wait_time)
            except ccxt.ExchangeError as e:
                logger.error("api_exchange_error", extra={"error": str(e)})
                raise
        raise last_error  # type: ignore[misc]

    def get_balance(self) -> float:
        """Get USDT wallet balance.

        Returns:
            Available USDT balance.
        """
        balance = self._retry(self.exchange.fetch_balance)
        usdt = balance.get("USDT", {})
        total = usdt.get("total", 0.0)
        logger.info("balance_fetched", extra={"usdt_total": total})
        return float(total)

    def get_ohlcv(
        self, symbol: str, timeframe: str, limit: int = 100
    ) -> pd.DataFrame:
        """Fetch OHLCV candlestick data.

        Args:
            symbol: Trading pair symbol (e.g., 'BTCUSDT').
            timeframe: Candle timeframe (e.g., '15m', '1h').
            limit: Number of candles to fetch.

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume.
        """
        ohlcv = self._retry(
            self.exchange.fetch_ohlcv, symbol, timeframe, limit=limit
        )
        df = pd.DataFrame(
            ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("timestamp")
        return df

    def place_order(
        self,
        side: str,
        size: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> OrderResult:
        """Place a market order with optional stop loss and take profit.

        Args:
            side: 'buy' or 'sell'.
            size: Position size in base currency.
            sl: Stop loss price.
            tp: Take profit price.

        Returns:
            OrderResult with order details.
        """
        params: dict = {}
        if sl is not None:
            params["stopLoss"] = {"triggerPrice": sl}
        if tp is not None:
            params["takeProfit"] = {"triggerPrice": tp}

        order = self._retry(
            self.exchange.create_order,
            self.symbol,
            "market",
            side,
            size,
            None,
            params,
        )

        result = OrderResult(
            order_id=order["id"],
            symbol=order["symbol"],
            side=side,
            size=size,
            price=order.get("average") or order.get("price"),
            sl=sl,
            tp=tp,
            status=order["status"],
            raw=order,
        )

        logger.info(
            "order_placed",
            extra={
                "order_id": result.order_id,
                "symbol": result.symbol,
                "side": side,
                "size": size,
                "price": result.price,
                "sl": sl,
                "tp": tp,
            },
        )
        return result

    def cancel_all_orders(self, symbol: Optional[str] = None) -> None:
        """Cancel all open orders for a symbol.

        Args:
            symbol: Trading pair. Defaults to configured symbol.
        """
        symbol = symbol or self.symbol
        self._retry(self.exchange.cancel_all_orders, symbol)
        logger.info("orders_cancelled", extra={"symbol": symbol})

    def close_all_positions(self, symbol: Optional[str] = None) -> None:
        """Close all open positions for a symbol.

        Args:
            symbol: Trading pair. Defaults to configured symbol.
        """
        symbol = symbol or self.symbol
        positions = self.get_positions()
        for pos in positions:
            if pos["symbol"] == symbol and float(pos["contracts"]) > 0:
                side = "sell" if pos["side"] == "long" else "buy"
                self._retry(
                    self.exchange.create_order,
                    symbol,
                    "market",
                    side,
                    float(pos["contracts"]),
                    None,
                    {"reduceOnly": True},
                )
        logger.info("positions_closed", extra={"symbol": symbol})

    def get_positions(self) -> list:
        """Get all open positions.

        Returns:
            List of position dictionaries.
        """
        positions = self._retry(self.exchange.fetch_positions, [self.symbol])
        active = [p for p in positions if float(p.get("contracts", 0)) > 0]
        logger.info("positions_fetched", extra={"count": len(active)})
        return active

    def get_funding_rate(self, symbol: Optional[str] = None) -> float:
        """Get current funding rate for a symbol.

        Args:
            symbol: Trading pair. Defaults to configured symbol.

        Returns:
            Current funding rate as a float.
        """
        symbol = symbol or self.symbol
        ticker = self._retry(self.exchange.fetch_funding_rate, symbol)
        rate = float(ticker.get("fundingRate", 0.0))
        logger.info(
            "funding_rate_fetched", extra={"symbol": symbol, "rate": rate}
        )
        return rate

    def set_leverage(self, leverage: int, symbol: Optional[str] = None) -> None:
        """Set leverage for a symbol.

        Args:
            leverage: Leverage multiplier.
            symbol: Trading pair. Defaults to configured symbol.
        """
        symbol = symbol or self.symbol
        self._retry(self.exchange.set_leverage, leverage, symbol)
        logger.info(
            "leverage_set", extra={"symbol": symbol, "leverage": leverage}
        )
