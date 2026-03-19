"""Exchange wrapper using ccxt for order management and data fetching.

Supports Bybit (default) and Binance futures testnet.
For Binance testnet, ccxt sandbox mode is broken (blocked in ccxt 4.5.44+),
so API URLs are manually overridden to https://testnet.binancefuture.com.
"""

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


def _safe_precision(exchange, symbol: str, value: float, kind: str = "amount") -> float:
    """Apply exchange-specific precision to a value.

    Args:
        exchange: ccxt exchange instance.
        symbol: Trading pair.
        value: Value to round.
        kind: "amount" for contract size, "price" for price levels.

    Returns:
        Precision-adjusted value.
    """
    try:
        if kind == "amount":
            return float(exchange.amount_to_precision(symbol, value))
        return float(exchange.price_to_precision(symbol, value))
    except Exception:
        # Fallback to reasonable defaults
        return round(value, 6 if kind == "amount" else 2)


class BybitClient:
    """Perpetual Futures client wrapping ccxt with retry logic and rate limiting.

    Supports Bybit (default) and Binance futures. Set config["exchange"] to
    "bybit" or "binance". Binance testnet bypasses the broken ccxt sandbox
    mode by directly overriding API URLs.
    """

    MAX_RETRIES = 3
    RATE_LIMIT_DELAY = 0.1  # 10 req/sec = 100ms between requests

    # Binance futures testnet base URL
    _BINANCE_TESTNET_BASE = "https://testnet.binancefuture.com"

    def __init__(self, config: dict) -> None:
        """Initialize the exchange client.

        Args:
            config: Bot configuration dictionary. Reads "exchange" key
                    ("bybit" or "binance", defaults to "bybit") and
                    "use_testnet" key.
        """
        self.config = config
        self._last_request_time = 0.0
        self._exchange_name = config.get("exchange", "bybit").lower()

        exchange_params = {
            "apiKey": os.getenv("API_KEY", ""),
            "secret": os.getenv("API_SECRET", ""),
            "enableRateLimit": True,
        }

        use_testnet = config.get("use_testnet", True)

        if self._exchange_name == "binance":
            self._init_binance(exchange_params, use_testnet)
        else:
            self._init_bybit(exchange_params, use_testnet)

        self.exchange.load_markets()
        self._normalize_symbol()

    def _normalize_symbol(self) -> None:
        """Resolve config symbol to ccxt unified market key.

        Config may have "BTCUSDT" but ccxt needs "BTC/USDT:USDT" for
        perpetual swaps/futures. Both Bybit and Binance futures use this
        unified format, so the same logic applies to both exchanges.
        """
        raw_symbol = self.config["symbol"]
        base = raw_symbol.replace("USDT", "")
        perp_symbol = f"{base}/USDT:USDT"
        if perp_symbol in self.exchange.markets:
            self.symbol = perp_symbol
            logger.info(
                "symbol_normalized",
                extra={"config_symbol": raw_symbol, "ccxt_symbol": perp_symbol},
            )
        elif raw_symbol in self.exchange.markets:
            self.symbol = raw_symbol
        else:
            # Try to find matching market by exchange ID
            matched = None
            for market_symbol, market in self.exchange.markets.items():
                if market.get("id", "") == raw_symbol or market.get("id", "") == raw_symbol.upper():
                    matched = market_symbol
                    break
            if matched:
                self.symbol = matched
                logger.info(
                    "symbol_normalized",
                    extra={"config_symbol": raw_symbol, "ccxt_symbol": matched},
                )
            else:
                self.symbol = raw_symbol
                logger.warning(
                    "symbol_not_found_in_markets",
                    extra={"symbol": raw_symbol, "falling_back": raw_symbol},
                )

    def _init_bybit(self, exchange_params: dict, use_testnet: bool) -> None:
        """Initialize Bybit exchange (default behaviour, unchanged)."""
        exchange_params["options"] = {"defaultType": "swap"}
        if use_testnet:
            self.exchange = ccxt.bybit(exchange_params)
            self.exchange.set_sandbox_mode(True)
            logger.info("exchange_init", extra={"exchange": "bybit", "mode": "testnet"})
        else:
            self.exchange = ccxt.bybit(exchange_params)
            logger.warning(
                "exchange_init",
                extra={"exchange": "bybit", "mode": "LIVE", "warning": "REAL MONEY MODE"},
            )

    def _init_binance(self, exchange_params: dict, use_testnet: bool) -> None:
        """Initialize Binance futures exchange.

        For testnet, ccxt sandbox mode is explicitly broken (blocked in
        ccxt >= 4.5.44 for futures), so we manually override the fapi
        endpoint URLs instead.
        """
        exchange_params["options"] = {"defaultType": "future"}
        self.exchange = ccxt.binance(exchange_params)

        if use_testnet:
            # Do NOT call set_sandbox_mode(True) — it is broken for futures in
            # ccxt 4.5.44+. Instead, override the fapi URLs directly.
            base = self._BINANCE_TESTNET_BASE
            self.exchange.urls["api"]["fapiPublic"] = f"{base}/fapi/v1"
            self.exchange.urls["api"]["fapiPublicV2"] = f"{base}/fapi/v2"
            self.exchange.urls["api"]["fapiPublicV3"] = f"{base}/fapi/v3"
            self.exchange.urls["api"]["fapiPrivate"] = f"{base}/fapi/v1"
            self.exchange.urls["api"]["fapiPrivateV2"] = f"{base}/fapi/v2"
            self.exchange.urls["api"]["fapiPrivateV3"] = f"{base}/fapi/v3"
            # Testnet does not expose sapi endpoints — disable features that
            # call them during load_markets() or fetch_balance().
            self.exchange.has["fetchCurrencies"] = False
            self.exchange.has["fetchMarginMarkets"] = False
            # Also redirect sapi URLs to testnet to prevent auth errors
            self.exchange.urls["api"]["sapi"] = f"{base}/sapi/v1"
            self.exchange.urls["api"]["sapiV2"] = f"{base}/sapi/v2"
            self.exchange.urls["api"]["sapiV3"] = f"{base}/sapi/v3"
            self.exchange.urls["api"]["sapiV4"] = f"{base}/sapi/v4"
            logger.info(
                "exchange_init",
                extra={"exchange": "binance", "mode": "testnet", "base_url": base},
            )
        else:
            logger.warning(
                "exchange_init",
                extra={"exchange": "binance", "mode": "LIVE", "warning": "REAL MONEY MODE"},
            )

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
            except ccxt.RateLimitExceeded as e:
                last_error = e
                wait_time = 2 ** (attempt + 1)  # Longer backoff for rate limits
                logger.warning(
                    "rate_limit_hit",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self.MAX_RETRIES,
                        "wait_seconds": wait_time,
                        "error": str(e),
                    },
                )
                time.sleep(wait_time)
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
        free = usdt.get("free", None)
        free = free if free is not None else usdt.get("total", 0.0)
        logger.info("balance_fetched", extra={"usdt_free": free})
        return float(free)

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
        reduce_only: bool = False,
    ) -> OrderResult:
        """Place a market order with optional stop loss and take profit.

        Args:
            side: 'buy' or 'sell'.
            size: Position size in base currency.
            sl: Stop loss price.
            tp: Take profit price.
            reduce_only: If True, order only reduces an existing position (no new position).

        Returns:
            OrderResult with order details.
        """
        params: dict = {}
        # Apply exchange precision to all values
        size = _safe_precision(self.exchange, self.symbol, size, "amount")
        if reduce_only:
            params["reduceOnly"] = True

        if self._exchange_name == "binance":
            # Binance futures uses flat stopPrice / takeProfit params,
            # not the nested Bybit dict format.
            if sl is not None:
                sl = _safe_precision(self.exchange, self.symbol, sl, "price")
                params["stopLoss"] = sl
            if tp is not None:
                tp = _safe_precision(self.exchange, self.symbol, tp, "price")
                params["takeProfit"] = tp
        else:
            # Bybit uses nested triggerPrice dicts
            if sl is not None:
                sl = _safe_precision(self.exchange, self.symbol, sl, "price")
                params["stopLoss"] = {"triggerPrice": sl}
            if tp is not None:
                tp = _safe_precision(self.exchange, self.symbol, tp, "price")
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

        # Use actual filled size, not requested size (handles partial fills)
        filled_size = float(order.get("filled") or order.get("amount") or size)
        if filled_size < size * 0.95:
            logger.warning(
                "partial_fill_detected",
                extra={
                    "requested": size,
                    "filled": filled_size,
                    "filled_pct": round(filled_size / size * 100, 1) if size > 0 else 0,
                },
            )

        # Extract execution price safely (market orders may not have 'average')
        avg_price = order.get("average") or order.get("price")
        if avg_price is None:
            raw_info = order.get("info", {})
            avg_price = raw_info.get("avgPrice") or raw_info.get("price")
            if avg_price is not None:
                avg_price = float(avg_price)

        result = OrderResult(
            order_id=order["id"],
            symbol=order["symbol"],
            side=side,
            size=filled_size,
            price=avg_price,
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
                "size": filled_size,
                "requested_size": size,
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
            # Use normalized comparison since symbol formats may differ
            pos_sym = pos.get("symbol", "").replace("/", "").replace(":USDT", "").upper()
            cfg_sym = symbol.replace("/", "").replace(":USDT", "").upper()
            if pos_sym == cfg_sym and float(pos["contracts"]) > 0:
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

    def get_ticker_price(self, symbol: Optional[str] = None) -> float:
        """Get current ticker price.

        Args:
            symbol: Trading pair. Defaults to configured symbol.

        Returns:
            Current last price.
        """
        symbol = symbol or self.symbol
        ticker = self._retry(self.exchange.fetch_ticker, symbol)
        return float(ticker.get("last", 0.0))

    def get_closed_pnl(self, symbol: Optional[str] = None, since_ms: Optional[int] = None) -> list[dict]:
        """Get recently closed positions with actual PnL from exchange.

        Args:
            symbol: Trading pair. Defaults to configured symbol.
            since_ms: Fetch trades since this timestamp (ms). Defaults to last 24h.

        Returns:
            List of dicts with: side, entry_price, exit_price, pnl, size, timestamp.
        """
        symbol = symbol or self.symbol
        if since_ms is None:
            since_ms = int((time.time() - 86400) * 1000)

        try:
            # Fetch closed orders to get fill prices
            trades = self._retry(
                self.exchange.fetch_my_trades, symbol, since_ms, limit=50
            )
            return [
                {
                    "id": t.get("id"),
                    "side": t.get("side"),
                    "price": float(t.get("price", 0)),
                    "amount": float(t.get("amount", 0)),
                    "cost": float(t.get("cost", 0)),
                    "timestamp": t.get("timestamp"),
                    "info": t.get("info", {}),
                }
                for t in trades
            ]
        except Exception as e:
            logger.warning("get_closed_pnl_failed", extra={"error": str(e)})
            return []

    def modify_sl(self, symbol: Optional[str] = None, side: str = "buy", new_sl: float = 0.0) -> bool:
        """Modify the stop loss of an existing position on exchange.

        Uses Bybit's set_trading_stop endpoint to update SL without
        cancelling and recreating orders. Not supported for Binance
        (returns False gracefully — caller should cancel and recreate).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
            side: Position side ('buy' for long, 'sell' for short).
            new_sl: New stop loss price.

        Returns:
            True if successful.
        """
        if self._exchange_name == "binance":
            logger.info(
                "sl_modify_skipped",
                extra={"reason": "binance_not_supported", "new_sl": new_sl},
            )
            return False
        symbol = symbol or self.symbol
        try:
            # Use Bybit v5 private API to modify position SL
            # ccxt generates implicit API methods in camelCase
            market = self.exchange.market(symbol)
            params = {
                "category": "linear",
                "symbol": market["id"],
                "stopLoss": str(new_sl),
                "positionIdx": 0,  # One-way mode
            }

            # Try camelCase (ccxt convention), then snake_case fallback
            method = getattr(
                self.exchange,
                "privatePostV5PositionTradingStop",
                getattr(self.exchange, "private_post_v5_position_trading_stop", None),
            )
            if method is not None:
                self._retry(method, params=params)
            else:
                # Last resort: use generic private API call
                self._retry(
                    self.exchange.privatePostV5PositionTradingStop,
                    params=params,
                )

            logger.info(
                "sl_modified",
                extra={"symbol": symbol, "side": side, "new_sl": new_sl},
            )
            return True
        except Exception as e:
            logger.warning("sl_modify_failed", extra={"error": str(e)})
            return False

    def set_leverage(self, leverage: int, symbol: Optional[str] = None) -> None:
        """Set leverage for a symbol.

        Args:
            leverage: Leverage multiplier.
            symbol: Trading pair. Defaults to configured symbol.
        """
        symbol = symbol or self.symbol
        try:
            self._retry(self.exchange.set_leverage, leverage, symbol)
        except Exception as e:
            # Bybit returns error 110043 if leverage is already set to this value
            if "not modified" in str(e):
                logger.info(
                    "leverage_already_set",
                    extra={"symbol": symbol, "leverage": leverage},
                )
                return
            raise
        logger.info(
            "leverage_set", extra={"symbol": symbol, "leverage": leverage}
        )
