"""Build structured market context for AI analyst from exchange and indicator data."""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from bot.data import add_indicators, compute_ema
from bot.exchange import BybitClient
from bot.news_fetcher import NewsFetcher
from bot.risk import RiskState

logger = logging.getLogger(__name__)


@dataclass
class MarketContext:
    """Complete market context for AI analyst evaluation."""

    # Price data
    symbol: str = ""
    current_price: float = 0.0
    candles_15m: list[dict] = field(default_factory=list)  # 20 latest [o,h,l,c,v]
    candles_1h: list[dict] = field(default_factory=list)  # 10 latest

    # Technical indicators (pre-computed)
    ema_9: float = 0.0
    ema_21: float = 0.0
    ema_50_1h: float = 0.0
    rsi_14: float = 0.0
    atr_14: float = 0.0
    volume_ratio: float = 0.0  # current vol / MA20 vol

    # Market microstructure
    funding_rate: float = 0.0  # positive = longs pay shorts
    open_interest_change: float = 0.0  # % change in OI over 1h
    bid_ask_spread: float = 0.0
    orderbook_imbalance: float = 0.5  # bid_vol / (bid_vol + ask_vol)

    # News
    news_headlines: list[str] = field(default_factory=list)
    news_sentiment: str = "neutral"  # "positive" | "negative" | "neutral"

    # Bot state
    daily_pnl_pct: float = 0.0
    consecutive_losses: int = 0
    hours_since_last_trade: float = 0.0


