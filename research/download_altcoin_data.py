"""Download 15m and 1h data for multiple altcoins using ccxt.

Saves files in the same format as backtest/data_loader.py:
  - CSV with timestamp as index column
  - Compatible with load_ohlcv(path) directly

Usage:
  # Default: download hardcoded COINS list
  python research/download_altcoin_data.py

  # Load coins from data/liquid_coins.json, skip existing
  python research/download_altcoin_data.py --from-json

  # Download specific coins by prefix
  python research/download_altcoin_data.py --coins aaveusdt,ftmusdt

  # Also download funding rates alongside OHLCV
  python research/download_altcoin_data.py --from-json --funding
  python research/download_altcoin_data.py --coins wifusdt,arbusdt --funding
"""
import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple

import ccxt
import pandas as pd
import requests

# Use Binance public API (no auth needed for OHLCV)
exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "future"},  # Perpetual futures
})

DATA_DIR = "/Users/iceai/Work/ccbt/data"
LIQUID_COINS_JSON = os.path.join(DATA_DIR, "liquid_coins.json")

TIMEFRAME_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}

# Binance futures OHLCV limit per request
BINANCE_FUTURES_LIMIT = 1000

# Funding rate API
FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"

# 2 years back from 2026-03-20
SINCE = "2024-03-20T00:00:00Z"


