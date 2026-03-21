"""Discover liquid USDT perpetual futures on Binance for strategy research.

Fetches all USDT-margined perpetual futures, filters by 24h volume, excludes
already-tested coins, and saves the result to data/liquid_coins.json.

Usage:
    python research/discover_liquid_coins.py
    python research/discover_liquid_coins.py --min-volume 20
"""

import argparse
import json
import os
import sys
import time
from typing import Any

import ccxt

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# Coins already tested — exclude from results
EXCLUDED_COINS = {
    "BTC", "ETH", "SOL", "DOGE", "AVAX", "LINK", "ADA", "XRP",
    "MATIC", "DOT", "NEAR", "APT", "ARB", "OP", "SUI", "WIF",
    "PEPE", "BONK", "PAXG",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover liquid USDT perpetual futures on Binance."
    )
    parser.add_argument(
        "--min-volume",
        type=float,
        default=10.0,
        metavar="M",
        help="Minimum 24h volume in millions USD (default: 10)",
    )
    return parser.parse_args()


def build_exchange() -> ccxt.binance:
    """Create a public Binance futures ccxt instance (no auth required)."""
    return ccxt.binance(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
    )


def load_markets(exchange: ccxt.binance) -> dict[str, Any]:
    """Load and return all Binance futures markets."""
    print("Loading markets from Binance futures...")
    markets = exchange.load_markets()
    print(f"  Total markets loaded: {len(markets)}")
    return markets


def filter_usdt_perps(markets: dict[str, Any]) -> list[str]:
    """Return symbols for USDT-margined perpetual futures only."""
    perps = []
    for symbol, market in markets.items():
        # Perpetual futures settle in USDT and have no expiry
        if (
            market.get("quote") == "USDT"
            and market.get("settle") == "USDT"
            and market.get("type") == "swap"
            and market.get("active", False)
        ):
            perps.append(symbol)
    print(f"  USDT perpetual futures found: {len(perps)}")
    return perps


def fetch_volumes(
    exchange: ccxt.binance, symbols: list[str]
) -> dict[str, float]:
    """Fetch 24h quote volume for all symbols via fetch_tickers (single call)."""
    print("Fetching 24h tickers (this may take a moment)...")

    # fetch_tickers with no argument fetches all tickers in one request on Binance futures
    try:
        tickers = exchange.fetch_tickers(symbols)
    except ccxt.NetworkError as exc:
        print(f"  Network error fetching tickers: {exc}", file=sys.stderr)
        sys.exit(1)
    except ccxt.ExchangeError as exc:
        print(f"  Exchange error fetching tickers: {exc}", file=sys.stderr)
        sys.exit(1)

    volume_map: dict[str, float] = {}
    for symbol, ticker in tickers.items():
        # quoteVolume = 24h volume in USDT
        qv = ticker.get("quoteVolume") or 0.0
        if qv and qv > 0:
            volume_map[symbol] = float(qv)

    print(f"  Tickers with volume data: {len(volume_map)}")
    return volume_map


def extract_base(symbol: str) -> str:
    """Extract base coin name from a ccxt symbol like 'AAVE/USDT:USDT'."""
    # format: BASE/QUOTE:SETTLE
    return symbol.split("/")[0].upper()


def build_prefix(symbol: str) -> str:
    """Build the lowercase data-file prefix from symbol, e.g. 'aaveusdt'."""
    base = extract_base(symbol)
    return f"{base.lower()}usdt"


def discover_coins(
    min_volume_m: float,
) -> list[dict[str, Any]]:
    """Full discovery pipeline. Returns sorted list of qualifying coins."""
    exchange = build_exchange()
    markets = load_markets(exchange)
    perp_symbols = filter_usdt_perps(markets)

    volume_map = fetch_volumes(exchange, perp_symbols)

    min_volume_usdt = min_volume_m * 1_000_000.0

    candidates: list[dict[str, Any]] = []
    for symbol in perp_symbols:
        base = extract_base(symbol)
        if base in EXCLUDED_COINS:
            continue
        vol = volume_map.get(symbol, 0.0)
        if vol < min_volume_usdt:
            continue
        candidates.append(
            {
                "symbol": symbol,
                "prefix": build_prefix(symbol),
                "volume_24h_m": round(vol / 1_000_000.0, 1),
            }
        )

    # Sort by volume descending
    candidates.sort(key=lambda x: x["volume_24h_m"], reverse=True)
    return candidates


def print_table(coins: list[dict[str, Any]]) -> None:
    """Print a formatted table of discovered coins."""
    if not coins:
        print("\nNo coins found matching criteria.")
        return

    header = f"{'Rank':>4}  {'Symbol':<22}  {'24h Volume':>12}"
    separator = "-" * len(header)
    print(f"\n{header}")
    print(separator)
    for rank, coin in enumerate(coins, start=1):
        vol_str = f"${coin['volume_24h_m']:,.1f}M"
        print(f"{rank:>4}  {coin['symbol']:<22}  {vol_str:>12}")
    print(separator)
    print(f"Total: {len(coins)} coins")


def save_json(coins: list[dict[str, Any]]) -> str:
    """Save coin list to data/liquid_coins.json and return the file path."""
    os.makedirs(DATA_DIR, exist_ok=True)
    output_path = os.path.join(DATA_DIR, "liquid_coins.json")
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(coins, fh, indent=2)
    return output_path


def main() -> None:
    args = parse_args()

    print(
        f"\nDiscover Liquid Coins — Binance USDT Perpetual Futures"
        f"\nMin 24h volume: ${args.min_volume:.0f}M"
        f"\nExcluded coins: {len(EXCLUDED_COINS)}"
        "\n" + "=" * 50
    )

    start = time.monotonic()
    coins = discover_coins(min_volume_m=args.min_volume)
    elapsed = time.monotonic() - start

    print_table(coins)

    if coins:
        path = save_json(coins)
        print(f"\nSaved {len(coins)} coins to: {path}")

    print(f"\nCompleted in {elapsed:.1f}s")

    if len(coins) < 40:
        print(
            f"\nNote: only {len(coins)} coins found. "
            "Try lowering --min-volume to get closer to 40-50 coins."
        )


if __name__ == "__main__":
    main()
