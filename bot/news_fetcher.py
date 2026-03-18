"""Fetch crypto news headlines from RSS feeds and CryptoPanic API."""

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
]

BTC_KEYWORDS = ["btc", "bitcoin", "crypto", "cryptocurrency"]


class NewsFetcher:
    """Fetches crypto news headlines from RSS feeds or CryptoPanic API.

    Filters for BTC/Bitcoin-related headlines within a lookback window.
    Returns empty list gracefully if no news is available.
    """

    def __init__(
        self,
        source: str = "rss",
        cryptopanic_token: Optional[str] = None,
        rss_feeds: Optional[list[str]] = None,
    ) -> None:
        """Initialize the news fetcher.

        Args:
            source: "rss" or "cryptopanic".
            cryptopanic_token: API token for CryptoPanic (required if source="cryptopanic").
            rss_feeds: Custom RSS feed URLs. Defaults to built-in list.
        """
        self.source = source
        self.cryptopanic_token = cryptopanic_token
        self.rss_feeds = rss_feeds or RSS_FEEDS

    async def fetch_headlines(self, lookback_hours: int = 2) -> list[str]:
        """Fetch recent BTC-related news headlines.

        Args:
            lookback_hours: Only include news from the last N hours.

        Returns:
            List of headline strings. Empty list if no news found.
        """
        try:
            if self.source == "cryptopanic" and self.cryptopanic_token:
                return await self._fetch_cryptopanic(lookback_hours)
            return await self._fetch_rss(lookback_hours)
        except Exception as e:
            logger.warning("news_fetch_error", extra={"error": str(e)})
            return []

    async def _fetch_rss(self, lookback_hours: int) -> list[str]:
        """Fetch headlines from RSS feeds.

        Args:
            lookback_hours: Lookback window in hours.

        Returns:
            Filtered list of headlines.
        """
        headlines = []
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=lookback_hours)

        for feed_url in self.rss_feeds:
            try:
                items = await asyncio.to_thread(self._parse_rss_feed, feed_url)
                for title, pub_date in items:
                    if self._is_btc_related(title):
                        if pub_date is None or pub_date >= cutoff:
                            headlines.append(title)
            except Exception as e:
                logger.warning(
                    "rss_feed_error",
                    extra={"feed": feed_url, "error": str(e)},
                )
                continue

        # Deduplicate and limit
        seen = set()
        unique = []
        for h in headlines:
            if h not in seen:
                seen.add(h)
                unique.append(h)

        logger.info("news_fetched", extra={"source": "rss", "count": len(unique)})
        return unique[:10]  # Max 10 headlines

    def _parse_rss_feed(self, url: str) -> list[tuple[str, Optional[datetime]]]:
        """Parse an RSS feed and extract titles and dates.

        Args:
            url: RSS feed URL.

        Returns:
            List of (title, pub_date) tuples.
        """
        req = Request(url, headers={"User-Agent": "TradingBot/1.0"})
        with urlopen(req, timeout=10) as response:
            xml_data = response.read()

        root = ET.fromstring(xml_data)
        items = []

        # Handle RSS 2.0
        for item in root.findall(".//item"):
            title_elem = item.find("title")
            pub_date_elem = item.find("pubDate")

            if title_elem is not None and title_elem.text:
                title = title_elem.text.strip()
                pub_date = None
                if pub_date_elem is not None and pub_date_elem.text:
                    pub_date = self._parse_rss_date(pub_date_elem.text)
                items.append((title, pub_date))

        return items

    def _parse_rss_date(self, date_str: str) -> Optional[datetime]:
        """Parse RSS date string to datetime.

        Args:
            date_str: RSS date string (RFC 822).

        Returns:
            Parsed datetime or None if unparsable.
        """
        # Common RSS date formats
        formats = [
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S GMT",
            "%Y-%m-%dT%H:%M:%S%z",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str.strip(), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
        return None

    async def _fetch_cryptopanic(self, lookback_hours: int) -> list[str]:
        """Fetch headlines from CryptoPanic API.

        Args:
            lookback_hours: Lookback window in hours.

        Returns:
            List of headlines.
        """
        url = (
            f"https://cryptopanic.com/api/v1/posts/"
            f"?auth_token={self.cryptopanic_token}"
            f"&currencies=BTC&kind=news"
        )

        def _fetch():
            req = Request(url, headers={"User-Agent": "TradingBot/1.0"})
            with urlopen(req, timeout=10) as response:
                import json
                return json.loads(response.read())

        try:
            data = await asyncio.to_thread(_fetch)
        except Exception as e:
            logger.warning("cryptopanic_error", extra={"error": str(e)})
            return []

        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=lookback_hours)
        headlines = []

        for post in data.get("results", []):
            title = post.get("title", "")
            pub_date_str = post.get("published_at", "")
            if pub_date_str:
                try:
                    pub_date = datetime.fromisoformat(
                        pub_date_str.replace("Z", "+00:00")
                    )
                    if pub_date < cutoff:
                        continue
                except ValueError:
                    pass

            # Extract vote data (bullish/bearish community sentiment)
            votes = post.get("votes", {})
            bullish = int(votes.get("positive", 0))
            bearish = int(votes.get("negative", 0))

            # Annotate title with vote counts if meaningful
            if bullish > 0 or bearish > 0:
                title = f"{title} [+{bullish}/-{bearish}]"

            headlines.append(title)

        logger.info(
            "news_fetched", extra={"source": "cryptopanic", "count": len(headlines)}
        )
        return headlines[:10]

    def _is_btc_related(self, text: str) -> bool:
        """Check if text is related to Bitcoin/crypto.

        Args:
            text: Text to check.

        Returns:
            True if BTC-related.
        """
        text_lower = text.lower()
        return any(kw in text_lower for kw in BTC_KEYWORDS)