def download_ohlcv(symbol: str, timeframe: str, since_str: str, filename: str) -> None:
    """Download OHLCV data from Binance futures and save to CSV.

    Matches the format produced by backtest/data_loader.save_ohlcv():
    timestamp as index, columns: open, high, low, close, volume.

    Args:
        symbol: ccxt symbol, e.g. 'DOGE/USDT:USDT'
        timeframe: '15m' or '1h'
        since_str: ISO8601 start date, e.g. '2024-03-20T00:00:00Z'
        filename: output filename (no path), saved into DATA_DIR
    """
    filepath = os.path.join(DATA_DIR, filename)

    if os.path.exists(filepath):
        df_existing = pd.read_csv(filepath)
        print(f"  {filename}: already exists ({len(df_existing)} rows) — skipping")
        return

    since_ms = exchange.parse8601(since_str)
    tf_ms = TIMEFRAME_MS[timeframe]
    all_data = []
    now_ms = int(time.time() * 1000)

    print(f"  Downloading {symbol} {timeframe} from {since_str}...", flush=True)

    while True:
        try:
            ohlcv = exchange.fetch_ohlcv(
                symbol, timeframe, since=since_ms, limit=BINANCE_FUTURES_LIMIT
            )
        except ccxt.BadSymbol as e:
            print(f"    SKIP — symbol not found on Binance futures: {e}")
            return
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
            print(f"    Network error: {e} — retrying in 5s...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"    Unexpected error: {e} — retrying in 5s...")
            time.sleep(5)
            continue

        if not ohlcv:
            break

        all_data.extend(ohlcv)
        last_ts = ohlcv[-1][0]
        since_ms = last_ts + tf_ms

        # Binance rate limit: 1200 weight/min, fetch_ohlcv costs ~5 weight
        time.sleep(0.5)

        # Stop when last candle is within 2 timeframes of now (reached current data)
        if last_ts >= now_ms - 2 * tf_ms:
            break

    if not all_data:
        print(f"    No data returned for {symbol} {timeframe}")
        return

    df = pd.DataFrame(all_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df = df.set_index("timestamp")
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()

    df.to_csv(filepath)
    print(
        f"  Saved {filename}: {len(df)} rows, "
        f"{df.index[0]} to {df.index[-1]}"
    )


def download_funding_rate(symbol_raw: str, prefix: str) -> None:
    """Download funding rate from Binance FAPI and save to CSV.

    Paginates forward from 2024-01-01 until current time.

    Args:
        symbol_raw: Binance raw symbol, e.g. 'AAVEUSDT' (no slashes)
        prefix: filename prefix, e.g. 'aaveusdt' — saved as {prefix}_funding_rate.csv
    """
    filepath = os.path.join(DATA_DIR, f"{prefix}_funding_rate.csv")

    if os.path.exists(filepath):
        df_existing = pd.read_csv(filepath)
        print(f"  {prefix}_funding_rate.csv: already exists ({len(df_existing)} rows) — skipping")
        return

    all_data: list = []
    start_ts = int(pd.Timestamp("2024-01-01").timestamp() * 1000)
    end_ts = int(pd.Timestamp.now().timestamp() * 1000)
    current_start = start_ts

    print(f"  Downloading funding rate for {symbol_raw}...", flush=True)

    while current_start < end_ts:
        params = {"symbol": symbol_raw, "startTime": current_start, "limit": 1000}
        try:
            resp = requests.get(FUNDING_RATE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            all_data.extend(data)
            last_ts = int(data[-1]["fundingTime"])
            if last_ts <= current_start:
                break
            current_start = last_ts + 1
            time.sleep(0.3)
        except requests.RequestException as e:
            print(f"    Request error: {e} — retrying in 5s...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"    Unexpected error: {e} — retrying in 5s...")
            time.sleep(5)
            continue

    if not all_data:
        print(f"    No funding rate data for {symbol_raw}")
        return

    df = pd.DataFrame(all_data)
    df["timestamp"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float)
    df = (
        df[["timestamp", "fundingRate"]]
        .drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
    )

    df.to_csv(filepath, index=False)
    print(
        f"  Saved {prefix}_funding_rate.csv: {len(df)} rows, "
        f"{df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}"
    )


def load_coins_from_json() -> List[Tuple[str, str]]:
    """Load (symbol, prefix) tuples from data/liquid_coins.json.

    Skips entries that already have both 15m and 1h data files.

    Returns:
        List of (ccxt_symbol, prefix) tuples with missing data files.
    """
    with open(LIQUID_COINS_JSON, "r") as f:
        entries = json.load(f)

    coins = []
    skipped = 0
    for entry in entries:
        symbol: str = entry["symbol"]
        prefix: str = entry["prefix"]
        file_15m = os.path.join(DATA_DIR, f"{prefix}_15m_2y.csv")
        file_1h = os.path.join(DATA_DIR, f"{prefix}_1h_2y.csv")
        if os.path.exists(file_15m) and os.path.exists(file_1h):
            skipped += 1
            continue
        coins.append((symbol, prefix))

    if skipped:
        print(f"  Skipping {skipped} coins that already have both data files.")
    return coins


def prefix_to_ccxt_symbol(prefix: str) -> str:
    """Convert a lowercase prefix like 'aaveusdt' to ccxt 'AAVE/USDT:USDT'.

    Assumes USDT-margined perpetual futures. Strips the 'usdt' suffix and
    rebuilds the ccxt symbol format.

    Args:
        prefix: lowercase coin prefix ending in 'usdt', e.g. 'aaveusdt'

    Returns:
        ccxt symbol string, e.g. 'AAVE/USDT:USDT'
    """
    upper = prefix.upper()
    if upper.endswith("USDT"):
        base = upper[:-4]
    else:
        base = upper
    return f"{base}/USDT:USDT"


def download_coin(
    symbol: str, prefix: str, download_funding: bool
) -> Tuple[int, float]:
    """Download OHLCV (and optionally funding rate) for one coin.

    Returns:
        (files_written, total_bytes) for the newly created files.
    """
    files_before = _snapshot_data_files()

    download_ohlcv(symbol, "15m", SINCE, f"{prefix}_15m_2y.csv")
    download_ohlcv(symbol, "1h", SINCE, f"{prefix}_1h_2y.csv")

    if download_funding:
        # Derive raw Binance symbol from ccxt symbol: 'AAVE/USDT:USDT' → 'AAVEUSDT'
        raw_symbol = symbol.split("/")[0] + "USDT"
        download_funding_rate(raw_symbol, prefix)

    files_after = _snapshot_data_files()
    new_files = files_after - files_before
    total_bytes = sum(
        os.path.getsize(os.path.join(DATA_DIR, f))
        for f in new_files
    )
    return len(new_files), total_bytes


def _snapshot_data_files() -> set:
    """Return the current set of CSV filenames in DATA_DIR."""
    try:
        return {f for f in os.listdir(DATA_DIR) if f.endswith(".csv")}
    except FileNotFoundError:
        return set()


def print_summary() -> None:
    """Print a summary of all CSV files in DATA_DIR."""
    print("\nAll CSV files in data dir:")
    total_mb = 0.0
    for f in sorted(os.listdir(DATA_DIR)):
        if f.endswith(".csv"):
            size_mb = os.path.getsize(os.path.join(DATA_DIR, f)) / 1024 / 1024
            total_mb += size_mb
            print(f"  {f}: {size_mb:.1f} MB")
    print(f"\nTotal: {total_mb:.1f} MB across data dir")


# Coins to download — liquid perpetual futures on Binance
# Format: (ccxt_symbol, filename_prefix)
COINS = [
    # Major altcoins (high liquidity)
    ("DOGE/USDT:USDT", "dogeusdt"),
    ("AVAX/USDT:USDT", "avaxusdt"),
    ("LINK/USDT:USDT", "linkusdt"),
    ("ADA/USDT:USDT", "adausdt"),
    ("XRP/USDT:USDT", "xrpusdt"),
    ("MATIC/USDT:USDT", "maticusdt"),
    ("DOT/USDT:USDT", "dotusdt"),
    # Trendy / volatile coins
    ("NEAR/USDT:USDT", "nearusdt"),
    ("APT/USDT:USDT", "aptusdt"),
    ("ARB/USDT:USDT", "arbusdt"),
    ("OP/USDT:USDT", "opusdt"),
    ("SUI/USDT:USDT", "suiusdt"),
    ("WIF/USDT:USDT", "wifusdt"),
    ("PEPE/USDT:USDT", "pepeusdt"),
    ("BONK/USDT:USDT", "bonkusdt"),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download OHLCV (and optionally funding rate) data for altcoins."
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--from-json",
        action="store_true",
        help=f"Load coins from {LIQUID_COINS_JSON} instead of the hardcoded COINS list. "
             "Skips coins that already have both 15m and 1h data files.",
    )
    source_group.add_argument(
        "--coins",
        type=str,
        metavar="PREFIX1,PREFIX2,...",
        help="Comma-separated list of coin prefixes to download, e.g. aaveusdt,ftmusdt. "
             "Automatically derives the ccxt symbol (USDT-margined perpetual).",
    )
    parser.add_argument(
        "--funding",
        action="store_true",
        help="Also download funding rates for each coin after OHLCV download.",
    )
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    # Resolve coin list
    if args.from_json:
        print(f"Loading coins from {LIQUID_COINS_JSON}...")
        coins = load_coins_from_json()
        print(f"  {len(coins)} coins to download.\n")
    elif args.coins:
        prefixes = [p.strip().lower() for p in args.coins.split(",") if p.strip()]
        coins = [(prefix_to_ccxt_symbol(p), p) for p in prefixes]
        print(f"Downloading {len(coins)} coins from --coins flag.\n")
    else:
        coins = COINS
        print(f"Downloading {len(coins)} hardcoded coins.\n")

    if not coins:
        print("Nothing to download.")
        return

    total_files_written = 0
    total_bytes_written = 0

    # Parallel download with ThreadPoolExecutor (max 3 workers — respects rate limits)
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_coin = {
            executor.submit(download_coin, symbol, prefix, args.funding): (symbol, prefix)
            for symbol, prefix in coins
        }
        completed = 0
        for future in as_completed(future_to_coin):
            symbol, prefix = future_to_coin[future]
            completed += 1
            try:
                files_written, bytes_written = future.result()
                total_files_written += files_written
                total_bytes_written += bytes_written
                print(
                    f"[{completed}/{len(coins)}] {symbol} done "
                    f"(+{files_written} files, +{bytes_written / 1024 / 1024:.1f} MB)",
                    flush=True,
                )
            except KeyboardInterrupt:
                print("\nInterrupted — partial downloads saved.")
                break
            except Exception as e:
                print(f"[{completed}/{len(coins)}] SKIP {symbol}: {e}")

    print(
        f"\n\nDone! {total_files_written} new files downloaded "
        f"({total_bytes_written / 1024 / 1024:.1f} MB total new data)."
    )
    print(f"Saved to: {DATA_DIR}")
    print_summary()


if __name__ == "__main__":
    main()
