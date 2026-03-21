"""Download funding rates for all altcoins."""
import requests
import pandas as pd
import time
import os

DATA_DIR = "/Users/iceai/Work/ccbt/data"

def download_funding_rate(symbol, start_date="2024-01-01"):
    base_url = "https://fapi.binance.com/fapi/v1/fundingRate"
    all_data = []
    start_ts = int(pd.Timestamp(start_date).timestamp() * 1000)
    end_ts = int(pd.Timestamp.now().timestamp() * 1000)
    current_start = start_ts

    print(f"Downloading funding rate for {symbol}...")

    while current_start < end_ts:
        params = {"symbol": symbol, "startTime": current_start, "limit": 1000}
        try:
            resp = requests.get(base_url, params=params)
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
        except Exception as e:
            print(f"  Error: {e}, retrying...")
            time.sleep(5)
            continue

    if not all_data:
        print(f"  No data for {symbol}")
        return

    df = pd.DataFrame(all_data)
    df["timestamp"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float)
    df = df[["timestamp", "fundingRate"]].drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

    filename = f"{symbol.lower()}_funding_rate.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath, index=False)
    print(f"  Saved {filepath}: {len(df)} rows, {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")


# Download for all coins
coins = [
    "DOGEUSDT", "ARBUSDT", "WIFUSDT", "AVAXUSDT", "NEARUSDT", "XRPUSDT",
    "LINKUSDT", "OPUSDT", "SUIUSDT", "APTUSDT",
]

for symbol in coins:
    filepath = os.path.join(DATA_DIR, f"{symbol.lower()}_funding_rate.csv")
    if os.path.exists(filepath):
        print(f"{symbol}: already exists, skipping")
        continue
    download_funding_rate(symbol)

print("\nDone! Funding rate files:")
for f in sorted(os.listdir(DATA_DIR)):
    if "funding" in f:
        rows = len(pd.read_csv(os.path.join(DATA_DIR, f)))
        print(f"  {f}: {rows} rows")
