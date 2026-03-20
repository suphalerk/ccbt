"""Download microstructure data from Binance Futures API.

API History Limitations (discovered 2026-03-21):
- takerlongshortRatio: ~30 days only. startTime not supported for old dates.
  Must use endTime-based backward pagination.
- openInterestHist: ~30 days only (same limitation).
- fundingRate (/fapi/v1/fundingRate): Full history from 2019+. startTime works fine.
"""
import requests
import pandas as pd
import time
import os
from typing import Optional

DATA_DIR = "/Users/iceai/Work/ccbt/data"

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _paginate_backward(base_url: str, params_base: dict, ts_field: str) -> list:
    """Paginate an endpoint backwards using endTime.

    Binance futures data endpoints (takerlongshortRatio, openInterestHist) only
    serve ~30 days of history and reject startTime for older dates. They do accept
    endTime, so we walk backwards from now until we get an empty or error response.

    Args:
        base_url: Full endpoint URL.
        params_base: Fixed params (symbol, period, limit). Must NOT include endTime.
        ts_field: Key name for the timestamp field in each record.

    Returns:
        All records sorted ascending by timestamp.
    """
    all_data: list = []
    current_end: Optional[int] = None

    while True:
        params = dict(params_base)
        if current_end is not None:
            params["endTime"] = current_end

        try:
            resp = requests.get(base_url, params=params, timeout=10)
            if resp.status_code == 400:
                # endTime too old — we have reached the limit
                break
            resp.raise_for_status()
            batch = resp.json()
        except Exception as exc:
            print(f"  Request error: {exc}, retrying in 5s…")
            time.sleep(5)
            continue

        if not batch:
            break

        all_data.extend(batch)
        earliest_ts = int(batch[0][ts_field])

        if len(all_data) % 5000 == 0 and len(all_data) > 0:
            dt = pd.to_datetime(earliest_ts, unit="ms")
            print(f"  {len(all_data)} records so far, back to {dt}…")

        # Advance the window one millisecond before the earliest record we got
        if current_end is not None and earliest_ts >= current_end:
            break  # No progress — prevent infinite loop
        current_end = earliest_ts - 1
        time.sleep(0.3)

    # Sort ascending
    all_data.sort(key=lambda r: int(r[ts_field]))
    return all_data


def _paginate_forward(base_url: str, params_base: dict, ts_field: str) -> list:
    """Paginate an endpoint forward using startTime (works for fundingRate).

    Args:
        base_url: Full endpoint URL.
        params_base: Fixed params (symbol, limit). Must NOT include startTime.
        ts_field: Key name for the timestamp field in each record.

    Returns:
        All records sorted ascending by timestamp.
    """
    all_data: list = []
    current_start: int = params_base.pop("startTime")

    while True:
        params = dict(params_base)
        params["startTime"] = current_start

        try:
            resp = requests.get(base_url, params=params, timeout=10)
            resp.raise_for_status()
            batch = resp.json()
        except Exception as exc:
            print(f"  Request error: {exc}, retrying in 5s…")
            time.sleep(5)
            continue

        if not batch:
            break

        all_data.extend(batch)
        last_ts = int(batch[-1][ts_field])

        if len(all_data) % 10000 == 0 and len(all_data) > 0:
            dt = pd.to_datetime(last_ts, unit="ms")
            print(f"  {len(all_data)} records so far, up to {dt}…")

        if last_ts <= current_start:
            break  # No progress
        current_start = last_ts + 1
        time.sleep(0.3)

    return all_data


# ------------------------------------------------------------------
# Download functions
# ------------------------------------------------------------------

