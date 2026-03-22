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

    def __init__(self, config: dict, shared_exchange=None) -> None:
        """Initialize the exchange client.

        Args:
            config: Bot configuration dictionary. Reads "exchange" key
                    ("bybit" or "binance", defaults to "bybit") and
                    "use_testnet" key.
            shared_exchange: Optional pre-built ccxt exchange instance with
                markets already loaded.  When provided, the client skips
                _init_bybit/_init_binance and load_markets(), saving ~60 MB
                and several seconds per bot in multi-bot mode.
                Use bot.shared_exchange_pool.get_shared_exchange() to obtain
                a suitable instance.
        """
        self.config = config
        self._last_request_time = 0.0
        self._exchange_name = config.get("exchange", "bybit").lower()

        if shared_exchange is not None:
            # Fast path: reuse caller-supplied exchange (already has markets loaded).
            self.exchange = shared_exchange
            logger.info(
                "exchange_init_shared",
                extra={
                    "exchange": self._exchange_name,
                    "testnet": config.get("use_testnet", True),
                },
            )
        else:
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
            # Binance futures: SL/TP are placed as separate algo conditional
            # orders via ccxt stopLossPrice/takeProfitPrice params (routed to
            # /fapi/v1/algo endpoint).  Do NOT pass in main market order.
            if sl is not None:
                sl = _safe_precision(self.exchange, self.symbol, sl, "price")
            if tp is not None:
                tp = _safe_precision(self.exchange, self.symbol, tp, "price")
        else:
            # Bybit uses nested triggerPrice dicts in the main order
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

        # Compute filled size BEFORE placing SL/TP so they use actual size
        filled_size = float(order.get("filled") or order.get("amount") or size)

        # Binance: place SL and TP as separate algo conditional orders
        # using ccxt unified stopLossPrice / takeProfitPrice params which
        # route to the Binance /fapi/v1/algo endpoint automatically.
        # IMPORTANT: use filled_size, not requested size — prevents SL/TP
        # quantity mismatch on partial fills.
        if self._exchange_name == "binance" and filled_size > 0:
            sl_order_side = "sell" if side == "buy" else "buy"
            sl_placed = False
            if sl is not None:
                try:
                    self._retry(
                        self.exchange.create_order,
                        self.symbol, "market", sl_order_side, filled_size, None,
                        {"stopLossPrice": sl, "reduceOnly": True},
                    )
                    sl_placed = True
                    logger.info("binance_sl_order_placed", extra={"sl": sl, "size": filled_size})
                except Exception as e:
                    logger.error("binance_sl_order_failed", extra={"sl": sl, "error": str(e)})
            # Only place TP if SL was successfully placed (never have TP without SL)
            if tp is not None and (sl_placed or sl is None):
                try:
                    self._retry(
                        self.exchange.create_order,
                        self.symbol, "market", sl_order_side, filled_size, None,
                        {"takeProfitPrice": tp, "reduceOnly": True},
                    )
                    logger.info("binance_tp_order_placed", extra={"tp": tp, "size": filled_size})
                except Exception as e:
                    logger.error("binance_tp_order_failed", extra={"tp": tp, "error": str(e)})
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

        On Binance, also cancels algo conditional orders (SL/TP) which live
        in a separate order book from regular orders.

        Args:
            symbol: Trading pair. Defaults to configured symbol.
        """
        symbol = symbol or self.symbol
        try:
            self._retry(self.exchange.cancel_all_orders, symbol)
        except Exception as e:
            # Binance may return error if no regular orders exist
            if "no order" not in str(e).lower():
                logger.warning("cancel_regular_orders_failed", extra={"error": str(e)})

        # Binance: also cancel algo conditional orders (SL/TP)
        if self._exchange_name == "binance":
            try:
                market = self.exchange.market(symbol)
                self._retry(
                    self.exchange.fapiPrivateDeleteAlgoOpenOrders,
                    {"symbol": market["id"]},
                )
            except Exception as e:
                logger.warning("cancel_algo_orders_failed", extra={"error": str(e)})

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

    def price_precision(self, price: float, symbol: Optional[str] = None) -> float:
        """Round a price value to exchange-specific precision.

        Args:
            price: Raw price value.
            symbol: Trading pair. Defaults to configured symbol.

        Returns:
            Precision-adjusted price.
        """
        symbol = symbol or self.symbol
        return _safe_precision(self.exchange, symbol, price, "price")

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

    def _get_binance_algo_orders(self, symbol: str) -> list[dict]:
        """Fetch open algo conditional orders for a symbol on Binance.

        Binance futures stores SL/TP (STOP_MARKET, TAKE_PROFIT_MARKET) as
        algo conditional orders that do NOT appear in fetch_open_orders.
        Must use the /fapi/v1/algo/openOrders endpoint instead.

        Args:
            symbol: Normalised ccxt symbol (e.g. 'BTC/USDT:USDT').

        Returns:
            List of raw algo order dicts with keys: algoId, orderType,
            triggerPrice, algoStatus, side, etc.
        """
        market = self.exchange.market(symbol)
        exchange_symbol = market["id"]
        try:
            response = self._retry(
                self.exchange.fapiPrivateGetOpenAlgoOrders,
                {"symbol": exchange_symbol},
            )
            # Binance returns {"orders": [...], "total": n} — NOT a raw list.
            # A bare isinstance(response, list) check silently returns [] every time.
            if isinstance(response, list):
                return response
            if isinstance(response, dict):
                return response.get("orders", [])
            return []
        except Exception as e:
            logger.warning("binance_get_algo_orders_failed", extra={"error": str(e)})
            return []

    def _modify_sl_binance(self, symbol: str, side: str, new_sl: float) -> bool:
        """Modify stop loss on Binance via cancel-and-recreate pattern.

        Binance futures stores SL orders as algo conditional orders
        (algoType=CONDITIONAL, orderType=STOP_MARKET).  They do NOT appear
        in fetch_open_orders — must use fapiPrivateGetOpenAlgoOrders to
        query and fapiPrivateDeleteAlgoOrder to cancel individual orders.

        Steps:
        1. Fetch open algo orders, cancel only STOP_MARKET ones (preserve TP).
        2. Place a new SL via ccxt stopLossPrice param (routed to algo API).

        Args:
            symbol: Normalised ccxt symbol (e.g. 'BTC/USDT:USDT').
            side: Position side — 'buy' for long, 'sell' for short.
            new_sl: New stop loss trigger price.

        Returns:
            True if the new SL was successfully placed.
        """
        sl_order_side = "sell" if side == "buy" else "buy"
        market = self.exchange.market(symbol)
        exchange_symbol = market["id"]

        cancelled_sl = False
        try:
            # Step 1: Cancel only STOP_MARKET algo orders (preserve TP orders)
            try:
                algo_orders = self._get_binance_algo_orders(symbol)
                for ao in algo_orders:
                    order_type = str(ao.get("orderType", "")).upper()
                    if order_type in ("STOP_MARKET", "STOP"):
                        algo_id = ao.get("algoId")
                        if algo_id:
                            try:
                                self._retry(
                                    self.exchange.fapiPrivateDeleteAlgoOrder,
                                    {"symbol": exchange_symbol, "algoId": str(algo_id)},
                                )
                                cancelled_sl = True
                            except Exception:
                                pass
                if cancelled_sl:
                    logger.info("binance_sl_algo_cancelled", extra={"symbol": symbol})
                else:
                    logger.info("binance_no_sl_algo_to_cancel", extra={"symbol": symbol})
            except Exception as cancel_err:
                err_str = str(cancel_err).lower()
                if "-2011" in str(cancel_err) or "no open" in err_str or "not found" in err_str:
                    logger.info("binance_no_algo_orders", extra={"symbol": symbol})
                else:
                    raise

            # Step 2: Place new SL via ccxt stopLossPrice → algo API
            new_sl_price = _safe_precision(self.exchange, symbol, new_sl, "price")

            # Need position size for the new SL order
            positions = self._retry(self.exchange.fetch_positions, [symbol])
            pos_size = 0.0
            for pos in positions:
                if float(pos.get("contracts", 0)) > 0:
                    pos_size = float(pos["contracts"])
                    break

            if pos_size <= 0:
                logger.warning(
                    "binance_sl_modify_no_position",
                    extra={"symbol": symbol, "reason": "no active position found, cannot place SL"},
                )
                return False

            self._retry(
                self.exchange.create_order,
                symbol,
                "market",
                sl_order_side,
                pos_size,
                None,
                {"stopLossPrice": new_sl_price, "reduceOnly": True},
            )
            logger.info(
                "binance_sl_placed",
                extra={"symbol": symbol, "side": sl_order_side, "new_sl": new_sl_price},
            )
            return True
        except Exception as e:
            logger.error(
                "binance_sl_modify_failed",
                extra={"symbol": symbol, "new_sl": new_sl, "error": str(e)},
            )
            # If we cancelled old SL but failed to place new one, try emergency
            # using actual position size (never a hardcoded fallback)
            if cancelled_sl:
                try:
                    emergency_sl = _safe_precision(self.exchange, symbol, new_sl, "price")
                    emergency_positions = self._retry(self.exchange.fetch_positions, [symbol])
                    emergency_size = 0.0
                    for ep in emergency_positions:
                        if float(ep.get("contracts", 0)) > 0:
                            emergency_size = float(ep["contracts"])
                            break
                    if emergency_size <= 0:
                        logger.error(
                            "binance_emergency_sl_no_position",
                            extra={"symbol": symbol},
                        )
                        return False
                    self._retry(
                        self.exchange.create_order,
                        symbol, "market", sl_order_side, emergency_size, None,
                        {"stopLossPrice": emergency_sl, "reduceOnly": True},
                    )
                    logger.warning("binance_emergency_sl_placed", extra={"sl": emergency_sl})
                    return True
                except Exception as e2:
                    logger.error("binance_emergency_sl_failed", extra={"error": str(e2)})
            return False

    def modify_tp_binance(self, symbol: Optional[str] = None, side: str = "buy", new_tp: float = 0.0) -> bool:
        """Modify take profit on Binance via cancel-and-recreate pattern.

        Cancels existing TAKE_PROFIT_MARKET algo orders and places a new one.

        Args:
            symbol: Trading pair. Defaults to configured symbol.
            side: Position side ('buy' for long, 'sell' for short).
            new_tp: New take profit trigger price.

        Returns:
            True if the new TP was successfully placed.
        """
        symbol = symbol or self.symbol
        if self._exchange_name != "binance":
            return False

        tp_order_side = "sell" if side == "buy" else "buy"
        market = self.exchange.market(symbol)
        exchange_symbol = market["id"]

        try:
            # Cancel existing TAKE_PROFIT_MARKET algo orders only
            algo_orders = self._get_binance_algo_orders(symbol)
            for ao in algo_orders:
                if str(ao.get("orderType", "")).upper() in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT"):
                    algo_id = ao.get("algoId")
                    if algo_id:
                        try:
                            self._retry(
                                self.exchange.fapiPrivateDeleteAlgoOrder,
                                {"symbol": exchange_symbol, "algoId": str(algo_id)},
                            )
                        except Exception:
                            pass

            # Place new TP with actual position size
            new_tp_price = _safe_precision(self.exchange, symbol, new_tp, "price")
            positions = self._retry(self.exchange.fetch_positions, [symbol])
            pos_size = 0.0
            for pos in positions:
                if float(pos.get("contracts", 0)) > 0:
                    pos_size = float(pos["contracts"])
                    break

            if pos_size <= 0:
                logger.warning("binance_tp_modify_no_position", extra={"symbol": symbol})
                return False

            self._retry(
                self.exchange.create_order,
                symbol, "market", tp_order_side, pos_size, None,
                {"takeProfitPrice": new_tp_price, "reduceOnly": True},
            )
            logger.info("binance_tp_modified", extra={"symbol": symbol, "new_tp": new_tp_price})
            return True
        except Exception as e:
            logger.error("binance_tp_modify_failed", extra={"symbol": symbol, "error": str(e)})
            return False

    def modify_sl(self, symbol: Optional[str] = None, side: str = "buy", new_sl: float = 0.0) -> bool:
        """Modify the stop loss of an existing position on exchange.

        For Bybit, uses the set_trading_stop v5 endpoint (in-place update).
        For Binance, uses a cancel-and-recreate pattern (STOP_MARKET order).

        Args:
            symbol: Trading pair. Defaults to configured symbol.
            side: Position side ('buy' for long, 'sell' for short).
            new_sl: New stop loss price.

        Returns:
            True if successful.
        """
        symbol = symbol or self.symbol
        if self._exchange_name == "binance":
            return self._modify_sl_binance(symbol, side, new_sl)
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
                raise AttributeError(
                    "ccxt Bybit instance has no privatePostV5PositionTradingStop method"
                )

            logger.info(
                "sl_modified",
                extra={"symbol": symbol, "side": side, "new_sl": new_sl},
            )
            return True
        except Exception as e:
            logger.warning("sl_modify_failed", extra={"error": str(e)})
            return False

    def set_leverage(self, leverage: int, symbol: Optional[str] = None) -> int:
        """Set leverage for a symbol, auto-reducing if exchange rejects it.

        Binance returns error -4028 when requested leverage exceeds the
        symbol's maximum.  When that happens we halve the leverage and retry
        (up to 5 attempts, minimum 1x).

        Args:
            leverage: Desired leverage multiplier.
            symbol: Trading pair. Defaults to configured symbol.

        Returns:
            The leverage value that was actually set on the exchange.
        """
        symbol = symbol or self.symbol
        current_lev = leverage

        for attempt in range(5):
            try:
                self._retry(self.exchange.set_leverage, current_lev, symbol)
                if current_lev != leverage:
                    logger.warning(
                        "leverage_auto_reduced",
                        extra={
                            "symbol": symbol,
                            "requested": leverage,
                            "actual": current_lev,
                        },
                    )
                else:
                    logger.info(
                        "leverage_set",
                        extra={"symbol": symbol, "leverage": current_lev},
                    )
                return current_lev
            except Exception as e:
                err_msg = str(e).lower()
                # Bybit: leverage already set to this value
                if "not modified" in err_msg:
                    logger.info(
                        "leverage_already_set",
                        extra={"symbol": symbol, "leverage": current_lev},
                    )
                    return current_lev
                # Binance -4028 or generic leverage rejection
                if any(kw in err_msg for kw in ("-4028", "invalid", "exceed", "max")) and "leverage" in err_msg:
                    new_lev = max(current_lev // 2, 1)
                    logger.warning(
                        "leverage_rejected_retrying",
                        extra={
                            "symbol": symbol,
                            "rejected": current_lev,
                            "next_try": new_lev,
                            "attempt": attempt + 1,
                            "error": str(e),
                        },
                    )
                    if new_lev == current_lev:
                        # Already at 1x, can't go lower
                        return current_lev
                    current_lev = new_lev
                    continue
                raise
        # Exhausted retries — return last attempted value
        logger.warning(
            "leverage_set_exhausted_retries",
            extra={"symbol": symbol, "final_leverage": current_lev},
        )
        return current_lev
