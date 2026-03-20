"""OANDA forex exchange wrapper for gold (XAU/USD) trading.

Mirrors the BybitClient interface so the TradingEngine can use either
exchange via duck typing. OANDA uses a REST API (oandapyV20) with a
Bearer token rather than an API key/secret pair.

Practice (paper) vs live mode is controlled by the OANDA_PRACTICE env
var or the config key ``use_testnet`` (True → practice).

Symbol format:
    Config may hold "XAU_USD" or "XAUUSD". Both are normalised to the
    OANDA instrument ID "XAU_USD" internally.

Timeframe mapping:
    CCBT uses "15m"/"1h"/"4h" notation; OANDA uses "M15"/"H1"/"H4".
    ``_map_timeframe()`` handles the conversion.
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

import oandapyV20
import oandapyV20.endpoints.accounts as v20_accounts
import oandapyV20.endpoints.instruments as v20_instruments
import oandapyV20.endpoints.orders as v20_orders
import oandapyV20.endpoints.positions as v20_positions
import oandapyV20.endpoints.trades as v20_trades
from oandapyV20.exceptions import V20Error

load_dotenv()

logger = logging.getLogger(__name__)

# Timeframe mapping: CCBT notation → OANDA granularity
_TIMEFRAME_MAP: dict[str, str] = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "30m": "M30",
    "1h": "H1",
    "2h": "H2",
    "4h": "H4",
    "6h": "H6",
    "8h": "H8",
    "12h": "H12",
    "1d": "D",
    "D": "D",
    # OANDA already-formatted passthrough
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "M30": "M30",
    "H1": "H1",
    "H2": "H2",
    "H4": "H4",
    "H6": "H6",
    "H8": "H8",
    "H12": "H12",
}

# URLs
_PRACTICE_URL = "https://api-fxpractice.oanda.com"
_LIVE_URL = "https://api-fxtrade.oanda.com"

# Max OANDA candles per single request
_MAX_CANDLES_PER_REQUEST = 5000


# ---------------------------------------------------------------------------
# Shared dataclass (same as in exchange.py so callers can use either)
# ---------------------------------------------------------------------------

@dataclass
class OrderResult:
    """Result of an order placement — matches BybitClient.OrderResult."""

    order_id: str
    symbol: str
    side: str
    size: float
    price: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    status: str
    raw: dict


# ---------------------------------------------------------------------------
# OandaClient
# ---------------------------------------------------------------------------

class OandaClient:
    """OANDA REST API client wrapping oandapyV20 with retry logic.

    Designed to be a drop-in replacement for BybitClient from the
    TradingEngine's perspective — every public method has the same
    signature and return type.

    Args:
        config: Bot configuration dictionary. Reads ``symbol`` (instrument),
                ``use_testnet`` (True → practice account), and optionally
                ``leverage`` (informational only — OANDA leverage is set at
                the account level, not per trade).
    """

    MAX_RETRIES = 3
    RATE_LIMIT_DELAY = 0.1  # Respect OANDA's rate limits

    def __init__(self, config: dict) -> None:
        self.config = config
        self._last_request_time = 0.0

        token = os.getenv("OANDA_API_TOKEN", "")
        if not token:
            raise ValueError(
                "OANDA_API_TOKEN environment variable is not set. "
                "Add it to your .env file."
            )

        self._account_id = os.getenv("OANDA_ACCOUNT_ID", "")
        if not self._account_id:
            raise ValueError(
                "OANDA_ACCOUNT_ID environment variable is not set. "
                "Add it to your .env file."
            )

        # Determine practice vs live
        use_testnet = config.get("use_testnet", True)
        env_practice = os.getenv("OANDA_PRACTICE", "true").lower()
        is_practice = use_testnet or env_practice not in ("false", "0", "no")

        environment = "practice" if is_practice else "live"
        self._api = oandapyV20.API(
            access_token=token,
            environment=environment,
        )

        mode_label = "practice" if is_practice else "LIVE"
        if not is_practice:
            logger.warning(
                "exchange_init",
                extra={
                    "exchange": "oanda",
                    "mode": "LIVE",
                    "warning": "REAL MONEY MODE",
                },
            )
        else:
            logger.info(
                "exchange_init",
                extra={"exchange": "oanda", "mode": mode_label},
            )

        self.symbol = self._normalize_symbol(config.get("symbol", "XAU_USD"))
        logger.info(
            "symbol_normalized",
            extra={"config_symbol": config.get("symbol"), "oanda_instrument": self.symbol},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        """Normalise various symbol formats to OANDA instrument ID.

        Examples:
            "XAUUSD"  → "XAU_USD"
            "XAU/USD" → "XAU_USD"
            "XAU_USD" → "XAU_USD"
        """
        raw = raw.strip().upper().replace("/", "_").replace("-", "_")
        # Handle concatenated pairs without separator (e.g. XAUUSD)
        if "_" not in raw and len(raw) == 6:
            raw = f"{raw[:3]}_{raw[3:]}"
        return raw

    @staticmethod
    def _map_timeframe(tf: str) -> str:
        """Map CCBT timeframe notation to OANDA granularity string.

        Args:
            tf: Timeframe string (e.g. "15m", "1h", "H4").

        Returns:
            OANDA granularity (e.g. "M15", "H1", "H4").

        Raises:
            ValueError: If the timeframe is not recognised.
        """
        mapped = _TIMEFRAME_MAP.get(tf)
        if mapped is None:
            raise ValueError(
                f"Unsupported timeframe '{tf}'. "
                f"Supported: {list(_TIMEFRAME_MAP.keys())}"
            )
        return mapped

    def _rate_limit(self) -> None:
        """Enforce minimum delay between successive API calls."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            time.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _retry(self, endpoint):
        """Execute an oandapyV20 endpoint with retry and exponential backoff.

        Args:
            endpoint: An oandapyV20 endpoint object (e.g. AccountDetails).

        Returns:
            The response dict returned by ``self._api.request(endpoint)``.

        Raises:
            V20Error: On non-retryable OANDA API errors.
            Exception: After all retries are exhausted.
        """
        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES):
            try:
                self._rate_limit()
                return self._api.request(endpoint)
            except V20Error as e:
                # HTTP 429 → rate limited; others are usually permanent errors
                status = getattr(e, "status", None) or 0
                if int(status) == 429:
                    last_error = e
                    wait_time = 2 ** (attempt + 1)
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
                else:
                    # Non-rate-limit OANDA error — log and re-raise immediately
                    logger.error("oanda_api_error", extra={"status": status, "error": str(e)})
                    raise
            except Exception as e:
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

        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Public interface (mirrors BybitClient)
    # ------------------------------------------------------------------

    def get_balance(self) -> float:
        """Get account balance (NAV) in USD.

        Returns:
            Account NAV (Net Asset Value) in USD.
        """
        ep = v20_accounts.AccountDetails(self._account_id)
        resp = self._retry(ep)
        nav = float(resp["account"]["NAV"])
        logger.info("balance_fetched", extra={"usd_nav": nav})
        return nav

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Fetch OHLCV candlestick data from OANDA.

        Args:
            symbol: Instrument (ignored — uses self.symbol from config).
            timeframe: Candle timeframe in CCBT or OANDA notation.
            limit: Number of completed candles to return (max 5000).

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume.
            Index is UTC datetime. Volume is tick count (OANDA does not provide
            true volume; tick count is the best available proxy).
        """
        granularity = self._map_timeframe(timeframe)
        # OANDA limit includes the forming candle; request one extra and drop it
        fetch_count = min(limit + 1, _MAX_CANDLES_PER_REQUEST)

        params = {
            "granularity": granularity,
            "count": str(fetch_count),
            "price": "M",  # Midpoint candles
        }
        ep = v20_instruments.InstrumentsCandles(self.symbol, params=params)
        resp = self._retry(ep)

        candles = resp.get("candles", [])
        # Drop forming (incomplete) candle — last entry where complete=False
        candles = [c for c in candles if c.get("complete", True)]
        # Apply user's limit after filtering
        candles = candles[-limit:]

        rows = []
        for c in candles:
            mid = c["mid"]
            rows.append(
                {
                    "timestamp": pd.Timestamp(c["time"]).tz_localize(None),  # UTC naive
                    "open": float(mid["o"]),
                    "high": float(mid["h"]),
                    "low": float(mid["l"]),
                    "close": float(mid["c"]),
                    "volume": float(c.get("volume", 0)),
                }
            )

        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df = df.set_index("timestamp")
        logger.info(
            "ohlcv_fetched",
            extra={
                "symbol": self.symbol,
                "timeframe": timeframe,
                "granularity": granularity,
                "rows": len(df),
            },
        )
        return df

    def get_ticker_price(self, symbol: Optional[str] = None) -> float:
        """Get the current mid-price for the instrument.

        Args:
            symbol: Ignored — uses self.symbol. Present for interface parity.

        Returns:
            Current mid-price as float.
        """
        params = {"count": "1", "granularity": "S5", "price": "M"}
        ep = v20_instruments.InstrumentsCandles(self.symbol, params=params)
        resp = self._retry(ep)
        candles = resp.get("candles", [])
        if candles:
            mid = candles[-1]["mid"]
            price = float(mid["c"])
        else:
            # Fallback: derive from account summary prices
            price = 0.0
            logger.warning("ticker_price_fallback", extra={"symbol": self.symbol})

        logger.info("ticker_price_fetched", extra={"symbol": self.symbol, "price": price})
        return price

    def place_order(
        self,
        side: str,
        size: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        reduce_only: bool = False,
    ) -> OrderResult:
        """Place a market order on OANDA.

        OANDA uses units (troy ounces for XAU_USD). Positive units = buy,
        negative = sell.

        The spread is OANDA's implicit commission — there is no explicit fee
        on the order itself. Spread cost is already embedded in fill prices.

        Args:
            side: "buy" or "sell".
            size: Position size in troy ounces (base currency units).
            sl: Stop loss price. If provided, attached as a stop-loss order.
            tp: Take profit price. If provided, attached as a take-profit order.
            reduce_only: If True, close-trade-only semantics (uses ``units``
                         with opposite sign of existing position).

        Returns:
            OrderResult with order details.
        """
        units = round(size, 2)
        if side == "sell":
            units = -units

        order_body: dict = {
            "order": {
                "type": "MARKET",
                "instrument": self.symbol,
                "units": str(units),
                "timeInForce": "FOK",  # Fill-or-Kill for market orders
                "positionFill": "REDUCE_ONLY" if reduce_only else "DEFAULT",
            }
        }

        if sl is not None:
            order_body["order"]["stopLossOnFill"] = {
                "price": f"{sl:.5f}",
                "timeInForce": "GTC",
            }

        if tp is not None:
            order_body["order"]["takeProfitOnFill"] = {
                "price": f"{tp:.5f}",
                "timeInForce": "GTC",
            }

        ep = v20_orders.OrderCreate(self._account_id, data=order_body)
        resp = self._retry(ep)

        # Parse response — OANDA wraps result in orderFillTransaction or
        # orderCancelTransaction depending on outcome.
        fill_tx = resp.get("orderFillTransaction", {})
        order_id = fill_tx.get("orderID") or resp.get("relatedTransactionIDs", [""])[0]
        fill_price = float(fill_tx.get("price", 0)) if fill_tx else 0.0
        filled_units = abs(float(fill_tx.get("units", size))) if fill_tx else size
        status = "closed" if fill_tx else "open"

        result = OrderResult(
            order_id=str(order_id),
            symbol=self.symbol,
            side=side,
            size=filled_units,
            price=fill_price if fill_price > 0 else None,
            sl=sl,
            tp=tp,
            status=status,
            raw=resp,
        )

        logger.info(
            "order_placed",
            extra={
                "order_id": result.order_id,
                "symbol": result.symbol,
                "side": side,
                "size": filled_units,
                "price": result.price,
                "sl": sl,
                "tp": tp,
            },
        )
        return result

    def get_positions(self, symbol: Optional[str] = None) -> list:
        """Get open positions for the instrument.

        Returns a list normalised to the same dict shape the TradingEngine
        expects from BybitClient.get_positions():
            {symbol, side, contracts, entryPrice, ...}

        Args:
            symbol: Ignored — uses self.symbol.

        Returns:
            List of position dicts (empty if no open position).
        """
        ep = v20_positions.PositionDetails(self._account_id, self.symbol)
        try:
            resp = self._retry(ep)
        except V20Error as e:
            # OANDA returns 404 when there is no position — treat as empty
            if "404" in str(e) or "POSITION_NOT_FOUND" in str(e):
                logger.info("no_open_positions", extra={"symbol": self.symbol})
                return []
            raise

        pos_data = resp.get("position", {})
        result: list[dict] = []

        for side_key, oanda_side in (("long", "long"), ("short", "short")):
            side_data = pos_data.get(side_key, {})
            units = abs(float(side_data.get("units", 0)))
            if units > 0:
                avg_price = float(side_data.get("averagePrice", 0))
                unrealised_pl = float(side_data.get("unrealizedPL", 0))
                result.append(
                    {
                        "symbol": self.symbol,
                        "side": side_key,            # "long" or "short"
                        "contracts": units,          # troy oz
                        "entryPrice": avg_price,
                        "unrealisedPnl": unrealised_pl,
                        # BybitClient compat keys
                        "info": side_data,
                    }
                )

        logger.info("positions_fetched", extra={"count": len(result)})
        return result

    def close_all_positions(self, symbol: Optional[str] = None) -> None:
        """Close all open positions for the instrument.

        Sends a MARKET order that fully closes each open side.

        Args:
            symbol: Ignored — uses self.symbol.
        """
        positions = self.get_positions()
        if not positions:
            logger.info("close_all_positions_noop", extra={"symbol": self.symbol})
            return

        for pos in positions:
            side = pos["side"]
            units = pos["contracts"]
            close_side = "sell" if side == "long" else "buy"
            try:
                self.place_order(close_side, units, reduce_only=True)
                logger.info(
                    "position_closed",
                    extra={"symbol": self.symbol, "side": side, "units": units},
                )
            except Exception as e:
                logger.error(
                    "close_position_failed",
                    extra={"symbol": self.symbol, "side": side, "error": str(e)},
                )

    def cancel_all_orders(self, symbol: Optional[str] = None) -> None:
        """Cancel all pending orders for the instrument.

        OANDA pending orders (limit/stop entry orders) are listed via
        TradesList endpoint. MARKET orders are filled synchronously so
        there are rarely pending orders in this strategy.

        Args:
            symbol: Ignored — uses self.symbol.
        """
        params = {"instrument": self.symbol, "state": "PENDING"}
        ep = v20_trades.TradesList(self._account_id, params=params)
        try:
            resp = self._retry(ep)
        except V20Error as e:
            logger.warning("cancel_orders_list_failed", extra={"error": str(e)})
            return

        pending = resp.get("trades", [])
        for trade in pending:
            trade_id = trade.get("id")
            if not trade_id:
                continue
            try:
                import oandapyV20.endpoints.trades as _v20t
                close_ep = _v20t.TradeClose(self._account_id, trade_id)
                self._retry(close_ep)
                logger.info("pending_order_cancelled", extra={"trade_id": trade_id})
            except V20Error as e:
                logger.warning(
                    "cancel_order_failed",
                    extra={"trade_id": trade_id, "error": str(e)},
                )

        logger.info("orders_cancelled", extra={"symbol": self.symbol, "count": len(pending)})

    def modify_sl(
        self,
        symbol: Optional[str] = None,
        side: str = "buy",
        new_sl: float = 0.0,
    ) -> bool:
        """Modify the stop loss on all open trades for the given side.

        OANDA SL is attached to individual trade tickets (not positions).
        This method iterates over all open trade tickets for the instrument
        and updates the SL on each one.

        Args:
            symbol: Ignored — uses self.symbol.
            side: Position side ("buy" for long, "sell" for short).
            new_sl: New stop loss price.

        Returns:
            True if at least one SL was updated successfully.
        """
        target_side = "long" if side == "buy" else "short"
        params = {"instrument": self.symbol, "state": "OPEN"}
        ep = v20_trades.TradesList(self._account_id, params=params)
        try:
            resp = self._retry(ep)
        except V20Error as e:
            logger.error("modify_sl_list_failed", extra={"error": str(e)})
            return False

        open_trades = resp.get("trades", [])
        updated = False

        for trade in open_trades:
            units = float(trade.get("currentUnits", 0))
            trade_side = "long" if units > 0 else "short"
            if trade_side != target_side:
                continue

            trade_id = trade["id"]
            try:
                import oandapyV20.endpoints.trades as _v20t
                body = {"stopLoss": {"price": f"{new_sl:.5f}", "timeInForce": "GTC"}}
                modify_ep = _v20t.TradeCRCDO(self._account_id, trade_id, data=body)
                self._retry(modify_ep)
                updated = True
                logger.info(
                    "sl_modified",
                    extra={"symbol": self.symbol, "trade_id": trade_id, "new_sl": new_sl},
                )
            except V20Error as e:
                logger.error(
                    "sl_modify_failed",
                    extra={"trade_id": trade_id, "new_sl": new_sl, "error": str(e)},
                )

        return updated

    def set_leverage(self, leverage: int, symbol: Optional[str] = None) -> None:
        """OANDA does not support per-trade leverage — leverage is account-level.

        Logs the configured leverage for information and continues. No API
        call is made. To change leverage, adjust account settings in the
        OANDA portal or via the account management API.

        Args:
            leverage: Leverage multiplier (informational only).
            symbol: Ignored.
        """
        logger.info(
            "leverage_noop",
            extra={
                "symbol": self.symbol,
                "leverage": leverage,
                "note": "OANDA leverage is set at account level — no API call made",
            },
        )

    def get_closed_pnl(
        self,
        symbol: Optional[str] = None,
        since_ms: Optional[int] = None,
    ) -> list[dict]:
        """Get recently closed trades with actual PnL from OANDA.

        Args:
            symbol: Ignored — uses self.symbol.
            since_ms: Fetch trades since this timestamp (ms). Defaults to last 24h.

        Returns:
            List of dicts with: id, side, price, amount, cost, timestamp, info.
            The ``cost`` field holds realised PnL for the trade.
        """
        if since_ms is None:
            since_ms = int((time.time() - 86400) * 1000)

        # OANDA TradesList with state=CLOSED
        params = {"instrument": self.symbol, "state": "CLOSED"}
        ep = v20_trades.TradesList(self._account_id, params=params)
        try:
            resp = self._retry(ep)
        except V20Error as e:
            logger.warning("get_closed_pnl_failed", extra={"error": str(e)})
            return []

        trades = resp.get("trades", [])
        result = []
        for t in trades:
            # Filter by since_ms — OANDA returns RFC3339 close time
            close_time_str = t.get("closeTime")
            if close_time_str:
                try:
                    close_ts_ms = int(
                        pd.Timestamp(close_time_str).timestamp() * 1000
                    )
                    if close_ts_ms < since_ms:
                        continue
                except Exception:
                    pass

            units = float(t.get("currentUnits", t.get("initialUnits", 0)))
            side = "buy" if units > 0 else "sell"
            avg_price = float(t.get("averageClosePrice", 0))
            realised_pl = float(t.get("realizedPL", 0))

            result.append(
                {
                    "id": t.get("id"),
                    "side": side,
                    "price": avg_price,
                    "amount": abs(units),
                    "cost": realised_pl,  # Realised PnL in account currency
                    "timestamp": close_time_str,
                    "info": t,
                }
            )

        logger.info(
            "closed_pnl_fetched",
            extra={"symbol": self.symbol, "count": len(result)},
        )
        return result

    def get_funding_rate(self, symbol: Optional[str] = None) -> float:
        """Return 0.0 — forex has no funding rate.

        OANDA charges overnight rollover interest (swap rates) rather than
        periodic funding. This is implicitly included in trade cost and is
        not surfaced here.

        Args:
            symbol: Ignored.

        Returns:
            Always 0.0.
        """
        return 0.0
