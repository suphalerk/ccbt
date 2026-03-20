"""Download 15m and 1h data for multiple altcoins using ccxt.

Saves files in the same format as backtest/data_loader.py:
  - CSV with timestamp as index column
  - Compatible with load_ohlcv(path) directly
"""
import ccxt
import pandas as pd
import time
import os

# Use Binance public API (no auth needed for OHLCV)
exchange = ccxt.binance({
    "enableRateLimit": True,
    "options": {"defaultType": "future"},  # Perpetual futures
})

DATA_DIR = "/Users/iceai/Work/ccbt/data"

TIMEFRAME_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
}

# Binance futures OHLCV limit per request
BINANCE_FUTURES_LIMIT = 1000


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

# 2 years back from 2026-03-20
SINCE = "2024-03-20T00:00:00Z"


def main() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)

    total = len(COINS) * 2
    done = 0

    for symbol, prefix in COINS:
        print(f"\n[{done // 2 + 1}/{len(COINS)}] {symbol}:")
        try:
            download_ohlcv(symbol, "15m", SINCE, f"{prefix}_15m_2y.csv")
            done += 1
            download_ohlcv(symbol, "1h", SINCE, f"{prefix}_1h_2y.csv")
            done += 1
        except KeyboardInterrupt:
            print("\nInterrupted — partial downloads saved.")
            break
        except Exception as e:
            print(f"  SKIP {symbol}: {e}")
            done += 2

    print(f"\n\nDone! {done}/{total} files. Saved to: {DATA_DIR}")
    print("\nAll CSV files in data dir:")
    for f in sorted(os.listdir(DATA_DIR)):
        if f.endswith(".csv"):
            size_mb = os.path.getsize(os.path.join(DATA_DIR, f)) / 1024 / 1024
            print(f"  {f}: {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