def download_taker_ratio(symbol: str = "BTCUSDT", interval: str = "15m") -> Optional[pd.DataFrame]:
    """Download taker buy/sell ratio.

    Endpoint: GET /futures/data/takerlongshortRatio
    History:  ~30 days only. Paginate backwards via endTime.
    Fields:   buySellRatio, buyVol, sellVol, timestamp.
    """
    print(f"\nDownloading taker ratio {symbol} {interval}…")
    base_url = "https://fapi.binance.com/futures/data/takerlongshortRatio"
    params = {"symbol": symbol, "period": interval, "limit": 500}

    records = _paginate_backward(base_url, params, ts_field="timestamp")

    if not records:
        print(f"  No data returned for {symbol} {interval}")
        return None

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
    df["buySellRatio"] = df["buySellRatio"].astype(float)
    df["buyVol"] = df["buyVol"].astype(float)
    df["sellVol"] = df["sellVol"].astype(float)
    df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    filename = f"{symbol.lower()}_taker_ratio_{interval}.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath, index=False)
    print(f"  Saved {filepath}: {len(df)} rows, {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


def download_funding_rate(symbol: str = "BTCUSDT", start_date: str = "2021-01-01") -> Optional[pd.DataFrame]:
    """Download full funding rate history.

    Endpoint: GET /fapi/v1/fundingRate
    History:  Full history from 2019+. startTime supported.
    Cadence:  Every 8 hours (3 records/day).
    """
    print(f"\nDownloading funding rate {symbol} from {start_date}…")
    base_url = "https://fapi.binance.com/fapi/v1/fundingRate"
    start_ts = int(pd.Timestamp(start_date).timestamp() * 1000)
    params = {"symbol": symbol, "startTime": start_ts, "limit": 1000}

    records = _paginate_forward(base_url, params, ts_field="fundingTime")

    if not records:
        print(f"  No funding data for {symbol}")
        return None

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms")
    df["fundingRate"] = df["fundingRate"].astype(float)
    df = df[["timestamp", "fundingRate"]].drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    filename = f"{symbol.lower()}_funding_rate.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath, index=False)
    print(f"  Saved {filepath}: {len(df)} rows, {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


def download_open_interest(symbol: str = "BTCUSDT", interval: str = "1h") -> Optional[pd.DataFrame]:
    """Download open interest history.

    Endpoint: GET /futures/data/openInterestHist
    History:  ~30 days only (same limit as taker ratio). Paginate backwards.
    Intervals: 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d.
    """
    print(f"\nDownloading OI {symbol} {interval}…")
    base_url = "https://fapi.binance.com/futures/data/openInterestHist"
    params = {"symbol": symbol, "period": interval, "limit": 500}

    records = _paginate_backward(base_url, params, ts_field="timestamp")

    if not records:
        print(f"  No OI data for {symbol} {interval}")
        return None

    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
    df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
    df["sumOpenInterestValue"] = df["sumOpenInterestValue"].astype(float)
    df = df[["timestamp", "sumOpenInterest", "sumOpenInterestValue"]].drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

    filename = f"{symbol.lower()}_oi_{interval}.csv"
    filepath = os.path.join(DATA_DIR, filename)
    df.to_csv(filepath, index=False)
    print(f"  Saved {filepath}: {len(df)} rows, {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")
    return df


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("MICROSTRUCTURE DATA DOWNLOAD")
    print("=" * 60)
    print("NOTE: taker ratio and OI are limited to ~30 days by Binance.")
    print("      Funding rate goes back to 2021+.")

    # --- BTC ---
    # Taker ratio: 15m (matches signal TF) and 5m
    download_taker_ratio("BTCUSDT", "15m")
    download_taker_ratio("BTCUSDT", "5m")

    # Funding rate: full history from 2021
    download_funding_rate("BTCUSDT", "2021-01-01")

    # OI: 1h and 15m (both ~30d)
    download_open_interest("BTCUSDT", "1h")
    download_open_interest("BTCUSDT", "15m")

    # --- DOGE ---
    download_taker_ratio("DOGEUSDT", "15m")
    download_funding_rate("DOGEUSDT", "2024-01-01")
    download_open_interest("DOGEUSDT", "1h")

    print("\n" + "=" * 60)
    print("DOWNLOAD COMPLETE")
    print("=" * 60)

    for fname in sorted(os.listdir(DATA_DIR)):
        if any(tag in fname for tag in ("taker", "funding", "oi_")):
            size = os.path.getsize(os.path.join(DATA_DIR, fname))
            print(f"  {fname}: {size / 1024:.0f} KB")