class ContextBuilder:
    """Builds MarketContext from live exchange data and news feeds.

    Handles missing data gracefully — returns defaults for any unavailable field.
    """

    def __init__(
        self,
        client: BybitClient,
        config: dict,
        news_fetcher: Optional[NewsFetcher] = None,
    ) -> None:
        """Initialize the context builder.

        Args:
            client: Bybit exchange client.
            config: Bot configuration.
            news_fetcher: Optional news fetcher. If None, news fields will be empty.
        """
        self.client = client
        self.config = config
        self.news_fetcher = news_fetcher
        self._last_trade_time: Optional[float] = None

    async def build(
        self,
        symbol: str,
        risk_state: Optional[RiskState] = None,
    ) -> MarketContext:
        """Build a complete market context for AI analysis.

        Args:
            symbol: Trading pair symbol.
            risk_state: Current risk manager state for bot context.

        Returns:
            MarketContext with all available data filled in.
        """
        ctx = MarketContext(symbol=symbol)

        # Fetch price data
        try:
            signal_df = self.client.get_ohlcv(
                symbol, self.config["timeframe_signal"], limit=100
            )
            ctx = self._fill_signal_data(ctx, signal_df)
        except Exception as e:
            logger.warning("context_signal_data_error", extra={"error": str(e)})

        try:
            trend_df = self.client.get_ohlcv(
                symbol, self.config["timeframe_trend"], limit=100
            )
            ctx = self._fill_trend_data(ctx, trend_df)
        except Exception as e:
            logger.warning("context_trend_data_error", extra={"error": str(e)})

        # Latest ticker price (more accurate than candle close)
        try:
            ticker = self.client._retry(
                self.client.exchange.fetch_ticker, symbol
            )
            ctx.current_price = float(ticker.get("last", ctx.current_price))
        except Exception as e:
            logger.warning("context_ticker_error", extra={"error": str(e)})

        # Funding rate
        try:
            ctx.funding_rate = self.client.get_funding_rate(symbol)
        except Exception as e:
            logger.warning("context_funding_error", extra={"error": str(e)})

        # Orderbook data
        try:
            ctx = self._fill_orderbook_data(ctx, symbol)
        except Exception as e:
            logger.warning("context_orderbook_error", extra={"error": str(e)})

        # News headlines
        if self.news_fetcher:
            try:
                lookback_hours = self.config.get("ai_layer", {}).get(
                    "news_lookback_hours", 2
                )
                headlines = await self.news_fetcher.fetch_headlines(
                    lookback_hours=lookback_hours
                )
                ctx.news_headlines = headlines
                ctx.news_sentiment = self._estimate_sentiment(headlines)
            except Exception as e:
                logger.warning("context_news_error", extra={"error": str(e)})

        # Bot state
        if risk_state:
            starting = risk_state.starting_balance
            ctx.daily_pnl_pct = (
                (risk_state.daily_pnl / starting * 100) if starting > 0 else 0.0
            )
            ctx.consecutive_losses = risk_state.consecutive_losses

        if self._last_trade_time:
            ctx.hours_since_last_trade = (
                time.time() - self._last_trade_time
            ) / 3600
        else:
            ctx.hours_since_last_trade = 99.0  # No trades yet

        logger.info(
            "context_built",
            extra={
                "symbol": symbol,
                "price": ctx.current_price,
                "rsi": ctx.rsi_14,
                "funding": ctx.funding_rate,
                "news_count": len(ctx.news_headlines),
            },
        )
        return ctx

    def record_trade_time(self) -> None:
        """Record the current time as the last trade time."""
        self._last_trade_time = time.time()

    def _fill_signal_data(
        self, ctx: MarketContext, df: pd.DataFrame
    ) -> MarketContext:
        """Fill signal timeframe data into context.

        Args:
            ctx: MarketContext to fill.
            df: Signal timeframe OHLCV DataFrame.

        Returns:
            Updated MarketContext.
        """
        df_ind = add_indicators(df, self.config)

        if len(df_ind) == 0:
            return ctx

        last = df_ind.iloc[-1]
        ctx.current_price = float(last["close"])
        ctx.ema_9 = float(last.get("ema_fast", 0.0))
        ctx.ema_21 = float(last.get("ema_slow", 0.0))
        ctx.rsi_14 = float(last.get("rsi", 50.0))
        ctx.atr_14 = float(last.get("atr", 0.0))

        # Volume ratio
        vol_ma = last.get("volume_ma", 0.0)
        if pd.notna(vol_ma) and vol_ma > 0:
            ctx.volume_ratio = float(last["volume"] / vol_ma)
        else:
            ctx.volume_ratio = 1.0

        # Latest 20 candles as dicts
        recent = df_ind.tail(20)
        ctx.candles_15m = [
            {
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            for _, r in recent.iterrows()
        ]

        return ctx

    def _fill_trend_data(
        self, ctx: MarketContext, df: pd.DataFrame
    ) -> MarketContext:
        """Fill trend timeframe data into context.

        Args:
            ctx: MarketContext to fill.
            df: Trend timeframe OHLCV DataFrame.

        Returns:
            Updated MarketContext.
        """
        if len(df) == 0:
            return ctx

        ema_trend = compute_ema(df["close"], self.config["ema_trend"])
        ctx.ema_50_1h = float(ema_trend.iloc[-1])

        # Latest 10 candles as dicts
        recent = df.tail(10)
        ctx.candles_1h = [
            {
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
            for _, r in recent.iterrows()
        ]

        return ctx

    def _fill_orderbook_data(
        self, ctx: MarketContext, symbol: str
    ) -> MarketContext:
        """Fill orderbook data into context.

        Args:
            ctx: MarketContext to fill.
            symbol: Trading pair.

        Returns:
            Updated MarketContext.
        """
        orderbook = self.client._retry(
            self.client.exchange.fetch_order_book, symbol, limit=20
        )
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if bids and asks:
            best_bid = bids[0][0]
            best_ask = asks[0][0]
            if best_ask > best_bid and best_bid > 0:
                ctx.bid_ask_spread = (best_ask - best_bid) / best_bid
            else:
                ctx.bid_ask_spread = 0.0

            bid_vol = sum(b[1] for b in bids[:10])
            ask_vol = sum(a[1] for a in asks[:10])
            total_vol = bid_vol + ask_vol
            ctx.orderbook_imbalance = bid_vol / total_vol if total_vol > 0 else 0.5

        return ctx

    def _estimate_sentiment(self, headlines: list[str]) -> str:
        """Simple keyword-based sentiment estimation.

        Args:
            headlines: List of news headlines.

        Returns:
            "positive", "negative", or "neutral".
        """
        if not headlines:
            return "neutral"

        positive_keywords = [
            "surge", "rally", "bull", "breakout", "soar", "gain",
            "approval", "adopt", "etf approved", "all-time high",
        ]
        negative_keywords = [
            "crash", "plunge", "bear", "hack", "ban", "lawsuit",
            "sec charges", "liquidat", "dump", "sell-off", "fraud",
        ]

        pos_count = 0
        neg_count = 0
        for h in headlines:
            h_lower = h.lower()
            pos_count += sum(1 for kw in positive_keywords if kw in h_lower)
            neg_count += sum(1 for kw in negative_keywords if kw in h_lower)

        if pos_count > neg_count:
            return "positive"
        elif neg_count > pos_count:
            return "negative"
        return "neutral"
